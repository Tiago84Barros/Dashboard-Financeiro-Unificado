"""Pipeline de ingestão Empresas Americanas (FMP → warehouse local market_us.*).

Namespace isolado do pipeline B3/FII (data_pipeline/market). A view NUNCA importa
este pacote — ele só é usado pela CLI run_us_ingest.py e por scripts de ingestão.
"""
