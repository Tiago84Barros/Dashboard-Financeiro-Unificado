# Dashboard Financeiro Unificado

Plataforma financeira em **Python 3.9 + Streamlit** para controle financeiro, gestão de investimentos, análise de empresas e indicadores macroeconômicos, com suporte a IA via OpenAI.

## Objetivo

Centralizar em uma única plataforma local as funcionalidades hoje distribuídas em três projetos originais:

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
# Copie o template de variáveis de ambiente
copy .env.example .env        # Windows
# cp .env.example .env        # Linux / macOS
```

Edite `.env` e preencha pelo menos `DATABASE_URL` com a sua string de conexão PostgreSQL:

```env
DATABASE_URL="postgresql://usuario:senha@host:5432/database"
```

> O app funciona **sem banco configurado** — exibe avisos na sidebar e opera em modo mock (`MOCK_MODE=true`).

---

## Executar localmente

```bash
streamlit run app.py
```

Acesse em: [http://localhost:8501](http://localhost:8501)

> **Windows — caracteres especiais:** Se o terminal exibir `?` no lugar de acentos, defina antes de rodar:
> ```bash
> set PYTHONIOENCODING=utf-8
> streamlit run app.py
> ```

---

## Verificação de integridade

```bash
# Checar sintaxe de todos os módulos
python -m py_compile app.py
python -m py_compile core/config.py core/database.py
python -m py_compile pages/dashboard_geral.py pages/configuracoes.py
# ... demais arquivos em pages/
```

---

## Estrutura do projeto

```
Dashboard-Financeiro-Unificado/
├── app.py                  ← ponto de entrada, roteamento via sidebar
├── requirements.txt        ← dependências com versões fixadas
├── .env.example            ← template de variáveis de ambiente
│
├── core/                   ← lógica de infraestrutura
│   ├── config.py           ← carrega .env, expõe Settings
│   └── database.py         ← engine SQLAlchemy singleton
│
├── pages/                  ← módulos de cada tela
│   ├── dashboard_geral.py
│   ├── controle_financeiro.py
│   ├── investimentos.py
│   ├── carteira.py
│   ├── proventos.py
│   ├── empresas_b3.py
│   ├── empresas_eua.py
│   ├── macro.py
│   └── configuracoes.py    ← único módulo funcional: exibe status do banco
│
├── design/                 ← tema visual e componentes (Fase 2)
├── etl/                    ← importação de dados (Fase 6)
└── docs/                   ← documentação técnica gerada
```

---

## Telas disponíveis

| Tela | Status |
|------|--------|
| Dashboard Geral | Stub — aguarda Fase 3 |
| Controle Financeiro | Stub — aguarda Fase 6 |
| Investimentos | Stub — aguarda Fase 5 |
| Carteira | Stub — aguarda Fase 5 |
| Proventos | Stub — aguarda Fase 5 |
| Empresas B3 | Stub — aguarda Fase 9 |
| Empresas EUA | Stub — aguarda Fase 9 |
| Cenário Macroeconômico | Stub — aguarda Fase 9 |
| **Configurações** | **Funcional** — exibe status do banco |

---

## Stack técnica

| Componente | Versão |
|-----------|--------|
| Python | 3.9+ |
| Streamlit | ≥1.39.0, <2.0 |
| Pandas | ≥2.2.0, <3.0 |
| Plotly | ≥5.24.0, <6.0 |
| SQLAlchemy | ≥2.0.0, <3.0 |
| psycopg2-binary | ≥2.9.0, <3.0 |
| python-dotenv | ≥1.0.0, <2.0 |
| OpenAI | ≥1.63.0, <2.0 |
| yfinance | ≥0.2.26, <0.3 |
| requests | ≥2.32.0, <3.0 |

---

## Status

**Fase 1 concluída** (2026-05-13): fundação implementada — roteamento real, infraestrutura (`core/config.py`, `core/database.py`), todos os 9 módulos com stubs funcionais, versões fixadas.

Próximo passo: **Fase 2 — Estrutura Visual** (tema dark, componentes reutilizáveis, formatadores).

> Planejamento completo: [`docs/plano_fases_implementacao.md`](docs/plano_fases_implementacao.md)
