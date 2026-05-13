# Status — Fase 3: Mock Dashboard Geral

> Gerado em: 2026-05-13
> Fase: 3 — Dados Mockados + Dashboard Estruturado
> Status: ✅ Concluída

---

## Objetivo

Construir o Dashboard Geral funcional com dados 100% mockados (sem Supabase, sem `.env` alterado),
estabelecendo o schema de dados que será substituído por queries SQL na Fase 4.

---

## Arquivos Criados

### `core/mock_data.py`
Dados estáticos com o mesmo schema das futuras queries reais.

| Constante            | Tipo   | Descrição                                      |
|----------------------|--------|------------------------------------------------|
| `MES_REFERENCIA`     | str    | Período de referência ("Maio 2026")            |
| `PATRIMONIO`         | dict   | Total, investido, saldo bancário, delta, score |
| `FLUXO_MES`          | dict   | Receitas, despesas, economia, poupança, alertas|
| `HISTORICO_MENSAL`   | list   | Últimos 6 meses (Dez → Mai)                   |
| `CATEGORIAS_DESPESA` | list   | 7 categorias com gasto, orçamento e % uso      |
| `PORTFOLIO`          | dict   | Rentabilidade mensal/anual, dividendos, ativos |
| `CLASSES_ATIVO`      | list   | 5 classes: ETF, Ações BR, Renda Fixa, FII, Cripto |
| `ALERTAS_DASHBOARD`  | list   | 4 alertas (1 sucesso, 2 alerta, 1 info)        |
| `PROXIMOS_PASSOS`    | list   | 4 ações (2 alta, 1 média, 1 baixa urgência)    |

### `core/financeiro.py`
Camada de serviço entre as páginas e a fonte de dados.

```python
@st.cache_data(ttl=300)
def get_visao_geral() -> dict   # entry point para o dashboard
def _visao_geral_mock() -> dict # lê core/mock_data.py
def _visao_geral_real() -> dict # placeholder Fase 4 (raise NotImplementedError)
def calcular_saude_score(...)   # helper de cálculo puro (testável)
```

**Decisão técnica:** `@st.cache_data(ttl=300)` aplicado no entry point público, não nas
funções internas. Cache de 5 minutos é adequado para dados financeiros; em Fase 4 será
ajustável via settings.

---

## Arquivos Modificados

### `design/componentes.py` — 3 novos componentes

| Componente           | Assinatura resumida                                          | Uso                          |
|----------------------|--------------------------------------------------------------|------------------------------|
| `card_alerta_resumo` | `(tipo, icone, titulo, descricao, modulo="")`                | Seção 5 — Alertas            |
| `card_proximo_passo` | `(numero, titulo, descricao, urgencia="media", modulo="")`   | Seção 6 — Próximos Passos    |
| `score_saude`        | `(score: int, label="Saúde Financeira")`                     | Seção 3 — Resumo do Mês      |

Classificação do `score_saude`:

| Faixa   | Cor       | Rótulo  |
|---------|-----------|---------|
| 80–100  | `#00C896` | Ótimo   |
| 60–79   | `#4A9EFF` | Bom     |
| 40–59   | `#F6C90E` | Atenção |
| 0–39    | `#FC5C7D` | Crítico |

### `pages/dashboard_geral.py` — Reescrito completo

6 seções renderizadas a partir de `get_visao_geral()`:

| Seção | Conteúdo                                                         | Componentes                           |
|-------|------------------------------------------------------------------|---------------------------------------|
| 1     | KPIs linha 1: patrimônio total, saldo, receitas, despesas        | `card_metrica` × 4                    |
| 2     | KPIs linha 2: pat. investido, rentab. mês, economia, poupança    | `card_metrica` × 4                    |
| 3     | Fluxo de caixa (gráfico) + resumo do mês                         | `_fig_fluxo`, `score_saude`, `st.metric` |
| 4     | Investimentos (donut) + orçamento por categoria                  | `_fig_classes`, `barra_progresso`, `indicador_linha` |
| 5     | Alertas principais                                               | `card_alerta_resumo` × 4              |
| 6     | Próximos passos financeiros                                      | `card_proximo_passo` × 4              |

**Gráficos implementados:**
- `_fig_fluxo(historico)` — barras agrupadas (receitas vs despesas), 6 meses, cores `#00C896`/`#FC5C7D`
- `_fig_classes(classes)` — donut (hole=0.55) com distribuição das 5 classes de ativos

---

## Dados Mockados — Valores de Referência (Maio 2026)

| Indicador             | Valor          |
|-----------------------|----------------|
| Patrimônio Total      | R$ 87.450,00   |
| Patrimônio Investido  | R$ 75.150,00   |
| Saldo Bancário        | R$ 12.300,00   |
| Receitas do Mês       | R$ 8.500,00    |
| Despesas do Mês       | R$ 4.200,00    |
| Economia              | R$ 4.300,00    |
| Taxa de Poupança      | 50,6%          |
| Rentabilidade Mês     | 3,2%           |
| Rentabilidade Ano     | 12,4%          |
| Dividendos do Mês     | R$ 420,00      |
| Score de Saúde        | 78/100 (Bom)   |
| Meses de Reserva      | 2,9×           |

---

## Decisões Técnicas

1. **Schema first**: campos de `mock_data.py` nomeados para espelhar as colunas/aliases que
   as queries SQL retornarão. Migração na Fase 4 será substituição cirúrgica de `_visao_geral_mock()`
   por `_visao_geral_real()` sem alterar o contrato do dict.

2. **Cache no entry point público**: `@st.cache_data(ttl=300)` apenas em `get_visao_geral()`.
   As funções internas `_mock()` e `_real()` não têm decorator — evita dupla cachagem.

3. **`NotImplementedError` em `_visao_geral_real()`**: página captura com `try/except` e exibe
   `st.error()` em vez de travar o app. Garante que `MOCK_MODE=false` não quebra a UI.

4. **`calcular_saude_score()` desacoplado**: helper puro, sem dependência de Streamlit ou
   de mock_data. Pode ser unit-testado isoladamente na Fase 8+.

5. **`barra_progresso` sem badge externo**: variável `tipo_badge` removida após ruff F841 —
   a cor da barra já comunica o status via `#00C896`/`#F6C90E`/`#FC5C7D` via CSS existente.

---

## Limitações Conhecidas

- Dados são 100% estáticos — não refletem transações reais (Fase 4).
- `meses_reserva = 2.9×` está hardcoded; cálculo real depende do saldo bancário ÷ média de despesas.
- Score de saúde (78) está hardcoded em `PATRIMONIO`; na Fase 4 será calculado por `calcular_saude_score()`.
- Gráfico de fluxo mostra apenas receitas e despesas; linha de patrimônio acumulado aguarda Fase 4.

---

## Comandos Executados

### Lint — `ruff check .`
```
1ª execução:  pages/dashboard_geral.py:295:13: F841 Local variable `pct` is assigned to but never used
Correção:     Removida variável `pct` e `tipo_badge` desnecessárias no loop de categorias
2ª execução:  All checks passed!
```

### Startup test — `streamlit run app.py --server.headless true`
```
Local URL:    http://localhost:8502
Network URL:  http://192.168.9.171:8502
HTTP status:  200 OK
```

---

## Critérios de Aceitação

| Critério                                             | Status |
|------------------------------------------------------|:------:|
| `core/mock_data.py` com schema completo              | ✅     |
| `core/financeiro.py` com cache + abstração mock/real | ✅     |
| `get_visao_geral()` retorna dict com 9 chaves        | ✅     |
| Dashboard com 8 KPIs em 2 linhas                     | ✅     |
| Gráfico de fluxo de caixa (6 meses)                  | ✅     |
| Donut chart de classes de ativos                     | ✅     |
| Barras de orçamento por categoria                    | ✅     |
| Alertas e próximos passos renderizados               | ✅     |
| Score de saúde financeira exibido                    | ✅     |
| `ruff check .` — zero erros                          | ✅     |
| App inicia sem erro (`streamlit --headless`)         | ✅     |

---

## Próximos Passos (Fase 4)

- Configurar `DATABASE_URL` no `.env` (decisão D01: Supabase vs. PostgreSQL local)
- Implementar `_visao_geral_real()` com queries SQLAlchemy
- Validar que o schema SQL retorna exatamente as mesmas chaves do mock
- Remover badge "Modo mock" do cabeçalho do dashboard
