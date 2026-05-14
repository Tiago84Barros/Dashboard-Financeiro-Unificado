# Fase 4.8 — Validação Pós-Migração

> Data: 2026-05-14  
> Versão: v0.4.8  
> Script de validação: `migration/06_validate_migration.py` + queries diretas  
> Executor: Claude Sonnet 4.6  
> Regras aplicadas: sem DELETE/DROP/TRUNCATE, sem alteração de schema, sem exposição de credenciais

---

## Decisão Final

> **⚠️ APROVADO COM RESSALVAS**
>
> Os dados estão íntegros e completos. Nenhuma perda de registros.  
> **Um problema crítico (P1)** impede o funcionamento correto das views financeiras e deve ser corrigido antes de conectar o app ao banco real.  
> O problema P2 (investimentos) é esperado para esta fase e será resolvido na Fase 4.9.

---

## 1. Contagens Finais por Tabela

| Tabela | Registros | Fonte | Status |
|--------|----------:|-------|:------:|
| `transactions` | 251 | App3 (Controle Financeiro) | ✅ |
| `assets` | 82 | App2 (Investimentos SQLite) | ✅ |
| `investment_transactions` | 1.351 | App2 | ✅ |
| `dividends` | 517 | App2 | ✅ |
| `categories` | 38 | Seeds (App3 + sistema) | ✅ |
| `accounts` | 2 | Seed manual | ✅ |
| `financial_institutions` | 7 | App2 | ✅ |
| `profiles` | 1 | Manual (pré-existente) | ✅ |
| `portfolios` | 0 | — | ℹ️ esperado |
| `portfolio_positions` | 0 | — | ⚠️ ver P2 |
| `migration_source_map` | 251 | App3 via 05_load | ⚠️ ver P3 |
| `import_batches` | 2 | App3 | ⚠️ ver P4 |
| `import_logs` | 251 | App3 | ✅ |
| `budgets` | 0 | — | ℹ️ esperado |
| `financial_goals` | 0 | — | ℹ️ esperado |
| `debts` | 0 | — | ℹ️ esperado |
| `cards` | 0 | — | ℹ️ esperado |

**Total de registros migrados:** 2.211 (transactions + assets + investment_transactions + dividends)  
**Seeds complementares:** 38 categorias + 2 contas + 7 instituições + 1 profile

---

## 2. Validação de Integridade

| Verificação | Resultado | Status |
|-------------|----------:|:------:|
| `transactions` sem `user_id` | 0 | ✅ |
| `investment_transactions` sem `user_id` | 0 | ✅ |
| `dividends` sem `user_id` | 0 | ✅ |
| `accounts` sem `user_id` | 0 | ✅ |
| `import_batches` sem `user_id` | 0 | ✅ |
| `investment_transactions` sem `asset_id` | 0 | ✅ |
| `dividends` sem `asset_id` | 0 | ✅ |
| `transactions` sem `category_id` | 0 | ✅ |
| `transactions` com `category_id` órfão | 0 | ✅ |
| `assets` sem `ticker` | 0 | ✅ |
| `assets` sem `sector` | 0 | ✅ |
| Duplicidades em `migration_source_map` | 0 | ✅ |

**Resultado:** 12/12 verificações de integridade OK. Nenhuma violação de FK, nenhum campo obrigatório nulo.

---

## 3. Somatórios Financeiros

### Transações (App3)

| Métrica | Valor |
|---------|------:|
| Total receitas (type=income, 47 tx) | R$ 319.708,65 |
| Total despesas (type=expense, 183 tx) | R$ 215.656,57 |
| Total transferências (type=transfer, 21 tx) | R$ 107.464,03 |
| **Saldo líquido (receita − despesa)** | **R$ 104.052,08** |

> ⚠️ **Ver P1** — Os valores acima foram calculados com `SUM FILTER (WHERE type=...)`, separando por tipo de transação. As views do banco calculam por sinal de `amount`. Como todas as despesas têm `amount > 0`, as views mostram valores incorretos.

### Investimentos (App2)

| Métrica | Valor |
|---------|------:|
| Compras (874 operações) | R$ 4.311.419,97 |
| Vendas (477 operações) | R$ 2.959.385,92 |
| Total dividendos/proventos (517 eventos) | R$ 114.144,19 |
| Ativos distintos com transações | 69 de 82 |
| Ativos sem transações | 13 |

> Os 13 ativos sem transações são históricos: frações (BRAP3F, DEXP3F…), instrumentos extintos (TESOURO IPCA+ 2024) e séries antigas de FIIs.

### Distribuição de dividendos por tipo

| Tipo | Quantidade | % |
|------|----------:|--:|
| `reit_income` (rendimentos FII) | 307 | 59% |
| `jcp` (Juros sobre Capital Próprio) | 120 | 23% |
| `dividend` (dividendos) | 90 | 17% |

---

## 4. Validação de Datas

| Tabela | Data mínima | Data máxima | Datas futuras | Datas nulas |
|--------|------------|------------|:------------:|:-----------:|
| `transactions` | 2025-03-03 | 2026-05-13 | 0 | 0 |
| `investment_transactions` | 2019-11-04 | 2026-04-29 | 0 | 0 |
| `dividends` | 2019-11-01 | 2026-04-29 | 0 | 0 |

**Resultado:** Nenhuma data futura, nenhuma data nula nos campos obrigatórios. Cobertura histórica de investimentos de **6,5 anos** (nov/2019 a abr/2026).

### Distribuição mensal de transactions por tipo

| Mês | Receita | Despesa | Transferência |
|-----|--------:|--------:|--------------:|
| 2025-03 | — | R$ 10.539,40 (2 tx) | — |
| 2025-11 | R$ 27.406,10 (5 tx) | R$ 35.204,67 (34 tx) | R$ 2.000,00 (2 tx) |
| 2025-12 | R$ 78.285,12 (20 tx) | R$ 47.577,58 (50 tx) | R$ 26.409,48 (4 tx) |
| 2026-01 | R$ 47.220,88 (7 tx) | R$ 39.658,02 (28 tx) | R$ 9.000,00 (3 tx) |
| 2026-02 | R$ 41.368,55 (5 tx) | R$ 23.368,55 (13 tx) | R$ 18.000,00 (3 tx) |
| 2026-03 | R$ 73.834,83 (3 tx) | R$ 22.363,36 (25 tx) | R$ 43.054,55 (6 tx) |
| 2026-04 | R$ 23.920,75 (4 tx) | R$ 23.019,59 (14 tx) | R$ 2.000,00 (1 tx) |
| 2026-05 | R$ 27.672,42 (3 tx) | R$ 13.925,40 (17 tx) | R$ 7.000,00 (2 tx) |

---

## 5. Validação das Views

| View | Status | Observação |
|------|:------:|------------|
| `v_account_balance` | ⚠️ | Retorna 2 linhas. Saldos **incorretos** por causa do P1 (expenses positivos) |
| `v_budget_usage_mtd` | ℹ️ | 0 linhas — esperado (sem orçamentos cadastrados) |
| `v_category_spending_mtd` | ⚠️ | 0 linhas — **incorreto**: 17 despesas em maio/2026 existem, mas `amount > 0` (P1) |
| `v_investment_summary` | ⚠️ | 0 linhas — esperado parcialmente: depende de `portfolio_positions` vazia (P2) |
| `v_monthly_cashflow` | ⚠️ | 8 linhas. `total_expenses = 0` em todos os meses — **incorreto** por causa de P1 |
| `v_net_worth` | ⚠️ | 1 linha. `investment_total = 0` (P2). `bank_balance` inflado por P1 |

### Detalhes das views com problema

**`v_monthly_cashflow`** — usa `amount > 0` para receita e `amount < 0` para despesa.
Como todas as despesas têm `amount > 0`, são contadas como receita. Exemplo:
- Novembro/2025: view mostra `total_income = R$ 62.610,77` mas o real é `receita = R$ 27.406,10` e `despesa = R$ 35.204,67`.

**`v_category_spending_mtd`** — filtra `AND amount < 0`. Nenhuma despesa tem `amount < 0`, portanto retorna 0 linhas sempre.

**`v_account_balance`** — soma todos os `amount` settled. Com expenses positivos, o saldo de "Conta Corrente" aparece como R$ 609.302,19 (deveria ser aproximadamente R$ 319.708,65 − R$ 182.129,51 = R$ 137.579,14).

**`v_net_worth`** — `bank_balance` herdado de `v_account_balance` (inflado). `investment_total = 0` porque `portfolio_positions` está vazia.

---

## 6. Assets — Distribuição por Classe e Setor

### Por classe
| Classe | Qtd |
|--------|----:|
| `reit` | 16 |
| `stock` | 66 |

### Por setor (top 5)
| Setor | Qtd |
|-------|----:|
| Fundos Imobiliários | 24 |
| Utilidade Pública | 17 |
| Financeiro | 10 |
| Consumo não Cíclico | 8 |
| Consumo Cíclico | 6 |

> 82/82 ativos com `sector` preenchido. Cobertura total após enriquecimento com dados da tabela `setores` do App1.

### Top ativos por volume de transações
| Ticker | Classe | Transações |
|--------|--------|----------:|
| EZTC3 | stock | 94 |
| GMAT3 | stock | 91 |
| BBAS3 | stock | 77 |
| SBSP3 | stock | 66 |
| EQTL3 | stock | 58 |
| MFII11 | reit | 57 |
| PETR3 | stock | 57 |

---

## 7. Categorias

38 categorias disponíveis, distribuídas por tipo:

- **expense** — 23 categorias: Alimentação, Assinaturas, Combustível, Compras, Condomínio, Despesas Domésticas, Educação, Financiamento, Impostos, Luz, Manutenção, Mercado, Outros, Plano de Saúde, Saúde, Streaming, Telefone, Viagem, Água/Gás, e demais App3
- **income** — 7 categorias: Aluguel Recebido, Freelance, Investimentos, Outros Rendimentos, Reembolso, Renda Extra, Salário
- **transfer** — 8 categorias: Aporte em Investimento, Exterior, Pagamento de Cartão, Pagamento de Fatura, Renda Fixa, TED/PIX, Tesouro Direto, Outros

**Cobertura:** 0 transações com `category_id` nulo. 0 `category_id` órfãos (todas as categorias referenciadas existem).

**Categorias mais usadas:**

| Categoria | Tipo | Transações |
|-----------|------|----------:|
| Outros | expense | 43 |
| Condomínio | expense | 31 |
| Despesas Domésticas | expense | 25 |
| Salário | income | 25 |
| Financiamento | expense | 15 |
| Pagamento de Cartão | transfer | 13 |

---

## 8. Rastreabilidade

### migration_source_map
| Source | Tabela Origem | Registros |
|--------|--------------|----------:|
| `app3` | `transactions` | 251 |
| **App2** | — | **0** ⚠️ ver P3 |

### import_batches
| Batch | Source | Status | Total | Imported | Errors |
|-------|--------|--------|------:|---------:|-------:|
| 47a71902 | app3 | `processing` ⚠️ P4 | 251 | 0 | 0 |
| b0360bc3 | app3 | `completed` ✅ | 251 | 251 | 0 |

---

## 9. Problemas Identificados

### P1 — CRÍTICO: Sinal de `amount` invertido nas despesas

**Descrição:**  
Todas as 183 transações do tipo `expense` têm `amount > 0` (positivo). As views do schema canônico usam a convenção `amount < 0` para despesas:

```sql
-- v_monthly_cashflow usa:
CASE WHEN amount > 0 THEN amount ELSE 0 END  -- receita
CASE WHEN amount < 0 THEN amount ELSE 0 END  -- despesa

-- v_category_spending_mtd usa:
WHERE amount < 0 AND type = 'expense'
```

**Causa raiz:**  
O script `04_transform_to_canonical.py` mapeou corretamente `saida → expense` (tipo), mas não negou o valor do `amount`. O App3 original armazenava todos os valores como positivos.

**Impacto:**
- `v_monthly_cashflow`: despesas = R$ 0 em todos os meses (expenses contabilizados como receita)
- `v_category_spending_mtd`: retorna 0 linhas sempre
- `v_account_balance`: saldo inflado (R$ 609.302,19 e R$ 33.527,06 sem dedução de despesas)
- `v_net_worth.bank_balance`: valor incorreto (R$ 642.829,25 vs. saldo real estimado R$ ~137.000)
- `v_budget_usage_mtd`: não funcionará quando houver orçamentos

**Registros afetados:** 183 transações — R$ 215.656,57 em despesas

**Correção recomendada (a aplicar na Fase 4.9, com autorização do usuário):**
```sql
-- Aplica sinal negativo apenas em expenses (não afeta income nem transfer)
UPDATE transactions
SET amount = -amount
WHERE type = 'expense'
  AND amount > 0;

-- Verificação pós-correção (deve retornar 183):
SELECT COUNT(*) FROM transactions WHERE type = 'expense' AND amount < 0;
```

> Esta correção é **não destrutiva** (UPDATE reversível), respeita todas as regras da Fase 4.8 e é pré-requisito para a Fase 4.9.

---

### P2 — MODERADO: `portfolio_positions` vazia → `v_investment_summary` e `v_net_worth.investment_total = 0`

**Descrição:**  
A view `v_investment_summary` é calculada a partir de `portfolio_positions` (JOIN com `assets` e `asset_quotes`). A tabela `portfolio_positions` está vazia porque o App2 migrou **transações brutas** (`investment_transactions`), não posições calculadas.

**Impacto:**
- `v_investment_summary`: 0 linhas
- `v_net_worth.investment_total`: R$ 0,00
- Patrimônio líquido subestimado: não inclui R$ ~1,35M em ativos (estimativa bruta: compras − vendas = R$ 1.352.034,05)

**Causa:** Esperado para Fase 4.8. Posições precisam ser calculadas a partir das transações, por ativo, com custo médio.

**Correção recomendada (Fase 4.9):**  
Criar script `migration/compute_portfolio_positions.py` que calcula posições consolidadas por ativo a partir de `investment_transactions` e insere em `portfolio_positions`.

---

### P3 — LEVE: App2 não rastreado em `migration_source_map`

**Descrição:**  
Os 1.350 registros do App2 (1.351 `investment_transactions` + 517 `dividends` + 82 `assets` + 7 `financial_institutions`) **não têm entrada em `migration_source_map`**. O script `migrate_app2_investimentos.py` usa `ON CONFLICT DO NOTHING` para idempotência, mas não registra no source_map.

**Risco:** Baixo. Se o script for re-executado, `ON CONFLICT DO NOTHING` protege contra duplicatas. Mas não é possível rastrear "quando e de onde veio cada registro do App2".

**Correção recomendada (futura):**  
Adicionar chamadas a `register_migration()` no script `migrate_app2_investimentos.py` após cada INSERT bem-sucedido.

---

### P4 — INFORMATIVO: `import_batches` com registro `processing` stale

**Descrição:**  
Existe um registro em `import_batches` com `status='processing'` e `imported_records=0` (batch_id=47a71902). Trata-se da primeira tentativa de migração do App3, que falhou antes dos bug fixes. O segundo batch completou com sucesso.

**Risco:** Cosmético. Não afeta dados. Não pode ser removido (regra: sem DELETE).

**Observação:** Queries que filtram `status='completed'` funcionam corretamente.

---

## 10. Riscos

| Risco | Severidade | Probabilidade | Mitigação |
|-------|:----------:|:-------------:|-----------|
| App conectado com expenses positivos → saldos errados exibidos ao usuário | Alta | Alta | Corrigir P1 antes de conectar |
| Re-execução do migrate_app2 sem source_map → sem rastreabilidade | Baixa | Baixa | ON CONFLICT protege duplicatas |
| portfolio_positions vazia → investimentos invisíveis no app | Média | Certa (atual) | Fase 4.9 deve computar posições |
| Dados de benchmark/asset_quotes ausentes → v_investment_summary calcula sem cotações | Baixa | Média | Usar `average_price` como fallback (já previsto na view) |

---

## 11. Correções Recomendadas (por prioridade)

| # | Prioridade | Descrição | Pré-requisito para Fase 4.9 |
|---|:----------:|-----------|:---------------------------:|
| 1 | 🔴 Alta | Negar `amount` de expenses (`UPDATE transactions SET amount = -amount WHERE type='expense' AND amount > 0`) | ✅ Sim |
| 2 | 🟡 Média | Computar `portfolio_positions` a partir de `investment_transactions` | ✅ Sim |
| 3 | 🟢 Baixa | Registrar App2 em `migration_source_map` | Não |
| 4 | ⚪ Muito baixa | Limpar batch stale em `import_batches` (aguardar permissão de DELETE) | Não |

---

## 12. Checklist de Aprovação

- [x] Contagens por tabela validadas
- [x] Integridade de FKs verificada (0 violações)
- [x] Campos obrigatórios preenchidos (user_id, asset_id, category_id)
- [x] Datas sem valores futuros ou nulos
- [x] Zero duplicidades em migration_source_map
- [x] Todas as 6 views respondem sem erro de execução
- [x] Distribuição de categorias verificada (38 cats, cobertura 100%)
- [x] Distribuição de setores verificada (82 assets, cobertura 100%)
- [x] Somatórios financeiros calculados e documentados
- [x] Problemas identificados, categorizados e com correções propostas
- [ ] P1 corrigido (pré-requisito para conectar app) — **pendente aprovação**
- [ ] portfolio_positions computadas — **pendente Fase 4.9**

---

## Próximos Passos

1. **Aplicar correção P1** (autorização do usuário → executar UPDATE)
2. **Criar script `compute_portfolio_positions.py`** para Fase 4.9
3. **Conectar o app ao banco** (`MOCK_MODE = False`) — Fase 4.9
4. **Testar o app com dados reais** e validar visualizações

---

*Gerado em: 2026-05-14 | Dashboard Financeiro Unificado — Fase 4.8*
