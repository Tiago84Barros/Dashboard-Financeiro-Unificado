# Validação de Dados Reais — Fase 5.5

> Gerado em: 2026-05-15
> Versão: v0.5.10
> Python: 3.9 (py -3.9)
> Fase: **5.5 — Consolidação de Dados Reais**

---

## 1. Ambiente

### 1.1 Variáveis de ambiente (.env)

| Variável | Status | Observação |
|----------|:------:|------------|
| `SUPABASE_DB_URL` | ✅ Configurada | Usado via fallback `db_url` em `config.py` — funciona sem renomear |
| `SUPABASE_UNIFICADO_URL` | ❌ Ausente | Variável preferida, mas não obrigatória — fallback cobre |
| `OWNER_USER_ID` | ✅ Configurada | `5185e9d5-...` — corresponde a 100% das linhas filtradas |
| `MOCK_MODE` | ✅ `false` | Modo real ativo — adicionado nesta fase |
| `APP_PASSWORD` | ❌ Ausente | App abre sem senha (modo dev local) — não bloqueante |
| `OPENAI_API_KEY` | ❌ Ausente | Módulo IA não implementado (Fase 8) — não bloqueante |

### 1.2 Versão yfinance

| Pacote | Versão em uso | requirements.txt |
|--------|:-------------:|-----------------|
| yfinance | 0.2.66 | `>=0.2.50,<0.3.0` |

> **Atualizado nesta fase:** versão mínima elevada de `>=0.2.26` para `>=0.2.50`. A versão 0.2.26 usava endpoint depreciado do Yahoo Finance e retornava 0/82 tickers com dados. A versão 0.2.66 funciona corretamente.

---

## 2. Conexão ao Banco

| Verificação | Resultado |
|-------------|:---------:|
| `config.db_url` resolvido via | `SUPABASE_DB_URL` (fallback) |
| Engine SQLAlchemy criado | ✅ |
| `SELECT 1` (ping) | ✅ retornou `1` |
| Latência aproximada | ~1,3 s (pooler Supabase) |

---

## 3. Tabelas — Contagem de Registros

| Tabela | Filtro | Registros | Impacto funcional |
|--------|:------:|----------:|-------------------|
| `transactions` | `user_id` | 251 | ✅ Controle Financeiro com dados reais |
| `categories` | sem `user_id` | 38 | ✅ Categorias de referência — `user_id = NULL` sem impacto |
| `accounts` | `user_id` | 2 | ✅ Contas ativas; `initial_balance = 0` (saldo calculado por transações) |
| `assets` | público | 82 | ✅ Catálogo B3 + EUA — sem filtro de usuário |
| `investment_transactions` | `user_id` | 1.351 | ✅ Histórico completo migrado do App 2 |
| `dividends` | `user_id` | 517 | ✅ Proventos históricos completos |
| `portfolio_positions` | `user_id` | 34 | ✅ Posições calculadas por `08_compute_portfolio_positions.py` |
| `financial_goals` | `user_id` | 0 | ⚠️ Vazia — tela Metas retorna lista vazia (sem mock automático) |
| `budgets` | `user_id` | 0 | ⚠️ Vazia — alerta R5 ativo; orçamento implícito (gasto × 1,2) |
| `asset_quotes` | público | 714 | ✅ Populada nesta fase — 54/82 ativos com cotação |

### Detalhe: `accounts.initial_balance = 0`

Ambas as contas (`Conta Corrente` e `Cartão C6`) têm `initial_balance = 0,00`. O saldo bancário efetivo é calculado pelo app via diferença entre receitas e despesas históricas nas `transactions`. Valores históricos confirmados: receitas R$427.172,68 − despesas R$215.656,57 = **saldo calculado R$211.516,11**.

### Detalhe: `categories.user_id = NULL`

Todos os 38 registros foram migrados com `user_id = NULL`. O módulo `core/controle.py` faz a query sem filtro de usuário em categorias (categorias são dados de referência), portanto não há impacto funcional.

---

## 4. Views

| View | Registros | Status | Observação |
|------|----------:|:------:|------------|
| `v_monthly_cashflow` | 8 linhas | ✅ | Dados de cashflow mensal disponíveis |
| `v_budget_usage_mtd` | 0 linhas | ⚠️ | `budgets` vazia → view retorna 0 linhas |

---

## 5. Módulos — data_source por Tela

Todos os módulos chamados com `MOCK_MODE=false` ativo no `.env`.

| Tela | Módulo core | data_source | Confirmação |
|------|-------------|:-----------:|-------------|
| Dashboard Geral | `core.financeiro.get_visao_geral` | **real** | ✅ |
| Carteira | `core.investimentos.get_carteira` | **real** | ✅ — 34 posições |
| Investimentos | `core.investimentos.get_cashflow_mensal` | **real** | ✅ — 8 meses de cashflow |
| Proventos | `core.proventos.get_proventos` | **real** | ✅ — 517 eventos |
| Controle Financeiro | `core.controle.get_controle` | **real** | ✅ — 251 transações |
| Metas | `core.metas.get_metas` | **real** | ✅ — retorna lista vazia (`financial_goals` = 0) |
| Alertas | `core.alertas.get_alertas` | **real** | ✅ |
| Empresas B3 | `core.empresas.get_ativos` | **real** | ✅ — 82 ativos |

**Nenhum módulo caiu em `mock_fallback`.**

---

## 6. Cotações (asset_quotes) — Resultado do Update

Script executado: `py -3.9 scripts/update_asset_quotes.py --periodo 1mo --apenas-sem-cotacao`

| Métrica | Valor |
|---------|------:|
| Ativos processados | 82 |
| Sucesso (com dados) | 54 |
| Sem dados (yfinance vazio) | 28 |
| Erros de execução | 0 |
| Pontos inseridos | 714 |
| Última data | 2026-05-14 |

### Ativos sem cotação (28)

Tickers que retornaram `SEM DADOS` do yfinance (tickers delistados, fundos fechados ou nomes não mapeados no Yahoo Finance):

`IRDM11.SA`, `BCFF11.SA`, `FAMB11B.SA`, `FIIP11B.SA`, `CDB`, `DEB`, `MXRF15.SA`, `BBAS3F.SA`, `PETR3F.SA`, `EQTL3F.SA`, `TRPL3F.SA`, `ITUB3F.SA`, `CSMG3F.SA`, `BRAP3F.SA` e outros (28 no total).

> Tickers com sufixo `F` (BBAS3F, PETR3F, etc.) são fracionários do mercado de balcão — não possuem cotação própria no Yahoo Finance. O preço de referência é o ticker-base sem `F`.

### Impacto nos cálculos

- **28 ativos sem cotação:** usam `preco_atual = preco_medio` → rentabilidade individual = 0%
- **54 ativos com cotação:** rentabilidade real calculada corretamente
- **Rentabilidade total da carteira:** +12,64% (base nos 34 ativos da `portfolio_positions`, com 28 com cotação e 6 usando preço médio)

---

## 7. Reconciliação de Dados

### 7.1 Contas

| Conta | initial_balance |
|-------|---------------:|
| Cartão C6 | R$ 0,00 |
| Conta Corrente | R$ 0,00 |
| **Saldo calculado (histórico)** | **R$ 211.516,11** |

> `initial_balance = 0` é esperado para ambas as contas. O saldo bancário correto é obtido pela diferença entre receitas e despesas históricas em `transactions`.

### 7.2 Transações Financeiras

| Métrica | Valor |
|---------|------:|
| Total de transações (histórico) | 251 |
| Receitas — mês atual (mai/2026) | R$ 34.672,42 |
| Despesas — mês atual (mai/2026) | R$ 13.925,40 |
| Saldo líquido — mês atual | R$ 20.747,02 |
| Receitas históricas totais | R$ 427.172,68 |
| Despesas históricas totais | R$ 215.656,57 |
| **Saldo histórico calculado** | **R$ 211.516,11** |

### 7.3 Carteira de Investimentos

| Métrica | Valor |
|---------|------:|
| Posições ativas | 34 |
| Ativos com cotação yfinance | 28 |
| Ativos sem cotação (preço médio) | 6 |
| Total investido (custo histórico) | R$ 193.557,86 |
| Total mercado (valor atual) | R$ 218.028,77 |
| **Rentabilidade total** | **+12,64%** |

### 7.4 Proventos

| Métrica | Valor |
|---------|------:|
| Total de eventos | 517 |
| **Total histórico recebido** | **R$ 114.144,19** |

### 7.5 Patrimônio Total Estimado

| Componente | Valor |
|------------|------:|
| Carteira (valor de mercado) | R$ 218.028,77 |
| Saldo bancário estimado | R$ 211.516,11 |
| Proventos (já realizados, não duplicar) | — |
| **Total estimado** | **R$ 429.544,88** |

> O saldo bancário estimado é calculado como `SUM(receitas) - SUM(despesas)` nas `transactions`. É uma aproximação — não reflete resgates ou transferências para investimentos que possam estar registrados em ambas as tabelas.

---

## 8. Pendências — Budgets (T10)

A tabela `budgets` está **vazia** (0 registros para o usuário).

**Impacto atual:**

| Item | Situação |
|------|---------|
| `v_budget_usage_mtd` | Retorna 0 linhas — view depende de `budgets` |
| Alerta R5 | **Ativo** — "Sem orçamentos cadastrados" |
| Alerta R1 | Inativo — não pode disparar sem orçamentos |
| Tela Controle Financeiro | Exibe orçamento implícito calculado como `gasto × 1,2` |

**Como resolver:** Configurações → aba Controle → cadastrar limites mensais por categoria.

---

## 9. Pendências — Financial Goals (T11)

A tabela `financial_goals` está **vazia** (0 registros para o usuário).

**Impacto atual:**

| Item | Situação |
|------|---------|
| Tela Metas | Exibe mensagem "Nenhuma meta cadastrada" (sem fallback mock) |
| Alertas R2, R3 | Inativos — dependem de metas com `target_amount` e `target_date` |

**Como resolver:** Tela Metas → formulário "Nova Meta" → cadastrar objetivos financeiros.

---

## 10. Recomendações — Próximos Passos

### Imediatos

| # | Ação | Impacto | Como |
|---|------|---------|------|
| 1 | Cadastrar orçamentos mensais | Elimina alerta R5; orçamento por categoria real | Controle Financeiro → aba Orçamento |
| 2 | Cadastrar metas financeiras | Tela Metas exibe dados reais | Metas → "Nova Meta" |
| 3 | Corrigir tickers fracionários | 28 ativos sem cotação mostram rentab=0% | Mapear `BBAS3F→BBAS3`, `PETR3F→PETR3`, etc. em `assets.ticker` ou criar lógica de fallback |
| 4 | Verificar contas | `initial_balance = 0` é esperado; confirmar se saldo calculado (R$211.516) é coerente | Cruzar com extrato bancário real |

### Fase 6 (próxima)

| Etapa | Descrição |
|-------|-----------|
| 6.1 | DDL `010_cards_schema.sql` — tabelas `cards`, `card_bills`, `card_transactions` |
| 6.2 | Implementar `core/cartao.py` + `views/cartao.py` |
| 6.3 | Implementar `core/ir.py` + `views/ir.py` (ganho de capital, DARF) |
| 6.4 | Adicionar rotas em `app.py` |

---

## Resumo Executivo

| Verificação | Resultado |
|-------------|:---------:|
| MOCK_MODE=false ativo | ✅ |
| Conexão ao banco | ✅ via `SUPABASE_DB_URL` |
| Todos os módulos retornam `data_source=real` | ✅ |
| Nenhum módulo em `mock_fallback` | ✅ |
| asset_quotes populado | ✅ 714 linhas / 54 ativos |
| Carteira com rentabilidade real | ✅ +12,64% / R$218.028,77 |
| Proventos históricos confirmados | ✅ 517 eventos / R$114.144,19 |
| Transações confirmadas | ✅ 251 registros |
| investment_transactions confirmadas | ✅ 1.351 registros |
| yfinance atualizado (0.2.66) | ✅ |
| Tabelas críticas com dados | ✅ (exceto `budgets` e `financial_goals`) |
| **Fase 5.5 concluída** | ✅ |

---

*Ver também: [`docs/validacao_modo_real.md`](validacao_modo_real.md) · [`docs/status_atual_implementacao.md`](status_atual_implementacao.md) · [`README.md`](../README.md)*
