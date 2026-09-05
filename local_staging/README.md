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

## Atualização automática das vitrines (FII, B3, EUA)

Tudo o que precisa ser publicado passa por um ponto só:

```bash
python scripts/atualizar_vitrines.py
```

Ele lê a agenda de `core/publicacao_agenda.py`, publica **o que está vencido**,
confere o resultado pelo leitor que a tela usa e avisa no Telegram quando falha.

**Roda nesta máquina, e não tem alternativa remota.** Das 22 tabelas
`market.fii*`, 18 existem só no armazém local (`fii_source_releases`,
`fii_metric_observations`, `fii_parser_calibrations`...). Foi tentar rodar a
cadeia de FIIs contra o Supabase que quebrou o `market-refresh.yml` em dez
execuções diárias seguidas, sempre no mesmo ponto -- e o `market.fiis` do
armazém ficou 20 dias parado enquanto a vitrine era republicada diariamente em
cima de um cadastro de três semanas.

### Registrar as tarefas agendadas

Uma vez só, num PowerShell qualquer (não precisa de administrador):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts
egistrar_tarefas.ps1
```

Isso cria **DFU - Atualizar vitrines** com dois gatilhos:

| Gatilho | Quando | Para quê |
|---|---|---|
| Diário | 19:30, depois do fechamento da B3 | o caso normal |
| Ao entrar na sessão | logon + 3 min (folga do Docker Desktop) | recuperar o que venceu com a máquina desligada |

O gatilho de logon não é redundância: é o único caminho pelo qual um dia perdido
é recuperado. Ele funciona porque **a cadência é medida contra a última
publicação bem-sucedida, nunca contra um horário**. Ligar o computador depois de
uma semana fora publica o que venceu, na ordem; ligar duas vezes no mesmo dia não
publica nada de novo. Um agendador puramente horário perderia o dia em silêncio e
no dia seguinte publicaria como se nada tivesse acontecido.

Também registra **DFU - Coleta de noticias**, a cada 30 minutos (:17 e :47) e
ao entrar na sessão. Ela roda `python -m data_pipeline.cli_noticias`.

**A coleta também não tem alternativa remota, e pelo mesmo motivo das vitrines.**
O `.github/workflows/noticias.yml` existe e tem cron de 30 minutos, mas desde que
o acervo passou a morar no armazém local um runner do GitHub não alcança
`noticias_itens`: ele coletaria, gastaria requisição de Alpha Vantage e Marketaux
e descartaria tudo. O job avisa (`partial_success`, "coleta não persistida") --
só que depois de a cota ter sido paga. Por isso o workflow ganhou um passo de
destino que reprova **antes** da primeira requisição, e a cadência real mudou de
casa para cá.

Para conferir o destino sem coletar nada (não toca em nenhuma API):

```bash
python -m data_pipeline.cli_noticias --destino
```

`0` quando há onde gravar, `1` quando não há. O freio de cadência mora no banco e
é medido contra a última coleta bem-sucedida, então disparar a cada 30 minutos
não significa coletar a cada 30 minutos: o modo corrente (Normal a Sistêmico)
decide, e a execução que ele não pede é descartada de graça.

Também registra **DFU - Backfill documentos FII** (sábados às 09:00) e remove as
tarefas obsoletas `DashboardFinanceiro-FII-Backfill` e
`DFU - Republicar vitrine de FIIs`.

### O que ele publica, e com que cadência

| Alvo | Cadência | O que é |
|---|---|---|
| `fii_ingest` | 1 dia | cadeia de 7 etapas de ingestão de FIIs **no armazém** |
| `fii_selection` | 1 dia | vitrine de seleção de FIIs no Supabase |
| `b3_metrics` | 7 dias | `market.calculated_metrics` |
| `b3_vintages` | 7 dias | safras PIT da B3 |
| `us_snapshot` | 7 dias | `market_us.company_snapshots` |
| `us_vintages` | por versão | safras PIT dos EUA (gatilho: `US_FUNDAMENTAL_SCORE_VERSION` mudar) |
| `us_delistings` | 30 dias | saídas de bolsa |
| `us_prices` | 30 dias | preços mensais |

Duas regras que existem por incidente:

- **Falha não vira silêncio.** Um alvo cujo último desfecho foi erro está sempre
  devendo, seja qual for a cadência -- sem isso, uma falha em alvo mensal
  esperaria um mês pela próxima tentativa.
- **Safra PIT não tem cadência de calendário.** Republicar a mesma versão de
  metodologia todo dia grava exatamente as mesmas linhas; o gatilho é a versão
  mudar.

### Estado, log e verificação

O estado fica em `local_staging/estado_publicacao.json`, gravado **depois de cada
alvo** (hibernar no meio não desfaz o que já foi publicado). Na primeira execução
ele é semeado com a data real da última publicação lida dos próprios bancos --
sem isso um arquivo vazio republicaria tudo, inclusive as 346 mil linhas de
`prices_monthly`:

```bash
python scripts/atualizar_vitrines.py --semear --listar
```

Log em `local_staging/logs/atualizacao_vitrines.log`.

Para conferir as vitrines a qualquer momento, sem publicar:

```bash
python scripts/verificar_frescor_vitrines.py
```

Sai com código 1 quando uma vitrine não pode ser lida, vem vazia, perde as
colunas que a decisão consulta ou passa da idade máxima do módulo (FII 4 dias,
B3 e EUA 10). A checagem é pelas **colunas de decisão**, não por `.empty`: foi um
quadro cheio de linhas e vazio de métricas que reprovou os 394 fundos em
31/08/2026, com a tela creditando a falha aos filtros de elegibilidade (PR #190).

A idade da B3 **não** sai da coluna `data` do quadro -- ali `data` é 31/12 do
exercício de referência, uma data contábil que fica no futuro o ano inteiro e
faria o portão aprovar para sempre. Quem sabe quando a vitrine foi escrita é
`updated_at` de `market.calculated_metrics`.

### Avisos

`scripts/notificar.py` manda pelo Telegram via Hermes. Avisa quando falha, e
quando publicou algo. **Não** avisa "está tudo bem" num dia sem publicação:
aviso diário de rotina treina a pessoa a ignorar a notificação, e aí o aviso de
falha some junto.

### Quando o Docker é que não sobe

A rotina abre o Docker Desktop sozinha antes de tentar o container. Motor fora
do ar e container parado são camadas diferentes: com o motor morto, `docker
start` devolve erro de conexão e a espera por saúde gasta os 600s perguntando
por um serviço que não existe -- o log culparia o armazém, que está intacto. Por
isso `daemon_pronto()` vem antes, e o log diz qual das duas falhou.

Se o log disser `o motor do Docker não subiu em 300s`, o problema é do Docker
Desktop, não da rotina. Em 01/09/2026 ele morria 20s depois de abrir com:

```
starting services: initializing Inference manager:
  listening on unix://.../AppData/Local/Docker/run/dockerInference:
  remove ...: The file cannot be accessed by the system.
```

Eram três *soquetes órfãos* de comprimento zero em `%LOCALAPPDATA%\Docker
un`
(`dockerInference`, `dockerEthernetVfkit`, `userAnalyticsOtlpHttp.sock`),
deixados por um desligamento sujo: reparse points que o próprio Docker não
consegue remover. A saída é aposentar a pasta inteira -- ele a recria vazia no
próximo start:

```powershell
Rename-Item "$env:LOCALAPPDATA\Docker
un" "run.quebrado-AAAA-MM-DD"
```

Renomear, e não apagar: se o diagnóstico estiver errado, dá para voltar. O
`docker_data.vhdx` não é tocado por isso -- confira o tamanho dele antes e
depois se quiser a prova.

### Outros modos

```bash
python scripts/atualizar_vitrines.py --listar                    # o que está devendo
python scripts/atualizar_vitrines.py --apenas fii_selection --forcar
python scripts/atualizar_vitrines.py --dry-run                   # decide e não executa
```

Gerenciar as tarefas:

```powershell
Get-ScheduledTask -TaskName "DFU - *"
Start-ScheduledTask -TaskName "DFU - Atualizar vitrines"   # rodar agora
Disable-ScheduledTask -TaskName "DFU - Atualizar vitrines" # suspender
```

### O que continua no GitHub Actions

`market-refresh.yml` mantém o refresh da B3 e, dos FIIs, só as duas etapas que
escrevem tabelas nativas do Supabase que o app lê direto (`run_market_ingest.py
fiis` e `benchmark`). Elas ficam remotas de propósito: rodam mesmo com esta
máquina desligada. Todo o resto da cadeia de FIIs saiu de lá.
