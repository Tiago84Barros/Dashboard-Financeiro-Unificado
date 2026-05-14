# Fase 4.6 — Relatório de Migração Planejada

**Gerado em:** 2026-05-13 (planejamento — sem execução real)
**Modo:** 🔵 dry_run (planejamento — nenhuma migração executada)
**MOCK_MODE:** true (app continua em modo mock)

---

## 1. Resumo Executivo

Este relatório documenta o **plano de migração** dos dados históricos dos Apps 1, 2 e 3
para o banco unificado Supabase. **Nenhuma migração real foi executada.**

Os scripts estão prontos e validados estruturalmente. A migração real ocorrerá na Fase 4.7,
após os pré-requisitos listados na Seção 7 serem satisfeitos.

| Métrica | Estimativa |
|---------|----------:|
| Fontes de dados | 3 (App1/App2/App3) |
| Tabelas a migrar | 10 (5 do App3 + 5 do App2) |
| Tabelas destino afetadas | 9 tabelas canônicas |
| Scripts criados | 8 (00–07) |
| dry_run testado | ✅ (estruturalmente — sem conexão real) |

---

## 2. Fontes de Dados

### 2.1 App 1 — Dashboard Financeiro (banco unificado)

O banco unificado já contém 14 tabelas do App 1 (análise fundamentalista / CVM).
Esses dados **não precisam ser migrados** para o modelo canônico pessoal —
são dados de mercado que coexistem no mesmo banco.

Tabelas App 1 presentes (14):
`Demonstracoes_Financeiras`, `Demonstracoes_Financeiras_TRI`, `cvm_to_ticker`,
`docs_corporativos`, `docs_corporativos_chunks`, `info_economica`,
`info_economica_mensal`, `multiplos`, `multiplos_TRI`, `patch6_runs`,
`portfolio_snapshot_analysis`, `portfolio_snapshot_items`, `portfolio_snapshots`, `setores`

### 2.2 App 3 — Controle Financeiro (PostgreSQL/Supabase)

> ⚠️ Contagens reais não disponíveis — extração não executada.
> Execute `python -m migration.02_extract_controle_financeiro --no-dry-run` para obter.

| Tabela (origem) | Tabela destino |
|-----------------|----------------|
| `transacoes` | `transactions` |
| `contas` | `accounts` |
| `categorias` | `categories` |
| `orcamentos` | `budgets` |
| `metas` | `financial_goals` |

### 2.3 App 2 — Dashboard Investimentos (SQLite)

> ⚠️ Contagens reais não disponíveis — extração não executada.
> Execute `python -m migration.03_extract_investimentos_sqlite --no-dry-run` para obter.

| Tabela (origem) | Tabela destino |
|-----------------|----------------|
| `assets` | `assets` |
| `institutions` | `financial_institutions` |
| `transactions` | `investment_transactions` |
| `incomes` | `dividends` |
| `xp_positions` | `portfolio_positions` |

---

## 3. Transformação para o Modelo Canônico

O script `04_transform_to_canonical.py` aplica:

| Transformação | Detalhes |
|--------------|---------|
| Renomeação de colunas | pt-BR → inglês (ref: `banco_unificado_mapa_origem_destino.md`) |
| Padronização de datas | → ISO 8601 `YYYY-MM-DD` |
| Padronização monetária | → `NUMERIC(15,2)` via `Decimal` |
| Padronização de tipos | classes de ativo e tipos de transação → CHECK canônico |
| Injeção de `user_id` | `OWNER_USER_ID` em todos os registros pessoais |
| Preservação de origem | `_source_system`, `_source_table`, `_source_id` |

---

## 4. Carga no Banco Unificado

**Status:** Não executada — dry_run pendente.

### Estratégia de carga:

```
INSERT INTO <table> (...) VALUES (...) ON CONFLICT DO NOTHING
```

- Idempotente — reexecutar é seguro
- Registra `import_batch_id` em `import_batches`
- Registra cada registro em `migration_source_map`
- Loga cada ação em `import_logs`
- Nunca usa `DELETE`, `TRUNCATE` ou `UPDATE` em massa

### Ordem de carga (respeita FKs):

```
1. financial_institutions  (sem FK)
2. assets                  (sem FK)
3. accounts                (→ profiles)
4. categories              (→ profiles)
5. investment_transactions (→ profiles, assets)
6. dividends               (→ profiles, assets)
7. transactions            (→ profiles, accounts, categories)
8. budgets                 (→ profiles, categories)
9. financial_goals         (→ profiles)
```

---

## 5. Validações Planejadas

| Validação | Descrição |
|-----------|-----------|
| V1 — Contagens | Registros por tabela canônica |
| V2 — Somas financeiras | Receitas, despesas, saldo líquido |
| V3 — Somas investimentos | Total de operações, volume financeiro |
| V4 — Datas | Mínima e máxima por tabela |
| V5 — Categorias | Transações sem categoria mapeada |
| V6 — Ativos | Assets sem ticker (impedem cotações) |
| V7 — Duplicidades | Duplicatas em `migration_source_map` |
| V8 — user_id | Registros pessoais sem proprietário |
| V9 — Logs | Cobertura de `import_logs` por tabela |

---

## 6. Riscos

| # | Risco | Severidade |
|---|-------|:----------:|
| R1 | Colunas App 3 com nomes inesperados | 🔴 Alto |
| R2 | IDs de FK (account_id, category_id) sem correspondência | 🔴 Alto |
| R3 | Ativos com classe não mapeada (`⚠️ SEM MAPEAMENTO`) | 🟡 Médio |
| R4 | OWNER_USER_ID ausente | 🔴 Alto (bloqueador) |
| R5 | App 3 offline ou credencial expirada | 🟡 Médio |
| R6 | SQLite do App 2 movido ou corrompido | 🟡 Médio |
| R7 | transactions.account_id sem FK no destino | 🔴 Alto |
| R8 | Carga dupla sem idempotência | 🟢 Baixo (migration_source_map) |

---

## 7. Pré-requisitos para Migração Real

Antes de executar `--no-dry-run`:

- [ ] `009_schema_amendments.sql` aplicado no Supabase
- [ ] Perfil criado em `profiles` → UUID copiado para `OWNER_USER_ID`
- [ ] `user_settings` criado para o perfil
- [ ] Conectividade com App 3 testada (`python -m migration.00_config`)
- [ ] Caminho do SQLite App 2 confirmado e arquivo acessível
- [ ] dry_run completo sem erros: `01 → 02 → 03 → 04 → 05`
- [ ] Backup do banco unificado feito no Supabase (Settings → Database → Backups)

---

## 8. Próximos Passos

| Fase | Ação |
|------|------|
| **4.7** | Satisfazer pré-requisitos e executar migração real |
| **4.8** | Validar dados no Supabase Table Editor (spot checks manuais) |
| **4.9** | Configurar `SUPABASE_UNIFICADO_URL` em Streamlit Secrets + `MOCK_MODE=false` |

---

*Relatório de planejamento gerado em 2026-05-13.*
*Atualizar com dados reais após execução da Fase 4.7.*
