"""Integração sob demanda da sustentabilidade de renda no universo EUA."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import core.us_data as us_data
import core.us_read as us_read
import core.us_score as us_score
from core.us_portfolio_creation import (
    USPortfolioCreationParams,
    prepare_eligible_universe,
)


def _historico(payouts: list[float]) -> list[dict]:
    return [
        {
            "fiscal_year": 2020 + posicao,
            "net_income": 100.0,
            "dividends_paid": -100.0 * payout,
            "free_cash_flow": 120.0,
        }
        for posicao, payout in enumerate(payouts)
    ]


def test_carregador_da_vitrine_limita_historico_aos_simbolos_pedidos(monkeypatch):
    """A criação de carteira não pode transformar o JSON pesado em leitura global."""
    capturado = {}

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    class _Engine:
        def connect(self):
            return _Conn()

    def _read_sql(query, conn, params):
        capturado["sql"] = str(query)
        capturado["params"] = params
        return pd.DataFrame({"symbol": ["AAPL"], "financials": [_historico([0.5] * 3)]})

    monkeypatch.setattr(us_read, "_engine", lambda: _Engine())
    monkeypatch.setattr(us_read.pd, "read_sql", _read_sql)

    out = us_read.load_snapshot_financials_for_symbols(["aapl", "MSFT", "aapl", " "])

    assert list(out) == ["AAPL"]
    assert "WHERE symbol IN" in capturado["sql"]
    assert "AAPL" in capturado["params"]["symbols"]
    assert "MSFT" in capturado["params"]["symbols"]
    assert len(capturado["params"]["symbols"]) == 2


def test_enriquecimento_calcula_por_simbolo_sem_alterar_o_frame_original(monkeypatch):
    original = pd.DataFrame({
        "symbol": ["AAPL", "MSFT"],
        "is_reit": [False, False],
        "shareholder_yield": [0.04, 0.04],
        "share_count_cagr_3y": [-0.01, -0.01],
    })
    monkeypatch.setattr(
        us_read,
        "load_snapshot_financials_for_symbols",
        lambda symbols: {"AAPL": _historico([0.5] * 3)},
    )

    enriched = us_read.enrich_with_renda_sustentavel(original)

    assert "payout_sustentabilidade" not in original
    assert enriched.loc[0, "payout_sustentabilidade"] == pytest.approx(1.0)
    assert enriched.loc[0, "payout_mediano_hist"] == pytest.approx(0.5)
    assert pd.isna(enriched.loc[1, "payout_sustentabilidade"])
    assert enriched.loc[1, "n_anos_payout"] == 0


def test_score_shareholder_incorpora_sustentabilidade_sem_mudar_peso_da_trilha():
    base = {
        "sector": "Technology", "industry": "Software",
        "shareholder_yield": 0.04, "share_count_cagr_3y": -0.01,
    }
    quadro = pd.DataFrame([
        {"symbol": "SUSTENTAVEL", **base, "payout_sustentabilidade": 1.0},
        {"symbol": "MEDIANA", **base, "payout_sustentabilidade": 0.5},
        {"symbol": "FRAGIL", **base, "payout_sustentabilidade": 0.0},
    ])

    scored = us_score.score_cross_section(quadro, min_group=2).set_index("symbol")

    assert us_score.DEFAULT_TRACK_WEIGHTS["shareholder"] == pytest.approx(0.12)
    assert us_score.FACTOR_TRACKS["shareholder"] == [
        "shareholder_yield", "share_count_cagr_3y", "payout_sustentabilidade",
    ]
    assert scored.loc["SUSTENTAVEL", "score_shareholder"] > scored.loc["FRAGIL", "score_shareholder"]


def test_ausencia_da_nota_historica_nao_pune_nem_premia_shareholder():
    base = pd.DataFrame([
        {"symbol": "A", "sector": "Technology", "industry": "Software",
         "shareholder_yield": 0.05, "share_count_cagr_3y": -0.02},
        {"symbol": "B", "sector": "Technology", "industry": "Software",
         "shareholder_yield": 0.02, "share_count_cagr_3y": 0.01},
    ])
    com_lacuna = base.assign(payout_sustentabilidade=float("nan"))

    sem = us_score.score_cross_section(base, min_group=2).set_index("symbol")
    com = us_score.score_cross_section(com_lacuna, min_group=2).set_index("symbol")

    assert com["score_shareholder"].equals(sem["score_shareholder"])
    assert com["coverage_shareholder"].equals(sem["coverage_shareholder"])


def _universo_para_criacao() -> pd.DataFrame:
    """Frame sintético já pontuado, como o entregue pela vitrine à criação."""
    rows = []
    for industry in ("Software", "Hardware"):
        for posicao in range(4):
            row = {
                "symbol": f"{industry[:1]}{posicao}",
                "sector": "Technology",
                "industry": industry,
                "exchange": "NASDAQ",
                "is_active": True,
                "is_reit": False,
                "score": 70.0,
                "coverage": 90.0,
                "_years": 8,
                "_market_cap": 2_000_000_000.0,
                "giro_diario_usd": 5_000_000.0,
                "giro_diario_usd_at": pd.Timestamp.now(tz="UTC"),
            }
            for metricas in us_score.FACTOR_TRACKS.values():
                for metric in metricas:
                    if metric != "payout_sustentabilidade":
                        row[metric] = 0.10 + posicao / 100
            rows.append(row)
    return pd.DataFrame(rows)


def _params_criacao() -> USPortfolioCreationParams:
    return USPortfolioCreationParams(
        min_companies_per_industry=4,
        min_market_cap=1_000_000_000.0,
        min_coverage=50.0,
        min_years=5,
        min_fundamental_score=45.0,
        min_daily_turnover_usd=1_000_000.0,
    )


def test_fluxo_real_da_criacao_enriquece_so_candidatos_e_recalcula_score(monkeypatch):
    """A criação chama a leitura pesada só após a elegibilidade, nunca na vitrine."""
    scored = _universo_para_criacao()
    params = _params_criacao()
    elegiveis, _ = prepare_eligible_universe(scored, params)
    chamados = []

    def _enrich(frame):
        chamados.append(frame["symbol"].tolist())
        out = frame.copy()
        out["payout_sustentabilidade"] = out["symbol"].str[-1].astype(int).map(
            lambda posicao: 1.0 if posicao % 2 == 0 else 0.0
        )
        return out

    monkeypatch.setattr(us_data._read, "enrich_with_renda_sustentavel", _enrich)

    result = us_data.portfolio_candidates_with_renda_sustentavel(scored, params)

    assert chamados == [elegiveis["symbol"].tolist()]
    assert len(chamados[0]) < len(scored) + 1
    assert result.loc[result["symbol"] == "S0", "score_shareholder"].item() > result.loc[
        result["symbol"] == "S1", "score_shareholder"
    ].item()
    assert result["coverage"].equals(us_score.score_cross_section(elegiveis)["coverage"])


def test_fluxo_da_criacao_mantem_neutra_a_ausencia_de_historico(monkeypatch):
    scored = _universo_para_criacao()
    params = _params_criacao()
    elegiveis, _ = prepare_eligible_universe(scored, params)
    monkeypatch.setattr(us_data._read, "enrich_with_renda_sustentavel", lambda frame: frame.copy())

    result = us_data.portfolio_candidates_with_renda_sustentavel(scored, params)
    esperado = us_score.score_cross_section(
        elegiveis, min_group=params.min_companies_per_industry,
    )

    assert result["score"].equals(esperado["score"])
    assert result["coverage"].equals(esperado["coverage"])


def test_tela_de_criacao_usa_o_frame_enriquecido_nas_duas_execucoes():
    """Baseline macro e resultado final devem partir da mesma evidência anual."""
    source = (Path(__file__).parents[1] / "views" / "empresas_americanas.py").read_text(
        encoding="utf-8"
    )

    assert "portfolio_scored = us.portfolio_candidates_with_renda_sustentavel(" in source
    assert source.count("portfolio_scored, params, score_panel") == 2
