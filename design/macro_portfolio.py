"""Componente Streamlit para sensibilidade histórica macro de carteiras."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from sqlalchemy.exc import SQLAlchemyError


def render_historical_macro_path(
    *,
    asset_class: str,
    holdings: pd.DataFrame,
    symbol_column: str,
    sector_column: str,
    score_column: str,
    mode: str,
    key: str,
    start_year: int = 2010,
) -> None:
    """Exibe pesos contrafactuais; cálculo só ocorre após ação explícita."""
    with st.expander("🕰️ Sensibilidade dos pesos ao histórico macro", expanded=False):
        st.caption(
            "Reaplica a composição atual aos dados macro de cada fim de ano. "
            "É uma reconstrução ex post para testar sensibilidade — não é backtest "
            "dos constituintes, não elimina viés de sobrevivência e não prevê retorno."
        )
        if st.button("Calcular trajetória desde 2010", key=f"{key}_calculate"):
            from core.macro_data.database import get_local_macro_engine
            from core.macro_data.portfolio_context import historical_macro_weight_path

            engine = get_local_macro_engine()
            if engine is None:
                st.session_state.pop(f"{key}_result", None)
                st.error("PostgreSQL macro do Docker local não está configurado.")
            else:
                now = datetime.now(timezone.utc)
                cutoffs = [
                    datetime(year, 12, 31, 23, 59, tzinfo=timezone.utc)
                    for year in range(start_year, now.year)
                ] + [now]
                try:
                    st.session_state[f"{key}_result"] = historical_macro_weight_path(
                        engine,
                        asset_class=asset_class,
                        holdings=holdings,
                        symbol_column=symbol_column,
                        sector_column=sector_column,
                        score_column=score_column,
                        cutoffs=cutoffs,
                        mode=mode,
                    )
                except (SQLAlchemyError, ValueError) as exc:
                    st.session_state.pop(f"{key}_result", None)
                    st.error(f"Trajetória macro indisponível: {exc}")

        path = st.session_state.get(f"{key}_result")
        if not isinstance(path, pd.DataFrame) or path.empty:
            return
        view = path.copy()
        view["Ano"] = pd.to_datetime(view["as_of"], utc=True).dt.year
        view["Δ peso"] = view["weight_contextual"] - view["weight_fundamental"]
        years = sorted(view["Ano"].unique(), reverse=True)
        selected_year = st.selectbox(
            "Data de corte reconstruída", years, key=f"{key}_year"
        )
        selected = view[view["Ano"].eq(selected_year)].copy()
        selected = selected.rename(columns={
            "symbol": "Ativo",
            "weight_fundamental": "Peso fundamental",
            "weight_contextual": "Peso contextual",
            "macro_impact": "Impacto macro",
            "coverage": "Cobertura",
        })
        st.dataframe(
            selected[[
                "Ativo", "Peso fundamental", "Peso contextual", "Δ peso",
                "Impacto macro", "Cobertura",
            ]].sort_values("Peso contextual", ascending=False),
            hide_index=True,
            width="stretch",
            column_config={
                "Peso fundamental": st.column_config.NumberColumn(format="percent"),
                "Peso contextual": st.column_config.NumberColumn(format="percent"),
                "Δ peso": st.column_config.NumberColumn(format="%+.2%"),
                "Impacto macro": st.column_config.NumberColumn(format="%+.1f"),
                "Cobertura": st.column_config.NumberColumn(format="percent"),
            },
        )
