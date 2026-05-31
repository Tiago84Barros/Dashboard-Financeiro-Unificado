import pandas as pd

import views.analise_portfolio_b3 as apb3


def test_chat_context_includes_weighted_portfolio_metrics():
    model = {
        "name": "Carteira Teste",
        "ano_compra": 2026,
        "metrics_json": {"score_medio": 72.5, "alpha_selic_medio": 18.0},
        "params_json": {"segmentos_analisados": 12, "segmentos_aprovados": 5},
        "items": [
            {
                "ticker": "AAA3",
                "nome": "AAA",
                "weight": 0.6,
                "segmento": "Energia",
                "score": 80,
                "motivos_json": ["Lider no score"],
            },
            {
                "ticker": "BBB3",
                "nome": "BBB",
                "weight": 0.4,
                "segmento": "Bancos",
                "score": 65,
            },
        ],
    }
    fund = {
        "AAA3": {"DY": 0.10, "P/L": 10.0, "ROE": 0.20},
        "BBB3": {"DY": 0.05, "P/L": 20.0, "ROE": 0.10},
    }
    weights = apb3._weights_from_model(model)
    consol = apb3._consolidated_metrics(fund, weights)

    ctx = apb3._build_chat_context(
        model,
        state={},
        macro_hist={},
        fund=fund,
        consol=consol,
        weights=weights,
        cobertura_docs={"AAA3": 2, "BBB3": 0},
        rag_ctx="Documentos CVM/IPE indexados: 1/2 empresas, 2 trechos no total.",
    )

    assert "Carteira Teste" in ctx
    assert "Metricas salvas da Criacao de Portfolio" in ctx
    assert "Dividend Yield: ponderada=8.0%" in ctx
    assert "P/L: ponderada=14.00x" in ctx
    assert "DOCUMENTOS" in ctx
    assert "AAA3" in ctx and "BBB3" in ctx


def test_portfolio_fundamentals_uses_reconciled_multiples(monkeypatch):
    if hasattr(apb3._portfolio_fundamentals, "clear"):
        apb3._portfolio_fundamentals.clear()

    monkeypatch.setattr(
        apb3._db,
        "load_multiplos_todos",
        lambda: pd.DataFrame({"Ticker": ["AAA3"], "DY": [0.0], "P/L": [0.0]}),
    )

    def fake_reconciled(tickers, df_base=None, include_status=False):
        assert tickers == ("AAA3",)
        assert include_status is False
        return (
            pd.DataFrame({"Ticker": ["AAA3"], "DY": [0.075], "P/L": [12.5]}),
            pd.DataFrame(),
            {},
        )

    monkeypatch.setattr(apb3._recon, "batch_multiplos_reconciliados", fake_reconciled)
    monkeypatch.setattr(apb3._db, "load_demonstracoes_batch", lambda tickers: {})

    fund = apb3._portfolio_fundamentals(("AAA3",))

    assert fund["AAA3"]["DY"] == 0.075
    assert fund["AAA3"]["P/L"] == 12.5
