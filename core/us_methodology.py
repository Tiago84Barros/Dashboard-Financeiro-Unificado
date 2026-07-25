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
US_FUNDAMENTAL_SCORE_VERSION = "0.5.0"

# Score de assimetria da aba "Empresas Fora da Curva".
US_ASYMMETRY_SCORE_VERSION = "0.1.0"
