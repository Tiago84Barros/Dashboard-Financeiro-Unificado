"""Testes do score fundamentalista relativo por indústria."""
import pandas as pd
import pytest

import core.us_score as sc


def _frame():
    # 5 empresas, mesma indústria; A domina, E é a pior.
    base = {"sector": "Technology", "industry": "Software"}
    rows = [
        {"symbol": "A", **base, "gross_margin": 0.80, "operating_margin": 0.35,
         "net_margin": 0.28, "roe": 0.30, "roic": 0.25, "revenue_cagr_3y": 0.30,
         "net_debt_ebitda": 0.2, "pe": 12, "fcf_yield": 0.08, "shareholder_yield": 0.04},
        {"symbol": "B", **base, "gross_margin": 0.65, "operating_margin": 0.25,
         "net_margin": 0.18, "roe": 0.22, "roic": 0.18, "revenue_cagr_3y": 0.20,
         "net_debt_ebitda": 1.0, "pe": 18, "fcf_yield": 0.05, "shareholder_yield": 0.02},
        {"symbol": "C", **base, "gross_margin": 0.55, "operating_margin": 0.18,
         "net_margin": 0.12, "roe": 0.15, "roic": 0.12, "revenue_cagr_3y": 0.10,
         "net_debt_ebitda": 2.0, "pe": 25, "fcf_yield": 0.03, "shareholder_yield": 0.01},
        {"symbol": "D", **base, "gross_margin": 0.45, "operating_margin": 0.10,
         "net_margin": 0.06, "roe": 0.09, "roic": 0.07, "revenue_cagr_3y": 0.04,
         "net_debt_ebitda": 3.5, "pe": 35, "fcf_yield": 0.01, "shareholder_yield": 0.0},
        {"symbol": "E", **base, "gross_margin": 0.30, "operating_margin": 0.02,
         "net_margin": -0.02, "roe": 0.01, "roic": 0.01, "revenue_cagr_3y": -0.05,
         "net_debt_ebitda": 5.0, "pe": 60, "fcf_yield": -0.02, "shareholder_yield": 0.0},
    ]
    return pd.DataFrame(rows)


def test_score_ordena_e_faixa_0_100():
    scored = sc.score_cross_section(_frame(), min_group=3)
    assert list(scored["symbol"])[0] == "A"        # melhor no topo
    assert list(scored["symbol"])[-1] == "E"        # pior no fim
    assert scored["score"].between(0, 100).all()
    assert scored.iloc[0]["score"] > scored.iloc[-1]["score"]


def test_lower_is_better_invertido():
    # menor P/L e menor alavancagem devem ajudar valuation/solidez de A
    scored = sc.score_cross_section(_frame(), min_group=3)
    a = scored[scored["symbol"] == "A"].iloc[0]
    e = scored[scored["symbol"] == "E"].iloc[0]
    assert a["score_valuation"] > e["score_valuation"]
    assert a["score_solidity"] > e["score_solidity"]


def test_missing_neutro_nao_quebra():
    df = _frame()
    df.loc[df["symbol"] == "C", "roic"] = None       # ausência
    scored = sc.score_cross_section(df, min_group=3)
    assert not scored.empty and scored["score"].notna().all()
    c = scored[scored["symbol"] == "C"].iloc[0]
    assert c["score_confidence"] < 100
    assert c["score_status"] in {"screen_grade", "research_grade", "decision_grade"}
    assert isinstance(c["critical_missing"], list)


def test_trilha_esparsa_encolhe_para_neutro_e_reduz_confianca():
    df = _frame()
    sparse = df["symbol"] == "A"
    for metric in sc.FACTOR_TRACKS["valuation"]:
        if metric in df.columns:
            df.loc[sparse, metric] = None
    scored = sc.score_cross_section(df, min_group=3)
    a = scored[scored["symbol"] == "A"].iloc[0]
    assert a["score_valuation"] == pytest.approx(50.0)
    assert "valuation" in a["critical_missing"]
    assert a["score_status"] != "decision_grade"


def test_empty_frame():
    out = sc.score_cross_section(pd.DataFrame())
    assert out.empty


def test_industry_comparison():
    scored = sc.score_cross_section(_frame(), min_group=3)
    peers = sc.industry_comparison(scored, "Software")
    assert len(peers) == 5 and list(peers["symbol"])[0] == "A"
    assert sc.industry_comparison(scored, "Inexistente").empty


def test_weights_renormalizam_e_override_setor():
    # A-139/A-140: o override vivo e o do banco, chaveado pela descricao SIC.
    # O de REIT foi removido -- REIT nao entra no universo de acoes.
    w = sc._weights_for("State Commercial Banks")
    assert sum(w.values()) == pytest.approx(1.0)
    assert w["solidity"] < sc.DEFAULT_TRACK_WEIGHTS["solidity"]
    w2 = sc._weights_for(None)
    assert sum(w2.values()) == pytest.approx(1.0)


# ── SBC e diluição no score (v0.5.0, auditoria 2026-07) ──────────────────────

def _par_sbc(sbc_a: float, sbc_b: float) -> pd.DataFrame:
    """Duas empresas idênticas, exceto no peso da remuneração em ações."""
    base = {"sector": "Technology", "industry": "Software",
            "gross_margin": 0.70, "operating_margin": 0.25, "net_margin": 0.18,
            "fcf_margin": 0.20, "cash_conversion": 1.1, "roe": 0.25, "roa": 0.12}
    return pd.DataFrame([
        {"symbol": "LEVE", **base, "sbc_to_revenue": sbc_a,
         "fcf_ex_sbc_margin": 0.20 - sbc_a},
        {"symbol": "PESADA", **base, "sbc_to_revenue": sbc_b,
         "fcf_ex_sbc_margin": 0.20 - sbc_b},
    ])


def test_sbc_pesada_reduz_a_trilha_de_qualidade():
    scored = sc.score_cross_section(_par_sbc(0.01, 0.15), min_group=2)
    leve = scored[scored["symbol"] == "LEVE"].iloc[0]
    pesada = scored[scored["symbol"] == "PESADA"].iloc[0]
    # Antes do v0.5.0 as duas empatavam: o FCF GAAP soma a SBC de volta.
    assert leve["score_quality"] > pesada["score_quality"]
    assert leve["score"] > pesada["score"]


def test_diluicao_anula_vantagem_do_shareholder_yield():
    base = {"sector": "Technology", "industry": "Software"}
    df = pd.DataFrame([
        # mesmo yield de recompra, mas uma emite ações e a outra retira
        {"symbol": "RECOMPRA", **base, "shareholder_yield": 0.05,
         "share_count_cagr_3y": -0.03},
        {"symbol": "DILUI", **base, "shareholder_yield": 0.05,
         "share_count_cagr_3y": 0.06},
    ])
    scored = sc.score_cross_section(df, min_group=2)
    recompra = scored[scored["symbol"] == "RECOMPRA"].iloc[0]
    dilui = scored[scored["symbol"] == "DILUI"].iloc[0]
    assert recompra["score_shareholder"] > dilui["score_shareholder"]


def test_ausencia_de_sbc_nao_penaliza_nem_premia():
    """Sem o dado, a empresa não pode ser punida — só perde cobertura."""
    df = _par_sbc(0.01, 0.15)
    df.loc[df["symbol"] == "PESADA", ["sbc_to_revenue", "fcf_ex_sbc_margin"]] = None
    scored = sc.score_cross_section(df, min_group=2)
    pesada = scored[scored["symbol"] == "PESADA"].iloc[0]
    assert pesada["coverage_quality"] < 100


def test_cross_section_volta_ordenado_por_nota() -> None:
    """Contrato explícito: quem consome NÃO pode colar coluna por posição.

    A função termina em `sort_values("score").reset_index(drop=True)`, então o
    quadro devolvido não está na ordem de entrada. Colar um vetor externo por
    posição não levanta erro, não deixa NaN e não muda o tamanho -- só troca os
    valores entre empresas. Foi exatamente assim que um experimento desta base
    concluiu que empresas quebradas pontuavam mais que as sobreviventes.
    """
    import pandas as pd

    entrada = pd.DataFrame([
        {"symbol": "RUIM", "industry": "35", "sector": "x", "net_margin": -0.4,
         "roe": -0.5, "roa": -0.3},
        {"symbol": "BOA", "industry": "35", "sector": "x", "net_margin": 0.25,
         "roe": 0.30, "roa": 0.12},
    ])
    saida = sc.score_cross_section(entrada, min_group=2)
    assert list(saida["symbol"]) == ["BOA", "RUIM"]
    assert list(saida["score"]) == sorted(saida["score"], reverse=True)
