# Fase 5.1 — Inventário Funcional: Controle Financeiro Original vs. Unificado

> Data: 2026-05-14
> Status: **✅ Auditoria concluída — implementação em andamento**
> Fonte original: `Projetos/Antigos/Controle financeiro em Python/Versão 2.0/Controle_Financeiro-main 2.1/`

---

## Visão Geral do App Original

| Item | Detalhe |
|------|---------|
| URL de deploy | `controlefinanceirotsb.streamlit.app` |
| Tecnologia | Python / Streamlit + psycopg2 + Altair + pandas |
| Banco de dados | Supabase PostgreSQL (tabela `transactions`) |
| Autenticação | Login/Senha com SHA-256 em tabela `app_users` |
| Arquivos fonte | `Controle.py`, `cartao.py`, `Consulta_Tabelas.py` |

---

## Estrutura de Navegação Original

```
sidebar.radio("Navegação", ["Dashboard", "Análises", "Tabelas", "Cartão de Crédito"])
```

As 4 seções eram páginas completas acionadas por radio no sidebar.

---

## Seção 1 — Dashboard (`Controle.py → main()`)

### Sidebar — Filtros + Novo Lançamento

| Elemento | Tipo | Comportamento original |
|---------|------|------------------------|
| `ref_date` | `date_input` | Mês de referência (DD/MM/YYYY) |
| `t_type` | `radio(horizontal=True)` | `"entrada" \| "saida" \| "investimento"` |
| `payment_type` | `selectbox` | **Só aparece para `saida`**: Conta \| Cartão de crédito \| Dinheiro \| Pix |
| Categorias | `selectbox` | **Preset por tipo** (ver abaixo) |
| `cat_choice == "Outra"` | `text_input` | Campo livre quando categoria = Outra |
| `d` | `date_input` | Data da transação |
| `valor_str` | `text_input` | **Aceita formato BRL: "1.234,56"** (parse via `parse_brl_to_float`) |
| Parcelas + Cartão | `number_input` + `text_input` | **Só aparecem quando `saida` + `Cartão de crédito`** |
| `description` | `text_area` | Opcional |
| Salvar | `form_submit_button` | Dentro de `st.form("novo_lancamento", clear_on_submit=True)` |

**Categorias pré-definidas por tipo:**
```python
income_categories   = ["Salário", "Renda Extra", "Dividendos", "Reembolso", "Outros"]
expense_categories  = ["Mercado", "Compras", "Condomínio", "Luz", "Internet",
                        "Transporte", "Combustível", "Saúde", "Despesas Domésticas",
                        "Lazer", "Assinaturas", "Educação", "Restaurante",
                        "Financiamento", "Pagamento de Cartão", "Outros"]
investment_categories = ["Renda Fixa", "Renda Variável", "Exterior", "Reserva de Despesa", "Outra"]
```

### Corpo — 4 KPI cards CSS

| Card | Fórmula |
|------|---------|
| Renda do mês | `SUM(amount) WHERE type='entrada'` |
| Despesas do mês | `SUM(amount) WHERE type='saida' AND payment_type != 'Cartão de crédito'` |
| Saldo líquido do mês | `entrada − saida_caixa − investimento` |
| Renda comprometida | `(saida_caixa + investimento) / entrada × 100` |

> **Regra**: Saídas com `payment_type = 'Cartão de crédito'` NÃO entram no fluxo de caixa do mês (são gastos futuros).

### Corpo — 2 gráficos (Altair)

| Gráfico | Detalhe |
|---------|---------|
| Gastos por categoria | Bar chart **vertical** (cor `#ff4d4d`) + tabela formatada com `% da renda` |
| Histórico 6 meses | Line chart **Receitas × Despesas × Investimentos** (3 linhas) + pivot table |

### Corpo — Últimos Lançamentos (seção ausente no unificado)

| Elemento | Detalhe |
|---------|---------|
| Dados | Últimos 20 de cada tipo (entrada/investimento/saida) concatenados |
| Checkbox | `"Habilitar edição dos últimos lançamentos"` |
| Modo leitura | `st.dataframe` — Tipo, Categoria, Data, Valor, Forma, Cartão, Parcelas, Descrição |
| Modo edição | `st.data_editor` com ID desabilitado e TextColumn para Valor (BRL) |
| Salvar | UPDATE direto via psycopg2 (todos os campos exceto ID) |

---

## Seção 2 — Análises (`Controle.py → render_analises()`)

| Sub-seção | Conteúdo |
|-----------|---------|
| **Comparativo Ano a Ano** | Pivot `year × type → amount`; tabela formatada; bar chart grouped por year+type |
| **Gastos com pagamento de cartão (mensal)** | Filtra `type='saida' AND payment_type='Conta' AND category='Pagamento de Cartão'`; selectbox por ano; bar chart mensal; tabela resumo |
| **Evolução do patrimônio investido** | Filtra `type='investimento'`; agrupa por ano; tabela (Ano / Investido / Acumulado); combo chart barras+linha dual-axis |

---

## Seção 3 — Tabelas (`Consulta_Tabelas.py → pagina_consulta_tabelas()`)

| Elemento | Detalhe |
|---------|---------|
| Tipo (top radio) | Entradas \| Saídas \| Investimentos (fora do form) |
| Form de filtros | 4 colunas: Categoria, Ano, Mês, **Dia** |
| Busca texto | Buscar na descrição (LIKE %texto%) |
| `st.form_submit_button` | "Aplicar filtros" — sem submit sem dados |
| Resumo | 3 metrics: Total filtrado / Total no ano / Total no mês |
| Tabela | Tipo, Categoria, Data, Valor, Forma, Descrição (sem id/card/parcelas/user_id) |

---

## Seção 4 — Cartão de Crédito (`cartao.py → pagina_cartao()`)

### Configuração sidebar
- Slider: `"Dia de vencimento da fatura"` (1–28, default 5)

### Dados filtrados
Apenas `type='saida' AND payment_type='Cartão de crédito'`

### Função `expand_installments(df, due_day)`
- Expande cada compra em N linhas (uma por parcela)
- Regra de vencimento: dia compra ≤ due_day → vence neste mês; senão mês seguinte

### Função `compute_card_summary(expanded)`
- `valor_fatura_mes`: parcelas com vencimento no mês atual
- `divida_ano_a_pagar`: parcelas do ano atual com vencimento ≥ hoje
- `valor_pago_ano`: parcelas do ano atual com vencimento < hoje

### Corpo

| Seção | Conteúdo |
|-------|---------|
| 3 KPIs (`st.metric`) | Cartão a pagar no mês / Dívida do ano ainda a pagar / Valor já pago no ano |
| Categorias + gráfico | Bar chart **vertical laranja** (`#FFA500`) categorias do ano atual + tabela participação |
| Histórico anual | Line chart por vencimento (Janeiro → Dezembro) |
| **Dívidas no cartão** | Form com filtros: Cartão / Categoria / Ano da compra / Status / texto |
| Dívidas ativas | `st.dataframe` + checkbox edição → `st.data_editor` (Cartão, Categoria, Descrição editáveis) |
| Dívidas concluídas | Idem para compras 100% quitadas |
| Salvar alterações | UPDATE via `update_transaction_fields()` (category, card_name, description) |

---

## Estado Atual do App Unificado

### `pages/controle_financeiro.py` — versão v3

| Seção | Status | Observações |
|-------|:------:|-------------|
| **Tab Dashboard** | 🟡 Parcial | 4 KPIs CSS presentes; histórico sem Investimentos; **Últimos Lançamentos ausente** |
| **Tab Análises** | 🟡 Parcial | Pizza + orçamento overlay + taxa poupança; **YOY, cartão mensal e patrimônio investido ausentes** |
| **Tab Tabelas** | 🟡 Parcial | Filtros (tipo/cat/busca); **sem filtro Dia; sem totais ano/mês** |
| **Tab Cartão** | 🔴 Incompleto | Mostra TODAS as despesas (não filtra cartão); **sem expand_installments; sem dívidas section; sem edit** |
| **Sidebar Form** | 🟡 Parcial | Categorias do DB (não preset); valor como number_input (não text); payment_type sempre visível |

### `core/controle.py`

| Função | Status |
|--------|:------:|
| `get_controle(ano, mes)` | ✅ Funcional — retorna KPIs + categorias + transações |
| `get_opcoes_formulario()` | ✅ Funcional — categorias e contas do DB |
| `inserir_transacao()` | ✅ Funcional — INSERT em `transactions` |

---

## Gaps Identificados para Implementação

### Prioridade Alta (funcionalidades presentes no original, ausentes no unificado)

| Gap | Tab | Impacto |
|-----|-----|:-------:|
| Seção "Últimos Lançamentos" com modo edição (data_editor + UPDATE) | Dashboard | Alto |
| Regra de caixa: excluir `payment_type='Cartão de crédito'` do fluxo | Dashboard | Alto |
| Histórico inclui Investimentos na 3ª linha | Dashboard | Médio |
| Comparativo Ano a Ano (YOY) | Análises | Alto |
| Gastos com pagamento de cartão (mensal) | Análises | Médio |
| Evolução do patrimônio investido | Análises | Médio |
| Filtro por Dia no formulário de busca | Tabelas | Médio |
| Total filtrado + Total no ano + Total no mês | Tabelas | Médio |
| `expand_installments()` com due_day sidebar | Cartão | Alto |
| KPIs corretos (fatura/dívida do ano/pago no ano) | Cartão | Alto |
| Bar chart categorias (laranja) | Cartão | Médio |
| Histórico anual por vencimento (line chart) | Cartão | Médio |
| Seção "Dívidas no cartão" ativas/concluídas + edição | Cartão | Alto |
| Formulário sidebar: categorias preset por tipo | Sidebar | Médio |
| Formulário sidebar: valor como text input (BRL parse) | Sidebar | Baixo |
| payment_type condicional (só para saída) | Sidebar | Médio |

### Prioridade Baixa (melhorias do unificado vs. original)

| Item | Observação |
|------|-----------|
| Altair → Plotly | O unificado usa Plotly; manter padrão do app unificado |
| Barra de progresso de orçamento | Adição do unificado — preservar |
| CSS cards com cores distintas | Melhoria visual do unificado — preservar |

---

*Gerado em: 2026-05-14 | Auditoria fonte: Controle_Financeiro-main 2.1 | Dashboard Financeiro Unificado — Fase 5.1*
