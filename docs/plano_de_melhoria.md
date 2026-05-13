# Plano de Melhoria — Dashboard Financeiro Unificado

> Gerado em: 2026-05-13
> Baseado na `docs/auditoria_tecnica.md`.
> Regras aplicadas: CLAUDE_INSTRUCTIONS.md (cofre ProjetoIA)
> Nenhuma implementação foi feita neste documento.

---

## Princípios do Plano

1. **Nada sem diagnóstico** — antes de migrar qualquer código dos repos originais, auditá-lo completamente.
2. **Uma fonte de verdade por entidade** — nenhuma tabela ou lógica duplicada entre módulos.
3. **Migrar por módulo completo** — nunca migrar metade de um módulo.
4. **Não quebrar o que funciona** — os 3 repos originais continuam funcionando até a migração ser validada.
5. **Sem credenciais no código** — toda config via `.env`, nunca hardcoded.

---

## Mapa de Problemas × Melhorias

| Problema (da auditoria) | Melhoria correspondente | Prioridade |
|------------------------|------------------------|:----------:|
| T01 — sem roteamento | M01 — roteamento em `app.py` | 🔴 |
| T02 — sem banco | M02 — `core/database.py` + `.env` | 🔴 |
| T03 — pastas ausentes | M03 — criar `core/`, `etl/`, `design/` | 🔴 |
| T04 — 5 módulos sem stub | M04 — stubs dos módulos faltantes | 🟠 |
| T05 — sem versões fixadas | M05 — fixar versões no `requirements.txt` | 🟠 |
| T06 — DATABASE_URL redundante | M06 — unificar variável de banco | 🟡 |
| T07 — sem `__init__.py` | M03 — incluso na criação das pastas | 🔴 |
| T08 — sem cache | M07 — decorators `@st.cache_data` | 🟡 |
| S01 — RLS bypassado | M08 — avaliar `supabase-py` vs. SQLAlchemy direto | 🟠 |
| U01 — menu sem telas | M04 — stubs dos módulos faltantes | 🟠 |
| U03 — sem tema | M09 — `design/theme` | 🟢 |

---

## Melhorias Detalhadas

---

### M01 — Implementar roteamento em `app.py`

**Problema:** O menu lateral exibe 9 opções mas `app.py` nunca chama as funções `render()`.
**App afetado:** App 4
**Módulo:** `app.py`
**Tabelas Supabase:** nenhuma
**Regra de negócio:** nenhuma
**Risco técnico:** baixo
**Documentação a atualizar:** `docs/arquitetura.md`

**Abordagem recomendada:**
```python
# Opção A — roteamento manual (mais simples, mais controle)
from pages import dashboard_geral, controle_financeiro, investimentos, carteira

PAGE_MAP = {
    'Dashboard Geral':       dashboard_geral.render,
    'Controle Financeiro':   controle_financeiro.render,
    'Investimentos':         investimentos.render,
    'Carteira':              carteira.render,
    # demais módulos após stubs serem criados
}

if menu in PAGE_MAP:
    PAGE_MAP[menu]()
else:
    st.info(f'Módulo "{menu}" em construção.')
```

**Por que não usar multipage Streamlit nativo (`st.navigation`):**
O multipage nativo não permite controle programático do estado atual via `menu` variável. O roteamento manual dá mais flexibilidade para a lógica de auth e permissões futuras.

---

### M02 — Criar `core/database.py` e configurar `.env`

**Problema:** Sem conexão ao banco, nenhuma funcionalidade real é possível.
**App afetado:** App 4
**Módulo:** `core/database.py`
**Tabelas Supabase:** todas
**Regra de negócio:** nenhuma (infraestrutura)
**Risco técnico:** médio — decisão pendente: mesmo banco dos apps Next.js ou banco local?
**Documentação a atualizar:** `docs/arquitetura.md`, Obsidian `05_Banco_de_Dados/`

**Estrutura recomendada para `core/database.py`:**
```python
# Padrão: SQLAlchemy engine singleton com @st.cache_resource
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import streamlit as st

@st.cache_resource
def get_engine():
    from core.config import settings
    return create_engine(settings.DATABASE_URL)

@st.cache_resource
def get_session_factory():
    return sessionmaker(bind=get_engine())
```

**Decisão pendente antes de implementar:**

| Questão | Opção A | Opção B |
|---------|---------|---------|
| Banco | Mesmo PostgreSQL dos apps Next.js (Supabase) | PostgreSQL local separado |
| Vantagem | Dados em sincronia | Independência total |
| Desvantagem | RLS deve ser respeitado | Dados duplicados / desatualizados |
| Recomendação | Banco compartilhado + supabase-py para operações normais | — |

---

### M03 — Criar pastas da arquitetura planejada

**Problema:** `core/`, `etl/`, `design/` não existem. Toda lógica futura ficaria nas pages (errado).
**App afetado:** App 4
**Risco técnico:** baixo

**Estrutura a criar:**
```
core/
├── __init__.py
├── database.py     ← engine e session
├── config.py       ← variáveis de ambiente validadas
├── utils.py        ← formatação de moeda, datas, percentuais
├── financeiro.py   ← lógica do Dashboard Geral (a popular na Fase 2)
├── investimentos.py ← custo médio, rentabilidade (a popular na Fase 3)
├── cotacoes.py     ← integração yfinance (a popular na Fase 3)
└── categorias.py   ← categorização de transações (a popular na Fase 4)

etl/
├── __init__.py
└── importacao.py   ← OFX, CSV, Open Banking (a popular na Fase 4)

design/
├── __init__.py
└── tema.py         ← paleta de cores, estilos CSS injetados via st.markdown
```

---

### M04 — Criar stubs para os 5 módulos faltantes

**Problema:** Menu aponta para Proventos, Empresas B3, Empresas EUA, Cenário Macro e Configurações sem arquivos correspondentes.
**Risco de UX:** usuário clica e nada acontece (ou erro se o roteamento for implementado sem os stubs).

Arquivos a criar:
```
pages/proventos.py
pages/empresas_b3.py
pages/empresas_eua.py
pages/macro.py
pages/configuracoes.py
```

Todos seguindo o padrão atual com mensagem de construção, aguardando migração real.

---

### M05 — Fixar versões no `requirements.txt`

**Problema:** `streamlit` sem versão instalará a última disponível. Uma atualização breaking pode silenciosamente quebrar o app.

**Abordagem:** definir faixas de versão compatíveis após validação local:
```
streamlit>=1.35.0,<2.0.0
pandas>=2.0.0,<3.0.0
plotly>=5.20.0,<6.0.0
sqlalchemy>=2.0.0,<3.0.0
psycopg2-binary>=2.9.0
python-dotenv>=1.0.0
openai>=1.30.0
yfinance>=0.2.40
requests>=2.31.0
```

---

### M06 — Unificar variável de conexão ao banco

**Problema:** `.env.example` tem `DATABASE_URL` e `SUPABASE_DB_URL` — duas strings para o mesmo banco.

**Recomendação:**
- Manter apenas `DATABASE_URL` para conexão SQLAlchemy.
- Se usar `supabase-py`, adicionar `SUPABASE_URL` e `SUPABASE_ANON_KEY` separadamente.
- Remover `SUPABASE_DB_URL` para evitar confusão.

---

### M07 — Implementar caching com `@st.cache_data`

**Problema:** Sem cache, cada rerender do Streamlit vai reconsultar o banco e as APIs externas.

**Onde aplicar:**
```python
# Em core/database.py — para o engine (cache_resource — não serializable)
@st.cache_resource
def get_engine(): ...

# Em funções de query — para resultados de dados (cache_data — serializable)
@st.cache_data(ttl=300)  # 5 minutos
def get_transacoes(usuario_id: int, mes: int, ano: int): ...

@st.cache_data(ttl=900)  # 15 minutos (delay yfinance)
def get_cotacoes(tickers: list[str]): ...
```

---

### M08 — Avaliar uso de `supabase-py` vs. SQLAlchemy direto

**Problema (S01):** Conexão direta ao PostgreSQL do Supabase bypassa as policies de Row Level Security.

**Análise:**

| Abordagem | RLS | Performance | Complexidade |
|-----------|:---:|:-----------:|:------------:|
| SQLAlchemy direto | ❌ bypassado | Alta | Baixa |
| `supabase-py` com anon key | ✅ respeitado | Média | Baixa |
| `supabase-py` com service_role | ❌ bypassado (intencional) | Alta | Baixa |

**Recomendação:**
- App 4 é de uso pessoal/local: SQLAlchemy direto é aceitável se o `.env` não for exposto.
- Se houver plano de hospedar o app, migrar para `supabase-py` com `anon key` + RLS.
- **Nunca usar `service_role_key` no frontend ou em apps sem autenticação.**

---

### M09 — Criar identidade visual (`design/`)

**Quando:** após Fase 2 (Dashboard Geral funcional).

**O que criar:**
- Paleta de cores financeira (verde para positivo, vermelho para negativo, azul para neutro).
- `design/tema.py` com CSS injetado via `st.markdown(unsafe_allow_html=True)`.
- `.streamlit/config.toml` com tema personalizado (primaryColor, backgroundColor, font).
- Componentes reutilizáveis: `card_metrica()`, `tabela_financeira()`, `grafico_linha()`.

---

## Resumo Priorizado

| # | Melhoria | Prioridade | Fase | Depende de |
|---|---------|:----------:|------|-----------|
| M01 | Roteamento em `app.py` | 🔴 Imediato | 0 | M04 (stubs) |
| M02 | `core/database.py` + `.env` | 🔴 Imediato | 0 | M03 (pastas) |
| M03 | Criar `core/`, `etl/`, `design/` | 🔴 Imediato | 0 | — |
| M04 | Stubs dos 5 módulos faltantes | 🟠 Curto | 0 | — |
| M05 | Fixar versões no `requirements.txt` | 🟠 Curto | 0 | — |
| M06 | Unificar variável de banco | 🟡 Médio | 0 | M02 |
| M07 | Cache com `@st.cache_data` | 🟡 Médio | 2–4 | M02 |
| M08 | Avaliar `supabase-py` vs. SQLAlchemy | 🟠 Curto | 0 | Decisão de hospedagem |
| M09 | Tema visual (`design/`) | 🟢 Longo | 6 | Fase 2 concluída |

---

*Ver também: `docs/auditoria_tecnica.md` e `docs/roadmap_mvp_unificado.md`*
*Documento gerado sem alterar nenhum código.*
