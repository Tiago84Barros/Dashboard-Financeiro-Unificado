-- Taxonomia inicial somente para séries cuja natureza é declarada pela fonte.
UPDATE macro_indicators SET category='monetary_policy' WHERE provider='fred' AND provider_code IN ('FEDFUNDS','DGS10');
UPDATE macro_indicators SET category='inflation' WHERE provider='fred' AND provider_code='CPIAUCSL';
UPDATE macro_indicators SET category='employment' WHERE provider='fred' AND provider_code='UNRATE';
UPDATE macro_indicators SET category='economic_activity' WHERE provider='fred' AND provider_code='GDPC1';
UPDATE macro_indicators SET category='credit_liquidity' WHERE provider='fred' AND provider_code='BAMLH0A0HYM2';
UPDATE macro_indicators SET category='monetary_policy' WHERE provider='bis' AND provider_code='WS_CBPOL|D.US';
UPDATE macro_indicators SET category='employment' WHERE provider='oecd' AND provider_code LIKE 'OECD.SDD.TPS%DF_IALFS_UNE_M%';
UPDATE macro_indicators SET category='inflation' WHERE provider='eurostat' AND provider_code LIKE 'prc_hicp_manr%';
UPDATE macro_indicators SET category='currencies' WHERE provider='ecb' AND provider_code LIKE 'EXR|%';
UPDATE macro_indicators SET category='economic_activity' WHERE provider='world_bank' AND provider_code='NY.GDP.MKTP.KD.ZG';
UPDATE macro_indicators SET category='inflation' WHERE provider='world_bank' AND provider_code='FP.CPI.TOTL.ZG';
UPDATE macro_indicators SET category='debt' WHERE provider='world_bank' AND provider_code='GC.DOD.TOTL.GD.ZS';
