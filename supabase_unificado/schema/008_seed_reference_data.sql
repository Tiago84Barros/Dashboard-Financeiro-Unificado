-- ============================================================
-- 008_seed_reference_data.sql
-- Dados de referência: benchmarks e categorias padrão do sistema
-- Banco: Dashboard Financeiro Unificado (Supabase — schema public)
-- Fase 4.3 — Scripts SQL não destrutivos
-- Criado: 2026-05-13
--
-- SEGURANÇA:
--   Não contém DROP TABLE, TRUNCATE, DELETE, DROP SCHEMA.
--   Todos os INSERT usam ON CONFLICT DO NOTHING — idempotente.
--   Seguro para executar múltiplas vezes.
--   Nenhuma credencial ou dado pessoal hardcoded.
--
-- DADOS INSERIDOS:
--   benchmarks (5): IBOV, CDI, IPCA, SELIC, IFIX
--   categories (23): categorias do sistema (user_id = NULL)
--                    visíveis a todos os usuários via RLS
--
-- DADOS NÃO INSERIDOS AQUI (inserir manualmente):
--   financial_institutions — cadastrar via Supabase Table Editor
--   profiles / user_settings — criados pelo fluxo de autenticação
--   asset_quotes / benchmark_quotes — populados pela Fase 4.7 (API)
--
-- DEPENDÊNCIAS: 001 a 007 (todas as tabelas e views devem existir)
-- FASE SEGUINTE: 4.4 — ETL Python (migração dos dados históricos)
-- ============================================================

-- ────────────────────────────────────────────────────────────
-- PARTE 1: Benchmarks de referência
-- Seed dos 5 índices/taxas principais usados no Dashboard.
-- ON CONFLICT (code) DO NOTHING = idempotente.
-- Cotações históricas serão populadas pela Fase 4.7 via yfinance / BCB API.
-- ────────────────────────────────────────────────────────────

INSERT INTO benchmarks (id, code, name, type, frequency, description)
VALUES
    (
        gen_random_uuid(),
        'IBOV',
        'Ibovespa',
        'index',
        'daily',
        'Índice de desempenho médio das cotações dos ativos de maior negociabilidade '
        'e representatividade da B3. Principal benchmark de ações brasileiras.'
    ),
    (
        gen_random_uuid(),
        'CDI',
        'CDI — Certificado de Depósito Interbancário',
        'rate',
        'daily',
        'Taxa de juros praticada entre os bancos nas operações de curtíssimo prazo. '
        'Principal benchmark de renda fixa. Fornecida pela B3/CETIP.'
    ),
    (
        gen_random_uuid(),
        'IPCA',
        'IPCA — Índice de Preços ao Consumidor Amplo',
        'rate',
        'monthly',
        'Índice oficial de inflação do Brasil, calculado pelo IBGE. '
        'Usado para medir o poder de compra e corrigir ativos de renda fixa atrelados à inflação.'
    ),
    (
        gen_random_uuid(),
        'SELIC',
        'SELIC — Taxa Básica de Juros',
        'rate',
        'daily',
        'Taxa básica de juros da economia brasileira, definida pelo COPOM/BCB. '
        'Referência para o custo do dinheiro e investimentos em Tesouro Selic.'
    ),
    (
        gen_random_uuid(),
        'IFIX',
        'IFIX — Índice de Fundos de Investimentos Imobiliários',
        'index',
        'daily',
        'Índice de desempenho médio das cotações dos fundos imobiliários (FIIs) '
        'listados na B3. Principal benchmark para investidores de FIIs.'
    )
ON CONFLICT (code) DO NOTHING;

COMMENT ON TABLE benchmarks IS
    'Índices e taxas de referência (IBOVESPA, CDI, IPCA, SELIC, IFIX). '
    'Seed em 008. Cotações históricas populadas na Fase 4.7.';

-- ────────────────────────────────────────────────────────────
-- PARTE 2: Categorias do sistema (user_id = NULL)
-- Visíveis a todos os usuários (RLS: user_id IS NULL OR user_id = auth.uid()).
-- ON CONFLICT (id) DO NOTHING não se aplica — usar nome como chave de negócio.
-- Idempotência garantida via DO $$ + INSERT ... WHERE NOT EXISTS.
-- ────────────────────────────────────────────────────────────

-- ── Categorias de receita ─────────────────────────────────────

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Salário' AND user_id IS NULL) THEN
        INSERT INTO categories (id, user_id, name, type, icon, color)
        VALUES (gen_random_uuid(), NULL, 'Salário', 'income', 'briefcase', '#22c55e');
    END IF;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Freelance' AND user_id IS NULL) THEN
        INSERT INTO categories (id, user_id, name, type, icon, color)
        VALUES (gen_random_uuid(), NULL, 'Freelance', 'income', 'laptop', '#16a34a');
    END IF;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Investimentos' AND user_id IS NULL) THEN
        INSERT INTO categories (id, user_id, name, type, icon, color)
        VALUES (gen_random_uuid(), NULL, 'Investimentos', 'income', 'trending-up', '#15803d');
    END IF;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Aluguel Recebido' AND user_id IS NULL) THEN
        INSERT INTO categories (id, user_id, name, type, icon, color)
        VALUES (gen_random_uuid(), NULL, 'Aluguel Recebido', 'income', 'home', '#4ade80');
    END IF;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Outros Rendimentos' AND user_id IS NULL) THEN
        INSERT INTO categories (id, user_id, name, type, icon, color)
        VALUES (gen_random_uuid(), NULL, 'Outros Rendimentos', 'income', 'plus-circle', '#86efac');
    END IF;
END; $$;

-- ── Categorias de despesa ─────────────────────────────────────

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Moradia' AND user_id IS NULL) THEN
        INSERT INTO categories (id, user_id, name, type, icon, color)
        VALUES (gen_random_uuid(), NULL, 'Moradia', 'expense', 'home', '#ef4444');
    END IF;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Alimentação' AND user_id IS NULL) THEN
        INSERT INTO categories (id, user_id, name, type, icon, color)
        VALUES (gen_random_uuid(), NULL, 'Alimentação', 'expense', 'utensils', '#f97316');
    END IF;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Transporte' AND user_id IS NULL) THEN
        INSERT INTO categories (id, user_id, name, type, icon, color)
        VALUES (gen_random_uuid(), NULL, 'Transporte', 'expense', 'car', '#eab308');
    END IF;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Saúde' AND user_id IS NULL) THEN
        INSERT INTO categories (id, user_id, name, type, icon, color)
        VALUES (gen_random_uuid(), NULL, 'Saúde', 'expense', 'heart', '#ec4899');
    END IF;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Educação' AND user_id IS NULL) THEN
        INSERT INTO categories (id, user_id, name, type, icon, color)
        VALUES (gen_random_uuid(), NULL, 'Educação', 'expense', 'book-open', '#8b5cf6');
    END IF;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Lazer' AND user_id IS NULL) THEN
        INSERT INTO categories (id, user_id, name, type, icon, color)
        VALUES (gen_random_uuid(), NULL, 'Lazer', 'expense', 'smile', '#06b6d4');
    END IF;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Vestuário' AND user_id IS NULL) THEN
        INSERT INTO categories (id, user_id, name, type, icon, color)
        VALUES (gen_random_uuid(), NULL, 'Vestuário', 'expense', 'shopping-bag', '#f43f5e');
    END IF;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Assinaturas' AND user_id IS NULL) THEN
        INSERT INTO categories (id, user_id, name, type, icon, color)
        VALUES (gen_random_uuid(), NULL, 'Assinaturas', 'expense', 'repeat', '#6366f1');
    END IF;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Telefone / Internet' AND user_id IS NULL) THEN
        INSERT INTO categories (id, user_id, name, type, icon, color)
        VALUES (gen_random_uuid(), NULL, 'Telefone / Internet', 'expense', 'wifi', '#64748b');
    END IF;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Pets' AND user_id IS NULL) THEN
        INSERT INTO categories (id, user_id, name, type, icon, color)
        VALUES (gen_random_uuid(), NULL, 'Pets', 'expense', 'heart', '#fb923c');
    END IF;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Impostos e Taxas' AND user_id IS NULL) THEN
        INSERT INTO categories (id, user_id, name, type, icon, color)
        VALUES (gen_random_uuid(), NULL, 'Impostos e Taxas', 'expense', 'file-text', '#94a3b8');
    END IF;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Seguros' AND user_id IS NULL) THEN
        INSERT INTO categories (id, user_id, name, type, icon, color)
        VALUES (gen_random_uuid(), NULL, 'Seguros', 'expense', 'shield', '#475569');
    END IF;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Presentes e Doações' AND user_id IS NULL) THEN
        INSERT INTO categories (id, user_id, name, type, icon, color)
        VALUES (gen_random_uuid(), NULL, 'Presentes e Doações', 'expense', 'gift', '#d946ef');
    END IF;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Outras Despesas' AND user_id IS NULL) THEN
        INSERT INTO categories (id, user_id, name, type, icon, color)
        VALUES (gen_random_uuid(), NULL, 'Outras Despesas', 'expense', 'more-horizontal', '#cbd5e1');
    END IF;
END; $$;

-- ── Categorias de transferência ───────────────────────────────

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Transferência entre Contas' AND user_id IS NULL) THEN
        INSERT INTO categories (id, user_id, name, type, icon, color)
        VALUES (gen_random_uuid(), NULL, 'Transferência entre Contas', 'transfer', 'arrow-right-left', '#94a3b8');
    END IF;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Aporte em Investimento' AND user_id IS NULL) THEN
        INSERT INTO categories (id, user_id, name, type, icon, color)
        VALUES (gen_random_uuid(), NULL, 'Aporte em Investimento', 'transfer', 'trending-up', '#0ea5e9');
    END IF;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Resgate de Investimento' AND user_id IS NULL) THEN
        INSERT INTO categories (id, user_id, name, type, icon, color)
        VALUES (gen_random_uuid(), NULL, 'Resgate de Investimento', 'transfer', 'download', '#38bdf8');
    END IF;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Pagamento de Fatura' AND user_id IS NULL) THEN
        INSERT INTO categories (id, user_id, name, type, icon, color)
        VALUES (gen_random_uuid(), NULL, 'Pagamento de Fatura', 'transfer', 'credit-card', '#7dd3fc');
    END IF;
END; $$;

-- ────────────────────────────────────────────────────────────
-- NOTA: user_settings
-- Não inserir aqui — user_settings é criado automaticamente
-- pelo trigger ou pelo código da aplicação após criar um perfil.
-- Inserção manual via:
--   INSERT INTO user_settings (user_id) VALUES ('<uuid>')
--   ON CONFLICT (user_id) DO NOTHING;
-- ────────────────────────────────────────────────────────────

-- ────────────────────────────────────────────────────────────
-- NOTA: financial_institutions
-- Cadastrar via Supabase Table Editor ou inserção manual.
-- Exemplo:
--   INSERT INTO financial_institutions (name, type, bank_code)
--   VALUES ('Nubank', 'digital_bank', '260')
--   ON CONFLICT DO NOTHING;
-- ────────────────────────────────────────────────────────────
