# Auditoria Técnica — Dashboard Financeiro Unificado

> Gerado em: 2026-05-13
> Baseado em leitura completa do repositório. Nenhuma alteração de código foi feita.
> Regras aplicadas: CLAUDE_INSTRUCTIONS.md (cofre ProjetoIA)

---

## 1. Stack Utilizada

| Camada | Tecnologia | Versão |
|--------|-----------|--------|
| Interface | Streamlit | não fixada |
| Visualização | Plotly | não fixada |
| Dados tabulares | Pandas | não fixada |
| ORM / banco | SQLAlchemy | não fixada |
| Driver PostgreSQL | psycopg2-binary | não fixada |
| Config / env | python-dotenv | não fixada |
| IA | OpenAI API | não fixada |
| Cotações | yfinance | não fixada |
| HTTP / APIs externas | requests | não fixada |

**Observação crítica:** Nenhuma dependência tem versão fixada no `requirements.txt`. Isso representa risco de incompatibilidade silenciosa em novas instalações.

---

## 2. Estrutura de Pastas (estado atual)

```
Dashboard-Financeiro-Unificado/
├── app.py                        ← ponto de entrada Streamlit
├── requirements.txt              ← 9 dependências sem versão
├── CLAUDE.md                     ← instruções para Claude Code
├── README.md                     ← documentação básica
├── .env.example                  ← modelo de variáveis de ambiente
├── .gitignore                    ← Python padrão + .env protegido
├── pages/
│   ├── dashboard_geral.py        ← stub (6 linhas)
│   ├── controle_financeiro.py    ← stub (6 linhas)
│   ├── investimentos.py          ← stub (6 linhas)
│   └── carteira.py               ← stub (6 linhas)
└── docs/
    └── arquitetura.md            ← plano de estrutura

PLANEJADAS MAS NÃO CRIADAS:
├── core/      ← lógica de negócio
├── etl/       ← importação e tratamento de dados
└── design/    ← identidade visual e componentes
```

---

## 3. Principais Arquivos do Projeto

| Arquivo | Linhas | Função real | Estado |
|---------|:------:|------------|--------|
| `app.py` | 25 | Ponto de entrada, menu de 9 itens | Parcial — sem roteamento |
| `requirements.txt` | 9 | Dependências | Funcional — sem versões fixadas |
| `CLAUDE.md` | 41 | Regras para o assistente | Completo |
| `README.md` | 23 | Documentação básica | Completo |
| `.env.example` | 13 | Modelo de variáveis | Completo |
| `.gitignore` | 218 | Proteção de arquivos sensíveis | Completo (padrão Python + Streamlit) |
| `pages/dashboard_geral.py` | 6 | Stub do Dashboard Geral | Placeholder |
| `pages/controle_financeiro.py` | 6 | Stub do Controle Financeiro | Placeholder |
| `pages/investimentos.py` | 6 | Stub do módulo Investimentos | Placeholder |
| `pages/carteira.py` | 6 | Stub da Carteira | Placeholder |
| `docs/arquitetura.md` | ~30 | Planejamento de estrutura | Documentação |

---

## 4. Funcionalidades Já Implementadas

| # | Funcionalidade | Onde | Observação |
|---|---------------|------|-----------|
| 1 | Configuração de página (layout wide, título) | `app.py` linha 3 | Funcional |
| 2 | Menu lateral com 9 seções via `st.sidebar.radio` | `app.py` linhas 8–21 | Visual apenas — não roteia |
| 3 | Exibição do nome da seção selecionada | `app.py` linha 23 | Funcional |
| 4 | Mensagem de status da estrutura | `app.py` linha 24 | Informativo |
| 5 | Função `render()` por módulo (4 páginas) | `pages/*.py` | Existe mas nunca é chamada |

**Resumo:** O app sobe, exibe título e menu. Nenhuma funcionalidade financeira real existe.

---

## 5. Funcionalidades Incompletas

| Módulo | Status | O que falta |
|--------|--------|-------------|
| Dashboard Geral | Stub | Toda a lógica — visão consolidada, cards, gráficos, saldo, fluxo de caixa |
| Controle Financeiro | Stub | Receitas, despesas, categorias, orçamento, metas, dívidas |
| Investimentos | Stub | Patrimônio, rentabilidade, benchmark, ativos, operações |
| Carteira | Stub | Ativos consolidados, alocação, custo médio, resultado |
| Proventos | Não existe | Dividendos, JCP, FII — nem stub criado |
| Empresas B3 | Não existe | Análise fundamentalista B3 — nem stub criado |
| Empresas EUA | Não existe | Análise NYSE/NASDAQ — nem stub criado |
| Cenário Macroeconômico | Não existe | SELIC, IPCA, câmbio, juros — nem stub criado |
| Configurações | Não existe | Conexão ao banco, preferências — nem stub criado |
| Roteamento (`app.py`) | Parcial | Menu não conecta às funções `render()` das pages |
| Conexão ao banco | Não existe | Sem `.env`, sem engine SQLAlchemy, sem schema |
| Integração yfinance | Não existe | Dependência declarada mas não utilizada |
| Integração OpenAI | Não existe | Dependência declarada mas não utilizada |
| Visualizações Plotly | Não existe | Dependência declarada mas nenhum gráfico implementado |

---

## 6. Telas Existentes

Existem **4 arquivos de tela** na pasta `pages/`, todos com estrutura idêntica:

```python
import streamlit as st

def render():
    st.title('Nome do Módulo')
    st.info('Módulo em construção...')
```

Nenhuma tela exibe dados reais. Nenhuma tela é invocada pelo `app.py` atual.

---

## 7. Componentes Existentes

| Componente Streamlit | Onde | Estado |
|---------------------|------|--------|
| `st.set_page_config` | `app.py` | Em uso |
| `st.title` | `app.py` + pages | Em uso |
| `st.sidebar.radio` | `app.py` | Em uso (visual) |
| `st.subheader` | `app.py` | Em uso |
| `st.info` | `app.py` + pages | Em uso (placeholder) |
| `st.markdown` | `app.py` | Em uso (texto estático) |

**Componentes planejados mas não implementados:** `st.columns`, `st.metric`, `st.dataframe`, `st.plotly_chart`, `st.form`, `st.cache_data`, `st.cache_resource`.

---

## 8. Integrações com Supabase

**Estado atual: nenhuma integração implementada.**

O `.env.example` declara duas variáveis de banco:
- `DATABASE_URL` — string de conexão PostgreSQL genérica
- `SUPABASE_DB_URL` — string de conexão direta ao banco do Supabase

A presença de `SUPABASE_DB_URL` indica intenção de usar o banco do Supabase via SQLAlchemy direto (sem Supabase client SDK). Isso é funcional, mas levanta um risco de segurança importante (ver seção 13).

---

## 9. Tabelas Supabase Aparentes

Nenhuma tabela está criada, referenciada ou documentada no repositório atual.

Com base no `CLAUDE.md` e nas páginas planejadas, as tabelas esperadas são:

| Tabela provável | Módulo relacionado | Origem |
|----------------|-------------------|--------|
| `transacoes` | Controle Financeiro | `Tiago84Barros/Controle_Financeiro` |
| `categorias` | Controle Financeiro | `Tiago84Barros/Controle_Financeiro` |
| `orcamentos` | Controle Financeiro | `Tiago84Barros/Controle_Financeiro` |
| `metas` | Controle Financeiro | `Tiago84Barros/Controle_Financeiro` |
| `ativos` | Investimentos / Carteira | `Tiago84Barros/Dashboard-Investimentos` |
| `operacoes` | Investimentos / Carteira | `Tiago84Barros/Dashboard-Investimentos` |
| `proventos` | Proventos | `Tiago84Barros/Dashboard-Investimentos` |
| `cotacoes` | Investimentos / Empresas | `Tiago84Barros/Dashboard-Investimentos` |
| `contas` | Dashboard Geral | `Tiago84Barros/Dashboard` |

**Ação necessária:** auditar os 3 repos originais para mapear o schema real antes de qualquer migração.

---

## 10. Regras de Negócio Aparentes

Nenhuma regra de negócio está implementada no código atual. Com base no planejamento documentado:

| Regra | Módulo | Fonte |
|-------|--------|-------|
| Cálculo de custo médio ponderado de ativos | Carteira / Investimentos | Dashboard-Investimentos |
| Rentabilidade vs. CDI/IBOVESPA (TWRR) | Investimentos | Dashboard-Investimentos |
| Controle de orçamento por categoria | Controle Financeiro | Controle_Financeiro |
| Cálculo de progresso de metas | Controle Financeiro | Controle_Financeiro |
| Categorização de transações | Controle Financeiro | Controle_Financeiro |
| Proventos: dividendos, JCP, FII | Proventos | Dashboard-Investimentos |
| IR estimado em renda variável | Investimentos | A definir |
| Saldo consolidado e fluxo de caixa | Dashboard Geral | Dashboard |

---

## 11. Problemas Técnicos

| ID | Severidade | Problema | Impacto |
|----|:----------:|---------|---------|
| T01 | 🔴 Crítico | `app.py` não roteia para as funções `render()` das pages | App não funciona como multi-página |
| T02 | 🔴 Crítico | Sem arquivo `.env`, sem conexão ao banco | Qualquer funcionalidade de dados é bloqueada |
| T03 | 🔴 Crítico | Pastas `core/`, `etl/`, `design/` não existem | Arquitetura incompleta, sem camada de negócio |
| T04 | 🟠 Alto | 5 de 9 módulos do menu não têm nem stub de arquivo | Menu aponta para telas inexistentes |
| T05 | 🟠 Alto | Nenhuma versão fixada no `requirements.txt` | Risco de quebra silenciosa em novas instalações |
| T06 | 🟡 Médio | `DATABASE_URL` e `SUPABASE_DB_URL` redundantes no `.env.example` | Confusão sobre qual usar; risco de duplicar conexão |
| T07 | 🟡 Médio | Sem `__init__.py` nas pastas (quando criadas) | Imports entre módulos podem falhar |
| T08 | 🟡 Médio | Sem nenhum decorator `@st.cache_data` planejado | Cada rerender vai reconsultar banco e APIs |
| T09 | 🟢 Baixo | Sem testes automatizados | Sem cobertura de lógica de negócio |
| T10 | 🟢 Baixo | Sem linter ou formatter configurado (`ruff`, `black`) | Inconsistência de estilo no código futuro |

---

## 12. Problemas de UX/UI

| ID | Severidade | Problema |
|----|:----------:|---------|
| U01 | 🔴 Crítico | Menu lateral mostra 9 opções mas apenas 4 têm arquivo — clicar nas 5 restantes não faz nada |
| U02 | 🟠 Alto | Sem feedback visual de carregamento (spinner) em nenhuma operação de dados futura |
| U03 | 🟠 Alto | Sem tema/identidade visual definida — pasta `design/` não existe |
| U04 | 🟡 Médio | `st.sidebar.radio` — para 9 itens, considerar `st.sidebar.selectbox` ou navegação por seções colapsáveis |
| U05 | 🟡 Médio | Sem tratamento de estados de erro ou dados vazios nas telas futuras |
| U06 | 🟢 Baixo | Sem favicon personalizado além do padrão Streamlit |

---

## 13. Riscos de Segurança

| ID | Severidade | Risco | Mitigação recomendada |
|----|:----------:|-------|----------------------|
| S01 | 🟠 Alto | `SUPABASE_DB_URL` é conexão direta ao PostgreSQL — **bypassa o Row Level Security (RLS) do Supabase** | Usar o Supabase Python client (`supabase-py`) com chave `anon` para operações normais; reservar conexão direta apenas para admin/migrations |
| S02 | 🟠 Alto | `DATABASE_URL` com credenciais ativas no `.env` local sem rotação definida | Documentar política de rotação de credenciais |
| S03 | 🟡 Médio | `OPENAI_API_KEY` no `.env` — se o app for hospedado, pode vazar via logs ou session state | Nunca exibir variáveis de ambiente na interface; usar `st.secrets` se hospedar no Streamlit Cloud |
| S04 | 🟡 Médio | Sem autenticação no app — qualquer pessoa com acesso à URL vê todos os dados | Implementar Streamlit Authenticator se houver hospedagem, mesmo que básica |
| S05 | 🟢 Baixo | `.streamlit/secrets.toml` está no `.gitignore` ✅ | Risco mitigado para esse arquivo |
| S06 | 🟢 Baixo | `.env` está no `.gitignore` ✅ | Risco mitigado para o arquivo principal |

---

## 14. Código Duplicado

| Duplicata | Arquivos | Observação |
|-----------|---------|-----------|
| Estrutura idêntica de `render()` | `pages/dashboard_geral.py`, `pages/controle_financeiro.py`, `pages/investimentos.py`, `pages/carteira.py` | 4 arquivos com exatamente o mesmo padrão (import, def render, title, info) — aceitável agora pois são stubs, mas cada um deve divergir quando a lógica real for implementada |

**Avaliação:** duplicação atual é mínima e justificada (estágio de estrutura). O risco real de duplicação surge na migração dos 3 repos originais se conexões de banco ou funções de cálculo forem copiadas sem ser centralizadas em `core/`.

---

## 15. Dependências Desnecessárias

Todas as 9 dependências declaradas têm uso previsto documentado. **Nenhuma é desnecessária agora.**

| Biblioteca | Uso previsto | Risco |
|-----------|-------------|-------|
| `streamlit` | Interface | — |
| `pandas` | Dados tabulares | — |
| `plotly` | Gráficos | — |
| `sqlalchemy` | ORM PostgreSQL | — |
| `psycopg2-binary` | Driver PostgreSQL | Usar `psycopg2` (não binary) em produção Linux |
| `python-dotenv` | Variáveis de ambiente | — |
| `openai` | IA | Verificar se a versão instalada é compatível com `gpt-4.1-mini` do `.env.example` |
| `yfinance` | Cotações | Sujeito a mudanças de API do Yahoo Finance |
| `requests` | APIs externas | — |

**Adição recomendada futura:** `supabase` (Python client oficial), `streamlit-authenticator`, `ruff` (dev).

---

## 16. Oportunidades de Refatoração

| ID | Oportunidade | Quando aplicar |
|----|-------------|----------------|
| R01 | Centralizar toda conexão ao banco em `core/database.py` com `create_engine` + `SessionLocal` compartilhado | Antes de qualquer migração de módulo |
| R02 | Criar `core/config.py` para carregar e validar variáveis de ambiente com Pydantic ou dataclass | Junto com P02 |
| R03 | Criar `design/theme.py` ou `design/config.toml` com paleta de cores e estilos padrão | Antes da primeira tela real |
| R04 | Fixar versões no `requirements.txt` (ex: `streamlit>=1.35,<2.0`) | Imediato |
| R05 | Criar `core/utils.py` com funções reutilizáveis: formatação de moeda, datas, percentuais | Antes de qualquer tela com dados |
| R06 | Adicionar `@st.cache_data(ttl=300)` em todas as funções de consulta ao banco e APIs | Ao implementar cada módulo |
| R07 | Separar lógica de negócio das views (pages/ só chama funções de core/) | Padrão arquitetural desde o início |

---

## 17. O Que Pode Ser Reaproveitado dos Outros Três Apps

> Os 3 repositórios originais ainda não foram auditados neste ciclo. As sugestões abaixo são baseadas no planejamento documentado no cofre Obsidian.

| Repo de origem | O que aproveitar | Módulo destino |
|---------------|-----------------|----------------|
| `Tiago84Barros/Dashboard` | Lógica de visão consolidada, cards de saldo e fluxo de caixa, queries de receita/despesa | `pages/dashboard_geral.py` + `core/financeiro.py` |
| `Tiago84Barros/Dashboard` | Conexão ao banco (se existir `database.py`) | `core/database.py` |
| `Tiago84Barros/Dashboard-Investimentos` | Cálculo de custo médio ponderado | `core/investimentos.py` |
| `Tiago84Barros/Dashboard-Investimentos` | Cálculo de rentabilidade (TWRR) vs. CDI/IBOVESPA | `core/investimentos.py` |
| `Tiago84Barros/Dashboard-Investimentos` | Schema de ativos, operações, proventos | `05_Banco_de_Dados/` (Obsidian) |
| `Tiago84Barros/Dashboard-Investimentos` | Integração com cotações (se usar yfinance ou Alpha Vantage) | `core/cotacoes.py` |
| `Tiago84Barros/Controle_Financeiro` | Schema de transações, categorias, orçamentos | `05_Banco_de_Dados/` (Obsidian) |
| `Tiago84Barros/Controle_Financeiro` | Lógica de categorização de gastos | `core/categorias.py` |
| `Tiago84Barros/Controle_Financeiro` | Importação OFX/CSV (se existir) | `etl/importacao.py` |
| Todos os 3 | Telas e componentes visuais adaptáveis para Streamlit | `pages/` + `design/` |

**Pré-condição:** auditar cada repo antes de migrar qualquer funcionalidade.

---

## 18. Ordem Recomendada de Melhoria

### Fase 0 — Fundação (bloqueante para tudo)
1. Fixar versões no `requirements.txt`
2. Criar pastas `core/`, `etl/`, `design/` com `__init__.py`
3. Criar `.env` local a partir do `.env.example`
4. Criar `core/database.py` com engine e session factory
5. Implementar roteamento real em `app.py`
6. Criar stubs para os 5 módulos faltantes

### Fase 1 — Diagnóstico dos repositórios originais
7. Auditar `Tiago84Barros/Dashboard`
8. Auditar `Tiago84Barros/Dashboard-Investimentos`
9. Auditar `Tiago84Barros/Controle_Financeiro`
10. Mapear schema de banco dos 3 repos
11. Decidir: banco compartilhado com apps Next.js ou banco local separado

### Fase 2 — Migração do núcleo (Dashboard Geral)
12. Migrar lógica central para `core/financeiro.py`
13. Implementar `pages/dashboard_geral.py` com dados reais

### Fase 3 — Migração de Investimentos
14. Migrar para `core/investimentos.py` e `core/cotacoes.py`
15. Implementar `pages/investimentos.py`, `pages/carteira.py`, `pages/proventos.py`

### Fase 4 — Migração de Controle Financeiro
16. Migrar para `core/categorias.py` e `etl/importacao.py`
17. Implementar `pages/controle_financeiro.py`

### Fase 5 — Novos módulos
18. `pages/empresas_b3.py` + `pages/empresas_eua.py`
19. `pages/macro.py`
20. Integração OpenAI

### Fase 6 — Qualidade e segurança
21. Tema visual (`design/`)
22. Cache com `@st.cache_data`
23. Autenticação básica
24. Substituir conexão direta ao Supabase por `supabase-py` (mitigar S01)

---

*Próxima ação: ver `docs/plano_de_melhoria.md` e `docs/roadmap_mvp_unificado.md`*
*Documento gerado sem alterar nenhum código.*
