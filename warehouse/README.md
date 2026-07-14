# Armazém local (warehouse) — runbook da migração

Objetivo: tirar do Supabase (plano Free, **0,5 GB por projeto**) os ~1,3 GB de
dados pesados de mercado/FII que a interface **não lê ao vivo**, movendo-os para
um Postgres local no seu PC. O Supabase passa a guardar só a **vitrine**
(~0,32 GB) que o app Streamlit consome.

> **Como as ações estão divididas**
> - Tudo que é **código** (este scaffolding, scripts, ajustes de workflow, job de
>   publicação) é preparado pelo assistente.
> - Os comandos que **conectam no seu Supabase** (dump, `DROP`, `VACUUM`) usam a
>   sua senha do banco e são executados **por você** — o assistente não manipula
>   credenciais.
> - Estes comandos ainda **não foram testados na sua máquina**; a ideia é rodar
>   cada passo juntos, conferindo o resultado antes de seguir.

---

## Pré-requisitos

- **Docker Desktop** para Windows instalado e rodando.
- Sua **connection string do Supabase `metadados`** (Dashboard → Project Settings
  → Database → Connection string → URI). Formato:
  `postgresql://postgres:SENHA@db.jdvijvfrjfpbnlyfxltr.supabase.co:5432/postgres`
  Use a conexão **direta** (porta 5432), não o pooler, para dump/restore.

Guarde-a numa variável de ambiente da sua sessão do PowerShell (assim ela não
fica em arquivo nem é digitada nos comandos):

```powershell
$env:SUPABASE_URL = "postgresql://postgres:SENHA@db.jdvijvfrjfpbnlyfxltr.supabase.co:5432/postgres"
```

---

## Passo 1 — Subir o Postgres local

```powershell
cd warehouse
copy .env.example .env
# edite .env e troque WAREHOUSE_PASSWORD por uma senha sua
docker compose up -d
docker compose ps          # deve mostrar dfu_warehouse "healthy"
```

Local disponível em: `postgresql://postgres:<senha>@localhost:5433/postgres`

## Passo 2 — Dump completo do Supabase

Rodamos o `pg_dump` **de dentro** de um container `postgres:17` (mesma versão do
Supabase), então você não precisa instalar client local:

```powershell
# a partir de warehouse/  (a pasta dumps/ é montada no container)
mkdir dumps -ErrorAction SilentlyContinue
docker run --rm -v "${PWD}\dumps:/dumps" postgres:17 `
  pg_dump "$env:SUPABASE_URL" -Fc --no-owner --no-privileges -f /dumps/metadados.dump
```

Confere o arquivo: `dir dumps\metadados.dump` (deve ter ~centenas de MB).

## Passo 3 — Restaurar no armazém local

```powershell
docker compose exec -T warehouse `
  pg_restore -U postgres -d postgres --no-owner --clean --if-exists /dumps/metadados.dump
```

Valide (deve bater com o Supabase, ~1,6 GB):

```powershell
docker compose exec warehouse psql -U postgres -d postgres -c `
  "SELECT pg_size_pretty(SUM(pg_total_relation_size(c.oid))) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind='r' AND n.nspname IN ('public','market');"
```

## Passo 4 — Rodar a ingestão apontando para o local

O app já resolve a engine por `SUPABASE_UNIFICADO_URL` (e `core/database.py` usa
conexão sem SSL quando o host é `localhost`). Então basta rodar a ingestão com
essa variável apontando para o armazém:

```powershell
# na raiz do repositório
$env:SUPABASE_UNIFICADO_URL = "postgresql://postgres:<senha>@localhost:5433/postgres"
$env:APP_ENV = "production"; $env:MOCK_MODE = "false"
python run_market_ingest.py daily --source market --json
python run_market_ingest.py fiis --json
# ...demais passos do market-refresh.yml, agora escrevendo no LOCAL
```

> A partir daqui, **os dados pesados crescem no seu PC**, não no Supabase.

## Passo 5 — Publicar a vitrine (local → Supabase)   ⏳ *a construir*

Um script `warehouse/publish_vitrine.py` (próximo incremento) vai copiar só as
tabelas da vitrine do local para o Supabase. Ele lê duas connection strings de
ambiente (`WAREHOUSE_URL` e `SUPABASE_URL`) — você fornece, o assistente não vê.

## Passo 6 — Desligar os passos pesados no GitHub Actions   ⏳ *a construir*

Editar `.github/workflows/market-refresh.yml` para **não** rodar mais a ingestão
pesada na nuvem (senão ela recria/repovoa as tabelas no Supabase). Fica só o que
alimenta a vitrine — ou, se a ingestão inteira migrar para o PC, desativa-se o
schedule e mantém-se `workflow_dispatch` manual.

## Passo 7 — Remover as pesadas do Supabase (durável) e compactar

**Só depois** dos passos 5 e 6 (senão o pipeline repovoa). Rode no SQL Editor do
`metadados`, na ordem: linhagem primeiro (é derivada), depois o resto.

```sql
-- Ver warehouse/tables_armazem.txt para a lista canônica.
DROP TABLE IF EXISTS market.fii_lineage_edges;
DROP TABLE IF EXISTS market.fii_metric_observations;
DROP TABLE IF EXISTS market.fii_b3_security_history;
DROP TABLE IF EXISTS market.cri_security_observations;
DROP TABLE IF EXISTS market.fii_exposures;
DROP TABLE IF EXISTS market.brapi_raw_payloads;
DROP TABLE IF EXISTS market.historical_price_observations;
DROP TABLE IF EXISTS market.calculated_metric_vintages;
DROP TABLE IF EXISTS market.fii_documents;
DROP TABLE IF EXISTS market.fii_source_releases;
DROP TABLE IF EXISTS market.fii_registry_observations;
DROP TABLE IF EXISTS public.multiplos_TRI;

-- Recupera o espaço físico (o DROP libera, mas VACUUM consolida o catálogo).
VACUUM;
```

Confira o novo tamanho (esperado ~0,32 GB):

```sql
SELECT pg_size_pretty(SUM(pg_total_relation_size(c.oid)))
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE c.relkind='r' AND n.nspname IN ('public','market');
```

---

## Alívio imediato (independe da migração)

Antes de tudo isso, o `VACUUM FULL` já recupera o inchaço das semanas de cron a
cada 5 min, sem apagar nada. Ver o bloco no chat / SQL Editor.

## Estado atual do scaffolding

| Item | Status |
|---|---|
| Postgres local (docker-compose) | ✅ pronto |
| Lista canônica de tabelas | ✅ `tables_armazem.txt` |
| Runbook dump/restore/ingest local | ✅ este arquivo |
| `publish_vitrine.py` | ⏳ próximo |
| Refator das 2 funções de leitura (`load_fii_methodology_inputs`, exposições) | ⏳ próximo |
| Edição do `market-refresh.yml` | ⏳ próximo |
