# Fase 4.6 — Scripts de Migração Controlada

**Data:** 2026-05-13
**Status:** ✅ Scripts criados — migração real pendente de execução manual
**MOCK_MODE:** `true` (app não conecta ao banco)

---

## 1. Visão Geral

A Fase 4.6 cria os scripts Python de migração controlada que transferirão
dados históricos dos três apps originais para o banco unificado Supabase.

**Princípios:**
- `dry_run=True` por padrão em todos os scripts
- Nenhum dado é modificado nas origens
- Toda carga registra `import_batch_id` e `migration_source_map`
- Idempotência garantida: executar múltiplas vezes é seguro
- Sem `DELETE`, `TRUNCATE` ou `UPDATE` em massa

---

## 2. Estrutura de Arquivos

```
migration/
├── __init__.py
├── 00_config.py                    # Configuração central e helpers
├── 01_extract_dashboard_financeiro.py  # Inspeção do banco unificado / App 1
├── 02_extract_controle_financeiro.py   # Extração App 3 (PostgreSQL)
├── 03_extract_investimentos_sqlite.py  # Extração App 2 (SQLite)
├── 04_transform_to_canonical.py        # Transformação para modelo canônico
├── 05_load_to_unified_supabase.py      # Carga no banco unificado
├── 06_validate_migration.py            # Validação pós-carga
├── 07_report_migration.py              # Geração de relatório Markdown
└── output/                         # Dados intermediários (gitignored)
    ├── .gitkeep
    ├── 01_app1_extract.json
    ├── 02_app3_*.json
    ├── 03_app2_*.json
    ├── 05_load_report.json
    ├── 06_validation_report.json
    └── transformed/
        └── 04_*_canonical.json
```

---

## 3. Fontes de Dados

| ID | App | Tipo | Variável | Tabelas principais |
|----|-----|------|----------|-------------------|
| `app1` | Dashboard Financeiro | PostgreSQL/Supabase | `SOURCE_DB_APP1` | 14 tabelas CVM/análise (sem migração para canônico) |
| `app2` | Dashboard Investimentos | SQLite | `SOURCE_DB_APP2` | `assets`, `transactions`, `incomes`, `xp_positions` |
| `app3` | Controle Financeiro | PostgreSQL/Supabase | `SUPABASE_ORIGEM_CONTROLE_URL` | `transacoes`, `contas`, `categorias`, `orcamentos`, `metas` |

---

## 4. Destino

| Item | Valor |
|------|-------|
| Banco | Dashboard Financeiro Unificado (Supabase) |
| Variável | `SUPABASE_UNIFICADO_URL` |
| Proprietário | `OWNER_USER_ID` (UUID de `profiles`) |
| Schema | `public` |
| Tabelas destino | 22 tabelas canônicas |

---

## 5. Variáveis de Ambiente Necessárias

```ini
# Destino (banco unificado)
SUPABASE_UNIFICADO_URL="postgresql://postgres.<project>:<senha>@<host>:5432/postgres"
OWNER_USER_ID="<uuid-do-perfil-em-profiles>"

# Fontes (somente leitura)
SUPABASE_ORIGEM_CONTROLE_URL="postgresql://..."   # App 3
SOURCE_DB_APP2="sqlite:///caminho/absoluto/investimentos.db"  # App 2

# App 1 (opcional — sem dados pessoais para migrar)
SOURCE_DB_APP1="postgresql://..."
```

> **Segurança:** Nunca commitar `.env` com valores reais.
> Configure em `.env` local ou use variáveis de ambiente do sistema.

---

## 6. Mapeamento de Colunas

### App 3 → Canônico (português → inglês)

| Tabela App 3 | Tabela canônica | Principais renomeações |
|-------------|----------------|----------------------|
| `transacoes` | `transactions` | `usuario_id→user_id`, `valor→amount`, `data_competencia→due_date`, `tipo→type` |
| `contas` | `accounts` | `usuario_id→user_id`, `nome→name`, `saldo_inicial→initial_balance` |
| `categorias` | `categories` | `usuario_id→user_id`, `nome→name`, `tipo→type`, `pai_id→parent_id` |
| `orcamentos` | `budgets` | `usuario_id→user_id`, `mes_ano→month_year`, `valor_limite→amount_limit` |
| `metas` | `financial_goals` | `usuario_id→user_id`, `valor_alvo→target_amount`, `ativa→active` |

### App 2 (SQLite) → Canônico

| Tabela SQLite | Tabela canônica | Principais mapeamentos |
|--------------|----------------|----------------------|
| `assets` | `assets` | `classe→class`, `nome→name` |
| `transactions` | `investment_transactions` | `price→unit_price`, `date→transaction_date` |
| `incomes` | `dividends` | `valor_por_cota→amount_per_unit`, `data_com→ex_date` |
| `xp_positions` | `portfolio_positions` | `preco_medio→average_price` |

---

## 7. Ordem de Execução

```
Pré-requisitos:
  1. Supabase com schema aplicado (001–009)
  2. Perfil criado em profiles + OWNER_USER_ID configurado
  3. user_settings criado para o perfil
  4. Conectividade com App 3 testada
  5. Caminho do SQLite App 2 confirmado

Scripts (em ordem):
  python -m migration.01_extract_dashboard_financeiro   # Inspeção
  python -m migration.02_extract_controle_financeiro    # Extração App 3 (dry_run)
  python -m migration.03_extract_investimentos_sqlite   # Extração App 2 (dry_run)
  python -m migration.04_transform_to_canonical         # Transformação
  python -m migration.05_load_to_unified_supabase       # Carga (dry_run)
  python -m migration.06_validate_migration             # Validação
  python -m migration.07_report_migration               # Relatório
```

---

## 8. Como Rodar em dry_run

Todos os scripts têm `dry_run=True` por padrão:

```bash
# Testar configuração
python -m migration.00_config

# Inspecionar banco unificado (sempre seguro)
python -m migration.01_extract_dashboard_financeiro

# Extrair contagens do App 3 (sem registros completos)
python -m migration.02_extract_controle_financeiro

# Extrair contagens do App 2 SQLite
python -m migration.03_extract_investimentos_sqlite

# Transformar (lê output/, não conecta ao banco)
python -m migration.04_transform_to_canonical

# Simular carga (mostra o que seria inserido)
python -m migration.05_load_to_unified_supabase

# Validar (conecta ao banco, apenas SELECT)
python -m migration.06_validate_migration

# Gerar relatório
python -m migration.07_report_migration
```

Para extração completa (registros reais nos JSONs de output):

```bash
python -m migration.02_extract_controle_financeiro --no-dry-run
python -m migration.03_extract_investimentos_sqlite --no-dry-run
python -m migration.04_transform_to_canonical --no-dry-run
```

Para carga real (**⚠️ irreversível sem backup**):

```bash
python -m migration.05_load_to_unified_supabase --no-dry-run
# ATENÇÃO: exibe contagem regressiva de 5 segundos antes de iniciar
```

---

## 9. Como Validar

```bash
# Após carga real:
python -m migration.06_validate_migration

# Verificações executadas:
# V1 — Contagens por tabela
# V2 — Soma de receitas e despesas
# V3 — Soma de movimentações de investimentos
# V4 — Datas mínimas e máximas
# V5 — Transações sem categoria
# V6 — Ativos sem ticker
# V7 — Duplicidades em migration_source_map
# V8 — Registros sem user_id
# V9 — Cobertura de import_logs

# Resultado salvo em:
# migration/output/06_validation_report.json
```

---

## 10. Rastreabilidade

Cada registro migrado gera:
1. Entrada em `import_batches` — rastreia o lote
2. Entrada em `import_logs` — rastreia o registro individualmente
3. Entrada em `migration_source_map` — garante idempotência

```sql
-- Verificar registros migrados de uma tabela
SELECT target_table, source, COUNT(*)
FROM migration_source_map
GROUP BY target_table, source;

-- Verificar logs de um lote
SELECT target_table, action, COUNT(*)
FROM import_logs WHERE batch_id = '<uuid>'
GROUP BY target_table, action;
```

---

## 11. Riscos

| # | Risco | Severidade | Mitigação |
|---|-------|:----------:|-----------|
| R1 | Colunas do App 3 com nomes diferentes do esperado | 🔴 Alto | Executar extração e revisar `columns` no JSON antes de transformar |
| R2 | IDs de categorias App 3 não coincidem (FK break) | 🔴 Alto | Verificar se account_id/category_id existem no destino antes de inserir transactions |
| R3 | Ativos SQLite com classe não mapeada | 🟡 Médio | Revisar `asset_class_coverage` no relatório do 03 |
| R4 | OWNER_USER_ID ausente ou errado | 🔴 Alto | Obrigatório — configurar antes de qualquer carga real |
| R5 | Banco App 3 offline | 🟡 Médio | Testar conectividade antes da extração |
| R6 | SQLite corrompido ou movido | 🟡 Médio | Confirmar caminho e integridade do arquivo |
| R7 | FK de transactions.account_id sem correspondência | 🔴 Alto | Migrar accounts ANTES de transactions |
| R8 | Carga dupla (re-execução sem idempotência) | 🟢 Baixo | migration_source_map garante ON CONFLICT DO NOTHING |

---

## 12. Próximos Passos

**Fase 4.7 — Migração real dos dados:**
1. Executar `009_schema_amendments.sql` no Supabase
2. Criar perfil + `user_settings`
3. Testar conectividade com App 3 e App 2
4. Rodar pipeline completo em dry_run sem erros
5. Executar carga real com `--no-dry-run`
6. Validar e gerar relatório final

**Fase 4.8 — Validação dos dados:**
- Conferência manual de registros no Supabase Table Editor
- Spot checks de saldos e movimentações
- Reconciliação de totais com os apps originais

**Fase 4.9 — Conexão do app:**
- Configurar `SUPABASE_UNIFICADO_URL` em Streamlit Secrets
- Alterar `MOCK_MODE = "false"`
- Implementar `_visao_geral_real()` em `core/financeiro.py`
