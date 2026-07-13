# Banco Unificado — Regras de Segurança

> Documento: `docs/banco_unificado_regras_de_seguranca.md`
> Criado em: 2026-05-13
> Escopo: Dashboard-Financeiro-Unificado + projeto Supabase do Dashboard Financeiro
> Status: Vigente a partir da Fase 4.0

---

## Princípio Fundamental

> **O banco de dados contém dados financeiros pessoais reais.**
> Qualquer vazamento, corrupção ou perda é irreversível e causará dano real.
> As regras abaixo não são sugestões — são obrigações técnicas invioláveis.

---

## REGRA 1 — Service Role Key é secreta e local

**Regra:**
A `service_role_key` do Supabase **nunca** é copiada para fora do ambiente local.

**O que proíbe:**
- Commitar a `service_role_key` em qualquer arquivo do repositório
- Incluir a `service_role_key` em documentação, logs, comentários ou mensagens de commit
- Usar a `service_role_key` em código que roda no Streamlit Cloud ou qualquer servidor
- Exibir a `service_role_key` em qualquer tela do app (nem mascarada)
- Passar a `service_role_key` como parâmetro de URL ou query string

**O que permite:**
- Armazenar a `service_role_key` apenas no `.env` local
- Usar a `service_role_key` apenas em scripts de migração e setup que rodam localmente
- Referenciar a variável `SUPABASE_UNIFICADO_SERVICE_ROLE_KEY` por nome em documentação
  (sem incluir o valor)

**Por quê:**
A `service_role_key` bypassa todas as políticas RLS do Supabase.
Qualquer pessoa que a possua tem acesso irrestrito a todos os dados do banco.

---

## REGRA 2 — Anon Key para app publicado

**Regra:**
Apenas a `anon_key` pode ser usada em código que roda fora do ambiente local.

**O que permite:**
- Incluir a `anon_key` no código Python do app quando necessário para autenticação Supabase
- Referenciar `SUPABASE_UNIFICADO_ANON_KEY` no `.env.example` (sem valor)
- Usar a `anon_key` em chamadas REST ao Supabase que passam por RLS

**O que proíbe:**
- Usar a `anon_key` como substituta da `service_role_key` para bypass de RLS
- Commitar o valor real da `anon_key` em qualquer arquivo

**Nota de arquitetura (auditada em 2026-07-13):**
As queries do app usam conexão direta via SQLAlchemy (`DATABASE_URL`). O ambiente
publicado ainda conecta como `postgres`; isso mantém o Streamlit/ETL operacional após
o RLS, mas é uma dívida técnica de privilégio excessivo. O próximo passo recomendado é
separar um role de leitura do app e um role de escrita do ETL, ambos com grants mínimos.
A `anon_key` é necessária apenas se futuras integrações usarem a API REST diretamente.

---

## REGRA 3 — RLS obrigatória em todas as tabelas de dados pessoais

**Regra:**
Todas as tabelas que contêm dados financeiros pessoais devem ter Row Level Security (RLS)
habilitada e pelo menos uma policy de leitura ativa.

**Tabelas que exigem RLS:**

| Tabela | Coluna de filtro | Policy obrigatória |
|--------|:---------------:|-------------------|
| `usuarios` | `id` | Usuário vê apenas seu próprio registro |
| `contas` | `usuario_id` | SELECT WHERE usuario_id = auth.uid() |
| `categorias` | `usuario_id` | SELECT WHERE usuario_id = auth.uid() |
| `transacoes` | `usuario_id` | SELECT WHERE usuario_id = auth.uid() |
| `orcamentos` | `usuario_id` | SELECT WHERE usuario_id = auth.uid() |
| `metas` | `usuario_id` | SELECT WHERE usuario_id = auth.uid() |
| `operacoes` | `usuario_id` | SELECT WHERE usuario_id = auth.uid() |
| `proventos` | `usuario_id` | SELECT WHERE usuario_id = auth.uid() |

**Dados de mercado e tabelas backend-only:**

Mesmo dados públicos de mercado não precisam ficar expostos pela Data API. Desde as
migrations `027` e `028`, todas as tabelas de `public` e `market` têm RLS. Objetos sem
uso REST possuem policy restritiva `data_api_private_deny` e grants de `anon` e
`authenticated` revogados. O schema `market` inteiro é privado para esses papéis.

**Atenção:**
O role `app4_reader` não usa `auth.uid()` (é uma conexão direta PostgreSQL).
Por isso, toda query do app deve incluir `WHERE usuario_id = :owner_id`
mesmo quando RLS está ativa. As duas camadas são complementares.

---

## REGRA 4 — Backup antes de qualquer operação no banco

**Regra:**
Nenhuma operação que altera o schema ou os dados pode ser executada sem um
backup verificado realizado nas últimas 24 horas.

**O que constitui um backup válido:**
- Arquivo `.sql` gerado pelo Supabase (Settings → Database → Backups)
- Arquivo tem tamanho > 0 bytes
- Arquivo foi aberto e inspecionado (não está corrompido)
- Arquivo está armazenado localmente fora do repositório (não commitar se tiver dados)

**Fases que exigem backup antes de iniciar:**
- Fase 4.5 — aplicação dos scripts DDL no Supabase
- Fase 4.7 — migração de dados

**Formato recomendado para nome do arquivo:**
```
dump_dashboard_financeiro_AAAAMMDD_HHMM.sql
dump_controle_financeiro_AAAAMMDD_HHMM.sql
```

---

## REGRA 5 — Logs de migração obrigatórios

**Regra:**
Toda execução de migração de dados deve gerar um log registrando o que foi feito.

**Conteúdo mínimo do log:**

```markdown
# Log de Migração — AAAA-MM-DD HH:MM

## Contexto
- Fase: 4.7
- Origem: [nome do projeto Supabase ou SQLite]
- Destino: Dashboard Financeiro (banco unificado)

## Resultado por tabela

| Tabela | Registros na origem | Registros no destino antes | Registros inseridos | dry_run |
|--------|--------------------:|---------------------------:|--------------------:|:-------:|
| transacoes | X | Y | Z | false |

## Erros encontrados
- (nenhum) ou lista de erros

## Verificação pós-migração
- [ ] Contagens conferem
- [ ] FK violations = 0
- [ ] Somas financeiras conferem

## Decisão
- Migração aprovada / revertida (motivo)
```

**Onde salvar:**
`supabase_unificado/validation/log_migracao_AAAAMMDD.md`

---

## REGRA 6 — Proibição de operações destrutivas sem autorização manual

**Regra:**
As seguintes operações **nunca** podem ser geradas automaticamente, executadas
automaticamente ou incluídas em scripts sem aprovação explícita do proprietário:

| Operação proibida | Exceção (requer aprovação explícita) |
|-------------------|-------------------------------------|
| `DROP TABLE` | Somente após backup verificado + autorização escrita do proprietário |
| `DROP SCHEMA` | Somente após backup verificado + autorização escrita do proprietário |
| `TRUNCATE` | Somente em ambiente de teste + autorização escrita do proprietário |
| `DELETE FROM ... WHERE ...` | Somente para remover dado específico + autorização escrita |
| `DELETE FROM ...` (sem WHERE) | Proibido absolutamente — equivale a TRUNCATE |
| `ALTER TABLE ... DROP COLUMN` | Somente após confirmação de que a coluna não tem dados úteis |
| `UPDATE ... SET ... WHERE ...` | Somente para correção de dado específico + autorização |

**O Claude nunca vai:**
- Gerar scripts com `DROP TABLE` não autorizado
- Executar `DELETE` em qualquer tabela automaticamente
- Sugerir `TRUNCATE` como solução para limpeza de dados

**Autorização manual = mensagem explícita do proprietário confirmando:**
1. Que o backup foi feito e verificado
2. Qual operação específica foi autorizada
3. Em qual tabela
4. Sob qual condição (`WHERE`)

---

## REGRA 7 — Migrations versionadas

**Regra:**
Todo script SQL que altera o schema deve ser versionado com número sequencial
e nunca modificado após ser aplicado.

**Convenção de nomes:**
```
NNN_descricao_curta.sql
001_usuarios.sql
002_contas.sql
...
012_rls_policies.sql
013_add_coluna_xxx.sql    ← adicao futura
```

**Princípios:**
- Scripts são imutáveis após aplicação — correcoes geram novo script (`014_fix_xxx.sql`)
- Scripts nunca contêm `DROP TABLE` ou operações destrutivas
- Cada script deve ser idempotente quando possível (`IF NOT EXISTS`, `IF NOT EXISTS`)
- Cada script deve ter comentário de cabeçalho com data e objetivo

**Formato de cabeçalho obrigatório:**
```sql
-- Migration: NNN_nome.sql
-- Data: AAAA-MM-DD
-- Objetivo: <descricao clara>
-- Pre-requisito: <script anterior ou "nenhum">
-- Reversao: <instrucoes para desfazer SE necessario e autorizado>
-- Autor: <humano ou Claude>
-- Status: gerado / revisado / aplicado
```

---

## REGRA 8 — Validação de somatórios financeiros

**Regra:**
Após qualquer migração de dados, os somatórios financeiros do destino devem
ser comparados com os somatórios da origem antes de declarar a migração concluída.

**Queries de validação obrigatórias:**

```sql
-- Soma total de transacoes (verificar contra origem)
SELECT
    DATE_TRUNC('month', data) AS mes,
    SUM(CASE WHEN valor > 0 THEN valor ELSE 0 END) AS total_receitas,
    SUM(CASE WHEN valor < 0 THEN valor ELSE 0 END) AS total_despesas,
    COUNT(*) AS total_registros
FROM transacoes
WHERE usuario_id = '<OWNER_USER_ID>'
GROUP BY 1
ORDER BY 1;
```

```sql
-- Soma total de operacoes de investimento
SELECT
    tipo,
    COUNT(*) AS total_operacoes,
    SUM(quantidade * preco_unitario) AS volume_total
FROM operacoes
WHERE usuario_id = '<OWNER_USER_ID>'
GROUP BY tipo;
```

```sql
-- Soma de proventos recebidos
SELECT
    tipo_provento,
    COUNT(*) AS total_registros,
    SUM(valor_liquido) AS total_recebido
FROM proventos
WHERE usuario_id = '<OWNER_USER_ID>'
GROUP BY tipo_provento;
```

**Critério de aprovação:**
Os totais do destino devem ser iguais (ou justificadamente diferentes, com
explicação registrada no log) aos totais da origem.

---

## Checklist Geral de Segurança

> Usar antes de avançar para qualquer fase que envolva banco de dados.

```
[ ] SERVICE_ROLE_KEY nunca comittada no repositório
[ ] SERVICE_ROLE_KEY nunca copiada para documentação
[ ] SERVICE_ROLE_KEY nunca usada em código que roda no Streamlit Cloud
[ ] ANON_KEY nunca comittada com valor real
[ ] .env está no .gitignore
[ ] substituir a conexão `postgres` do app por roles mínimos separados para leitura/ETL
[ ] app4_reader tem apenas SELECT (confirmar com \du no psql ou equivalente)
[ ] app4_reader não tem BYPASSRLS
[ ] Toda query do app inclui WHERE usuario_id = :owner_id
[ ] RLS habilitada em todas as tabelas de `public` e `market`
[ ] tabelas backend-only sem grants para `anon`/`authenticated`
[ ] Backup verificado antes de operação DDL ou DML destrutiva
[ ] Log de migração criado e salvo em supabase_unificado/validation/
[ ] Somatórios financeiros comparados origem × destino
[ ] Nenhum DROP TABLE / TRUNCATE / DELETE executado sem autorização escrita
[ ] Scripts SQL versionados com cabeçalho de data e objetivo
[ ] Migrations numeradas sequencialmente e nunca modificadas após aplicação
```

---

## Referências

- `docs/estrategia_supabase_unificado_plano_gratuito.md` — decisão arquitetural
- `docs/banco_unificado_fases.md` — plano de execução completo
- `etl/schema_setup.py` — DDL das 10 tabelas
- `core/auth.py` — gate de autenticação do app
- `supabase_unificado/schema/` — scripts DDL versionados
- `supabase_unificado/migrations/` — scripts ETL de migração
- `supabase_unificado/validation/` — logs e relatórios
