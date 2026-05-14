# Fase 4.8 — Validação Pós-Migração

> Data: 2026-05-14  
> Versão: v0.4.8  
> Script de validação: `migration/06_validate_migration.py` + queries diretas  
> Executor: Claude Sonnet 4.6  
> Regras aplicadas: sem DELETE/DROP/TRUNCATE, sem alteração de schema, sem exposição de credenciais

---

## Decisão Final

> **✅ APROVADO — pronto para Fase 4.9**
>
> Os dados estão íntegros e completos. Nenhuma perda de registros.  
> **P1 corrigido em 2026-05-14** — `UPDATE transactions SET amount = -amount WHERE type='expense' AND amount > 0` (182 registros).  
> Todas as 6 views financeiras funcionam corretamente após a correção.  
> O problema P2 (portfolio_positions) é esperado para esta fase e será resolvido na Fase 4.9.

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

### Estado final (pós-correção P1)

| View | Status | Resultado |
|------|:------:|-----------|
| `v_account_balance` | ✅ | 2 linhas — Conta Corrente R$ 245.043,17 · C6 R$ −33.527,06 |
| `v_budget_usage_mtd` | ℹ️ | 0 linhas — esperado (sem orçamentos) |
| `v_category_spending_mtd` | ✅ | 9 categorias em maio/2026 · Top: Outros R$ 4.666,67 |
| `v_investment_summary` | ⚠️ | 0 linhas — depende de `portfolio_positions` (P2, Fase 4.9) |
| `v_monthly_cashflow` | ✅ | 8 meses · Receitas e despesas corretas por mês |
| `v_net_worth` | ⚠️ | bank_balance R$ 211.516,11 ✅ · investment_total R$ 0 (P2) |

### `v_monthly_cashflow` — resultado pós-P1

| Mês | Receita | Despesa | Saldo |
|-----|--------:|--------:|------:|
| 2025-03 | — | R$ 10.539,40 | R$ −10.539,40 |
| 2025-11 | R$ 27.406,10 | R$ 35.204,67 | R$ −7.798,57 |
| 2025-12 | R$ 78.285,12 | R$ 47.577,58 | R$ +30.707,54 |
| 2026-01 | R$ 47.220,88 | R$ 39.658,02 | R$ +7.562,86 |
| 2026-02 | R$ 41.368,55 | R$ 23.368,55 | R$ +18.000,00 |
| 2026-03 | R$ 73.834,83 | R$ 22.363,36 | R$ +51.471,47 |
| 2026-04 | R$ 23.920,75 | R$ 23.019,59 | R$ +901,16 |
| 2026-05 | R$ 27.672,42 | R$ 13.925,40 | R$ +13.747,02 |

### `v_category_spending_mtd` — maio/2026

| Categoria | Gasto |
|-----------|------:|
| Outros | R$ 4.666,67 |
| Pagamento de Cartão | R$ 3.025,57 |
| Financiamento | R$ 1.677,40 |
| Condomínio | R$ 1.591,08 |
| Despesas Domésticas | R$ 1.390,00 |
| Saúde | R$ 720,00 |
| Luz | R$ 336,91 |
| Combustível | R$ 332,77 |
| Internet | R$ 185,00 |

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

### P1 — ✅ CORRIGIDO: Sinal de `amount` invertido nas despesas

**Status:** Corrigido em 2026-05-14 (182 registros atualizados)

**Descrição original:**  
Todas as 183 transações do tipo `expense` tinham `amount > 0` (positivo). As views do schema canônico usam a convenção `amount < 0` para despesas.

**Causa raiz:**  
O script `04_transform_to_canonical.py` mapeou corretamente `saida → expense` (tipo), mas não negou o valor do `amount`. O App3 original armazenava todos os valores como positivos.

**Correção aplicada:**
```sql
UPDATE transactions
SET amount = -amount
WHERE type = 'expense'
  AND amount > 0;
-- Resultado: 182 rows updated (1 expense com amount=0 permanece)
```

**Verificação pós-correção:**
- `expenses com amount < 0`: 182 ✅
- `expenses com amount > 0`: 0 ✅  
- `expenses com amount = 0`: 1 (correto — zero é neutro)

**Impacto após correção:**
- `v_monthly_cashflow`: despesas e receitas corretas por mês ✅
- `v_category_spending_mtd`: retorna categorias de gasto do mês atual ✅
- `v_account_balance`: Conta Corrente = R$ 245.043,17 | Cartão C6 = R$ −33.527,06 ✅
- `v_net_worth.bank_balance`: R$ 211.516,11 (saldo líquido real) ✅

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

| # | Prioridade | Descrição | Status |
|---|:----------:|-----------|:------:|
| 1 | 🔴 Alta | Negar `amount` de expenses | ✅ Aplicado em 2026-05-14 |
| 2 | 🟡 Média | Computar `portfolio_positions` a partir de `investment_transactions` | ⏳ Fase 4.9 |
| 3 | 🟢 Baixa | Registrar App2 em `migration_source_map` | ⏳ Futuro |
| 4 | ⚪ Muito baixa | Limpar batch stale em `import_batches` (aguardar permissão de DELETE) | ⏳ Futuro |

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
- [x] P1 corrigido — `UPDATE` aplicado, 182 registros, views validadas
- [ ] portfolio_positions computadas — **pendente Fase 4.9**

---

## Próximos Passos

1. ~~**Aplicar correção P1**~~ ✅ Concluído
2. **Criar script `compute_portfolio_positions.py`** — Fase 4.9
3. **Conectar o app ao banco** (`MOCK_MODE = False`) — Fase 4.9
4. **Testar o app com dados reais** e validar visualizações

---

*Gerado em: 2026-05-14 | Dashboard Financeiro Unificado — Fase 4.8*
