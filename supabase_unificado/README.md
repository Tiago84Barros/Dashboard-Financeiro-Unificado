# supabase_unificado/ — Pasta Operacional do Banco Unificado

Scripts SQL versionados do Supabase que o app publicado lê.

---

## ⚠️ AVISO IMPORTANTE

> **Nenhum script desta pasta deve ser executado sem revisão humana prévia.**
>
> Os scripts precisam ser lidos, validados e aprovados pelo proprietário antes de
> qualquer execução no Supabase. Executar sem revisão pode criar tabelas com
> schema errado, duplicar dados ou causar perda de dados.
>
> **Leia antes de executar. Sempre.**

---

## Estrutura

```
supabase_unificado/
├── README.md     ← este arquivo
├── schema/       ← scripts DDL versionados (CREATE TABLE, RLS, views)
├── rollback/     ← scripts de reversão de migrations específicas
└── backups/      ← dumps antes de operações críticas (não commitados)
```

---

### `schema/`

**Convenção de nomes:** `NNN_descricao.sql`, executados em ordem numérica.
A ordem explícita e as exceções (por exemplo, os dois `018`) estão em
`schema/README_EXECUCAO_SQL.md`.

**Regras:**
- Todo script usa `CREATE ... IF NOT EXISTS` — nunca `DROP TABLE` antes
- Scripts são imutáveis após aplicação no banco
- Correções futuras geram novo arquivo com número maior
- Cada arquivo tem cabeçalho com data, objetivo e pré-requisito

Comentários e valores herdados da carga inicial (por exemplo, o `CHECK` de
`import_batches.source` com `app1`/`app2`/`app3`) ficam como estão: os scripts já
foram aplicados e o banco guarda esses valores nas linhas antigas.

---

### `backups/`

Dumps `.sql` antes de operações críticas.

- Backup obrigatório antes de executar qualquer script DDL
- Arquivos de backup **não são commitados** (`.gitignore` exclui `*.sql` e `*.dump`)
- Nome: `dump_dashboard_financeiro_AAAAMMDD_HHMM.sql`
- Gerar em Supabase Dashboard → projeto → Settings → Database → Backups

---

## O Que Não Está Aqui

- Credenciais ou valores de variáveis de ambiente
- Dados reais (transações, saldos, nomes pessoais)
- Scripts com `DROP TABLE`, `TRUNCATE` ou `DELETE` não autorizado
- Código de aplicação (está em `core/`, `etl/`, `views/`)

---

## Referências

- `schema/*.sql` — definição vigente das tabelas
- `docs/banco_unificado_dicionario_dados.md` — dicionário de dados
- `docs/banco_unificado_decisoes_modelagem.md` — decisões de modelagem
- `docs/banco_unificado_regras_de_seguranca.md` — regras de segurança
- `docs/plano_saida_supabase_free.md` — limite do plano gratuito e arquitetura local-first
