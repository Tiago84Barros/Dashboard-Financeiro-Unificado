# Fase 5.0 — Plano de Migração Funcional

> Data: 2026-05-14
> Status: **✅ Planejamento concluído**
> Objetivo: consolidar as 51 funcionalidades dos 3 apps originais no app unificado, organizadas em fases 5.1 a 5.10.

---

## Visão Geral das Subfases

```
Fase 5.1 ──► Carteira real (posições, alocação, preço médio)         [Alta prioridade]
Fase 5.2 ──► Proventos reais (calendário, totais, por ativo)          [Alta prioridade]
Fase 5.3 ──► Dashboard Investimentos completo (rentabilidade, benchmarks)
Fase 5.4 ──► Controle Financeiro completo (CRUD de transações)        [Alta prioridade]
Fase 5.5 ──► Cartão de Crédito (fatura, parcelamento)
Fase 5.6 ──► Tabelas e lançamentos (histórico completo, relatórios)
Fase 5.7 ──► Empresas B3 — Análise Básica (múltiplos, scores)
Fase 5.8 ──► Análise Avançada (DCF, balanço, DRE, backtest)
Fase 5.9 ──► Criação e Análise de Portfólio (snapshots, score)
Fase 5.10 ──► Histórico, importações e configurações avançadas
```

**Pré-requisito para todas as fases:** alimentar `asset_quotes` com cotações reais (yfinance) — sem isso, rentabilidade, scores e análise de empresas ficam bloqueados.

---

## Fase 5.1 — Carteira Real

> **Prioridade:** Alta | **Estimativa:** 3–5 dias | **Página:** `carteira.py`

### Objetivo
Substituir o placeholder de 3 linhas em `carteira.py` por uma tela funcional conectada ao banco real, exibindo as posições atuais da carteira.

### Pré-requisitos
- `portfolio_positions` view disponível no Supabase ✅ (já existe — calculada a partir de `investment_transactions`)
- `assets` com 82 ativos migrados ✅
- `investment_transactions` com 1.351 registros ✅
- `OWNER_USER_ID` configurado em Streamlit Secrets ✅

### Funcionalidades a Implementar

| # | Funcionalidade | Complexidade | Depende de |
|---|---------------|:------------:|-----------|
| 1 | Posições atuais (ticker, quantidade, preço médio, valor atual) | Média | `portfolio_positions` view |
| 2 | Valor total da carteira e patrimônio investido | Baixa | `v_net_worth` (já conectado) |
| 3 | Distribuição por classe de ativo (donut chart) | Baixa | `assets.asset_type`, `investment_transactions` |
| 4 | Distribuição por setor (donut chart) | Baixa | `assets.sector` |
| 5 | Preço médio calculado por ativo | Média | `investment_transactions` |
| 6 | Cotação atual e valor de mercado | Alta | `asset_quotes` — **bloqueante** |
| 7 | Rentabilidade por ativo (%) | Alta | `asset_quotes` + preço médio |
| 8 | Badge de fonte de dados (real / mock / fallback) | Baixa | padrão da Fase 4.9 |

### Arquivos a Criar/Modificar
- `pages/carteira.py` — reescrever do placeholder para tela completa
- `core/investimentos.py` — novo módulo com `get_carteira()`, `get_posicoes()` (similar ao `core/financeiro.py`)
- `core/financeiro.py` — opcional: extrair helpers de portfólio para `core/investimentos.py`

### Critérios de Conclusão
- [ ] `carteira.py` exibe tabela de posições com dados reais
- [ ] Donut chart de alocação por classe de ativo
- [ ] Badge "Dados reais" / "Modo mock" / "Fallback"
- [ ] Fallback para mock sem crash
- [ ] Nenhuma credencial exposta

---

## Fase 5.2 — Proventos Reais

> **Prioridade:** Alta | **Estimativa:** 2–3 dias | **Página:** `proventos.py`

### Objetivo
Substituir o placeholder em `proventos.py` por tela funcional conectada à tabela `dividends` (517 registros migrados).

### Pré-requisitos
- `dividends` com 517 registros ✅
- Revisar `ex_date` dos registros (possível bug: dividendos aparecem como R$0 no dashboard geral)
- `assets` com 82 ativos ✅

### Funcionalidades a Implementar

| # | Funcionalidade | Complexidade | Depende de |
|---|---------------|:------------:|-----------|
| 1 | Total de proventos recebidos no mês atual | Baixa | `dividends` (filtro `payment_date` ou `ex_date`) |
| 2 | Total de proventos no ano | Baixa | `dividends` |
| 3 | Proventos por ativo (top N) | Baixa | `dividends`, `assets` |
| 4 | Calendário de proventos (tabela por mês) | Média | `dividends` |
| 5 | Classificação por tipo (dividendo, JCP, rendimento FII) | Baixa | `dividends.income_type` |
| 6 | Yield on Cost por ativo | Alta | `dividends`, `investment_transactions` |
| 7 | Projeção de proventos futuros (baseado em histórico) | Alta | `dividends` — opcional |

### Fix Crítico (ex_date)
O campo `ex_date` de todos os 517 registros parece estar em anos anteriores a 2026, causando `dividendos_ano = R$0` no dashboard geral. Investigar e corrigir antes de implementar a tela.

### Critérios de Conclusão
- [ ] `proventos.py` exibe calendário de proventos com dados reais
- [ ] Total do mês e do ano corretos
- [ ] Tabela de proventos por ativo
- [ ] Badge de fonte de dados

---

## Fase 5.3 — Dashboard Investimentos Completo

> **Prioridade:** Alta | **Estimativa:** 4–6 dias | **Páginas:** `carteira.py` + `investimentos.py`

### Objetivo
Replicar as funcionalidades de rentabilidade e comparativo vs benchmarks do App 2 (Dashboard-Investimentos).

### Pré-requisitos
- Fase 5.1 concluída ✅
- `asset_quotes` alimentada com cotações reais (crítico)
- `benchmarks` — definir fonte (CDI via BCB/SGS API, IBOV via yfinance)

### Funcionalidades a Implementar

| # | Funcionalidade | Complexidade | Depende de |
|---|---------------|:------------:|-----------|
| 1 | Rentabilidade por ativo com cotação real (%) | Alta | `asset_quotes` |
| 2 | Rentabilidade total da carteira (mês/ano/total) | Alta | `asset_quotes`, `investment_transactions` |
| 3 | Comparativo vs CDI, IPCA, IBOV | Alta | `benchmarks` (BCB/SGS + yfinance) |
| 4 | Evolução patrimonial (gráfico de linha) | Alta | `position_snapshots` ou cálculo retroativo |
| 5 | Top gainers / losers da carteira | Média | `asset_quotes`, `investment_transactions` |
| 6 | Histórico de transações (compras/vendas) | Média | `investment_transactions` |

### Alimentação de `asset_quotes`
Antes desta fase, criar script/cron para alimentar `asset_quotes` com:
- Cotações B3: via `yfinance` (ticker + `.SA`)
- Cotações EUA: via `yfinance`
- Frequência: diária (pode ser disparado manualmente em `configuracoes.py` ou via cron externo)

---

## Fase 5.4 — Controle Financeiro Completo

> **Prioridade:** Alta | **Estimativa:** 5–7 dias | **Página:** `controle_financeiro.py`

### Objetivo
Replicar as funcionalidades de CRUD de transações do App 3 (Controle Financeiro Next.js). 251 transações já migradas.

### Pré-requisitos
- `transactions` com 251 registros ✅
- `categories` com 38 categorias ✅
- `accounts` com 2 contas ✅

### Funcionalidades a Implementar

| # | Funcionalidade | Complexidade | Depende de |
|---|---------------|:------------:|-----------|
| 1 | Listar transações (paginado, filtrado por mês) | Média | `transactions` |
| 2 | Filtrar por categoria, conta, tipo (receita/despesa) | Média | `transactions`, `categories`, `accounts` |
| 3 | Formulário para adicionar transação | Alta | `transactions`, `categories`, `accounts` |
| 4 | Editar transação existente | Alta | `transactions` |
| 5 | Excluir transação | Média | `transactions` (soft delete recomendado) |
| 6 | Resumo mensal (totais por categoria) | Baixa | `v_category_spending_mtd` (já conectado) |
| 7 | Definir orçamento por categoria | Alta | `budgets` (tabela vazia — requer UI de CRUD) |
| 8 | Comparar gasto real vs orçado | Média | `transactions`, `budgets` |

### Considerações de Segurança
- Todas as operações de escrita (INSERT, UPDATE, DELETE) devem usar `WHERE user_id = :owner_user_id`
- Usar `st.session_state` para controle de formulários (evitar re-render acidental)
- Nunca expor `user_id` em campos visíveis da UI

---

## Fase 5.5 — Cartão de Crédito

> **Prioridade:** Média | **Estimativa:** 3–4 dias | **Página:** `controle_financeiro.py` (aba/seção)

### Objetivo
Gerenciar faturas e compras parceladas no cartão de crédito.

### Funcionalidades a Implementar

| # | Funcionalidade | Complexidade | Depende de |
|---|---------------|:------------:|-----------|
| 1 | Listar compras no cartão (current month) | Média | `transactions` (filtro `payment_method`) |
| 2 | Lançar compra parcelada (gerar N transações futuras) | Alta | `transactions` |
| 3 | Resumo da fatura do mês | Média | `transactions` |
| 4 | Projeção de faturas futuras (parcelas a vencer) | Alta | `transactions` (parcelas com `due_date` futuro) |

### Modelagem
O app original (App 3) usa `transactions` com `payment_method = 'credit_card'`. Parcelamentos são representados como N transações com datas diferentes. O schema atual suporta isso.

---

## Fase 5.6 — Tabelas e Lançamentos (Relatórios)

> **Prioridade:** Média | **Estimativa:** 3–4 dias | **Página:** `controle_financeiro.py` + nova aba de relatórios

### Funcionalidades a Implementar

| # | Funcionalidade | Complexidade | Depende de |
|---|---------------|:------------:|-----------|
| 1 | Gráfico de despesas por categoria (histórico 12 meses) | Média | `transactions`, `categories` |
| 2 | Evolução de receitas vs despesas (gráfico de linha) | Baixa | `transactions` |
| 3 | Balanço por conta | Média | `transactions`, `accounts` |
| 4 | Tabela de transações completa (exportável) | Média | `transactions` |
| 5 | Exportar CSV | Baixa | `transactions` (Streamlit `st.download_button`) |

---

## Fase 5.7 — Empresas B3 — Análise Básica

> **Prioridade:** Alta | **Estimativa:** 5–7 dias | **Página:** `empresas_b3.py`

### Objetivo
Replicar as funcionalidades de análise básica do App 1 (Dashboard Financeiro).

### Pré-requisitos
- Migrar tabelas `multiplos`, `setores`, `cvm_to_ticker` do Supabase do App 1 para o banco unificado
- `asset_quotes` alimentada com cotações

### Funcionalidades a Implementar

| # | Funcionalidade | Complexidade | Depende de |
|---|---------------|:------------:|-----------|
| 1 | Busca por ticker / empresa | Baixa | `assets`, `cvm_to_ticker` |
| 2 | Score de qualidade da empresa | Alta | `multiplos`, `setores` |
| 3 | Indicadores fundamentalistas (P/L, P/VP, ROE, ROIC, DY, Dívida) | Média | `multiplos` |
| 4 | Classificação setorial | Baixa | `setores`, `assets` |
| 5 | Comparativo de múltiplos entre empresas | Média | `multiplos` |
| 6 | Gráfico histórico de preços (1m, 3m, 6m, 1a, 3a) | Média | `asset_quotes` ou yfinance direto |
| 7 | Calendário de dividendos da empresa | Média | `dividends`, `assets` |

---

## Fase 5.8 — Análise Avançada de Empresas

> **Prioridade:** Média | **Estimativa:** 7–10 dias | **Página:** `empresas_b3.py` (seções avançadas)

### Pré-requisitos
- Fase 5.7 concluída
- Migrar `Demonstracoes_Financeiras` do Supabase App 1

### Funcionalidades a Implementar

| # | Funcionalidade | Complexidade | Depende de |
|---|---------------|:------------:|-----------|
| 1 | Análise de DRE histórico (receita, lucro, margens) | Alta | `Demonstracoes_Financeiras` |
| 2 | Análise de balanço patrimonial | Alta | `Demonstracoes_Financeiras` |
| 3 | Análise de endividamento | Média | `Demonstracoes_Financeiras`, `multiplos` |
| 4 | Valuation por múltiplos e DCF | Alta | `Demonstracoes_Financeiras`, `multiplos` |
| 5 | Comparativo setor vs empresa (benchmarking) | Alta | `setores`, `multiplos` |
| 6 | Backtest de estratégias (buy/hold/sell) | Alta | `asset_quotes` |
| 7 | Pipeline CVM manual (trigger via UI) | Alta | Script ETL + `Demonstracoes_Financeiras` |

---

## Fase 5.9 — Criação e Análise de Portfólio

> **Prioridade:** Média | **Estimativa:** 5–7 dias | **Página:** `investimentos.py`

### Objetivo
Replicar as funcionalidades de criação e scoring de portfólio do App 1.

### Funcionalidades a Implementar

| # | Funcionalidade | Complexidade | Depende de |
|---|---------------|:------------:|-----------|
| 1 | Criar snapshot de portfólio hipotético | Alta | `portfolio_snapshots`, `portfolio_snapshot_items` |
| 2 | Adicionar/remover ativos com pesos definidos | Média | `portfolio_snapshot_items` |
| 3 | Análise de diversificação do portfólio criado | Média | `setores`, `assets` |
| 4 | Score automático do portfólio | Alta | `portfolio_snapshot_analysis`, lógica do App 1 |
| 5 | Simulação de aportes mensais | Alta | `portfolio_snapshot_items`, `asset_quotes` |
| 6 | Comparar portfólio atual vs portfólio hipotético | Alta | múltiplas tabelas |

---

## Fase 5.10 — Histórico, Importações e Configurações Avançadas

> **Prioridade:** Baixa | **Estimativa:** 7–10 dias | **Páginas:** `configuracoes.py` + novas UIs

### Funcionalidades a Implementar

| # | Funcionalidade | Complexidade | Depende de |
|---|---------------|:------------:|-----------|
| 1 | Alimentar `asset_quotes` via yfinance (botão na UI) | Média | yfinance, `asset_quotes` |
| 2 | Import nota de corretagem XP (PDF) | Alta | Parser PDF, `investment_transactions` |
| 3 | Import posições XP (CSV/xlsx) | Alta | Parser CSV, `xp_positions` |
| 4 | Import carteira Nomad (PDF) | Alta | Parser PDF, `investment_transactions`, `assets` |
| 5 | Import proventos (CSV) | Média | Parser CSV, `dividends` |
| 6 | USD→BRL automático (via BCB/SGS API) | Média | BCB API, `investment_transactions` |
| 7 | Histórico de imports (jobs, status, erros) | Média | `import_jobs` |
| 8 | Ciclo de reset de posições (data de corte) | Alta | `investment_transactions`, lógica do App 2 |
| 9 | Snapshot histórico de patrimônio (cron mensal) | Alta | `position_snapshots` |
| 10 | Exportar dados completos (backup CSV) | Baixa | múltiplas tabelas |

---

## Ordem de Implementação Recomendada

```
IMEDIATO (desbloqueadores):
  → Alimentar asset_quotes (yfinance batch script)  ← sem isso, 60% das fases ficam incompletas
  → Revisar/corrigir ex_date dos dividends           ← dividendos_ano = R$0 no dashboard geral

PRIORIDADE ALTA:
  5.1 Carteira real          ← 1.351 transações esperando UI
  5.2 Proventos reais        ← 517 proventos esperando UI
  5.4 Controle Financeiro    ← 251 transações esperando CRUD

PRIORIDADE MÉDIA:
  5.3 Dashboard Investimentos completo   ← requer 5.1 + asset_quotes
  5.5 Cartão de crédito
  5.6 Relatórios e histórico
  5.7 Empresas B3 básico

PRIORIDADE BAIXA:
  5.8 Análise avançada de empresas
  5.9 Criação e análise de portfólio
  5.10 Importações e configurações avançadas
```

---

## Resumo de Esforço Total

| Fase | Complexidade | Estimativa |
|------|:------------:|:----------:|
| Alimentar `asset_quotes` (pré-requisito) | Média | 1–2 dias |
| 5.1 — Carteira real | Média | 3–5 dias |
| 5.2 — Proventos reais | Baixa | 2–3 dias |
| 5.3 — Investimentos completo | Alta | 4–6 dias |
| 5.4 — Controle Financeiro | Alta | 5–7 dias |
| 5.5 — Cartão de Crédito | Média | 3–4 dias |
| 5.6 — Tabelas e Relatórios | Média | 3–4 dias |
| 5.7 — Empresas B3 básico | Alta | 5–7 dias |
| 5.8 — Análise Avançada | Muito Alta | 7–10 dias |
| 5.9 — Criação de Portfólio | Alta | 5–7 dias |
| 5.10 — Importações e Config | Muito Alta | 7–10 dias |
| **Total estimado** | | **45–65 dias úteis** |

---

*Gerado em: 2026-05-14 | Dashboard Financeiro Unificado — Fase 5.0*
