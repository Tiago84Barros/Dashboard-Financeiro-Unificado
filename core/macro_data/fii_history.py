"""Reconstrução macro FII restrita aos ativos da composição exibida."""
import pandas as pd

from core.fii_portfolio_v4 import optimize_diligence_portfolio


def rebuild_fii_macro_history(base, impacts, mode, *, scenario, policy,
                              correlation_matrix=None, correlation_penalty=0.0,
                              previous_weights=None):
    """Reotimiza apenas os ativos consultados, sob a política e cenário atuais.

    Não seleciona candidatos externos. Inviabilidade ou mudança da composição
    resulta em erro explícito, nunca em uma trajetória de outra carteira.
    """
    rows = base.rename(columns={"symbol": "ticker"}).to_dict("records")
    rebuilt = optimize_diligence_portfolio(
        rows, scenario, policy=policy, correlation_matrix=correlation_matrix,
        correlation_penalty=correlation_penalty, previous_weights=previous_weights,
        macro_impacts=impacts, macro_mode=mode,
    )
    if not rebuilt.get("items"):
        raise ValueError("Reconstrução FII inviável sob as restrições atuais.")
    return pd.DataFrame(rebuilt["items"]).rename(columns={"ticker": "symbol"})
