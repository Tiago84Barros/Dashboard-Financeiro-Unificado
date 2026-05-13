# Fase 4.3 — Scripts SQL Não Destrutivos

**Data:** 2026-05-13
**Status:** ✅ Concluída
**Objetivo:** Criar scripts SQL versionados, idempotentes e não destrutivos que documentam e reproduzem o schema completo do banco unificado.

---

## Resumo Executivo

A Fase 4.3 entregou **8 arquivos SQL** cobrindo as 22 tabelas canônicas do banco unificado,
índices de performance, políticas RLS, views analíticas e dados de referência iniciais.

Todos os scripts foram validados textualmente: **zero comandos destrutivos** e
**zero credenciais** encontrados.

---

## Arquivos Criados

| # | Arquivo | Tamanho | O que contém |
|---|---------|---------|--------------|
| 1 | `schema/001_core_tables.sql` | ~120 linhas | `profiles`, `financial_institutions` |
| 2 | `schema/002_financial_tables.sql` | ~171 linhas | `accounts`, `cards`, `categories`, `transactions`, `budgets`, `financial_goals`, `debts` |
| 3 | `schema/003_investment_tables.sql` | ~174 linhas | `assets`, `portfolios`, `portfolio_positions`, `investment_transactions`, `dividends`, `asset_quotes`, `benchmarks`, `benchmark_quotes` |
| 4 | `schema/004_import_migration_tables.sql` | ~130 linhas | `alerts`, `user_settings`, `import_batches`, `import_logs`, `migration_source_map` |
| 5 | `schema/005_indexes.sql` | ~183 linhas | ~30 índices com `CREATE INDEX IF NOT EXISTS` |
| 6 | `schema/006_rls_policies.sql` | ~298 linhas | Role `app4_reader`, RLS em 15 tabelas, 17 policies |
| 7 | `schema/007_views.sql` | ~175 linhas | 6 views analíticas |
| 8 | `schema/008_seed_reference_data.sql` | ~200 linhas | 5 benchmarks, 23 categorias do sistema |
| — | `schema/README_EXECUCAO_SQL.md` | ~150 linhas | Guia completo de execução |

**Total:** ~1.400 linhas de SQL documentado e versionado.

---

## Tabelas Cobertas (22/22)

### Dados pessoais (com RLS)
| Tabela | Arquivo | RLS | Policy |
|--------|---------|:---:|--------|
| `profiles` | 001 | ✅ | `profiles_owner_select` + `profiles_owner_update` |
| `accounts` | 002 | ✅ | `accounts_owner_all` |
| `cards` | 002 | ✅ | `cards_owner_all` |
| `categories` | 002 | ✅ | `categories_read_owner_or_system` + `categories_write_owner` |
| `transactions` | 002 | ✅ | `transactions_owner_all` |
| `budgets` | 002 | ✅ | `budgets_owner_all` |
| `financial_goals` | 002 | ✅ | `goals_owner_all` |
| `debts` | 002 | ✅ | `debts_owner_all` |
| `portfolios` | 003 | ✅ | `portfolios_owner_all` |
| `portfolio_positions` | 003 | ✅ | `positions_owner_all` |
| `investment_transactions` | 003 | ✅ | `inv_tx_owner_all` |
| `dividends` | 003 | ✅ | `dividends_owner_all` |
| `alerts` | 004 | ✅ | `alerts_owner_all` |
| `user_settings` | 004 | ✅ | `settings_owner_all` |
| `import_batches` | 004 | ✅ | `batches_owner_all` |

### Dados de mercado/controle (sem RLS)
| Tabela | Arquivo | Justificativa |
|--------|---------|---------------|
| `financial_institutions` | 001 | Dados públicos de bancos |
| `assets` | 003 | Dados de mercado (ativos) |
| `asset_quotes` | 003 | Cotações de ativos |
| `benchmarks` | 003 | Índices/taxas de referência |
| `benchmark_quotes` | 003 | Cotações de benchmarks |
| `import_logs` | 004 | Controle interno (não dado pessoal) |
| `migration_source_map` | 004 | Idempotência da migração |

---

## Views Analíticas Criadas (6)

| View | Descrição | Depende de |
|------|-----------|------------|
| `v_account_balance` | Saldo atual = `initial_balance` + SUM(settled) | `accounts`, `transactions` |
| `v_monthly_cashflow` | Receitas, despesas, net por mês | `transactions` |
| `v_category_spending_mtd` | Gastos por categoria no mês corrente | `transactions`, `categories` |
| `v_budget_usage_mtd` | Planejado vs realizado no mês corrente | `budgets`, `transactions`, `categories` |
| `v_investment_summary` | Posição consolidada por classe de ativo | `portfolio_positions`, `assets`, `asset_quotes` |
| `v_net_worth` | Patrimônio líquido total | `v_account_balance`, `v_investment_summary` |

### Notas técnicas das views

- **`v_account_balance`**: `current_balance = initial_balance + COALESCE(SUM(settled), 0)` — LEFT JOIN com subquery para não excluir contas sem transações.
- **`v_monthly_cashflow`**: GROUP BY `DATE_TRUNC('month', due_date)::DATE` — usa `due_date`, não `payment_date`, para consistência com o orçamento.
- **`v_category_spending_mtd`**: exclui transações sem `category_id` (NULL); expõe `icon` e `color` para o frontend.
- **`v_budget_usage_mtd`**: `amount_remaining` negativo = orçamento estourado; `usage_pct` calculado com `NULLIF(amount_limit, 0)` para evitar divisão por zero.
- **`v_investment_summary`**: usa `LATERAL JOIN` para buscar a cotação mais recente de cada ativo; fallback para `average_price` quando não há cotação.
- **`v_net_worth`**: `FULL OUTER JOIN` entre `bank` e `investments` CTEs — inclui usuários com apenas contas ou apenas investimentos.

---

## Dados de Referência Inseridos (arquivo 008)

### Benchmarks (5)
| Código | Nome | Tipo | Frequência |
|--------|------|------|------------|
| `IBOV` | Ibovespa | index | daily |
| `CDI` | CDI | rate | daily |
| `IPCA` | IPCA | rate | monthly |
| `SELIC` | SELIC | rate | daily |
| `IFIX` | IFIX | index | daily |

### Categorias do sistema (23, `user_id = NULL`)
- **5 de receita:** Salário, Freelance, Investimentos, Aluguel Recebido, Outros Rendimentos
- **14 de despesa:** Moradia, Alimentação, Transporte, Saúde, Educação, Lazer, Vestuário, Assinaturas, Telefone/Internet, Pets, Impostos e Taxas, Seguros, Presentes e Doações, Outras Despesas
- **4 de transferência:** Transferência entre Contas, Aporte em Investimento, Resgate de Investimento, Pagamento de Fatura

---

## Políticas RLS — Detalhamento

### Role `app4_reader`
- Criado como `NOLOGIN` (sem login direto)
- `GRANT SELECT` nas 22 tabelas + (a executar) 6 views
- **Nunca** conceder: `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`, `BYPASSRLS`

### Arquitetura de segurança em camadas

```
Camada 1 (App 4 — SQLAlchemy): WHERE user_id = :owner_id em toda query
Camada 2 (Supabase API — anon key): RLS policies com auth.uid()
Camada 3 (Princípio do menor privilégio): app4_reader tem apenas SELECT
```

> Nota: `auth.uid()` retorna NULL em conexões SQLAlchemy diretas.
> Isso é esperado e correto — o filtro real está na Camada 1.

---

## Validação Textual (tarefa 9)

Busca executada em todos os 8 arquivos SQL por:

| Padrão buscado | Resultado |
|----------------|:---------:|
| `DROP TABLE` | ✅ Zero ocorrências |
| `DROP SCHEMA` | ✅ Zero ocorrências |
| `TRUNCATE` | ✅ Zero ocorrências |
| `DELETE` | ✅ Zero ocorrências |
| `DROP INDEX` | ✅ Zero ocorrências |
| `DROP ROLE` | ✅ Zero ocorrências |
| `DROP POLICY` | ✅ Zero ocorrências |
| `DROP VIEW` | ✅ Zero ocorrências |
| `password` / `senha` / `secret` / `@pooler` | ✅ Zero ocorrências |
| `BYPASSRLS` concedido | ✅ Zero ocorrências (mencionado apenas em comentário de aviso) |

**Resultado:** Scripts são 100% não destrutivos e não contêm credenciais.

---

## Relação com Fase 4.5

Os scripts da Fase 4.3 documentam o estado atual do banco, que foi aplicado **diretamente**
via Python/SQLAlchemy na Fase 4.5 (com autorização explícita do proprietário).

| O que a Fase 4.5 aplicou | Coberto em qual script |
|--------------------------|----------------------|
| 22 tabelas `CREATE TABLE IF NOT EXISTS` | 001–004 |
| 11 índices básicos | 005 (versão completa com ~30 índices) |
| RLS em 15 tabelas + 15 policies | 006 (versão completa com 17 policies) |
| Role `app4_reader` + GRANT SELECT | 006 |
| 5 benchmarks | 008 |
| (não aplicado ainda) 6 views analíticas | 007 — **a executar no SQL Editor** |
| (não aplicado ainda) ~19 índices adicionais | 005 — **a executar no SQL Editor** |
| (não aplicado ainda) 2 policies adicionais | 006 — **a executar no SQL Editor** |
| (não aplicado ainda) 23 categorias do sistema | 008 — **a executar no SQL Editor** |

---

## Pendências para Execução no Supabase

Os scripts são idempotentes — podem ser executados sem risco de duplicação:

1. **Executar `007_views.sql`** no SQL Editor → cria as 6 views analíticas
2. **Executar o GRANT de views** (bloco comentado em `006_rls_policies.sql`) → libera `app4_reader`
3. **Executar `005_indexes.sql`** (os ~19 índices que a Fase 4.5 não criou) → melhora performance
4. **Executar `008_seed_reference_data.sql`** → insere as 23 categorias do sistema

> A ordem exata para reexecução segura: 005 → 006 (apenas o GRANT) → 007 → 007 GRANT → 008.

---

## Riscos Identificados

| Risco | Probabilidade | Mitigação |
|-------|:------------:|-----------|
| Executar scripts fora de ordem | Baixa | README_EXECUCAO_SQL.md tem checklist e tabela de ordem |
| Executar no projeto errado (Controle Financeiro) | Baixa | Checklist pré-execução no README |
| `v_net_worth` retornar valores incorretos antes de ter dados | Normal | Views retornam NULL/0 quando não há dados — comportamento correto |
| `v_investment_summary` sem cotações (tabela vazia) | Esperado | Fallback para `average_price` no LATERAL JOIN |
| Categorias do sistema duplicadas em reexecução | Nenhum | Cada INSERT em 008 usa `DO $$ IF NOT EXISTS ... $$` |

---

## Próxima Fase

**Fase 4.4 → Fase 4.6 — Scripts de Migração ETL**

Antes de iniciar:
1. Criar o registro em `profiles` para o proprietário
2. Copiar o UUID → `OWNER_USER_ID` no `.env`
3. Criar `user_settings` para o perfil criado
4. Executar os scripts pendentes no SQL Editor (005, 007, 008 e GRANT de views)
