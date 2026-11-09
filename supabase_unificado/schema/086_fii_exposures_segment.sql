-- 086: exposicao `segment` em market.fii_exposures.
--
-- O informe mensal da CVM (inf_mensal_fii_geral) declara `Segmento_Atuacao`
-- (Lajes Corporativas, Shoppings, Logistica, ...). O parser 1.8.0 grava esse
-- rotulo como exposicao `segment`, peso 1.0, com o knowledge_at da entrega do
-- mensal. Ele e RESERVA do `sector` por inquilino (informe trimestral), que
-- continua a valer quando existe; o PIT e o estado corrente so usam o
-- cadastral quando nenhum setor por inquilino e conhecido na data da decisao.
-- Motivo: o ZIP anual da CVM guarda so a ultima versao do trimestral, e a
-- reapresentacao empurra o knowledge_at do setor meses para a frente.
--
-- Aplicar no warehouse local (dfu_warehouse) antes de reingerir o mensal.
-- No Supabase, so se a tabela receber exposicoes do parser 1.8.0.
ALTER TABLE market.fii_exposures
    DROP CONSTRAINT IF EXISTS fii_exposures_exposure_type_check;
ALTER TABLE market.fii_exposures
    ADD CONSTRAINT fii_exposures_exposure_type_check CHECK (exposure_type IN
        ('manager','sector','tenant','debtor','issuer','indexer','region',
         'holding','security','asset_class','segment'));
