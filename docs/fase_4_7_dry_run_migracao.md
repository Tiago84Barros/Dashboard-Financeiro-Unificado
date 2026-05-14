# Fase 4.7 — Dry Run da Migração Controlada

**Data:** 2026-05-14
**Status:** ✅ Dry run executado — pipeline validado estruturalmente
**MOCK_MODE:** `true` (app não conecta ao banco)
**Credenciais configuradas:** Nenhuma (sem fontes reais nesta etapa)

---

## 1. Resumo Executivo

O dry run da Fase 4.7 executou o pipeline completo de migração (Steps 0–7)
com `dry_run=True` em todos os scripts. **Nenhum dado foi gravado. Nenhuma
conexão com banco de dados foi aberta.**

| Métrica | Resultado |
|---------|:---------:|
| Scripts importados com sucesso | ✅ 8/8 |
| Conexões reais abertas | ✅ 0 (zero) |
| Dados gravados no banco unificado | ✅ 0 (zero) |
| Dados alterados nas fontes | ✅ 0 (zero) |
| Erros de execução | ✅ 0 (zero) |
| Bugs corrigidos nesta fase | ⚙️ 3 (B01–B03) |
| Warnings esperados (sem credenciais) | ℹ️ 12 |

**Decisão:** ✅ Pipeline estruturalmente aprovado. Aguarda credenciais reais para dry_run com dados.

---

## 2. Bugs Encontrados e Corrigidos

### B01 — `migration/config.py` inexistente (Crítico — bloqueador)

**Descrição:** Todos os 8 scripts importam `from migration.config import MigrationConfig`,
mas o arquivo se chamava `migration/00_config.py`. Python não consegue importar módulos
cujos nomes são apenas o sufixo numérico dentro de um pacote via import padrão.

**Erro:** `ModuleNotFoundError: No module named 'migration.config'`

**Correção:**
- Criado `migration/config.py` com toda a lógica de configuração (classe `MigrationConfig`,
  `make_engine`, `_ensure_utf8_stdout`, `run_config_check`)
- `migration/00_config.py` reescrito como thin wrapper CLI que importa de `config.py`

**Status:** ✅ Corrigido

---

### B02 — Script 05 exigia credenciais mesmo em dry_run (Médio)

**Descrição:** `05_load_to_unified_supabase.py` verificava `dest_url` e `owner_id` antes
de entrar no bloco `dry_run`, causando saída prematura mesmo sem necessidade de conexão.

**Comportamento anterior:**
```
❌ SUPABASE_UNIFICADO_URL ausente.   ← retornava aqui sem chegar no dry_run
```

**Correção:** Verificação de credenciais movida para após o branch `if cfg.dry_run`.
Em dry_run, ausência de credenciais é informativa, não fatal.

**Status:** ✅ Corrigido

---

### B03 — `f-string` sem placeholder em `07_report_migration.py` e `run_dry_run.py` (Baixo)

**Descrição:** Duas strings tinham prefixo `f` desnecessário (sem `{variavel}` no conteúdo).
Detectado por `ruff check` (F541).

**Correção:** Removido prefixo `f` das strings afetadas.

**Status:** ✅ Corrigido

---

## 3. Estrutura dos Scripts — Revisão de Segurança

### Confirmação: dry_run=True é o padrão em todos os scripts

| Script | dry_run padrão | Flag para real |
|--------|:--------------:|---------------|
| `config.py` | `True` (parâmetro default) | N/A |
| `01_extract_dashboard_financeiro.py` | `True` | `--no-dry-run` |
| `02_extract_controle_financeiro.py` | `True` | `--no-dry-run` |
| `03_extract_investimentos_sqlite.py` | `True` | `--no-dry-run` |
| `04_transform_to_canonical.py` | `True` | `--no-dry-run` |
| `05_load_to_unified_supabase.py` | `True` | `--no-dry-run` (+ 5s countdown) |
| `06_validate_migration.py` | `True` | `--no-dry-run` |
| `07_report_migration.py` | Não aplica (sem escrita em banco) | N/A |
| `run_dry_run.py` | **Hardcoded True** | Não há — interrompe se False |

### Confirmação: script 05 NÃO insere sem parâmetro explícito

```python
# Em dry_run=True (padrão), o bloco abaixo executa SEM conectar ao banco:
if cfg.dry_run:
    print(f"  [dry_run] {total_records:,} registros seriam carregados.")
    print("  Simulando carga sem conectar ao banco...")
    for entity in LOAD_ORDER:
        ...  # apenas imprime contagens
    result["import_batch_id"] = "dry_run_" + str(uuid.uuid4())[:8]
    return result  # ← retorna ANTES de make_engine()
```

Confirmado: `make_engine()` nunca é chamado em modo dry_run.

### Confirmação: migration/output/ no .gitignore

```gitignore
migration/output/*.csv
migration/output/*.json
migration/output/*.jsonl
migration/output/*.parquet
migration/output/transformed/*.json
migration/output/transformed/*.csv
```

✅ Nenhum arquivo de extração real jamais será comitado.

---

## 4. Scripts Testados — Saída do Dry Run

### Comandos executados

```bash
# Orquestrador completo (recomendado):
python migration/run_dry_run.py

# Ou passo a passo:
python -m migration.01_extract_dashboard_financeiro --dry-run
python -m migration.02_extract_controle_financeiro
python -m migration.03_extract_investimentos_sqlite
python -m migration.04_transform_to_canonical
python -m migration.05_load_to_unified_supabase
python -m migration.06_validate_migration
python -m migration.07_report_migration
```

### Step 0 — Configuração

```
dry_run              : ✅ SIM (nenhum dado será gravado)
dest_url             : ✗ não configurado
owner_id             : ✗ não configurado
app1_url (opcional)  : ✗ não configurado
app2_path (SQLite)   : ✗ não configurado
app3_url             : ✗ não configurado
```

**Resultado:** ✅ Config carregada sem erros. Ausência de credenciais é esperada nesta etapa.

### Step 1 — Extração banco unificado (App 1)

**Fontes:** SUPABASE_UNIFICADO_URL ausente → simulado

**Resultado:** ✅ Script rodou sem erros. Retornou imediatamente em modo simulado.
Arquivo `migration/output/01_app1_extract.json` gerado com estrutura vazia esperada.

### Step 2 — Extração App 3 (Controle Financeiro)

**Fontes:** SUPABASE_ORIGEM_CONTROLE_URL ausente → simulado

**Resultado:** ✅ Script rodou sem erros. Total de registros: 0 (esperado — sem credencial).
Arquivo `migration/output/02_app3_summary.json` gerado.

### Step 3 — Extração App 2 (SQLite Investimentos)

**Fontes:** SOURCE_DB_APP2 ausente → simulado

**Resultado:** ✅ Script rodou sem erros. Total de registros: 0 (esperado — sem SQLite).
Arquivo `migration/output/03_app2_summary.json` gerado.

### Step 4 — Transformação para modelo canônico

**Resultado:** ✅ Script rodou sem erros. 8 entidades processadas, 0 registros (esperado — sem extração real).

Arquivos gerados em `migration/output/transformed/`:
```
04_accounts_canonical.json
04_categories_canonical.json
04_transactions_canonical.json
04_budgets_canonical.json
04_financial_goals_canonical.json
04_assets_canonical.json
04_investment_transactions_canonical.json
04_dividends_canonical.json
04_transform_summary.json
```

**Warnings de entidade** (todos esperados — arquivos fonte não existem sem extração real):
```
accounts:               Arquivo não encontrado: 02_app3_contas.json
categories:             Arquivo não encontrado: 02_app3_categorias.json
transactions:           Arquivo não encontrado: 02_app3_transacoes.json
budgets:                Arquivo não encontrado: 02_app3_orcamentos.json
financial_goals:        Arquivo não encontrado: 02_app3_metas.json
assets:                 Arquivo não encontrado: 03_app2_assets.json
investment_transactions: Arquivo não encontrado: 03_app2_transactions.json
dividends:              Arquivo não encontrado: 03_app2_incomes.json
```

**Aviso global:** `OWNER_USER_ID não configurado — registros pessoais não terão user_id.`
→ Esperado. Será configurado na migração real.

### Step 5 — Simulação de carga

**Resultado:** ✅ Carga simulada sem conexão ao banco.

```
[dry_run] 0 registros seriam carregados.
Simulando carga sem conectar ao banco...

  financial_institutions    0 registros
  assets                    0 registros
  accounts                  0 registros
  categories                0 registros
  investment_transactions   0 registros
  dividends                 0 registros
  transactions              0 registros
  budgets                   0 registros
  financial_goals           0 registros

Batch simulado: dry_run_47fc2340
Total inserido (simulado): 0
Erros: 0
```

**Confirmado:** make_engine() nunca foi chamado. Nenhuma conexão aberta.

### Step 6 — Validação

**Resultado:** ✅ Validação ignorada por ausência de SUPABASE_UNIFICADO_URL.
Comportamento correto — validação real requer banco configurado.

### Step 7 — Relatório

**Resultado:** ✅ Relatório gerado em `docs/fase_4_6_relatorio_migracao_planejada.md`.

---

## 5. Fontes Detectadas

| Fonte | URL / Caminho | Status |
|-------|--------------|:------:|
| Banco unificado (App 1) | `SUPABASE_UNIFICADO_URL` | ❌ Não configurado |
| App 3 — Controle Financeiro | `SUPABASE_ORIGEM_CONTROLE_URL` | ❌ Não configurado |
| App 2 — SQLite Investimentos | `SOURCE_DB_APP2` | ❌ Não configurado |
| `OWNER_USER_ID` | UUID do perfil em `profiles` | ❌ Não configurado |

**Observação:** A ausência das fontes é esperada nesta etapa de revisão estrutural.
O dry run com dados reais (Step 2 da Fase 4.7 — veja Seção 8) requer estas variáveis.

---

## 6. Tabelas de Origem Esperadas

### App 3 — Controle Financeiro (PostgreSQL/Supabase)

| Tabela (origem) | Tabela destino | Colunas mínimas esperadas |
|----------------|----------------|--------------------------|
| `transacoes` | `transactions` | `id`, `descricao`, `valor` |
| `contas` | `accounts` | `id`, `nome` |
| `categorias` | `categories` | `id`, `nome`, `tipo` |
| `orcamentos` | `budgets` | `id` |
| `metas` | `financial_goals` | `id`, `nome` |

### App 2 — Investimentos (SQLite)

| Tabela (origem) | Tabela destino | Observação |
|----------------|----------------|------------|
| `assets` | `assets` | Coluna `classe` será mapeada |
| `institutions` | `financial_institutions` | — |
| `transactions` | `investment_transactions` | Tipo mapeado (`compra→buy`, `venda→sell`) |
| `incomes` | `dividends` | — |
| `xp_positions` | `portfolio_positions` | — |
| `position_snapshots` | `portfolio_positions` (snapshot) | Pode não existir |
| `sync_log` | `import_logs` (referência) | Pode não existir |

---

## 7. Tabelas de Destino Esperadas

Ordem de carga (respeita dependências FK):

```
1. financial_institutions  (sem FK)
2. assets                  (sem FK)
3. accounts                (→ profiles)
4. categories              (→ profiles, self-ref)
5. investment_transactions (→ profiles, assets)
6. dividends               (→ profiles, assets)
7. transactions            (→ profiles, accounts, categories)
8. budgets                 (→ profiles, categories)
9. financial_goals         (→ profiles)
```

---

## 8. Campos Sem Mapeamento Identificados

Sem conexão real às fontes, não é possível verificar os valores exatos.
Os mapeamentos conhecidos e riscos documentados:

### Risco de mapeamento de classes de ativo (App2)

| Valor na origem | Mapeamento canônico | Status |
|----------------|---------------------|:------:|
| `acao` / `ação` / `Ação` | `stock` | ✅ Mapeado |
| `fii` / `FII` | `reit` | ✅ Mapeado |
| `etf` / `ETF` | `etf` | ✅ Mapeado |
| `renda_fixa` | `fixed_income` | ✅ Mapeado |
| `cripto` / `crypto` | `crypto` | ✅ Mapeado |
| `other` | `other` | ✅ Mapeado |
| Outros valores | `⚠️ SEM MAPEAMENTO` | 🔴 Risco |

### Risco de mapeamento de tipos de transação (App2)

| Valor na origem | Mapeamento canônico | Status |
|----------------|---------------------|:------:|
| `buy` / `compra` / `Compra` / `C` / `B` | `buy` | ✅ Mapeado |
| `sell` / `venda` / `Venda` / `V` / `S` | `sell` | ✅ Mapeado |
| Outros valores | `⚠️ SEM MAPEAMENTO` | 🔴 Risco |

**Ação necessária:** Executar Step 3 com credenciais reais e `--no-dry-run` para ver
a cobertura real (`asset_class_coverage` e `tx_type_coverage` no relatório do script 03).

---

## 9. Erros e Dados Ausentes

| Categoria | Status | Ação necessária |
|-----------|:------:|----------------|
| Credenciais de banco ausentes | ⚠️ Esperado | Configurar antes da migração real |
| Arquivos de extração ausentes | ⚠️ Esperado | Resultado de extração com credenciais reais |
| OWNER_USER_ID ausente | ⚠️ Esperado | Criar perfil + copiar UUID |
| Erros de importação Python | ✅ Zero | — |
| Carga real acidental | ✅ Zero | — |
| Conexões abertas indesejadas | ✅ Zero | — |

---

## 10. Riscos Identificados

| # | Risco | Severidade | Status |
|---|-------|:----------:|:------:|
| R1 | Colunas do App3 com nomes diferentes do esperado | 🔴 Alto | ⏳ Verificar na extração real |
| R2 | IDs de FK (account_id, category_id) sem correspondência no destino | 🔴 Alto | ⏳ Verificar no transform real |
| R3 | Ativos SQLite com classe não mapeada | 🟡 Médio | ⏳ Ver `asset_class_coverage` |
| R4 | `OWNER_USER_ID` ausente ou UUID errado | 🔴 Alto | 🔴 Obrigatório antes da carga |
| R5 | App3 offline ou credencial expirada | 🟡 Médio | ⏳ Testar antes de extrair |
| R6 | SQLite do App2 movido ou corrompido | 🟡 Médio | ⏳ Confirmar caminho |
| R7 | `transactions.account_id` sem FK correspondente no destino | 🔴 Alto | ⏳ Migrar accounts ANTES |
| R8 | Valores monetários com precisão incorreta | 🟢 Baixo | ✅ Decimal/ROUND_HALF_UP implementado |
| R9 | Encoding UTF-8 em terminal Windows | 🟡 Médio | ✅ `_ensure_utf8_stdout()` adicionado |

---

## 11. Decisão

### ✅ APROVADO PARA DRY RUN COM DADOS REAIS

**Condição:** Configurar as 4 variáveis de ambiente abaixo antes de executar novamente.

O pipeline estrutural está validado:
- Todos os 8 scripts importam corretamente
- `dry_run=True` é o padrão e não pode ser contornado acidentalmente
- Nenhuma conexão real foi aberta durante os testes
- `run_dry_run.py` contém 3 verificações de segurança independentes

---

## 12. Próximos Passos

### Etapa A — Dry run com dados reais (fontes configuradas)

**Variáveis a configurar (nunca commitar):**
```ini
# .env local (não versionado)
SUPABASE_UNIFICADO_URL="postgresql://postgres.<project>:<senha>@<host>:5432/postgres"
OWNER_USER_ID="<uuid-do-profiles>"
SUPABASE_ORIGEM_CONTROLE_URL="postgresql://..."
SOURCE_DB_APP2="sqlite:///caminho/absoluto/investimentos.db"
```

**Executar:**
```bash
# 1. Verificar configuração
python migration/00_config.py

# 2. Extrair contagens (dry_run por padrão = apenas contagens, sem registros completos)
python -m migration.01_extract_dashboard_financeiro
python -m migration.02_extract_controle_financeiro
python -m migration.03_extract_investimentos_sqlite

# 3. Verificar os JSONs em migration/output/ antes de continuar
# Confirmar colunas, contagens e cobertura de mapeamento

# 4. Transformar (opera sobre os JSONs, sem conexão ao banco)
python -m migration.04_transform_to_canonical

# 5. Revisar migration/output/transformed/ — verificar amostra de registros

# 6. Simular carga (dry_run = sem inserções)
python -m migration.05_load_to_unified_supabase

# 7. Se tudo ok → APROVAR para Fase 4.8 (migração real)
```

### Etapa B — Checklist pré-migração real (Fase 4.8)

- [ ] `009_schema_amendments.sql` aplicado no Supabase (M01–M05 da Fase 4.4)
- [ ] Perfil criado em `profiles` e `OWNER_USER_ID` configurado
- [ ] `user_settings` criado para o perfil
- [ ] Conectividade com App3 confirmada
- [ ] Arquivo SQLite App2 confirmado e acessível
- [ ] Backup do banco unificado feito no Supabase (Settings → Database → Backups)
- [ ] dry_run com dados reais executado sem erros
- [ ] Amostras dos arquivos `migration/output/transformed/` revisadas manualmente

---

*Fase 4.7 executada em 2026-05-14. Pipeline estrutural validado — pronto para dry run com dados reais.*
