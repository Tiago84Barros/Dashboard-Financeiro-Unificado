"""Identidade da metodologia Empresas Americanas (compartilhada UI ↔ persistência).

Espelha core/b3_methodology.py. Versões separadas por trilha para que os scores
sejam versionados de forma point-in-time em market_us.score_vintages.
"""

# Schema físico das tabelas market_us.* (migration 040).
US_SCHEMA_VERSION = 1

# Score fundamentalista (Qualidade/Crescimento/Solidez/Eficiência/Valuation/Retorno).
# 0.5.0 (auditoria 2026-07): stock-based compensation e diluição passam a ser
# fatores explícitos — sbc_to_revenue e fcf_ex_sbc_margin em Qualidade,
# share_count_cagr_3y em Retorno ao acionista. Scores anteriores continuam
# consultáveis em market_us.score_vintages pela versão antiga.
#
# 0.6.0 (2026-08-29): a trilha de Crescimento deixa de medir lucro operacional,
# LPA e fluxo de caixa por CAGR. A taxa composta não é definida com base ou
# ponta <= 0, e devolvia ausência para 1.159 das 1.976 empresas com par de anos
# de lucro operacional -- a maioria. Ausência não é nota baixa: ela derruba a
# COBERTURA da trilha, e por ela a confiança, de modo que a empresa no prejuízo
# era tratada como empresa sem demonstração. Entram no lugar taxas SIMÉTRICAS
# (Davis-Haltiwanger-Schuh), definidas através do zero e limitadas, sob nomes
# novos -- `op_income_growth_3y`, `eps_growth_3y`, `fcf_growth_3y`. Receita
# continua em CAGR: não fica negativa, e a taxa composta é a leitura familiar.
US_FUNDAMENTAL_SCORE_VERSION = "0.6.0"

# Score de assimetria da aba "Empresas Fora da Curva".
US_ASYMMETRY_SCORE_VERSION = "0.1.0"
