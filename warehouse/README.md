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

O dump completo do Supabase inclui schemas internos (auth, storage, etc.) que
não existem/importam localmente. Restauramos **só `public` e `market`** e
criamos a extensão `vector` antes:

```powershell
# se você tinha subido com a imagem antiga (postgres:17), recrie com a nova:
docker compose up -d              # recria o container com pgvector/pgvector:pg17

# extensão do RAG (idempotente)
docker compose exec warehouse psql -U postgres -d postgres -c "CREATE EXTENSION IF NOT EXISTS vector;"

# restaura apenas public + market (ignora auth/storage/etc. do Supabase)
docker compose exec -T warehouse `
  pg_restore -U postgres -d postgres --no-owner --clean --if-exists `
  -n public -n market /dumps/metadados.dump
```

> Alguns avisos de "role does not exist" / "already exists, skipping" são
> esperados e inofensivos (pg_restore segue em frente). O que importa é as
> tabelas de `public`/`market` ficarem lá — validamos no comando abaixo.

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

## Passo 5 — Publicar a vitrine (local → Supabase)

Copia só as tabelas de `tables_vitrine.txt` (as que o app lê ao vivo) do armazém
para o Supabase, com **truncate + load** (data-only), sem tocar nas finanças
pessoais. Rode a partir de `warehouse/`, com `$env:SUPABASE_URL` já definido:

```powershell
# monta a lista "-t market.x -t market.y ..." a partir do arquivo
$tabs = (Get-Content tables_vitrine.txt | Where-Object { $_ -and $_ -notmatch '^\s*#' })
$targs = ($tabs | ForEach-Object { "-t", $_ }) 

# 1) dump data-only da vitrine, do LOCAL, para dumps/vitrine.dump
docker run --rm -v "${PWD}\dumps:/dumps" --network host postgres:17 `
  pg_dump "postgresql://postgres:<senha>@host.docker.internal:5433/postgres" `
  -Fc --data-only --no-owner @targs -f /dumps/vitrine.dump

# 2) truncate das mesmas tabelas no Supabase
$trunc = "TRUNCATE " + ($tabs -join ", ") + " RESTART IDENTITY CASCADE;"
docker run --rm postgres:17 psql "$env:SUPABASE_URL" -c $trunc

# 3) carrega a vitrine no Supabase
docker run --rm -v "${PWD}\dumps:/dumps" postgres:17 `
  pg_restore "$env:SUPABASE_URL" --data-only --no-owner /dumps/vitrine.dump
```

> ⚠️ `TRUNCATE ... CASCADE` só nas tabelas de mercado da vitrine. Confirme que
> `tables_vitrine.txt` não contém nenhuma tabela pessoal antes de rodar.
> Vamos validar este passo juntos com uma tabela pequena primeiro
> (ex.: `market.fiis`) antes de rodar a lista inteira.

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
| Lista da vitrine + fluxo de publicação | ✅ `tables_vitrine.txt` + Passo 5 |
| Refator das 2 funções de leitura (`load_fii_methodology_inputs`, exposições) | ⏳ próximo |
| Edição do `market-refresh.yml` | ✅ cargas pesadas removidas do Supabase |

## Supabase remoto ainda acima do limite

O warehouse local não reduz automaticamente o banco remoto. Depois que o
workflow remoto foi desativado para cargas pesadas, ainda é necessário remover
as tabelas históricas que já foram acumuladas no Supabase. Essa remoção deve ser
precedida por validação da vitrine e aprovação explícita, pois envolve `DROP
TABLE`. Consulte `warehouse/remote_cleanup.md` para o roteiro somente leitura e
a lista de tabelas armazém.

## Expansão auditável da base de FIIs

O warehouse local é o destino dos históricos extensos. Para ampliar a base sem
look-ahead e sem reprocessar arquivos idênticos, aplique as migrations até
`038_fii_cri_archive_checkpoints.sql` e execute o orquestrador:

```powershell
python run_market_ingest.py fiis-enrich --warehouse --years 5 `
  --candidate-limit 12 --document-limit 150 --document-budget-mb 250 --json
```

Para executar etapas isoladas:

```powershell
# CVM: histórico estruturado completo disponível desde 2016
python run_market_ingest.py fiis-cvm-structured --years 11 --json

# B3: security master/COTAHIST desde 2010, incluindo fundos encerrados
python run_market_ingest.py fiis-b3-history --years 17 --json

# PDFs recentes, com orçamento de 250 MB e reserva de 10 GB no disco
python run_market_ingest.py fiis-documents --warehouse --limit 50 --recent-months 24 `
  --max-batch-mb 250 --max-document-mb 30 --min-free-gb 10 --json
```

Cada arquivo CVM é identificado por `SHA-256`, encadeado em
`market.fii_source_releases` e controlado por parser em
`market.fii_cvm_archive_loads` e `market.fii_cri_archive_loads`. Uma reapresentação
gera nova revisão; um hash já concluído com a mesma versão do parser é ignorado.
Os PDFs ficam no filesystem
endereçados por conteúdo, enquanto o Postgres guarda hash, tamanho, URL,
evidências e versão do parser.
