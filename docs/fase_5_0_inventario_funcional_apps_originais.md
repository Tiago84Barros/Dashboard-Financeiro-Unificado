# Fase 5.0 — Inventário Funcional dos Apps Originais

> Data: 2026-05-14
> Status: **✅ Concluído — auditoria de código realizada**
> Objetivo: documentar todas as funcionalidades dos 3 apps originais antes da migração funcional para o app unificado.

---

## Resumo Executivo

| App | Tecnologia | Tabelas no banco unificado | Funcionalidades mapeadas |
|-----|-----------|:---------------------------:|:------------------------:|
| **App 1 — Dashboard Financeiro** | Python / Streamlit + Supabase + yfinance + CVM | `assets`, `portfolio_snapshots`, `portfolio_snapshot_items`, `portfolio_snapshot_analysis`, `multiplos`, `Demonstracoes_Financeiras`, `setores`, `cvm_to_ticker` | 6 seções, ~30 funções |
| **App 2 — Dashboard Investimentos** | FastAPI (Python) + Streamlit frontend + SQLite (migrado) | `transactions`, `incomes`, `assets`, `accounts`, `investment_transactions`, `dividends`, `financial_institutions`, `benchmarks`, `position_snapshots` | 5 seções, ~25 endpoints/funções |
| **App 3 — Controle Financeiro** | Next.js / TypeScript + Supabase | `transactions`, `categories`, `accounts` | 4 seções conhecidas (sem código Python local) |

---

## App 1 — Dashboard Financeiro

**Repositório:** `Dashboard-Financeiro\Dashboard\`
**Tecnologia:** Python/Streamlit, Supabase (PostgreSQL), yfinance, CVM pipeline
**Tabelas principais:** `portfolio_snapshots`, `portfolio_snapshot_items`, `portfolio_snapshot_analysis`, `assets`, `Demonstracoes_Financeiras`, `multiplos`, `setores`, `cvm_to_ticker`

### Seção 1 — Análise Básica (`page/basic.py`)

| Funcionalidade | Tela | Fonte de Dados | Tabela/API | Status no Unificado |
|---------------|------|---------------|-----------|:-------------------:|
| Score de qualidade da empresa | `basic.py` | Supabase | `multiplos`, `setores`, `Demonstracoes_Financeiras` | ❌ Ausente |
| Indicadores fundamentalistas (P/L, P/VP, ROE, ROIC, DY, etc.) | `basic.py` | Supabase | `multiplos` | ❌ Ausente |
| Classificação setorial | `basic.py` | Supabase | `setores` | ❌ Ausente |
| Busca por ticker / empresa | `basic.py` | Supabase | `assets`, `cvm_to_ticker` | ❌ Ausente |
| Comparativo de múltiplos entre empresas | `basic.py` | Supabase | `multiplos` | ❌ Ausente |
| Gráfico histórico de preços | `basic.py` | yfinance (API) | yfinance | ❌ Ausente |
| Indicadores de tendência / momentum | `basic.py` | yfinance (API) | yfinance | ❌ Ausente |

### Seção 2 — Análise Avançada (`page/advanced.py`)

| Funcionalidade | Tela | Fonte de Dados | Tabela/API | Status no Unificado |
|---------------|------|---------------|-----------|:-------------------:|
| Backtest de estratégias (compra/hold/venda) | `advanced.py` | yfinance + Supabase | `multiplos`, yfinance | ❌ Ausente |
| Valuation por DCF e múltiplos | `advanced.py` | Supabase | `Demonstracoes_Financeiras`, `multiplos` | ❌ Ausente |
| Análise de balanço patrimonial | `advanced.py` | Supabase | `Demonstracoes_Financeiras` | ❌ Ausente |
| Análise de DRE histórico | `advanced.py` | Supabase | `Demonstracoes_Financeiras` | ❌ Ausente |
| Análise de endividamento | `advanced.py` | Supabase | `Demonstracoes_Financeiras`, `multiplos` | ❌ Ausente |
| Comparativo setor vs empresa | `advanced.py` | Supabase | `setores`, `multiplos` | ❌ Ausente |
| Pipeline CVM (DFP/ITR) | background | CVM (externo) | `Demonstracoes_Financeiras`, `cvm_to_ticker` | ❌ Ausente |

### Seção 3 — Criação de Portfólio (`page/criacao_portfolio.py`)

| Funcionalidade | Tela | Fonte de Dados | Tabela/API | Status no Unificado |
|---------------|------|---------------|-----------|:-------------------:|
| Criar snapshot de portfólio | `criacao_portfolio.py` | Supabase | `portfolio_snapshots`, `portfolio_snapshot_items` | ❌ Ausente (tabela migrada ✅) |
| Adicionar/remover ativos do portfólio | `criacao_portfolio.py` | Supabase | `portfolio_snapshot_items` | ❌ Ausente |
| Definir pesos e metas de alocação | `criacao_portfolio.py` | Supabase | `portfolio_snapshot_items` | ❌ Ausente |
| Análise de diversificação | `criacao_portfolio.py` | Supabase | `portfolio_snapshot_items`, `setores` | ❌ Ausente |
| Score automático do portfólio | `criacao_portfolio.py` | Supabase + yfinance | `portfolio_snapshot_analysis` | ❌ Ausente (tabela migrada ✅) |
| Simulação de aportes mensais | `criacao_portfolio.py` | Supabase | `portfolio_snapshot_items` | ❌ Ausente |

### Seção 4 — Análise de Empresa (`page/empresa_view.py`)

| Funcionalidade | Tela | Fonte de Dados | Tabela/API | Status no Unificado |
|---------------|------|---------------|-----------|:-------------------:|
| Visão consolidada de uma empresa | `empresa_view.py` | Supabase + yfinance | `multiplos`, `Demonstracoes_Financeiras`, yfinance | ❌ Ausente |
| Gráficos de resultado histórico | `empresa_view.py` | Supabase | `Demonstracoes_Financeiras` | ❌ Ausente |
| Cotação em tempo real (intraday) | `empresa_view.py` | yfinance | yfinance | ❌ Ausente |
| Indicadores de curto prazo | `empresa_view.py` | yfinance | yfinance | ❌ Ausente |
| Histórico de dividendos da empresa | `empresa_view.py` | yfinance + Supabase | `dividends`, yfinance | ❌ Ausente |

### Seção 5 — Dividendos (`page/dividendos.py`)

| Funcionalidade | Tela | Fonte de Dados | Tabela/API | Status no Unificado |
|---------------|------|---------------|-----------|:-------------------:|
| Calendário de proventos | `dividendos.py` | Supabase | `dividends` | 🟡 Placeholder (`proventos.py`) |
| Total de proventos recebidos no mês/ano | `dividendos.py` | Supabase | `dividends` | 🟡 Placeholder |
| Proventos por ativo | `dividendos.py` | Supabase | `dividends`, `assets` | 🟡 Placeholder |
| Yield médio da carteira | `dividendos.py` | Supabase | `dividends`, `assets`, `investment_transactions` | 🟡 Placeholder |
| Projeção de proventos futuros | `dividendos.py` | Supabase | `dividends` | ❌ Ausente |
| Histórico de pagamentos por tipo (JCP, dividendo, bonificação) | `dividendos.py` | Supabase | `dividends` | 🟡 Placeholder |

### Seção 6 — Configurações (`page/configuracoes.py`)

| Funcionalidade | Tela | Fonte de Dados | Tabela/API | Status no Unificado |
|---------------|------|---------------|-----------|:-------------------:|
| Atualizar cotações (yfinance batch) | `configuracoes.py` | yfinance | `asset_quotes` | ❌ Ausente |
| Pipeline CVM manual | `configuracoes.py` | CVM | `Demonstracoes_Financeiras` | ❌ Ausente |
| Configurar OWNER_USER_ID | `configuracoes.py` | .env / Secrets | — | ✅ `configuracoes.py` (parcial) |
| Status do banco | `configuracoes.py` | Supabase | — | ✅ `configuracoes.py` |
| Modo mock / real | `configuracoes.py` | Settings | MOCK_MODE | ✅ `configuracoes.py` |

---

## App 2 — Dashboard Investimentos

**Repositório:** `Dashboard-Investimentos-main\investment-dashboard\`
**Tecnologia:** FastAPI (Python) + Streamlit (frontend) + SQLite (migrado para PostgreSQL unificado)
**Tabelas principais:** `transactions`, `incomes`, `assets`, `accounts`, `investment_transactions`, `dividends`, `financial_institutions`, `benchmarks`, `position_snapshots`
**Backend:** `app/routers/` (portfolios, transactions, incomes, assets, imports), `services/portfolio_service.py`
**Frontend:** `frontend/app.py` via `frontend/api_client.py`

### Seção 1 — Dashboard de Portfólio (`routers/portfolios.py` + frontend)

| Funcionalidade | Endpoint / Tela | Fonte de Dados | Tabela | Status no Unificado |
|---------------|----------------|---------------|--------|:-------------------:|
| Posições atuais da carteira (quantidade, preço médio, valor atual) | `GET /portfolio/positions` | SQLite→PostgreSQL | `investment_transactions`, `assets` | 🟡 Placeholder (`carteira.py`) |
| Valor total investido e patrimônio atual | `GET /portfolio/summary` | SQLite→PostgreSQL | `portfolio_positions` (view) | 🟡 Placeholder |
| Rentabilidade por ativo (%) | `GET /portfolio/performance` | SQLite + yfinance | `investment_transactions`, `asset_quotes` | 🟡 Placeholder |
| Rentabilidade da carteira vs benchmarks (CDI, IPCA, IBOV) | `GET /portfolio/benchmarks` | SQLite + APIs | `benchmarks` | 🟡 Placeholder |
| Distribuição por setor / tipo de ativo | `GET /portfolio/allocation` | SQLite→PostgreSQL | `investment_transactions`, `assets` | 🟡 Placeholder |
| Snapshot de posição (point-in-time) | `GET /portfolio/snapshots` | SQLite→PostgreSQL | `position_snapshots` | ❌ Ausente |

### Seção 2 — Histórico de Transações (`routers/transactions.py`)

| Funcionalidade | Endpoint / Tela | Fonte de Dados | Tabela | Status no Unificado |
|---------------|----------------|---------------|--------|:-------------------:|
| Listar transações de investimento (compra/venda) | `GET /transactions` | SQLite→PostgreSQL | `investment_transactions` | ❌ Ausente |
| Filtrar por ativo, data, tipo | `GET /transactions?filters` | SQLite→PostgreSQL | `investment_transactions` | ❌ Ausente |
| Adicionar transação manual | `POST /transactions` | SQLite→PostgreSQL | `investment_transactions` | ❌ Ausente |
| Calcular preço médio por ativo | computed | SQLite→PostgreSQL | `investment_transactions` | ❌ Ausente |
| Ciclo de reset (zerar posições em data) | `portfolio_service.py` | SQLite→PostgreSQL | `investment_transactions` | ❌ Ausente |

### Seção 3 — Proventos / Rendimentos (`routers/incomes.py`)

| Funcionalidade | Endpoint / Tela | Fonte de Dados | Tabela | Status no Unificado |
|---------------|----------------|---------------|--------|:-------------------:|
| Listar proventos recebidos (dividendos, JCP, rendimentos FII) | `GET /incomes` | SQLite→PostgreSQL | `dividends` | 🟡 Placeholder |
| Total mensal / anual de proventos | `GET /incomes/summary` | SQLite→PostgreSQL | `dividends` | 🟡 Placeholder |
| Proventos por ativo | `GET /incomes/by-asset` | SQLite→PostgreSQL | `dividends`, `assets` | 🟡 Placeholder |
| Classificar por tipo (dividendo, JCP, rend. FII, amortização) | `GET /incomes?tipo=` | SQLite→PostgreSQL | `dividends` | 🟡 Placeholder |

### Seção 4 — Análise e Rentabilidade (`routers/assets.py` + frontend)

| Funcionalidade | Endpoint / Tela | Fonte de Dados | Tabela | Status no Unificado |
|---------------|----------------|---------------|--------|:-------------------:|
| Buscar ativo por ticker | `GET /assets/{ticker}` | SQLite→PostgreSQL | `assets` | ❌ Ausente |
| Cotação atual via yfinance | computed | yfinance | `asset_quotes` | ❌ Ausente |
| Rentabilidade histórica por período | computed | `asset_quotes` | `asset_quotes`, `investment_transactions` | ❌ Ausente |
| Comparativo de ativos na carteira | frontend | múltiplas tabelas | — | ❌ Ausente |
| XP snapshot (prioridade sobre cálculo manual) | `portfolio_service.py` | SQLite→PostgreSQL | `xp_positions` | ❌ Ausente |
| USD→BRL conversão automática (Nomad) | `portfolio_service.py` | BCB/SGS API | external | ❌ Ausente |

### Seção 5 — Importações (`routers/imports.py`)

| Funcionalidade | Endpoint / Tela | Fonte de Dados | Tabela | Status no Unificado |
|---------------|----------------|---------------|--------|:-------------------:|
| Import de nota de corretagem XP (PDF) | `POST /imports/xp` | Upload PDF | `investment_transactions`, `xp_positions` | ❌ Ausente |
| Import de posições XP (CSV/xlsx) | `POST /imports/xp-positions` | Upload CSV | `xp_positions` | ❌ Ausente |
| Import de carteira Nomad (PDF) | `POST /imports/nomad` | Upload PDF | `investment_transactions`, `assets` | ❌ Ausente |
| Import de proventos (CSV) | `POST /imports/incomes` | Upload CSV | `dividends` | ❌ Ausente |
| Status de jobs de importação | `GET /imports/jobs` | SQLite→PostgreSQL | `import_jobs` | ❌ Ausente |
| Histórico de imports com deduplicação | `portfolio_service.py` | SQLite→PostgreSQL | `import_jobs` | ❌ Ausente |

---

## App 3 — Controle Financeiro

**Repositório:** Next.js / TypeScript (sem código Python local disponível para auditoria)
**Tecnologia:** Next.js + TypeScript + Supabase (PostgreSQL)
**Tabelas principais:** `transactions` (251 registros migrados), `categories` (38), `accounts` (2)
**Nota:** Funcionalidades reconstruídas a partir da descrição do usuário e dos dados migrados.

### Seção 1 — Controle de Receitas e Despesas

| Funcionalidade | Tela | Fonte de Dados | Tabela | Status no Unificado |
|---------------|------|---------------|--------|:-------------------:|
| Lançar transação (receita/despesa) | CRUD UI | Supabase | `transactions` | ❌ Ausente (🟡 stub `controle_financeiro.py`) |
| Editar / excluir transação | CRUD UI | Supabase | `transactions` | ❌ Ausente |
| Filtrar por período (mês/ano) | UI | Supabase | `transactions` | ❌ Ausente |
| Filtrar por categoria | UI | Supabase | `transactions`, `categories` | ❌ Ausente |
| Filtrar por conta | UI | Supabase | `transactions`, `accounts` | ❌ Ausente |
| Resumo mensal (total receitas, despesas, saldo) | Dashboard | Supabase | `transactions` | 🟡 Parcial (dashboard geral) |

### Seção 2 — Orçamento por Categoria

| Funcionalidade | Tela | Fonte de Dados | Tabela | Status no Unificado |
|---------------|------|---------------|--------|:-------------------:|
| Definir orçamento mensal por categoria | UI | Supabase | `budgets` | ❌ Ausente (tabela vazia) |
| Comparar gasto real vs orçado | Dashboard | Supabase | `transactions`, `budgets` | 🟡 Parcial (dashboard geral, orçamento implícito) |
| Alertar ao ultrapassar orçamento | Alertas | Supabase | `transactions`, `budgets` | ❌ Ausente |

### Seção 3 — Cartão de Crédito

| Funcionalidade | Tela | Fonte de Dados | Tabela | Status no Unificado |
|---------------|------|---------------|--------|:-------------------:|
| Lançar compra no cartão de crédito | UI | Supabase | `transactions` (com `payment_method=credit_card`) | ❌ Ausente |
| Gerenciar fatura do cartão | UI | Supabase | `credit_card_invoices` (ou `transactions` agrupadas) | ❌ Ausente |
| Parcelamento de compras | UI | Supabase | `transactions` (installments) | ❌ Ausente |
| Resumo de gastos no cartão | Dashboard | Supabase | `transactions` | ❌ Ausente |

### Seção 4 — Relatórios e Histórico

| Funcionalidade | Tela | Fonte de Dados | Tabela | Status no Unificado |
|---------------|------|---------------|--------|:-------------------:|
| Gráfico de despesas por categoria (histórico) | Relatórios | Supabase | `transactions`, `categories` | ❌ Ausente |
| Evolução de receitas vs despesas (meses) | Relatórios | Supabase | `transactions` | 🟡 Parcial (dashboard geral — 6 meses) |
| Exportar extratos (CSV/PDF) | Relatórios | Supabase | `transactions` | ❌ Ausente |
| Balanço por conta | Relatórios | Supabase | `transactions`, `accounts` | ❌ Ausente |

---

## Estado Atual do App Unificado

### Páginas por estado de implementação

| Página | Arquivo | Estado | Dados |
|--------|---------|:------:|-------|
| Dashboard Geral | `pages/dashboard_geral.py` | ✅ Real | `v_net_worth`, `v_monthly_cashflow`, `v_category_spending_mtd`, `v_investment_summary`, `v_budget_usage_mtd`, `dividends` |
| Configurações | `pages/configuracoes.py` | ✅ Real | `core/database.get_db_status()` |
| Controle Financeiro | `pages/controle_financeiro.py` | 🟡 Mock local | `_MOCK` dict interno, sem DB |
| Investimentos | `pages/investimentos.py` | 🟡 Mock local | `_MOCK` dict interno, sem DB |
| Metas | `pages/metas.py` | 🟡 Mock local | `_MOCK` dict interno, sem DB |
| Alertas | `pages/alertas.py` | 🟡 Mock local | `_MOCK` dict interno, sem DB |
| Carteira | `pages/carteira.py` | 🔴 Placeholder (3 linhas) | Sem dados |
| Proventos | `pages/proventos.py` | 🔴 Placeholder (3 linhas) | Sem dados |
| Empresas B3 | `pages/empresas_b3.py` | 🔴 Placeholder (3 linhas) | Sem dados |
| Empresas EUA | `pages/empresas_eua.py` | 🔴 Placeholder (3 linhas) | Sem dados |
| Cenário Macro | `pages/macro.py` | 🔴 Placeholder (3 linhas) | Sem dados |

### Core por estado

| Módulo | Estado | Exportações públicas |
|--------|:------:|---------------------|
| `core/financeiro.py` | ✅ Parcial | `get_visao_geral()`, `calcular_saude_score()` |
| `core/database.py` | ✅ Funcional | `get_engine()`, `get_db_status()`, `test_connection()` |
| `core/config.py` | ✅ Funcional | `settings` (MOCK_MODE, db_url, OWNER_USER_ID) |
| `core/auth.py` | ✅ Funcional | `verificar_autenticacao()` |
| `core/utils.py` | ✅ Funcional | `fmt_moeda()`, `fmt_percentual()`, `delta_str()` |

---

*Gerado em: 2026-05-14 | Auditoria de código dos apps originais | Dashboard Financeiro Unificado — Fase 5.0*
