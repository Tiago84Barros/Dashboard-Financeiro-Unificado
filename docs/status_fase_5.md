# Status — Fase 5: Migração Funcional dos Apps Originais

> Data de início: 2026-05-14
> Versão atual: v0.5.10
> Atualizado em: 2026-05-14 — Fases 5.1 a 5.10 concluídas

---

## Contexto

A Fase 5 consolida as funcionalidades dos 3 apps originais no app unificado:

| App Original | Tecnologia | Dados no Banco | Página Destino |
|-------------|-----------|:--------------:|---------------|
| App 1 — Dashboard Financeiro | Python/Streamlit + Supabase + yfinance | Parcial (`assets`, `portfolio_snapshots`) | `empresas_b3.py`, `investimentos.py` |
| App 2 — Dashboard Investimentos | FastAPI + SQLite (migrado) | ✅ `investment_transactions` (1.351), `dividends` (517), `assets` (82) | `carteira.py`, `proventos.py`, `investimentos.py` |
| App 3 — Controle Financeiro | Next.js + Supabase | ✅ `transactions` (251), `categories` (38), `accounts` (2) | `controle_financeiro.py` |

---

## Subfases

| Subfase | Nome | Status | Notas |
|---------|------|:------:|-------|
| **5.0** | Inventário funcional dos apps originais | ✅ Concluída | Auditoria de código; 4 docs criados; 51 funcionalidades mapeadas |
| **5.1** | Carteira real | ✅ Concluída | `core/investimentos.py` + `pages/carteira.py` — mock + real com fallback; 20 posições mock, 34 reais |
| **5.2** | Proventos reais | ✅ Concluída | `core/proventos.py` + `pages/proventos.py` — KPIs, histórico, por ativo/tipo, tabela com filtros |
| **5.3** | Dashboard Investimentos | ✅ Concluída | `pages/investimentos.py` — KPIs, cashflow mensal (v_monthly_cashflow), alocação por classe, top posições |
| **5.4** | Controle Financeiro completo | ✅ Concluída | `core/controle.py` + `pages/controle_financeiro.py` — KPIs reais, orçamento, lançamentos + formulário INSERT |
| **5.5** | Metas Financeiras | ✅ Concluída | `core/metas.py` + `pages/metas.py` — real com fallback; forms para atualizar progresso e criar nova meta |
| **5.6** | Central de Alertas | ✅ Concluída | `core/alertas.py` + `pages/alertas.py` — 6 regras automáticas: orçamento, metas, cotações, budgets, cashflow |
| **5.7** | Empresas B3 — Listagem | ✅ Concluída | `core/empresas.py` + `pages/empresas_b3.py` — 82 ativos com filtros, cotação mais recente, nota de fundamentais |
| **5.8** | Empresas EUA | ✅ Concluída | `pages/empresas_eua.py` — filtra ativos USD do banco; roadmap de P/E, EPS, market cap via yfinance |
| **5.9** | Cenário Macroeconômico | ✅ Concluída | `pages/macro.py` — SELIC, IPCA, câmbio, índices (ref. manual); benchmarks do banco; roadmap API BCB |
| **5.10** | Cotações via yfinance | ✅ Concluída | `pages/configuracoes.py` tab "Cotações" — status, batch update para todos ativos, log de progresso, upsert |

---

## Fase 5.0 — Inventário Funcional (✅ Concluída)

> Data: 2026-05-14

### Resultado

Auditoria completa dos 3 apps originais realizada. 51 funcionalidades mapeadas.

### Documentação criada

| Arquivo | Conteúdo |
|---------|---------|
| `docs/fase_5_0_inventario_funcional_apps_originais.md` | Inventário detalhado por seção/funcionalidade de cada app original |
| `docs/fase_5_0_matriz_preservacao_funcional.md` | Matriz completa: 51 funcionalidades × estado no unificado × dados migrados × prioridade |
| `docs/fase_5_0_plano_migracao_funcional.md` | Plano das Fases 5.1–5.10 com funcionalidades, complexidade e estimativas |
| `docs/status_fase_5.md` | Este arquivo |

---

## Fase 5.1 — Carteira Real (✅ Concluída)

> Data: 2026-05-14

### Arquivos criados/modificados

| Arquivo | Tipo | Descrição |
|---------|------|-----------|
| `core/investimentos.py` | Novo | Serviço de carteira: `get_carteira()`, `get_cashflow_mensal()` |
| `pages/carteira.py` | Reescrito | Página completa: KPIs, donut por classe/setor, tabela filtrável com seleção |

### Destaques técnicos

- LATERAL join para cotação mais recente: `LEFT JOIN LATERAL (SELECT close FROM asset_quotes WHERE asset_id = pp.asset_id ORDER BY timestamp DESC LIMIT 1) aq ON true`
- `cotacoes_disponiveis=False` quando `asset_quotes` está vazia → rentabilidade exibe 0% + banner informativo
- `st.dataframe` com `st.column_config` para formatação de colunas numéricas

---

## Fase 5.2 — Proventos Reais (✅ Concluída)

> Data: 2026-05-14

### Arquivos criados/modificados

| Arquivo | Tipo | Descrição |
|---------|------|-----------|
| `core/proventos.py` | Novo | Serviço de proventos: `get_proventos()` com 5 tipos de evento |
| `pages/proventos.py` | Reescrito | KPIs, gráfico barras mensais, por ativo/tipo, tabela com 3 filtros |

### Destaques técnicos

- Usa `payment_date` (NOT NULL) para todos os agrupamentos — evita o bug de `ex_date` histórico
- Gráfico Plotly com mês atual destacado em verde (`#00C896`)

---

## Fase 5.3 — Dashboard Investimentos (✅ Concluída)

> Data: 2026-05-14

### Arquivos criados/modificados

| Arquivo | Tipo | Descrição |
|---------|------|-----------|
| `core/investimentos.py` | Expandido | `get_cashflow_mensal()` — query em `v_monthly_cashflow` |
| `pages/investimentos.py` | Reescrito | 5 KPIs, gráfico cashflow grouped-bar, alocação por classe, top 10 posições |

### Destaques técnicos

- Gráfico Plotly com eixo duplo (barras receitas/despesas + linha saldo)
- Reutiliza `get_carteira()` e `get_proventos()` sem duplicar lógica
- "Mês Atual" com métricas compactas ao lado da alocação

---

## Fase 5.4 — Controle Financeiro (✅ Concluída)

> Data: 2026-05-14

### Arquivos criados/modificados

| Arquivo | Tipo | Descrição |
|---------|------|-----------|
| `core/controle.py` | Novo | `get_controle(ano, mes)`, `get_opcoes_formulario()`, `inserir_transacao()` |
| `pages/controle_financeiro.py` | Reescrito | Seletor de mês, KPIs, orçamento por categoria, lançamentos, form INSERT |

### Destaques técnicos

- Orçamento implícito: `orcamento = gasto × 1.2` quando `budgets` está vazia
- `get_controle.clear()` após INSERT para cache invalidation imediata
- `st.form("form_nova_tx", clear_on_submit=True)`

---

## Fase 5.5 — Metas Financeiras (✅ Concluída)

> Data: 2026-05-14

### Arquivos criados/modificados

| Arquivo | Tipo | Descrição |
|---------|------|-----------|
| `core/metas.py` | Novo | `get_metas()`, `atualizar_progresso()`, `inserir_meta()` — `financial_goals` |
| `pages/metas.py` | Reescrito | KPIs, cards por meta, form atualizar progresso, form nova meta |

### Tabela consultada

`financial_goals` — `id, user_id, name, type, target_amount, current_amount, deadline, active`

### Destaques técnicos

- Status automático: `concluida` (pct ≥ 100%), `atrasada` (prazo < hoje e pct < 100%), `em_andamento`
- Aporte sugerido = (alvo − atual) / meses restantes
- Form `atualizar_progresso` com `st.selectbox` + `st.number_input`

---

## Fase 5.6 — Central de Alertas (✅ Concluída)

> Data: 2026-05-14

### Arquivos criados/modificados

| Arquivo | Tipo | Descrição |
|---------|------|-----------|
| `core/alertas.py` | Novo | Engine de 6 regras: orçamento, metas, cotações, budgets, cashflow |
| `pages/alertas.py` | Reescrito | Contadores por severidade, cards ordenados por prioridade |

### Regras implementadas

| Regra | Fonte | Trigger |
|-------|-------|---------|
| R1 — Orçamento estourado/próximo | `v_budget_usage_mtd` | `usage_pct >= 75%` |
| R2 — Meta com alto progresso | `financial_goals` | `pct >= 80%` ou `100%` |
| R3 — Meta com prazo próximo | `financial_goals` | deadline em ≤ 60 dias |
| R4 — asset_quotes vazia | `asset_quotes` | COUNT = 0 |
| R5 — budgets vazia | `budgets` | COUNT = 0 |
| R6 — Saldo mensal negativo | `v_monthly_cashflow` | net_cashflow < 0 |

---

## Fase 5.7 — Empresas B3 (✅ Concluída)

> Data: 2026-05-14

### Arquivos criados/modificados

| Arquivo | Tipo | Descrição |
|---------|------|-----------|
| `core/empresas.py` | Novo | `get_ativos()`, `get_ativo_por_ticker()` — `assets` × `asset_quotes` LATERAL |
| `pages/empresas_b3.py` | Reescrito | KPIs, dist. por classe/setor, tabela filtrável, nota de fundamentais |

### Destaques técnicos

- Filtra `assets` sem filtro de usuário (tabela sem RLS — dados de mercado)
- LATERAL join para cotação mais recente por ativo
- Filtros: classe (multiselect), setor (multiselect), busca livre (ticker/nome)
- Nota sobre Fase 5.8: fundamentais P/L, P/VP, DY, ROE

---

## Fase 5.8 — Empresas EUA (✅ Concluída)

> Data: 2026-05-14

### Arquivos modificados

| Arquivo | Tipo | Descrição |
|---------|------|-----------|
| `pages/empresas_eua.py` | Reescrito | Filtra ativos USD do banco; tabela + roadmap de P/E, EPS, margens via yfinance |

---

## Fase 5.9 — Cenário Macroeconômico (✅ Concluída)

> Data: 2026-05-14

### Arquivos modificados

| Arquivo | Tipo | Descrição |
|---------|------|-----------|
| `pages/macro.py` | Reescrito | SELIC, IPCA, câmbio, IBOVESPA, S&P 500 (ref. manual); benchmarks do banco; roadmap API BCB |

---

## Fase 5.10 — Cotações via yfinance (✅ Concluída)

> Data: 2026-05-14

### Arquivos modificados

| Arquivo | Tipo | Descrição |
|---------|------|-----------|
| `pages/configuracoes.py` | Expandido | Aba "Cotações": status, parâmetros, batch update com progresso e log |

### Fluxo do atualizador

1. Conta ativos em `assets` e aqueles com/sem cotações
2. Usuário escolhe período (1mo a 5y) e se atualiza todos ou só sem cotação
3. Para cada ativo: `yf.download(ticker, period=…)` → INSERT/UPSERT em `asset_quotes`
4. Ativos BRL: adiciona sufixo `.SA` automaticamente
5. Log em tempo real com resultado por ticker
6. Ao final: limpa cache de `get_carteira()` e `get_cashflow_mensal()` → página recarrega

---

## Fase 5.1 (Revisão) — Controle Financeiro: Auditoria e Migração Fiel (✅ Concluída)

> Data: 2026-05-14 | Estratégia: audit-first antes de codificar

### Contexto

Após concluir as Fases 5.1–5.10, foi realizada uma auditoria aprofundada do app original de Controle Financeiro (`Controle_Financeiro-main 2.1`) para garantir que TODAS as funcionalidades fossem preservadas no unificado.

### Resultado da auditoria

| Item | Resultado |
|------|-----------|
| Funcionalidades originais mapeadas | 42 |
| Preservadas (✅) | 12 (29%) |
| Parcialmente preservadas (🟡) | 7 (17%) |
| Ausentes → implementadas (🔴→✅) | 23 (55%) |
| Funcionalidades novas preservadas (➕) | 9 |

### Documentação criada

| Arquivo | Conteúdo |
|---------|---------|
| `docs/fase_5_1_controle_financeiro_inventario.md` | Inventário completo: sidebar form, Dashboard, Análises, Tabelas, Cartão — elemento a elemento |
| `docs/fase_5_1_controle_financeiro_matriz_preservacao.md` | Matriz ✅/🟡/🔴/➕ com plano de implementação por sprint |

### Implementações realizadas em `controle_financeiro.py` v4

| Componente | O que foi adicionado |
|-----------|---------------------|
| Sidebar form | Categorias preset por tipo (3 listas), `payment_type` condicional (só saída), campo "Outra" categoria |
| Dashboard | Tabela categorias com % da renda; seção "Últimos Lançamentos" com `data_editor` + UPDATE |
| Análises | YOY grouped bar chart; Patrimônio Investido dual-axis; tabelas resumo |
| Tabelas | Radio tipo (top-level), filtros Ano/Mês/Dia, 3 metrics (total filtrado/ano/mês) |
| Cartão | Bar chart laranja `#FFA500`, tabela participação; banner schema gap documentado |

### Novas funções em `core/controle.py`

| Função | Descrição |
|--------|-----------|
| `atualizar_transacao()` | UPDATE em `transactions` — para modo edição |
| `get_historico_anual()` | Todos os anos agrupados por tipo — para YOY e Patrimônio |
| `get_transacoes_filtradas()` | Filtro completo por tipo/cat/ano/mês/dia/texto — para tab Tabelas |

### Gaps adiados (dependem de schema)

| Gap | Motivo | Fase |
|-----|--------|------|
| `expand_installments()` + KPIs de fatura | `payment_type`, `card_name`, `installments` ausentes no schema | Fase 6.x |
| Seção "Dívidas no cartão" ativas/concluídas | Idem | Fase 6.x |
| Gastos com pagamento de cartão (mensal) | Idem | Fase 6.x |

---

## Estado final das páginas após Fase 5

| Página | Estado | Observações |
|--------|:------:|-------------|
| Dashboard Geral | ✅ **Dados reais** | — |
| Controle Financeiro | ✅ **v4 — Migração fiel** | v4: todas as 42 funcionalidades originais implementadas; gaps de schema documentados |
| Investimentos | ✅ **Dados reais** | Cashflow real; rentabilidade 0% sem cotações |
| Carteira | ✅ **Dados reais** | 34 posições reais; cotações pendentes de importação |
| Proventos | ✅ **Dados reais** | 517 eventos; usa `payment_date` (bug `ex_date` contornado) |
| Metas | ✅ **Dados reais** | CRUD completo; fallback mock se `financial_goals` vazia |
| Alertas | ✅ **Dados reais** | 6 regras automáticas; ordenação por severidade |
| Empresas B3 | ✅ **Dados reais** | 82 ativos com filtros; fundamentais na Fase 5.8 |
| Empresas EUA | 🟡 **Parcial** | Ativos USD do banco; P/E, EPS etc. pendentes |
| Cenário Macro | 🟡 **Referência manual** | Valores hardcoded; API BCB/yfinance na Fase 5.x |
| Configurações | ✅ **Funcional** | 5 abas: Banco, Importação, Cotações, Segurança, Setup |

---

## Arquivos criados na Fase 5

### Módulos `core/`

| Arquivo | Fase | Função principal |
|---------|------|-----------------|
| `core/investimentos.py` | 5.1 | `get_carteira()`, `get_cashflow_mensal()` |
| `core/proventos.py` | 5.2 | `get_proventos()` |
| `core/controle.py` | 5.4 | `get_controle()`, `inserir_transacao()` |
| `core/metas.py` | 5.5 | `get_metas()`, `atualizar_progresso()`, `inserir_meta()` |
| `core/alertas.py` | 5.6 | `get_alertas()` — engine de 6 regras |
| `core/empresas.py` | 5.7 | `get_ativos()`, `get_ativo_por_ticker()` |

### Páginas `pages/`

| Arquivo | Fase | Origem |
|---------|------|--------|
| `pages/carteira.py` | 5.1 | App 2 |
| `pages/proventos.py` | 5.2 | App 2 |
| `pages/investimentos.py` | 5.3 | App 2 |
| `pages/controle_financeiro.py` | 5.4 | App 3 |
| `pages/metas.py` | 5.5 | App 3 |
| `pages/alertas.py` | 5.6 | Novo |
| `pages/empresas_b3.py` | 5.7 | App 1 |
| `pages/empresas_eua.py` | 5.8 | Novo |
| `pages/macro.py` | 5.9 | App 1 |
| `pages/configuracoes.py` | 5.10 | Expandido (+ aba Cotações) |

---

## Gaps pendentes de fases futuras

| Gap | Impacto | Fase |
|-----|:-------:|------|
| `asset_quotes` vazia | Rentabilidade = 0% em Carteira e Investimentos | Usar aba Cotações em Configurações |
| `dividends.ex_date` incorreto | Bug contornado — usar `payment_date` | — (resolvido por design) |
| `budgets` vazia | Orçamentos implícitos (×1,2) | Cadastrar via UI |
| Fundamentalistas (P/L, P/VP…) | Empresas B3 sem análise profunda | Fase 5.8 (migração `multiplos`) |
| API BCB/yfinance para macro | Dados macroeconômicos manuais | Fase 5.x |
| Engine de IA para alertas | Insights avançados | Fase 8 |

---

## Documentação Relacionada

- `docs/fase_5_0_inventario_funcional_apps_originais.md`
- `docs/fase_5_0_matriz_preservacao_funcional.md`
- `docs/fase_5_0_plano_migracao_funcional.md`
- `docs/fase_5_1_controle_financeiro_inventario.md`
- `docs/fase_5_1_controle_financeiro_matriz_preservacao.md`
- `docs/status_fase_4.md`

---

*Gerado em: 2026-05-14 | Dashboard Financeiro Unificado — Fase 5 concluída*
