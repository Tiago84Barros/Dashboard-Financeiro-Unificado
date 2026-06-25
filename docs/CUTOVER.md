# Cutover: legado (public.*) → market.* (BRAPI Pro)

Plano de virada da leitura de dados B3, da fonte legada (scraping yfinance/
Fundamentus/Status Invest em `public.*`) para a fonte nova (BRAPI Pro em
`market.*`). Reversível: basta voltar `MARKET_READ_SOURCE=legacy`.

## Gatilho
- **Só executar quando a cobertura `market/legado` ≥ 90%** (porteiro em
  `core/b3_data.market_coverage()`; ver na página *Saúde dos Dados*).
- Hoje: ~57%. Drenando via `bootstrap-brapi.yml` (cron diário).

## Já pronto (não precisa esperar o cutover)
- Facade `core/b3_data.py` com flag `MARKET_READ_SOURCE` (legacy|market|compare)
  + porteiro de cobertura (`market` só ativa ≥90%).
- Leitura `core/market_read.py` (setores, múltiplos atuais, histórico anual com
  valuation exato).
- Reparos das telas **já são sensíveis à flag** (`_db.market_active()`): com
  `market` ativo, `empresas_b3` NÃO roda MICE, NÃO imputa por mediana de grupo e
  NÃO faz patch via Fundamentus — nulo vira rank neutro. Winsorização e percentil
  permanecem (são método de ranking, não reparo).
- Painel `core/market_health.py` (qualidade do market.*) já na página.

## Passos do cutover (PR único, ao atingir 90%)

### 1. Virar a flag
- Definir `MARKET_READ_SOURCE=market` (Variable do Actions / `secrets.toml`).
- A partir daqui a UI lê do `market.*` e os reparos das telas se desligam sozinhos.

### 2. Desativar jobs legados (registry + workflow)
- `data_pipeline/update_registry.py`: `is_active=False` (ou `frequency='manual'`)
  para: `update_b3_fundamentals`, `update_dividendos`, `audit_and_heal`,
  `heal_fundamentals`, `update_b3_quotes` (parte B3), `update_cvm`, `update_macro`.
- `.github/workflows/data_pipeline.yml`: remover passos de audit/heal e updates
  legados de B3; **manter** `update_bcb`, `update_fx_rates`, `update_cvm_ipe`,
  `update_empresas_eua` e o passo internacional de cotações.

### 3. Remover código de reparo (balde ❌)
- `core/data_healing.py`, `core/data_reconciliacao.py` (parte de scraping).
- `data_pipeline/jobs/audit_and_heal.py`, `heal_fundamentals.py`,
  `update_b3_fundamentals.py`, `update_dividendos.py`, `update_cvm.py`,
  `update_macro.py`, `update_brapi_history.py` (confirmar superado).
- `data_pipeline/quality/`: `comparer.py`, `sanitizer.py`, `updater.py`,
  `score.py`, `report.py`, `validator.py` (manter `scheduler.py` — usado pelo
  `market/ingest`).
- `views/empresas_b3.py`: remover `_impute_with_group_median`,
  `_enrich_multiplos_fallback_web`, `_fundamentus_fallback_canonico` e o uso de
  `core/mice_imputer` (hoje só desativados pela flag).
- **Manter**: `_winsorize_series`, `_percentile_score` e estatística robusta
  (mediana, outlier de preço no backtest).

### 4. Aposentar a leitura legada
- `core/b3_data.py`: tornar `market` o default; decidir se mantém `b3_db` apenas
  como fallback de emergência ou remove de vez.
- `core/b3_db.py` + tabelas `public.multiplos`, `public.setores`,
  `public."Demonstracoes_Financeiras"`: remover/arquivar após período de
  observação.

### 5. Página Saúde dos Dados
- Remover seções do healing legado (`data_quality_scores/reports`,
  `data_healing_audit`); manter a seção `market.*` (já pronta).
- Congelar/arquivar tabelas `data_quality_*`, `multiplos_healing_backup`.

## Validação pós-cutover
1. `python run_market_ingest.py parity` — conferir paridade final.
2. Abrir Empresas B3 / Análise Avançada / Portfólio e checar ranking sane.
3. Página Saúde dos Dados: completude e anomalias do market.*.
4. Rollback se necessário: `MARKET_READ_SOURCE=legacy`.

## Gaps conhecidos (não bloqueiam)
- Bancos/seguradoras: lucro líquido não padronizado → menos métricas (esperado).
- ~2 tickers (ex.: VALE) sem LPA → valuation anual aproximado (rotulado).
- Macro continua via BCB (brapi não assumiu); CVM segue como identidade/setor.
