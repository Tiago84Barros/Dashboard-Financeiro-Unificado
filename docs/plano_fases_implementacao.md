# Plano de Implementação em Fases — Dashboard Financeiro Unificado

> Gerado em: 2026-05-13
> Baseado em: `docs/auditoria_tecnica.md`, `docs/plano_de_melhoria.md`, `docs/roadmap_mvp_unificado.md`
> Regras aplicadas: CLAUDE_INSTRUCTIONS.md (cofre ProjetoIA)
> **Nenhum código foi alterado na geração deste documento.**

---

## Visão Geral das 10 Fases

```
Fase 1  ──► Correções críticas e build funcionando        [BLOQUEANTE]
Fase 2  ──► Estrutura visual e navegação principal        [BLOQUEANTE]
Fase 3  ──► Visão Geral financeira com dados mockados     [MVP visual]
Fase 4  ──► Integração Supabase segura                    [MVP real]
Fase 5  ──► Módulo de Investimentos                       [MVP completo]
Fase 6  ──► Módulo de Controle Financeiro
Fase 7  ──► Metas financeiras
Fase 8  ──► Alertas inteligentes
Fase 9  ──► Refinamento visual e responsividade
Fase 10 ──► Documentação final e deploy
```

**Regra de progressão:** cada fase só começa após os critérios de conclusão da fase anterior serem verificados.

---

## Fase 1 — Correções Críticas e Build Funcionando

### Objetivo
Resolver os 3 problemas bloqueantes da auditoria (T01, T02, T03) e garantir que o app roda sem erros com navegação real entre todas as 9 telas. Esta fase não entrega funcionalidade visual — entrega fundação.

### Arquivos Prováveis

| Arquivo | Ação | Descrição |
|---------|:----:|-----------|
| `requirements.txt` | ✏️ Alterar | Fixar faixas de versão para todas as 9 dependências |
| `app.py` | ✏️ Alterar | Implementar roteamento real com `PAGE_MAP` |
| `core/__init__.py` | ➕ Criar | Marca a pasta como módulo Python |
| `core/config.py` | ➕ Criar | Carrega e valida variáveis de ambiente com `python-dotenv` |
| `core/database.py` | ➕ Criar | Engine SQLAlchemy + `@st.cache_resource` (stub sem URL real) |
| `etl/__init__.py` | ➕ Criar | Marca a pasta como módulo Python |
| `design/__init__.py` | ➕ Criar | Marca a pasta como módulo Python |
| `pages/proventos.py` | ➕ Criar | Stub `render()` com mensagem "em construção" |
| `pages/empresas_b3.py` | ➕ Criar | Stub `render()` com mensagem "em construção" |
| `pages/empresas_eua.py` | ➕ Criar | Stub `render()` com mensagem "em construção" |
| `pages/macro.py` | ➕ Criar | Stub `render()` com mensagem "em construção" |
| `pages/configuracoes.py` | ➕ Criar | Stub `render()` com mensagem "em construção" |

### Dependências
- Nenhuma dependência externa nova
- Requer que Python e pip estejam instalados

### Riscos

| Risco | Prob. | Impacto | Mitigação |
|-------|:-----:|:-------:|-----------|
| Versões fixadas incompatíveis entre si | Baixa | Alto | Testar com `pip install -r requirements.txt` em ambiente limpo antes de fixar |
| Import circular entre `app.py` e `pages/` | Baixa | Médio | Usar lazy imports dentro de cada `if menu ==` no `PAGE_MAP` |
| `core/database.py` falhar se `.env` não existir | Alta | Baixo | Usar `try/except` no carregamento da URL; app não deve travar se banco não estiver configurado |

### Critérios de Conclusão
- [ ] `pip install -r requirements.txt` executa sem erros em ambiente limpo
- [ ] `streamlit run app.py` sobe sem erros no terminal
- [ ] Cada um dos 9 itens do menu, ao ser clicado, exibe a tela correspondente (mesmo que seja "em construção")
- [ ] Nenhum erro `ModuleNotFoundError` ou `ImportError` no console
- [ ] `core/`, `etl/`, `design/` existem com `__init__.py`

### Testes Necessários
```
TESTE 1 — Instalação limpa
  pip install -r requirements.txt
  ESPERADO: zero erros

TESTE 2 — Boot do app
  streamlit run app.py
  ESPERADO: abre no browser sem exceção

TESTE 3 — Navegação completa (manual)
  Clicar em cada um dos 9 itens do menu
  ESPERADO: cada tela exibe título e mensagem (sem tela em branco)

TESTE 4 — Imports
  python -c "from core import config, database"
  python -c "from pages import dashboard_geral, controle_financeiro, investimentos, carteira, proventos, empresas_b3, empresas_eua, macro, configuracoes"
  ESPERADO: nenhum erro
```

---

## Fase 2 — Estrutura Visual e Navegação Principal

### Objetivo
Criar a identidade visual do app e padronizar a navegação antes de qualquer dado real ser adicionado. Entregar um app com aparência profissional mesmo com telas vazias, e estabelecer os componentes reutilizáveis que todas as fases seguintes usarão.

### Arquivos Prováveis

| Arquivo | Ação | Descrição |
|---------|:----:|-----------|
| `.streamlit/config.toml` | ➕ Criar | Tema visual: cores, fontes, layout |
| `design/tema.py` | ➕ Criar | CSS customizado injetado via `st.markdown` |
| `design/componentes.py` | ➕ Criar | Funções reutilizáveis: `card_metrica()`, `secao_titulo()`, `badge_status()` |
| `core/utils.py` | ➕ Criar | Formatadores: `fmt_moeda()`, `fmt_percentual()`, `fmt_data()`, `cor_valor()` |
| `app.py` | ✏️ Alterar | Aplicar tema na sidebar; adicionar logo/ícone; melhorar header |
| `pages/configuracoes.py` | ✏️ Alterar | Adicionar preview do tema atual |

### Detalhamento dos componentes-chave

**`.streamlit/config.toml` — tema financeiro:**
```toml
[theme]
primaryColor = "#1E88E5"        # azul institucional
backgroundColor = "#0F1117"     # fundo escuro (modo dark)
secondaryBackgroundColor = "#1A1D27"  # cards e sidebar
textColor = "#FAFAFA"
font = "sans serif"
```

**`core/utils.py` — formatadores essenciais:**
```python
# fmt_moeda(1234.56) → "R$ 1.234,56"
# fmt_percentual(0.1523) → "+15,23%"
# cor_valor(valor) → "green" se positivo, "red" se negativo
```

**`design/componentes.py` — card reutilizável:**
```python
# card_metrica(titulo, valor, delta, icone)
# Usado em: Dashboard Geral, Investimentos, Controle Financeiro
```

### Dependências
- Fase 1 concluída
- Nenhuma dependência de dados — apenas Streamlit e CSS

### Riscos

| Risco | Prob. | Impacto | Mitigação |
|-------|:-----:|:-------:|-----------|
| CSS injetado conflitar com tema nativo Streamlit | Média | Baixo | Testar em múltiplos browsers; usar seletores CSS específicos |
| `config.toml` com tema dark dificultar leitura de gráficos Plotly | Baixa | Médio | Testar com gráficos de exemplo antes de avançar para Fase 3 |
| Componentes reutilizáveis com API instável — mudanças causam quebra cascata | Baixa | Médio | Documentar API de cada componente com docstring antes de usar |

### Critérios de Conclusão
- [ ] App sobe com tema visual aplicado (cores, fontes)
- [ ] `card_metrica()` renderiza corretamente com valores positivos e negativos
- [ ] `fmt_moeda(1234.56)` retorna `"R$ 1.234,56"`
- [ ] `fmt_percentual(0.1523)` retorna `"+15,23%"`
- [ ] Sidebar tem aparência consistente com identidade visual
- [ ] Telas "em construção" usam o novo layout (não o `st.info` padrão)

### Testes Necessários
```
TESTE 1 — Tema visual
  streamlit run app.py
  ESPERADO: app com fundo escuro, cores azuis, fonte sans-serif

TESTE 2 — Componentes (isolado)
  python -c "
  import streamlit as st
  from design.componentes import card_metrica
  # verificar que não gera ImportError
  "

TESTE 3 — Formatadores
  python -c "
  from core.utils import fmt_moeda, fmt_percentual, cor_valor
  assert fmt_moeda(1234.56) == 'R$ 1.234,56'
  assert fmt_percentual(0.1523) == '+15,23%'
  assert cor_valor(100) == 'green'
  assert cor_valor(-50) == 'red'
  print('OK')
  "

TESTE 4 — Compatibilidade dark theme com Plotly
  Criar gráfico de linha simples no dashboard_geral e verificar legibilidade
```

---

## Fase 3 — Visão Geral Financeira com Dados Mockados

### Objetivo
Implementar a tela principal (`Dashboard Geral`) com dados mockados, validando layout, componentes e lógica de apresentação **antes** de conectar ao banco real. Permite detectar problemas de UX sem depender de infraestrutura.

### Arquivos Prováveis

| Arquivo | Ação | Descrição |
|---------|:----:|-----------|
| `pages/dashboard_geral.py` | ✏️ Alterar | Tela completa com dados mockados |
| `core/financeiro.py` | ➕ Criar | Funções de negócio com modo mock/real via flag |
| `core/mock_data.py` | ➕ Criar | Dados estáticos para desenvolvimento sem banco |

### Detalhamento das telas e KPIs

**Cards da Home (linha superior):**
```
┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
│ Saldo Total │ │  Receitas   │ │  Despesas   │ │  Patrimônio │
│ R$ 12.450   │ │ R$ 8.200    │ │ R$ 5.300    │ │ R$ 87.300   │
│ +2,3% mês   │ │ vs mês ant. │ │ vs mês ant. │ │ líquido     │
└─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘
```

**Gráfico principal:** Barras receita × despesa — últimos 12 meses (Plotly)

**Cards secundários:**
- Taxa de poupança do mês: `(Receita − Despesa) / Receita × 100`
- Fluxo de caixa líquido do mês

**Filtros:**
- Seletor de mês/ano (para dados mockados: escolher entre meses pré-definidos)

### Estrutura recomendada para `core/financeiro.py`
```python
USE_MOCK = True  # Fase 3: True | Fase 4: False (banco real)

def get_saldo_total() -> float:
    if USE_MOCK:
        return _mock_saldo_total()
    return _db_saldo_total()

def get_receitas_mes(mes: int, ano: int) -> float: ...
def get_despesas_mes(mes: int, ano: int) -> float: ...
def get_fluxo_caixa_12m() -> pd.DataFrame: ...
def calcular_taxa_poupanca(receitas: float, despesas: float) -> float: ...
```

### Dependências
- Fase 2 concluída (componentes e formatadores disponíveis)
- Pandas (já no requirements.txt)
- Plotly (já no requirements.txt)
- **Não requer banco de dados**

### Riscos

| Risco | Prob. | Impacto | Mitigação |
|-------|:-----:|:-------:|-----------|
| Dados mockados com formato diferente do banco real → retrabalho na Fase 4 | Alta | Médio | Criar mock com mesmo schema esperado das tabelas reais (mesmo que valores sejam fictícios) |
| Plotly com layout ruim no Streamlit (overflow, tamanho incorreto) | Média | Baixo | Usar `use_container_width=True` em todos os gráficos |
| `USE_MOCK = True` esquecido ao migrar para Fase 4 | Baixa | Alto | Ler variável `USE_MOCK` do `.env` (`MOCK_MODE=true/false`), não do código |

### Critérios de Conclusão
- [ ] Tela `Dashboard Geral` exibe 4 cards de métricas com valores mockados formatados
- [ ] Gráfico de barras 12 meses renderiza sem erros
- [ ] Taxa de poupança calculada corretamente: `(8200 - 5300) / 8200 = 35,37%`
- [ ] Filtro de mês/ano funciona e atualiza os valores
- [ ] Layout não quebra com janela redimensionada
- [ ] Nenhum `st.error` ou traceback visível na tela

### Testes Necessários
```
TESTE 1 — KPI de taxa de poupança
  from core.financeiro import calcular_taxa_poupanca
  assert round(calcular_taxa_poupanca(8200, 5300), 4) == 0.3537
  assert calcular_taxa_poupanca(0, 0) == 0  # divisão por zero tratada

TESTE 2 — Formato dos dados mockados
  from core.mock_data import get_mock_fluxo_caixa
  df = get_mock_fluxo_caixa()
  assert list(df.columns) == ['mes', 'ano', 'receitas', 'despesas']
  assert len(df) == 12

TESTE 3 — Renderização visual (manual)
  Abrir o app, ir em "Dashboard Geral"
  Verificar: 4 cards, 1 gráfico, 2 cards secundários
  Verificar: valores em R$ formatados corretamente
  Verificar: cores verde/vermelho nos deltas

TESTE 4 — Responsividade (manual)
  Redimensionar janela do browser
  ESPERADO: cards quebram em linha mas não ficam sobrepostos
```

---

## Fase 4 — Integração Supabase Segura

### Objetivo
Substituir os dados mockados por dados reais do banco PostgreSQL (Supabase), com conexão segura, cache adequado e sem expor credenciais. A flag `USE_MOCK` passa de `True` para `False`.

### Arquivos Prováveis

| Arquivo | Ação | Descrição |
|---------|:----:|-----------|
| `.env` | ➕ Criar | Variáveis reais (não versionado — já no .gitignore) |
| `.env.example` | ✏️ Atualizar | Remover `SUPABASE_DB_URL` redundante; adicionar `MOCK_MODE` |
| `core/config.py` | ✏️ Completar | Validação de todas as variáveis obrigatórias com mensagens de erro claras |
| `core/database.py` | ✏️ Completar | Engine real + `@st.cache_resource` + tratamento de falha de conexão |
| `core/financeiro.py` | ✏️ Alterar | Queries SQL reais substituindo os mocks |
| `pages/dashboard_geral.py` | ✏️ Alterar | Remover dependência de `mock_data`; adicionar spinner de carregamento |

### Detalhamento de `core/database.py`
```python
# Padrão correto: engine singleton via cache_resource
@st.cache_resource
def get_engine():
    url = settings.DATABASE_URL
    if not url:
        st.error("DATABASE_URL não configurada. Verifique o arquivo .env")
        st.stop()
    return create_engine(url, pool_pre_ping=True)
```

**Decisão obrigatória antes desta fase:**
> Banco compartilhado com apps Next.js (Supabase) **ou** PostgreSQL local separado?
> Registrar decisão em: `ProjetoIA/04_App_Dashboard_Financeiro_Unificado/proximos_passos.md`

### Segurança — Checklist obrigatório

| Item | Verificação |
|------|------------|
| `.env` não está versionado | `git status` não deve mostrar `.env` |
| Credenciais não estão no código | `grep -r "password\|secret\|key" core/ pages/ --include="*.py"` → zero resultados hardcoded |
| `service_role_key` nunca usado no app | Usar apenas `DATABASE_URL` com usuário de leitura/escrita limitado |
| Pool de conexão configurado | `pool_pre_ping=True`, `pool_size=5`, `max_overflow=2` |

### Dependências
- Fase 3 concluída (tela funcional com mock)
- Banco PostgreSQL acessível (Supabase ou local)
- Tabelas `contas` e `transacoes` existentes no banco
- Decisão D01 (banco compartilhado vs. separado) tomada e registrada

### Riscos

| Risco | Prob. | Impacto | Mitigação |
|-------|:-----:|:-------:|-----------|
| Schema do banco diferente do esperado — queries falham | Alta | Alto | Auditar os 3 repos originais ANTES de escrever as queries (roadmap Fase 1) |
| Conexão cai em produção — app trava | Média | Alto | `pool_pre_ping=True` + try/except em todas as queries |
| Dados reais divergentes dos mocks — tela quebra | Média | Médio | Validar tipos e formato dos dados retornados antes de renderizar |
| `SUPABASE_DB_URL` conecta direto ao Postgres e bypassa RLS | Alta | Alto | Usar apenas `DATABASE_URL` com usuário sem privilégio superuser; documentar decisão de RLS |

### Critérios de Conclusão
- [ ] `streamlit run app.py` conecta ao banco sem erros
- [ ] Dashboard Geral exibe dados reais (não mockados)
- [ ] Valores de saldo, receitas e despesas batem com os dados do banco
- [ ] Spinner de carregamento aparece durante queries
- [ ] Se banco estiver inacessível: mensagem de erro clara, app não trava
- [ ] `.env` não aparece em `git status`

### Testes Necessários
```
TESTE 1 — Conexão ao banco
  python -c "
  from core.database import get_engine
  engine = get_engine()
  with engine.connect() as conn:
      result = conn.execute('SELECT 1')
      print('Conexão OK:', result.fetchone())
  "

TESTE 2 — Query de saldo
  from core.financeiro import get_saldo_total
  saldo = get_saldo_total()
  assert isinstance(saldo, float)
  assert saldo >= 0

TESTE 3 — Fallback sem banco (manual)
  Remover DATABASE_URL do .env temporariamente
  ESPERADO: mensagem de erro clara na tela, não traceback

TESTE 4 — Nenhuma credencial no código
  grep -r "password\|senha\|secret\|@" core/ pages/ --include="*.py"
  ESPERADO: nenhuma string de credencial hardcoded
```

---

## Fase 5 — Módulo de Investimentos

### Objetivo
Implementar carteira de ativos com cotações em tempo real via yfinance, cálculo de custo médio, rentabilidade e comparativo com benchmarks (CDI, IBOVESPA).

### Arquivos Prováveis

| Arquivo | Ação | Descrição |
|---------|:----:|-----------|
| `core/cotacoes.py` | ➕ Criar | Wrapper yfinance com cache de 15min |
| `core/investimentos.py` | ➕ Criar | Custo médio, rentabilidade, TWRR, patrimônio |
| `pages/investimentos.py` | ✏️ Completar | Gráfico evolução patrimônio + rentabilidade vs. benchmark |
| `pages/carteira.py` | ✏️ Completar | Tabela de ativos + pie chart de alocação |
| `pages/proventos.py` | ✏️ Completar | Histórico de dividendos, JCP, FII |

### Detalhamento das regras de negócio

**Custo médio ponderado:**
```
custo_medio = Σ(quantidade_compra × preco_compra) / Σ(quantidade_compra)
```

**Rentabilidade por ativo:**
```
resultado_percentual = (preco_atual - custo_medio) / custo_medio × 100
resultado_reais = (preco_atual - custo_medio) × quantidade_atual
```

**Rentabilidade total da carteira (TWRR simplificado):**
```
patrimonio_atual = Σ(quantidade × preco_atual)
custo_total = Σ(quantidade × custo_medio)
rentabilidade = (patrimonio_atual - custo_total) / custo_total × 100
```

**Cache de cotações:**
```python
@st.cache_data(ttl=900)  # 15 minutos — delay do yfinance
def get_cotacao_atual(ticker: str) -> float: ...

@st.cache_data(ttl=3600)  # 1 hora — dados históricos
def get_historico(ticker: str, periodo: str) -> pd.DataFrame: ...
```

### Tabelas Supabase envolvidas
- `ativos` — catálogo: ticker, nome, classe (ação, FII, ETF, cripto, RF)
- `operacoes` — compras e vendas: ativo_id, data, tipo, quantidade, preco
- `proventos` — dividendos e JCP: ativo_id, data, tipo, valor_por_cota

### Dependências
- Fase 4 concluída (banco conectado e funcionando)
- `yfinance` instalado (já no requirements.txt)
- Tabelas `ativos`, `operacoes`, `proventos` existentes no banco
- Dados dos repos `Dashboard-Investimentos` migrados ou recriados

### Riscos

| Risco | Prob. | Impacto | Mitigação |
|-------|:-----:|:-------:|-----------|
| yfinance indisponível ou rate limit | Alta | Alto | Fallback para último valor cacheado; `try/except` em todas as chamadas |
| Tickers B3 com sufixo `.SA` vs. sem sufixo | Alta | Médio | Normalizar ticker no banco: sempre salvar com `.SA` para B3 |
| TWRR com múltiplos aportes produz resultado diferente do esperado | Média | Alto | Validar cálculo contra planilha Excel antes de exibir ao usuário |
| Dados de operações com custo médio já calculado no repo original — divergência | Média | Médio | Recalcular sempre a partir das operações; não confiar em campo `custo_medio` do banco |
| yfinance muda API — código quebra silenciosamente | Baixa | Alto | Fixar versão: `yfinance>=0.2.40,<0.3.0`; monitorar changelog |

### Critérios de Conclusão
- [ ] `pages/carteira.py` exibe tabela com: ticker, qtd, custo médio, preço atual, resultado R$, resultado %
- [ ] Resultado calculado bate com cálculo manual para pelo menos 3 ativos
- [ ] Pie chart de alocação por classe exibe percentuais corretos (soma = 100%)
- [ ] `pages/investimentos.py` exibe gráfico de evolução do patrimônio (últimos 12 meses)
- [ ] `pages/proventos.py` exibe histórico de proventos e total recebido no ano
- [ ] Cotações atualizadas via yfinance (timestamp visível na tela)
- [ ] Cache: segunda visita à tela não chama yfinance novamente (verificar no terminal)

### Testes Necessários
```
TESTE 1 — Custo médio (unitário)
  from core.investimentos import calcular_custo_medio
  operacoes = [
      {'tipo': 'C', 'quantidade': 10, 'preco': 50.0},
      {'tipo': 'C', 'quantidade': 10, 'preco': 60.0},
  ]
  assert calcular_custo_medio(operacoes) == 55.0

TESTE 2 — Cotação via yfinance
  from core.cotacoes import get_cotacao_atual
  preco = get_cotacao_atual('PETR4.SA')
  assert isinstance(preco, float)
  assert preco > 0

TESTE 3 — Fallback de cotação
  Simular falha de yfinance (desconectar internet)
  ESPERADO: mensagem de aviso, último valor cacheado exibido (ou zero com aviso)

TESTE 4 — Rentabilidade (validação manual)
  Inserir operação conhecida no banco
  Verificar que o percentual exibido bate com cálculo na calculadora

TESTE 5 — Soma da alocação
  Verificar que a soma dos percentuais no pie chart = 100%
```

---

## Fase 6 — Módulo de Controle Financeiro

### Objetivo
Implementar gestão de transações, categorização, orçamento mensal e visualização de gastos por categoria, com suporte a importação via CSV.

### Arquivos Prováveis

| Arquivo | Ação | Descrição |
|---------|:----:|-----------|
| `core/categorias.py` | ➕ Criar | Lógica de categorização, hierarquia pai/filho |
| `etl/importacao.py` | ➕ Criar | Parser CSV/OFX para importar extratos bancários |
| `pages/controle_financeiro.py` | ✏️ Completar | Tela completa de controle de gastos |

### Detalhamento da tela `controle_financeiro.py`

**Seções da tela:**
1. **Filtros** — data (mês/ano), categoria, conta
2. **Resumo do mês** — total receitas, total despesas, saldo do mês
3. **Orçamento por categoria** — barra de progresso: valor gasto vs. limite mensal
4. **Listagem de transações** — tabela com: data, descrição, categoria, valor, tipo (receita/despesa)
5. **Formulário de lançamento manual** — adicionar nova transação

**Barras de orçamento:**
```
Alimentação    [████████░░] R$ 800 / R$ 1.000   80%  → amarelo
Lazer          [████░░░░░░] R$ 200 / R$ 500      40%  → verde
Assinaturas    [██████████] R$ 350 / R$ 300     117%  → vermelho (acima do limite)
```

### Tabelas Supabase envolvidas
- `transacoes` — data, descricao, valor, tipo, categoria_id, conta_id
- `categorias` — id, nome, tipo (receita/despesa), pai_id
- `orcamentos` — categoria_id, mes_ano, valor_limite
- `contas` — id, nome, tipo, saldo_inicial

### Dependências
- Fase 4 concluída (banco conectado)
- Tabelas `transacoes`, `categorias`, `orcamentos`, `contas` existentes
- Dados do repo `Controle_Financeiro` mapeados (roadmap Fase 1)

### Riscos

| Risco | Prob. | Impacto | Mitigação |
|-------|:-----:|:-------:|-----------|
| Categorias hierárquicas (pai/filho) com query recursiva complexa no PostgreSQL | Média | Médio | Usar `WITH RECURSIVE` no SQL ou buscar todas e construir hierarquia em Python |
| Importação CSV com formatos diferentes de cada banco (Itaú, Nubank, XP) | Alta | Médio | Criar parsers separados por banco; documentar formatos suportados |
| Formulário de lançamento manual causar rerenders indesejados no Streamlit | Alta | Baixo | Usar `st.form` com `clear_on_submit=True` |
| Transações sem categoria (null) quebrando os cálculos de orçamento | Alta | Médio | Criar categoria padrão "Outros" para transações não categorizadas |

### Critérios de Conclusão
- [ ] Listagem de transações com filtros funcional
- [ ] Barras de orçamento calculadas corretamente (gasto/limite × 100)
- [ ] Transações acima do limite orçamentário exibidas em vermelho
- [ ] Formulário de lançamento manual persiste no banco (sem reload manual)
- [ ] Importação de CSV simples funciona para pelo menos 1 formato de banco
- [ ] Totais de receita e despesa batem com soma das transações listadas

### Testes Necessários
```
TESTE 1 — Porcentagem de orçamento (unitário)
  from core.categorias import calcular_percentual_orcamento
  assert calcular_percentual_orcamento(gasto=800, limite=1000) == 80.0
  assert calcular_percentual_orcamento(gasto=350, limite=300) == 116.67

TESTE 2 — Parser CSV (unitário)
  from etl.importacao import parse_csv_generico
  df = parse_csv_generico('tests/fixtures/extrato_exemplo.csv')
  assert list(df.columns) == ['data', 'descricao', 'valor', 'tipo']
  assert len(df) > 0

TESTE 3 — Inserção de transação (integração)
  Inserir transação via formulário no app
  Verificar que aparece na listagem sem reload manual
  Verificar que o saldo do mês foi atualizado

TESTE 4 — Filtro de data (manual)
  Selecionar mês diferente no filtro
  ESPERADO: lista e totais mudam para o mês selecionado
```

---

## Fase 7 — Metas Financeiras

### Objetivo
Implementar o sistema de metas com progresso visual, cálculo de aporte necessário mensal e projeção de prazo.

### Arquivos Prováveis

| Arquivo | Ação | Descrição |
|---------|:----:|-----------|
| `core/metas.py` | ➕ Criar | Lógica de progresso, projeção de prazo, aporte necessário |
| `pages/controle_financeiro.py` | ✏️ Alterar | Adicionar seção de metas (ou criar `pages/metas.py` separado) |

### Detalhamento das regras de negócio

**Progresso da meta:**
```
progresso = valor_acumulado / valor_alvo × 100
```

**Aporte mensal necessário para atingir meta no prazo:**
```
meses_restantes = (data_prazo - hoje).days / 30
valor_restante = valor_alvo - valor_acumulado
aporte_necessario = valor_restante / meses_restantes
```

**Status visual:**
```
< 25%  → vermelho   (iniciando)
25-74% → amarelo    (em andamento)
≥ 75%  → verde      (quase lá)
100%   → azul       (atingida)
```

**Cards de cada meta:**
```
┌──────────────────────────────┐
│ 🎯 Reserva de Emergência     │
│ R$ 6.000 / R$ 20.000         │
│ [██████░░░░░░░░░░░] 30%       │
│ Faltam: R$ 14.000             │
│ Aporte necessário: R$ 700/mês │
│ Prazo: dez/2026               │
└──────────────────────────────┘
```

### Tabelas Supabase envolvidas
- `metas` — id, nome, valor_alvo, valor_acumulado, prazo, tipo

### Dependências
- Fase 6 concluída (módulo de controle financeiro funcionando)
- Tabela `metas` existente no banco

### Riscos

| Risco | Prob. | Impacto | Mitigação |
|-------|:-----:|:-------:|-----------|
| `meses_restantes` = 0 (prazo vencido) causa divisão por zero | Alta | Médio | Tratar caso especial: exibir "Prazo vencido" em vez de calcular |
| Meta com `valor_acumulado` > `valor_alvo` (meta superada) | Média | Baixo | Limitar progress bar a 100%; exibir badge "Meta atingida!" |
| Aporte calculado acima da capacidade financeira do usuário — pode ser frustrante | Baixa | Baixo | Exibir aporte com contexto (ex: "equivale a X% da renda") |

### Critérios de Conclusão
- [ ] Cards de metas com barra de progresso renderizam corretamente
- [ ] `progresso(6000, 20000)` retorna `30.0`
- [ ] `aporte_necessario(14000, 20)` retorna `700.0`
- [ ] Prazo vencido exibe mensagem de alerta, sem erro de divisão por zero
- [ ] Meta atingida exibe badge "Concluída" (progress bar não ultrapassa 100%)
- [ ] Formulário para adicionar nova meta funciona e persiste no banco

### Testes Necessários
```
TESTE 1 — Cálculos de meta (unitários)
  from core.metas import calcular_progresso, calcular_aporte_necessario
  assert calcular_progresso(6000, 20000) == 30.0
  assert calcular_progresso(20000, 20000) == 100.0
  assert calcular_aporte_necessario(14000, 20) == 700.0
  assert calcular_aporte_necessario(14000, 0) is None  # prazo vencido

TESTE 2 — Edge cases
  calcular_progresso(25000, 20000)  # superada → deve retornar 100.0 (capped)
  calcular_progresso(0, 0)          # divisão por zero → deve retornar 0

TESTE 3 — Renderização (manual)
  Criar 3 metas com progresso < 25%, 50%, > 75%
  ESPERADO: cores diferentes para cada status
```

---

## Fase 8 — Alertas Inteligentes

### Objetivo
Implementar sistema de alertas automáticos baseado em regras financeiras e, opcionalmente, análise via OpenAI para sugestões contextualizadas.

### Arquivos Prováveis

| Arquivo | Ação | Descrição |
|---------|:----:|-----------|
| `core/alertas.py` | ➕ Criar | Engine de regras de alerta com severidade |
| `core/ia.py` | ➕ Criar | Wrapper OpenAI para análise financeira contextualizada |
| `pages/dashboard_geral.py` | ✏️ Alterar | Adicionar painel de alertas no Dashboard Geral |

### Alertas por regra (sem IA)

| Trigger | Mensagem | Severidade |
|---------|---------|:----------:|
| Despesas do mês > receitas | "Gastos acima da renda este mês" | 🔴 Crítico |
| Categoria com orçamento > 100% | "Orçamento de [categoria] estourado" | 🟠 Alto |
| Saldo < R$ 500 | "Saldo em conta baixo" | 🟠 Alto |
| Meta com prazo < 30 dias e < 80% do valor | "Meta [nome] vence em breve" | 🟡 Atenção |
| Taxa de poupança < 10% | "Taxa de poupança abaixo do recomendado" | 🟡 Atenção |
| Ativo com queda > 10% na semana | "Queda relevante em [ticker]" | 🟡 Atenção |

### Integração OpenAI (opcional, requer chave)
```python
# core/ia.py — análise contextual dos alertas
def gerar_diagnostico_financeiro(contexto: dict) -> str:
    """
    Recebe resumo financeiro do mês e retorna análise em português.
    Contexto: receitas, despesas, taxa_poupanca, alertas_ativos, patrimonio
    Retorna: texto de 2-3 parágrafos com análise e sugestões
    """
```

**Segurança obrigatória para OpenAI:**
- Nunca enviar dados pessoais identificáveis (nome, CPF, saldo exato) — apenas agregados
- Nunca exibir `OPENAI_API_KEY` na interface
- Tratar erro de API key inválida ou rate limit com mensagem amigável

### Dependências
- Fases 5, 6 e 7 concluídas (dados financeiros disponíveis)
- `openai` instalado (já no requirements.txt)
- `OPENAI_API_KEY` no `.env` (opcional — alertas por regra não requerem)

### Riscos

| Risco | Prob. | Impacto | Mitigação |
|-------|:-----:|:-------:|-----------|
| Custo da API OpenAI em uso frequente | Média | Médio | Cache de resposta por dia (`@st.cache_data(ttl=86400)`); usar modelo mais barato (gpt-4o-mini) |
| OpenAI API Key inválida ou sem créditos — app quebra | Média | Médio | `try/except` com fallback para alertas por regra apenas |
| Dados sensíveis enviados por engano à OpenAI | Baixa | Crítico | Revisar payload antes de cada chamada; usar apenas agregados |
| Muitos alertas simultâneos poluem a tela | Média | Baixo | Limitar exibição a top 3 alertas mais críticos |

### Critérios de Conclusão
- [ ] Pelo menos 6 regras de alerta implementadas e funcionando
- [ ] Painel de alertas no Dashboard Geral exibe alertas ativos
- [ ] Alertas ordenados por severidade (crítico primeiro)
- [ ] Se `OPENAI_API_KEY` não configurada: sistema funciona apenas com alertas por regra
- [ ] Se OpenAI falhar: fallback para alertas por regra, sem erro visível
- [ ] Nenhum dado identificável enviado à OpenAI (apenas agregados)

### Testes Necessários
```
TESTE 1 — Motor de regras (unitário)
  from core.alertas import avaliar_alertas
  contexto = {'receitas': 5000, 'despesas': 6000, 'saldo': 300}
  alertas = avaliar_alertas(contexto)
  assert any(a['tipo'] == 'despesas_acima_receita' for a in alertas)
  assert any(a['tipo'] == 'saldo_baixo' for a in alertas)

TESTE 2 — Sem OpenAI key
  Remover OPENAI_API_KEY do .env
  ESPERADO: alertas por regra funcionam normalmente; seção de IA não aparece

TESTE 3 — Rate limit OpenAI (mock)
  Simular HTTPError 429 na chamada à OpenAI
  ESPERADO: mensagem "Análise de IA temporariamente indisponível" — sem traceback

TESTE 4 — Payload OpenAI (segurança)
  Inspecionar o dict enviado à API
  VERIFICAR: sem CPF, nome, email, saldo exato — apenas agregados percentuais
```

---

## Fase 9 — Refinamento Visual e Responsividade

### Objetivo
Elevar a qualidade percebida do app: loading states, estados vazios, tratamento de erros, consistência visual entre telas e ajustes de layout para diferentes tamanhos de tela.

### Arquivos Prováveis

| Arquivo | Ação | Descrição |
|---------|:----:|-----------|
| `design/componentes.py` | ✏️ Ampliar | Adicionar: `estado_vazio()`, `spinner_carregando()`, `badge_alerta()`, `tooltip_info()` |
| `design/tema.py` | ✏️ Refinar | Ajustes de padding, tamanho de fonte, espaçamentos |
| `pages/*.py` (todos) | ✏️ Revisar | Aplicar loading states, tratamento de dados vazios, mensagens de erro consistentes |
| `core/utils.py` | ✏️ Ampliar | Adicionar: `truncar_texto()`, `pluralizar()`, `tempo_atualizado()` |

### Padrões de estado a implementar em todas as telas

```
ESTADO CARREGANDO:
  with st.spinner('Carregando dados...'):
      dados = get_dados()

ESTADO VAZIO (sem dados):
  if dados.empty:
      estado_vazio(
          icone='📊',
          titulo='Nenhum dado encontrado',
          descricao='Adicione sua primeira transação para começar.',
          acao='Adicionar transação'
      )

ESTADO DE ERRO:
  try:
      dados = get_dados()
  except Exception as e:
      st.error(f'Erro ao carregar dados. Tente novamente.')
      # log interno do erro, nunca exibir stacktrace ao usuário
```

### Checklist de revisão por tela

| Tela | Loading | Estado vazio | Erro tratado | Layout responsivo |
|------|:-------:|:------------:|:------------:|:-----------------:|
| Dashboard Geral | — | — | — | — |
| Controle Financeiro | — | — | — | — |
| Investimentos | — | — | — | — |
| Carteira | — | — | — | — |
| Proventos | — | — | — | — |
| Metas | — | — | — | — |
| Configurações | — | — | — | — |

*(preencher com ✅/❌ durante a fase)*

### Dependências
- Todas as fases 1–8 concluídas
- Nenhuma nova dependência

### Riscos

| Risco | Prob. | Impacto | Mitigação |
|-------|:-----:|:-------:|-----------|
| Mudanças visuais quebrarem lógica existente | Baixa | Médio | Só alterar CSS e layout — nunca lógica de negócio nesta fase |
| Streamlit ter limitações de responsividade em mobile | Alta | Baixo | App é primariamente desktop; documentar limitação explicitamente |

### Critérios de Conclusão
- [ ] Todas as telas têm spinner durante carregamento de dados
- [ ] Todas as telas tratam o caso de dados vazios com mensagem contextual
- [ ] Todos os erros de banco e API exibem mensagem amigável (sem stacktrace)
- [ ] Layout do Dashboard Geral é consistente com tela de 1280px e 1920px
- [ ] Cores de positivo/negativo são consistentes em todas as telas
- [ ] Nenhum `st.error` com traceback Python visível para o usuário final

### Testes Necessários
```
TESTE 1 — Estado vazio (manual, por tela)
  Limpar dados de cada módulo no banco
  ESPERADO: mensagem de estado vazio, não tela em branco

TESTE 2 — Erro de banco simulado (manual)
  Desconectar banco durante uso
  ESPERADO: mensagem de erro amigável em todas as telas

TESTE 3 — Consistência visual (manual)
  Navegar por todas as 9 telas em sequência
  VERIFICAR: mesma fonte, mesmas cores de status, mesma posição de títulos

TESTE 4 — Layout 1280px vs 1920px (manual)
  Abrir em janela de 1280px → verificar sem overflow
  Abrir em tela full 1920px → verificar sem excesso de espaço vazio
```

---

## Fase 10 — Documentação Final e Deploy

### Objetivo
Preparar o app para uso contínuo: documentação atualizada, instruções de deploy, configuração de backup e registro de decisões técnicas tomadas ao longo do projeto.

### Arquivos Prováveis

| Arquivo | Ação | Descrição |
|---------|:----:|-----------|
| `README.md` | ✏️ Reescrever | Documentação completa: requisitos, instalação, configuração, uso |
| `docs/decisoes_tecnicas.md` | ➕ Criar | Registro de ADRs (Architecture Decision Records) |
| `docs/guia_deploy.md` | ➕ Criar | Instruções para deploy local, Streamlit Cloud e Docker |
| `docs/guia_banco.md` | ➕ Criar | Schema final, migrations, backup |
| `CLAUDE.md` | ✏️ Atualizar | Refletir estrutura final do projeto |
| `.env.example` | ✏️ Revisar | Garantir que todas as variáveis usadas estão documentadas |
| `requirements.txt` | ✏️ Revisar | Confirmar versões testadas e compatíveis |

### Decisões técnicas a registrar (ADRs)

| ADR | Decisão | Contexto |
|-----|---------|---------|
| ADR-001 | Banco compartilhado vs. separado | Tomada na Fase 4 |
| ADR-002 | yfinance vs. API paga para cotações | Tomada na Fase 5 |
| ADR-003 | SQLAlchemy direto vs. supabase-py | Tomada na Fase 4 |
| ADR-004 | App local vs. hospedado | Define autenticação e segurança |
| ADR-005 | Roteamento manual vs. multipage nativo Streamlit | Tomada na Fase 1 |

### Deploy — Opções

**Opção A — Local (padrão atual):**
```bash
git clone https://github.com/Tiago84Barros/Dashboard-Financeiro-Unificado
pip install -r requirements.txt
cp .env.example .env  # preencher variáveis
streamlit run app.py
```

**Opção B — Streamlit Cloud (hospedagem gratuita):**
- Requer: repo público ou privado no GitHub
- Secrets via `st.secrets` (não via `.env`)
- Atenção: autenticação obrigatória se dados forem pessoais

**Opção C — Docker (portabilidade):**
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.port=8501"]
```

### Atualizações no Obsidian (cofre ProjetoIA)

Ao final desta fase, atualizar:
- `04_App_Dashboard_Financeiro_Unificado/visao_geral.md` — status: MVP concluído
- `04_App_Dashboard_Financeiro_Unificado/arquitetura_atual.md` — estrutura final real
- `STATUS_DOS_APPS.md` — App 4: "MVP concluído"
- `MAPA_GERAL_DOS_APPS.md` — atualizar integração com o banco

### Dependências
- Todas as fases 1–9 concluídas
- Decisão de hospedagem tomada

### Riscos

| Risco | Prob. | Impacto | Mitigação |
|-------|:-----:|:-------:|-----------|
| Deploy no Streamlit Cloud exigir refatoração do `.env` para `st.secrets` | Alta | Médio | Documentar a diferença no `guia_deploy.md`; criar wrapper `core/config.py` que lê ambos |
| README desatualizado em relação à estrutura real | Alta | Baixo | Escrever README DEPOIS que todas as fases estiverem concluídas |
| Variáveis de `.env.example` faltantes após adições ao longo do projeto | Média | Médio | Validar `.env.example` contra o `core/config.py` final |

### Critérios de Conclusão
- [ ] `README.md` tem instruções completas de instalação e execução
- [ ] `docs/guia_deploy.md` documenta as 3 opções de deploy
- [ ] `docs/decisoes_tecnicas.md` registra todos os ADRs do projeto
- [ ] `docs/guia_banco.md` documenta schema final com todas as tabelas
- [ ] `.env.example` tem todas as variáveis usadas no código (nenhuma faltando)
- [ ] `STATUS_DOS_APPS.md` (Obsidian) atualizado com status "MVP concluído"
- [ ] CLAUDE.md reflete estrutura real do projeto

### Testes Necessários
```
TESTE 1 — Instalação do zero
  Clonar repo em máquina limpa (sem deps instaladas)
  Seguir exatamente o README
  ESPERADO: app rodando sem necessidade de buscar informação extra

TESTE 2 — Variáveis de ambiente
  python -c "
  from core.config import settings
  # Verificar que todas as vars obrigatórias são carregadas
  print(settings.DATABASE_URL[:20])  # apenas início, não expor credenciais
  "

TESTE 3 — Deploy local completo
  Destruir venv, reinstalar, configurar .env, subir app
  Navegar por todas as 9 telas
  ESPERADO: app funcional sem erros

TESTE 4 — .env.example vs. config.py
  Comparar variáveis em .env.example com variáveis lidas em core/config.py
  ESPERADO: nenhuma variável usada no código ausente do .env.example
```

---

## Resumo das 10 Fases

| Fase | Objetivo | Entregável Principal | Estimativa |
|:----:|---------|---------------------|:----------:|
| 1 | Correções críticas | App multi-página sem erros | 1–2h |
| 2 | Estrutura visual | Tema, componentes, formatadores | 2–4h |
| 3 | Mock do Dashboard | Tela com dados fictícios reais | 4–8h |
| 4 | Integração Supabase | Dados reais no Dashboard Geral | 4–8h |
| 5 | Investimentos | Carteira + rentabilidade + cotações | 1–2 sem. |
| 6 | Controle Financeiro | Transações + orçamento + CSV | 1–2 sem. |
| 7 | Metas | Cards de progresso + projeção | 2–3 dias |
| 8 | Alertas | Regras + IA opcional | 3–5 dias |
| 9 | Refinamento visual | Polimento de todas as telas | 3–5 dias |
| 10 | Deploy | README + ADRs + instrução de deploy | 1–2 dias |

**Total estimado:** 6–10 semanas de trabalho incremental.

---

## Decisões Pendentes (pré-condições)

Antes de iniciar qualquer fase acima de 4, as seguintes decisões devem ser tomadas e registradas:

| # | Decisão | Impacto | Onde registrar |
|---|---------|:-------:|----------------|
| D01 | Banco compartilhado (Supabase) ou local separado? | 🔴 Alto | `proximos_passos.md` (Obsidian) |
| D02 | yfinance ou API paga (Alpha Vantage)? | 🟡 Médio | `proximos_passos.md` (Obsidian) |
| D03 | App local ou hospedado? | 🟠 Alto | `proximos_passos.md` (Obsidian) |
| D04 | Schema compartilhado com apps Next.js? | 🔴 Alto | `05_Banco_de_Dados/modelagem_inicial.md` (Obsidian) |

---

*Ver também: `docs/auditoria_tecnica.md`, `docs/plano_de_melhoria.md`, `docs/roadmap_mvp_unificado.md`*
*Documento gerado sem alterar nenhum código.*
