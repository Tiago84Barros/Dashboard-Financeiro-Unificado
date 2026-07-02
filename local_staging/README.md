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
