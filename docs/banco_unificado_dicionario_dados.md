# Banco Unificado — Dicionário de Dados

> Documento: `docs/banco_unificado_dicionario_dados.md`
> Criado em: 2026-05-13
> Fase: 4.2 — Modelo Canônico
> Status: Rascunho — acompanha `docs/banco_unificado_modelo_canonico.md`
> Propósito: descrever a semântica de negócio de cada coluna, restrições e regras de uso.

---

## Convenções

| Símbolo | Significado |
|:-------:|-------------|
| 🔑 | Chave primária |
| 🔗 | Chave estrangeira |
| ✳️ | Obrigatório (NOT NULL) |
| ○ | Opcional (nullable) |
| 🔒 | Imutável após criação |
| ⚠️ | Atenção: regra de negócio importante |

**Todos os UUIDs** são gerados por `gen_random_uuid()` no banco — nunca gerados no cliente.
**Todos os timestamps** são `TIMESTAMPTZ` (fuso horário armazenado em UTC).
**Valores monetários** usam `NUMERIC(15,2)` — nunca `FLOAT` (sem erro de ponto flutuante).

---

## `profiles`

| Coluna | Sem. | Regra de negócio |
|--------|:----:|-----------------|
| `id` | 🔑✳️🔒 | UUID gerado pelo banco. Imutável. `OWNER_USER_ID` no `.env` deve referenciar este valor. |
| `name` | ✳️ | Nome de exibição. Pode ser alterado pelo usuário. |
| `email` | ✳️🔒 | E-mail único no sistema. Usado como identificação alternativa. |
| `password_hash` | ✳️🔒 | Hash SHA-256 ou bcrypt da senha do app. **Nunca armazenar senha em texto claro.** |
| `created_at` | ✳️🔒 | Data de criação do perfil. Preenchida pelo banco; não editável. |
| `active` | ✳️ | `TRUE` = perfil ativo. `FALSE` = desativado (soft-delete). App 4 filtra `WHERE active = TRUE`. |

**Regra:** App 4 é single-user. Deve existir exatamente 1 registro com `active = TRUE`.

---

## `financial_institutions`

| Coluna | Sem. | Regra de negócio |
|--------|:----:|-----------------|
| `id` | 🔑✳️🔒 | UUID. |
| `name` | ✳️ | Nome oficial da instituição (ex: "Banco Itaú S.A.", "XP Investimentos"). |
| `type` | ✳️ | Classificação: `bank` (banco), `broker` (corretora), `fintech`, `insurance` (seguradora). |
| `cnpj` | ○ | CNPJ no formato `XX.XXX.XXX/XXXX-XX`. UNIQUE quando preenchido. |
| `bank_code` | ○ | Código COMPE de 3 dígitos (ex: `341` = Itaú, `033` = Santander, `260` = Nubank). |
| `website` | ○ | URL do portal (ex: `https://www.itau.com.br`). |
| `active` | ✳️ | `FALSE` = instituição encerrada ou removida do app. |

**Regra:** `cnpj` e `bank_code` são opcionais para permitir cadastro simplificado.

---

## `accounts`

| Coluna | Sem. | Regra de negócio |
|--------|:----:|-----------------|
| `id` | 🔑✳️🔒 | UUID. |
| `user_id` | 🔗✳️🔒 | Referência ao proprietário. Toda query filtra `WHERE user_id = :owner_id`. |
| `financial_institution_id` | 🔗○ | Banco ou fintech. NULL = conta sem instituição cadastrada (ex: dinheiro em espécie). |
| `name` | ✳️ | Nome da conta (ex: "Conta Corrente Nubank", "Carteira de Emergência"). |
| `type` | ✳️ | `checking` (corrente), `savings` (poupança), `digital_wallet` (carteira digital), `investment` (conta de investimento). |
| `initial_balance` | ✳️ | Saldo no momento de cadastro. Saldo atual é calculado: `initial_balance + SUM(transactions.amount)`. |
| `currency` | ✳️ | Moeda ISO 4217 (ex: `BRL`, `USD`). Default `BRL`. |
| `active` | ✳️ | `FALSE` = conta encerrada. Transações existentes são preservadas. |
| `created_at` | ✳️🔒 | Data de cadastro. |

⚠️ **Saldo atual** não é armazenado — é sempre calculado dinamicamente para evitar inconsistência.

---

## `cards`

| Coluna | Sem. | Regra de negócio |
|--------|:----:|-----------------|
| `id` | 🔑✳️🔒 | UUID. |
| `user_id` | 🔗✳️🔒 | Proprietário. |
| `account_id` | 🔗○ | Conta vinculada (para débito automático da fatura). NULL = sem conta vinculada. |
| `financial_institution_id` | 🔗○ | Emissor do cartão. |
| `name` | ✳️ | Nome de identificação (ex: "Nubank Roxinho", "Itaú Platinum"). |
| `brand` | ○ | Bandeira: `visa`, `mastercard`, `elo`, `amex`, `hipercard`. |
| `credit_limit` | ○ | Limite de crédito total. NULL = não informado. |
| `due_day` | ○ | Dia do mês em que a fatura vence (1–31). |
| `close_day` | ○ | Dia do mês em que a fatura fecha (1–31). |
| `active` | ✳️ | `FALSE` = cartão cancelado. Transações existentes são preservadas. |

⚠️ **Transações de cartão** são lançadas em `transactions` com `card_id` preenchido.
O vínculo com `account_id` é apenas para identificar qual conta paga a fatura.

---

## `categories`

| Coluna | Sem. | Regra de negócio |
|--------|:----:|-----------------|
| `id` | 🔑✳️🔒 | UUID. |
| `user_id` | 🔗○ | NULL = categoria do sistema (pré-definida). UUID = categoria criada pelo usuário. |
| `name` | ✳️ | Nome (ex: "Alimentação", "Salário", "Transporte"). |
| `type` | ✳️ | `income` (receita), `expense` (despesa), `transfer` (transferência entre contas). |
| `icon` | ○ | Emoji ou nome de ícone (ex: `🍔`, `home`, `car`). |
| `color` | ○ | Cor em hex (ex: `#FF5733`). Usada na interface. |
| `parent_id` | 🔗○ | ID da categoria pai (para subcategorias). NULL = categoria raiz. |

⚠️ **Subcategorias** não podem ter mais de 2 níveis de profundidade (pai → filho).
Consultas de totais por categoria devem incluir subcategorias recursivamente.

---

## `transactions`

| Coluna | Sem. | Regra de negócio |
|--------|:----:|-----------------|
| `id` | 🔑✳️🔒 | UUID. |
| `user_id` | 🔗✳️🔒 | Proprietário. |
| `account_id` | 🔗✳️ | Conta debitada/creditada. |
| `card_id` | 🔗○ | Cartão (preenchido apenas para lançamentos de fatura). |
| `category_id` | 🔗○ | Categoria. NULL = não categorizada. |
| `description` | ✳️ | Descrição legível (ex: "Supermercado Pão de Açúcar"). |
| `amount` | ✳️ | **Positivo = receita; negativo = despesa.** Transferências podem ter +/- conforme direção. |
| `due_date` | ✳️ | Data de competência (quando o evento econômico ocorreu). |
| `payment_date` | ○ | Data de pagamento efetivo. NULL = não pago (status = `pending`). |
| `type` | ✳️ | `income`, `expense`, `transfer`. |
| `status` | ✳️ | `pending` (agendado), `settled` (liquidado), `cancelled` (cancelado). |
| `recurring` | ✳️ | `TRUE` = transação recorrente (ex: aluguel mensal). |
| `installment_current` | ○ | Número da parcela atual (ex: `3`). NULL = à vista. |
| `installment_total` | ○ | Total de parcelas (ex: `12`). |
| `installment_group` | ○ | UUID compartilhado entre todas as parcelas do mesmo parcelamento. |
| `source` | ✳️ | Origem: `manual` (digitada), `import` (importada via ETL), `csv` (upload de arquivo). |
| `created_at` | ✳️🔒 | Data de criação do registro. |

⚠️ **Transferências** entre contas geram 2 registros: um negativo (conta origem) e um positivo (conta destino).
⚠️ **Parcelamentos** geram N registros com mesmo `installment_group` e `due_date` diferentes.

---

## `budgets`

| Coluna | Sem. | Regra de negócio |
|--------|:----:|-----------------|
| `id` | 🔑✳️🔒 | UUID. |
| `user_id` | 🔗✳️🔒 | Proprietário. |
| `category_id` | 🔗✳️ | Categoria orçada. |
| `month_year` | ✳️ | Primeiro dia do mês (ex: `2026-05-01`). Nunca armazenar dia diferente de 1. |
| `amount_limit` | ✳️ | Limite orçado para a categoria naquele mês. Sempre positivo. |

⚠️ **Consumo do orçamento** = `ABS(SUM(transactions.amount))` WHERE `category_id`, `due_date` no mesmo mês e `type = expense`.
Constraint UNIQUE em `(user_id, category_id, month_year)`.

---

## `financial_goals`

| Coluna | Sem. | Regra de negócio |
|--------|:----:|-----------------|
| `id` | 🔑✳️🔒 | UUID. |
| `user_id` | 🔗✳️🔒 | Proprietário. |
| `name` | ✳️ | Nome da meta (ex: "Reserva de Emergência", "Viagem para Europa"). |
| `type` | ○ | Tipo: `emergency_fund`, `travel`, `purchase`, `debt_payment`. Extensível. |
| `target_amount` | ✳️ | Valor alvo a ser atingido. Sempre positivo. |
| `current_amount` | ✳️ | Valor acumulado atual. Atualizado manualmente ou por regra de negócio. |
| `deadline` | ○ | Data desejada de conclusão. NULL = sem prazo definido. |
| `active` | ✳️ | `FALSE` = meta concluída ou abandonada. |
| `created_at` | ✳️🔒 | — |

⚠️ **Progresso** = `current_amount / target_amount * 100`. Pode ultrapassar 100%.

---

## `debts`

| Coluna | Sem. | Regra de negócio |
|--------|:----:|-----------------|
| `id` | 🔑✳️🔒 | UUID. |
| `user_id` | 🔗✳️🔒 | Proprietário. |
| `name` | ✳️ | Identificação da dívida (ex: "Financiamento Carro Honda"). |
| `type` | ✳️ | `loan` (empréstimo), `financing` (financiamento imóvel/veículo), `credit_card_revolving` (rotativo), `installment` (parcelado). |
| `original_amount` | ○ | Valor original da dívida. NULL = não informado. |
| `outstanding_balance` | ✳️ | Saldo devedor atual. Atualizado manualmente. |
| `interest_rate` | ○ | Taxa de juros mensal em percentual (ex: `2.5` = 2,5% a.m.). |
| `start_date` | ○ | Data de início da dívida ou parcelamento. |
| `end_date` | ○ | Data prevista de quitação. |
| `total_installments` | ○ | Total de parcelas. |
| `paid_installments` | ○ | Parcelas já pagas. |
| `active` | ✳️ | `FALSE` = dívida quitada. |

---

## `assets`

| Coluna | Sem. | Regra de negócio |
|--------|:----:|-----------------|
| `id` | 🔑✳️🔒 | UUID. |
| `ticker` | ✳️🔒 | Código de negociação único (ex: `PETR4`, `MXRF11`, `AAPL`, `BTC-USD`). Usado pelo yfinance. |
| `name` | ✳️ | Nome completo (ex: "Petrobras PN", "Maxi Renda FII", "Apple Inc."). |
| `class` | ✳️ | `stock` (ação), `reit` (FII), `etf`, `fixed_income` (renda fixa), `crypto`. |
| `sector` | ○ | Setor de atuação (ex: `energy`, `financials`, `real_estate`). |
| `currency` | ✳️ | Moeda de negociação (`BRL` para B3, `USD` para NYSE/NASDAQ). |
| `exchange` | ○ | Bolsa: `B3`, `NASDAQ`, `NYSE`, `BINANCE`. |

⚠️ `ticker` é a chave de integração com o yfinance. Deve seguir o formato do yfinance
(ex: `PETR4.SA` para ações B3, não `PETR4`).

---

## `portfolios`

| Coluna | Sem. | Regra de negócio |
|--------|:----:|-----------------|
| `id` | 🔑✳️🔒 | UUID. |
| `user_id` | 🔗✳️🔒 | Proprietário. |
| `name` | ✳️ | Nome da carteira (ex: "Carteira Principal", "Previdência", "Especulativa"). |
| `description` | ○ | Descrição livre da estratégia da carteira. |
| `type` | ○ | `personal` (geral), `pension` (previdência), `speculative` (curto prazo). |
| `active` | ✳️ | `FALSE` = carteira encerrada. |

**Nota:** na migração do App 2, criar uma carteira padrão chamada "Carteira App 2" para
receber todos os dados históricos sem carteira nomeada.

---

## `portfolio_positions`

| Coluna | Sem. | Regra de negócio |
|--------|:----:|-----------------|
| `id` | 🔑✳️🔒 | UUID. |
| `user_id` | 🔗✳️🔒 | Proprietário. |
| `portfolio_id` | 🔗✳️ | Carteira. |
| `asset_id` | 🔗✳️ | Ativo. |
| `quantity` | ✳️ | Quantidade atual em custódia (resultado líquido de compras e vendas). |
| `average_price` | ✳️ | Preço médio de aquisição (calculado pelo ETL ou atualizado a cada compra). |
| `total_invested` | ✳️ | `quantity × average_price`. Atualizado junto com `average_price`. |
| `updated_at` | ✳️ | Última vez que a posição foi recalculada. |

⚠️ Constraint UNIQUE em `(portfolio_id, asset_id)` — um registro por ativo por carteira.
Posições zeradas (quantity = 0) podem ser mantidas para histórico ou removidas.

---

## `investment_transactions`

| Coluna | Sem. | Regra de negócio |
|--------|:----:|-----------------|
| `id` | 🔑✳️🔒 | UUID. |
| `user_id` | 🔗✳️🔒 | Proprietário. |
| `asset_id` | 🔗✳️ | Ativo negociado. |
| `portfolio_id` | 🔗○ | Carteira (NULL = dados históricos sem carteira nomeada). |
| `type` | ✳️ | `buy` (compra) ou `sell` (venda). |
| `quantity` | ✳️ | Quantidade de cotas/ações. Sempre positivo (tipo indica direção). |
| `unit_price` | ✳️ | Preço unitário na data da operação. |
| `fees` | ✳️ | Corretagem + emolumentos + impostos. Default 0. |
| `transaction_date` | ✳️ | Data de liquidação ou data do pregão. |
| `broker` | ○ | Nome da corretora (ex: "XP Investimentos", "Clear"). |
| `created_at` | ✳️🔒 | — |

⚠️ **Custo médio** é calculado pelo código Python — não é armazenado aqui.
⚠️ **Total da operação** = `quantity × unit_price + fees` (calculado).

---

## `dividends`

| Coluna | Sem. | Regra de negócio |
|--------|:----:|-----------------|
| `id` | 🔑✳️🔒 | UUID. |
| `user_id` | 🔗✳️🔒 | Proprietário. |
| `asset_id` | 🔗✳️ | Ativo pagador. |
| `type` | ✳️ | `dividend` (dividendo ação), `jcp` (juros sobre capital próprio), `reit_income` (rendimento FII), `amortization` (amortização). |
| `amount_per_unit` | ✳️ | Valor por cota/ação declarado pelo ativo. |
| `quantity` | ✳️ | Quantidade em custódia na data-com (quantidade que gerou o provento). |
| `total_amount` | ✳️ | Valor líquido recebido (`amount_per_unit × quantity`, descontado IR quando aplicável). |
| `ex_date` | ○ | Data-com (data limite para ter direito ao provento). NULL = não informada. |
| `payment_date` | ✳️ | Data em que o valor foi creditado na conta. |

---

## `asset_quotes`

| Coluna | Sem. | Regra de negócio |
|--------|:----:|-----------------|
| `asset_id` | 🔗✳️ | Ativo (parte da PK). |
| `timestamp` | ✳️ | Data e hora em UTC (parte da PK). Para dados diários, usar `YYYY-MM-DD 00:00:00+00`. |
| `open` | ○ | Abertura. NULL para dados de fechamento apenas. |
| `high` | ○ | Máxima. |
| `low` | ○ | Mínima. |
| `close` | ✳️ | Fechamento. Único campo obrigatório (usado em cálculos de rentabilidade). |
| `volume` | ○ | Volume financeiro. NULL para ativos sem dado de volume. |

⚠️ Para ativos de renda fixa (`class = fixed_income`), `close` = valor de face ou rentabilidade acumulada.
A granularidade (diária, semanal, intraday) é controlada pelo valor de `timestamp`.

---

## `benchmarks`

| Coluna | Sem. | Regra de negócio |
|--------|:----:|-----------------|
| `id` | 🔑✳️🔒 | UUID. |
| `code` | ✳️🔒 | Código único de referência (ex: `IBOVESPA`, `CDI`, `IPCA`, `SELIC`, `IFIX`). |
| `name` | ✳️ | Nome completo (ex: "Índice Bovespa", "CDI - Taxa DI"). |
| `type` | ○ | `index` (índice de pontos) ou `rate` (taxa percentual). |
| `frequency` | ○ | `daily` (diário) ou `monthly` (mensal). CDI/SELIC: diário; IPCA: mensal. |
| `description` | ○ | Descrição livre. |

**Seed inicial recomendado:**

| code | name | type | frequency |
|------|------|------|-----------|
| `IBOVESPA` | Índice Bovespa | `index` | `daily` |
| `CDI` | Taxa CDI | `rate` | `daily` |
| `IPCA` | IPCA Mensal | `rate` | `monthly` |
| `SELIC` | Taxa Selic | `rate` | `daily` |
| `IFIX` | Índice de FIIs | `index` | `daily` |

---

## `benchmark_quotes`

| Coluna | Sem. | Regra de negócio |
|--------|:----:|-----------------|
| `benchmark_id` | 🔗✳️ | Benchmark (parte da PK). |
| `date` | ✳️ | Data de referência (parte da PK). |
| `value` | ✳️ | Para `index`: valor em pontos; para `rate`: percentual (ex: `0.0427` = 4,27% a.m.). |
| `daily_change_pct` | ○ | Variação percentual em relação ao dia anterior. NULL = não calculado. |

---

## `alerts`

| Coluna | Sem. | Regra de negócio |
|--------|:----:|-----------------|
| `id` | 🔑✳️🔒 | UUID. |
| `user_id` | 🔗✳️🔒 | Proprietário. |
| `type` | ✳️ | `price_target` (preço atingido), `budget_exceeded` (orçamento estourado), `goal_reached` (meta concluída), `debt_due` (dívida a vencer). |
| `reference_id` | ○ | UUID do objeto monitorado (ativo, orçamento, meta ou dívida). |
| `reference_type` | ○ | Tipo do objeto: `asset`, `budget`, `goal`, `debt`. |
| `condition` | ○ | Condição para disparo: `above`, `below`, `equals`, `percentage`. |
| `trigger_value` | ○ | Valor que dispara o alerta (ex: preço, % do orçamento). |
| `message` | ○ | Mensagem personalizada exibida quando o alerta dispara. |
| `active` | ✳️ | `FALSE` = alerta desativado. |
| `triggered` | ✳️ | `TRUE` = alerta já disparou ao menos uma vez. |
| `triggered_at` | ○ | Quando disparou pela última vez. |
| `created_at` | ✳️🔒 | — |

---

## `user_settings`

| Coluna | Sem. | Regra de negócio |
|--------|:----:|-----------------|
| `user_id` | 🔑🔗✳️🔒 | PK + FK → `profiles`. Relação 1:1. |
| `default_currency` | ✳️ | Moeda padrão da interface (ex: `BRL`). |
| `language` | ✳️ | Idioma (ex: `pt-BR`, `en-US`). |
| `theme` | ✳️ | `dark` ou `light`. |
| `notifications_active` | ✳️ | Habilita/desabilita verificação de alertas. |
| `month_start_day` | ✳️ | Dia em que começa o "mês financeiro" (padrão = 1; pode ser 5 para quem recebe na virada). |
| `extra_settings` | ○ | JSONB para configurações futuras sem alterar schema. |
| `updated_at` | ✳️ | Atualizado automaticamente a cada mudança. |

**Seed:** criar 1 registro padrão para `OWNER_USER_ID` com todos os valores default.

---

## `import_batches`

| Coluna | Sem. | Regra de negócio |
|--------|:----:|-----------------|
| `id` | 🔑✳️🔒 | UUID. |
| `user_id` | 🔗✳️🔒 | Proprietário. |
| `source` | ✳️ | Origem dos dados: `app1_dashboard`, `app2_investments`, `app3_controle`, `csv`, `manual`. |
| `filename` | ○ | Nome do arquivo CSV/Excel. NULL para importações de banco. |
| `status` | ✳️ | Ciclo de vida: `pending` → `processing` → `completed` / `error`. |
| `total_records` | ✳️ | Total de registros no lote de entrada. |
| `imported_records` | ✳️ | Registros inseridos com sucesso. |
| `error_records` | ✳️ | Registros que falharam. |
| `dry_run` | ✳️ | `TRUE` = simulação (sem gravação real). Nunca gravar com `dry_run = TRUE`. |
| `started_at` | ✳️🔒 | — |
| `completed_at` | ○ | NULL enquanto `status = processing`. |
| `notes` | ○ | Observações livres do operador. |

⚠️ `total_records = imported_records + error_records` — validar no final do lote.

---

## `import_logs`

| Coluna | Sem. | Regra de negócio |
|--------|:----:|-----------------|
| `id` | 🔑✳️🔒 | UUID. |
| `batch_id` | 🔗✳️🔒 | Lote pai. |
| `target_table` | ✳️ | Nome da tabela de destino (ex: `transactions`). |
| `source_id` | ○ | ID original na fonte (string para aceitar qualquer tipo). NULL = registro sem ID na origem. |
| `destination_id` | ○ | UUID gerado no destino. NULL = registro não inserido (erro ou skip). |
| `action` | ✳️ | `inserted` (novo), `skipped` (duplicado — ON CONFLICT DO NOTHING), `error` (falha). |
| `message` | ○ | Mensagem de erro ou razão do skip. NULL para `action = inserted`. |
| `created_at` | ✳️🔒 | — |

---

## `migration_source_map`

| Coluna | Sem. | Regra de negócio |
|--------|:----:|-----------------|
| `id` | 🔑✳️🔒 | UUID. |
| `target_table` | ✳️🔒 | Nome da tabela no banco unificado (ex: `transactions`). |
| `target_id` | ✳️🔒 | UUID do registro no banco unificado. |
| `source` | ✳️🔒 | Fonte: `app1`, `app2`, `app3`. |
| `source_table` | ✳️🔒 | Nome da tabela na fonte (ex: `transacoes`). |
| `source_id` | ✳️🔒 | ID original na fonte (qualquer tipo como string). |
| `migrated_at` | ✳️🔒 | Timestamp de quando foi migrado. |

**Constraints:**
- UNIQUE `(target_table, target_id, source)`: um destino mapeado para uma fonte no máximo
- UNIQUE `(source, source_table, source_id)`: um registro de origem mapeado para um destino no máximo

⚠️ Esta tabela é a **chave de idempotência da migração**.
Antes de inserir um registro, o ETL verifica se `(source, source_table, source_id)` já existe.
Se existir → skip. Se não existir → insere e registra o mapeamento.

---

## Histórico

| Data | Versão | Mudança |
|------|--------|---------|
| 2026-05-13 | v1.0 | Dicionário criado para as 22 tabelas do modelo canônico |
