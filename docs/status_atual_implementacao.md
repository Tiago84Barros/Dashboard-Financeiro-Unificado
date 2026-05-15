# Status Atual de Implementação — Dashboard Financeiro Unificado

> Gerado em: 2026-05-14
> Versão: v0.5.10
> Fase atual: **Fase 5 concluída**

---

## 1. Fase Atual

**Fase 5 — Módulo Completo** — concluída em 2026-05-14 (10 subfases: 5.0 a 5.10).

O app saiu de esqueleto com stubs (v0.1.0) para plataforma funcional com 11 telas, dados reais de três bases migradas, cotações via yfinance, sistema de alertas automáticos e controle financeiro com 42 funcionalidades equivalentes ao app original.

**Próxima fase:** Fase 6 — Schema cartão + IR estimado.

---

## 2. Telas Implementadas (Funcionais)

| Tela | Módulo page | Módulo core | Dados reais | Volumes |
|------|------------|-------------|:-----------:|---------|
| Dashboard Geral | `dashboard_geral.py` | `financeiro.py`, `mock_data.py` | ✅ | KPIs de `transactions`, `portfolio_positions` |
| Controle Financeiro | `controle_financeiro.py` | `controle.py` | ✅ | 251 transações, 38 categorias, 2 contas |
| Metas | `metas.py` | `metas.py` | ✅ | `financial_goals` (fallback mock se vazia) |
| Alertas | `alertas.py` | `alertas.py` | ✅ | 6 regras em `v_budget_usage_mtd`, `financial_goals`, `asset_quotes`, `budgets`, `v_monthly_cashflow` |
| Investimentos | `investimentos.py` | `investimentos.py`, `proventos.py` | ✅ | 1.351 `investment_transactions`, 517 proventos |
| Carteira | `carteira.py` | `investimentos.py` | ✅ | 34 posições via `portfolio_positions` + LATERAL `asset_quotes` |
| Proventos | `proventos.py` | `proventos.py` | ✅ | 517 eventos em `dividends` × `assets` |
| Empresas B3 | `empresas_b3.py` | `empresas.py` | ✅ | 82 ativos em `assets` + cotação mais recente |
| Configurações | `configuracoes.py` | `auth.py`, `database.py` | ✅ | 5 abas: Banco, Importação, Cotações, Segurança, Setup |

**Total: 9 telas plenamente funcionais.**

---

## 3. Telas Parcialmente Implementadas

### 3.1 Empresas EUA (`empresas_eua.py`)

| O que funciona | O que está pendente |
|---------------|---------------------|
| Filtra ativos USD do banco (`assets` WHERE `currency = 'USD'`) | P/L, P/VP, dividend yield, market cap via yfinance |
| Tabela com ticker, nome, classe e cotação mais recente | Dados fundamentalistas (`multiplos` tabela não populada) |
| Filtros por classe e busca por ticker/nome | Roadmap: Fase 7 — yfinance ou CVM |

### 3.2 Cenário Macroeconômico (`macro.py`)

| O que funciona | O que está pendente |
|---------------|---------------------|
| Exibe valores de referência: SELIC, IPCA, câmbio, IBOVESPA, S&P 500 | Séries históricas dinâmicas via API BCB ou yfinance |
| Benchmarks do banco (`benchmarks` + `benchmark_quotes`) | `info_economica` e `info_economica_mensal` não populadas |
| Layout completo pronto para dados dinâmicos | Roadmap: Fase 7 |

---

## 4. Dependências de Banco

### 4.1 Tabelas consultadas por módulo

| Módulo | Tabelas / Views |
|--------|----------------|
| `core/investimentos.py` | `portfolio_positions`, `assets`, `asset_quotes`, `investment_transactions` |
| `core/proventos.py` | `dividends`, `assets` |
| `core/controle.py` | `transactions`, `categories`, `accounts`, `budgets`, `v_monthly_cashflow` |
| `core/metas.py` | `financial_goals` |
| `core/alertas.py` | `v_budget_usage_mtd`, `financial_goals`, `asset_quotes`, `budgets`, `v_monthly_cashflow` |
| `core/empresas.py` | `assets`, `asset_quotes` |
| `core/financeiro.py` | `transactions`, `accounts`, `portfolio_positions` |

### 4.2 Views requeridas

| View | Usado em | Status |
|------|---------|:------:|
| `v_monthly_cashflow` | `core/controle.py`, `core/alertas.py` | ✅ DDL em `007_views.sql` |
| `v_budget_usage_mtd` | `core/alertas.py` | ✅ DDL em `007_views.sql` |

### 4.3 Tabelas com RLS ativo

Todas as tabelas financeiras (`transactions`, `investment_transactions`, `dividends`, `portfolio_positions`, `financial_goals`, `budgets`) têm RLS com filtro por `user_id`. O app usa `OWNER_USER_ID` do `.env` em todas as queries parametrizadas com `:uid`.

A tabela `assets` **não tem filtro de user** — é tratada como catálogo de mercado público.

---

## 5. Pendências de Supabase

### 5.1 Tabelas/colunas populadas mas com dados ausentes

| Tabela | Situação | Impacto |
|--------|---------|---------|
| `asset_quotes` | Vazia após migração | Rentabilidade exibe 0% em Carteira e Investimentos; banner informativo exibido; resolver via Configurações → Cotações |
| `budgets` | Vazia | Orçamentos calculados de forma implícita (`gasto × 1,2`); alertas R5 ativo; resolver cadastrando orçamentos no app |
| `info_economica` | Vazia | Tela Macro usa referências manuais |
| `info_economica_mensal` | Vazia | Idem |
| `multiplos` | Vazia | Tela Empresas EUA sem fundamentalistas |
| `financial_goals` | Pode estar vazia | Módulo de Metas cai em mock automático |

### 5.2 Campos ausentes no schema atual (dependência da Fase 6)

| Campo | Tabela | Impacto |
|-------|--------|---------|
| `payment_type` | `transactions` | Controle Financeiro sem distinção de lançamento à vista vs. parcelado |
| `card_name` | `transactions` | Gastos de cartão sem identificação por cartão |
| `installments` | `transactions` | Parcelamentos não expandidos mês a mês |
| Tabela `cards` | — | Tela de Cartão não existe (Fase 6) |
| Tabela `card_bills` | — | Idem |
| Tabela `card_transactions` | — | Idem |

### 5.3 Schema executado

Os DDLs estão em `supabase_unificado/schema/`. Já aplicados ao banco:

```
001_core_tables.sql       ✅
002_financial_tables.sql  ✅
003_investment_tables.sql ✅
004_import_migration_tables.sql ✅
005_indexes.sql           ✅
006_rls_policies.sql      ✅
007_views.sql             ✅
008_seed_reference_data.sql ✅
009_schema_amendments.sql ✅
```

---

## 6. Pendências de Migração

### 6.1 Dados já migrados

| Origem | Tabela destino | Registros | Script |
|--------|---------------|:---------:|--------|
| App 2 (SQLite) — transações de investimento | `investment_transactions` | 1.351 | `migration/03_extract_investimentos_sqlite.py` |
| App 2 — proventos | `dividends` | 517 | idem |
| App 2 — ativos | `assets` | 82 | idem |
| App 3 (Supabase) — transações financeiras | `transactions` | 251 | `migration/02_extract_controle_financeiro.py` |
| App 3 — categorias | `categories` | 38 | idem |
| App 3 — contas | `accounts` | 2 | idem |

### 6.2 Dados ainda não migrados

| Dado | Origem | Status | Impacto |
|------|--------|:------:|---------|
| Cotações históricas | yfinance | Pendente — executar via Configurações | Rentabilidade e análise histórica |
| Fundos imobiliários — múltiplos | CVM / yfinance | Não iniciado | Tela Empresas B3 sem P/VP, DY |
| Séries macroeconômicas | API BCB / yfinance | Não iniciado | Tela Macro com dados estáticos |
| Posições de cartão | App 3 (se houver) | Não mapeado | Tela Cartão (Fase 6) |

### 6.3 Scripts de migração disponíveis

```
migration/01_extract_dashboard_financeiro.py    ← App 1
migration/02_extract_controle_financeiro.py     ← App 3
migration/03_extract_investimentos_sqlite.py    ← App 2 (SQLite)
migration/04_transform_to_canonical.py          ← normalização
migration/05_load_to_unified_supabase.py        ← carga no Supabase
migration/06_validate_migration.py              ← validação pós-carga
migration/07_report_migration.py               ← relatório
migration/08_compute_portfolio_positions.py     ← cálculo de posições
```

---

## 7. Checklist — Ativar MOCK_MODE=false com Segurança

Siga esta sequência para ativar dados reais sem quebrar o app.

### Pré-condições

- [ ] **7.1** `SUPABASE_UNIFICADO_URL` configurada no `.env` com URL válida do Supabase
- [ ] **7.2** `OWNER_USER_ID` configurado no `.env` com o UUID do usuário dono dos dados
- [ ] **7.3** `APP_PASSWORD` configurado no `.env`
- [ ] **7.4** `.env` não está versionado (`git status` não deve listar `.env`)

### Verificação do banco

- [ ] **7.5** Executar: `python -c "from core.database import get_engine; e = get_engine(); print('OK' if e else 'ERRO')"` → deve retornar `OK`
- [ ] **7.6** Confirmar que as 9 migrations DDL foram executadas no Supabase (ver seção 5.3)
- [ ] **7.7** Confirmar que `v_monthly_cashflow` e `v_budget_usage_mtd` existem: consultar via Supabase SQL Editor

### Migração de dados

- [ ] **7.8** Se ainda não migrado, executar `migration/05_load_to_unified_supabase.py`
- [ ] **7.9** Executar `migration/06_validate_migration.py` — verificar relatório sem erros críticos
- [ ] **7.10** Executar `migration/08_compute_portfolio_positions.py` — preenche `portfolio_positions`

### Ativar modo real

- [ ] **7.11** No `.env`, definir `MOCK_MODE=false`
- [ ] **7.12** Reiniciar o app: `streamlit run app.py`

### Verificação tela a tela

- [ ] **7.13 Dashboard Geral** — KPIs exibem valores reais (saldo, receitas/despesas do mês)
- [ ] **7.14 Controle Financeiro** — lista transações reais do mês atual
- [ ] **7.15 Carteira** — exibe posições reais; se rentabilidade = 0% → executar passo 7.16
- [ ] **7.16 Cotações** — Configurações → aba Cotações → "Atualizar Cotações" (preenche `asset_quotes`)
- [ ] **7.17 Proventos** — lista eventos com `payment_date` real
- [ ] **7.18 Investimentos** — gráfico cashflow com dados reais; 4 tabs funcionando
- [ ] **7.19 Metas** — lista metas de `financial_goals`; se vazia → banner mock esperado
- [ ] **7.20 Alertas** — verificar se alertas R4 (cotações vazias) e R5 (budgets vazios) somem após 7.16

### Pendências documentadas (não bloqueiam uso)

| Item | Tela afetada | Quando resolver |
|------|-------------|----------------|
| `asset_quotes` vazia | Carteira, Investimentos | Após passo 7.16 |
| `budgets` vazia | Alertas (R5), Controle | Cadastrar manualmente no app |
| `financial_goals` vazia | Metas | Cadastrar via form na tela Metas |
| Fundamentalistas ausentes | Empresas EUA | Fase 7 |
| Séries macro | Cenário Macro | Fase 7 |

---

## 8. Integridade do Código (Verificado em 2026-05-14)

| Verificação | Resultado |
|------------|:---------:|
| 11 rotas em `app.py` com `views/X.py` correspondente | ✅ |
| 11 arquivos `views/X.py` com função `render()` | ✅ |
| 16 módulos (`core/`, `design/`, `etl/`) sem erros de sintaxe | ✅ |
| `py_compile` em todos os módulos | ✅ 0 erros |
| Nenhuma credencial hardcoded | ✅ |
| `.env` no `.gitignore` | ✅ |

---

## 9. Decisões Técnicas Tomadas

| Decisão | Escolha | Motivo |
|---------|---------|--------|
| Banco compartilhado vs. separado | Banco unificado Supabase (separado dos apps Next.js) | App 4 é standalone, schema diferente |
| Cotações | yfinance (gratuito) | Suficiente para uso pessoal; sem custo |
| ORM | SQLAlchemy direto | Mais controle sobre queries; supabase-py não era necessário |
| Roteamento | `importlib.import_module` manual | Evita conflito com multipage nativo Streamlit |
| Autenticação | Gate SHA-256 simples | App local, não precisa de auth complexa |
| TWRR | Simplificado (custo médio por posição) | Dados históricos de cotação insuficientes para TWRR puro |

---

## 10. Próximos Passos Recomendados

### Imediatos (antes de iniciar a Fase 6)

| # | Ação | Por quê | Como |
|---|------|---------|------|
| 1 | Importar cotações via yfinance | `asset_quotes` vazia → rentabilidade = 0% em Carteira e Investimentos | Configurações → aba Cotações → "Atualizar Cotações" |
| 2 | Cadastrar orçamentos mensais | `budgets` vazia → alerta R5 ativo; orçamento implícito (×1,2) não é o ideal | Controle Financeiro → aba Orçamento → inserir limites por categoria |
| 3 | Cadastrar metas financeiras | `financial_goals` vazia → tela Metas exibe apenas mock | Metas → form "Nova Meta" |
| 4 | Verificar saldo das contas | Confirmar que `accounts.current_balance` reflete o saldo real | Controle Financeiro → KPIs de saldo |

### Fase 6 — Schema Cartão + IR Estimado

| Etapa | Descrição | Dependência |
|-------|-----------|-------------|
| 6.1 | Criar DDL `supabase_unificado/schema/010_cards_schema.sql` | Nenhuma (só precisa de revisão humana antes de executar) |
| 6.2 | Implementar `core/cartao.py` | DDL executado |
| 6.3 | Implementar `views/cartao.py` | `core/cartao.py` |
| 6.4 | Implementar `core/ir.py` (ganho de capital, DARF) | `investment_transactions` com campo `type = 'sell'` |
| 6.5 | Implementar `views/ir.py` | `core/ir.py` |
| 6.6 | Adicionar rotas em `app.py` | Ambas as páginas implementadas |

### Fase 7 — Fundamentalistas + Macro

| Etapa | Descrição |
|-------|-----------|
| 7.1 | Completar `views/empresas_eua.py` com P/L, EPS, market cap via `yfinance.Ticker.info` |
| 7.2 | Completar `views/macro.py` com séries históricas via API BCB (`api.bcb.gov.br`) ou yfinance |
| 7.3 | Popular tabela `multiplos` com dados de fundamentalistas B3 |

### Backlog técnico (não blocante)

| Item | Impacto | Prioridade |
|------|---------|:----------:|
| Adicionar `@st.cache_data` a `core/controle.get_controle()` após testar invalidação | Performance em tabelas grandes | Média |
| Validar `migration/06_validate_migration.py` contra dados atuais do banco | Confirmar integridade pós-migração | Baixa |
| Criar `docs/decisoes_tecnicas.md` com os ADRs registrados na Seção 9 | Documentação de arquitetura | Baixa |

---

*Ver também: [`docs/status_fase_5.md`](status_fase_5.md) · [`docs/plano_fases_implementacao.md`](plano_fases_implementacao.md) · [`README.md`](../README.md)*
