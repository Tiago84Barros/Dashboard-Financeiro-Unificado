"""Identidade da metodologia Empresas Americanas (compartilhada UI ↔ persistência).

Espelha core/b3_methodology.py. Versões separadas por trilha para que os scores
sejam versionados de forma point-in-time em market_us.score_vintages.
"""

# Schema físico das tabelas market_us.* (migration 040).
US_SCHEMA_VERSION = 1

# Score fundamentalista (Qualidade/Crescimento/Solidez/Eficiência/Valuation/Retorno).
US_FUNDAMENTAL_SCORE_VERSION = "0.4.0"

# Score de assimetria da aba "Empresas Fora da Curva".
US_ASYMMETRY_SCORE_VERSION = "0.1.0"
