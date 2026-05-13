# Banco Unificado — Fases de Execução

> Documento: `docs/banco_unificado_fases.md`
> Criado em: 2026-05-13
> Contexto: Fase 4 do Dashboard-Financeiro-Unificado — construção do banco Supabase unificado
>           usando o projeto existente do Dashboard Financeiro (plano gratuito, 2 projetos).
> Referência de segurança: `docs/banco_unificado_regras_de_seguranca.md`
> Estratégia completa: `docs/estrategia_supabase_unificado_plano_gratuito.md`

---

## Regra Geral de Execução

> **Nenhuma fase avança sem a anterior estar concluída e validada.**
> **Nenhum SQL é executado automaticamente pelo app ou pelo Claude.**
> **Toda operação que altera banco de dados é executada manualmente pelo proprietário.**
> **Qualquer erro inesperado interrompe o plano — não avançar sem diagnóstico.**

---

## Visão Geral das Fases

| Fase | Nome | Status | Executado por |
|------|------|:------:|:-------------:|
| **4.0** | Estratégia e documentação | ✅ Concluída | Claude + revisão humana |
| **4.1** | Auditoria dos bancos atuais | ⏳ Pendente | Humano (SQL read-only) |
| **4.2** | Modelo canônico do banco unificado | ⏳ Pendente | Claude + revisão humana |
| **4.3** | Geração dos scripts SQL não destrutivos | ⏳ Pendente | Claude |
| **4.4** | Revisão humana dos scripts SQL | ⏳ Pendente | Humano (obrigatório) |
| **4.5** | Aplicação manual no Supabase | ⏳ Pendente | Humano (SQL Editor) |
| **4.6** | Scripts de migração | ⏳ Pendente | Claude |
| **4.7** | Migração controlada | ⏳ Pendente | Humano + app |
| **4.8** | Validação dos dados migrados | ⏳ Pendente | Humano + app |
| **4.9** | Conexão do app ao banco unificado | ⏳ Pendente | Claude + humano |

---

## Fase 4.0 — Estratégia e Documentação

**Status:** ✅ Concluída em 2026-05-13

**Objetivo:**
Documentar a decisão arquitetural, definir regras de segurança, criar a estrutura
de pastas operacional e estabelecer o plano de execução completo.

**O que foi feito:**

| Arquivo | Tipo | Descrição |
|---------|------|-----------|
| `docs/estrategia_supabase_unificado_plano_gratuito.md` | Decisão | Estratégia completa, papéis de cada projeto, riscos |
| `docs/banco_unificado_fases.md` | Plano | Este documento |
| `docs/banco_unificado_regras_de_seguranca.md` | Segurança | Regras invioláveis |
| `supabase_unificado/README.md` | Estrutura | Guia da pasta operacional |
| `supabase_unificado/schema/` | Pasta | Scripts DDL (a preencher na Fase 4.3) |
| `supabase_unificado/migrations/` | Pasta | Scripts de migração (a preencher na Fase 4.6) |
| `supabase_unificado/backups/` | Pasta | Dumps de backup (a preencher na Fase 4.5) |
| `supabase_unificado/validation/` | Pasta | Relatórios de validação (a preencher na Fase 4.8) |

**Critério de conclusão:** ✅ Todos os arquivos criados e commtados no GitHub.

---

## Fase 4.1 — Auditoria dos Bancos Atuais

**Status:** ⏳ Pendente — inicia após Fase 4.0

**Objetivo:**
Mapear exatamente o que existe nos dois projetos Supabase antes de qualquer
criação ou migração. Evitar surpresas de schema, dados existentes e conflitos de RLS.

**Projetos a auditar:**
1. **Dashboard Financeiro** — futuro banco unificado (destino)
2. **Controle Financeiro** — fonte de migração (origem)

**Queries de auditoria (executar no SQL Editor de cada projeto — somente leitura):**

```sql
-- 1. Listar todas as tabelas e colunas
SELECT
    table_name,
    column_name,
    data_type,
    is_nullable,
    column_default
FROM information_schema.columns
WHERE table_schema = 'public'
ORDER BY table_name, ordinal_position;
```

```sql
-- 2. Contagem de registros por tabela
SELECT
    relname AS tabela,
    n_live_tup AS registros_estimados
FROM pg_stat_user_tables
ORDER BY n_live_tup DESC;
```

```sql
-- 3. Policies RLS ativas
SELECT
    tablename,
    policyname,
    cmd,
    qual,
    with_check
FROM pg_policies
WHERE schemaname = 'public'
ORDER BY tablename, policyname;
```

```sql
-- 4. Schemas existentes
SELECT schema_name
FROM information_schema.schemata
WHERE schema_name NOT IN ('pg_catalog','information_schema','pg_toast')
ORDER BY schema_name;
```

```sql
-- 5. Foreign keys existentes
SELECT
    tc.table_name,
    kcu.column_name,
    ccu.table_name AS tabela_referenciada,
    ccu.column_name AS coluna_referenciada
FROM information_schema.table_constraints AS tc
JOIN information_schema.key_column_usage AS kcu
    ON tc.constraint_name = kcu.constraint_name
JOIN information_schema.constraint_column_usage AS ccu
    ON ccu.constraint_name = tc.constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY'
  AND tc.table_schema = 'public'
ORDER BY tc.table_name;
```

**Output esperado:**
- `docs/auditoria_banco_dashboard_financeiro.md` — resultado das 5 queries no projeto destino
- `docs/auditoria_banco_controle_financeiro.md` — resultado das 5 queries no projeto origem

**Critério de conclusão:** ambos os documentos preenchidos com os resultados reais das queries.

**Quem executa:** proprietário, no SQL Editor do Supabase (aba "SQL Editor").

**Pré-requisito:** nenhum. Pode ser feito antes mesmo do backup.

---

## Fase 4.2 — Modelo Canônico do Banco Unificado

**Status:** ⏳ Pendente — aguarda Fase 4.1

**Objetivo:**
Comparar o schema planejado (`etl/schema_setup.py`) com o que realmente existe
nos bancos após a auditoria 4.1. Definir o DDL final que será aplicado.

**Trabalho:**
- Claude lê os resultados da Fase 4.1
- Compara com `etl/schema_setup.py` e `modelagem_inicial.md` do Obsidian
- Identifica diferenças: tabelas faltantes, colunas divergentes, tipos incompatíveis
- Propõe o modelo canônico final (sem executar nada)

**Output esperado:**
- `supabase_unificado/schema/modelo_canonico.md`
  — descrição final das 10 tabelas com justificativa de cada escolha
  — lista de divergências encontradas e como foram resolvidas

**Critério de conclusão:** documento aprovado pelo proprietário antes de avançar para 4.3.

**Quem executa:** Claude (geração) + proprietário (aprovação).

---

## Fase 4.3 — Geração dos Scripts SQL Não Destrutivos

**Status:** ⏳ Pendente — aguarda Fase 4.2 aprovada

**Objetivo:**
Gerar scripts SQL que criam o schema unificado sem destruir nada que já existe.

**Regras absolutas para os scripts:**
- Todo `CREATE TABLE` usa `IF NOT EXISTS`
- Todo `ADD COLUMN` usa `IF NOT EXISTS`
- Nenhum `DROP TABLE`, `DROP COLUMN`, `TRUNCATE`, `DELETE` não autorizado
- Scripts idempotentes — seguros para executar múltiplas vezes
- Scripts separados por domínio para facilitar revisão

**Estrutura de arquivos a criar em `supabase_unificado/schema/`:**

```
001_usuarios.sql          -- tabela usuarios + RLS
002_contas.sql            -- tabela contas + RLS
003_categorias.sql        -- tabela categorias + RLS
004_transacoes.sql        -- tabela transacoes + RLS
005_orcamentos.sql        -- tabela orcamentos + RLS
006_metas.sql             -- tabela metas + RLS
007_ativos.sql            -- tabela ativos (dados de mercado, sem RLS por usuario)
008_operacoes.sql         -- tabela operacoes + RLS
009_proventos.sql         -- tabela proventos + RLS
010_cotacoes.sql          -- tabela cotacoes (dados de mercado)
011_role_app4_reader.sql  -- criacao do role SELECT-only
012_rls_policies.sql      -- todas as policies RLS consolidadas
```

**Critério de conclusão:** 12 arquivos SQL gerados e salvos em `supabase_unificado/schema/`.

**Quem executa:** Claude (geração dos scripts).

---

## Fase 4.4 — Revisão Humana dos Scripts SQL

**Status:** ⏳ Pendente — aguarda Fase 4.3

**Objetivo:**
O proprietário lê cada script SQL antes que qualquer coisa seja executada no banco.

**Esta fase não pode ser pulada ou delegada ao Claude.**

**Checklist de revisão para cada script:**
```
[ ] O script usa CREATE TABLE IF NOT EXISTS (não DROP antes)
[ ] O script não contém DELETE, TRUNCATE ou DROP TABLE
[ ] Os nomes de tabela estão corretos
[ ] Os tipos de dados fazem sentido
[ ] As constraints FK apontam para as tabelas certas
[ ] As policies RLS filtram por usuario_id
[ ] O role app4_reader recebe apenas SELECT (sem INSERT/UPDATE/DELETE)
[ ] Nenhuma credencial aparece no script
```

**Output esperado:**
- Aprovação explícita do proprietário para cada script
- Eventuais correções solicitadas → Claude ajusta → revisão repetida

**Critério de conclusão:** proprietário aprova todos os 12 scripts com "ok para executar".

---

## Fase 4.5 — Aplicação Manual no Supabase

**Status:** ⏳ Pendente — aguarda Fase 4.4 aprovada

**Objetivo:**
Executar os scripts aprovados no SQL Editor do Supabase, no projeto
Dashboard Financeiro.

**Pré-requisito absoluto: backup verificado**
Antes de executar qualquer script:
1. Acessar Supabase Dashboard → Settings → Database → Backups
2. Baixar o dump mais recente do projeto Dashboard Financeiro
3. Confirmar que o arquivo não está vazio
4. Salvar em `supabase_unificado/backups/dump_dashboard_financeiro_AAAAMMDD.sql`
   (não commitar o arquivo se contiver dados sensíveis)

**Ordem de execução:**
```
1. 001_usuarios.sql
2. 002_contas.sql
3. 003_categorias.sql
4. 004_transacoes.sql
5. 005_orcamentos.sql
6. 006_metas.sql
7. 007_ativos.sql
8. 008_operacoes.sql
9. 009_proventos.sql
10. 010_cotacoes.sql
11. 011_role_app4_reader.sql
12. 012_rls_policies.sql
```

**Verificação após cada script:**
```sql
-- Confirmar que a tabela foi criada
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name = 'NOME_DA_TABELA';
```

**Critério de conclusão:** todas as 10 tabelas existem no banco. Confirmação via
App 4 → Configurações → Banco de Dados → aba "🗄️ Banco de Dados" (10 badges verdes).

**Quem executa:** proprietário, no SQL Editor do Supabase.

---

## Fase 4.6 — Scripts de Migração

**Status:** ⏳ Pendente — aguarda Fase 4.5

**Objetivo:**
Gerar os scripts ETL que copiam dados dos bancos de origem para o banco unificado.

**Origens de dados:**
1. **Controle Financeiro (PostgreSQL/Supabase)** → tabelas: `transacoes`, `contas`, `categorias`, `orcamentos`, `metas`
2. **Dashboard-Investimentos (SQLite)** → tabelas: `transactions→operacoes`, `incomes→proventos`, `assets→ativos`

**Estratégia de inserção:**
- `INSERT INTO destino (...) SELECT ... FROM origem ON CONFLICT DO NOTHING`
- Idempotente: executar duas vezes não duplica dados
- Sempre com `dry_run=True` na primeira execução

**Arquivos a criar em `supabase_unificado/migrations/`:**
```
001_migrar_usuarios.py           -- seed do usuario proprietario
002_migrar_controle_financeiro.py -- transacoes, contas, categorias, orcamentos, metas
003_migrar_investimentos_sqlite.py -- operacoes, proventos, ativos do SQLite
004_verificar_migracao.py        -- contagem e spot checks
```

**Critério de conclusão:** 4 scripts gerados, revisados e aprovados.

**Quem executa:** Claude (geração) + proprietário (revisão e aprovação).

---

## Fase 4.7 — Migração Controlada

**Status:** ⏳ Pendente — aguarda Fase 4.6

**Objetivo:**
Executar a migração de dados em modo controlado, com dry_run primeiro.

**Sequência:**
1. Executar cada script com `dry_run=True` — confirma mapeamento sem gravar
2. Verificar logs de saída — sem erros de FK, tipo ou constraint
3. Executar com `dry_run=False` — grava os dados
4. Verificar contagens imediatamente após cada etapa
5. Registrar resultado em `supabase_unificado/validation/log_migracao_AAAAMMDD.md`

**Critério de parada imediata:**
- Qualquer erro de FK violada
- Contagem de destino < contagem de origem
- Dados duplicados inesperados
- Timeout de conexão

**Pré-requisito:** backup do banco de origem (Controle Financeiro) também realizado.

**Critério de conclusão:** todas as tabelas migradas sem erros. Log salvo.

---

## Fase 4.8 — Validação dos Dados Migrados

**Status:** ⏳ Pendente — aguarda Fase 4.7

**Objetivo:**
Confirmar que os dados migrados estão corretos antes de apontar o app para o banco.

**Checklist de validação:**
```
[ ] Contagem de registros bate entre origem e destino (por tabela)
[ ] Soma de valores financeiros confere entre origem e destino
[ ] Pelo menos 1 usuário existe em `usuarios`
[ ] OWNER_USER_ID configurado e corresponde a um registro real
[ ] Nenhuma FK está quebrada (foreign key violations = 0)
[ ] RLS policies retornam linhas corretas para o usuario do proprietario
[ ] Tabelas de investimentos (ativos, operacoes, proventos) populadas
[ ] Datas de transacoes coerentes (sem datas futuras inesperadas)
[ ] Valores negativos em despesas e positivos em receitas coerentes
[ ] Tickers de ativos reconhecidos pelo yfinance (teste por amostragem)
```

**Output esperado:**
- `supabase_unificado/validation/relatorio_validacao_AAAAMMDD.md`
  com resultados de cada item do checklist

**Critério de conclusão:** todos os itens do checklist marcados. Relatório aprovado.

---

## Fase 4.9 — Conexão do App ao Banco Unificado

**Status:** ⏳ Pendente — aguarda Fase 4.8

**Objetivo:**
Conectar o Dashboard-Financeiro-Unificado ao banco real, desativar MOCK_MODE e
implementar as queries reais.

**Passos:**

| Passo | Arquivo | Ação |
|-------|---------|------|
| 4.9.1 | `.env` | Adicionar `SUPABASE_UNIFICADO_URL` e `OWNER_USER_ID` |
| 4.9.2 | `.env` | Alterar `MOCK_MODE=false` |
| 4.9.3 | `core/config.py` | Adicionar variáveis `SUPABASE_UNIFICADO_*` + atualizar `db_url` |
| 4.9.4 | `core/financeiro.py` | Implementar `_visao_geral_real()` com as 4 queries Q01–Q04 |
| 4.9.5 | App | Testar Dashboard Geral com dados reais |
| 4.9.6 | Fase 5 | Módulo de Investimentos com dados reais |

**Rollback disponível:** se qualquer etapa falhar, voltar para `MOCK_MODE=true`.
O app continua funcionando com dados mockados sem perda de funcionalidade.

**Critério de conclusão:** Dashboard Geral exibe dados reais sem erros.
Configurações → Banco de Dados mostra todos os badges verdes.

---

## Histórico de Mudanças

| Data | Fase | Mudança |
|------|------|---------|
| 2026-05-13 | 4.0 | Documento criado — estrutura completa das 10 fases definida |
