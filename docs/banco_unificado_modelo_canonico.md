# Banco Unificado — Modelo Canônico

> Documento: `docs/banco_unificado_modelo_canonico.md`
> Criado em: 2026-05-13
> Fase: 4.2 — Modelo Canônico do Banco Unificado
> Status: **Rascunho para aprovação humana — nenhum SQL executado**
> Pré-requisito para: Fase 4.3 (geração dos scripts DDL)
> Referências: `etl/schema_setup.py` · `ProjetoIA/05_Banco_de_Dados/modelagem_inicial.md`
>              `docs/auditoria_dados_investimentos.md` · `docs/fase_4_supabase_auditoria.md`

---

## Contexto e Decisão de Nomenclatura

O schema atual (`etl/schema_setup.py`) define 10 tabelas com nomes em **português**.
O modelo canônico adota nomes em **inglês** para todas as 22 tabelas, por três razões:

1. **Consistência**: os 12 novos domínios não têm equivalentes claros em português técnico.
2. **Expansibilidade**: interoperabilidade futura com APIs REST e outros clientes.
3. **Convenção SQL**: nomes em inglês são o padrão em projetos PostgreSQL / Supabase.

A transição de nomes é documentada em `docs/banco_unificado_mapa_origem_destino.md`.
O script DDL da Fase 4.3 usará os nomes deste documento.

---

## Resumo — 22 Tabelas em 8 Domínios

| # | Tabela | Domínio | Origem | RLS |
|---|--------|---------|--------|:---:|
| 1 | `profiles` | Identidade | `usuarios` (atual) | ✅ |
| 2 | `financial_institutions` | Instituições | **Nova** | ❌ |
| 3 | `accounts` | Contas | `contas` (atual) | ✅ |
| 4 | `cards` | Cartões | **Nova** | ✅ |
| 5 | `categories` | Finanças Pessoais | `categorias` (atual) | ✅ |
| 6 | `transactions` | Finanças Pessoais | `transacoes` (atual) | ✅ |
| 7 | `budgets` | Finanças Pessoais | `orcamentos` (atual) | ✅ |
| 8 | `financial_goals` | Finanças Pessoais | `metas` (atual) | ✅ |
| 9 | `debts` | Finanças Pessoais | **Nova** | ✅ |
| 10 | `assets` | Investimentos | `ativos` (atual) | ❌ |
| 11 | `portfolios` | Investimentos | **Nova** | ✅ |
| 12 | `portfolio_positions` | Investimentos | **Nova** | ✅ |
| 13 | `investment_transactions` | Investimentos | `operacoes` (atual) | ✅ |
| 14 | `dividends` | Investimentos | `proventos` (atual) | ✅ |
| 15 | `asset_quotes` | Dados de Mercado | `cotacoes` (atual) | ❌ |
| 16 | `benchmarks` | Dados de Mercado | **Nova** | ❌ |
| 17 | `benchmark_quotes` | Dados de Mercado | **Nova** | ❌ |
| 18 | `alerts` | Preferências | **Nova** | ✅ |
| 19 | `user_settings` | Preferências | **Nova** | ✅ |
| 20 | `import_batches` | Controle | **Nova** | ✅ |
| 21 | `import_logs` | Controle | **Nova** | ✅ |
| 22 | `migration_source_map` | Controle | **Nova** | ❌ |

**RLS** = Row Level Security obrigatória (dados pessoais do usuário).
**❌ RLS** = dados de mercado ou controle interno — sem dado pessoal identificável.

---

## Ordem de Criação (dependências FK)

```
profiles
  └─ financial_institutions          (sem dependência)
  └─ accounts    → profiles, financial_institutions
  └─ cards       → profiles, accounts, financial_institutions
  └─ categories  → profiles (auto-referência: parent_id)
  └─ transactions → profiles, accounts, cards, categories
  └─ budgets     → profiles, categories
  └─ financial_goals → profiles
  └─ debts       → profiles

assets                               (sem dependência de usuário)
  └─ asset_quotes → assets
  └─ portfolios  → profiles
  └─ portfolio_positions → profiles, portfolios, assets
  └─ investment_transactions → profiles, assets, portfolios
  └─ dividends   → profiles, assets

benchmarks                           (sem dependência)
  └─ benchmark_quotes → benchmarks

alerts         → profiles
user_settings  → profiles

import_batches → profiles
  └─ import_logs → import_batches
migration_source_map                 (sem FK estrutural — referências textuais)
```

---

## Domínio 1: Identidade

### `profiles`
> Equivalente ao atual `usuarios`. Perfil do proprietário da conta.

| Coluna | Tipo | Nullable | Padrão | Descrição |
|--------|------|:--------:|--------|-----------|
| `id` | UUID | ❌ | `gen_random_uuid()` | Chave primária |
| `name` | VARCHAR(150) | ❌ | — | Nome completo |
| `email` | VARCHAR(255) | ❌ | — | E-mail único |
| `password_hash` | TEXT | ❌ | — | Hash SHA-256 ou bcrypt |
| `created_at` | TIMESTAMPTZ | ❌ | `NOW()` | Data de criação |
| `active` | BOOLEAN | ❌ | `TRUE` | Soft-delete |

**Unique:** `email`
**RLS:** SELECT WHERE id = auth.uid()
**Nota:** App 4 é single-user; `OWNER_USER_ID` em `.env` aponta para este registro.

---

## Domínio 2: Instituições Financeiras

### `financial_institutions`
> Nova tabela. Centraliza bancos, corretoras e fintechs referenciados por contas e cartões.

| Coluna | Tipo | Nullable | Padrão | Descrição |
|--------|------|:--------:|--------|-----------|
| `id` | UUID | ❌ | `gen_random_uuid()` | Chave primária |
| `name` | VARCHAR(200) | ❌ | — | Nome da instituição |
| `type` | VARCHAR(50) | ❌ | — | `bank` · `broker` · `fintech` · `insurance` |
| `cnpj` | VARCHAR(18) | ✅ | NULL | CNPJ formatado |
| `bank_code` | CHAR(3) | ✅ | NULL | Código COMPE (ex: `341` = Itaú) |
| `website` | VARCHAR(255) | ✅ | NULL | URL do portal |
| `active` | BOOLEAN | ❌ | `TRUE` | — |

**Unique:** `cnpj` (quando preenchido)
**RLS:** não requerida (dados públicos de referência)
**Origem de dados:** migração do SQLite `institutions` (App 2); população manual (Apps 1/3)

---

## Domínio 3: Contas e Cartões

### `accounts`
> Equivalente ao atual `contas`. Contas bancárias, poupança, carteiras digitais.

| Coluna | Tipo | Nullable | Padrão | Descrição |
|--------|------|:--------:|--------|-----------|
| `id` | UUID | ❌ | `gen_random_uuid()` | Chave primária |
| `user_id` | UUID | ❌ | — | FK → `profiles.id` |
| `financial_institution_id` | UUID | ✅ | NULL | FK → `financial_institutions.id` |
| `name` | VARCHAR(100) | ❌ | — | Nome da conta |
| `type` | VARCHAR(50) | ❌ | — | `checking` · `savings` · `digital_wallet` · `investment` |
| `initial_balance` | NUMERIC(15,2) | ❌ | `0` | Saldo inicial |
| `currency` | CHAR(3) | ❌ | `BRL` | Moeda ISO 4217 |
| `active` | BOOLEAN | ❌ | `TRUE` | Soft-delete |
| `created_at` | TIMESTAMPTZ | ❌ | `NOW()` | — |

**FK:** `user_id → profiles`, `financial_institution_id → financial_institutions`
**RLS:** SELECT/INSERT/UPDATE WHERE user_id = auth.uid()
**Origem de dados:** `contas` (atual) + SQLite `accounts` (App 2)

---

### `cards`
> Nova tabela. Cartões de crédito com controle de limite, vencimento e fechamento.

| Coluna | Tipo | Nullable | Padrão | Descrição |
|--------|------|:--------:|--------|-----------|
| `id` | UUID | ❌ | `gen_random_uuid()` | Chave primária |
| `user_id` | UUID | ❌ | — | FK → `profiles.id` |
| `account_id` | UUID | ✅ | NULL | FK → `accounts.id` (conta vinculada) |
| `financial_institution_id` | UUID | ✅ | NULL | FK → `financial_institutions.id` |
| `name` | VARCHAR(100) | ❌ | — | Nome do cartão (ex: "Nubank Roxinho") |
| `brand` | VARCHAR(50) | ✅ | NULL | `visa` · `mastercard` · `elo` · `amex` |
| `credit_limit` | NUMERIC(15,2) | ✅ | NULL | Limite de crédito |
| `due_day` | SMALLINT | ✅ | NULL | Dia de vencimento da fatura (1–31) |
| `close_day` | SMALLINT | ✅ | NULL | Dia de fechamento da fatura (1–31) |
| `active` | BOOLEAN | ❌ | `TRUE` | — |
| `created_at` | TIMESTAMPTZ | ❌ | `NOW()` | — |

**FK:** `user_id → profiles`, `account_id → accounts`, `financial_institution_id → financial_institutions`
**RLS:** SELECT WHERE user_id = auth.uid()
**Origem de dados:** população manual (não há fonte de migração)

---

## Domínio 4: Finanças Pessoais

### `categories`
> Equivalente ao atual `categorias`. Categorias de transação, hierárquicas.

| Coluna | Tipo | Nullable | Padrão | Descrição |
|--------|------|:--------:|--------|-----------|
| `id` | UUID | ❌ | `gen_random_uuid()` | Chave primária |
| `user_id` | UUID | ✅ | NULL | FK → `profiles.id` (NULL = categoria do sistema) |
| `name` | VARCHAR(100) | ❌ | — | Nome da categoria |
| `type` | VARCHAR(20) | ❌ | — | `income` · `expense` · `transfer` |
| `icon` | VARCHAR(50) | ✅ | NULL | Emoji ou nome de ícone |
| `color` | CHAR(7) | ✅ | NULL | Hex color (`#RRGGBB`) |
| `parent_id` | UUID | ✅ | NULL | FK → `categories.id` (subcategorias) |

**FK:** `user_id → profiles`, `parent_id → categories`
**RLS:** SELECT WHERE user_id = auth.uid() OR user_id IS NULL
**Origem de dados:** `categorias` (atual) + Controle Financeiro categorias (App 3)

---

### `transactions`
> Equivalente ao atual `transacoes`. Receitas, despesas e transferências.

| Coluna | Tipo | Nullable | Padrão | Descrição |
|--------|------|:--------:|--------|-----------|
| `id` | UUID | ❌ | `gen_random_uuid()` | Chave primária |
| `user_id` | UUID | ❌ | — | FK → `profiles.id` |
| `account_id` | UUID | ❌ | — | FK → `accounts.id` |
| `card_id` | UUID | ✅ | NULL | FK → `cards.id` (se lançamento de cartão) |
| `category_id` | UUID | ✅ | NULL | FK → `categories.id` |
| `description` | VARCHAR(255) | ❌ | — | Descrição da transação |
| `amount` | NUMERIC(15,2) | ❌ | — | Positivo = receita; negativo = despesa |
| `due_date` | DATE | ❌ | — | Data de competência |
| `payment_date` | DATE | ✅ | NULL | Data de pagamento efetivo |
| `type` | VARCHAR(20) | ❌ | — | `income` · `expense` · `transfer` |
| `status` | VARCHAR(20) | ❌ | `settled` | `pending` · `settled` · `cancelled` |
| `recurring` | BOOLEAN | ❌ | `FALSE` | Transação recorrente |
| `installment_current` | SMALLINT | ✅ | NULL | Número da parcela atual |
| `installment_total` | SMALLINT | ✅ | NULL | Total de parcelas |
| `installment_group` | UUID | ✅ | NULL | Agrupa parcelas do mesmo parcelamento |
| `source` | VARCHAR(50) | ❌ | `manual` | `manual` · `import` · `csv` · `open_banking` |
| `created_at` | TIMESTAMPTZ | ❌ | `NOW()` | — |

**Indexes:**
- `(user_id, due_date DESC)` — queries de período
- `(category_id)` — queries de categoria

**FK:** `user_id → profiles`, `account_id → accounts`, `card_id → cards`, `category_id → categories`
**RLS:** SELECT WHERE user_id = auth.uid()
**Origem de dados:** `transacoes` (atual) + Controle Financeiro (App 3)

---

### `budgets`
> Equivalente ao atual `orcamentos`. Orçamentos mensais por categoria.

| Coluna | Tipo | Nullable | Padrão | Descrição |
|--------|------|:--------:|--------|-----------|
| `id` | UUID | ❌ | `gen_random_uuid()` | Chave primária |
| `user_id` | UUID | ❌ | — | FK → `profiles.id` |
| `category_id` | UUID | ❌ | — | FK → `categories.id` |
| `month_year` | DATE | ❌ | — | Primeiro dia do mês (ex: `2026-05-01`) |
| `amount_limit` | NUMERIC(15,2) | ❌ | — | Limite orçado |

**Unique:** `(user_id, category_id, month_year)`
**FK:** `user_id → profiles`, `category_id → categories`
**RLS:** SELECT WHERE user_id = auth.uid()
**Origem de dados:** `orcamentos` (atual) + Controle Financeiro orçamentos (App 3)

---

### `financial_goals`
> Equivalente ao atual `metas`. Metas financeiras com acompanhamento de progresso.

| Coluna | Tipo | Nullable | Padrão | Descrição |
|--------|------|:--------:|--------|-----------|
| `id` | UUID | ❌ | `gen_random_uuid()` | Chave primária |
| `user_id` | UUID | ❌ | — | FK → `profiles.id` |
| `name` | VARCHAR(150) | ❌ | — | Nome da meta |
| `type` | VARCHAR(50) | ✅ | NULL | `emergency_fund` · `travel` · `purchase` · `debt_payment` |
| `target_amount` | NUMERIC(15,2) | ❌ | — | Valor alvo |
| `current_amount` | NUMERIC(15,2) | ❌ | `0` | Valor acumulado até o momento |
| `deadline` | DATE | ✅ | NULL | Prazo desejado |
| `active` | BOOLEAN | ❌ | `TRUE` | — |
| `created_at` | TIMESTAMPTZ | ❌ | `NOW()` | — |

**FK:** `user_id → profiles`
**RLS:** SELECT WHERE user_id = auth.uid()
**Origem de dados:** `metas` (atual) + Controle Financeiro metas (App 3, se existirem)

---

### `debts`
> Nova tabela. Dívidas, financiamentos e parcelamentos de longo prazo.

| Coluna | Tipo | Nullable | Padrão | Descrição |
|--------|------|:--------:|--------|-----------|
| `id` | UUID | ❌ | `gen_random_uuid()` | Chave primária |
| `user_id` | UUID | ❌ | — | FK → `profiles.id` |
| `name` | VARCHAR(200) | ❌ | — | Nome / descrição da dívida |
| `type` | VARCHAR(50) | ❌ | — | `loan` · `financing` · `credit_card_revolving` · `installment` |
| `original_amount` | NUMERIC(15,2) | ✅ | NULL | Valor original contratado |
| `outstanding_balance` | NUMERIC(15,2) | ❌ | — | Saldo devedor atual |
| `interest_rate` | NUMERIC(8,4) | ✅ | NULL | Taxa de juros (% a.m.) |
| `start_date` | DATE | ✅ | NULL | Data de início |
| `end_date` | DATE | ✅ | NULL | Data de quitação prevista |
| `total_installments` | SMALLINT | ✅ | NULL | Total de parcelas |
| `paid_installments` | SMALLINT | ✅ | `0` | Parcelas já pagas |
| `active` | BOOLEAN | ❌ | `TRUE` | — |
| `created_at` | TIMESTAMPTZ | ❌ | `NOW()` | — |

**FK:** `user_id → profiles`
**RLS:** SELECT WHERE user_id = auth.uid()
**Origem de dados:** população manual (não há fonte de migração)

---

## Domínio 5: Investimentos

### `assets`
> Equivalente ao atual `ativos`. Cadastro de ativos negociáveis (ações, FIIs, ETFs, renda fixa, cripto).

| Coluna | Tipo | Nullable | Padrão | Descrição |
|--------|------|:--------:|--------|-----------|
| `id` | UUID | ❌ | `gen_random_uuid()` | Chave primária |
| `ticker` | VARCHAR(20) | ❌ | — | Código de negociação único |
| `name` | VARCHAR(200) | ❌ | — | Nome do ativo |
| `class` | VARCHAR(50) | ❌ | — | `stock` · `reit` · `etf` · `fixed_income` · `crypto` |
| `sector` | VARCHAR(100) | ✅ | NULL | Setor (ex: `financials`, `real_estate`) |
| `currency` | CHAR(3) | ❌ | `BRL` | Moeda de negociação |
| `exchange` | VARCHAR(20) | ✅ | NULL | `B3` · `NASDAQ` · `NYSE` · `BINANCE` |

**Unique:** `ticker`
**RLS:** não requerida (dados de mercado)
**Origem de dados:** `ativos` (atual) + SQLite `assets` (App 2)

---

### `portfolios`
> Nova tabela. Agrupa operações de investimento em carteiras temáticas.

| Coluna | Tipo | Nullable | Padrão | Descrição |
|--------|------|:--------:|--------|-----------|
| `id` | UUID | ❌ | `gen_random_uuid()` | Chave primária |
| `user_id` | UUID | ❌ | — | FK → `profiles.id` |
| `name` | VARCHAR(150) | ❌ | — | Nome da carteira |
| `description` | TEXT | ✅ | NULL | Descrição livre |
| `type` | VARCHAR(50) | ✅ | NULL | `personal` · `pension` · `speculative` |
| `active` | BOOLEAN | ❌ | `TRUE` | — |
| `created_at` | TIMESTAMPTZ | ❌ | `NOW()` | — |

**FK:** `user_id → profiles`
**RLS:** SELECT WHERE user_id = auth.uid()
**Origem de dados:** população manual (App 2 não possui carteiras nomeadas)

---

### `portfolio_positions`
> Nova tabela. Posição consolidada atual por ativo em cada carteira (calculada a partir de `investment_transactions`).

| Coluna | Tipo | Nullable | Padrão | Descrição |
|--------|------|:--------:|--------|-----------|
| `id` | UUID | ❌ | `gen_random_uuid()` | Chave primária |
| `user_id` | UUID | ❌ | — | FK → `profiles.id` |
| `portfolio_id` | UUID | ❌ | — | FK → `portfolios.id` |
| `asset_id` | UUID | ❌ | — | FK → `assets.id` |
| `quantity` | NUMERIC(18,8) | ❌ | — | Quantidade em carteira |
| `average_price` | NUMERIC(15,6) | ❌ | — | Preço médio de aquisição |
| `total_invested` | NUMERIC(15,2) | ❌ | — | Valor total investido (qty × avg_price) |
| `updated_at` | TIMESTAMPTZ | ❌ | `NOW()` | Última atualização da posição |

**Unique:** `(portfolio_id, asset_id)`
**FK:** `user_id → profiles`, `portfolio_id → portfolios`, `asset_id → assets`
**RLS:** SELECT WHERE user_id = auth.uid()
**Origem de dados:** calculada a partir de `investment_transactions`; SQLite `xp_positions` e `position_snapshots` (App 2)

---

### `investment_transactions`
> Equivalente ao atual `operacoes`. Operações de compra e venda de ativos.

| Coluna | Tipo | Nullable | Padrão | Descrição |
|--------|------|:--------:|--------|-----------|
| `id` | UUID | ❌ | `gen_random_uuid()` | Chave primária |
| `user_id` | UUID | ❌ | — | FK → `profiles.id` |
| `asset_id` | UUID | ❌ | — | FK → `assets.id` |
| `portfolio_id` | UUID | ✅ | NULL | FK → `portfolios.id` |
| `type` | VARCHAR(10) | ❌ | — | `buy` · `sell` |
| `quantity` | NUMERIC(18,8) | ❌ | — | Quantidade negociada |
| `unit_price` | NUMERIC(15,6) | ❌ | — | Preço unitário |
| `fees` | NUMERIC(15,2) | ❌ | `0` | Taxas e corretagem |
| `transaction_date` | DATE | ❌ | — | Data da operação |
| `broker` | VARCHAR(100) | ✅ | NULL | Nome da corretora |
| `created_at` | TIMESTAMPTZ | ❌ | `NOW()` | — |

**Indexes:** `(user_id, asset_id)`
**FK:** `user_id → profiles`, `asset_id → assets`, `portfolio_id → portfolios`
**RLS:** SELECT WHERE user_id = auth.uid()
**Origem de dados:** `operacoes` (atual) + SQLite `transactions` (App 2)

---

### `dividends`
> Equivalente ao atual `proventos`. Dividendos, JCP, rendimentos de FII, amortizações.

| Coluna | Tipo | Nullable | Padrão | Descrição |
|--------|------|:--------:|--------|-----------|
| `id` | UUID | ❌ | `gen_random_uuid()` | Chave primária |
| `user_id` | UUID | ❌ | — | FK → `profiles.id` |
| `asset_id` | UUID | ❌ | — | FK → `assets.id` |
| `type` | VARCHAR(30) | ❌ | — | `dividend` · `jcp` · `reit_income` · `amortization` |
| `amount_per_unit` | NUMERIC(15,6) | ❌ | — | Valor por cota/ação |
| `quantity` | NUMERIC(18,8) | ❌ | — | Quantidade em custódia na data-com |
| `total_amount` | NUMERIC(15,2) | ❌ | — | Valor total recebido |
| `ex_date` | DATE | ✅ | NULL | Data-com |
| `payment_date` | DATE | ❌ | — | Data de pagamento |

**FK:** `user_id → profiles`, `asset_id → assets`
**RLS:** SELECT WHERE user_id = auth.uid()
**Origem de dados:** `proventos` (atual) + SQLite `incomes` (App 2)

---

## Domínio 6: Dados de Mercado

### `asset_quotes`
> Equivalente ao atual `cotacoes`. Série histórica de preços de ativos.

| Coluna | Tipo | Nullable | Padrão | Descrição |
|--------|------|:--------:|--------|-----------|
| `asset_id` | UUID | ❌ | — | FK → `assets.id` |
| `timestamp` | TIMESTAMPTZ | ❌ | — | Data e hora (UTC) |
| `open` | NUMERIC(15,6) | ✅ | NULL | Preço de abertura |
| `high` | NUMERIC(15,6) | ✅ | NULL | Máxima |
| `low` | NUMERIC(15,6) | ✅ | NULL | Mínima |
| `close` | NUMERIC(15,6) | ❌ | — | Preço de fechamento |
| `volume` | NUMERIC(20,2) | ✅ | NULL | Volume negociado |

**Primary Key:** `(asset_id, timestamp)`
**Index:** `(asset_id, timestamp DESC)`
**FK:** `asset_id → assets`
**RLS:** não requerida (dados de mercado)
**Nota:** TimescaleDB não disponível no Supabase free; usar particionamento manual por ano se necessário.

---

### `benchmarks`
> Nova tabela. Índices e taxas de referência (IBOVESPA, CDI, IPCA, SELIC, IFIX).

| Coluna | Tipo | Nullable | Padrão | Descrição |
|--------|------|:--------:|--------|-----------|
| `id` | UUID | ❌ | `gen_random_uuid()` | Chave primária |
| `code` | VARCHAR(20) | ❌ | — | Código único (ex: `IBOVESPA`, `CDI`, `IPCA`) |
| `name` | VARCHAR(100) | ❌ | — | Nome completo |
| `type` | VARCHAR(50) | ✅ | NULL | `index` · `rate` |
| `frequency` | VARCHAR(20) | ✅ | NULL | `daily` · `monthly` |
| `description` | TEXT | ✅ | NULL | — |

**Unique:** `code`
**RLS:** não requerida
**Origem de dados:** população manual (5–10 registros de referência)

---

### `benchmark_quotes`
> Nova tabela. Série histórica dos benchmarks (variação diária ou mensal).

| Coluna | Tipo | Nullable | Padrão | Descrição |
|--------|------|:--------:|--------|-----------|
| `benchmark_id` | UUID | ❌ | — | FK → `benchmarks.id` |
| `date` | DATE | ❌ | — | Data de referência |
| `value` | NUMERIC(15,8) | ❌ | — | Valor do índice ou taxa |
| `daily_change_pct` | NUMERIC(8,6) | ✅ | NULL | Variação diária (%) |

**Primary Key:** `(benchmark_id, date)`
**FK:** `benchmark_id → benchmarks`
**RLS:** não requerida

---

## Domínio 7: Preferências e Alertas

### `alerts`
> Nova tabela. Alertas configuráveis por preço de ativo, orçamento ou meta.

| Coluna | Tipo | Nullable | Padrão | Descrição |
|--------|------|:--------:|--------|-----------|
| `id` | UUID | ❌ | `gen_random_uuid()` | Chave primária |
| `user_id` | UUID | ❌ | — | FK → `profiles.id` |
| `type` | VARCHAR(50) | ❌ | — | `price_target` · `budget_exceeded` · `goal_reached` · `debt_due` |
| `reference_id` | UUID | ✅ | NULL | UUID do objeto alvo (ativo, orçamento, meta, dívida) |
| `reference_type` | VARCHAR(50) | ✅ | NULL | `asset` · `budget` · `goal` · `debt` |
| `condition` | VARCHAR(50) | ✅ | NULL | `above` · `below` · `equals` · `percentage` |
| `trigger_value` | NUMERIC(15,6) | ✅ | NULL | Valor que dispara o alerta |
| `message` | TEXT | ✅ | NULL | Mensagem personalizada |
| `active` | BOOLEAN | ❌ | `TRUE` | — |
| `triggered` | BOOLEAN | ❌ | `FALSE` | Já disparou? |
| `triggered_at` | TIMESTAMPTZ | ✅ | NULL | Quando disparou |
| `created_at` | TIMESTAMPTZ | ❌ | `NOW()` | — |

**FK:** `user_id → profiles`
**RLS:** SELECT WHERE user_id = auth.uid()
**Origem de dados:** população manual

---

### `user_settings`
> Nova tabela. Preferências do usuário (uma linha por usuário).

| Coluna | Tipo | Nullable | Padrão | Descrição |
|--------|------|:--------:|--------|-----------|
| `user_id` | UUID | ❌ | — | PK + FK → `profiles.id` |
| `default_currency` | CHAR(3) | ❌ | `BRL` | Moeda padrão da interface |
| `language` | VARCHAR(10) | ❌ | `pt-BR` | Idioma da interface |
| `theme` | VARCHAR(20) | ❌ | `dark` | `dark` · `light` |
| `notifications_active` | BOOLEAN | ❌ | `TRUE` | Notificações habilitadas |
| `month_start_day` | SMALLINT | ❌ | `1` | Dia de início do mês financeiro |
| `extra_settings` | JSONB | ✅ | NULL | Configurações futuras (chave-valor JSON) |
| `updated_at` | TIMESTAMPTZ | ❌ | `NOW()` | — |

**Primary Key:** `user_id` (relação 1:1 com `profiles`)
**FK:** `user_id → profiles`
**RLS:** SELECT WHERE user_id = auth.uid()

---

## Domínio 8: Controle de Importação e Migração

### `import_batches`
> Nova tabela. Rastreia cada lote de importação (CSV, banco de origem, App 1/2/3).

| Coluna | Tipo | Nullable | Padrão | Descrição |
|--------|------|:--------:|--------|-----------|
| `id` | UUID | ❌ | `gen_random_uuid()` | Chave primária |
| `user_id` | UUID | ❌ | — | FK → `profiles.id` |
| `source` | VARCHAR(50) | ❌ | — | `app1_dashboard` · `app2_investments` · `app3_controle` · `csv` · `manual` |
| `filename` | VARCHAR(500) | ✅ | NULL | Nome do arquivo (CSV/Excel) |
| `status` | VARCHAR(20) | ❌ | `pending` | `pending` · `processing` · `completed` · `error` |
| `total_records` | INTEGER | ❌ | `0` | Total de registros no lote |
| `imported_records` | INTEGER | ❌ | `0` | Registros inseridos com sucesso |
| `error_records` | INTEGER | ❌ | `0` | Registros com erro |
| `dry_run` | BOOLEAN | ❌ | `TRUE` | Simulação sem gravação |
| `started_at` | TIMESTAMPTZ | ❌ | `NOW()` | Início do processamento |
| `completed_at` | TIMESTAMPTZ | ✅ | NULL | Fim do processamento |
| `notes` | TEXT | ✅ | NULL | Observações livres |

**FK:** `user_id → profiles`
**RLS:** SELECT WHERE user_id = auth.uid()

---

### `import_logs`
> Nova tabela. Log por registro dentro de um lote de importação.

| Coluna | Tipo | Nullable | Padrão | Descrição |
|--------|------|:--------:|--------|-----------|
| `id` | UUID | ❌ | `gen_random_uuid()` | Chave primária |
| `batch_id` | UUID | ❌ | — | FK → `import_batches.id` |
| `target_table` | VARCHAR(100) | ❌ | — | Tabela de destino |
| `source_id` | TEXT | ✅ | NULL | ID original na fonte (qualquer tipo) |
| `destination_id` | UUID | ✅ | NULL | UUID gerado no destino |
| `action` | VARCHAR(20) | ❌ | — | `inserted` · `skipped` · `error` |
| `message` | TEXT | ✅ | NULL | Detalhe do erro ou razão do skip |
| `created_at` | TIMESTAMPTZ | ❌ | `NOW()` | — |

**Indexes:**
- `(batch_id)` — listar logs de um lote
- `(target_table, source_id)` — rastrear origem de um registro

**FK:** `batch_id → import_batches`
**RLS:** não requerida (dados de controle interno)

---

### `migration_source_map`
> Nova tabela. Mapeia IDs de origem (apps antigos) para IDs de destino (banco unificado).
> Essencial para idempotência — evita duplicação em reexecuções da migração.

| Coluna | Tipo | Nullable | Padrão | Descrição |
|--------|------|:--------:|--------|-----------|
| `id` | UUID | ❌ | `gen_random_uuid()` | Chave primária |
| `target_table` | VARCHAR(100) | ❌ | — | Tabela destino (ex: `transactions`) |
| `target_id` | UUID | ❌ | — | UUID no banco unificado |
| `source` | VARCHAR(50) | ❌ | — | `app1` · `app2` · `app3` |
| `source_table` | VARCHAR(100) | ❌ | — | Tabela de origem (ex: `transacoes`) |
| `source_id` | TEXT | ❌ | — | ID original (pode ser UUID, INTEGER, etc.) |
| `migrated_at` | TIMESTAMPTZ | ❌ | `NOW()` | — |

**Unique:** `(target_table, target_id, source)` — um registro destino mapeado uma vez por fonte
**Unique:** `(source, source_table, source_id)` — um registro origem mapeado apenas uma vez
**Indexes:**
- `(target_table, target_id)` — dado destino → qual era a origem
- `(source, source_table, source_id)` — dado origem → onde foi parar

**RLS:** não requerida (dados de controle interno)

---

## Diagrama de Relacionamentos (texto)

```
profiles (1)
  ├─── (N) accounts ─── (1) financial_institutions
  ├─── (N) cards ────── (1) accounts
  │                 └── (1) financial_institutions
  ├─── (N) categories (auto-ref: parent_id)
  ├─── (N) transactions ── (1) accounts
  │                    ├── (1) cards
  │                    └── (1) categories
  ├─── (N) budgets ──── (1) categories
  ├─── (N) financial_goals
  ├─── (N) debts
  ├─── (N) portfolios
  │         └── (N) portfolio_positions ─── (1) assets
  ├─── (N) investment_transactions ──── (1) assets
  │                              └── (1) portfolios
  ├─── (N) dividends ──────────── (1) assets
  ├─── (N) alerts
  ├─── (1) user_settings
  └─── (N) import_batches
              └── (N) import_logs

assets (N)
  ├─── (N) asset_quotes
  ├─── (N) investment_transactions
  ├─── (N) dividends
  └─── (N) portfolio_positions

benchmarks (N)
  └─── (N) benchmark_quotes

migration_source_map (sem FK estrutural — referências textuais)
```

---

## Notas de Implementação para a Fase 4.3

1. **`etl/schema_setup.py`** precisará ser atualizado com os 22 DDLs e os novos nomes de tabela.
2. **`_TABELAS_VALIDAS`** em `etl/importacao.py` precisará incluir os novos nomes.
3. **`verificar_schema()`** precisará listar as 22 tabelas, não apenas as 10 atuais.
4. A renomeação das 10 tabelas existentes (português → inglês) é feita via
   `ALTER TABLE nome_antigo RENAME TO nome_novo` — seguro, não destrutivo.
   Isto será incluído no script de migração da Fase 4.5 se a auditoria (4.1) confirmar
   que as tabelas existem com os nomes antigos.
5. O campo `financial_institution_id` em `accounts` e `cards` é NULLABLE para não
   bloquear migração de dados que não têm esta informação.
6. `portfolio_id` em `investment_transactions` é NULLABLE para compatibilidade com
   dados históricos do App 2 que não tinham conceito de carteira nomeada.

---

## Aprovação Necessária Antes da Fase 4.3

> Este documento representa o modelo proposto.
> **Nenhum SQL será gerado ou executado sem aprovação explícita do proprietário.**
>
> Para aprovar: responder "modelo canônico aprovado" ou solicitar ajustes.
> Após aprovação, a Fase 4.3 gera os scripts DDL em `supabase_unificado/schema/`.

---

## Histórico

| Data | Versão | Mudança |
|------|--------|---------|
| 2026-05-13 | v1.0 | Modelo criado — 22 tabelas em 8 domínios; aguarda aprovação |
