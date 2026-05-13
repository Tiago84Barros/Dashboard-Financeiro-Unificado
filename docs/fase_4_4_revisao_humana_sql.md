# Fase 4.4 — Revisão Humana dos Scripts SQL

**Data:** 2026-05-13
**Revisor:** Claude Code (análise estática)
**Status:** ✅ Revisão concluída — aguardando aprovação humana
**Decisão recomendada:** ⚠️ **APROVADO COM AJUSTES** (ver Seção 9)

---

## 1. Resumo Executivo

Foram revisados **8 arquivos SQL** e **1 README** gerados na Fase 4.3, cobrindo
22 tabelas, ~30 índices, 17 policies RLS, 6 views analíticas e dados de referência.

**Resultado geral:**

| Categoria | Resultado |
|-----------|:---------:|
| Comandos destrutivos | ✅ Zero encontrados |
| Credenciais / connection strings | ✅ Zero encontradas |
| Primary Keys | ✅ Todas as 22 tabelas |
| `user_id` em tabelas pessoais | ✅ Todas as 15 tabelas com RLS |
| Tabelas de migração rastreáveis | ✅ source, source_table, source_id |
| Idempotência | ✅ IF NOT EXISTS em todo lugar |
| Separação de domínios | ✅ Domínios claramente separados |
| RLS ativo | ✅ 15 tabelas com policies |
| Lógica SQL das views | ✅ Correta |

**Problemas encontrados:** 0 críticos · 5 médios · 5 baixos

Os scripts são seguros para execução. Os ajustes identificados são melhorias
de robustez, não bloqueadores. Recomendação: aplicar os scripts agora e
corrigir os pontos médios via `009_schema_amendments.sql` na Fase 4.6.

---

## 2. Tabela de Revisão por Arquivo

| Arquivo | Avaliação | Problemas | Pronto? |
|---------|:---------:|:---------:|:-------:|
| `001_core_tables.sql` | ✅ Sólido | 1 baixo | ✅ Sim |
| `002_financial_tables.sql` | ⚠️ Ajuste recomendado | 2 médios, 2 baixos | ✅ Sim* |
| `003_investment_tables.sql` | ⚠️ Ajuste recomendado | 2 médios, 1 baixo | ✅ Sim* |
| `004_import_migration_tables.sql` | ✅ Sólido | 1 baixo | ✅ Sim |
| `005_indexes.sql` | ✅ Sólido | — | ✅ Sim |
| `006_rls_policies.sql` | ⚠️ Ajuste recomendado | 2 médios | ✅ Sim* |
| `007_views.sql` | ⚠️ Atenção em uso | 1 médio | ✅ Sim* |
| `008_seed_reference_data.sql` | ⚠️ Ajuste recomendado | 1 médio | ✅ Sim* |
| `README_EXECUCAO_SQL.md` | ✅ Completo | — | ✅ Sim |

*Sim com ajustes recomendados a aplicar via `009_schema_amendments.sql`.

---

## 3. Verificação de Segurança (Tarefa 1)

### 3.1 Comandos destrutivos

Busca realizada com grep em todos os 8 arquivos `.sql`:

| Padrão | Arquivos verificados | Ocorrências |
|--------|---------------------|:-----------:|
| `DROP TABLE` | 001–008 | **0** ✅ |
| `DROP DATABASE` | 001–008 | **0** ✅ |
| `DROP SCHEMA` | 001–008 | **0** ✅ |
| `DROP INDEX` | 001–008 | **0** ✅ |
| `DROP VIEW` | 001–008 | **0** ✅ |
| `DROP ROLE` | 001–008 | **0** ✅ |
| `DROP POLICY` | 001–008 | **0** ✅ |
| `TRUNCATE` | 001–008 | **0** ✅ |
| `DELETE` | 001–008 | **0** ✅ |
| `UPDATE` em massa | 001–008 | **0** ✅ |
| `ALTER TABLE` destrutivo | 001–008 | **0** ✅ |

> Único `ALTER TABLE` presente: `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` (006) — operação **aditiva e idempotente** ✅

### 3.2 Credenciais e dados sensíveis

| Padrão | Resultado |
|--------|:---------:|
| `password =` / `senha =` / `secret =` | ✅ Zero |
| `@pooler.` / `supabase.co` / `postgresql://` | ✅ Zero |
| `service_role_key` / `anon_key` / `JWT` | ✅ Zero |
| `BYPASSRLS` concedido | ✅ Zero (aparece apenas em comentários de aviso) |
| `.env` mencionado | ⚠️ 2 ocorrências — ambas em comentários SQL (`001`), sem valor real |

**Resultado:** Scripts 100% livres de credenciais. ✅

---

## 4. Consistência Estrutural (Tarefa 2)

### 4.1 Primary Keys

| Tabela | PK | Tipo | OK? |
|--------|-----|------|:---:|
| `profiles` | `id` | `UUID DEFAULT gen_random_uuid()` | ✅ |
| `financial_institutions` | `id` | `UUID DEFAULT gen_random_uuid()` | ✅ |
| `accounts` | `id` | `UUID DEFAULT gen_random_uuid()` | ✅ |
| `cards` | `id` | `UUID DEFAULT gen_random_uuid()` | ✅ |
| `categories` | `id` | `UUID DEFAULT gen_random_uuid()` | ✅ |
| `transactions` | `id` | `UUID DEFAULT gen_random_uuid()` | ✅ |
| `budgets` | `id` | `UUID DEFAULT gen_random_uuid()` | ✅ |
| `financial_goals` | `id` | `UUID DEFAULT gen_random_uuid()` | ✅ |
| `debts` | `id` | `UUID DEFAULT gen_random_uuid()` | ✅ |
| `assets` | `id` | `UUID DEFAULT gen_random_uuid()` | ✅ |
| `portfolios` | `id` | `UUID DEFAULT gen_random_uuid()` | ✅ |
| `portfolio_positions` | `id` | `UUID DEFAULT gen_random_uuid()` | ✅ |
| `investment_transactions` | `id` | `UUID DEFAULT gen_random_uuid()` | ✅ |
| `dividends` | `id` | `UUID DEFAULT gen_random_uuid()` | ✅ |
| `asset_quotes` | `(asset_id, timestamp)` | PK composta | ✅ adequado para série temporal |
| `benchmarks` | `id` | `UUID DEFAULT gen_random_uuid()` | ✅ |
| `benchmark_quotes` | `(benchmark_id, date)` | PK composta | ✅ adequado para série temporal |
| `alerts` | `id` | `UUID DEFAULT gen_random_uuid()` | ✅ |
| `user_settings` | `user_id` | FK = PK (1:1) | ✅ |
| `import_batches` | `id` | `UUID DEFAULT gen_random_uuid()` | ✅ |
| `import_logs` | `id` | `UUID DEFAULT gen_random_uuid()` | ✅ |
| `migration_source_map` | `id` | `UUID DEFAULT gen_random_uuid()` | ✅ |

**22/22 tabelas com PK definida.** ✅

### 4.2 `user_id` nas tabelas sensíveis

| Tabela | `user_id` | NOT NULL | Referência |
|--------|:---------:|:--------:|------------|
| `accounts` | ✅ | ✅ | `profiles(id) CASCADE` |
| `cards` | ✅ | ✅ | `profiles(id) CASCADE` |
| `categories` | ✅ | Nullable¹ | `profiles(id) CASCADE` |
| `transactions` | ✅ | ✅ | `profiles(id) CASCADE` |
| `budgets` | ✅ | ✅ | `profiles(id) CASCADE` |
| `financial_goals` | ✅ | ✅ | `profiles(id) CASCADE` |
| `debts` | ✅ | ✅ | `profiles(id) CASCADE` |
| `portfolios` | ✅ | ✅ | `profiles(id) CASCADE` |
| `portfolio_positions` | ✅ | ✅ | `profiles(id) CASCADE` |
| `investment_transactions` | ✅ | ✅ | `profiles(id) CASCADE` |
| `dividends` | ✅ | ✅ | `profiles(id) CASCADE` |
| `alerts` | ✅ | ✅ | `profiles(id) CASCADE` |
| `user_settings` | ✅ | ✅ (é a PK) | `profiles(id) CASCADE` |
| `import_batches` | ✅ | ✅ | `profiles(id) CASCADE` |

¹ `categories.user_id` é nullable por design: NULL = categoria do sistema (visível a todos). Correto. ✅

**14/14 tabelas pessoais com `user_id` e referência correta.** ✅

### 4.3 Tabelas de migração — rastreabilidade

| Tabela | `source` | `source_table` | `source_id` | `target_table` | `target_id` |
|--------|:--------:|:--------------:|:-----------:|:--------------:|:-----------:|
| `import_batches` | ✅ | — | — | — | — |
| `import_logs` | — | ✅ | ✅ TEXT | ✅ | ✅ UUID |
| `migration_source_map` | ✅ | ✅ | ✅ TEXT | ✅ | ✅ UUID |

**Rastreabilidade completa.** ✅ A combinação `(source, source_table, source_id)` é a chave de idempotência da migração.

### 4.4 `created_at` e `updated_at`

| Tabela | `created_at` | `updated_at` | Observação |
|--------|:------------:|:------------:|------------|
| `profiles` | ✅ | — | Single-user, OK sem updated_at |
| `financial_institutions` | ⚠️ ausente | — | Referência, baixa prioridade |
| `accounts` | ✅ | ⚠️ ausente | Útil para saber quando conta foi editada |
| `cards` | ✅ | — | OK |
| `categories` | ⚠️ ausente | — | Baixa prioridade |
| `transactions` | ✅ | — | OK — transações raramente editadas |
| `budgets` | ⚠️ ausente | — | Útil para histórico de ajustes |
| `financial_goals` | ✅ | ⚠️ ausente | `current_amount` muda com frequência |
| `debts` | ✅ | ⚠️ ausente | `outstanding_balance` muda com frequência |
| `assets` | ⚠️ ausente | — | Referência, baixa prioridade |
| `portfolios` | ✅ | — | OK |
| `portfolio_positions` | — | ✅ | PK temporal bem coberta |
| `investment_transactions` | ✅ | — | Imutável após criação |
| `dividends` | ⚠️ ausente | — | Baixa prioridade |
| `benchmarks` | ⚠️ ausente | — | Referência, baixa prioridade |
| `alerts` | ✅ | — | `triggered_at` supre necessidade |
| `user_settings` | — | ✅ | OK |
| `import_batches` | ✅ (`started_at`) | ✅ (`completed_at`) | OK |
| `import_logs` | ✅ | — | Imutável |
| `migration_source_map` | ✅ (`migrated_at`) | — | Imutável |

**Observação:** As ausências de `created_at`/`updated_at` são **baixo risco** para o MVP. Recomenda-se adicionar via `009_schema_amendments.sql` antes da Fase 4.7 (quando os dados de mercado começarem a ser populados).

### 4.5 Foreign Keys — análise de ON DELETE

| FK | ON DELETE atual | Recomendado | Risco |
|----|:---------------:|:-----------:|:-----:|
| `accounts.user_id → profiles` | CASCADE ✅ | CASCADE | — |
| `accounts.financial_institution_id → financial_institutions` | nenhum | SET NULL | 🟡 Médio |
| `cards.user_id → profiles` | CASCADE ✅ | CASCADE | — |
| `cards.account_id → accounts` | nenhum | SET NULL | 🟡 Médio |
| `cards.financial_institution_id → financial_institutions` | nenhum | SET NULL | 🟡 Médio |
| `categories.parent_id → categories` | nenhum | SET NULL | 🟡 Médio |
| `transactions.account_id → accounts` | nenhum | **RESTRICT** | 🟠 **Médio-alto** |
| `transactions.card_id → cards` | nenhum | SET NULL | 🟡 Médio |
| `transactions.category_id → categories` | nenhum | SET NULL | 🟡 Médio |
| `budgets.category_id → categories` | nenhum | RESTRICT | 🟡 Médio |
| `portfolio_positions.asset_id → assets` | nenhum | RESTRICT | 🟡 Médio |
| `investment_transactions.asset_id → assets` | nenhum | RESTRICT | 🟡 Médio |
| `investment_transactions.portfolio_id → portfolios` | nenhum | SET NULL | 🟡 Médio |
| `dividends.asset_id → assets` | nenhum | RESTRICT | 🟡 Médio |
| `asset_quotes.asset_id → assets` | nenhum | CASCADE | 🟡 Médio |
| `benchmark_quotes.benchmark_id → benchmarks` | nenhum | CASCADE | 🟡 Médio |

**Risco mais alto:** `transactions.account_id` é NOT NULL sem ON DELETE. Se uma conta for deletada diretamente (ex: via Table Editor no Supabase), as transações vinculadas ficam órfãs e quebram o banco. Recomenda-se `ON DELETE RESTRICT` para impedir a exclusão de contas com transações.

### 4.6 Tipos de dados para valores financeiros

| Campo | Tipo atual | Avaliação |
|-------|-----------|:---------:|
| `initial_balance`, `amount`, `amount_limit` | `NUMERIC(15,2)` | ✅ Correto para BRL |
| `unit_price`, `average_price`, `amount_per_unit` | `NUMERIC(15,6)` | ✅ Correto para fracionários |
| `quantity` (investimentos) | `NUMERIC(18,8)` | ✅ Correto para cripto |
| `interest_rate` | `NUMERIC(8,4)` | ✅ Correto (% a.m.) |
| `daily_change_pct` | `NUMERIC(8,6)` | ⚠️ Máx ~99.999999% — pode transbordar em crises extremas |
| `value` (benchmark_quotes) | `NUMERIC(15,8)` | ✅ Correto |
| `return_pct` (view) | `NUMERIC` calculado | ✅ Sem limite, correto para view |
| Moedas | `CHAR(3)` | ✅ ISO 4217 |
| Datas | `DATE` | ✅ Correto |
| Timestamps | `TIMESTAMPTZ` | ✅ Com timezone, correto |

### 4.7 Padronização de nomes

- ✅ Todas as 22 tabelas em inglês, snake_case
- ✅ Views com prefixo `v_`
- ✅ Índices com prefixo `idx_`
- ✅ Políticas com nome descritivo (`<tabela>_<operação>`)
- ✅ Nenhum nome reservado PostgreSQL usado como nome de coluna

---

## 5. Separação de Domínios (Tarefa 3)

| Separação | Status | Detalhes |
|-----------|:------:|----------|
| Transações pessoais × movimentações de investimento | ✅ | `transactions` vs `investment_transactions` — domínios distintos |
| Posição atual × histórico de movimentação | ✅ | `portfolio_positions` (estado) vs `investment_transactions` (histórico) |
| Proventos × cotações | ✅ | `dividends` vs `asset_quotes` — sem mistura |
| Benchmarks × ativos negociáveis | ✅ | Tabelas e séries de cotação separadas |
| Categorias do sistema × categorias do usuário | ✅ | `user_id IS NULL` (sistema) vs `user_id = <uuid>` (usuário) |
| Importações rastreáveis | ✅ | `import_batches` + `import_logs` + `migration_source_map` |
| Tipo de transação coerente | ✅ | `categories.type` e `transactions.type` usam os mesmos valores (`income`, `expense`, `transfer`) |

**Separação de domínios: limpa e correta.** ✅

---

## 6. Verificação RLS (Tarefa 4)

### 6.1 Tabelas com RLS habilitado (15)

`profiles` · `accounts` · `cards` · `categories` · `transactions` · `budgets` ·
`financial_goals` · `debts` · `portfolios` · `portfolio_positions` ·
`investment_transactions` · `dividends` · `alerts` · `user_settings` · `import_batches`

### 6.2 Tabelas sem RLS (7 — dados de mercado e controle)

`financial_institutions` · `assets` · `asset_quotes` · `benchmarks` ·
`benchmark_quotes` · `import_logs` · `migration_source_map`

**Justificativa documentada e coerente:** dados de mercado são públicos; dados de controle interno não contêm PII direta.

### 6.3 Políticas por tabela

| Tabela | Política | Operação | Filtro |
|--------|---------|---------|--------|
| `profiles` | `profiles_owner_select` | SELECT | `id = auth.uid()` |
| `profiles` | `profiles_owner_update` | UPDATE | `id = auth.uid()` |
| `accounts` | `accounts_owner_all` | ALL | `user_id = auth.uid()` |
| `cards` | `cards_owner_all` | ALL | `user_id = auth.uid()` |
| `categories` | `categories_read_owner_or_system` | SELECT | `user_id = auth.uid() OR user_id IS NULL` |
| `categories` | `categories_write_owner` | ALL | `user_id = auth.uid()` |
| `transactions` | `transactions_owner_all` | ALL | `user_id = auth.uid()` |
| `budgets` | `budgets_owner_all` | ALL | `user_id = auth.uid()` |
| `financial_goals` | `goals_owner_all` | ALL | `user_id = auth.uid()` |
| `debts` | `debts_owner_all` | ALL | `user_id = auth.uid()` |
| `portfolios` | `portfolios_owner_all` | ALL | `user_id = auth.uid()` |
| `portfolio_positions` | `positions_owner_all` | ALL | `user_id = auth.uid()` |
| `investment_transactions` | `inv_tx_owner_all` | ALL | `user_id = auth.uid()` |
| `dividends` | `dividends_owner_all` | ALL | `user_id = auth.uid()` |
| `alerts` | `alerts_owner_all` | ALL | `user_id = auth.uid()` |
| `user_settings` | `settings_owner_all` | ALL | `user_id = auth.uid()` |
| `import_batches` | `batches_owner_all` | ALL | `user_id = auth.uid()` |

### 6.4 Riscos RLS identificados

#### 🟡 RISCO MÉDIO — R01: Ausência de policy INSERT para `profiles`

A tabela `profiles` tem apenas `profiles_owner_select` e `profiles_owner_update`.
Não há policy de INSERT.

**Impacto:** Usuário não consegue criar seu próprio perfil via Supabase API (anon key).
**Contexto App 4:** Single-user, conexão direta como `postgres` (bypassa RLS).
**Severidade real:** Baixa para o uso atual; média se o app evoluir para multi-usuário.
**Ação:** Adicionar policy de INSERT em `009_schema_amendments.sql`.

#### 🟡 RISCO MÉDIO — R02: Sobreposição de políticas em `categories`

`categories_read_owner_or_system` (FOR SELECT) e `categories_write_owner` (FOR ALL)
coexistem. `FOR ALL` engloba SELECT — um usuário terá duas políticas SELECT ativas
para suas próprias categorias.

**Impacto:** PostgreSQL aplica `OR` entre políticas — o resultado é correto, mas
há redundância que pode confundir auditorias futuras.
**Severidade:** Baixa (funcionalidade correta).
**Ação:** Refatorar para `categories_write_owner` ser `FOR INSERT, UPDATE, DELETE`.

#### 🟡 RISCO MÉDIO — R03: app4_reader via conexão direta vê zero dados

`app4_reader` é `NOLOGIN` e não tem `BYPASSRLS`. Via conexão SQLAlchemy direta,
`auth.uid()` retorna NULL → todas as policies de RLS retornam FALSE → sem dados.

**Impacto:** O role `app4_reader` só é útil para conexões via Supabase API autenticadas.
Não pode ser usado como conexão SQLAlchemy direta para o App 4.
**Severidade:** Baixa — o App 4 usa `postgres` (que tem BYPASSRLS) por design.
**Ação:** Documentar explicitamente no README que `app4_reader` é para uso API, não SQLAlchemy.

#### 🟢 RISCO BAIXO — R04: `import_logs` e `migration_source_map` sem RLS

Sem RLS, qualquer usuário conectado via API com `anon key` poderia ler todos os
logs de importação de todos os usuários.
**Contexto App 4:** App usa conexão direta; API anon não é usada.
**Severidade:** Baixa no contexto atual; média se o app ganhar múltiplos usuários.

### 6.5 Compatibilidade com Streamlit

| Cenário | Como `auth.uid()` se comporta | RLS funciona? |
|---------|:---------------------------:|:-------------:|
| App 4 — SQLAlchemy como `postgres` | NULL (bypassa RLS) | N/A — BYPASSRLS ativo |
| Supabase API — anon key não autenticado | NULL | ❌ Bloqueia acesso |
| Supabase API — JWT de usuário autenticado | UUID do usuário | ✅ Filtra corretamente |
| app4_reader — SQLAlchemy direto | NULL | ❌ Vê zero dados |

**Conclusão:** A arquitetura atual (App 4 conecta como `postgres` + `WHERE user_id = :owner_id`)
é funcional e segura para single-user. **O filtro `WHERE user_id = :owner_id` no código Python
é a linha de defesa real**, não o RLS.

---

## 7. Verificação das Views (Tarefa 5)

### 7.1 Dependências de tabelas e campos

| View | Tabelas/Views usadas | Campos existem? | OK? |
|------|---------------------|:---------------:|:---:|
| `v_account_balance` | `accounts`, `transactions` | ✅ todos os campos | ✅ |
| `v_monthly_cashflow` | `transactions` | ✅ todos os campos | ✅ |
| `v_category_spending_mtd` | `transactions`, `categories` | ✅ todos os campos | ✅ |
| `v_budget_usage_mtd` | `budgets`, `categories`, `transactions` | ✅ todos os campos | ✅ |
| `v_investment_summary` | `portfolio_positions`, `assets`, `asset_quotes` | ✅ todos os campos | ✅ |
| `v_net_worth` | `v_account_balance`, `v_investment_summary` | ✅ usa views criadas acima | ✅ |

### 7.2 Respeito ao `user_id`

| View | Expõe `user_id`? | App deve filtrar? | Risco |
|------|:----------------:|:-----------------:|:-----:|
| `v_account_balance` | ✅ | `WHERE user_id = :owner_id` | 🟡 |
| `v_monthly_cashflow` | ✅ | `WHERE user_id = :owner_id` | 🟡 |
| `v_category_spending_mtd` | ✅ | `WHERE user_id = :owner_id` | 🟡 |
| `v_budget_usage_mtd` | ✅ | `WHERE user_id = :owner_id` | 🟡 |
| `v_investment_summary` | ✅ | `WHERE user_id = :owner_id` | 🟡 |
| `v_net_worth` | ✅ | `WHERE user_id = :owner_id` | 🟡 |

#### 🟡 RISCO MÉDIO — R05: Views retornam dados de todos os usuários sem filtro

Views em PostgreSQL não têm RLS próprio. Quando o App 4 conecta como `postgres`
(que tem BYPASSRLS), uma query `SELECT * FROM v_net_worth` sem cláusula `WHERE`
retorna dados de **todos** os usuários do banco.

**Impacto:** Em multi-usuário, vazamento de dados seria crítico.
**Contexto App 4:** Single-user + OWNER_USER_ID sempre filtra nas queries Python.
**Severidade:** Baixa para uso atual; **alta** se o app ganhar múltiplos usuários sem ajuste.
**Ação:** Documentar obrigatoriedade do filtro. Avaliar `SECURITY DEFINER` + `SET LOCAL app.current_user_id` em fase futura.

### 7.3 Validação lógica das views

**`v_account_balance`:**
- LEFT JOIN correto — contas sem transações aparecem com `current_balance = initial_balance` ✅
- `COALESCE(tx.settled_sum, 0)` previne NULL aritmético ✅
- Filtra apenas `status = 'settled'` — ignora `pending` e `cancelled` corretamente ✅

**`v_monthly_cashflow`:**
- Exclui `type = 'transfer'` — correto para fluxo de caixa real ✅
- `total_expenses` retorna negativo; `total_expenses_abs` retorna positivo — documentado ✅
- `net_cashflow = SUM(all amount)` para income+expense — matematicamente correto ✅

**`v_category_spending_mtd`:**
- INNER JOIN em `category_id` — transações sem categoria são excluídas ⚠️ (comportamento correto, mas deve estar no README do app para o usuário não achar que os dados estão incompletos)
- `DATE_TRUNC('month', CURRENT_DATE)` — filtra pelo mês corrente dinamicamente ✅

**`v_budget_usage_mtd`:**
- `NULLIF(b.amount_limit, 0)` previne divisão por zero ✅
- `amount_remaining` negativo indica estouro de orçamento — semanticamente correto ✅
- LEFT JOIN: orçamentos sem gastos aparecem com `amount_spent = 0` ✅

**`v_investment_summary`:**
- `LATERAL` corretamente usado para última cotação por ativo ✅
- Fallback para `average_price` quando sem cotação ✅
- `NULLIF(SUM(total_invested), 0)` previne divisão por zero ✅
- `ROUND(..., 2)` — função PostgreSQL, correto ✅
- `GROUP BY pp.user_id, a.class` — correto para sumarização por classe ✅

**`v_net_worth`:**
- CTEs `bank` e `investments` bem estruturadas ✅
- `FULL OUTER JOIN` correto para incluir usuários com apenas um dos tipos de patrimônio ✅
- `COALESCE(..., 0)` previne NULL na soma final ✅
- Dependência de views criadas anteriormente no mesmo arquivo — ordem correta ✅

---

## 8. Problemas Encontrados — Resumo Consolidado

### 🔴 Críticos (0)
Nenhum problema crítico. Scripts podem ser executados.

### 🟠 Médios — requerem correção antes da Fase 4.7

| ID | Arquivo | Problema | Impacto |
|----|---------|---------|---------|
| M01 | `002` | `transactions.account_id` sem `ON DELETE RESTRICT` — transações órfãs se conta for deletada diretamente | Integridade referencial quebrada |
| M02 | `003` | FKs para `assets` sem `ON DELETE RESTRICT` (`portfolio_positions`, `investment_transactions`, `dividends`) | Dados de investimento sem ativo referência |
| M03 | `006` | `profiles` sem policy de INSERT | Criação de perfil bloqueada via API |
| M04 | `006` | `categories_write_owner` (FOR ALL) sobrepõe `categories_read_owner_or_system` (FOR SELECT) | Redundância, confusão em auditoria |
| M05 | `008` | `financial_institutions.type` CHECK não inclui `'digital_bank'` mas exemplo no seed menciona esse valor | Erro de INSERT ao usar 'digital_bank' |

### 🟡 Baixos — melhorias opcionais

| ID | Arquivo | Problema |
|----|---------|---------|
| B01 | `001`/`002`/`003` | `financial_institutions`, `categories`, `budgets`, `assets`, `dividends`, `benchmarks` sem `created_at` |
| B02 | `002`/`003` | `accounts`, `financial_goals`, `debts` sem `updated_at` |
| B03 | `002`/`003` | FKs anuláveis sem `ON DELETE SET NULL` (`cards.account_id`, `transactions.card_id`, `transactions.category_id`, etc.) |
| B04 | `003` | `benchmark_quotes.daily_change_pct NUMERIC(8,6)` — precisão pode ser insuficiente em crises extremas |
| B05 | `007` | Views não filtram `user_id` internamente — app obrigatoriamente deve incluir `WHERE user_id = :owner_id` |

---

## 9. Checklist Humano (Tarefa 6)

### ✅ Scripts aprovados para execução imediata

- [x] `001_core_tables.sql` — Nenhum problema bloqueador
- [x] `002_financial_tables.sql` — Executar; corrigir M01 via 009 depois
- [x] `003_investment_tables.sql` — Executar; corrigir M02 via 009 depois
- [x] `004_import_migration_tables.sql` — Nenhum problema bloqueador
- [x] `005_indexes.sql` — Nenhum problema
- [x] `006_rls_policies.sql` — Executar; corrigir M03 e M04 via 009 depois
- [x] `007_views.sql` — Executar; garantir filtro `WHERE user_id` no app
- [x] `008_seed_reference_data.sql` — Executar; corrigir M05 (não usar 'digital_bank' em financial_institutions)

### ⚠️ Scripts com ajuste pendente (não bloqueadores)

Criar `009_schema_amendments.sql` na Fase 4.6 com:
- [ ] `ALTER TABLE transactions ALTER COLUMN account_id SET ON DELETE RESTRICT`
- [ ] `ALTER TABLE portfolio_positions ALTER COLUMN asset_id SET ON DELETE RESTRICT`
- [ ] Adicionar policy INSERT para `profiles`
- [ ] Refatorar policy `categories_write_owner` para `FOR INSERT, UPDATE, DELETE`
- [ ] Adicionar `'digital_bank'` ao CHECK de `financial_institutions.type` ou remover da documentação

### 🚨 Riscos altos (a monitorar)

- [ ] Views não filtram `user_id` — garantir filtro no código Python em toda query a view
- [ ] `transactions.account_id` sem ON DELETE RESTRICT — nunca deletar contas com transações via Table Editor até 009 ser aplicado

### 🟡 Riscos médios

- [ ] `app4_reader` só funciona via API autenticada, não via SQLAlchemy direto
- [ ] `import_logs` sem RLS — monitorar se o app evoluir para multi-usuário

### 🟢 Riscos baixos

- [ ] `created_at` ausente em 6 tabelas — adicionar via 009 antes da Fase 4.7
- [ ] `daily_change_pct NUMERIC(8,6)` — monitorar se app importar dados de cripto com volatilidade extrema

### ❓ Dúvidas antes de executar

1. **Perfis:** O INSERT em `profiles` será feito via SQL direto ou via algum fluxo de autenticação? (afeta a necessidade de policy INSERT)
2. **financial_institutions:** Quais bancos/corretoras cadastrar inicialmente? Confirmar que 'digital_bank' não é necessário no CHECK type.
3. **Múltiplos usuários:** O App 4 é definitivamente single-user (um proprietário)? Se sim, os riscos de RLS e views são baixos. Se houver planos de multi-usuário, M03 e R05 se tornam críticos.

### 📋 Ordem segura de execução no Supabase SQL Editor

```
001 → 002 → 003 → 004 → 005 → 006 → 007 → 008
(aguardar confirmação de sucesso de cada arquivo antes de prosseguir)
```

---

## 10. Decisão Final

### ⚠️ APROVADO COM AJUSTES

Os 8 scripts SQL estão **tecnicamente corretos e seguros** para execução imediata no Supabase.

**Não há nenhum bloqueador para execução.**

Os 5 problemas médios identificados (M01–M05) são melhorias de robustez que
**não impedem a criação das tabelas** — o banco funcionará corretamente com eles.
Devem ser tratados via `009_schema_amendments.sql` **antes de iniciar a migração
de dados reais (Fase 4.6/4.7)**, pois algumas das correções (ON DELETE RESTRICT)
são mais simples de aplicar com tabelas vazias.

**Cronograma recomendado:**

| Quando | Ação |
|--------|------|
| Agora | Executar 001–008 no Supabase SQL Editor |
| Antes da Fase 4.6 | Aplicar `009_schema_amendments.sql` (ver `fase_4_4_plano_correcao_sql.md`) |
| Na Fase 4.9 | Revisar filtros `WHERE user_id` em todas as queries que usam views |
| Se app for multi-usuário | Revisar R05 (views sem RLS próprio) e R03 (policy INSERT em profiles) |

---

## 11. Próxima Ação Recomendada

**Imediata:** Proprietário executa os 8 scripts no Supabase SQL Editor na ordem indicada.

**Em seguida:**
1. Confirmar as 22 tabelas + 6 views criadas com o checklist do README_EXECUCAO_SQL.md
2. Criar o registro de perfil: `INSERT INTO profiles (name, email, password_hash) VALUES (...) RETURNING id;`
3. Anotar o UUID retornado → adicionar como `OWNER_USER_ID` no `.env`
4. Criar `user_settings`: `INSERT INTO user_settings (user_id) VALUES ('<uuid>') ON CONFLICT DO NOTHING;`
5. Iniciar Fase 4.6 — ETL de migração dos dados históricos

---

*Documento gerado por análise estática dos arquivos SQL. Nenhum SQL foi executado.*
*Banco permanece em MOCK_MODE=true.*
