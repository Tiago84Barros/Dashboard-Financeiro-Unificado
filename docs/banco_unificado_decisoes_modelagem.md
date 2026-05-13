# Banco Unificado — Decisões de Modelagem

> Documento: `docs/banco_unificado_decisoes_modelagem.md`
> Criado em: 2026-05-13
> Fase: 4.2 — Modelo Canônico
> Status: Registro permanente — atualizar a cada nova decisão
> Propósito: documentar o raciocínio por trás de cada escolha estrutural do schema.

---

## Convenção de Registro

Cada decisão tem:
- **ID**: identificador único (DM-NNN)
- **Status**: `aprovada` · `pendente` · `revisada`
- **Impacto**: `alto` · `médio` · `baixo`
- **Contexto**: por que a questão surgiu
- **Opções consideradas**: alternativas avaliadas
- **Decisão**: o que foi escolhido e por quê
- **Consequências**: o que esta decisão implica

---

## DM-001 — Nomes de Tabelas em Inglês

**Status:** `pendente aprovação`
**Impacto:** `alto`
**Data:** 2026-05-13

**Contexto:**
O schema atual (`etl/schema_setup.py`) usa nomes em português (`usuarios`, `contas`, `transacoes`).
O usuário especificou as 22 tabelas do modelo canônico com nomes em inglês
(`profiles`, `accounts`, `transactions`). Há inconsistência a resolver.

**Opções consideradas:**

| Opção | Prós | Contras |
|-------|------|---------|
| Manter português em tudo | Sem renomeação; sem migração de código | Inconsistência com os 12 novos domínios; dificulta interoperabilidade |
| Inglês para todas (modelo canônico) | Consistência; padrão PostgreSQL/Supabase | Requer renomeação das 10 tabelas existentes e atualização do código |
| Híbrido (português para antigas, inglês para novas) | Sem renomeação imediata | Inconsistência permanente; confusão de nomenclatura |

**Decisão:** **Inglês para todas as 22 tabelas** (conforme especificação do usuário).

A renomeação é segura (`ALTER TABLE RENAME`) e não destrutiva.
O banco não tem dados reais (MOCK_MODE=true) — janela ideal para a mudança.
Após a migração de dados (Fase 4.7) não é possível renomear sem impacto.

**Consequências:**
- `etl/schema_setup.py` → atualizar `_DDL` e `TABELAS_ESPERADAS` (Fase 4.9)
- `etl/importacao.py` → atualizar `_TABELAS_VALIDAS`
- `core/financeiro.py` → atualizar todas as queries SQL inline
- Scripts DDL da Fase 4.3 já usarão os nomes em inglês
- Renomeação das 10 tabelas existentes incluída no script DDL da Fase 4.5

---

## DM-002 — Nomes de Colunas em Inglês

**Status:** `aprovada`
**Impacto:** `alto`
**Data:** 2026-05-13

**Contexto:**
Consequência direta de DM-001. Se as tabelas terão nomes em inglês, as colunas devem seguir.

**Decisão:** Inglês para todos os nomes de colunas.

Mapeamento definido em `docs/banco_unificado_mapa_origem_destino.md` (Seção 2).

**Consequências:**
- `usuario_id` → `user_id` em todas as tabelas (afeta código de RLS e queries)
- `criado_em` → `created_at` (afeta `ORDER BY` e filtros de data)
- `valor` → `amount` (semântica mais precisa)
- `tipo` → `type` (padronizado)
- `ativo`/`ativa` → `active` (padronizado)

---

## DM-003 — 22 Tabelas em Vez de 10

**Status:** `aprovada`
**Impacto:** `alto`
**Data:** 2026-05-13

**Contexto:**
O schema inicial tinha 10 tabelas baseadas em `modelagem_inicial.md` do vault Obsidian.
O modelo canônico expande para 22 tabelas para suportar todos os domínios do App 4.

**Tabelas adicionadas e justificativa:**

| Tabela | Justificativa |
|--------|--------------|
| `financial_institutions` | Centralizar bancos/corretoras evita duplicação em `accounts` e `cards`; permite enriquecer com CNPJ e código COMPE |
| `cards` | Cartões de crédito têm regras próprias (limite, vencimento, fechamento) que não cabem em `accounts` |
| `debts` | Dívidas e financiamentos são entidades financeiras distintas de transações; precisam de saldo devedor e juros |
| `portfolios` | Carteiras nomeadas permitem estratégias separadas (pessoal vs. previdência vs. especulativa) |
| `portfolio_positions` | Posição atual por ativo é dado derivado mas necessário para performance — evitar recalcular N operações a cada tela |
| `benchmarks` | Comparação com CDI/IBOVESPA é funcionalidade core de qualquer dashboard de investimentos |
| `benchmark_quotes` | Série histórica de benchmarks separada de cotações de ativos — tipos diferentes de dado |
| `alerts` | Alertas de preço/orçamento são funcionalidade de valor; entidade própria para flexibilidade |
| `user_settings` | Preferências do usuário precisam de persistência; evita `st.session_state` volátil |
| `import_batches` | Rastrear lotes de importação é necessário para auditoria e idempotência |
| `import_logs` | Log por registro permite diagnóstico de problemas de migração |
| `migration_source_map` | Mapa origem→destino é a chave de idempotência — evita duplicação em reexecuções |

**Consequências:**
- Schema mais complexo → mais scripts DDL na Fase 4.3
- Mais tabelas a rastrear em `verificar_schema()`
- Tabelas novas não têm dados de origem — requerem seed ou população manual

---

## DM-004 — Separação de `investment_transactions` e `transactions`

**Status:** `aprovada`
**Impacto:** `alto`
**Data:** 2026-05-13

**Contexto:**
Alternativa seria unificar todas as movimentações em uma única tabela `transactions`
com discriminador de tipo.

**Opções consideradas:**

| Opção | Prós | Contras |
|-------|------|---------|
| Tabela única com tipo | Queries de saldo total simplificadas | Colunas específicas de investimento ficam nulas em finanças pessoais; schema poluído |
| Tabelas separadas (escolhida) | Schema limpo; cada tabela otimizada para seu domínio | Queries de visão geral precisam de UNION |

**Decisão:** Tabelas separadas.

`transactions` modela receitas/despesas/transferências pessoais.
`investment_transactions` modela compras/vendas de ativos com preço, quantidade e taxas.
Os domínios têm semânticas e colunas completamente diferentes.

**Consequências:**
- `core/financeiro.py` precisa de UNION para dashboard de patrimônio total
- Dashboard de investimentos lê apenas `investment_transactions`
- Dashboard financeiro pessoal lê apenas `transactions`

---

## DM-005 — `amount` Signed vs. Coluna de Tipo

**Status:** `aprovada`
**Impacto:** `médio`
**Data:** 2026-05-13

**Contexto:**
Duas abordagens para representar receita/despesa:
1. `amount` sempre positivo + coluna `type` = `income`/`expense`
2. `amount` signed: positivo = receita, negativo = despesa

**Opções consideradas:**

| Opção | Prós | Contras |
|-------|------|---------|
| `amount` + `type` | Valores sempre positivos; mais legível | Saldo exige SUM condicional: `SUM(CASE WHEN type='income' THEN amount ELSE -amount END)` |
| `amount` signed (escolhida) | Saldo = `SUM(amount)` direto; padrão contábil | Valores negativos podem confundir visualizações |

**Decisão:** `amount` signed (herdado de `modelagem_inicial.md` e `etl/schema_setup.py`).

A coluna `type` é mantida para filtragem e classificação, mas `amount` determina o sinal.
**Invariante obrigatória:** despesas devem ter `amount < 0`; receitas devem ter `amount > 0`.
Transferências de saída têm `amount < 0`; de entrada têm `amount > 0`.

**Consequências:**
- Queries de saldo: `SUM(amount) WHERE user_id = :owner_id` — simples e eficiente
- ETL deve garantir que despesas importadas do App 3 tenham `amount` negativo
- `ABS(amount)` para exibição de valores de despesa na UI

---

## DM-006 — `financial_institution_id` em `accounts` é Nullable

**Status:** `aprovada`
**Impacto:** `médio`
**Data:** 2026-05-13

**Contexto:**
Dados históricos do App 3 (Controle Financeiro) têm o campo `banco` como string livre,
não como FK para uma tabela de instituições. A migração não pode mapear todos os valores.

**Decisão:** `financial_institution_id` em `accounts` é NULLABLE.

Permite migrar todos os dados históricos imediatamente sem criar instituições falsas.
Após a migração, o usuário pode vincular manualmente as contas às instituições.

**Consequências:**
- A coluna `banco` (string) da tabela atual é substituída por `financial_institution_id`
- Dados migrados do App 3 terão `financial_institution_id = NULL` inicialmente
- UI deve exibir "Banco não vinculado" quando NULL, com opção de vincular

---

## DM-007 — `portfolio_id` em `investment_transactions` é Nullable

**Status:** `aprovada`
**Impacto:** `médio`
**Data:** 2026-05-13

**Contexto:**
O App 2 (SQLite) não tem conceito de carteira nomeada — todas as operações são planas.
Tornar `portfolio_id` obrigatório bloquearia a migração.

**Decisão:** `portfolio_id` é NULLABLE em `investment_transactions`.

Na migração do App 2, criar uma carteira padrão "Carteira App 2" e associar todos
os dados históricos a ela. Novos registros podem (ou não) ter carteira associada.

**Consequências:**
- Queries de carteira devem tratar NULL (todas as operações sem carteira)
- Dashboard de posições totais: `WHERE portfolio_id IS NULL OR portfolio_id = :id`

---

## DM-008 — `portfolio_positions` como Tabela de Posição Atual

**Status:** `aprovada`
**Impacto:** `médio`
**Data:** 2026-05-13

**Contexto:**
A posição atual pode ser calculada dinamicamente (`SUM(quantity) WHERE type='buy' - SUM(quantity) WHERE type='sell'`),
mas é lenta para carteiras grandes.

**Opções consideradas:**

| Opção | Prós | Contras |
|-------|------|---------|
| Calcular dinamicamente | Sempre precisa; sem redundância | Lento para N operações; impraticável em tempo real |
| Tabela de posição (escolhida) | Performance; diretamente consultável | Pode ficar defasada; precisa de atualização |
| View materializada | Automático; sem código extra | Supabase free não suporta REFRESH automático |

**Decisão:** Tabela `portfolio_positions` atualizada pelo ETL a cada importação.

O App 4 tem dados históricos estáticos (não é trading em tempo real).
A tabela é atualizada quando: (a) nova operação é importada; (b) usuário clica "recalcular posições".

**Consequências:**
- ETL de investimentos precisa recalcular `portfolio_positions` após cada lote
- `updated_at` permite saber quando a posição foi calculada pela última vez
- Posições importadas do SQLite `xp_positions` e `position_snapshots` usam o snapshot mais recente

---

## DM-009 — `migration_source_map` como Garantia de Idempotência

**Status:** `aprovada`
**Impacto:** `alto`
**Data:** 2026-05-13

**Contexto:**
A migração de dados pode ser executada múltiplas vezes (falha parcial, nova fonte,
dados adicionais). Sem controle de idempotência, registros seriam duplicados.

`ON CONFLICT DO NOTHING` ajuda para duplicatas dentro do mesmo lote, mas não
resolve a questão de reexecuções completas em tabelas sem UNIQUE natural.

**Decisão:** Tabela `migration_source_map` com UNIQUE em `(source, source_table, source_id)`.

Antes de inserir, o ETL verifica se `source_id` já foi migrado.
Se sim → `action = skipped`. Se não → inserir + registrar o mapeamento.

**Consequências:**
- Cada script de migração precisa consultar `migration_source_map` antes de inserir
- `import_logs.action = 'skipped'` cobre tanto duplicatas de constraint quanto de mapeamento
- A tabela cresce junto com o volume de dados migrados, mas é leve (apenas UUIDs e strings)

---

## DM-010 — Não Usar TimescaleDB em `asset_quotes`

**Status:** `aprovada`
**Impacto:** `baixo`
**Data:** 2026-05-13

**Contexto:**
`modelagem_inicial.md` do vault menciona TimescaleDB para `cotacoes` (séries temporais).
O Supabase free não oferece TimescaleDB.

**Decisão:** Usar PostgreSQL puro com índice `(asset_id, timestamp DESC)`.

Para o volume de dados do App 4 (dados pessoais de 1 usuário), o índice nativo é suficiente.
Queries típicas buscam os últimos 252 dias (1 ano de pregão) de um conjunto fixo de tickers.

**Consequências:**
- Sem necessidade de extensão adicional
- `ORDER BY timestamp DESC LIMIT N` funciona com o índice
- Se o volume crescer além de ~10M registros, reavaliar particionamento manual por ano

---

## DM-011 — `user_settings` como Linha Única por Usuário

**Status:** `aprovada`
**Impacto:** `baixo`
**Data:** 2026-05-13

**Contexto:**
Alternativas: (a) tabela chave-valor (`key`, `value`); (b) linha única com colunas fixas; (c) JSONB.

**Decisão:** Linha única por usuário com colunas fixas + campo `extra_settings JSONB`.

Para App 4 single-user, a linha única é a abordagem mais simples.
Configurações futuras que não justificam nova coluna vão para `extra_settings`.

**Consequências:**
- SELECT/UPDATE por `user_id` — sempre retorna 1 linha
- Novas preferências simples → `extra_settings`; preferências estruturadas → nova coluna

---

## DM-012 — Schema `public` em Vez de Schema `app4`

**Status:** `pendente revisão`
**Impacto:** `alto`
**Data:** 2026-05-13

**Contexto:**
O `docs/status_fase_4.md` (seção de redefinição) menciona criar um schema `app4`
dentro do projeto Supabase Dashboard Financeiro, para coexistir com dados dos apps Next.js.

**Opções consideradas:**

| Opção | Prós | Contras |
|-------|------|---------|
| Schema `app4` | Isolamento; sem conflito de nomes | Supabase free tem limitações em search_path; mais complexidade de configuração |
| Schema `public` (preferência atual) | Padrão Supabase; RLS funciona out-of-the-box; sem configuração extra | Se houver tabelas Next.js no mesmo projeto, pode haver conflito de nomes |

**Decisão pendente:** confirmar na **Fase 4.1** se o banco Dashboard Financeiro já tem
tabelas em `public`. Se houver conflito de nomes → usar schema `app4`.
Se `public` estiver vazio → usar `public` (mais simples).

**Consequências se `app4`:**
- `set search_path = app4, public` no início de cada sessão (ou na DATABASE_URL)
- Scripts DDL usam `CREATE TABLE IF NOT EXISTS app4.tabela`
- `etl/schema_setup.py` precisa incluir o prefixo de schema

**Consequências se `public`:**
- Sem mudança na configuração atual
- Risco de conflito se o projeto tiver tabelas Next.js — a ser verificado na Fase 4.1

---

## DM-013 — `NUMERIC(15,2)` para Valores Monetários

**Status:** `aprovada`
**Impacto:** `baixo`
**Data:** 2026-05-13

**Contexto:**
Herdado de `modelagem_inicial.md`. Alternativas: `FLOAT`, `DECIMAL`, `INTEGER` (centavos).

**Decisão:** `NUMERIC(15,2)` para BRL/USD; `NUMERIC(15,6)` para preços de ativos; `NUMERIC(18,8)` para quantidades de cripto.

`NUMERIC` é exato — sem erros de ponto flutuante que afetam cálculos financeiros.
`15,2` → até R$ 9,999,999,999,999.99 (suficiente para patrimônio individual).
`15,6` → 6 casas decimais para preços de ativos fracionários (fundos, cripto).
`18,8` → 8 casas decimais para quantidades de Bitcoin/Ethereum.

**Consequências:**
- Todos os cálculos financeiros em Python devem usar `Decimal`, não `float`
- `pandas` + `dtype='float64'` pode perder precisão em somas grandes — usar `Decimal` ou `round(2)`

---

## Pendências de Decisão

| ID | Questão | Bloqueador |
|----|---------|------------|
| DM-001 | Aprovação humana do uso de nomes em inglês | Aguarda resposta do proprietário |
| DM-012 | Schema `public` vs. `app4` | Aguarda resultado da Fase 4.1 (auditoria) |

---

## Histórico

| Data | ID | Mudança |
|------|-----|---------|
| 2026-05-13 | DM-001 a DM-013 | Documento criado — 13 decisões registradas para o modelo canônico |
