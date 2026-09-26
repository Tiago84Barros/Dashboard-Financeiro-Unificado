"""Cenário inteiramente sintético para AppTest e inspeção visual local."""
import time
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
import streamlit as st

from core.macro_cenario import CenarioObservado
from tests.test_fii_portfolio_v4 import _candidate


def synthetic_rows(mode="partial"):
    kinds = (["tijolo", "papel", "fof", "hibrido"] * 5 if mode == "full" else
             ["tijolo"] * 4 + ["papel"] * 10 + ["fof"] * 2 + ["hibrido"])
    rows = [_candidate(i, kind) for i, kind in enumerate(kinds)]
    for row in rows:
        row.update(income_recurrence=.9, pvp=.9, data_readiness_status="ready",
                   vacancia_fisica=.04, property_count=12, region_count=3,
                   duration_anos=3.0, delinquency=.01, ltv=.35,
                   nav_discount=.1, holdings_overlap=.2, leverage=.1)
        if mode != "full" and row["tipo"] in {"papel", "hibrido"}:
            row["issuers"] = {"synthetic-shared": 1.0}
    return rows


def render_preview(mode="partial", history=True, llm=True):
    import views.fiis as view

    st.session_state["_app4_user"] = {
        "id": "11111111-1111-1111-1111-111111111111",
        "expires_at": time.time() + 600,
    }
    rows = synthetic_rows(mode)
    eligible = rows if mode != "empty" else []
    dates = pd.date_range("2022-01-31", periods=36, freq="ME")
    rng = np.random.default_rng(42)
    prices = pd.DataFrame(
        100 * np.cumprod(1 + rng.normal(.006, .02, (36, len(rows) + 2)), axis=0),
        index=dates, columns=[r["ticker"] for r in rows] + ["XFIX11", "BOVA11"])
    market = prices[["XFIX11", "BOVA11"]].pct_change().rename(
        columns={"XFIX11": "IFIX", "BOVA11": "Universo"})

    def force_partial(*args, **kwargs):
        kwargs["optimizer_kwargs"](rows)
        return {"items": [], "blockers": ["Metas estritas inviáveis (teste)"]}

    def answer(context, _history, question):
        st.session_state["synthetic_chat_context"] = context
        return "Resposta simulada, sem serviço externo: " + question

    with ExitStack() as stack:
        def mocked(target, **kwargs):
            return stack.enter_context(patch(target, **kwargs))

        mocked("core.chat_repository.load", return_value=[])
        mocked("core.chat_repository.save", return_value=None)
        mocked("core.chat_repository.clear", return_value=None)
        mocked("views.fiis._mr.load_fii_methodology_inputs", return_value=pd.DataFrame(rows))
        mocked("views.fiis._mr.load_fii_validation_status", return_value={})
        mocked("views.fiis._mr.load_precos_mensais", side_effect=lambda tickers:
               prices.reindex(columns=list(tickers)) if history else pd.DataFrame())
        mocked("views.fiis._mr.load_mercado_retorno_mensal",
               return_value=market if history else pd.DataFrame())
        mocked("views.fiis.apply_integrated_eligibility", return_value=(
            eligible, {"eligible_count": len(eligible), "universe_count": len(rows), "policy": {}}))
        mocked("views.fiis.score_fiis_by_type", side_effect=lambda rows, **kw: rows)
        mocked("views.fiis.get_macro_source", return_value=None)
        mocked("views.fiis.evaluate_publication_gate", return_value=SimpleNamespace(
            median_confidence=.9, can_publish_recommendation=False, reasons=["PIT pendente"]))
        mocked("core.fii_portfolio_model.load_active_fii_portfolio_model", return_value={})
        mocked("views.fiis._render_portfolio_version_history", return_value=None)
        mocked("views.fiis._cenario_macro_observado", return_value=CenarioObservado(
            ano=2025, selic=15., ipca=4., fonte="fixture sintética"))
        mocked("views.fiis.llm_disponivel", return_value=llm)
        mocked("views.fiis.provedores_disponiveis", return_value=["simulado"])
        mocked("views.fiis.chat_com_fiis", side_effect=answer)
        if mode == "partial":
            mocked("views.fiis.montar_carteira_com_concessao", side_effect=force_partial)
        st.markdown(view._CSS, unsafe_allow_html=True)
        st.title("Carteira-modelo · Seleção de FIIs")
        st.caption("VALIDAÇÃO LOCAL — fundos, preços e resposta de IA sintéticos.")
        view._tab_carteira(pd.DataFrame())


if __name__ == "__main__":
    st.set_page_config(layout="wide", page_title="FIIs · validação sintética")
    render_preview()
