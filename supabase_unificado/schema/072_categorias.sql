-- 072_categorias.sql — categorias do Controle Financeiro configuráveis.
--
-- Rodar À MÃO no SQL Editor do Supabase. Idempotente: pode rodar de novo.
--
-- O que este arquivo conserta, e por que cada coisa:
--
-- 1. `active`. Não existia. Sem ela não há como tirar uma categoria do seletor
--    a não ser APAGANDO a linha — e apagar levaria junto a classificação dos
--    lançamentos que a usam, sem volta.
--
-- 2. Retipar, não inserir. `Exterior`, `Renda Fixa`, `Renda Variável` e
--    `Aporte em Investimento` já existem, como `type = 'transfer'`, e 21
--    lançamentos apontam para elas. Inserir cópias `investment` criaria DUAS
--    `Exterior` e partiria o histórico em duas fatias que nenhuma tela soma.
--    `Resgate de Investimento` fica em `transfer` de propósito: é o caminho
--    inverso do aporte.
--
-- 3. Três nomes do seletor não existiam no banco: `Dividendos`, `Restaurante`
--    e `Reserva de Despesa`. Escolher um deles gravava o lançamento com
--    `category_id = NULL`, em silêncio. `Outros` existia só como `expense`, e
--    a resolução do id não olhava o tipo — uma entrada classificada como
--    "Outros" recebia a categoria de DESPESA de mesmo nome.
--
-- 4. O CHECK declarado em `002_financial_tables.sql` NUNCA foi aplicado a este
--    banco (`pg_constraint` não tem nenhum CHECK em `categories` nem em
--    `transactions`), e o DDL declarado já não descreve o que está gravado:
--    `transactions.type` tem `investment`, que o CHECK do 002 rejeita, e
--    `transactions.source` tem `csv_migration`, idem. Declarar um CHECK que
--    contradiz o dado gravado é pior do que não declarar: a primeira migration
--    que tentasse aplicá-lo falharia sem explicar por quê. Aqui os CHECKs
--    passam a existir DE FATO, com os valores que o banco realmente contém.

BEGIN;

-- 1 ── arquivar sem apagar ───────────────────────────────────────────────────
ALTER TABLE categories
    ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE;

-- 2 ── o tipo `investment` passa a existir em categories ─────────────────────
UPDATE categories
   SET type = 'investment'
 WHERE type = 'transfer'
   AND name IN ('Aporte em Investimento', 'Exterior',
                'Renda Fixa', 'Renda Variável', 'Renda Variavel');

-- 3 ── os nomes do seletor que não existiam ──────────────────────────────────
INSERT INTO categories (id, user_id, name, type)
SELECT gen_random_uuid(), NULL, nome, tipo
  FROM (VALUES
            ('Dividendos',         'income'),
            ('Outros',             'income'),
            ('Restaurante',        'expense'),
            ('Reserva de Despesa', 'investment'),
            ('Outros',             'investment')
       ) AS novas(nome, tipo)
 WHERE NOT EXISTS (
           SELECT 1 FROM categories c
            WHERE lower(c.name) = lower(novas.nome)
              AND c.type = novas.tipo
              AND c.user_id IS NULL
       );

-- 4 ── uma categoria por (dono, nome, tipo) ──────────────────────────────────
-- COALESCE porque NULL nunca conflita com NULL: sem isso as categorias de
-- sistema (user_id IS NULL) poderiam ser duplicadas à vontade.
-- Conferido antes de criar: nenhuma duplicata existe hoje.
CREATE UNIQUE INDEX IF NOT EXISTS categories_dono_nome_tipo_uq
    ON categories (
        COALESCE(user_id, '00000000-0000-0000-0000-000000000000'::uuid),
        lower(name),
        type
    );

-- 5 ── os CHECKs passam a descrever o banco real ─────────────────────────────
ALTER TABLE categories   DROP CONSTRAINT IF EXISTS categories_type_check;
ALTER TABLE categories
    ADD CONSTRAINT categories_type_check
    CHECK (type IN ('income', 'expense', 'transfer', 'investment'));

ALTER TABLE transactions DROP CONSTRAINT IF EXISTS transactions_type_check;
ALTER TABLE transactions
    ADD CONSTRAINT transactions_type_check
    CHECK (type IN ('income', 'expense', 'transfer', 'investment'));

ALTER TABLE transactions DROP CONSTRAINT IF EXISTS transactions_source_check;
ALTER TABLE transactions
    ADD CONSTRAINT transactions_source_check
    CHECK (source IN ('manual', 'import', 'csv', 'csv_migration', 'open_banking'));

COMMIT;
