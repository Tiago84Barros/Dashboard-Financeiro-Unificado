# Plano de Implementação em Fases — Dashboard Financeiro Unificado

> Gerado em: 2026-05-13 | Última revisão: 2026-05-14 (pós-Fase 5, v0.5.10)
> Baseado em: `docs/auditoria_tecnica.md`, `docs/plano_de_melhoria.md`, `docs/roadmap_mvp_unificado.md`

---

## Estado Atual — v0.5.10 (2026-05-14)

| Fase | Nome | Status |
|:----:|------|:------:|
| 1 | Correções críticas e build funcionando | ✅ Concluída |
| 2 | Estrutura visual e navegação principal | ✅ Concluída |
| 3 | Visão Geral financeira com dados mockados | ✅ Concluída |
| 4 | Integração Supabase segura | ✅ Concluída |
| 5 | Módulo de Investimentos (+ Controle, Metas, Alertas) | ✅ Concluída |
| 6 | Schema cartão + IR estimado | 🔲 Próxima |
| 7 | Empresas — fundamentalistas + séries macro | 🔲 Pendente |
| 8 | Módulo IA (OpenAI) | 🔲 Pendente |
| 9 | Refinamento visual e responsividade | 🔲 Pendente |
| 10 | Documentação final e deploy | 🔲 Pendente |

> **Nota sobre a Fase 5:** O escopo original previa apenas o módulo de Investimentos. Durante a execução, os módulos de Controle Financeiro, Metas e Alertas (planejados originalmente para as Fases 6, 7 e 8) foram antecipados e implementados como subfases 5.4–5.6. O plano de fases foi ajustado para refletir a nova realidade.

---

## Visão Geral das Fases

```
Fase 1  ──► Correções críticas e build funcionando        ✅ CONCLUÍDA
Fase 2  ──► Estrutura visual e navegação principal        ✅ CONCLUÍDA
Fase 3  ──► Visão Geral financeira com dados mockados     ✅ CONCLUÍDA
Fase 4  ──► Integração Supabase segura                    ✅ CONCLUÍDA
Fase 5  ──► Módulo completo de investimentos + controle   ✅ CONCLUÍDA
Fase 6  ──► Schema cartão + IR estimado                   ← PRÓXIMA
Fase 7  ──► Empresas (fundamentalistas + macro)
Fase 8  ──► Módulo IA (OpenAI)
Fase 9  ──► Refinamento visual e responsividade
Fase 10 ──► Documentação final e deploy
```

---

## Fase 1 — Correções Críticas e Build Funcionando ✅ CONCLUÍDA

**Concluída em:** 2026-05-13

**Entregou:**
- Roteamento real via `importlib` em `app.py`
- `core/__init__.py`, `core/config.py`, `core/database.py`
- `etl/__init__.py`, `design/__init__.py`
- Stubs funcionais para todas as páginas
- Versões fixadas em `requirements.txt`

### Critérios de Conclusão
- [x] `pip install -r requirements.txt` executa sem erros
- [x] `streamlit run app.py` sobe sem erros no terminal
- [x] Todos os itens do menu exibem a tela correspondente
- [x] Nenhum `ModuleNotFoundError` ou `ImportError`
- [x] `core/`, `etl/`, `design/` existem com `__init__.py`

---

## Fase 2 — Estrutura Visual e Navegação Principal ✅ CONCLUÍDA

**Concluída em:** 2026-05-13

**Entregou:**
- `.streamlit/config.toml` com tema dark
- `design/tema.py` — CSS customizado
- `design/componentes.py` — `card_metrica()`, `secao_titulo()`, `badge_status()`
- `core/utils.py` — `fmt_moeda()`, `fmt_percentual()`, `fmt_data()`, `cor_valor()`

### Critérios de Conclusão
- [x] App sobe com tema visual dark aplicado
- [x] `card_metrica()` renderiza corretamente
- [x] `fmt_moeda(1234.56)` retorna `"R$ 1.234,56"`
- [x] Sidebar com aparência consistente

---

## Fase 3 — Visão Geral Financeira com Dados Mockados ✅ CONCLUÍDA

**Concluída em:** 2026-05-13

**Entregou:**
- `core/mock_data.py` — schema completo de dados mockados
- `core/financeiro.py` — KPIs com padrão mock/real/fallback
- `pages/dashboard_geral.py` — 6 seções com KPIs e gráficos Plotly

### Critérios de Conclusão
- [x] Tela exibe 4+ cards de métricas com valores formatados
- [x] Gráfico de barras 12 meses renderiza sem erros
- [x] Layout não quebra com janela redimensionada

---

## Fase 4 — Integração Supabase Segura ✅ CONCLUÍDA

**Concluída em:** 2026-05-13

**Entregou:**
- `core/auth.py` — gate de senha SHA-256
- `etl/schema_setup.py` — DDL para 10 tabelas
- `etl/importacao.py` — pipeline CSV → PostgreSQL
- `core/config.py` expandido (`APP_PASSWORD`, `OWNER_USER_ID`, `SUPABASE_UNIFICADO_URL`)
- `pages/configuracoes.py` — 4 abas (DB status, ETL, configurações, credenciais)
- `app.py` v0.4.0 — gate de autenticação integrado

### Critérios de Conclusão
- [x] `streamlit run app.py` conecta ao banco sem erros
- [x] Gate de senha funcional
- [x] ETL de importação operacional (CSV → Supabase)
- [x] `.env` não aparece em `git status`

---

## Fase 5 — Módulo Completo (Investimentos + Controle + Metas + Alertas) ✅ CONCLUÍDA

**Concluída em:** 2026-05-14 — v0.5.10 (10 subfases: 5.0 a 5.10)

**Escopo executado:**

| Subfase | Entregável | Dados reais |
|---------|-----------|:-----------:|
| 5.0 | Inventário funcional: 51 funcionalidades mapeadas nos 3 apps originais | — |
| 5.1 | `core/investimentos.py` + `pages/carteira.py` — 34 posições, LATERAL JOIN cotações | 34 posições |
| 5.2 | `core/proventos.py` + `pages/proventos.py` — 517 eventos, KPIs, gráficos | 517 proventos |
| 5.3 | `pages/investimentos.py` — 4 tabs, 6 KPIs, radar de risco, evolução patrimonial | 1.351 transações |
| 5.4 | `core/controle.py` + `pages/controle_financeiro.py` — KPIs reais, orçamento, form INSERT | 251 transações |
| 5.5 | `core/metas.py` + `pages/metas.py` — CRUD completo, status automático, aporte sugerido | — |
| 5.6 | `core/alertas.py` + `pages/alertas.py` — 6 regras automáticas | — |
| 5.7–5.9 | `core/empresas.py` + `pages/empresas_b3.py`, `empresas_eua.py`, `macro.py` | 82 ativos |
| 5.10 | `pages/configuracoes.py` aba Cotações — batch yfinance → `asset_quotes` | — |
| 5 revisão | `pages/controle_financeiro.py` v4 — auditoria fiel 42 funcionalidades, YOY, dual-axis | — |

**Módulos core criados:**
`core/investimentos.py`, `core/proventos.py`, `core/controle.py`, `core/metas.py`, `core/alertas.py`, `core/empresas.py`

### Critérios de Conclusão
- [x] 11 rotas em `app.py` com `pages/X.py` correspondente e `render()` funcional
- [x] 16 módulos `core/` + `design/` + `etl/` compilam sem erro (`py_compile`)
- [x] Dados reais exibidos: 34 posições, 517 proventos, 1.351 transações de investimento
- [x] Cotações atualizadas via yfinance (`configuracoes.py` aba Cotações)
- [x] 6 regras de alertas automáticas ativas
- [x] Controle Financeiro com 42 funcionalidades equivalentes ao App 3 original

---

## Fase 6 — Schema Cartão + IR Estimado 🔲 PRÓXIMA

### Objetivo
Implementar o registro de gastos de cartão de crédito (faturas, lançamentos futuros, limite) e o cálculo estimado de IR sobre ganhos de capital e dividendos recebidos.

### Arquivos Prováveis

| Arquivo | Ação | Descrição |
|---------|:----:|-----------|
| `supabase_unificado/schema/010_cards_schema.sql` | ➕ Criar | DDL para tabelas `cards`, `card_bills`, `card_transactions` |
| `core/cartao.py` | ➕ Criar | Serviço de fatura, lançamentos e limite disponível |
| `core/ir.py` | ➕ Criar | Cálculo de IR: ganho de capital (15%/20%) e DARF mensal |
| `pages/cartao.py` | ➕ Criar | Tela de cartão: fatura atual, lançamentos, limite |
| `pages/ir.py` | ➕ Criar | Estimativa de IR mensal e acumulado no ano |
| `app.py` | ✏️ Alterar | Adicionar rotas "💳 Cartão" e "🧾 IR Estimado" |

### Regras de negócio — IR sobre ganhos de capital

```
Venda ação BR (lucro ≤ R$ 20.000/mês) → isento
Venda ação BR (lucro > R$ 20.000/mês) → 15% sobre o lucro
Venda day trade → 20% sobre o lucro
Venda FII → 20% sobre o lucro (não há isenção)
JCP recebido → IR na fonte 15% (já retido)
Dividendo BR → isento (regra pré-reforma 2024)
```

### Tabelas Supabase envolvidas
- `cards` — id, name, limit, closing_day, due_day
- `card_bills` — card_id, reference_month, total_amount, status
- `card_transactions` — bill_id, date, description, amount, category_id
- `investment_transactions` — já existente; adicionar campo `ir_aliquota`

### Dependências
- Fase 5 concluída
- Decisão: adicionar tabelas `cards` ao schema existente ou schema separado?
- Dados de vendas em `investment_transactions` com campo `type = 'sell'` populado

### Riscos

| Risco | Prob. | Impacto | Mitigação |
|-------|:-----:|:-------:|-----------|
| Regras de IR mudando por reforma tributária | Alta | Médio | Parametrizar alíquotas em `core/config.py` ou tabela `tax_rules` |
| Cálculo de IR com compensação de prejuízo (carry-forward) | Média | Alto | Implementar acumulador mensal de prejuízo por tipo de ativo |
| Schema `cards` conflitar com tabela existente no Supabase | Baixa | Alto | Auditar banco antes de executar DDL |

### Critérios de Conclusão
- [ ] `cards` e `card_bills` criadas no Supabase sem quebrar tabelas existentes
- [ ] Tela de Cartão exibe fatura atual e lançamentos do mês
- [ ] IR estimado calculado para vendas do mês corrente
- [ ] IR acumulado no ano exibido com breakdown por tipo (ações, FII, day trade)
- [ ] Fatura do cartão integrada ao Controle Financeiro como despesa do mês

---

## Fase 7 — Empresas (Fundamentalistas + Séries Macro) 🔲 PENDENTE

### Objetivo
Completar as telas `Empresas EUA` e `Cenário Macroeconômico` com dados fundamentalistas e séries históricas das principais variáveis econômicas.

### Arquivos Prováveis

| Arquivo | Ação | Descrição |
|---------|:----:|-----------|
| `pages/empresas_eua.py` | ✏️ Completar | P/L, EV/EBITDA, dividend yield, setor, filtros avançados |
| `pages/macro.py` | ✏️ Completar | Séries históricas: SELIC, IPCA, câmbio, IBOVESPA, CDI |
| `core/empresas.py` | ✏️ Ampliar | Fonte de dados fundamentalistas (yfinance ou CVM) |

### Dependências
- Fase 5 concluída
- Dados fundamentalistas disponíveis via yfinance ou API CVM

### Critérios de Conclusão
- [ ] `empresas_eua.py` exibe P/L, dividend yield e filtros por setor
- [ ] `macro.py` exibe SELIC, IPCA e câmbio com gráficos históricos de 12 meses
- [ ] Benchmarks IBOVESPA e CDI comparados com rentabilidade da carteira

---

## Fase 8 — Módulo IA (OpenAI) 🔲 PENDENTE

### Objetivo
Implementar análise contextual da saúde financeira via OpenAI, com diagnóstico mensal e sugestões personalizadas baseadas nos dados reais do usuário.

### Arquivos Prováveis

| Arquivo | Ação | Descrição |
|---------|:----:|-----------|
| `core/ia.py` | ➕ Criar | Wrapper OpenAI — prompt estruturado + contexto financeiro agregado |
| `pages/dashboard_geral.py` | ✏️ Alterar | Adicionar seção de diagnóstico IA |

### Segurança obrigatória
- Nunca enviar CPF, nome, saldo exato — apenas agregados percentuais
- `OPENAI_API_KEY` nunca exposta na interface
- `@st.cache_data(ttl=86400)` para evitar custo por recarga de página
- Fallback: se API falhar, exibir apenas alertas por regra

### Dependências
- Fases 5–7 concluídas (dados ricos disponíveis para contexto)
- `OPENAI_API_KEY` configurada no `.env`

### Critérios de Conclusão
- [ ] Diagnóstico mensal gerado com base em receitas, despesas, carteira e alertas
- [ ] Cache de 24h evita chamadas repetidas à API
- [ ] Se OpenAI indisponível: sistema funciona sem IA, sem erro visível
- [ ] Nenhum dado identificável enviado à OpenAI

---

## Fase 9 — Refinamento Visual e Responsividade 🔲 PENDENTE

### Objetivo
Elevar qualidade percebida: loading states, estados vazios, consistência visual entre telas, ajustes de layout.

### Padrões a aplicar em todas as telas

```python
# Loading
with st.spinner('Carregando dados...'):
    dados = get_dados()

# Estado vazio
if not dados:
    estado_vazio(icone='📊', titulo='Nenhum dado', descricao='...')

# Erro tratado (sem stacktrace ao usuário)
try:
    dados = get_dados()
except Exception:
    st.error('Erro ao carregar dados. Tente novamente.')
```

### Critérios de Conclusão
- [ ] Todas as telas têm spinner durante carregamento
- [ ] Todas as telas tratam dados vazios com mensagem contextual
- [ ] Todos os erros exibem mensagem amigável (sem traceback Python)
- [ ] Layout consistente em 1280px e 1920px

---

## Fase 10 — Documentação Final e Deploy 🔲 PENDENTE

### Objetivo
Preparar o app para uso contínuo: documentação atualizada, instruções de deploy, backup e registro de decisões técnicas.

### Arquivos Prováveis

| Arquivo | Ação | Descrição |
|---------|:----:|-----------|
| `README.md` | ✏️ Atualizar | Já reescrito em 2026-05-14 — revisar ao final |
| `docs/decisoes_tecnicas.md` | ➕ Criar | ADRs do projeto |
| `docs/guia_deploy.md` | ➕ Criar | Deploy local, Streamlit Cloud e Docker |
| `docs/guia_banco.md` | ➕ Criar | Schema final, migrations, backup |

### Deploy — Opções

**Opção A — Local (padrão atual):**
```bash
git clone https://github.com/Tiago84Barros/Dashboard-Financeiro-Unificado
pip install -r requirements.txt
cp .env.example .env  # preencher variáveis
streamlit run app.py
```

**Opção B — Streamlit Cloud:**
- Secrets via `st.secrets` (não via `.env`)
- Autenticação obrigatória se dados forem pessoais

**Opção C — Docker:**
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.port=8501"]
```

### Critérios de Conclusão
- [ ] `README.md` tem instruções completas de instalação e execução
- [ ] `docs/guia_deploy.md` documenta as 3 opções de deploy
- [ ] `docs/decisoes_tecnicas.md` registra todos os ADRs do projeto
- [ ] `STATUS_DOS_APPS.md` (Obsidian) atualizado com status "MVP concluído"

---

## Resumo das Fases — Estado Atual

| Fase | Objetivo | Status | Versão |
|:----:|---------|:------:|:------:|
| 1 | Correções críticas | ✅ Concluída | v0.1.0 |
| 2 | Estrutura visual | ✅ Concluída | v0.2.0 |
| 3 | Dashboard mockado | ✅ Concluída | v0.3.0 |
| 4 | Integração Supabase | ✅ Concluída | v0.4.0 |
| 5 | Módulo completo | ✅ Concluída | v0.5.10 |
| 6 | Schema cartão + IR | 🔲 Próxima | v0.6.x |
| 7 | Fundamentalistas + macro | 🔲 Pendente | v0.7.x |
| 8 | Módulo IA | 🔲 Pendente | v0.8.x |
| 9 | Refinamento visual | 🔲 Pendente | v0.9.x |
| 10 | Deploy | 🔲 Pendente | v1.0.0 |

---

*Ver também: `docs/status_atual_implementacao.md`, `docs/status_fase_5.md`*
