# Fase 5.0 — Matriz de Preservação Funcional

> Data: 2026-05-14
> Status: **✅ Concluído**
> Objetivo: mapear cada funcionalidade dos apps originais para seu estado no app unificado.

---

## Legenda

| Símbolo | Significado |
|:-------:|-------------|
| ✅ | Implementado e funcional no app unificado |
| 🟡 | Parcialmente implementado (mock, placeholder ou dado parcial) |
| ❌ | Ausente no app unificado |
| 📦 | Dados migrados para o banco unificado (mas sem UI) |
| 🔴 | Gap crítico — impacta MVP ou experiência core |

---

## Matriz Completa

| App Origem | Seção | Funcionalidade | Existe no Unificado? | Dados Migrados? | Página Destino | Prioridade | Próxima Ação |
|-----------|-------|---------------|:--------------------:|:---------------:|----------------|:----------:|-------------|
| **App 2** | Carteira | Posições atuais (quantidade, preço médio, valor) | 🟡 Placeholder | 📦 `portfolio_positions` (view) | `carteira.py` | 🔴 Alta | Fase 5.1 |
| **App 2** | Carteira | Patrimônio total e valor atual | 🟡 Parcial (dashboard geral) | 📦 `v_net_worth` | `carteira.py` | 🔴 Alta | Fase 5.1 |
| **App 2** | Carteira | Distribuição por setor/tipo | 🟡 Placeholder | 📦 `assets`, `investment_transactions` | `carteira.py` | 🔴 Alta | Fase 5.1 |
| **App 2** | Carteira | Rentabilidade por ativo (%) | 🟡 Placeholder | ❌ `asset_quotes` vazia | `carteira.py` | 🔴 Alta | Fase 5.0 → alimentar cotações |
| **App 2** | Carteira | Rentabilidade vs benchmarks (CDI, IPCA, IBOV) | ❌ Ausente | ❌ `benchmarks` vazia | `carteira.py` / `investimentos.py` | Alta | Fase 5.3 |
| **App 2** | Carteira | Snapshot de posição (point-in-time) | ❌ Ausente | 📦 `position_snapshots` | `carteira.py` | Média | Fase 5.10 |
| **App 2** | Proventos | Calendário de proventos | 🟡 Placeholder | 📦 `dividends` (517 reg.) | `proventos.py` | Alta | Fase 5.2 |
| **App 2** | Proventos | Total mensal/anual de proventos | 🟡 Parcial (dashboard geral = R$0 por bug de ex_date) | 📦 `dividends` | `proventos.py` | Alta | Fase 5.2 + fix ex_date |
| **App 2** | Proventos | Proventos por ativo | 🟡 Placeholder | 📦 `dividends`, `assets` | `proventos.py` | Alta | Fase 5.2 |
| **App 2** | Proventos | Classificação por tipo (dividendo, JCP, rend. FII) | 🟡 Placeholder | 📦 `dividends` | `proventos.py` | Média | Fase 5.2 |
| **App 2** | Proventos | Yield médio da carteira | ❌ Ausente | 📦 `dividends`, `investment_transactions` | `proventos.py` | Média | Fase 5.2 |
| **App 2** | Proventos | Projeção de proventos futuros | ❌ Ausente | ❌ Não calculado | `proventos.py` | Baixa | Fase 5.2 (opcional) |
| **App 2** | Histórico | Listar transações de investimento | ❌ Ausente | 📦 `investment_transactions` (1.351 reg.) | `carteira.py` / `investimentos.py` | Alta | Fase 5.3 |
| **App 2** | Histórico | Filtrar por ativo, data, tipo | ❌ Ausente | 📦 `investment_transactions` | `investimentos.py` | Alta | Fase 5.3 |
| **App 2** | Histórico | Calcular preço médio por ativo | ❌ Ausente | 📦 `investment_transactions` | `carteira.py` | Alta | Fase 5.1 |
| **App 2** | Importações | Import nota de corretagem XP (PDF) | ❌ Ausente | ❌ Sem pipeline | `configuracoes.py` | Baixa | Fase 5.10 |
| **App 2** | Importações | Import posições XP (CSV/xlsx) | ❌ Ausente | ❌ Sem pipeline | `configuracoes.py` | Baixa | Fase 5.10 |
| **App 2** | Importações | Import carteira Nomad (PDF) | ❌ Ausente | ❌ Sem pipeline | `configuracoes.py` | Baixa | Fase 5.10 |
| **App 2** | Análise | Cotação atual via yfinance | ❌ Ausente | ❌ `asset_quotes` vazia | `carteira.py` / `investimentos.py` | 🔴 Alta | Fase 5.0 — alimentar cotações |
| **App 2** | Análise | USD→BRL automático (Nomad) | ❌ Ausente | ❌ Não configurado | `investimentos.py` | Baixa | Fase 5.10 |
| **App 2** | Análise | XP snapshot (prioridade sobre cálculo manual) | ❌ Ausente | ❌ `xp_positions` vazia | `carteira.py` | Baixa | Fase 5.10 |
| **App 1** | Análise Básica | Score de qualidade da empresa | ❌ Ausente | ❌ `multiplos` não migrado | `empresas_b3.py` | Alta | Fase 5.7 |
| **App 1** | Análise Básica | Indicadores fundamentalistas (P/L, P/VP, ROE...) | ❌ Ausente | ❌ `multiplos` não migrado | `empresas_b3.py` | Alta | Fase 5.7 |
| **App 1** | Análise Básica | Classificação setorial | ❌ Ausente | ❌ `setores` não migrado para unificado | `empresas_b3.py` | Alta | Fase 5.7 |
| **App 1** | Análise Básica | Busca por ticker/empresa | ❌ Ausente | 📦 `assets` (82 ativos) | `empresas_b3.py` | Alta | Fase 5.7 |
| **App 1** | Análise Básica | Gráfico histórico de preços | ❌ Ausente | ❌ `asset_quotes` vazia | `empresas_b3.py` | Alta | Fase 5.7 |
| **App 1** | Análise Avançada | Backtest de estratégias | ❌ Ausente | ❌ Requer `asset_quotes` + lógica | `empresas_b3.py` | Baixa | Fase 5.8 |
| **App 1** | Análise Avançada | Valuation por DCF e múltiplos | ❌ Ausente | ❌ `Demonstracoes_Financeiras` não migrado | `empresas_b3.py` | Média | Fase 5.8 |
| **App 1** | Análise Avançada | Análise de balanço / DRE histórico | ❌ Ausente | ❌ `Demonstracoes_Financeiras` não migrado | `empresas_b3.py` | Média | Fase 5.8 |
| **App 1** | Análise Avançada | Pipeline CVM (DFP/ITR) | ❌ Ausente | ❌ Script não migrado | background / `configuracoes.py` | Baixa | Fase 5.8 |
| **App 1** | Criação Portfólio | Criar snapshot de portfólio | ❌ Ausente | 📦 `portfolio_snapshots` (tabela vazia) | `investimentos.py` | Média | Fase 5.9 |
| **App 1** | Criação Portfólio | Score automático do portfólio | ❌ Ausente | 📦 `portfolio_snapshot_analysis` (tabela vazia) | `investimentos.py` | Média | Fase 5.9 |
| **App 1** | Criação Portfólio | Simulação de aportes mensais | ❌ Ausente | ❌ Não calculado | `investimentos.py` | Baixa | Fase 5.9 |
| **App 1** | Dividendos | Calendário de proventos (visão empresa) | 🟡 Placeholder | 📦 `dividends` | `empresas_b3.py` | Média | Fase 5.7 |
| **App 1** | Configurações | Atualizar cotações (yfinance batch) | ❌ Ausente | ❌ `asset_quotes` vazia | `configuracoes.py` | 🔴 Alta | Fase 5.0 |
| **App 1** | Configurações | Pipeline CVM manual | ❌ Ausente | ❌ Sem script integrado | `configuracoes.py` | Baixa | Fase 5.8 |
| **App 3** | Controle | Lançar transação (receita/despesa) | ❌ Ausente | 📦 `transactions` (251 reg.) | `controle_financeiro.py` | 🔴 Alta | Fase 5.4 |
| **App 3** | Controle | Editar/excluir transação | ❌ Ausente | 📦 `transactions` | `controle_financeiro.py` | 🔴 Alta | Fase 5.4 |
| **App 3** | Controle | Filtrar por período/categoria/conta | ❌ Ausente | 📦 `transactions`, `categories`, `accounts` | `controle_financeiro.py` | 🔴 Alta | Fase 5.4 |
| **App 3** | Controle | Resumo mensal | 🟡 Parcial (dashboard geral) | 📦 `v_monthly_cashflow` | `controle_financeiro.py` | Alta | Fase 5.4 |
| **App 3** | Orçamento | Definir orçamento por categoria | ❌ Ausente | ❌ `budgets` vazia | `controle_financeiro.py` | Alta | Fase 5.4 |
| **App 3** | Orçamento | Comparar gasto vs orçado | 🟡 Parcial (dashboard geral, orçamento implícito) | 📦 `v_category_spending_mtd` | `controle_financeiro.py` | Alta | Fase 5.4 |
| **App 3** | Cartão | Gerenciar fatura do cartão | ❌ Ausente | ❌ Não modelado | `controle_financeiro.py` | Média | Fase 5.5 |
| **App 3** | Cartão | Parcelamento de compras | ❌ Ausente | ❌ Não modelado | `controle_financeiro.py` | Média | Fase 5.5 |
| **App 3** | Relatórios | Gráfico de despesas por categoria (histórico) | ❌ Ausente | 📦 `transactions` | `controle_financeiro.py` | Alta | Fase 5.6 |
| **App 3** | Relatórios | Evolução receitas vs despesas | 🟡 Parcial (dashboard geral — 6 meses) | 📦 `v_monthly_cashflow` | `controle_financeiro.py` | Alta | Fase 5.6 |
| **App 3** | Relatórios | Exportar extratos (CSV/PDF) | ❌ Ausente | 📦 `transactions` | `controle_financeiro.py` | Baixa | Fase 5.6 |

---

## Resumo por Status

| Status | Funcionalidades | % |
|:------:|:--------------:|:--:|
| ✅ Implementado e real | 2 | 4% |
| 🟡 Parcial / mock / placeholder | 12 | 24% |
| ❌ Ausente | 37 | 72% |
| **Total mapeado** | **51** | |

## Dados Migrados vs. Ausentes

| Tabela | Registros | UI no Unificado |
|--------|----------:|:---------------:|
| `investment_transactions` | 1.351 | ❌ Nenhuma |
| `dividends` | 517 | 🟡 Placeholder |
| `transactions` | 251 | ❌ Nenhuma |
| `assets` | 82 | 🟡 Via dashboard geral |
| `categories` | 38 | ❌ Nenhuma |
| `portfolio_positions` (view) | calculado | 🟡 Placeholder |
| `accounts` | 2 | ❌ Nenhuma |
| `budgets` | 0 | ❌ Nenhuma |
| `asset_quotes` | 0 | ❌ **Gap crítico** |
| `xp_positions` | 0 | ❌ Nenhuma |
| `benchmarks` | 0 | ❌ Nenhuma |

## Gaps Críticos (🔴)

1. **`asset_quotes` vazia** — bloqueia rentabilidade real, scores de portfólio e análise de empresas. Requer alimentação via yfinance.
2. **Página `carteira.py`** — placeholder de 3 linhas. 1.351 transações de investimento migradas sem nenhuma UI.
3. **Página `controle_financeiro.py`** — mock local desconectado. 251 transações migradas sem nenhuma UI de CRUD.
4. **`budgets` vazia** — orçamentos estão sendo gerados artificialmente (×1,2). Bloqueia alertas reais de orçamento.

---

*Gerado em: 2026-05-14 | Dashboard Financeiro Unificado — Fase 5.0*
