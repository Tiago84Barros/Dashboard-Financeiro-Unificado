# Dashboard Financeiro Unificado

Plataforma financeira local em **Python + Streamlit** que unifica controle financeiro, gestão de investimentos, análise de empresas e indicadores macroeconômicos. Versão atual: **v0.5.10 — Fase 5 concluída**.

## Objetivo

Centralizar em uma única plataforma local as funcionalidades distribuídas em três projetos originais:

- [`Tiago84Barros/Dashboard`](https://github.com/Tiago84Barros/Dashboard)
- [`Tiago84Barros/Controle_Financeiro`](https://github.com/Tiago84Barros/Controle_Financeiro)
- [`Tiago84Barros/Dashboard-Investimentos`](https://github.com/Tiago84Barros/Dashboard-Investimentos)

---

## Pré-requisitos

- Python 3.9+
- pip

---

## Instalação

```bash
# 1. Clone o repositório
git clone https://github.com/Tiago84Barros/Dashboard-Financeiro-Unificado.git
cd Dashboard-Financeiro-Unificado

# 2. (Opcional) Crie e ative um ambiente virtual
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux / macOS

# 3. Instale as dependências
pip install -r requirements.txt
```

---

## Configuração

```bash
copy .env.example .env        # Windows
# cp .env.example .env        # Linux / macOS
```

Edite `.env` com a string de conexão do Supabase para ativar dados reais:

```env
# Banco unificado (Supabase)
SUPABASE_UNIFICADO_URL="postgresql://postgres:[senha]@[host]:5432/postgres"
OWNER_USER_ID="uuid-do-usuario"

# Senha de acesso ao app
APP_PASSWORD="sua-senha"

# OpenAI (opcional — módulo IA ainda não implementado)
OPENAI_API_KEY=""

# Modo mock: true = dados de demonstração, false = banco real
MOCK_MODE=true
```

> O app funciona **sem banco configurado** (`MOCK_MODE=true`) — exibe dados de demonstração em todas as telas.

---

## Executar localmente

```bash
streamlit run app.py
```

Acesse em: [http://localhost:8501](http://localhost:8501)

> **Windows — encoding:** Se o terminal exibir `?` no lugar de acentos:
> ```bash
> set PYTHONIOENCODING=utf-8
> streamlit run app.py
> ```

---

## Telas disponíveis

### Implementadas e funcionais

| Tela | Módulo | Dados reais |
|------|--------|:-----------:|
| Dashboard Geral | `views/dashboard_geral.py` | ✅ |
| Controle Financeiro | `views/controle_financeiro.py` | ✅ — 251 transações, 38 categorias |
| Metas | `views/metas.py` | ✅ — CRUD completo, aporte sugerido |
| Alertas | `views/alertas.py` | ✅ — 6 regras automáticas |
| Investimentos | `views/investimentos.py` | ✅ — 1.351 transações, 4 tabs |
| Carteira | `views/carteira.py` | ✅ — 34 posições, donut por classe/setor |
| Proventos | `views/proventos.py` | ✅ — 517 eventos, filtros por ticker/tipo/ano |
| Empresas B3 | `views/empresas_b3.py` | ✅ — 82 ativos com filtros |
| Configurações | `views/configuracoes.py` | ✅ — banco, ETL, cotações yfinance, segurança |

### Implementadas parcialmente

| Tela | Módulo | O que funciona | O que está pendente |
|------|--------|----------------|---------------------|
| Empresas EUA | `views/empresas_eua.py` | Filtra ativos USD do banco; tabela com cotação | P/L, EPS, market cap — Fase 7 |
| Cenário Macroeconômico | `views/macro.py` | Exibe referências SELIC/IPCA/câmbio; benchmarks do banco | Séries históricas via API BCB — Fase 7 |

### Planejadas (ainda não existem)

| Tela | Fase | Descrição |
|------|:----:|-----------|
| Cartão de Crédito | 6 | Faturas, lançamentos parcelados, limite disponível |
| IR Estimado | 6 | Ganho de capital (ações/FII), DARF mensal |

---

## Estrutura do projeto

```
Dashboard-Financeiro-Unificado/
├── app.py                        ← ponto de entrada, roteamento via sidebar
├── requirements.txt              ← dependências com versões fixadas
├── .env.example                  ← template de variáveis de ambiente
│
├── core/                         ← lógica de negócio e infraestrutura
│   ├── config.py                 ← carrega .env, expõe Settings (MOCK_MODE, OWNER_USER_ID, ...)
│   ├── database.py               ← engine SQLAlchemy singleton (@st.cache_resource)
│   ├── auth.py                   ← gate de senha SHA-256
│   ├── utils.py                  ← formatadores (moeda, %, data)
│   ├── financeiro.py             ← KPIs financeiros gerais
│   ├── mock_data.py              ← dados de demonstração (schema completo)
│   ├── investimentos.py          ← carteira, cashflow — LATERAL JOIN cotações
│   ├── proventos.py              ← dividendos, JCP, rendimentos FII
│   ├── controle.py               ← transações, orçamento, categorias
│   ├── metas.py                  ← metas financeiras CRUD
│   ├── alertas.py                ← 6 regras de alertas automáticas
│   └── empresas.py               ← ativos B3 e EUA
│
├── views/                        ← módulo de cada tela (cada um expõe render())
│   ├── dashboard_geral.py
│   ├── controle_financeiro.py
│   ├── metas.py
│   ├── alertas.py
│   ├── investimentos.py
│   ├── carteira.py
│   ├── proventos.py
│   ├── empresas_b3.py
│   ├── empresas_eua.py
│   ├── macro.py
│   └── configuracoes.py
│
├── design/                       ← tema dark e componentes reutilizáveis
│   ├── tema.py
│   └── componentes.py
│
├── etl/                          ← importação de dados (CSV → Supabase)
│   ├── importacao.py
│   └── schema_setup.py
│
├── migration/                    ← scripts de migração dos 3 repos originais
│   ├── 00_config.py ... 08_compute_portfolio_positions.py
│   └── backup/                   ← snapshots JSON do banco
│
├── supabase_unificado/           ← DDL e políticas RLS do banco
│   └── schema/
│       ├── 001_core_tables.sql
│       ├── 002_financial_tables.sql
│       ├── 003_investment_tables.sql
│       ├── 004_import_migration_tables.sql
│       ├── 005_indexes.sql
│       ├── 006_rls_policies.sql
│       ├── 007_views.sql
│       └── 008_seed_reference_data.sql
│
└── docs/                         ← documentação técnica gerada
    ├── plano_fases_implementacao.md
    ├── status_fase_5.md          ← registro completo da Fase 5
    └── status_atual_implementacao.md
```

---

## MOCK_MODE=true vs MOCK_MODE=false

| Aspecto | `MOCK_MODE=true` (padrão) | `MOCK_MODE=false` |
|---------|--------------------------|-------------------|
| Banco necessário | Não | Sim — Supabase configurado |
| Dados exibidos | Dados de demonstração estáticos em `core/mock_data.py` | Dados reais do banco PostgreSQL |
| `SUPABASE_UNIFICADO_URL` | Ignorada | Obrigatória |
| `OWNER_USER_ID` | Ignorado | Obrigatório |
| `APP_PASSWORD` | Opcional | Recomendado |
| Rentabilidade em Carteira | Valores fictícios | Calculada sobre posições reais; 0% se `asset_quotes` vazia |
| Proventos | 24 eventos mockados | Todos os eventos reais do banco |
| Alertas | Baseados em dados mockados | Baseados em dados reais |
| Metas | Mock se `financial_goals` vazia | Dados reais se tabela populada |
| Cotações (yfinance) | Não usadas | Importadas via Configurações → aba Cotações |

> Use `MOCK_MODE=true` para explorar o app sem banco. Use `MOCK_MODE=false` para trabalhar com dados reais.

---

## Variáveis de ambiente obrigatórias para dados reais

| Variável | Obrigatória | Descrição |
|----------|:-----------:|-----------|
| `SUPABASE_UNIFICADO_URL` | ✅ | String de conexão PostgreSQL do Supabase |
| `OWNER_USER_ID` | ✅ | UUID do usuário — usado como filtro em todas as queries |
| `APP_PASSWORD` | Recomendado | Se não definido, o app abre sem senha (modo dev) |
| `MOCK_MODE` | — | `false` para ativar banco real; padrão `true` |
| `OPENAI_API_KEY` | Opcional | Necessário apenas quando o módulo de IA (Fase 8) for implementado |

---

## Ativar dados reais (MOCK_MODE=false)

1. Criar projeto no Supabase (plano free é suficiente)
2. Executar os SQLs em `supabase_unificado/schema/` em ordem (`001` → `009`)
3. Configurar `.env` com `SUPABASE_UNIFICADO_URL`, `OWNER_USER_ID` e `APP_PASSWORD`
4. Definir `MOCK_MODE=false`
5. Executar a migração: `python migration/05_load_to_unified_supabase.py`
6. Importar cotações: Configurações → aba Cotações → Atualizar Cotações

> Checklist detalhado: [`docs/status_atual_implementacao.md`](docs/status_atual_implementacao.md)

---

## Stack técnica

| Componente | Versão |
|-----------|--------|
| Python | 3.9+ |
| Streamlit | >=1.39.0, <2.0 |
| Pandas | >=2.2.0, <3.0 |
| Plotly | >=5.24.0, <6.0 |
| SQLAlchemy | >=2.0.0, <3.0 |
| psycopg2-binary | >=2.9.0, <3.0 |
| python-dotenv | >=1.0.0, <2.0 |
| yfinance | >=0.2.26, <0.3 |
| OpenAI | >=1.63.0, <2.0 |
| requests | >=2.32.0, <3.0 |

---

## Status

**Fase 5 concluída — v0.5.10** (2026-05-14)

11 telas com dados reais ou mock, 6 módulos `core/`, 1.351 transações de investimento + 517 proventos migrados, cotações via yfinance, 6 regras de alertas automáticas.

Próximo passo: **Fase 6 — Schema cartão + IR estimado**.

> Planejamento completo: [`docs/plano_fases_implementacao.md`](docs/plano_fases_implementacao.md)
