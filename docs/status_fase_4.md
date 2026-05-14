# Status — Fase 4: Banco Supabase Unificado

> Data: 2026-05-14
> Versão: v0.4.9
> ruff: All checks passed!
> Startup: HTTP 200 ✅
> Produção: ✅ Validado em 2026-05-14 — Badge "Dados reais" · R$ 405.073,96 · Score 70/100
> Atualizado em: 2026-05-14 — Fase 4.9 validada em produção (Streamlit Cloud)

---

## Redefinição da Fase 4

A Fase 4 foi redefinida e expandida em 10 subfases (4.0 a 4.9) para construir
o banco Supabase unificado de forma segura, sem perda de dados e sem criar
um terceiro projeto Supabase (limitação do plano gratuito).

**Decisão arquitetural:** usar o projeto Supabase existente do **Dashboard Financeiro**
como banco unificado. O projeto **Controle Financeiro** é fonte temporária de migração.

| Subfase | Nome | Status |
|---------|------|:------:|
| **4.0** | Estratégia e documentação | ✅ Concluída |
| **4.1** | Auditoria dos bancos atuais | ✅ Concluída |
| **4.2** | Modelo canônico | ✅ Concluída |
| **4.3** | Scripts SQL não destrutivos | ✅ Concluída |
| **4.4** | Revisão humana dos scripts | ✅ Concluída — scripts aprovados com ajustes |
| **4.5** | Aplicação manual no Supabase | ✅ Concluída — 22 tabelas + RLS + role criados |
| **4.6** | Scripts de migração ETL | ✅ Concluída — 8 scripts Python criados (dry_run por padrão) |
| **4.7** | Migração controlada | ✅ Concluída — dry run com dados reais aprovado (0 erros, 3 fontes, 9/9 validações) |
| **4.8** | Validação dos dados | ✅ Concluída — aprovado · P1 corrigido · pronto para Fase 4.9 |
| **4.9** | Conexão do app ao banco | ✅ Concluída e validada em produção — badge "Dados reais" · R$ 405.073,96 |

**Documentação completa:**
- `docs/fase_4_9_conexao_app_banco_real.md` — relatório completo da Fase 4.9
- `docs/banco_unificado_fases.md` — detalhamento de cada subfase
- `docs/estrategia_supabase_unificado_plano_gratuito.md` — decisão e estratégia
- `docs/banco_unificado_regras_de_seguranca.md` — regras de segurança
- `docs/banco_unificado_modelo_canonico.md` — modelo canônico das 22 tabelas (Fase 4.2)
- `docs/banco_unificado_mapa_origem_destino.md` — mapeamento de origens → destino
- `docs/banco_unificado_dicionario_dados.md` — dicionário de dados completo
- `docs/banco_unificado_decisoes_modelagem.md` — log de decisões arquiteturais
- `supabase_unificado/` — pasta operacional (schema, migrations, backups, validation)
- `docs/fase_4_8_validacao_pos_migracao.md` — relatório completo da validação (Fase 4.8)

---

## Fase 4.8 — Validação Pós-Migração (✅ Concluída — aprovado com ressalvas)

> Data: 2026-05-14

### Resultado geral

**✅ APROVADO** — dados íntegros, P1 e P2 corrigidos, todas as views funcionando. Pronto para Fase 4.9.

### Contagens validadas

| Tabela | Registros | Fonte |
|--------|----------:|-------|
| `transactions` | 251 | App3 (Controle Financeiro) |
| `assets` | 82 | App2 (Investimentos SQLite) |
| `investment_transactions` | 1.351 | App2 |
| `dividends` | 517 | App2 |
| `categories` | 38 | Seeds |
| `accounts` | 2 | Seed manual |
| `financial_institutions` | 7 | App2 |
| **Total migrado** | **2.211** | |

### Integridade

- ✅ 12/12 verificações de integridade OK
- ✅ Zero violações de FK
- ✅ Zero `user_id` nulos em tabelas pessoais
- ✅ Zero `category_id` órfãos
- ✅ Zero datas futuras ou nulas
- ✅ 82/82 ativos com setor preenchido

### Somatórios

- **Receitas:** R$ 319.708,65 (47 transações)
- **Despesas:** R$ 215.656,57 (183 transações)
- **Saldo líquido:** R$ 104.052,08
- **Volume compras (investimentos):** R$ 4.311.419,97 (874 operações)
- **Volume vendas (investimentos):** R$ 2.959.385,92 (477 operações)
- **Total proventos:** R$ 114.144,19 (517 eventos)

### Views

| View | Status |
|------|:------:|
| `v_account_balance` | ✅ CC R$ 245.043 · C6 R$ −33.527 |
| `v_budget_usage_mtd` | ℹ️ Sem dados (esperado) |
| `v_category_spending_mtd` | ✅ 9 categorias em maio/2026 |
| `v_investment_summary` | ✅ reit 6 ativos · stock 28 ativos · R$ 193.557 |
| `v_monthly_cashflow` | ✅ 8 meses com receitas e despesas corretas |
| `v_net_worth` | ✅ Patrimônio total R$ 405.073,96 |

### Problemas identificados

| ID | Severidade | Descrição |
|----|:----------:|-----------|
| **P1** | ✅ Corrigido | 182 expenses negadas em 2026-05-14. Views corretas. |
| **P2** | ✅ Corrigido | `portfolio_positions` populada em 2026-05-14 (Fase 4.8.1). 34 posições, R$ 193.557. |
| **P3** | 🟢 Leve | App2 não rastreado em `migration_source_map` (ON CONFLICT protege) |
| **P4** | ⚪ Info | Um `import_batch` com `status='processing'` stale (não deletável) |

### Fase 4.8.1 — Compute portfolio_positions (✅ Concluída)

- Script: `migration/08_compute_portfolio_positions.py`
- Portfolio criado: "Carteira Principal" (id=015ce5fc-...)
- 34 posições inseridas via custo médio ponderado
- 1 posição excluída (DIRR3 — preço médio negativo por histórico incompleto)
- 34 ativos zerados não inseridos
- `v_investment_summary`: 6 REITs + 28 ações = R$ 193.557,85
- `v_net_worth.net_worth`: **R$ 405.073,96** (bancário R$ 211.516 + investimentos R$ 193.558)

### Pré-requisitos para Fase 4.9

1. ~~Aplicar correção P1~~ ✅ Concluído em 2026-05-14
2. ~~Computar portfolio_positions~~ ✅ Concluído em 2026-05-14 (Fase 4.8.1)
3. ~~Conectar app (`MOCK_MODE = False`)~~ ✅ Concluído em 2026-05-14 (Fase 4.9)

---

## Fase 4.9 — Conexão do App ao Banco Real (✅ Concluída e validada em produção)

> Data: 2026-05-14  
> Validação em produção: 2026-05-14

### Escopo

Conectar a página **Visão Geral / Dashboard Geral** ao banco Supabase unificado,
mantendo fallback automático para mock em caso de falha.

### Arquivos modificados

| Arquivo | Ação | Descrição |
|---------|:----:|-----------|
| `core/financeiro.py` | **MODIFICADO** | `_visao_geral_real()` implementada + fallback + `data_source` |
| `pages/dashboard_geral.py` | **MODIFICADO** | Badge dinâmico ("Dados reais" / "Modo mock" / "Fallback") |

### Views conectadas

| View | Dados fornecidos |
|------|----------------|
| `v_net_worth` | `patrimonio.total`, `.investido`, `.saldo_bancario` |
| `v_monthly_cashflow` | `fluxo_mes`, `historico_mensal` (últimos 6 meses) |
| `v_category_spending_mtd` | `categorias_despesa`, `maior_categoria`, `categoria_alerta` |
| `v_investment_summary` | `classes_ativo`, `portfolio.num_ativos` |
| `v_budget_usage_mtd` | `categorias_despesa.orcamento` (quando configurado) |
| `dividends` | `portfolio.dividendos_mes`, `portfolio.dividendos_ano` |

### Lógica de fallback

```python
# MOCK_MODE=true  → mock intencional (data_source="mock")
# MOCK_MODE=false → tenta banco real
#   → OK   : data_source="real"
#   → FALHA : data_source="mock_fallback" (log de aviso, sem crash)
```

### Indicador de fonte (dashboard_geral.py)

| Fonte | Badge | Cor |
|-------|-------|-----|
| `"real"` | ✅ Dados reais | Verde |
| `"mock"` | ⚠️ Modo mock | Amarelo |
| `"mock_fallback"` | ❌ Fallback (mock) | Vermelho |

### Valores reais (Mai 2026)

| Indicador | Valor |
|-----------|------:|
| Patrimônio total | R$ 405.073,96 |
| Saldo bancário | R$ 211.516,11 |
| Patrimônio investido | R$ 193.557,85 |
| Receitas do mês | R$ 27.672,42 |
| Despesas do mês | R$ 13.925,40 |
| Economia | R$ 13.747,02 |
| Taxa de poupança | 49,7% |
| Meses de reserva | 15,2× |
| Score de saúde | 70/100 |

### Limitações conhecidas (resolvidas nas próximas fases)

| Limitação | Causa | Solução futura |
|-----------|-------|----------------|
| `rentabilidade_mes_pct = 0%` | `asset_quotes` vazia | Fase 5: alimentar cotações |
| `dividendos_ano = R$ 0` | Dividendos históricos (datas em anos anteriores) | Revisão dos `ex_date` nos registros |
| `orcamento` gerado (120% do gasto) | Nenhum budget cadastrado | Cadastrar budgets via UI |

### Testes executados

| Teste | Resultado |
|-------|-----------|
| `ruff check core/financeiro.py pages/dashboard_geral.py` | ✅ All checks passed! |
| Importação `core.financeiro` fora do contexto Streamlit | ✅ OK |
| `_visao_geral_mock()` — schema completo | ✅ 9/9 chaves |
| `_visao_geral_real()` com banco real | ✅ 9/9 chaves · schema idêntico ao mock |
| Fallback `engine=None` → RuntimeError | ✅ propagada corretamente |
| Fallback `owner_id=''` → RuntimeError | ✅ propagada corretamente |
| `calcular_saude_score(50, 15, 0, 7, False)` | ✅ → 70 |

### Validação visual em produção (Streamlit Cloud)

> Data: 2026-05-14 · `MOCK_MODE=false` · `DATABASE_URL` em Streamlit Secrets

| KPI | Valor validado |
|-----|:-------------:|
| Badge | ✅ **Dados reais** (verde) |
| Patrimônio Total | R$ 405.073,96 |
| Saldo Disponível | R$ 211.516,11 |
| Receitas do Mês | R$ 27.672,42 |
| Despesas do Mês | R$ 13.925,40 |
| Patrimônio Investido | R$ 193.557,85 |
| Economia do Mês | R$ 13.747,02 |
| Taxa de Poupança | 49,70% |
| Score de Saúde | 70/100 |

**Fallback mock preservado:** `MOCK_MODE=true` continua funcionando sem acesso ao banco.  
**Página Carteira:** em construção — será conectada ao banco real na **Fase 5.1**.

### Para ativar no Streamlit Cloud

Adicione em **Settings > Secrets**:

```toml
MOCK_MODE = "false"
OWNER_USER_ID = "<uuid-do-usuario>"
# Uma das três (prioridade nesta ordem):
SUPABASE_UNIFICADO_URL = "postgresql://..."
# ou DATABASE_URL = "postgresql://..."
# ou SUPABASE_DB_URL = "postgresql://..."
```

> ⚠️ **Nunca** adicionar a connection string ao código-fonte ou ao git.

---

## Fase 4.0 — Estratégia e Documentação (✅ Concluída)

**O que foi entregue nesta subfase:**

---

## Implementação de Base (Fases 4.0 anterior → código)

Entregue antes da redefinição da Fase 4 — código permanece válido e é a base
sobre a qual as Fases 4.1–4.9 serão construídas.

Objetivo original implementado:
1. Gate de autenticação para proteção no Streamlit Cloud
2. Gerenciamento de schema do banco (CREATE TABLE IF NOT EXISTS)
3. Camada de importação ETL (CSV/Excel e PostgreSQL-to-PostgreSQL)
4. Página de Configurações funcional com 4 abas

---

## Arquivos Criados / Modificados

| Arquivo | Ação | Descrição |
|---------|:----:|-----------|
| `core/auth.py` | **CRIADO** | Gate de autenticação — senha em texto simples ou hash SHA-256 |
| `etl/schema_setup.py` | **CRIADO** | DDL das 10 tabelas em ordem de dependência FK; `criar_schema()` / `verificar_schema()` |
| `etl/importacao.py` | **CRIADO** | `ImportadorCSV` (transações, operações, proventos) + `ImportadorPostgres` (genérico + app1/2/3) |
| `core/config.py` | **MODIFICADO** | Adicionados `APP_PASSWORD`, `OWNER_USER_ID`, `SOURCE_DB_APP1/2/3`, `has_owner`, `has_source_*` |
| `app.py` | **MODIFICADO** | `verificar_autenticacao()` adicionado; versão bumpeada para v0.4.0 |
| `.env.example` | **MODIFICADO** | Documentadas todas as novas variáveis com exemplos e instruções |
| `pages/configuracoes.py` | **MODIFICADO** | Reescrito com 4 abas funcionais |

---

## Segurança Implementada

### S01 — Bypass de RLS (mitigado por design)

A conexão direta PostgreSQL/SQLAlchemy não aciona o RLS do Supabase.
Mitigação:
- Criar role `app4_reader` com `GRANT SELECT` apenas nas 10 tabelas necessárias
- Nunca conceder `BYPASSRLS` a este role
- Toda query deve incluir `WHERE usuario_id = :owner_id`
- Instruções SQL para setup do role documentadas na aba "Setup" das Configurações

### S02 — Sem autenticação (resolvido)

Dois mecanismos independentes:
1. `core/auth.py` — password gate antes de qualquer renderização (SHA-256 ou texto simples)
2. `OWNER_USER_ID` — UUID do proprietário nos dados; filtro universal nas queries

### S03 — OPENAI_API_KEY (documentado)

Variável isolada em `.env`, nunca exposta na UI. Aviso exibido na sidebar se ausente.

---

## Camada ETL

### ImportadorCSV

Suporta upload de arquivos CSV/Excel diretamente na interface Streamlit.
Mapeia para as tabelas: `transacoes`, `operacoes`, `proventos`.
Todas as operações são `dry_run=True` por padrão — o usuário precisa desmarcar
explicitamente para gravar no banco.

Campos obrigatórios por tipo:

| Tipo | Colunas mínimas |
|------|----------------|
| Transações | `data`, `descricao`, `valor` |
| Operações | `data`, `ticker`, `tipo`, `quantidade`, `preco_unitario` |
| Proventos | `data_pagamento`, `ticker`, `tipo_provento`, `valor_liquido` |

### ImportadorPostgres

Conexão somente-leitura nos bancos dos apps originais (SOURCE_DB_APP1/2/3).
Métodos:
- `listar_tabelas()` — introspecção das tabelas disponíveis
- `listar_colunas(tabela)` — introspecção das colunas
- `importar_tabela_generica(...)` — mapeamento de colunas configurável via UI
- `importar_app1/2/3_*` — placeholders para importação específica por app
  (requer auditoria dos schemas originais antes de implementar)

---

## Schema Setup

10 tabelas em ordem de dependência FK:

```
usuarios → contas → categorias → transacoes → orcamentos → metas
ativos → operacoes → proventos → cotacoes
```

Funções disponíveis:
- `verificar_schema()` → `dict[str, bool]` — presença de cada tabela
- `criar_schema()` → `dict` — executa DDL, retorna `{ok, criadas, ja_existiam, erros}`

Seguro para executar múltiplas vezes (todas as DDLs usam `IF NOT EXISTS`).

---

## Página de Configurações — 4 Abas

### 🗄️ Banco de Dados
- 4 badges de status: DATABASE_URL, conexão ativa, MOCK_MODE, OWNER_USER_ID
- Tabela de presença das 10 tabelas no schema
- Botão "Criar tabelas" (chama `criar_schema()`)

### 📥 Importação de Dados
- Sub-aba CSV/Excel: upload de arquivo, tipo (transações/operações/proventos),
  toggle dry_run, preview das primeiras linhas, botão de importação
- Sub-aba Banco de Origem: campo de URL, teste de conexão, listagem de tabelas,
  mapeamento de colunas, importação genérica com dry_run

### 🔒 Segurança
- Checklist de 7 pontos de segurança com status visual
- Botão de logout (encerra sessão autenticada)
- Gerador de hash SHA-256 para senha do APP_PASSWORD

### 📋 Setup
- Instruções SQL para criar o role `app4_reader` no Supabase
- Passo a passo para configurar OWNER_USER_ID
- Guia de configuração do arquivo `.env`

---

## Variáveis de Ambiente (adicionadas na Fase 4)

| Variável | Obrigatória | Descrição |
|----------|:-----------:|-----------|
| `APP_PASSWORD` | Não | Protege o app no Streamlit Cloud — texto ou hash SHA-256 |
| `OWNER_USER_ID` | Sim (banco real) | UUID do proprietário; filtro universal nas queries |
| `SOURCE_DB_APP1` | Não | Connection string do banco do App 1 (somente leitura) |
| `SOURCE_DB_APP2` | Não | Connection string do banco do App 2 (somente leitura) |
| `SOURCE_DB_APP3` | Não | Connection string do banco do App 3 (somente leitura) |

---

## MOCK_MODE Preservado

O `MOCK_MODE=true` continua sendo o padrão no `.env.example`.
Nenhuma página existente foi alterada.
O app inicializa e exibe todos os dados mockados normalmente enquanto o banco
não estiver configurado — zero regressão.

---

## Resultado dos Testes

| Teste | Resultado |
|-------|-----------|
| `python -m ruff check . --output-format=concise` | ✅ All checks passed! |
| `curl http://localhost:8502` | ✅ HTTP 200 |
| Inicialização com MOCK_MODE=true | ✅ Todos os módulos carregados |
| Import de `core.auth` | ✅ Sem erros |
| Import de `etl.schema_setup` | ✅ Sem erros |
| Import de `etl.importacao` | ✅ Sem erros |
| Import de `core.config` fora do contexto Streamlit | ✅ Sem erros — `_get_secret()` fallback OK |

---

## Fase 4.1 — Auditoria do Banco (✅ Concluída em 2026-05-13)

**Executada diretamente via SQLAlchemy (Python) com autorização do proprietário.**

**Resultado:**
- PostgreSQL 17.6 em `aws-1-sa-east-1.pooler.supabase.com`
- **14 tabelas existentes no schema `public`** — todas do App 1 (Dashboard Financeiro: análise fundamentalista, documentos CVM, dados econômicos)
- **0 conflitos de nome** com as 22 tabelas canônicas do App 4
- DM-012 resolvida: usar schema **`public`** (sem colisão com tabelas existentes)
- DM-001 resolvida: tabelas do App 4 criadas diretamente em inglês (as 10 tabelas em português do `schema_setup.py` nunca tinham sido aplicadas neste banco)
- Extensões disponíveis: `uuid-ossp`, `pgcrypto`, `vector` (pgvector), `pg_stat_statements`

**Tabelas existentes (App 1):** `Demonstracoes_Financeiras`, `Demonstracoes_Financeiras_TRI`, `cvm_to_ticker`, `docs_corporativos`, `docs_corporativos_chunks`, `info_economica`, `info_economica_mensal`, `multiplos`, `multiplos_TRI`, `patch6_runs`, `portfolio_snapshot_analysis`, `portfolio_snapshot_items`, `portfolio_snapshots`, `setores`

---

## Fase 4.2 — Modelo Canônico (✅ Concluída em 2026-05-13)

**Entregue em 2026-05-13.** Criado sem aguardar a Fase 4.1, com base em:
- `etl/schema_setup.py` — 10 tabelas existentes em português
- `ProjetoIA/05_Banco_de_Dados/modelagem_inicial.md` — modelo original do vault
- `docs/auditoria_dados_investimentos.md` — schema SQLite do App 2
- Especificação do proprietário: 22 tabelas canônicas em inglês

**Documentos entregues:**

| Arquivo | Conteúdo |
|---------|---------|
| `docs/banco_unificado_modelo_canonico.md` | 22 tabelas em 8 domínios — colunas, tipos, índices, RLS |
| `docs/banco_unificado_mapa_origem_destino.md` | Mapeamento completo de origens (App 1/2/3 + schema atual) → destino |
| `docs/banco_unificado_dicionario_dados.md` | Semântica de negócio por coluna; regras, invariantes, observações |
| `docs/banco_unificado_decisoes_modelagem.md` | 13 decisões documentadas com contexto, opções e justificativa |

**Resumo do modelo canônico:**

| Domínio | Tabelas | Observação |
|---------|---------|------------|
| Identidade | `profiles` | Renomeado de `usuarios` |
| Instituições | `financial_institutions` | Nova |
| Contas e Cartões | `accounts`, `cards` | `accounts` renomeado; `cards` nova |
| Finanças Pessoais | `categories`, `transactions`, `budgets`, `financial_goals`, `debts` | 4 renomeadas; `debts` nova |
| Investimentos | `assets`, `portfolios`, `portfolio_positions`, `investment_transactions`, `dividends` | 3 renomeadas; 2 novas |
| Dados de Mercado | `asset_quotes`, `benchmarks`, `benchmark_quotes` | 1 renomeada; 2 novas |
| Preferências | `alerts`, `user_settings` | Ambas novas |
| Controle | `import_batches`, `import_logs`, `migration_source_map` | Todas novas |

**Decisões pendentes de aprovação humana:**

| Decisão | Questão |
|---------|---------|
| DM-001 | Confirmar que nomes em inglês são a preferência para todas as 22 tabelas |
| DM-012 | Schema `public` vs. `app4` — aguarda resultado da Fase 4.1 (auditoria) |

**Critério para avançar para Fase 4.3:** proprietário aprova o modelo canônico e as decisões DM-001 / DM-012.

---

## Fase 4.4 — Revisão Humana dos Scripts SQL (✅ Concluída em 2026-05-13)

**Análise estática completa de todos os 8 scripts SQL da Fase 4.3.**
**Decisão: ⚠️ APROVADO COM AJUSTES — nenhum bloqueador para execução.**

| Categoria | Resultado |
|-----------|:---------:|
| Comandos destrutivos | ✅ Zero |
| Credenciais | ✅ Zero |
| Primary Keys (22/22) | ✅ |
| `user_id` em tabelas pessoais | ✅ |
| Lógica das views | ✅ |
| Problemas críticos | ✅ Zero |
| Problemas médios | ⚠️ 5 (não bloqueadores) |
| Problemas baixos | ⚠️ 5 (melhorias opcionais) |

**Documentação:**
- `docs/fase_4_4_revisao_humana_sql.md` — relatório completo com todos os achados
- `docs/fase_4_4_plano_correcao_sql.md` — SQL de correção dos 5 problemas médios (arquivo 009)

**Principais ajustes a aplicar antes da Fase 4.6 (via `009_schema_amendments.sql`):**
- M01: `transactions.account_id ON DELETE RESTRICT` (proteção contra órfãos)
- M02: FKs para `assets` com `ON DELETE RESTRICT`
- M03: Policy INSERT para `profiles` (para uso futuro via API)
- M04: Refatorar `categories_write_owner` para não sobrepor SELECT
- M05: Corrigir exemplo 'digital_bank' no 008 (não está no CHECK constraint)

---

## Fase 4.3 — Scripts SQL Não Destrutivos (✅ Concluída em 2026-05-13)

**8 arquivos SQL versionados, idempotentes e não destrutivos criados em `supabase_unificado/schema/`.**

| Arquivo | Conteúdo |
|---------|---------|
| `001_core_tables.sql` | `profiles`, `financial_institutions` |
| `002_financial_tables.sql` | `accounts`, `cards`, `categories`, `transactions`, `budgets`, `financial_goals`, `debts` |
| `003_investment_tables.sql` | `assets`, `portfolios`, `portfolio_positions`, `investment_transactions`, `dividends`, `asset_quotes`, `benchmarks`, `benchmark_quotes` |
| `004_import_migration_tables.sql` | `alerts`, `user_settings`, `import_batches`, `import_logs`, `migration_source_map` |
| `005_indexes.sql` | ~30 índices com `CREATE INDEX IF NOT EXISTS` |
| `006_rls_policies.sql` | Role `app4_reader`, RLS em 15 tabelas, 17 policies idempotentes |
| `007_views.sql` | 6 views analíticas: `v_account_balance`, `v_monthly_cashflow`, `v_category_spending_mtd`, `v_budget_usage_mtd`, `v_investment_summary`, `v_net_worth` |
| `008_seed_reference_data.sql` | 5 benchmarks + 23 categorias do sistema (`user_id = NULL`) |
| `README_EXECUCAO_SQL.md` | Guia de execução, checklist pré/pós, alertas de segurança |

**Validação textual (zero ocorrências em todos os 8 arquivos):**
`DROP TABLE` · `DROP SCHEMA` · `TRUNCATE` · `DELETE` · `DROP INDEX` · credenciais/connection strings

**Documentação:** `docs/fase_4_3_scripts_sql_nao_destrutivos.md`

> Scripts são idempotentes — seguros para executar múltiplas vezes.
> Executar no SQL Editor do Supabase em ordem numérica (001 → 008).

---

## Fase 4.5 — Aplicação do Schema (✅ Concluída em 2026-05-13)

**Aplicado diretamente via Python/SQLAlchemy com autorização explícita do proprietário.**
**70 operações executadas. 0 erros.**

| Operação | Qtd | Resultado |
|----------|:---:|:---------:|
| `CREATE TABLE IF NOT EXISTS` | 22 | ✅ Todas criadas |
| `CREATE INDEX IF NOT EXISTS` | 11 | ✅ Todos criados |
| `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` | 15 | ✅ RLS ativada |
| `CREATE POLICY` | 15 | ✅ Policies criadas |
| `CREATE ROLE app4_reader` | 1 | ✅ Role criado |
| `GRANT SELECT` (22 tabelas) | 1 | ✅ Concedido |
| `INSERT INTO benchmarks` (seed) | 5 | ✅ IBOVESPA, CDI, IPCA, SELIC, IFIX |

**Tabelas App 4 no banco agora (22/22):**
`profiles` · `financial_institutions` · `accounts` · `cards` · `categories` · `transactions` · `budgets` · `financial_goals` · `debts` · `assets` · `portfolios` · `portfolio_positions` · `investment_transactions` · `dividends` · `asset_quotes` · `benchmarks` · `benchmark_quotes` · `alerts` · `user_settings` · `import_batches` · `import_logs` · `migration_source_map`

---

## Fase 4.6 — Scripts de Migração Controlada (✅ Concluída em 2026-05-13)

**8 scripts Python ETL criados em `migration/`. Nenhuma migração real executada — tudo em `dry_run=True`.**

### Estrutura criada

| Arquivo | Responsabilidade |
|---------|----------------|
| `migration/__init__.py` | Pacote Python |
| `migration/00_config.py` | `MigrationConfig`, `make_engine()`, máscara de credenciais |
| `migration/01_extract_dashboard_financeiro.py` | Inspeciona 14 tabelas App 1 + 22 tabelas canônicas (somente-leitura) |
| `migration/02_extract_controle_financeiro.py` | Extrai App 3 (PostgreSQL): 5 tabelas; dry_run = só contagens |
| `migration/03_extract_investimentos_sqlite.py` | Extrai App 2 (SQLite): 8 tabelas; analisa cobertura de mapeamento |
| `migration/04_transform_to_canonical.py` | Renomeia colunas pt→en, padroniza datas ISO 8601, monetários NUMERIC(15,2) |
| `migration/05_load_to_unified_supabase.py` | Carga com `ON CONFLICT DO NOTHING`, registra `import_batches` / `migration_source_map` / `import_logs` |
| `migration/06_validate_migration.py` | 9 validações (contagens, somas, datas, duplicatas, user_id órfãos) |
| `migration/07_report_migration.py` | Gera `docs/fase_4_6_relatorio_migracao_planejada.md` |
| `migration/output/.gitkeep` | Pasta de saída (outputs gitignored) |

### Princípios de segurança implementados

| Princípio | Implementação |
|-----------|---------------|
| `dry_run=True` por padrão | Nenhum script altera dados sem `--no-dry-run` explícito |
| Fontes somente-leitura | Scripts 01/02/03 nunca fazem INSERT/UPDATE/DELETE |
| Idempotência | `migration_source_map` UNIQUE `(source, source_table, source_id)` + `ON CONFLICT DO NOTHING` |
| Rastreabilidade | Todo registro migrado gera entrada em `import_batches`, `import_logs`, `migration_source_map` |
| Credenciais mascaradas | `_mask()` exibe apenas os últimos 6 chars; `print_summary()` nunca imprime URLs completas |
| Contagem regressiva de 5s | Script 05 exibe countdown antes de carga real (`--no-dry-run`) |

### Mapeamentos implementados

**App 3 → Canônico (pt-BR → inglês):**
`transacoes→transactions`, `contas→accounts`, `categorias→categories`, `orcamentos→budgets`, `metas→financial_goals`

**App 2 (SQLite) → Canônico:**
`assets→assets`, `transactions→investment_transactions`, `incomes→dividends`, `xp_positions→portfolio_positions`, `institutions→financial_institutions`

### Variáveis necessárias para migração real

```ini
SUPABASE_UNIFICADO_URL="postgresql://postgres.<project>:<senha>@<host>:5432/postgres"
OWNER_USER_ID="<uuid-do-perfil-em-profiles>"
SUPABASE_ORIGEM_CONTROLE_URL="postgresql://..."   # App 3 (somente leitura)
SOURCE_DB_APP2="sqlite:///caminho/absoluto/investimentos.db"  # App 2
```

### Documentação gerada

| Documento | Conteúdo |
|-----------|---------|
| `docs/fase_4_6_scripts_migracao_controlada.md` | Visão completa: fontes, mapeamentos, execução, riscos |
| `docs/fase_4_6_relatorio_migracao_planejada.md` | Relatório de planejamento (estático — sem dados reais) |

### `.gitignore` atualizado

`migration/output/*.{csv,json,jsonl,parquet}` e `migration/output/transformed/*.{json,csv}` nunca são comitados.

---

## Fase 4.7 — Dry Run da Migração Controlada (✅ Concluída em 2026-05-14)

**Dois ciclos executados. Zero erros em ambos. Zero dados gravados.**

### Ciclo 1 — Dry Run Estrutural (sem credenciais)

| Bug | Gravidade | Descrição | Correção |
|-----|:---------:|-----------|---------|
| B01 | 🔴 Crítico | `migration/config.py` inexistente — todos os scripts falhavam na importação | Criado `migration/config.py` como módulo importável; `00_config.py` virou CLI wrapper |
| B02 | 🟡 Médio | Script 05 exigia credenciais mesmo em dry_run | Verificação de credenciais condicionada ao modo real |
| B03 | 🟢 Baixo | `f-string` sem placeholder (ruff F541) | Prefixo `f` removido |

### Ciclo 2 — Dry Run com Dados Reais (todas as credenciais configuradas)

| Bug | Gravidade | Descrição | Correção |
|-----|:---------:|-----------|---------|
| B04 | 🔴 Alto | Schema real do App3 diferente do documentado (colunas inglês, tabela `transactions`) | `02_extract_controle_financeiro.py` e `04_transform_to_canonical.py` reescritos para schema real |
| B05 | 🟡 Médio | Caminho SQLite com "Área de Trabalho" perdia encoding no PowerShell | `_load_dotenv()` adicionado ao `config.py` — lê `.env` em UTF-8 diretamente |
| B06 | 🟡 Médio | Transform buscava arquivos de tabelas antigas do App3 (ex: `02_app3_transacoes.json`) | Substituído por handler único para `02_app3_transactions.json` |

### Resultado do dry run com dados reais

```
Fontes disponíveis   : 3/3 (App2 SQLite + App3 Supabase + banco unificado)
App3 transactions    : 251 registros confirmados (colunas: id, type, category, date, amount, ...)
App2 SQLite          : 2.155 registros (assets 82, transactions 1.351, incomes 517, ...)
Validações           : 9/9 passaram (100%)
Total erros          : 0
Total warnings       : 3 (tabelas opcionais App2 — não bloqueadores)
RESULTADO            : PRONTO PARA MIGRAÇÃO REAL (após checklist)
```

### Confirmações de segurança

| Verificação | Resultado |
|-------------|:---------:|
| `dry_run=True` padrão em todos os scripts | ✅ |
| Script 05 não conecta ao banco em dry_run | ✅ |
| `make_engine()` não chamado nas fontes em dry_run | ✅ |
| `migration/output/` no `.gitignore` | ✅ |
| Zero dados escritos no banco unificado | ✅ |
| Zero dados alterados nas fontes | ✅ |

### Novo arquivo: `migration/run_dry_run.py`

Orquestrador que executa o pipeline completo (Steps 0–7) em dry_run, com `_assert_dry_run()` executado 3× e sumário final de fontes detectadas, warnings e decisão.

**Documentação:** `docs/fase_4_7_dry_run_migracao.md`

---

## Correção: Leitura de Secrets no Streamlit Cloud (2026-05-13)

**Problema:** o app lia variáveis de ambiente apenas via `os.getenv()` (`.env` local).
No Streamlit Cloud (Settings > Secrets), as variáveis ficavam invisíveis → banco não configurado.

**Solução aplicada:**

| Arquivo | Mudança |
|---------|---------|
| `core/config.py` | Adicionada `_get_secret(key)`: lê `st.secrets` → `os.environ` → default. Todos os `os.getenv()` da classe `Settings` substituídos. |
| `core/database.py` | SQLite compatibility: `pool_size`/`max_overflow`/`connect_args` omitidos em URLs `sqlite://` |
| `core/financeiro.py` | Mensagem de erro e docstring atualizadas para mencionar Streamlit Secrets |
| `etl/schema_setup.py` | Mensagem de erro atualizada |
| `etl/importacao.py` | Mensagem de erro atualizada |
| `pages/configuracoes.py` | 4 mensagens + docs de setup atualizados (Opção A `.env` + Opção B Secrets) |
| `pages/alertas.py` | Alerta de banco não configurado atualizado |
| `pages/dashboard_geral.py` | Mensagem de erro atualizada |
| `.env.example` | Header atualizado com nota sobre Streamlit Secrets |

**Comportamento resultante:**
- **Streamlit Cloud:** lê de `st.secrets` (Settings > Secrets) → banco configurado automaticamente
- **Dev local:** fallback para `os.getenv()` → `.env` continua funcionando normalmente
- **CLI / testes:** `try/except` captura a ausência de contexto Streamlit → usa `os.getenv()` sem erros

**Resultado ruff:** All checks passed!

---

## Próxima Subfase: 4.8 — Migração Real dos Dados

**Fase 4.7 concluída (dry run com dados reais aprovado).** Para executar a migração real (Fase 4.8), o checklist abaixo deve ser satisfeito:

1. **`009_schema_amendments.sql` aplicado no Supabase** (M01–M05 da Fase 4.4)
2. **Backup do banco unificado** — Supabase Dashboard → Settings → Database → Backups
3. **Revisar amostra** das 251 transações App3 em `migration/output/02_app3_transactions.json`
4. **Revisar amostra** dos ativos App2 em `migration/output/03_app2_assets.json`
5. **Confirmar categorias existentes** (23 em `categories`) vs categorias do App3
6. **Confirmar accounts existentes** (ou criar) para `account_id` das transactions

Após checklist: `python -m migration.05_load_to_unified_supabase --no-dry-run`

Ver checklist completo: `docs/fase_4_7_dry_run_migracao.md` (Seção 12).

---

## ⚠️ Contexto da Redefinição — Banco Supabase Unificado (plano gratuito)

> Atualização pós-criação de `docs/estrategia_supabase_unificado_plano_gratuito.md`

### Contexto da mudança

O plano gratuito do Supabase permite no máximo **2 projetos ativos**.
Os dois projetos já existentes são:
- **Dashboard Financeiro** (`finapp-prod` / `finapp-dev`) — projetado como agregador
- **Controle Financeiro** — transações, categorias, orçamentos

Criar um terceiro projeto para o App 4 não é possível sem upgrade para plano pago.

### Decisão D01 — Revisada

| | Antes | Depois |
|---|---|---|
| Projeto alvo | `finapp-dev` (novo projeto dedicado) | **Dashboard Financeiro** (projeto existente) |
| Justificativa | Isolamento total | Aproveitamento do plano gratuito; projeto já arquitetado como agregador |

### Estratégia adotada

**Opção A — Usar o projeto "Dashboard Financeiro" como banco unificado.**

- Schema próprio do App 4 criado via `CREATE SCHEMA IF NOT EXISTS app4`
- Coexistência segura com os dados dos apps Next.js
- Role `app4_reader` com `SELECT` apenas nas tabelas do App 4
- Projeto "Controle Financeiro" torna-se fonte de migração → depois staging do App 3

### Fases de execução (P0–P7)

| Fase | Nome | Ação |
|------|------|------|
| P0 | Backup | Export `.sql` de ambos os projetos antes de qualquer mudança |
| P1 | Auditoria de schema | Mapear tabelas existentes no Dashboard Financeiro |
| P2 | Criação do schema | `CREATE SCHEMA IF NOT EXISTS app4` + DDL 10 tabelas (sem DROP) |
| P3 | Migração Controle Financeiro | ETL somente-leitura do projeto Controle Financeiro |
| P4 | Migração SQLite investimentos | `SOURCE_DB_APP2` → `importar_app2_investimentos()` |
| P5 | Validação | Contagem de linhas, spot checks, testes de queries |
| P6 | Chaveamento gradual | `MOCK_MODE=false` + `SUPABASE_UNIFICADO_URL` configurado |
| P7 | Repropósito | Controle Financeiro vira staging exclusivo do App 3 |

**Regra inviolável:** nenhum `DROP TABLE`, `TRUNCATE` ou `DELETE` sem backup confirmado
e autorização manual explícita. Ver `docs/estrategia_supabase_unificado_plano_gratuito.md`.

### Variáveis de ambiente — nomes propostos

```ini
# Banco unificado (App 4 usa como destino)
SUPABASE_UNIFICADO_URL=""
SUPABASE_UNIFICADO_ANON_KEY=""
SUPABASE_UNIFICADO_SERVICE_ROLE_KEY=""   # somente local, nunca expor

# Fonte de migração (Controle Financeiro → leitura)
SUPABASE_ORIGEM_CONTROLE_URL=""
SUPABASE_ORIGEM_CONTROLE_ANON_KEY=""
```

Prioridade de `db_url` em `core/config.py` (a implementar):

```python
@property
def db_url(self) -> str:
    return (
        self.SUPABASE_UNIFICADO_URL
        or self.DATABASE_URL
        or self.SUPABASE_DB_URL
        or ""
    )
```

---

## Próximo Passo: Configurar Banco

Para ativar dados reais (sequência atualizada):

1. Executar backup `.sql` do projeto Dashboard Financeiro no Supabase (P0)
2. Auditar tabelas existentes no projeto Dashboard Financeiro (P1)
3. Criar schema `app4` e as 10 tabelas via aba "Banco de Dados" das Configurações (P2)
4. Configurar `.env` com `SUPABASE_UNIFICADO_URL` + `OWNER_USER_ID` + `MOCK_MODE=false`
5. Criar role `app4_reader` executando o SQL da aba "📋 Setup" das Configurações
6. Implementar `_visao_geral_real()` em `core/financeiro.py`

> Sequência completa: `docs/estrategia_supabase_unificado_plano_gratuito.md`

---

## Fase 5 — Módulo de Investimentos (planejada)

| Item | Arquivo | Dependência |
|------|---------|-------------|
| Wrapper yfinance | `core/cotacoes.py` | Decisão D02 (yfinance vs. API paga) |
| Custo médio + TWRR | `core/investimentos.py` | Fase 4 banco ativo |
| Carteira completa | `pages/carteira.py` | `core/investimentos.py` |
| Evolução + benchmark | `pages/investimentos.py` | `core/cotacoes.py` |
| Histórico de dividendos | `pages/proventos.py` | `core/investimentos.py` |
