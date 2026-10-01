-- 078_companies_datas_cvm.sql — datas do cadastro CVM em market.companies.
--
-- Rodar À MÃO no SQL Editor do Supabase (e no armazém local, se quiser o
-- espelho completo). Idempotente: pode rodar de novo.
--
-- Por que:
--
-- O chat de Análise de empresas (Empresas B3) não sabia dizer quando a
-- companhia foi fundada nem desde quando é aberta — a lacuna b23e53d5
-- registrou isso para SOND5. O dado existe e o projeto já o baixa:
-- cad_cia_aberta.csv traz DT_CONST (constituição) e DT_REG (registro na CVM),
-- mas core/cvm_cadastro.parse_cad os descartava.
--
-- O que as colunas NÃO são:
--
-- dt_registro_cvm é a data do registro de companhia aberta na CVM, não a da
-- listagem na B3 (a CVM não publica esta). O contexto da LLM diz isso.
--
-- Custo: 390 linhas x 2 datas + 2 textos curtos, poucos KB.
--
-- Depois de aplicar, popular com:
--   python -c "from data_pipeline.market.ingest import cadastro; print(cadastro(apply=True))"

ALTER TABLE market.companies
    ADD COLUMN IF NOT EXISTS dt_constituicao DATE,
    ADD COLUMN IF NOT EXISTS dt_registro_cvm DATE,
    ADD COLUMN IF NOT EXISTS categoria_registro TEXT,
    ADD COLUMN IF NOT EXISTS controle_acionario TEXT;

COMMENT ON COLUMN market.companies.dt_constituicao IS
    'DT_CONST do cad_cia_aberta.csv (CVM): data de constituição da companhia.';
COMMENT ON COLUMN market.companies.dt_registro_cvm IS
    'DT_REG do cad_cia_aberta.csv (CVM): registro de companhia aberta na CVM. Não é a data de listagem na B3.';
COMMENT ON COLUMN market.companies.categoria_registro IS
    'CATEG_REG do cad_cia_aberta.csv (CVM): Categoria A ou B.';
COMMENT ON COLUMN market.companies.controle_acionario IS
    'CONTROLE_ACIONARIO do cad_cia_aberta.csv (CVM): privado, estatal, estrangeiro...';
