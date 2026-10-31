# Plano de saída do aperto do Supabase free (INF-A2)

Escrito em 04/10/2026. A carência do egress termina em **14/10/2026**. Depois
disso o Supabase responde 402 e o app publicado na Streamlit Cloud fica sem
dados.

Este plano trata dois limites diferentes, que pedem remédios diferentes:

| Limite | Plano free | Situação | Remédio |
|---|---|---|---|
| Tamanho do banco | 500 MB | ~465 MB (auditoria de 04/10; 454,8 MB em 28/09) | apagar ou mover tabela e depois `VACUUM FULL` |
| Egress (bytes que saem do banco) | 5 GB por ciclo | 7,801 GB no ciclo 24/08–24/09 (**estourado**) | ler menos e com menos frequência (cache e artefato local) |

Apagar linha não reduz egress. Cache não reduz tamanho. Cada um dos dois
precisa da sua própria frente.

---

## 1. Números medidos

### 1.1 Egress por ciclo (página Usage, 02/10)

- Ciclo 24/08–24/09: **7,801 GB** contra 5 GB do plano.
- Ciclo 24/09–24/10: **2,08 GB em 8 dias**, uns 130–180 MB/dia, com picos
  de ~480 MB em 26 e 27/09 (dias de `VACUUM FULL` e do espelho). Nesse
  ritmo, a projeção é de ~7 GB, ou seja, estoura de novo.
- **Orçamento: < 160 MB/dia** (5 GB ÷ 31 dias). A margem fica abaixo de
  ~130 MB/dia.

### 1.2 Leituras que o app publicado ainda faz, medidas por leitura

A medição foi feita no armazém local, somente leitura, com
`sum(octet_length(t::text))` sobre a mesma consulta que o app faz no Supabase.
Isso dá o volume em texto, que é como o psycopg2 traz os dados. Não conta o
overhead do protocolo por linha, então o número real fica um pouco acima.

| Leitura (função) | Linhas | Volume por leitura | TTL antes | Pior caso por processo/dia antes | Depois deste PR |
|---|---:|---:|---|---:|---|
| Vitrine de FIIs `market.fii_selection_inputs` (`_load_fii_selection_snapshot`, leitura em 2º plano) | 434 | **2,9 MB** (`payload_json` 2,32 + `coverage_json` 0,33; gz 370 KB) | 900 s, e **descartada** | **~280 MB** | zero enquanto o artefato tiver ≤ 4 dias; senão 1 leitura a cada 6 h, usada se for mais nova |
| Lista e visão geral dos EUA (`us_data.companies`/`overview` → `_snapshot_df`) | 3.701 | **0,87 MB** | 300 s | **~250 MB** | 12 h → ~1,7 MB/dia |
| Histórico de preços dos FIIs (`load_mercado_retorno_mensal`) | 42.628 | **1,24 MB** | 3600 s | ~30 MB | 12 h → ~2,5 MB/dia |
| `company_snapshots` `metrics`+`advanced` (`scored_universe`) | 3.701 | 8,57 MB | 12 h (PR #444) | ~17 MB | igual |
| `_multiplos_long` (B3) | 4.739 | 0,15 MB | 1 h | ~3,6 MB | igual |
| `_annual_upto_long(2025)` (B3) | 4.318 | 0,14 MB | 1 h | ~3,4 MB | igual |
| `load_setores` | 449 | 0,04 MB | 1 h | ~1 MB | igual |

O "pior caso" supõe uma visita logo depois de cada expiração do cache. O
cache do `st.cache_data` vale por processo: cada reinício do container na
Streamlit Cloud começa frio.

Referências de tamanho, também medidas no armazém:

- `company_snapshots`, a linha inteira: 74,78 MB.
- `historical_prices`, a tabela inteira: 153.893 linhas, 28 MB em texto.
- JSON médio por linha da vitrine dos EUA: dossiê 7,4 KB, financials 7,9 KB,
  metrics 1,8 KB, asymmetry 0,8 KB, advanced 0,4 KB.

### 1.3 Por que a vitrine de FIIs era a maior

`_load_fii_selection_snapshot` servia o artefato local verificado
(`data/public/fii_selection_snapshot_v2.json.gz`) e, a cada expiração do
cache de 15 min, disparava em segundo plano uma leitura completa do Supabase
(2,9 MB). O resultado ia para uma memória que o artefato vencia já na chamada
seguinte. Era egress pago para ser jogado fora, até 96 vezes por dia por
processo. A auditoria de 04/10 registrou o problema em duas notas (INF-A2 e
FII).

### 1.4 Tamanho do banco (as maiores tabelas, levantamento de 28/09)

| Tabela | MB | Quem lê |
|---|---:|---|
| `market.calculated_metric_vintages` | 70 | app (B3, PIT) |
| `market_us.prices_monthly` | 64 | app (painel PIT, agora só o recorte do PR #445) |
| `market.brapi_raw_payloads` | 59 | ETL; o app só a junta na linhagem (`core/b3_validation.py::lineage_counts`) quando ela existe — sem ela, lê `data/public/b3_linhagem.json` |
| `market.historical_prices` | 57 | app |
| `market_us.company_snapshots` | 41 | app |
| `market.calculated_metrics` | 33 | app |
| `market.fii_score_snapshots` | 13,5 | app (só a última safra por ticker, em `confianca_ativos`) + ETL |
| índices com `idx_scan = 0` | 16,8 | ninguém: lixo |

`pg_size_pretty` mostra MiB: 434 MiB = 454,8 MB. O teto de 500 MB é em MB.

---

## 2. O que já saiu do Supabase (ou deixou de ser lido em excesso)

| O quê | Onde foi parar | Efeito |
|---|---|---|
| Corpus RAG (chunks) | Parquet local, alvo `rag_corpus` | −162 MB de tamanho |
| `brapi_raw_payloads` antigos | arquivados no armazém; poda diária pelo alvo `brapi_raw_poda` | tamanho controlado; a tabela ainda existe |
| Painel PIT dos EUA | `market_us.score_panel_pub`, pronto (PR #448) | o maior egress de julho a setembro (~2,2 GB) |
| `load_score_panel` | lê 39 mil preços em vez de 368 mil (PR #445) | ~9× menos por leitura |
| `score_panel`, `scored_universe`, `asymmetry_universe`, múltiplos B3 | TTL de 12 h (PRs #444/#445) | — |
| `fii_metrics_monthly`, macro, `valuation_historico` | artefatos em `data/public/` | sem leitura remota |
| PR #501 | vitrine de FIIs sem leitura descartada; `companies`/`overview` e `load_mercado_retorno_mensal` com TTL de 12 h | ~−560 MB/dia por processo, no pior caso |
| Ingestão brapi da B3 (caminho A, 05/10/2026) | saiu do GitHub (`refresh-b3` só por disparo manual) para os alvos locais `b3_brapi`/`b3_brapi_anual`, com `--warehouse`; `scripts/publish_b3_brapi_from_local.py` leva só o que o app lê, por marca d'água | sem leitura de demonstrações e preços a cada recálculo; nenhum payload bruto da B3 gravado no Supabase |
| PR seguinte (INF-A2, parte 2) | `us_data.dossie()` com cache de 12 h por ticker; `us_data._use_snapshot()` guarda o "sim" por 12 h; linhagem B3 desacoplada de `brapi_raw_payloads` | ~7 KB e 2–3 consultas a menos por rerun da aba EUA; a tabela pode sair sem quebrar a validação |

## 3. O que ainda falta

Tamanho do banco:

1. **`brapi_raw_payloads`** (~59–71 MB). Quem lê é o ETL. A cópia local
   está completa (18.622 payloads, zero hashes ausentes em 16/08). Falta a
   compactação remota, que é do usuário (§5.4).
   **Destravado do lado do app (INF-A2, parte 2):** antes, tirá-la inteira
   fazia a junção de `lineage_counts` falhar e derrubava o manifesto inteiro
   da validação B3 (Empresas B3, Confiança, Portfólio B3). Agora
   `lineage_counts` decide a fonte:
   - com a tabela no banco, mede ali (`verificacao='banco'`). A poda diária
     preserva todo payload referenciado, então a medida segue válida;
   - sem ela, lê `data/public/b3_linhagem.json` (`verificacao='artefato_armazem'`,
     com `gerado_em`, `idade_dias` e `velho` acima de 14 dias). Esse arquivo
     sai do armazém pelo alvo semanal `b3_linhagem`
     (`scripts/publish_b3_linhagem.py`). As linhas do banco atual seguem ao
     lado, em `rows_banco_atual`;
   - sem os dois, `verificacao='indisponivel'`: linhas e ponteiros contados,
     baldes em `None`. Nunca "toda linha sem origem".
   Antes do `DROP`, ainda falta confirmar que nenhuma ingestão grava payloads
   no Supabase. `data_pipeline/market/repository.py` insere em
   `market.brapi_raw_payloads` na conexão que recebe, e o crescimento de
   ~2,6 MB/dia que motivou o alvo `brapi_raw_poda` indica que alguma ainda
   grava. Sem a tabela, essa ingestão falharia.
   **Atualização de 05/10/2026:** a ingestão da B3 saiu do Supabase (caminho A,
   §2). Resta o job `refresh-fiis` do `market-refresh.yml`: o passo
   `run_market_ingest.py fiis` roda contra o Supabase e grava lá o payload
   `quote_fii_full`. O `DROP` depende de mudá-lo também.
2. **16,8 MB de índices mortos** e os 3 índices únicos duplicados sobre
   `docs_corporativos.doc_hash`. O SQL está pronto em
   `warehouse/remote_cleanup.md` (§5.3).
3. **`fii_score_snapshots`** (13,5 MB). O levantamento de 16/08 dizia "só ETL",
   mas desde o A-150 `core/global_portfolio/confianca_ativos.py::_fii` lê dela
   em runtime: só a `reference_date` mais recente por ticker. **Não pode sair
   inteira.** O que dá para fazer é podar as safras antigas, depois de
   arquivá-las no armazém (§5.5). Antes, é preciso confirmar que o
   `fii_monitoring` não precisa do histórico remoto.
4. **`calculated_metric_vintages`** (70 MB) não tem política de retenção e
   cresce a cada ingestão. O app lê o histórico PIT, então não dá para podar
   às cegas: falta definir quais safras o PIT precisa (por exemplo, a primeira
   e a última de cada período fiscal) e só então podar.
5. Item 5 do roteiro da auditoria: aplicar a migration 080; publicar
   `docs_corporativos`, `fii_metrics` e `info_economica`; tirar
   `brapi_raw_payloads` do Supabase.

Egress, fora do escopo deste PR e já observado:

- ~~`us_data.dossie()` não tem cache~~: resolvido na parte 2 do INF-A2
  (12 h por ticker; a ausência não fica guardada).
- ~~`us_data._use_snapshot()` não tem cache~~: resolvido na parte 2. O "sim"
  vale 12 h. O "não" é refeito, porque falha transitória também responde
  "não" e não pode fixar o modo.
- `pgbouncer.get_auth`: 370 mil conexões novas desde julho. Cada handshake
  TLS custa alguns KB.
- O topo atual do `pg_stat_statements` não foi medido nesta sessão, porque a
  leitura no Supabase foi negada pelo classificador. A consulta está em §5.2.
  **Rode-a antes de mexer em mais coisa.**

---

## 4. Ordem de execução

| # | Quando | O quê | Quem |
|---|---|---|---|
| 1 | já | Merge deste PR e **Reboot** da Streamlit Cloud. Só o `core/` mudou, e a Cloud só recarrega o `app.py` | usuário |
| 2 | dia seguinte | Ver na Usage o egress de 24 h; meta < 130 MB/dia | usuário |
| 3 | se passar de 130 MB/dia | Rodar o `pg_stat_statements` (§5.2) e atacar o novo topo | usuário + PR |
| 4 | até 10/10 | `DROP INDEX CONCURRENTLY` dos índices mortos (§5.3) | usuário |
| 5 | até 10/10 | Compactar `brapi_raw_payloads` e depois `VACUUM FULL` (§5.4). Tirá-la inteira está destravado no app (§3, item 1); antes, confirmar quem ainda grava nela | usuário |
| 6 | se o banco ainda passar de 480 MB | Arquivar e podar as safras antigas de `fii_score_snapshots` (§5.5) | usuário |
| 7 | depois | Retenção de `calculated_metric_vintages` (precisa de PR com teste PIT) | PR |
| 8 | se um gatilho disparar | Plano pago ou outro host (§6) | usuário |

### Gatilhos

| Gatilho | Ação |
|---|---|
| Banco **> 480 MB** (em MB, não MiB) | Executar já os passos 4–6. Se ainda passar, ir para §6 |
| Egress do ciclo **> 80 %** (4 GB) antes do dia 24 | Contratar o Pro (§6) antes de estourar. Estouro reincidente perde a carência |
| Egress diário **> 160 MB** por 3 dias seguidos | Rodar o `pg_stat_statements` e abrir PR contra o topo |
| Vitrine de FIIs com `fallback_reason = local_artifact_stale` por mais de 4 dias | A rotina noturna não publicou o artefato: ver `fii_selection` na agenda |

---

## 5. Comandos que o usuário precisa rodar

O Claude Code não grava no Supabase: o classificador bloqueia DDL, `DELETE` e
manutenção em produção. Todos os comandos abaixo são para você.

### 5.1 Depois do merge

1. Streamlit Cloud → app → **Reboot**.
2. Supabase → Organization → **Usage** → Egress. Anote o valor do dia e
   compare no dia seguinte.

### 5.2 Leituras de diagnóstico (só leitura, SQL Editor serve)

```sql
-- tamanho real, em MB (o pg_size_pretty mostra MiB)
SELECT pg_database_size(current_database()) / 1e6 AS mb;

-- o que mais tira linhas do banco desde o último reset
SELECT calls,
       rows,
       rows / NULLIF(calls, 0)                AS linhas_por_chamada,
       round(total_exec_time::numeric / 1000) AS seg_total,
       left(regexp_replace(query, '\s+', ' ', 'g'), 140) AS consulta
FROM pg_stat_statements
ORDER BY rows DESC
LIMIT 20;

-- opcional: zerar para medir só o efeito deste PR (anote a data do reset)
-- SELECT pg_stat_statements_reset();
```

Linhas × largura da linha dá uma estimativa do egress. O protocolo texto do
psycopg2 deixa o volume 1,5 a 2× acima da largura binária.

### 5.3 Índices mortos (−16,8 MB)

A lista completa e o SQL de reversão estão em
`warehouse/remote_cleanup.md`. Antes de rodar, confira que o
`idx_scan` continua 0:

```sql
SELECT schemaname, indexrelname, idx_scan,
       pg_relation_size(indexrelid) / 1e6 AS mb
FROM pg_stat_user_indexes
WHERE indexrelname IN ('idx_metric_vintages_lookup', 'idx_calcmetric_conf',
                       'idx_chunks_ticker_date', 'docs_chunks_ix_ticker',
                       'idx_brapi_raw_endpoint', 'idx_dul_job_name',
                       'idx_brapi_raw_payloads_supersedes', 'idx_dul_started_at');
```

`DROP INDEX CONCURRENTLY` não roda dentro de transação. Rode um por vez,
por conexão direta (psql) ou no SQL Editor com uma instrução por execução.

### 5.4 Compactar `brapi_raw_payloads` e recuperar o espaço

`DELETE` sozinho não devolve espaço: o arquivo só encolhe com `VACUUM FULL`.
Rode-o por **conexão direta** (porta 5432, não o pooler), em AUTOCOMMIT. O
SQL Editor corta a sessão por volta de 2 min.

```sql
-- 1) ninguém segurando lock nem transação aberta
SELECT pid, state, xact_start, left(query, 80)
FROM pg_stat_activity
WHERE datname = current_database()
  AND (state = 'idle in transaction' OR xact_start < now() - interval '5 min');
-- encerre o que sobrar: SELECT pg_terminate_backend(<pid>);

-- 2) não esperar lock indefinidamente
SET lock_timeout = '10s';

-- 3) a compactação em si é o script, que se recusa a rodar se faltar algum
--    hash no armazém (ver warehouse/remote_cleanup.md):
--      python -m scripts.compact_remote_brapi_raw          (simulação)
--      python -m scripts.compact_remote_brapi_raw --apply
--    Se a poda tiver sido por DELETE em vez de recriar a tabela:
VACUUM (FULL, ANALYZE) market.brapi_raw_payloads;

-- 4) conferir
SELECT pg_database_size(current_database()) / 1e6 AS mb;
```

### 5.5 Podar as safras antigas de `fii_score_snapshots`

O app lê só a safra mais recente de cada ticker
(`confianca_ativos._fii`, `DISTINCT ON (ticker) ... ORDER BY reference_date DESC`).
O ETL (`fii_monitoring`, `fii_enrichment`) também lê essa tabela. Por isso,
confira antes que nenhum dos dois precisa do histórico remoto.

1. Arquive a tabela no armazém, com `pg_dump -t market.fii_score_snapshots`
   do Supabase e restauração no `dfu_warehouse`. Confira que as contagens
   batem.
2. Veja quanto sairia:

   ```sql
   SELECT count(*) FILTER (WHERE rn > 1) AS sairiam, count(*) AS total
   FROM (SELECT row_number() OVER (PARTITION BY ticker
                                   ORDER BY reference_date DESC) AS rn
         FROM market.fii_score_snapshots) t;
   ```

3. Pode e recupere o espaço, com o mesmo cuidado do §5.4 (conexão direta,
   `lock_timeout`):

   ```sql
   DELETE FROM market.fii_score_snapshots f
   USING (SELECT ticker, max(reference_date) AS ultima
          FROM market.fii_score_snapshots GROUP BY ticker) u
   WHERE f.ticker = u.ticker AND f.reference_date < u.ultima;
   VACUUM (FULL, ANALYZE) market.fii_score_snapshots;
   ```

---

## 6. Alternativa paga ou outro host

Os preços abaixo são a referência pública de 2026. **Confira na página de
preços antes de contratar.**

| Opção | Custo | Banco | Egress | Mudança no código |
|---|---|---|---|---|
| **Supabase Pro** | US$ 25/mês (~R$ 140) | 8 GB incluídos | 250 GB incluídos; ~US$ 0,09/GB acima | nenhuma: mesma URL |
| Neon (Postgres serverless) | plano pago a partir de ~US$ 19/mês; o free tem limite de computação e de armazenamento | 10 GB+ no pago | cobrado por transferência | trocar `DATABASE_URL` e migrar com `pg_dump`/`pg_restore` |
| VPS próprio (Hetzner, Contabo etc.) com Postgres | ~US$ 5–10/mês | o disco da máquina (40–80 GB) | 20 TB incluídos (Hetzner) | migrar e passar a cuidar de backup, TLS e atualização; o pgvector precisa ser instalado |
| Túnel para o armazém local (PR #341) | zero | o armazém (vários GB) | sem cota | já existe só para leitura; depende do PC ligado, então não serve como fonte principal da Cloud |

A recomendação é o **Supabase Pro** se, depois do Reboot, o egress diário
continuar acima de 160 MB ou o banco passar de 480 MB. Os US$ 25 eliminam os
dois limites de uma vez, sem migração e sem risco de o app sair do ar em
14/10. As outras opções custam menos por mês, mas cobram em migração e em
operação.
