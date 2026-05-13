# README — Execução dos Scripts SQL

**Banco:** Dashboard Financeiro Unificado (Supabase — schema `public`)
**Fase:** 4.3 — Scripts SQL não destrutivos
**Data:** 2026-05-13

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

> **Regra:** Cada arquivo depende do anterior. Nunca pule etapas.

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

---

## 5. Checklist Pós-execução

Após executar todos os 8 arquivos, verifique:

- [ ] **22 tabelas criadas** — Table Editor deve listar:
  - `profiles`, `financial_institutions`, `accounts`, `cards`, `categories`, `transactions`, `budgets`, `financial_goals`, `debts`, `assets`, `portfolios`, `portfolio_positions`, `investment_transactions`, `dividends`, `asset_quotes`, `benchmarks`, `benchmark_quotes`, `alerts`, `user_settings`, `import_batches`, `import_logs`, `migration_source_map`
- [ ] **6 views criadas** — verificar em Database > Views:
  - `v_account_balance`, `v_monthly_cashflow`, `v_category_spending_mtd`, `v_budget_usage_mtd`, `v_investment_summary`, `v_net_worth`
- [ ] **5 benchmarks inseridos** — executar: `SELECT code, name FROM benchmarks ORDER BY code;`
- [ ] **23 categorias do sistema** — executar: `SELECT name, type FROM categories WHERE user_id IS NULL ORDER BY type, name;`
- [ ] **RLS ativo** — executar: `SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname = 'public' AND rowsecurity = TRUE ORDER BY tablename;` (deve retornar 15 linhas)
- [ ] **Role app4_reader** — executar: `SELECT rolname FROM pg_roles WHERE rolname = 'app4_reader';`

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
