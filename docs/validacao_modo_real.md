# Validação — Modo Real (MOCK_MODE=false)

> Gerado em: 2026-05-14
> Versão: v0.5.10
> Python: 3.9 (pacotes em `C:\...\Python39\lib\site-packages`)

---

## 1. Variáveis de Ambiente (.env)

| Variável | Status | Observação |
|----------|:------:|------------|
| `SUPABASE_UNIFICADO_URL` | ❌ Ausente | Variável preferida — não configurada |
| `SUPABASE_DB_URL` | ✅ Configurada | Nome legado, aceito via fallback `db_url` em `config.py` |
| `DATABASE_URL` | ❌ Ausente | Alternativa intermediária — não necessária |
| `OWNER_USER_ID` | ✅ Configurada | `5185e9d5-...` |
| `APP_PASSWORD` | ❌ Ausente | App abre sem senha (modo dev local) — não bloqueante |
| `MOCK_MODE` | ❌ Ausente | Padrão assumido pelo `config.py`: `"true"` → **app está em mock** |

### Ação necessária

Adicionar ao `.env`:
```env
MOCK_MODE=false
```

> O `SUPABASE_DB_URL` existente já é suficiente — o `config.py` usa a prioridade
> `SUPABASE_UNIFICADO_URL → DATABASE_URL → SUPABASE_DB_URL`. Não é preciso renomear.

---

## 2. Teste de Conexão

| Verificação | Resultado |
|-------------|:---------:|
| `config.db_url` resolvido via | `SUPABASE_DB_URL` (fallback) |
| Engine SQLAlchemy criado | ✅ |
| `SELECT 1` (ping) | ✅ retornou `1` |
| Latência aproximada | ~1,3 s (pooler Supabase) |

---

## 3. Tabelas Principais — Contagem de Registros

| Tabela | Filtro | Registros | Status |
|--------|:------:|----------:|:------:|
| `transactions` | `user_id` | 251 | ✅ |
| `categories` | sem `user_id` | 38 | ✅ — coluna `user_id` existe mas é `NULL` em todos os registros |
| `accounts` | `user_id` | 2 | ✅ |
| `assets` | público | 82 | ✅ |
| `investment_transactions` | `user_id` | 1.351 | ✅ |
| `dividends` | `user_id` | 517 | ✅ |
| `portfolio_positions` | `user_id` | 34 | ✅ |
| `financial_goals` | `user_id` | 0 | ⚠️ Vazia — tela Metas usa fallback mock |
| `budgets` | `user_id` | 0 | ⚠️ Vazia — orçamento implícito (×1,2) |
| `asset_quotes` | público (sem `user_id`) | 0 | ⚠️ Vazia — rentabilidade exibirá 0% |

### Detalhe: `categories.user_id = NULL`

Todos os 38 registros de categorias foram migrados com `user_id = NULL`. O módulo `core/controle.py` faz a query sem filtro de usuário em categorias (correto, pois são dados de referência), portanto **não há impacto funcional**. A tela Controle Financeiro recebe as 38 categorias normalmente.

---

## 4. Views

| View | Registros | Status |
|------|----------:|:------:|
| `v_monthly_cashflow` | 8 linhas | ✅ Existe e retorna dados |
| `v_budget_usage_mtd` | 0 linhas | ⚠️ Existe mas vazia — `budgets` está vazia |

---

## 5. data_source por Tela (MOCK_MODE=false)

Todos os módulos foram chamados com `MOCK_MODE=false` forçado via `os.environ`.

| Tela | Módulo core | data_source | Dados confirmados |
|------|------------|:-----------:|-------------------|
| Dashboard Geral | `core.financeiro.get_visao_geral` | **real** | ✅ |
| Carteira | `core.investimentos.get_carteira` | **real** | ✅ — 34 ativos |
| Proventos | `core.proventos.get_proventos` | **real** | ✅ — 517 eventos |
| Controle Financeiro | `core.controle.get_controle` | **real** | ✅ — 251 transações |
| Metas | `core.metas.get_metas` | **real** | ✅ — retorna lista vazia (`financial_goals` = 0) |
| Alertas | `core.alertas.get_alertas` | **real** | ✅ |
| Empresas B3 | `core.empresas.get_ativos` | **real** | ✅ — 82 ativos |

**Nenhum módulo caiu em `mock_fallback`.** O padrão mock/real/fallback funciona corretamente — a conexão ao banco é estável o suficiente para não acionar o fallback.

---

## 6. Alertas Automáticos Esperados (MOCK_MODE=false)

Com os dados atuais do banco, os seguintes alertas serão ativados automaticamente:

| Alerta | Regra | Motivo |
|--------|-------|--------|
| R4 — Cotações ausentes | `asset_quotes COUNT = 0` | Tabela vazia → rentabilidade = 0% |
| R5 — Sem orçamentos | `budgets COUNT = 0` | Tabela vazia → sem limites configurados |

Os alertas R1 (orçamento estourado), R2 (meta próxima), R3 (prazo de meta), R6 (saldo negativo) dependerão dos dados reais de transações e metas.

---

## 7. Ação para Ativar MOCK_MODE=false

**Uma única linha a adicionar no `.env`:**

```env
MOCK_MODE=false
```

Não é necessário renomear `SUPABASE_DB_URL` nem configurar `SUPABASE_UNIFICADO_URL` — o fallback já está funcionando.

### Após adicionar MOCK_MODE=false

1. Reiniciar o app: `streamlit run app.py`
2. Verificar que a sidebar não mostra o aviso "MOCK_MODE=true — dados mockados em uso"
3. Ir em **Configurações → aba Cotações → Atualizar Cotações** para preencher `asset_quotes`
4. Opcionalmente cadastrar orçamentos mensais para eliminar o alerta R5

---

## 8. Pendências Não Bloqueantes

| Item | Impacto | Como resolver |
|------|---------|---------------|
| `asset_quotes` vazia | Rentabilidade = 0% em Carteira e Investimentos | Configurações → Cotações → Atualizar |
| `budgets` vazia | Orçamentos implícitos (gasto × 1,2); alerta R5 ativo | Cadastrar via Controle Financeiro |
| `financial_goals` vazia | Tela Metas mostra lista vazia (sem fallback mock visível) | Cadastrar via tela Metas |
| `categories.user_id = NULL` | Nenhum (sem impacto funcional — app não filtra por user_id em categorias) | Nenhuma ação necessária |
| `APP_PASSWORD` ausente | App abre sem senha — adequado para uso local | Configurar se for expor em rede |
| Python 3.9 sem venv | Pacotes instalados globalmente no Python 3.9 | Criar `.venv` para isolar dependências |

---

## 9. Resumo Executivo

| Verificação | Resultado |
|-------------|:---------:|
| Conexão ao banco | ✅ Funciona via `SUPABASE_DB_URL` |
| MOCK_MODE ativo agora | ⚠️ `true` (variável ausente no .env → padrão) |
| Dados reais disponíveis | ✅ 1.351 transações invest. + 517 proventos + 251 transações + 34 posições + 82 ativos |
| Todos módulos retornam `data_source=real` | ✅ Quando `MOCK_MODE=false` |
| Nenhum módulo cai em `mock_fallback` | ✅ |
| Views necessárias existem | ✅ `v_monthly_cashflow` + `v_budget_usage_mtd` |
| Tabelas críticas com dados | ✅ (exceto `asset_quotes`, `budgets`, `financial_goals`) |
| **Para ativar modo real** | **Adicionar `MOCK_MODE=false` no `.env`** |

---

*Ver também: [`docs/status_atual_implementacao.md`](status_atual_implementacao.md) · [`README.md`](../README.md)*
