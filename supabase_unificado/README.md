# supabase_unificado/ — Pasta Operacional do Banco Unificado

> Criado em: 2026-05-13
> Contexto: Fase 4.0 do Dashboard-Financeiro-Unificado
> Projeto Supabase alvo: Dashboard Financeiro (banco unificado)

---

## ⚠️ AVISO IMPORTANTE

> **Nenhum script desta pasta deve ser executado sem revisão humana prévia.**
>
> Os scripts são gerados pelo Claude e precisam ser lidos, validados e aprovados
> pelo proprietário antes de qualquer execução no Supabase.
>
> Executar scripts sem revisão pode criar tabelas com schema errado,
> duplicar dados ou (em caso de erro humano) causar perda de dados.
>
> **Leia antes de executar. Sempre.**

---

## Objetivo desta Pasta

Esta pasta centraliza todos os artefatos operacionais necessários para construir,
popular e validar o banco Supabase unificado do Dashboard-Financeiro-Unificado.

O banco unificado é o projeto Supabase existente do **Dashboard Financeiro**,
que concentra as 10 tabelas do schema canônico:

```
usuarios → contas → categorias → transacoes → orcamentos → metas
ativos → operacoes → proventos → cotacoes
```

---

## Estrutura de Subpastas

```
supabase_unificado/
├── README.md              ← este arquivo
├── schema/                ← scripts DDL versionados (CREATE TABLE, RLS, roles)
├── migrations/            ← scripts ETL de migração de dados
├── backups/               ← dumps de backup antes de operações críticas
└── validation/            ← logs de migração e relatórios de validação
```

---

### `schema/`

Contém os scripts SQL que criam ou modificam a estrutura do banco.

**Convenção de nomes:**
```
NNN_descricao.sql
001_usuarios.sql
002_contas.sql
...
012_rls_policies.sql
```

**Regras:**
- Todo script usa `CREATE TABLE IF NOT EXISTS` — nunca `DROP TABLE` antes
- Scripts são imutáveis após aplicação no banco
- Correções futuras geram novo arquivo com número maior
- Cada arquivo tem cabeçalho com data, objetivo e pré-requisito
- Executar na ordem numérica (001 antes de 002, etc.)

**Status dos scripts:**
- `[gerado]` — criado pelo Claude, aguarda revisão humana
- `[revisado]` — aprovado pelo proprietário, pronto para executar
- `[aplicado]` — executado com sucesso no Supabase

---

### `migrations/`

Contém os scripts Python que copiam dados de origens externas para o banco unificado.

**Origens previstas:**
1. **Controle Financeiro (PostgreSQL/Supabase)** — transações, categorias, orçamentos, metas
2. **Dashboard-Investimentos (SQLite)** — operações, proventos, ativos

**Convenção de nomes:**
```
NNN_migrar_origem.py
001_migrar_usuarios.py
002_migrar_controle_financeiro.py
003_migrar_investimentos_sqlite.py
004_verificar_migracao.py
```

**Regras:**
- Todo script de migração começa em `dry_run=True`
- Usa `INSERT ... ON CONFLICT DO NOTHING` — idempotente
- Nunca executa `DELETE`, `TRUNCATE` ou `DROP` na origem
- Registra log de execução em `validation/`

---

### `backups/`

Contém os dumps `.sql` dos projetos Supabase antes de operações críticas.

**Regras:**
- Backup obrigatório antes de executar qualquer script DDL (schema/)
- Backup obrigatório antes de executar qualquer migração de dados (migrations/)
- Arquivos de backup **não são commitados** se contiverem dados pessoais reais
- `.gitignore` desta pasta exclui arquivos `*.sql` e `*.dump`

**Formato de nome:**
```
dump_dashboard_financeiro_AAAAMMDD_HHMM.sql
dump_controle_financeiro_AAAAMMDD_HHMM.sql
```

**Como gerar o backup:**
1. Acessar Supabase Dashboard → projeto → Settings → Database → Backups
2. Clicar em "Download" para o backup mais recente
3. Salvar com o nome no formato acima

---

### `validation/`

Contém logs de migração e relatórios de validação.

**Arquivos gerados:**
- `log_migracao_AAAAMMDD.md` — resultado de cada execução de migração
- `relatorio_validacao_AAAAMMDD.md` — checklist de validação pós-migração
- `auditoria_banco_dashboard_financeiro.md` — resultado das queries de auditoria (Fase 4.1)
- `auditoria_banco_controle_financeiro.md` — resultado das queries de auditoria (Fase 4.1)

---

## Ordem de Uso das Pastas por Fase

| Fase | Pasta utilizada | Ação |
|------|----------------|------|
| 4.1 | `validation/` | Salvar resultado das queries de auditoria |
| 4.3 | `schema/` | Scripts DDL gerados pelo Claude |
| 4.4 | `schema/` | Revisar cada script (humano) |
| 4.5 | `backups/` + `schema/` | Backup → executar scripts no Supabase |
| 4.6 | `migrations/` | Scripts de migração gerados pelo Claude |
| 4.7 | `migrations/` | Executar migração com dry_run=True → False |
| 4.8 | `validation/` | Salvar log e relatório de validação |

---

## O Que Não Está Aqui

Esta pasta **não contém:**
- Credenciais ou valores de variáveis de ambiente
- Dados reais (transações, saldos, nomes pessoais)
- Scripts com `DROP TABLE`, `TRUNCATE` ou `DELETE` não autorizado
- Código de aplicação (está em `core/`, `etl/`, `pages/`)

---

## Referências de Documentação

- `docs/estrategia_supabase_unificado_plano_gratuito.md` — decisão arquitetural
- `docs/banco_unificado_fases.md` — plano completo com todas as fases
- `docs/banco_unificado_regras_de_seguranca.md` — regras de segurança
- `etl/schema_setup.py` — DDL atual das 10 tabelas (ponto de partida para schema/)
- `docs/auditoria_dados_investimentos.md` — mapeamento do SQLite de investimentos
