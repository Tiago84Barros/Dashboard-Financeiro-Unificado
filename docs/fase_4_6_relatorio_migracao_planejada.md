# Fase 4.6 — Relatório de Migração Planejada

**Gerado em:** 2026-05-14 13:46 UTC  
**Modo:** 🔵 dry_run (planejamento)  
**MOCK_MODE:** true (app continua em modo mock)  

---

## 1. Resumo Executivo

| Métrica | Valor |
|---------|------:|
| Registros App 3 (Controle Financeiro) | 251 |
| Registros App 2 (SQLite Investimentos) | 2,155 |
| Registros transformados | 3 |
| Registros inseridos (load) | 3 |
| Erros na carga | 0 |
| Validações aprovadas | 9 |
| Validações com falha | 0 |

> ℹ️  **dry_run=True** — nenhum dado foi gravado. Os números acima
> refletem o que seria migrado. Execute com `--no-dry-run` para migração real.

---

## 2. Fontes de Dados

### 2.1 App 1 — Dashboard Financeiro (banco unificado)

Tabelas canônicas com dados existentes no banco:

| Tabela | Registros |
|--------|----------:|
| `profiles` | 1 |
| `accounts` | 2 |
| `categories` | 38 |
| `benchmarks` | 6 |

### 2.2 App 3 — Controle Financeiro (PostgreSQL/Supabase)

| Tabela (origem) | Registros | Tabela destino |
|-----------------|----------:|----------------|
| `transactions` | 251 | `transactions` |

### 2.3 App 2 — Dashboard Investimentos (SQLite)

| Tabela (origem) | Registros | Tabela destino |
|-----------------|----------:|----------------|
| `assets` | 82 | `assets` |
| `institutions` | 7 | `financial_institutions` |
| `accounts` | 1 | `accounts` |
| `transactions` | 1,351 | `investment_transactions` |
| `incomes` | 517 | `dividends` |
| `xp_positions` | 197 | `portfolio_positions` |
| `position_snapshots` | 0 | `portfolio_positions (snapshot)` |
| `sync_log` | 0 | `import_logs` |

---

## 3. Transformação para o Modelo Canônico

| Entidade canônica | Registros transformados |
|-------------------|------------------------:|
| `transactions` | 3 |
| `assets` | 0 |
| `investment_transactions` | 0 |
| `dividends` | 0 |

---

## 4. Carga no Banco Unificado

**dry_run:** True  
**import_batch_id:** `dry_run_17e4168d`  

| Tabela | Inseridos | Ignorados | Erros |
|--------|----------:|----------:|------:|
| `financial_institutions` | 0 | 0 | 0 |
| `assets` | 0 | 0 | 0 |
| `accounts` | 0 | 0 | 0 |
| `categories` | 0 | 0 | 0 |
| `investment_transactions` | 0 | 0 | 0 |
| `dividends` | 0 | 0 | 0 |
| `transactions` | 3 | 0 | 0 |
| `budgets` | 0 | 0 | 0 |
| `financial_goals` | 0 | 0 | 0 |

---

## 5. Validação

| Validação | Status | Detalhe |
|-----------|:------:|---------|
| `V1_record_counts` | ✅ |  |
| `V2_transaction_sums` | ✅ |  |
| `V3_investment_sums` | ✅ |  |
| `V4_date_ranges` | ✅ |  |
| `V5_unmapped_categories` | ✅ |  |
| `V6_assets_without_ticker` | ✅ |  |
| `V7_duplicate_sources` | ✅ |  |
| `V8_records_without_user_id` | ✅ |  |
| `V9_import_log_coverage` | ✅ |  |

---

## 6. Riscos e Pendências

| # | Risco | Impacto | Status |
|---|-------|:-------:|:------:|
| R1 | Colunas do App 3 diferentes do esperado (nomes pt-br) | Alto | ⏳ Verificar na extração |
| R2 | IDs de categorias do App 3 não coincidem com canônico | Médio | ⏳ Verificar no transform |
| R3 | Ativos SQLite com classe não mapeada | Médio | ⏳ Verificar cobertura |
| R4 | Transações de investimento com tipo não canônico | Alto | ⏳ Verificar mapeamento |
| R5 | OWNER_USER_ID não configurado antes da carga | Alto | 🔴 Obrigatório |
| R6 | Banco App 3 offline ou credencial expirada | Médio | ⏳ Testar conectividade |
| R7 | Arquivo SQLite movido ou corrompido | Médio | ⏳ Confirmar caminho |

---

## 7. Próximos Passos

### Para executar a migração real:

```bash
# 1. Configurar variáveis de ambiente
export SUPABASE_UNIFICADO_URL='postgresql://...'
export SUPABASE_ORIGEM_CONTROLE_URL='postgresql://...'
export SOURCE_DB_APP2='sqlite:///path/to/investimentos.db'
export OWNER_USER_ID='<uuid-do-profiles>'

# 2. Extrair (dry_run por padrão)
python -m migration.01_extract_dashboard_financeiro
python -m migration.02_extract_controle_financeiro --no-dry-run
python -m migration.03_extract_investimentos_sqlite --no-dry-run

# 3. Transformar
python -m migration.04_transform_to_canonical --no-dry-run

# 4. Revisar migration/output/transformed/ antes de prosseguir

# 5. Carregar (--no-dry-run inicia contagem regressiva de 5s)
python -m migration.05_load_to_unified_supabase --no-dry-run

# 6. Validar
python -m migration.06_validate_migration

# 7. Gerar relatório
python -m migration.07_report_migration
```

### Checklist pré-migração:

- [ ] `009_schema_amendments.sql` aplicado no Supabase
- [ ] Perfil criado em `profiles` e `OWNER_USER_ID` configurado
- [ ] `user_settings` criado para o perfil
- [ ] Conectividade com App 3 (Supabase Controle Financeiro) testada
- [ ] Caminho do SQLite App 2 confirmado
- [ ] Backup do banco unificado feito antes da carga real
- [ ] `dry_run=True` testado sem erros

---

*Relatório gerado automaticamente por `migration/07_report_migration.py`.*  
*2026-05-14 13:46 UTC*