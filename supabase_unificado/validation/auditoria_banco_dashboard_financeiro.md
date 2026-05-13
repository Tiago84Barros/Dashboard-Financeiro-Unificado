# Auditoria — Banco Dashboard Financeiro

> Executada em: 2026-05-13
> Fase: 4.1 — Auditoria dos Bancos Atuais
> Método: Python + SQLAlchemy (leitura direta)
> Banco: Dashboard Financeiro (projeto Supabase — banco unificado do App 4)

---

## Resultado da Conexão

```
Banco:    postgres
Usuário:  postgres
Servidor: aws-1-sa-east-1.pooler.supabase.com:5432
Versão:   PostgreSQL 17.6 on aarch64-unknown-linux-gnu
```

---

## Q1 — Tabelas Existentes (antes da Fase 4.5)

**14 tabelas encontradas no schema `public` — todas do App 1 (Dashboard Financeiro):**

| Tabela | Colunas | Domínio |
|--------|:-------:|---------|
| `Demonstracoes_Financeiras` | 35 | Demonstrações financeiras anuais por ticker |
| `Demonstracoes_Financeiras_TRI` | 35 | Demonstrações financeiras trimestrais por ticker |
| `cvm_to_ticker` | 4 | Mapeamento código CVM → ticker |
| `docs_corporativos` | 22 | Documentos corporativos (RI, fatos relevantes) |
| `docs_corporativos_chunks` | 13 | Chunks de documentos para embeddings (pgvector) |
| `info_economica` | 12 | Dados econômicos (SELIC, câmbio, IPCA, PIB, etc.) |
| `info_economica_mensal` | 12 | Dados econômicos mensais |
| `multiplos` | 25 | Múltiplos financeiros anuais por ticker |
| `multiplos_TRI` | 25 | Múltiplos financeiros trimestrais por ticker |
| `patch6_runs` | 8 | Controle de execuções de análise |
| `portfolio_snapshot_analysis` | 31 | Análise de portfólio por snapshot |
| `portfolio_snapshot_items` | 5 | Itens por snapshot de portfólio |
| `portfolio_snapshots` | 9 | Snapshots de portfólio |
| `setores` | 6 | Setores e segmentos por ticker |

**Conflito com tabelas do App 4:** ❌ Nenhum — todos os 22 nomes canônicos são distintos.

---

## Q2 — Contagem de Registros

| Tabela | Registros |
|--------|----------:|
| `docs_corporativos_chunks` | 16.052 |
| `Demonstracoes_Financeiras_TRI` | 12.905 |
| `multiplos_TRI` | 11.301 |
| `multiplos` | 4.598 |
| `Demonstracoes_Financeiras` | 4.598 |
| `docs_corporativos` | 894 |
| `cvm_to_ticker` | 345 |
| `setores` | 266 |
| `portfolio_snapshot_items` | 224 |
| `info_economica_mensal` | 193 |
| `portfolio_snapshot_analysis` | 166 |
| `patch6_runs` | 79 |
| `portfolio_snapshots` | 20 |
| `info_economica` | 17 |

---

## Q3 — Policies RLS

**Nenhuma policy RLS encontrada** nas 14 tabelas existentes.
O App 1 usa conexão via `service_role_key` (bypassa RLS) ou `anon_key` sem políticas.

---

## Q4 — Schemas Existentes

Schemas relevantes (além dos internos do PostgreSQL e Supabase):
- `public` — tabelas dos apps
- `auth` — autenticação Supabase
- `storage` — Supabase Storage
- `vault` — Supabase Vault
- `extensions` — extensões PostgreSQL
- `graphql` / `graphql_public` — GraphQL (Supabase)

---

## Q5 — Foreign Keys

| Tabela Origem | Coluna | Tabela Referenciada | Coluna |
|---------------|--------|---------------------|--------|
| `docs_corporativos_chunks` | `doc_id` | `docs_corporativos` | `id` |
| `patch6_runs` | `snapshot_id` | `portfolio_snapshots` | `id` |
| `portfolio_snapshot_analysis` | `snapshot_id` | `portfolio_snapshots` | `id` |
| `portfolio_snapshot_items` | `snapshot_id` | `portfolio_snapshots` | `id` |

---

## Q6 — Extensões Instaladas

| Extensão | Versão | Uso no App 4 |
|----------|--------|-------------|
| `pgcrypto` | 1.3 | `gen_random_uuid()` para PKs |
| `uuid-ossp` | 1.1 | Alternativa para UUIDs |
| `vector` | 0.8.0 | pgvector — embeddings (App 1) |
| `pg_stat_statements` | 1.11 | Monitoramento de queries |
| `plpgsql` | 1.0 | PL/pgSQL (linguagem procedural) |
| `supabase_vault` | 0.3.1 | Secrets management |

---

## Decisões Tomadas com Base na Auditoria

| Decisão | Escolha | Justificativa |
|---------|---------|--------------|
| DM-012: schema `public` vs. `app4` | **`public`** | 14 tabelas existentes sem conflito de nomes com as 22 do App 4 |
| DM-001: nomes em inglês | **Confirmado** | Banco não tinha as 10 tabelas em português — criar direto em inglês |
| RENAME necessário? | **Não** | As tabelas `usuarios/contas/etc.` nunca foram criadas neste banco |

---

## Resultado da Fase 4.5

Após a auditoria, as 22 tabelas canônicas foram aplicadas imediatamente.
Ver `docs/status_fase_4.md` para o resultado completo.

**Total de tabelas no banco após Fase 4.5:** 36 (14 App 1 + 22 App 4)
