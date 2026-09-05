"""Camada canônica de dados macroeconômicos internacionais.

Conectores só leem e traduzem a resposta de cada fonte. Persistência, sinais e
apresentação ficam em módulos separados para evitar que uma API determine a
interpretação econômica do APP4.
"""

from core.macro_data.models import MacroIndicator, MacroObservation, ProviderHealth

__all__ = ("MacroIndicator", "MacroObservation", "ProviderHealth")
