# Fase 4.9 — Conexão do App ao Banco Real

> Data: 2026-05-14  
> Validação em produção: **2026-05-14**  
> Status: **✅ Concluído e validado em produção**

---

## Objetivo

Substituir progressivamente os dados mockados da Visão Geral por dados reais das
views do Supabase/PostgreSQL unificado, mantendo fallback automático para mock em
caso de erro.

---

## Escopo

- **Página conectada:** `pages/dashboard_geral.py` (Visão Geral / Dashboard Geral)
- **Outras páginas:** permanecem em mock — não alteradas
- **Schema:** não alterado
- **Dados:** não migrados, não apagados

---

## Arquivos Modificados

| Arquivo | Tipo | Resumo da Mudança |
|---------|:----:|-------------------|
| `core/financeiro.py` | **MODIFICADO** | `_visao_geral_real()` implementada com 6 views; fallback no `get_visao_geral()`; `data_source` injetado |
| `pages/dashboard_geral.py` | **MODIFICADO** | Badge dinâmico de fonte de dados (real / mock / fallback) |

---

## Arquitetura da Solução

### Fluxo de dados

```
settings.MOCK_MODE
    │
    ├─ True  ──────────────────────────────────► _visao_geral_mock()
    │                                               data_source = "mock"
    │
    └─ False ──► _visao_geral_real()
                      │
                      ├─ OK    ────────────────► dict com dados reais
                      │                           data_source = "real"
                      │
                      └─ Exception ───────────► _visao_geral_mock()
                           └─ logger.warning      data_source = "mock_fallback"
```

### Indicador de fonte no Dashboard Geral

| `data_source` | Badge | Tipo | Significado |
|---------------|-------|:----:|-------------|
| `"real"` | ✅ Dados reais | sucesso (verde) | Banco conectado e respondendo |
| `"mock"` | ⚠️ Modo mock | alerta (amarelo) | `MOCK_MODE=true` intencional |
| `"mock_fallback"` | ❌ Fallback (mock) | erro (vermelho) | Banco falhou → dados mock |

---

## Views Conectadas

| View | Dados mapeados |
|------|----------------|
| `v_net_worth` | `patrimonio.total`, `.investido`, `.saldo_bancario` |
| `v_monthly_cashflow` | `fluxo_mes.{receitas,despesas,economia,taxa_poupanca_pct}`, `historico_mensal` |
| `v_category_spending_mtd` | `categorias_despesa`, `maior_categoria`, `meses_reserva` |
| `v_budget_usage_mtd` | `categorias_despesa.orcamento` / `usage_pct` quando configurado |
| `v_investment_summary` | `classes_ativo`, `portfolio.num_ativos` |
| `dividends` | `portfolio.{dividendos_mes,dividendos_ano}` |

---

## Schema do Dict Retornado

Idêntico ao mock — 100% compatível. Campos adicionados:

```python
{
    "data_source": "real" | "mock" | "mock_fallback",
    # ... todos os campos existentes do mock permanecem
    "mes_referencia":     str,
    "patrimonio":         {total, investido, saldo_bancario, delta_mes_pct, saude_score, ...},
    "fluxo_mes":          {receitas, despesas, economia, taxa_poupanca_pct, meses_reserva, ...},
    "historico_mensal":   [{"mes", "receitas", "despesas", "economia", "patrimonio"}, ...],
    "categorias_despesa": [{"nome", "gasto", "orcamento", "pct_usado"}, ...],
    "portfolio":          {rentabilidade_mes_pct, dividendos_mes, num_ativos, ...},
    "classes_ativo":      [{"nome", "valor", "pct_carteira", "rentab_mes_pct", "cor"}, ...],
    "alertas":            [{"tipo", "icone", "titulo", "descricao", "acao", "modulo"}, ...],
    "proximos_passos":    [{"numero", "urgencia", "titulo", "descricao", "modulo"}, ...],
}
```

---

## Valores Reais — Mai 2026

| Indicador | Valor Real | Mock (anterior) |
|-----------|:----------:|:---------------:|
| Patrimônio total | **R$ 405.073,96** | R$ 87.450,00 |
| Saldo bancário | **R$ 211.516,11** | R$ 12.300,00 |
| Patrimônio investido | **R$ 193.557,85** | R$ 75.150,00 |
| Receitas do mês | **R$ 27.672,42** | R$ 8.500,00 |
| Despesas do mês | **R$ 13.925,40** | R$ 4.200,00 |
| Taxa de poupança | **49,7%** | 50,6% |
| Meses de reserva | **15,2×** | 2,9× |
| Score de saúde | **70/100** | 78/100 |
| Ativos na carteira | **34** | 12 |

---

## Lógica de Cálculos Aplicados

### Saúde score (0–100)
```
40 pts × min(taxa_poupanca/30, 1.0)   → 40 pts (49.7% > 30%)
30 pts × min(meses_reserva/6, 1.0)   → 30 pts (15.2× > 6×)
20 pts × cats_no_limite/total_cats    → 0 pts  (sem orçamentos)
10 pts × rentabilidade_positiva       → 0 pts  (sem cotações)
Total: 70
```

### Delta patrimônio mês (aproximação)
```
base_prev = net_worth - net_cashflow_mes
delta_pct = net_cashflow_mes / base_prev × 100
          = 13.747 / 391.327 × 100 = 3,5%
```

### Orçamento implícito (sem budgets cadastrados)
```
orcamento = gasto × 1,2   → pct_usado = 83,3% (visual limpo)
```

---

## Regras de Segurança Aplicadas

| Regra | Implementação |
|-------|---------------|
| Sem credenciais no código | `settings.db_url` lido de `.env`/Streamlit Secrets |
| Sem SQL com dados sensíveis | Apenas `SELECT` em views; `WHERE user_id = :uid` paramétrico |
| Sem DDL/DML | Apenas `SELECT` nas 6 views |
| Fallback automático | `except Exception → mock` com `logger.warning` |
| user_id sempre presente | `OWNER_USER_ID` verificado antes de qualquer query |
| Sem impressão de connection string | `get_engine()` usa `@st.cache_resource`; URL nunca logada |

---

## Limitações Conhecidas

| Limitação | Causa | Solução |
|-----------|-------|---------|
| `rentabilidade = 0%` | `asset_quotes` vazia | Fase 5: alimentar cotações (B3/Yahoo Finance) |
| `dividendos_ano = R$ 0` | 517 registros com `ex_date` antes de 2026 | Revisar `ex_date` dos dividendos migrados |
| `orcamento` gerado artificialmente | Nenhum `budget` cadastrado | Cadastrar via UI ou script |
| `historico_mensal.patrimonio` = 0 (exceto mês atual) | Sem snapshots históricos de patrimônio | Cron de snapshot mensal (futura melhoria) |

---

## Validação em Produção — 2026-05-14

**Ambiente:** Streamlit Cloud (app publicado)  
**URL:** `https://dashboard-financeiro-unificado-btwf9tchiycm7fbof7sqvj.streamlit.app`  
**Configuração:** `MOCK_MODE=false` + `DATABASE_URL` + `OWNER_USER_ID` em Streamlit Secrets

### Resultado visual confirmado

| Elemento | Resultado |
|----------|-----------|
| Badge | **✅ Dados reais** (verde) |
| Período | **Mai 2026** |
| Score | **70/100** |

### KPIs validados na tela

| KPI | Valor exibido |
|-----|:-------------:|
| Patrimônio Total | **R$ 405.073,96** |
| Saldo Disponível | **R$ 211.516,11** |
| Receitas do Mês | **R$ 27.672,42** |
| Despesas do Mês | **R$ 13.925,40** |
| Patrimônio Investido | **R$ 193.557,85** |
| Rentabilidade Mês | **0,00%** *(cotações ausentes — esperado)* |
| Economia do Mês | **R$ 13.747,02** |
| Taxa de Poupança | **49,70%** |

### Fallback mock — preservado ✅

O mecanismo de fallback permanece intacto:
- `MOCK_MODE=true` → badge amarelo "Modo mock" — sem acesso ao banco
- `MOCK_MODE=false` + sem DB URL → badge vermelho "Fallback (mock)" — sem crash
- `MOCK_MODE=false` + DB configurado → badge verde "Dados reais" — confirmado acima

### Situação das páginas após Fase 4.9

| Página | Estado | Próxima fase |
|--------|:------:|-------------|
| Dashboard Geral | ✅ **Dados reais** | — |
| Controle Financeiro | 🟡 Em construção | Fase 5.x |
| Carteira | 🟡 Em construção | **Fase 5.1** |
| Proventos | 🟡 Em construção | Fase 5.2 |
| Investimentos | 🟡 Em construção | Fase 5.x |
| Metas, Alertas, demais | 🟡 Em construção | Fase 5.x |

> **Nota — Página Carteira:** exibe atualmente dados placeholder.
> Será conectada ao banco real (`portfolio_positions`) na **Fase 5.1**.

---

## Testes Realizados

### 1. MOCK_MODE=true (comportamento preservado)
```
settings.MOCK_MODE = True (default quando MOCK_MODE não definido no .env)
_visao_geral_mock() → data_source = "mock"
Badge: ⚠️ Modo mock (amarelo)
```
✅ App continua abrindo normalmente com mock

### 2. MOCK_MODE=false sem credenciais
```
settings.db_url = "" (engine = None)
_visao_geral_real() → RuntimeError: "Engine indisponível"
get_visao_geral()   → logger.warning + fallback mock
data_source = "mock_fallback"
Badge: ❌ Fallback (mock) (vermelho)
```
✅ App não crasha — usa mock com indicador vermelho

### 3. MOCK_MODE=false com credenciais (SUPABASE_DB_URL configurado)
```
engine = create_engine(SUPABASE_DB_URL)
v_net_worth:             ✅ (1 linha)
v_monthly_cashflow:      ✅ (8 meses)
v_category_spending_mtd: ✅ (9 categorias)
v_investment_summary:    ✅ (2 classes)
v_budget_usage_mtd:      ✅ (0 linhas — sem budgets)
dividends query:         ✅ (0 no mês atual)

data_source = "real"
Badge: ✅ Dados reais (verde)
```
✅ Dados reais carregados — schema 100% compatível com mock

---

## Como Ativar no Streamlit Cloud

Adicionar nas **Settings > Secrets** do app:

```toml
# Modo real
MOCK_MODE = "false"
OWNER_USER_ID = "<uuid-36-chars>"

# URL do banco unificado (uma das três, em ordem de prioridade)
SUPABASE_UNIFICADO_URL = "postgresql://postgres.<project>:<senha>@<host>:6543/postgres"
# ou: DATABASE_URL = "..."
# ou: SUPABASE_DB_URL = "..."
```

> ⚠️ **Nunca** colocar a connection string no código-fonte, no git ou nos logs.
> A URL contém senha — tratar como segredo.

---

## Próximas Fases

| Fase | Objetivo | Prioridade |
|------|---------|:----------:|
| **4.9.1** | Revisar `ex_date` dos dividendos migrados → `dividendos_ano` real | Média |
| **5.0** | Alimentar `asset_quotes` com cotações reais (B3/Yahoo Finance) → rentabilidade real | Alta |
| **5.1** | Conectar página **Carteira** com dados reais de `portfolio_positions` | Alta |
| **5.2** | Conectar página **Proventos** com `dividends` reais | Média |
| **5.x** | Conectar demais páginas (Controle Financeiro, Metas, Alertas, Investimentos) | Baixa |

---

*Gerado em: 2026-05-14 | Validado em produção: 2026-05-14 | Dashboard Financeiro Unificado — Fase 4.9*
