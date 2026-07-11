-- migration/remap_tickers_brapi_b3.sql
-- Reconciliação de tickers brapi -> B3 (jul/2026)
--
-- A ingestão da brapi gravou algumas empresas sob símbolos que NÃO batem com o
-- ticker de negociação da B3 usado pelo app (public.setores). Causas: rebrand
-- (Eletrobras->Axia, CCR->Motiva, Creditas), classe trocada (Azul ON x PN) ou
-- erro de símbolo da própria brapi (Embraer como EMBJ3). Como o app faz join
-- pelo ticker-B3, essas empresas ficavam invisíveis / sem setor.
--
-- Correção: reescrever o ticker do dado market.* (brapi -> B3) para as 9
-- empresas CONFIRMADAS por CVM (mesmo codigo_cvm nos dois símbolos, brapi com
-- métricas+DRE anuais, ticker-B3 sem colisão). Já APLICADA no banco; este
-- arquivo é o registro reproduzível + tabela de alias para durabilidade.
--
-- IMPORTANTE (durabilidade): a próxima ingestão da brapi ainda pode recriar os
-- símbolos antigos (AXIA3, EMBJ3, ...). Antes de re-ingerir, a normalização em
-- data_pipeline/market/ingest.py deve consultar market.ticker_alias e gravar
-- sob o b3_ticker. Enquanto isso não é feito, re-rodar a ingestão pode desfazer
-- este remap.

BEGIN;

-- 1) Tabela de alias: símbolo brapi -> ticker B3 (registro + futura ingestão)
CREATE TABLE IF NOT EXISTS market.ticker_alias(
    brapi_symbol text PRIMARY KEY,
    b3_ticker    text NOT NULL,
    codigo_cvm   integer,
    motivo       text,
    created_at   timestamptz DEFAULT now()
);

INSERT INTO market.ticker_alias(brapi_symbol, b3_ticker, codigo_cvm, motivo) VALUES
    ('AXIA3','ELET3',2437 ,'rebrand Eletrobras->Axia'),
    ('MOTV3','CCRO3',18821,'rebrand CCR->Motiva'),
    ('EMBJ3','EMBR3',20087,'erro simbolo brapi'),
    ('AZUL3','AZUL4',24112,'classe ON/PN'),
    ('RIAA3','GUAR3',4669 ,'rebrand/simbolo'),
    ('APTI4','APTI3',12823,'classe/simbolo'),
    ('FIEI3','CRDE3',20630,'rebrand Creditas'),
    ('ARND3','NINJ3',25887,'simbolo brapi'),
    ('ODER4','ODER3',4693 ,'classe Oderich')
ON CONFLICT (brapi_symbol) DO UPDATE
    SET b3_ticker = EXCLUDED.b3_ticker, codigo_cvm = EXCLUDED.codigo_cvm;

-- 2) Reescreve o ticker nas tabelas de dados que o app lê (brapi -> B3).
--    Idempotente: se já reescrito, o WHERE em upper(ticker)=brapi não casa nada.
DO $$
DECLARE
    t text;
    tabelas text[] := ARRAY['assets','balance_sheets','calculated_metric_vintages',
        'calculated_metrics','cash_flow_statements','dividends',
        'historical_prices','income_statements'];
BEGIN
    FOREACH t IN ARRAY tabelas LOOP
        EXECUTE format($f$
            UPDATE market.%I d SET ticker = m.b3_ticker
            FROM market.ticker_alias m
            WHERE upper(d.ticker) = m.brapi_symbol
        $f$, t);
    END LOOP;
END $$;

COMMIT;

-- Reversão (se necessário): trocar o sentido do UPDATE (b3_ticker -> brapi_symbol)
-- usando os CSVs de backup em scratchpad/remap_backup_*.csv como conferência.
