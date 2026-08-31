# Ingestão local (staging) → Supabase curado

Arquitetura em duas camadas para o corpus RAG (documentos CVM/IPE):

- **Staging (local):** processa o corpus **completo** sem limite de tamanho —
  descoberta, extração de texto completo, ajustes, reprocessamento.
- **Serviço (Supabase):** recebe só o **subconjunto curado** que o app consulta
  (universo inteiro, ~25 docs de alto sinal por empresa, **sem embeddings**).

Assim o Supabase fica enxuto (bem abaixo dos 500 MB do plano free) e o trabalho
pesado roda rápido na sua máquina.

---

## Pré-requisito

[Docker Desktop](https://www.docker.com/products/docker-desktop/) instalado e rodando.

## 1) Subir o banco local

```bash
cd local_staging
docker compose up -d
docker compose ps        # deve mostrar "healthy"
```

Banco disponível em `postgresql://postgres:postgres@localhost:5433/staging`.

## 2) Bootstrap (schema + referências)

Copia do Supabase as tabelas de referência (universo, taxonomia de setor,
carteira) e cria as tabelas de documentos vazias no local.

> As variáveis abaixo são para o **terminal**; o `.env` do projeto continua
> apontando o app para o Supabase. Aqui só sobrescrevemos na sessão do shell.

PowerShell (Windows):
```powershell
$env:STAGING_DB_URL = "postgresql://postgres:postgres@localhost:5433/staging"
python scripts/staging_bootstrap.py
```

## 3) Rodar a ingestão apontando para o LOCAL

Aponte os coletores para o staging (uma variável) e rode:

```powershell
$env:SUPABASE_DB_URL_B3 = "postgresql://postgres:postgres@localhost:5433/staging"

# metadados de todos os anos + universo
python scripts/backfill_cvm_ipe.py --years 2023,2024,2025,2026 --apply
python scripts/enrich_setores_cvm.py --apply

# texto completo — sem limite (é local): rode em blocos grandes até esvaziar a fila
$env:CVM_FULLTEXT_MAX = "500"; $env:CVM_FULLTEXT_DELAY = "1.5"
python -c "import data_pipeline.jobs.update_cvm_fulltext as j; print(j.run())"
#   repita até 'records_updated' vir 0 (fila drenada)
```

> Dica: mantenha o delay (`CVM_FULLTEXT_DELAY`) em ~1.5–3s para o ENET não
> bloquear; o disjuntor do job já protege contra rate-limit.

## 4) Publicar o curado no Supabase

Origem = staging local, destino = Supabase. **Dry-run primeiro** (não escreve):

```powershell
$env:STAGING_DB_URL  = "postgresql://postgres:postgres@localhost:5433/staging"
# SUPABASE_DB_URL vem do .env do projeto (destino de produção)
python scripts/sync_docs_to_supabase.py                 # mostra quantos docs/chunks e MB
python scripts/sync_docs_to_supabase.py --tickers PETR  # testa uma empresa (aplica só ela)  --apply
python scripts/sync_docs_to_supabase.py --apply         # publica o universo curado
```

O sync **substitui** o corpus de documentos no Supabase pelo curado (o
`market.*` de fundamentos não é tocado). Sobe só o texto dos chunks, **sem
embeddings** — o RAG serve em modo temporal.

## 5) Manutenção (mensal ou quando quiser atualizar)

1. `docker compose up -d`
2. Rodar os coletores (passo 3) — pega só os documentos novos (dedup por URL).
3. `python scripts/sync_docs_to_supabase.py --apply` — republica o curado.

Para parar sem apagar os dados: `docker compose down`.
Para zerar o staging: `docker compose down -v`.

---

## Ajuste de tamanho

O botão principal é `--per-ticker` no sync (default 25). Se o Supabase ficar
apertado, publique com menos history:

```powershell
python scripts/sync_docs_to_supabase.py --per-ticker 15 --apply
```

---

## Republicação diária da vitrine de FIIs

A vitrine de FIIs tem prazo de validade e a publicação **precisa rodar nesta
máquina**: o armazém local (Docker, porta 5433) não é alcançável pelo GitHub
Actions, então não existe workflow remoto que substitua isto.

Deixar envelhecer não é inofensivo. Em 31/08/2026 a vitrine chegou a 5 dias, a
leitura foi recusada e a tela de Seleção de FIIs reprovou os 394 fundos por
métrica ausente, creditando a falha aos filtros de elegibilidade (PR #190). O
código agora falha de forma visível, mas o remédio é a vitrine não vencer.

Tarefa agendada no Windows — **DFU - Republicar vitrine de FIIs**, diária às
19:30 (depois do fechamento da B3), com `StartWhenAvailable` para recuperar o
dia caso o computador esteja desligado no horário.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\republicar_vitrine_fii_diario.ps1
```

O runner sobe o container se estiver parado, espera ficar saudável, publica e
**confere o resultado pelo mesmo caminho que a tela usa** — publicar sem
verificar seria repetir o defeito de origem. Log em
`local_staging/logs/republicacao_fii.log` (stderr em `.stderr.log`).

Para conferir a vitrine a qualquer momento, sem republicar:

```bash
python scripts/verificar_frescor_vitrine_fii.py --max-idade-dias 4
```

Sai com código 1 quando a vitrine não pode ser lida, vem vazia, ou perde as
colunas que a elegibilidade consulta — a checagem é pelas **colunas de
decisão**, não por `.empty`: foi um quadro cheio de linhas e vazio de métricas
que reprovou os 394 fundos.

Gerenciar a tarefa:

```powershell
Get-ScheduledTask -TaskName "DFU - Republicar vitrine de FIIs"
Start-ScheduledTask -TaskName "DFU - Republicar vitrine de FIIs"   # rodar agora
Disable-ScheduledTask -TaskName "DFU - Republicar vitrine de FIIs" # suspender
```

As vitrines de B3 e EUA continuam manuais; só a de FIIs tem portão de idade.
