# Roadmap MVP — Dashboard Financeiro Unificado

> Gerado em: 2026-05-13
> Baseado em `docs/auditoria_tecnica.md` e `docs/plano_de_melhoria.md`
> Regras aplicadas: CLAUDE_INSTRUCTIONS.md (cofre ProjetoIA)
> Nenhuma implementação foi feita neste documento.

---

## Definição de MVP

O **MVP do Dashboard Financeiro Unificado** é a versão mínima que entrega valor real ao usuário, com dados reais do banco, sem depender de todos os módulos estarem prontos.

**MVP = App rodando localmente com:**
- Menu funcional (todas as rotas respondendo)
- Dashboard Geral com saldo, receitas, despesas e fluxo de caixa reais
- Investimentos com carteira e rentabilidade reais
- Dados reais do banco PostgreSQL
- Sem travar, sem erros visíveis, sem telas em branco

---

## Visão das Fases

```
Fase 0 ──► Fundação estrutural          (sem isso, nada funciona)
Fase 1 ──► Diagnóstico dos repos        (sem isso, não dá para migrar)
Fase 2 ──► MVP Core: Dashboard Geral    ← MVP mínimo começa aqui
Fase 3 ──► MVP Investimentos            ← MVP completo termina aqui
Fase 4 ──► Controle Financeiro
Fase 5 ──► Novos módulos (B3, EUA, Macro)
Fase 6 ──► IA, qualidade e segurança
```

---

## Fase 0 — Fundação Estrutural

> **Objetivo:** Fazer o app funcionar como multi-página com arquitetura correta.
> **Estimativa:** 1–2 horas de trabalho
> **Pré-requisito para:** tudo

### Checklist

- [ ] Fixar versões no `requirements.txt`
- [ ] Criar pasta `core/` com `__init__.py`
- [ ] Criar pasta `etl/` com `__init__.py`
- [ ] Criar pasta `design/` com `__init__.py`
- [ ] Criar `core/config.py` — carregamento de variáveis de ambiente
- [ ] Criar `core/database.py` — engine SQLAlchemy com `@st.cache_resource`
- [ ] Criar `.env` local a partir de `.env.example`
- [ ] Criar stubs em `pages/` para os 5 módulos faltantes:
  - `pages/proventos.py`
  - `pages/empresas_b3.py`
  - `pages/empresas_eua.py`
  - `pages/macro.py`
  - `pages/configuracoes.py`
- [ ] Implementar roteamento real em `app.py` (chamar `render()` de cada página)
- [ ] Testar: `streamlit run app.py` → menu deve navegar entre todas as 9 telas

### Entregável
App sobe sem erros, menu navega para todas as telas (com mensagem de "em construção"), banco conectado.

---

## Fase 1 — Diagnóstico dos Repositórios Originais

> **Objetivo:** Entender o que existe nos 3 repos antes de migrar qualquer coisa.
> **Estimativa:** 2–4 horas por repositório
> **Pré-requisito para:** Fases 2, 3 e 4

### Checklist por repositório

#### `Tiago84Barros/Dashboard`
- [ ] Mapear estrutura de pastas e arquivos
- [ ] Identificar conexão com banco (qual engine, qual ORM, qual string)
- [ ] Mapear tabelas utilizadas e queries principais
- [ ] Identificar lógica de cálculo de saldo, receita e despesa
- [ ] Identificar componentes visuais reutilizáveis
- [ ] Documentar em Obsidian: `04_App_Dashboard_Financeiro_Unificado/`

#### `Tiago84Barros/Dashboard-Investimentos`
- [ ] Mapear estrutura de pastas e arquivos
- [ ] Identificar schema de ativos, operações, proventos, cotações
- [ ] Identificar lógica de custo médio, rentabilidade, TWRR
- [ ] Verificar se tem integração com yfinance, Alpha Vantage ou outra API
- [ ] Documentar em Obsidian: `04_App_Dashboard_Financeiro_Unificado/`

#### `Tiago84Barros/Controle_Financeiro`
- [ ] Mapear estrutura de pastas e arquivos
- [ ] Identificar schema de transações, categorias, orçamentos, metas
- [ ] Verificar se tem importação OFX/CSV
- [ ] Identificar lógica de categorização
- [ ] Documentar em Obsidian: `04_App_Dashboard_Financeiro_Unificado/`

### Decisão obrigatória ao final da Fase 1

> **Banco compartilhado ou banco separado?**

| Critério | Banco compartilhado (Supabase) | Banco local separado |
|---------|-------------------------------|---------------------|
| Dados sempre atualizados | ✅ | ❌ |
| Independência dos apps Next.js | ❌ | ✅ |
| Complexidade de setup | Baixa | Baixa |
| RLS e segurança | Requer cuidado | Sem RLS |
| **Recomendação** | **Preferida** se os apps Next.js estiverem em desenvolvimento ativo | Válida para prototipagem rápida |

### Entregável
Documento de diagnóstico dos 3 repos no Obsidian (`04_App_Dashboard_Financeiro_Unificado/`) com schema de banco mapeado e decisão de banco registrada.

---

## Fase 2 — MVP Core: Dashboard Geral

> **Objetivo:** Primeira tela com dados reais — o coração do app.
> **Estimativa:** 1–2 semanas
> **Módulo:** `pages/dashboard_geral.py` + `core/financeiro.py`
> **Fonte:** `Tiago84Barros/Dashboard`

### Checklist

- [ ] Criar `core/financeiro.py` com funções migradas do Dashboard original:
  - `get_saldo_total()`
  - `get_receitas_mes(mes, ano)`
  - `get_despesas_mes(mes, ano)`
  - `get_fluxo_caixa_12m()`
- [ ] Aplicar `@st.cache_data(ttl=300)` em todas as funções de query
- [ ] Implementar `pages/dashboard_geral.py` com:
  - Cards de saldo total, receitas do mês, despesas do mês
  - Gráfico de barras: receita vs. despesa (12 meses) com Plotly
  - Taxa de poupança do mês atual
- [ ] Testar com dados reais do banco
- [ ] Validar resultados contra o Dashboard original

### KPIs do módulo (conforme Obsidian `09_Relatorios_e_Indicadores/kpis_principais.md`)
- Saldo Total Disponível = Σ saldos de contas ativas
- Taxa de Poupança = (Receita − Despesa) / Receita × 100
- Fluxo de Caixa Líquido = Σ receitas − Σ despesas (período)

### Entregável
Dashboard Geral exibindo dados reais. **Este é o marco do MVP mínimo.**

---

## Fase 3 — MVP Investimentos

> **Objetivo:** Segunda tela com dados reais — carteira e rentabilidade.
> **Estimativa:** 1–2 semanas
> **Módulos:** `pages/investimentos.py`, `pages/carteira.py`, `pages/proventos.py`
> **Fontes:** `Tiago84Barros/Dashboard-Investimentos`

### Checklist

- [ ] Criar `core/cotacoes.py` com integração yfinance:
  - `get_cotacao_atual(ticker: str) -> float`
  - `get_historico(ticker: str, periodo: str) -> pd.DataFrame`
  - Aplicar `@st.cache_data(ttl=900)` (delay de 15min do yfinance)
- [ ] Criar `core/investimentos.py`:
  - `calcular_custo_medio(operacoes: list) -> dict`
  - `calcular_rentabilidade_twrr(operacoes: list, cotacoes: dict) -> float`
  - `get_patrimonio_total() -> float`
- [ ] Implementar `pages/carteira.py`:
  - Tabela de ativos: ticker, qtd, preço médio, preço atual, resultado R$ e %
  - Pie chart: alocação por classe de ativo
- [ ] Implementar `pages/investimentos.py`:
  - Gráfico de evolução do patrimônio (12 meses)
  - Rentabilidade total vs. CDI (via BCB SGS ou yfinance `^BVSP`)
- [ ] Implementar `pages/proventos.py`:
  - Tabela de histórico de proventos (dividendos, JCP, FII)
  - Total recebido no ano
- [ ] Validar cálculos contra Dashboard-Investimentos original

### KPIs do módulo (conforme Obsidian `kpis_principais.md`)
- Patrimônio Total Investido = Σ (qtd × preço_atual)
- Rentabilidade TWRR = [(1+R₁)×(1+R₂)×...] − 1
- Dividend Yield = Σ proventos_12m / custo_total × 100

### Entregável
Carteira, Investimentos e Proventos com dados reais. **Este é o marco do MVP completo.**

---

## Fase 4 — Controle Financeiro

> **Objetivo:** Módulo de receitas, despesas e orçamento.
> **Estimativa:** 1–2 semanas
> **Módulo:** `pages/controle_financeiro.py` + `etl/importacao.py`
> **Fonte:** `Tiago84Barros/Controle_Financeiro`

### Checklist

- [ ] Criar `core/categorias.py` com lógica de categorização
- [ ] Criar `etl/importacao.py` com parser OFX/CSV (se existir no repo original)
- [ ] Implementar `pages/controle_financeiro.py`:
  - Listagem de transações com filtros de data, categoria, conta
  - Barras de orçamento por categoria (valor gasto vs. limite)
  - Progresso de metas financeiras
- [ ] Testar com dados reais

### Entregável
Módulo de controle financeiro operacional.

---

## Fase 5 — Novos Módulos

> **Objetivo:** Módulos que não existem nos repos originais — construção do zero.
> **Estimativa:** 2–3 semanas (todos juntos)

### Módulos a construir

| Módulo | Arquivo | Dados principais | APIs |
|--------|---------|-----------------|------|
| Empresas B3 | `pages/empresas_b3.py` | Indicadores fundamentalistas, P/L, P/VP, DY | yfinance |
| Empresas EUA | `pages/empresas_eua.py` | NYSE/NASDAQ, P/E, EPS, market cap | yfinance |
| Cenário Macroeconômico | `pages/macro.py` | SELIC, IPCA, câmbio, curva de juros | BCB SGS API, yfinance |
| Configurações | `pages/configuracoes.py` | Conexão banco, tema, preferências | — |

---

## Fase 6 — IA, Qualidade e Segurança

> **Objetivo:** Elevar o app de funcional para robusto.
> **Estimativa:** 2–3 semanas

### Checklist

- [ ] **OpenAI:** criar `core/ia.py` com análise de portfólio e diagnóstico financeiro
- [ ] **Design:** criar `.streamlit/config.toml` com tema financeiro (cores, fontes)
- [ ] **Cache:** revisar todas as funções — garantir `@st.cache_data` em todas as queries
- [ ] **Segurança:** avaliar se RLS deve ser implementado (decisão de hospedagem)
- [ ] **Autenticação:** implementar Streamlit Authenticator se app for hospedado
- [ ] **Versões:** revisar `requirements.txt` com versões testadas

---

## Critérios de Conclusão do MVP

| Critério | MVP Mínimo (Fase 2) | MVP Completo (Fase 3) |
|---------|:-------------------:|:---------------------:|
| App roda sem erros | ✅ | ✅ |
| Menu navega em todas as telas | ✅ | ✅ |
| Dados reais no Dashboard Geral | ✅ | ✅ |
| Dados reais de Investimentos | ❌ | ✅ |
| Cotações atualizadas via yfinance | ❌ | ✅ |
| Controle Financeiro com dados reais | ❌ | ❌ (Fase 4) |
| Empresas B3/EUA/Macro | ❌ | ❌ (Fase 5) |
| IA integrada (OpenAI) | ❌ | ❌ (Fase 6) |

---

## Timeline Estimada

```
Semana 1  ──► Fase 0 (fundação) + Fase 1 (diagnóstico dos repos)
Semana 2  ──► Fase 2 (Dashboard Geral — MVP mínimo)
Semana 3  ──► Fase 3 (Investimentos + Carteira + Proventos — MVP completo)
Semana 4  ──► Fase 4 (Controle Financeiro)
Semana 5–6 ─► Fase 5 (Novos módulos: B3, EUA, Macro)
Semana 7–8 ─► Fase 6 (IA, qualidade, segurança)
```

---

## Decisões Pendentes (antes de começar)

| # | Decisão | Impacto | Prazo |
|---|---------|:-------:|-------|
| D01 | Banco compartilhado (Supabase) ou banco PostgreSQL local separado? | 🔴 Alto | Antes da Fase 2 |
| D02 | yfinance (gratuito, delay 15min) ou API paga (Alpha Vantage)? | 🟡 Médio | Antes da Fase 3 |
| D03 | App será hospedado ou apenas local? | 🟠 Alto | Define auth e segurança |
| D04 | App 4 usa o mesmo schema de banco que os apps Next.js (Apps 2 e 3) ou schema próprio? | 🔴 Alto | Antes da Fase 2 |

---

## Relação com o Ecossistema Next.js

O App 4 é **paralelo e independente** dos Apps 1, 2 e 3. Pode avançar em qualquer fase sem bloquear ou ser bloqueado por eles.

```
Apps 1, 2, 3 (Next.js / NestJS)     App 4 (Python / Streamlit)
─────────────────────────────────    ──────────────────────────
Fase 1: App 2 (Investimentos)   ←──(banco opcional)──→ Fase 3: Investimentos
Fase 2: App 3 (Controle)        ←──(banco opcional)──→ Fase 4: Controle Financeiro
Fase 3: App 1 (Agregador)       ←──(banco opcional)──→ Fase 2: Dashboard Geral
```

---

*Ver também: `docs/auditoria_tecnica.md` e `docs/plano_de_melhoria.md`*
*Documento gerado sem alterar nenhum código.*
