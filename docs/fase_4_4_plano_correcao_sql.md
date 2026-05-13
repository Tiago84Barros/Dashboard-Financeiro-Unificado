# Fase 4.4 — Plano de Correção SQL

**Data:** 2026-05-13
**Status:** ⏳ Aguardando aplicação após execução de 001–008
**Referência:** `docs/fase_4_4_revisao_humana_sql.md` — Problemas M01–M05

---

## Contexto

Este arquivo documenta as correções identificadas na revisão humana (Fase 4.4)
dos scripts SQL não destrutivos (Fase 4.3).

As correções devem ser aplicadas como um novo arquivo `009_schema_amendments.sql`
**antes de iniciar a migração de dados reais (Fase 4.6/4.7)**.

> ⚠️ NÃO criar este arquivo agora — aguardar aprovação e execução dos scripts 001–008 primeiro.

---

## Correções a implementar em 009_schema_amendments.sql

### M01 — `transactions.account_id` sem ON DELETE RESTRICT

**Problema:** Se uma conta for deletada diretamente no Table Editor do Supabase,
as transações vinculadas ficam órfãs (violação de integridade referencial).

**Correção:**
```sql
-- Adicionar ação ON DELETE RESTRICT à FK de transactions.account_id
ALTER TABLE transactions
    DROP CONSTRAINT transactions_account_id_fkey;

ALTER TABLE transactions
    ADD CONSTRAINT transactions_account_id_fkey
        FOREIGN KEY (account_id)
        REFERENCES accounts(id)
        ON DELETE RESTRICT;
```

**Quando aplicar:** Antes de inserir qualquer dado em `transactions`.

---

### M02 — FKs para `assets` sem ON DELETE RESTRICT

**Problema:** Ativos referenciados por posições, operações e dividendos não têm
proteção contra exclusão acidental.

**Correção:**
```sql
-- portfolio_positions.asset_id
ALTER TABLE portfolio_positions
    DROP CONSTRAINT portfolio_positions_asset_id_fkey;

ALTER TABLE portfolio_positions
    ADD CONSTRAINT portfolio_positions_asset_id_fkey
        FOREIGN KEY (asset_id)
        REFERENCES assets(id)
        ON DELETE RESTRICT;

-- investment_transactions.asset_id
ALTER TABLE investment_transactions
    DROP CONSTRAINT investment_transactions_asset_id_fkey;

ALTER TABLE investment_transactions
    ADD CONSTRAINT investment_transactions_asset_id_fkey
        FOREIGN KEY (asset_id)
        REFERENCES assets(id)
        ON DELETE RESTRICT;

-- dividends.asset_id
ALTER TABLE dividends
    DROP CONSTRAINT dividends_asset_id_fkey;

ALTER TABLE dividends
    ADD CONSTRAINT dividends_asset_id_fkey
        FOREIGN KEY (asset_id)
        REFERENCES assets(id)
        ON DELETE RESTRICT;
```

**Quando aplicar:** Antes de inserir dados em `portfolio_positions`, `investment_transactions` ou `dividends`.

---

### M03 — Ausência de policy INSERT para `profiles`

**Problema:** Nenhuma policy de INSERT para `profiles` — impossível criar perfil via Supabase API.

**Correção:**
```sql
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname = 'public'
                     AND tablename  = 'profiles'
                     AND policyname = 'profiles_owner_insert') THEN
        CREATE POLICY profiles_owner_insert ON profiles
            FOR INSERT
            WITH CHECK (id = auth.uid());
    END IF;
END; $$;
```

**Quando aplicar:** Somente se o app evoluir para criar perfis via Supabase API.
Para o App 4 atual (single-user, conexão direta como `postgres`), não é urgente.

---

### M04 — Sobreposição de políticas em `categories`

**Problema:** `categories_write_owner` (FOR ALL) inclui SELECT, sobrepondo-se
à `categories_read_owner_or_system` (FOR SELECT).

**Correção:**
```sql
-- Remover a policy ALL e substituir por INSERT, UPDATE, DELETE
DROP POLICY IF EXISTS categories_write_owner ON categories;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname = 'public'
                     AND tablename  = 'categories'
                     AND policyname = 'categories_write_owner') THEN
        CREATE POLICY categories_write_owner ON categories
            FOR INSERT
            WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname = 'public'
                     AND tablename  = 'categories'
                     AND policyname = 'categories_update_owner') THEN
        CREATE POLICY categories_update_owner ON categories
            FOR UPDATE
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname = 'public'
                     AND tablename  = 'categories'
                     AND policyname = 'categories_delete_owner') THEN
        CREATE POLICY categories_delete_owner ON categories
            FOR DELETE
            USING (user_id = auth.uid());
    END IF;
END; $$;
```

> Nota: `DROP POLICY IF EXISTS` é destrutivo (remove policy existente), mas é
> controlado e imediato — não há janela sem proteção pois as novas policies são
> criadas na mesma transação.

**Quando aplicar:** Antes de abrir o app para múltiplos usuários via Supabase API.

---

### M05 — CHECK constraint de `financial_institutions.type` incompleto

**Problema:** O CHECK atual é `('bank','broker','fintech','insurance')`.
O comentário no arquivo 008 menciona `'digital_bank'` como exemplo de valor,
mas esse valor não está no CHECK — causaria erro de INSERT.

**Opção A — Adicionar 'digital_bank' ao CHECK:**
```sql
ALTER TABLE financial_institutions
    DROP CONSTRAINT IF EXISTS financial_institutions_type_check;

ALTER TABLE financial_institutions
    ADD CONSTRAINT financial_institutions_type_check
        CHECK (type IN ('bank', 'broker', 'fintech', 'insurance', 'digital_bank'));
```

**Opção B — Corrigir o comentário no 008 para usar 'fintech':**
Não envolve SQL — apenas editar o comentário no arquivo `008_seed_reference_data.sql`
para usar `'fintech'` no lugar de `'digital_bank'` como exemplo.

**Recomendação:** Opção B (mais simples, sem ALTER TABLE). Nubank é uma `fintech`.

**Quando aplicar:** Antes de inserir dados em `financial_institutions`.

---

## Melhorias opcionais (B01–B05)

### B01 — Adicionar `created_at` às tabelas sem timestamp

```sql
ALTER TABLE financial_institutions ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE categories             ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE budgets                ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE assets                 ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE dividends              ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE benchmarks             ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
```

### B02 — Adicionar `updated_at` às tabelas que precisam rastrear mudanças

```sql
ALTER TABLE accounts        ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE financial_goals ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE debts            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
```

> Para atualizar automaticamente, criar triggers com `CREATE OR REPLACE FUNCTION set_updated_at()`
> que faz `NEW.updated_at = NOW()` — implementar na Fase 4.6.

### B03 — ON DELETE SET NULL nas FKs anuláveis

```sql
-- accounts.financial_institution_id
ALTER TABLE accounts DROP CONSTRAINT IF EXISTS accounts_financial_institution_id_fkey;
ALTER TABLE accounts ADD CONSTRAINT accounts_financial_institution_id_fkey
    FOREIGN KEY (financial_institution_id) REFERENCES financial_institutions(id) ON DELETE SET NULL;

-- cards.account_id
ALTER TABLE cards DROP CONSTRAINT IF EXISTS cards_account_id_fkey;
ALTER TABLE cards ADD CONSTRAINT cards_account_id_fkey
    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE SET NULL;

-- transactions.card_id
ALTER TABLE transactions DROP CONSTRAINT IF EXISTS transactions_card_id_fkey;
ALTER TABLE transactions ADD CONSTRAINT transactions_card_id_fkey
    FOREIGN KEY (card_id) REFERENCES cards(id) ON DELETE SET NULL;

-- transactions.category_id
ALTER TABLE transactions DROP CONSTRAINT IF EXISTS transactions_category_id_fkey;
ALTER TABLE transactions ADD CONSTRAINT transactions_category_id_fkey
    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL;
```

### B04 — `benchmark_quotes.daily_change_pct` — precisão

```sql
-- Aumentar para NUMERIC(10,6) para suportar valores como 999.999999%
ALTER TABLE benchmark_quotes
    ALTER COLUMN daily_change_pct TYPE NUMERIC(10, 6);
```

### B05 — Documentação de filtro obrigatório nas views

Não envolve SQL — adicionar comentário em cada view:

```sql
COMMENT ON VIEW v_net_worth IS
    'Patrimônio líquido total: bank_balance (contas ativas) + investment_total. '
    'OBRIGATÓRIO: filtrar por user_id = :owner_id no código da aplicação. '
    'Sem filtro, retorna dados de todos os usuários (postgres bypassa RLS).';
```

---

## Cronograma recomendado

| Fase | Ação |
|------|------|
| **Agora** | Executar 001–008. Não criar 009 ainda. |
| **Antes de 4.6** | Criar e aplicar 009 com M01, M02, M05 (B01 e B02 opcionais) |
| **4.6** | Aplicar M03 e M04 junto com scripts de migração |
| **4.9** | Revisar B05 (comentários de views) com o código final do app |

---

## Observação final

Estes ajustes são **non-breaking**: aplicar `ALTER TABLE ... ADD CONSTRAINT` em tabela vazia
é mais seguro e rápido. Se aplicados após inserção de dados, o PostgreSQL valida todas as
linhas existentes antes de aceitar a constraint — risco de erro se dados inconsistentes
já existirem.

**Recomendação:** Aplicar 009 logo após confirmar que 001–008 foram executados com sucesso
e antes de qualquer inserção de dados reais.
