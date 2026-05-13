# Banco Unificado — Mapa de Origem → Destino

> Documento: `docs/banco_unificado_mapa_origem_destino.md`
> Criado em: 2026-05-13
> Fase: 4.2 — Modelo Canônico
> Status: Rascunho — acompanha `docs/banco_unificado_modelo_canonico.md`
> Propósito: mapear cada fonte de dados para a tabela e coluna de destino no banco unificado.

---

## Fontes de Dados

| Fonte | Identificador | Tipo | Variável de conexão |
|-------|:------------:|------|---------------------|
| Schema atual do App 4 | `app4_atual` | PostgreSQL / Supabase | `SUPABASE_UNIFICADO_URL` |
| Dashboard Financeiro (App 1) | `app1` | PostgreSQL / Supabase | `SOURCE_DB_APP1` |
| Dashboard-Investimentos (App 2) | `app2` | SQLite | `SOURCE_DB_APP2` |
| Controle Financeiro (App 3) | `app3` | PostgreSQL / Supabase | `SUPABASE_ORIGEM_CONTROLE_URL` |

> **app4_atual** = tabelas já existentes no banco alvo (10 tabelas em português).
> Estas serão **renomeadas**, não recriadas. Os dados existentes são preservados.

---

## Seção 1 — Renomeação das 10 Tabelas Atuais (português → inglês)

> Operação: `ALTER TABLE <nome_antigo> RENAME TO <nome_novo>`
> Esta operação é **não destrutiva** — preserva todos os dados e índices.
> Será incluída nos scripts da Fase 4.3 / executada na Fase 4.5.

| # | Tabela atual (português) | Tabela canônica (inglês) | Operação |
|---|--------------------------|--------------------------|:--------:|
| 1 | `usuarios` | `profiles` | RENAME |
| 2 | `contas` | `accounts` | RENAME |
| 3 | `categorias` | `categories` | RENAME |
| 4 | `transacoes` | `transactions` | RENAME |
| 5 | `orcamentos` | `budgets` | RENAME |
| 6 | `metas` | `financial_goals` | RENAME |
| 7 | `ativos` | `assets` | RENAME |
| 8 | `operacoes` | `investment_transactions` | RENAME |
| 9 | `proventos` | `dividends` | RENAME |
| 10 | `cotacoes` | `asset_quotes` | RENAME |

**Atenção:** após o RENAME, os seguintes arquivos precisarão ser atualizados (Fase 4.9):
- `etl/schema_setup.py` — `_DDL` e `TABELAS_ESPERADAS`
- `etl/importacao.py` — `_TABELAS_VALIDAS`
- `core/financeiro.py` — todas as queries SQL inline

---

## Seção 2 — Renomeação de Colunas nas 10 Tabelas Existentes

> Operação: `ALTER TABLE <tabela> RENAME COLUMN <col_antiga> TO <col_nova>`
> Apenas colunas cujo nome muda; colunas com mesmo nome em ambos os idiomas são omitidas.

### `profiles` (era `usuarios`)

| Coluna atual | Coluna canônica | Mudança |
|--------------|----------------|:-------:|
| `nome` | `name` | RENAME |
| `senha_hash` | `password_hash` | RENAME |
| `criado_em` | `created_at` | RENAME |
| `ativo` | `active` | RENAME |
| `id`, `email` | `id`, `email` | — (sem mudança) |

### `accounts` (era `contas`)

| Coluna atual | Coluna canônica | Mudança |
|--------------|----------------|:-------:|
| `usuario_id` | `user_id` | RENAME |
| `nome` | `name` | RENAME |
| `tipo` | `type` | RENAME |
| `banco` | *(removida)* | — coluna `banco` será substituída por `financial_institution_id` |
| `saldo_inicial` | `initial_balance` | RENAME |
| `moeda` | `currency` | RENAME |
| `ativo` | `active` | RENAME |
| `criado_em` | `created_at` | RENAME |
| — | `financial_institution_id` | ADD COLUMN (nullable) |

### `categories` (era `categorias`)

| Coluna atual | Coluna canônica | Mudança |
|--------------|----------------|:-------:|
| `usuario_id` | `user_id` | RENAME |
| `nome` | `name` | RENAME |
| `tipo` | `type` | RENAME |
| `icone` | `icon` | RENAME |
| `cor` | `color` | RENAME |
| `pai_id` | `parent_id` | RENAME |

### `transactions` (era `transacoes`)

| Coluna atual | Coluna canônica | Mudança |
|--------------|----------------|:-------:|
| `usuario_id` | `user_id` | RENAME |
| `conta_id` | `account_id` | RENAME |
| `categoria_id` | `category_id` | RENAME |
| `descricao` | `description` | RENAME |
| `valor` | `amount` | RENAME |
| `data_competencia` | `due_date` | RENAME |
| `data_pagamento` | `payment_date` | RENAME |
| `tipo` | `type` | RENAME |
| `recorrente` | `recurring` | RENAME |
| `parcela_atual` | `installment_current` | RENAME |
| `total_parcelas` | `installment_total` | RENAME |
| `grupo_parcela` | `installment_group` | RENAME |
| `origem` | `source` | RENAME |
| `criado_em` | `created_at` | RENAME |
| — | `card_id` | ADD COLUMN (nullable) |

### `budgets` (era `orcamentos`)

| Coluna atual | Coluna canônica | Mudança |
|--------------|----------------|:-------:|
| `usuario_id` | `user_id` | RENAME |
| `categoria_id` | `category_id` | RENAME |
| `mes_ano` | `month_year` | RENAME |
| `valor_limite` | `amount_limit` | RENAME |

### `financial_goals` (era `metas`)

| Coluna atual | Coluna canônica | Mudança |
|--------------|----------------|:-------:|
| `usuario_id` | `user_id` | RENAME |
| `nome` | `name` | RENAME |
| `tipo` | `type` | RENAME |
| `valor_alvo` | `target_amount` | RENAME |
| `valor_acumulado` | `current_amount` | RENAME |
| `prazo` | `deadline` | RENAME |
| `ativa` | `active` | RENAME |
| `criado_em` | `created_at` | RENAME |

### `assets` (era `ativos`)

| Coluna atual | Coluna canônica | Mudança |
|--------------|----------------|:-------:|
| `nome` | `name` | RENAME |
| `classe` | `class` | RENAME |
| `setor` | `sector` | RENAME |
| `moeda` | `currency` | RENAME |
| `ticker`, `exchange`, `id` | iguais | — |

### `investment_transactions` (era `operacoes`)

| Coluna atual | Coluna canônica | Mudança |
|--------------|----------------|:-------:|
| `usuario_id` | `user_id` | RENAME |
| `ativo_id` | `asset_id` | RENAME |
| `tipo` | `type` | RENAME |
| `quantidade` | `quantity` | RENAME |
| `preco_unitario` | `unit_price` | RENAME |
| `taxas` | `fees` | RENAME |
| `data_operacao` | `transaction_date` | RENAME |
| `corretora` | `broker` | RENAME |
| `criado_em` | `created_at` | RENAME |
| — | `portfolio_id` | ADD COLUMN (nullable) |

### `dividends` (era `proventos`)

| Coluna atual | Coluna canônica | Mudança |
|--------------|----------------|:-------:|
| `usuario_id` | `user_id` | RENAME |
| `ativo_id` | `asset_id` | RENAME |
| `tipo` | `type` | RENAME |
| `valor_por_cota` | `amount_per_unit` | RENAME |
| `quantidade` | `quantity` | RENAME |
| `valor_total` | `total_amount` | RENAME |
| `data_com` | `ex_date` | RENAME |
| `data_pagamento` | `payment_date` | RENAME |

### `asset_quotes` (era `cotacoes`)

| Coluna atual | Coluna canônica | Mudança |
|--------------|----------------|:-------:|
| `ativo_id` | `asset_id` | RENAME |
| `abertura` | `open` | RENAME |
| `maxima` | `high` | RENAME |
| `minima` | `low` | RENAME |
| `fechamento` | `close` | RENAME |
| `timestamp`, `volume` | iguais | — |

---

## Seção 3 — Tabelas Novas (sem dados de origem)

| Tabela | Motivo | População |
|--------|--------|-----------|
| `financial_institutions` | Centralizar bancos/corretoras | Manual + SQLite `institutions` (App 2) |
| `cards` | Cartões de crédito | Manual |
| `debts` | Dívidas e financiamentos | Manual |
| `portfolios` | Carteiras de investimento | Manual |
| `portfolio_positions` | Posições consolidadas | Calculada a partir de `investment_transactions` |
| `benchmarks` | IBOVESPA, CDI, IPCA, SELIC, IFIX | Manual (seed) |
| `benchmark_quotes` | Séries históricas de benchmarks | API (yfinance / BCB) |
| `alerts` | Alertas de preço/orçamento | Manual |
| `user_settings` | Preferências da interface | Seed automático (1 linha por usuário) |
| `import_batches` | Controle de importações | Automático pelo ETL |
| `import_logs` | Log por registro importado | Automático pelo ETL |
| `migration_source_map` | Mapa origem→destino para idempotência | Automático pelo ETL |

---

## Seção 4 — Migração de Dados do App 3 (Controle Financeiro — PostgreSQL)

> Fonte: `SUPABASE_ORIGEM_CONTROLE_URL` (somente leitura)
> Estratégia: INSERT ... ON CONFLICT DO NOTHING
> Operação: somente após Fase 4.5 (schema aplicado)

| Tabela Origem (App 3) | Tabela Destino | Colunas mapeadas | Observação |
|----------------------|----------------|-----------------|------------|
| `transacoes` | `transactions` | Todas as colunas + `user_id = OWNER_USER_ID` | Verificar nomes de colunas na auditoria 4.1 |
| `contas` | `accounts` | Todas + `user_id = OWNER_USER_ID` | Verificar se `banco` existe como coluna |
| `categorias` | `categories` | Todas + `user_id = OWNER_USER_ID` | Manter subcategorias (pai_id → parent_id) |
| `orcamentos` | `budgets` | Todas + `user_id = OWNER_USER_ID` | Verificar formato da coluna de mês |
| `metas` | `financial_goals` | Todas + `user_id = OWNER_USER_ID` | Se existir a tabela |

> **Atenção:** os nomes exatos das colunas no banco do App 3 devem ser confirmados
> na **Fase 4.1** (auditoria). Este mapeamento assume que o App 3 usa schema equivalente
> ao `modelagem_inicial.md`.

---

## Seção 5 — Migração de Dados do App 2 (Dashboard-Investimentos — SQLite)

> Fonte: `SOURCE_DB_APP2` → `sqlite:///caminho/para/investment_dashboard.db`
> Estratégia: INSERT ... ON CONFLICT DO NOTHING
> SQLAlchemy suporta SQLite nativamente — mesma interface de `ImportadorPostgres`

| Tabela Origem (App 2 SQLite) | Tabela Destino | Colunas mapeadas | Observação |
|------------------------------|----------------|-----------------|------------|
| `assets` | `assets` | `ticker`, `name`, `class`, `sector`, `currency`, `exchange` | ON CONFLICT (ticker) DO NOTHING |
| `institutions` | `financial_institutions` | `name`, `type` | Sem CNPJ no SQLite — preencher NULL |
| `accounts` | `accounts` | `name`, `type`, `institution_id` → `financial_institution_id` | Requer mapeamento de IDs de instituições |
| `transactions` | `investment_transactions` | `asset_id`, `type` (buy/sell), `quantity`, `price` → `unit_price`, `fees`, `date` → `transaction_date`, `broker` | Requer mapeamento de asset_id |
| `incomes` | `dividends` | `asset_id`, `type`, `amount_per_unit`, `quantity`, `total_amount`, `ex_date`, `payment_date` | Verificar nomes exatos das colunas |
| `xp_positions` | `portfolio_positions` | `asset_id`, `quantity`, `average_price`, `total_invested` | Associar a portfolio_id padrão criado na migração |
| `position_snapshots` | `portfolio_positions` | Snapshot mais recente por ativo | Usar MAX(date) para cada ativo |
| `sync_log` | `import_logs` | `action`, `created_at` | Referência: batch_id do lote de migração |

### Mapeamento de tipos — SQLite `transactions.type` → `investment_transactions.type`

| Valor SQLite | Valor canônico |
|:------------:|:--------------:|
| `buy` | `buy` |
| `sell` | `sell` |
| `compra` | `buy` |
| `venda` | `sell` |

### Mapeamento de classes de ativo — SQLite → canônico

| Valor SQLite | Valor canônico |
|:------------:|:--------------:|
| `acao` / `stock` / `Ação` | `stock` |
| `fii` / `FII` / `reit` | `reit` |
| `etf` / `ETF` | `etf` |
| `renda_fixa` / `fixed_income` | `fixed_income` |
| `cripto` / `crypto` | `crypto` |

---

## Seção 6 — Tabelas do App 1 (Dashboard Financeiro — PostgreSQL)

> Fonte: `SOURCE_DB_APP1`
> Status: **Sem mapeamento definido** — depende da auditoria da Fase 4.1.
> O App 1 é um projeto Next.js/NestJS com schema próprio ainda não auditado.
>
> Esta seção será preenchida após a Fase 4.1 revelar o schema real do App 1.

---

## Seção 7 — Sequência de Migração Recomendada

A ordem abaixo respeita as dependências de chave estrangeira:

```
1. profiles           ← seed manual (OWNER_USER_ID)
2. financial_institutions ← SQLite institutions + manual
3. assets             ← app4_atual (rename) + SQLite assets
4. accounts           ← app4_atual (rename) + App3 + SQLite accounts
5. categories         ← app4_atual (rename) + App3
6. portfolios         ← manual (criar carteira padrão para dados do App2)
7. transactions       ← app4_atual (rename) + App3
8. budgets            ← app4_atual (rename) + App3
9. financial_goals    ← app4_atual (rename) + App3
10. investment_transactions ← app4_atual (rename) + SQLite transactions
11. dividends         ← app4_atual (rename) + SQLite incomes
12. asset_quotes      ← app4_atual (rename)
13. portfolio_positions ← SQLite xp_positions / position_snapshots (calculada)
14. user_settings     ← seed automático (1 linha padrão)
15. benchmarks        ← seed manual (5–10 registros)
16. benchmark_quotes  ← API (após benchmarks serem criados)
17. debts             ← manual
18. cards             ← manual
19. alerts            ← manual
20. import_batches    ← automático (ETL)
21. import_logs       ← automático (ETL)
22. migration_source_map ← automático (ETL)
```

---

## Checklist de Validação Pós-Migração (por tabela)

> Usar após a Fase 4.7. Preencher com os valores reais.

| Tabela | Registros (origem) | Registros (destino) | Diferença | OK? |
|--------|:-----------------:|:-------------------:|:---------:|:---:|
| `profiles` | 1 | 1 | 0 | ☐ |
| `financial_institutions` | — | — | — | ☐ |
| `accounts` | — | — | — | ☐ |
| `categories` | — | — | — | ☐ |
| `transactions` | — | — | — | ☐ |
| `budgets` | — | — | — | ☐ |
| `financial_goals` | — | — | — | ☐ |
| `assets` | — | — | — | ☐ |
| `investment_transactions` | — | — | — | ☐ |
| `dividends` | — | — | — | ☐ |
| `asset_quotes` | — | — | — | ☐ |

> **Regra:** diferença ≠ 0 requer justificativa registrada em `supabase_unificado/validation/`.

---

## Histórico

| Data | Versão | Mudança |
|------|--------|---------|
| 2026-05-13 | v1.0 | Documento criado — mapeamento completo das 3 fontes + renomeação das 10 tabelas |
