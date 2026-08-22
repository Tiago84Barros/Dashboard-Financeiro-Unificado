# README — Execução dos Scripts SQL

**Banco:** Dashboard Financeiro Unificado (Supabase — schema `public`)
**Fase:** Schema operacional completo
**Atualizado:** 2026-08-17

---

## 1. Ordem de Execução

Execute os arquivos **exatamente nesta sequência** no SQL Editor do Supabase:

| # | Arquivo | O que faz | Tabelas/Objetos |
|---|---------|-----------|-----------------|
| 1 | `001_core_tables.sql` | Tabelas centrais | `profiles`, `financial_institutions` |
| 2 | `002_financial_tables.sql` | Finanças pessoais | `accounts`, `cards`, `categories`, `transactions`, `budgets`, `financial_goals`, `debts` |
| 3 | `003_investment_tables.sql` | Investimentos e mercado | `assets`, `portfolios`, `portfolio_positions`, `investment_transactions`, `dividends`, `asset_quotes`, `benchmarks`, `benchmark_quotes` |
| 4 | `004_import_migration_tables.sql` | Alertas, preferências, migração | `alerts`, `user_settings`, `import_batches`, `import_logs`, `migration_source_map` |
| 5 | `005_indexes.sql` | Índices de performance | ~30 índices nas 22 tabelas |
| 6 | `006_rls_policies.sql` | Segurança Row Level Security | Role `app4_reader`, 15 tabelas com RLS, 17 policies |
| 7 | `007_views.sql` | Views analíticas | 6 views: `v_account_balance`, `v_monthly_cashflow`, `v_category_spending_mtd`, `v_budget_usage_mtd`, `v_investment_summary`, `v_net_worth` |
| 8 | `008_seed_reference_data.sql` | Dados iniciais | 5 benchmarks, 23 categorias do sistema |
| 9 | `009_schema_amendments.sql` | Ajustes do schema base | Colunas e constraints incrementais |
| 10 | `010_portfolio_position_snapshots.sql` | Snapshots de carteira | Histórico de posições |
| 11 | `011_b3_portfolio_models.sql` | Carteira modelo B3 | Modelos e itens |
| 12 | `012_bank_statement_imports.sql` | Importação bancária | Auditoria de lotes |
| 13 | `013_market_brapi_schema.sql` | Schema de mercado | `market.*` |
| 14 | `014_legacy_isolation.sql` | Isolamento legado | Compatibilidade |
| 15 | `015_market_fiis.sql` | FIIs | Ranking e métricas |
| 16 | `016_fiis_cvm.sql` | Dados CVM de FIIs | Identificação e patrimônio |
| 17 | `017_fiis_detalhe.sql` | Detalhes dos FIIs | Imóveis e métricas mensais |
| 18A | `018_fiis_hardening.sql` | Restrições de FIIs | Integridade e RLS |
| 18B | `018_rls_portfolio_models.sql` | RLS das carteiras B3 | Policies e unicidade |
| 19 | `019_point_in_time.sql` | Proveniência contábil | `first_seen_at` e payload |
| 20A | `020_b3_portfolio_hardening.sql` | Restrições da carteira B3 | Pesos e versão |
| 20B | `020_fii_score_snapshot.sql` | Snapshot de score FII | Evidência de score FII |
| 21 | `021_market_metric_vintages.sql` | Vintages imutáveis | Histórico das métricas |
| 22 | `022_market_pit_cutover.sql` | Corte temporal PIT | Quarentena do baseline |
| 23 | `023_fii_methodology_v4.sql` | Metodologia FII v4 | Observações PIT, exposições, scores e validações |
| 24 | `024_fii_pro_data_foundation.sql` | Fundação Brapi Pro | Releases, `knowledge_at`, qualidade, documentos e linhagem |
| 25 | `025_fii_cvm_structured_coverage.sql` | Cobertura CVM estruturada | Prontidão dos dados e metodologia FII v4.1 |
| 26 | `026_fii_observation_maintenance_indexes.sql` | Manutenção FII | Índices para vintages e substituição idempotente por fonte |
| 27 | `027_data_api_rls_hardening.sql` | Hardening da Data API | RLS e exposição de schemas |
| 28 | `028_private_policy_and_function_hardening.sql` | Hardening privado | Policies e funções privadas |
| 29 | `029_fii_cri_history_document_evolution.sql` | Evolução FII/CRI | Histórico, documentos e fila |
| 30 | `030_fii_pipeline_indexes.sql` | Índices do pipeline FII | FKs e filas de processamento |
| 31 | `031_fii_methodology_v5.sql` | Metodologia FII v5 | Registro e referências metodológicas |
| 32 | `032_historical_price_legacy_defaults.sql` | Compatibilidade de preços | Defaults temporários para coletores legados |
| 33 | `033_fii_pit_validation_and_calibration.sql` | Metodologia FII v6 | PIT, backtest, calibração e monitoramento |
| 34 | `034_fii_v6_covering_indexes.sql` | Índices FII v6 | Cobertura dos relacionamentos v6 |
| 35 | `035_fii_b3_archive_checkpoints.sql` | Checkpoints B3 | Retomada por arquivo oficial |
| 36 | `036_fii_cvm_archive_checkpoints.sql` | Checkpoints CVM | Releases estruturados e parser |
| 37 | `037_fii_b3_parser_checkpoints.sql` | Checkpoints de parser B3 | Reprocessamento por versão do parser |
| 38 | `038_fii_cri_archive_checkpoints.sql` | Checkpoints CRI | Arquivos públicos da CVM |
| 39 | `039_fii_selection_inputs_snapshot.sql` | Vitrine de seleção FII | Payload compacto para o App 4 |
| 40 | `040_market_us_schema.sql` | Schema de mercado EUA | Estrutura `market_us` |
| 41A | `041_fii_evidence_review_and_rls.sql` | Evidência FII e RLS | Revisão auditável e hardening |
| 41B | `041_market_us_portfolio.sql` | Carteira de mercado EUA | Dados de carteira `market_us` |
| 42A | `042_fii_document_source_hash_storage.sql` | Linhagem documental FII | Hash, tamanho e MIME da fonte |
| 42B | `042_market_us_outliers.sql` | Outliers EUA | Regras de qualidade `market_us` |
| 43A | `043_b3_validation_and_pit_audit.sql` | Auditoria B3/PIT | Evidência reproduzível de validações |
| 43B | `043_market_us_retained_earnings.sql` | Lucros retidos EUA | Dados fundamentais `market_us` |
| 44A | `044_b3_audit_immutability.sql` | Imutabilidade B3 | Trilha append-only |
| 44B | `044_market_us_snapshot.sql` | Snapshot EUA | Snapshot de mercado `market_us` |
| 45 | `045_market_us_quality_v3.sql` | Qualidade EUA v3 | Controles `market_us` |
| 46 | `046_fii_official_documents_and_projects.sql` | Documentos/projetos FII | Fontes oficiais e observações |
| 47 | `047_us_portfolio_models.sql` | Carteira modelo EUA | `us_portfolio_models`, `us_portfolio_model_items`, índices e RLS |
| 48 | `048_market_us_macro.sql` | Macro EUA | Dados macroeconômicos `market_us` |
| 49 | `049_portfolio_asset_snapshots.sql` | Snapshots de carteira | Ativos e metas de alocação |

> **Regra:** execute exatamente na ordem acima. Os arquivos com o mesmo número
> usam sufixos A/B para fixar a ordem documental: `018_fiis_hardening.sql` antes
> de `018_rls_portfolio_models.sql`, `020_b3_portfolio_hardening.sql` antes de
> `020_fii_score_snapshot.sql`, e assim sucessivamente para 041–044. Não pule
> 027–049: as migrations posteriores podem depender de tabelas, policies,
> funções ou índices já criados. Execute `047_us_portfolio_models.sql` somente
> após 001–046: ele depende de `profiles` (001) e completa o preflight exigido
> pela persistência de carteira EUA. O aplicativo não cria schema em runtime.

---

## 2. Como Executar no Supabase SQL Editor

1. Acesse o painel do projeto: [app.supabase.com](https://app.supabase.com)
2. Vá em **SQL Editor** (menu lateral esquerdo)
3. Clique em **New query**
4. Copie e cole o conteúdo completo do arquivo
5. Clique em **Run** (ou `Ctrl+Enter`)
6. Verifique a mensagem de sucesso antes de prosseguir para o próximo arquivo
7. Repita para cada arquivo na ordem da tabela acima

---

## 3. Alertas de Segurança

### O que estes scripts NÃO fazem (garantido)

- ❌ **Sem `DROP TABLE`** — nenhuma tabela é destruída
- ❌ **Sem `TRUNCATE`** — nenhuma linha é apagada
- ❌ **Sem `DELETE`** — nenhum registro é removido
- ❌ **Sem credenciais** — nenhuma senha ou connection string hardcoded
- ❌ **Sem `DROP SCHEMA`** — o schema `public` é preservado

### O que fazer se um script falhar

1. **Leia a mensagem de erro** — geralmente indica qual tabela não existe (faltou executar um arquivo anterior)
2. **Execute os arquivos anteriores** que estavam faltando
3. **Reexecute o arquivo com falha** — todos usam `IF NOT EXISTS`, portanto são seguros para reexecutar
4. **Nunca execute fora de ordem**

### Permissões necessárias

Você deve estar conectado como `postgres` (owner do banco) ou ter permissão de `SUPERUSER` para:
- `CREATE TABLE`
- `ALTER TABLE ... ENABLE ROW LEVEL SECURITY`
- `CREATE ROLE`
- `GRANT SELECT`

No Supabase, o SQL Editor executa como `postgres` por padrão — nenhuma configuração adicional é necessária.

---

## 4. Checklist Pré-execução

Antes de executar qualquer script, confirme:

- [ ] Você está no projeto correto no painel Supabase ("Dashboard Financeiro Unificado", não "Controle Financeiro")
- [ ] O banco está na versão PostgreSQL 17.x (verificar em Settings > Database)
- [ ] Nenhuma migração de dados está em andamento (nenhum ETL rodando)
- [ ] Você tem acesso ao SQL Editor (permissão de `postgres`)
- [ ] `MOCK_MODE = "true"` no Streamlit Secrets — a aplicação **não** deve acessar o banco durante a execução dos scripts
- [ ] Os scripts 001–046 aplicáveis ao ambiente foram concluídos sem erro; em
  particular, `profiles` existe antes de executar `047_us_portfolio_models.sql`
- [ ] Há backup verificável e plano de reversão testado em dados descartáveis
  antes de aplicar 047, que cria tabelas, índices, RLS e policies

---

## 5. Checklist Pós-execução

Após executar todos os scripts até 047, verifique:

- [ ] **22 tabelas criadas** — Table Editor deve listar:
  - `profiles`, `financial_institutions`, `accounts`, `cards`, `categories`, `transactions`, `budgets`, `financial_goals`, `debts`, `assets`, `portfolios`, `portfolio_positions`, `investment_transactions`, `dividends`, `asset_quotes`, `benchmarks`, `benchmark_quotes`, `alerts`, `user_settings`, `import_batches`, `import_logs`, `migration_source_map`
- [ ] **6 views criadas** — verificar em Database > Views:
  - `v_account_balance`, `v_monthly_cashflow`, `v_category_spending_mtd`, `v_budget_usage_mtd`, `v_investment_summary`, `v_net_worth`
- [ ] **5 benchmarks inseridos** — executar: `SELECT code, name FROM benchmarks ORDER BY code;`
- [ ] **23 categorias do sistema** — executar: `SELECT name, type FROM categories WHERE user_id IS NULL ORDER BY type, name;`
- [ ] **RLS ativo** — executar: `SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname = 'public' AND rowsecurity = TRUE ORDER BY tablename;` (deve retornar 15 linhas)
- [ ] **Role app4_reader** — executar: `SELECT rolname FROM pg_roles WHERE rolname = 'app4_reader';`
- [ ] **Carteira EUA criada e protegida por RLS** — executar:
  `SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname = 'public' AND tablename IN ('us_portfolio_models', 'us_portfolio_model_items') ORDER BY tablename;`;
  deve retornar as duas tabelas com `rowsecurity = true`
- [ ] **Policies da carteira EUA** — executar:
  `SELECT tablename, policyname FROM pg_policies WHERE schemaname = 'public' AND tablename IN ('us_portfolio_models', 'us_portfolio_model_items') ORDER BY tablename, policyname;`;
  deve retornar `us_portfolio_models_owner_all` e `us_portfolio_model_items_owner_all`

---

## 6. Grant adicional para views (executar após 007)

Após criar as views em `007_views.sql`, libere acesso ao role `app4_reader`:

```sql
GRANT SELECT ON TABLE
    v_account_balance,
    v_monthly_cashflow,
    v_category_spending_mtd,
    v_budget_usage_mtd,
    v_investment_summary,
    v_net_worth
TO app4_reader;
```

> Este comando está comentado ao final de `006_rls_policies.sql` como lembrete.

---

## 7. Arquitetura de Segurança (resumo)

```
Conexão via Supabase API (anon key)
  └─ RLS ativo → policies verificam auth.uid()
  └─ app4_reader → apenas SELECT

Conexão via SQLAlchemy (postgres / App 4)
  └─ Bypass RLS por padrão (conexão direta)
  └─ Filtro real: WHERE user_id = :owner_id no código Python
  └─ auth.uid() retorna NULL — comportamento esperado
```

**Nunca** conceder `BYPASSRLS`, `INSERT`, `UPDATE` ou `DELETE` ao role `app4_reader`.

---

## 8. Próximos passos (após execução bem-sucedida)

1. **Fase 4.4** — ETL Python: migrar dados históricos dos Apps 1, 2 e 3 para o banco unificado
2. **Fase 4.7** — Integração com APIs externas: popular `asset_quotes` e `benchmark_quotes`
3. **Fase 4.9** — Conectar o App 4 (Streamlit) ao banco (desativar `MOCK_MODE`)
