# Fase 5.1 — Matriz de Preservação Funcional: Controle Financeiro

> Data: 2026-05-14
> Objetivo: garantir que nenhuma funcionalidade do app original seja perdida na migração para o app unificado.
> Convenção: ✅ Preservado | 🟡 Parcial | 🔴 Ausente | ➕ Novo (adição do unificado)

---

## Legenda de Status

| Símbolo | Significado |
|---------|-------------|
| ✅ | Funcionalidade preservada e funcionando no unificado |
| 🟡 | Parcialmente preservada — lógica presente mas incompleta ou divergente |
| 🔴 | Ausente no unificado — precisa ser implementada |
| ➕ | Funcionalidade nova adicionada no unificado (não existia no original) |

---

## 1. Navegação e Estrutura

| Funcionalidade | Original | Unificado | Status | Ação |
|---------------|---------|-----------|:------:|------|
| 4 seções de navegação (Dashboard / Análises / Tabelas / Cartão) | `sidebar.radio` | `st.tabs()` | ✅ | — |
| Seletor de mês de referência | `date_input` na sidebar | `selectbox` no header | ✅ | — |
| Autenticação login/senha | `login_screen()` | `core/auth.py` | ✅ | — |

---

## 2. Sidebar — Formulário "Novo Lançamento"

| Funcionalidade | Original | Unificado | Status | Ação |
|---------------|---------|-----------|:------:|------|
| Tipo radio (entrada/saida/investimento) | horizontal=True | horizontal=False | 🟡 | Manter, diferença apenas de UI |
| Forma de pagamento **só para saída** | Sim | Não — aparece para todos | 🟡 | Tornar condicional |
| Categorias preset por tipo (hardcoded) | 3 listas fixas por tipo | Categorias do DB | 🟡 | Adicionar preset como fallback se DB vazio |
| Categoria personalizada (campo texto) | Sim (`cat_choice == "Outra"`) | Não | 🔴 | Adicionar campo condicional |
| Valor como text input (parse BRL) | `text_input` + `parse_brl_to_float` | `number_input` | 🟡 | Aceitar; number_input funciona bem |
| Campos Parcelas+Cartão **só para saída+cartão** | Sim | Parcial (aparece sempre) | 🟡 | Tornar condicional |
| Formulário dentro de `st.form(clear_on_submit=True)` | Sim | `st.form` presente | ✅ | — |
| Validação: valor > 0 e categoria preenchida | Sim | Sim | ✅ | — |
| INSERT em `transactions` | Via `psycopg2` | Via `SQLAlchemy` | ✅ | — |

---

## 3. Tab Dashboard

| Funcionalidade | Original | Unificado | Status | Ação |
|---------------|---------|-----------|:------:|------|
| Card: Renda do mês | ✅ | ✅ | ✅ | — |
| Card: Despesas do mês (excluindo cartão de crédito) | ✅ (excl. CC) | 🟡 (inclui tudo) | 🟡 | Corrigir lógica de caixa |
| Card: Saldo líquido (entrada − saida_caixa − investimento) | ✅ | 🟡 (sem investimento) | 🟡 | Incluir investimentos no cálculo |
| Card: Renda comprometida | ✅ | ✅ | ✅ | — |
| Gráfico categorias do mês (barras) | Altair vertical, vermelho | Plotly horizontal, vermelho | ✅ | Diferença de visualização aceitável |
| Tabela de categorias com % da renda | ✅ | 🔴 | 🔴 | Adicionar tabela/porcentagem |
| Histórico 6 meses — 3 linhas (Receitas+Despesas+Investimentos) | ✅ | 🟡 (só Receitas+Despesas) | 🟡 | Adicionar linha de Investimentos |
| Tabela resumo histórico (pivot mês × tipo) | ✅ | 🔴 | 🔴 | Adicionar tabela histórico |
| **Seção "Últimos Lançamentos"** | ✅ | 🔴 | 🔴 | Implementar seção completa |
| Modo leitura com st.dataframe | ✅ | 🔴 | 🔴 | Implementar |
| Modo edição com st.data_editor + UPDATE | ✅ | 🔴 | 🔴 | Implementar |

---

## 4. Tab Análises

| Funcionalidade | Original | Unificado | Status | Ação |
|---------------|---------|-----------|:------:|------|
| **Comparativo Ano a Ano** (tabela + bar chart grouped) | ✅ | 🔴 | 🔴 | Implementar |
| **Gastos com pagamento de cartão mensal** (bar chart + tabela) | ✅ | 🔴 | 🔴 | Implementar |
| **Evolução do patrimônio investido** (barras anuais + linha acumulada) | ✅ | 🔴 | 🔴 | Implementar |
| Pizza distribuição despesas | 🔴 | ✅ | ➕ | Preservar (adição do unificado) |
| Orçamento vs Realizado overlay | 🔴 | ✅ | ➕ | Preservar |
| Barra de progresso por categoria | 🔴 | ✅ | ➕ | Preservar |
| Taxa de poupança mensal (bar chart 12m) | 🔴 | ✅ | ➕ | Preservar |
| KPIs: taxa poupança / despesa média / maior cat / saldo acumulado | 🔴 | ✅ | ➕ | Preservar |

---

## 5. Tab Tabelas

| Funcionalidade | Original | Unificado | Status | Ação |
|---------------|---------|-----------|:------:|------|
| Tipo como radio top-level (Entradas/Saídas/Investimentos) | ✅ | 🔴 (no filtro interno) | 🟡 | Colocar radio fora do form |
| Filtro Categoria | ✅ | ✅ | ✅ | — |
| Filtro Ano | ✅ | 🔴 | 🔴 | Adicionar |
| Filtro Mês | ✅ | 🔴 | 🔴 | Adicionar |
| **Filtro Dia** | ✅ | 🔴 | 🔴 | Adicionar |
| Busca na descrição (LIKE) | ✅ | ✅ | ✅ | — |
| "Aplicar filtros" via form submit | ✅ | 🔴 | 🟡 | Filtro em tempo real também OK |
| Metric: Total filtrado | ✅ | 🔴 | 🔴 | Adicionar |
| Metric: Total no ano | ✅ | 🔴 | 🔴 | Adicionar |
| Metric: Total no mês | ✅ | 🔴 | 🔴 | Adicionar |
| Tabela de lançamentos (sem id/parcelas/user_id) | ✅ | ✅ | ✅ | — |

---

## 6. Tab Cartão de Crédito

| Funcionalidade | Original | Unificado | Status | Ação |
|---------------|---------|-----------|:------:|------|
| Filtro: **apenas saídas com `payment_type='Cartão de crédito'`** | ✅ | 🔴 (todas despesas) | 🔴 | Corrigir filtro |
| Sidebar slider: dia de vencimento da fatura | ✅ | 🔴 | 🔴 | Adicionar slider |
| **`expand_installments(df, due_day)`** | ✅ | 🔴 | 🔴 | Implementar lógica de parcelas |
| **`compute_card_summary(expanded)`** | ✅ | 🔴 | 🔴 | Implementar |
| KPI: Cartão a pagar no mês | ✅ | 🔴 | 🔴 | Implementar |
| KPI: Dívida do ano ainda a pagar | ✅ | 🔴 | 🔴 | Implementar |
| KPI: Valor já pago no ano | ✅ | 🔴 | 🔴 | Implementar |
| Bar chart categorias (laranja, ano atual) + tabela participação | ✅ | 🔴 | 🔴 | Implementar |
| Line chart histórico anual por vencimento | ✅ | 🔴 | 🔴 | Implementar |
| **Seção "Dívidas no cartão"** | ✅ | 🔴 | 🔴 | Implementar |
| Form filtros: Cartão / Categoria / Ano / Status / texto | ✅ | 🔴 | 🔴 | Implementar |
| Dívidas ativas (view + edit) | ✅ | 🔴 | 🔴 | Implementar |
| Dívidas concluídas (view + edit) | ✅ | 🔴 | 🔴 | Implementar |
| UPDATE via `update_transaction_fields()` | ✅ | 🔴 | 🔴 | Implementar |
| KPIs simples (Total Despesas, Ticket Médio, Maior) | 🔴 | ✅ | ➕ | Substituir pelos KPIs corretos |

---

## Resumo Executivo

| Tab | Funcionalidades Originais | Preservadas | Parciais | Ausentes |
|-----|:------------------------:|:-----------:|:--------:|:--------:|
| Sidebar Form | 8 | 4 | 3 | 1 |
| Dashboard | 11 | 5 | 3 | 3 |
| Análises | 3 | 0 | 0 | 3 |
| Tabelas | 8 | 3 | 1 | 4 |
| Cartão | 12 | 0 | 0 | 12 |
| **Total** | **42** | **12 (29%)** | **7 (17%)** | **23 (55%)** |

**Funcionalidades novas do unificado (preservar):** 9 (pizza, orçamento, taxa poupança, barra progresso, KPIs extras, etc.)

---

## Plano de Implementação (Fase 5.1)

### Sprint 1 — Prioridade Alta (bloqueante)
- [ ] Corrigir `_tab_cartao()`: filtrar por `payment_type = 'Cartão de crédito'` + `expand_installments` + KPIs corretos
- [ ] Adicionar seção "Últimos Lançamentos" no Dashboard com view + edit
- [ ] Implementar seção "Dívidas no cartão" (ativas + concluídas)

### Sprint 2 — Análises e Tabelas
- [ ] Adicionar YOY no tab Análises
- [ ] Adicionar gastos de cartão mensal no tab Análises
- [ ] Adicionar patrimônio investido no tab Análises
- [ ] Corrigir tab Tabelas: tipo radio + filtros Ano/Mês/Dia + totais

### Sprint 3 — Refinamentos
- [ ] Corrigir lógica de caixa (excluir CC do fluxo)
- [ ] Adicionar Investimentos na linha do histórico 6m
- [ ] Tornar `payment_type` condicional no sidebar form
- [ ] Adicionar campo "Categoria personalizada" no form

---

*Gerado em: 2026-05-14 | Dashboard Financeiro Unificado — Fase 5.1*
