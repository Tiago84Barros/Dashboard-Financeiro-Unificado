import pandas as pd

from core.fii_methodology import MacroScenario
from core.fii_selection_explanations import (
    build_selection_explanations,
    build_selection_reports,
)


def _row(ticker, score, confidence=.8, coverage=.8, dy=.1):
    return {
        "ticker": ticker, "tipo": "tijolo", "type_score": score,
        "confidence": confidence, "coverage": coverage, "dy_12m": dy,
        "components": {"quality": score, "risk": score - 5},
        "missing_critical": (), "data_readiness_status": "ready",
        "publication_status": "diligence_only", "weight": .1,
    }


def test_explanation_compares_only_same_type_peers():
    selected = _row("TOP11", 90, confidence=.9, coverage=.9, dy=.12)
    peers = [selected, _row("MID11", 60), _row("LOW11", 30),
             {**_row("PAP11", 99), "tipo": "papel"}]
    result = build_selection_explanations([selected], peers, regime="juros_reais_altos")[0]
    assert result["rank"] == 1
    assert result["peer_count"] == 3
    assert result["top_percent"] == 34
    assert any("mediana do tipo" in reason for reason in result["strengths"])
    assert "juros_reais_altos" in result["role"]


def test_explanation_surfaces_missing_critical_data_and_diligence_status():
    selected = _row("MISS11", 70, confidence=.6, coverage=.5)
    selected.update({
        "missing_critical": ("wault_anos", "vacancia_financeira"),
        "data_readiness_status": "insufficient",
        "data_readiness_reasons": ("confiança 60% abaixo do mínimo 75%",),
    })
    result = build_selection_explanations([selected], [selected])[0]
    caveats = " ".join(result["caveats"])
    assert "WAULT" in caveats
    assert "vacância financeira" in caveats
    assert "dados ainda insuficientes" in caveats
    assert "candidato de diligência" in caveats


def test_detailed_report_uses_peer_category_structure_and_market_history():
    selected = _row("LOGX11", 90, confidence=.82, coverage=.76, dy=.13)
    selected.update({
        "pvp": .84, "liquidez_diaria": 2_500_000,
        "patrimonio_liquido": 1_200_000_000, "num_cotistas": 85_000,
        "property_count": 12, "region_count": 3, "vacancia_fisica": .035,
        "tenant_concentration": .18, "income_recurrence": .91,
        "income_growth_per_share_3y": .04,
        "regions": {"Sudeste": .55, "Sul": .30, "Nordeste": .15},
        "metric_metadata": {"vacancia_fisica": {"reference_date": "2026-06-30"}},
    })
    peer = _row("PEER11", 55, dy=.09)
    dates = pd.date_range("2024-01-31", periods=24, freq="ME")
    prices = pd.DataFrame({
        "LOGX11": [100 + i * 1.1 for i in range(24)],
        "XFIX11": [100 + i * .8 for i in range(24)],
        "BOVA11": [100 + i * .3 + (i % 3) for i in range(24)],
    }, index=dates)
    report = build_selection_reports(
        [selected], [selected, peer],
        scenario=MacroScenario(selic=14.0, ipca=4.5), prices=prices,
    )[0]
    combined = " ".join(report["facts"] + report["structure"] + report["market"])
    assert "desconto patrimonial de 16.0%" in combined
    assert "mediana do tipo" in combined
    assert "12 imóveis" in combined
    assert "vacância física: 3.5%" in combined
    assert "correlação com IFIX" in combined
    assert report["relationship_months"] >= 12
    assert report["data_reference"] == "2026-06-30"


def test_detailed_report_does_not_invent_missing_category_metrics():
    selected = {**_row("PAPR11", 80), "tipo": "papel", "pvp": .95}
    report = build_selection_reports([selected], [selected])[0]
    assert report["structure"] == []
    assert all("LTV" not in text for text in report["facts"])


def test_readmitido_fora_do_ranking_nao_recebe_ultima_posicao():
    """Ausência de medição não pode ser publicada como a pior medição."""
    readmitido = _row("REND11", 55)
    pares = [_row("TOP11", 90), _row("MID11", 60)]

    resultado = build_selection_explanations([readmitido], pares)[0]

    assert resultado["rank"] is None
    assert resultado["top_percent"] is None
    assert resultado["peer_count"] == 2


def _com_renda(ticker, dy, recorrencia, score=70):
    """Par de insumos da renda recorrente sobre o mesmo esqueleto dos demais."""
    return {**_row(ticker, score, dy=dy), "income_recurrence": recorrencia}


def test_yield_inflado_nao_entra_como_forca():
    """O caso KORE11: DY alto sustentado por receita não recorrente.

    O fundo reprova no piso de renda recorrente e era readmitido por cessão de
    proteção; o relatório então o elogiava pelo yield bruto, contradizendo a
    regra que o reprovou.
    """
    kore = _com_renda("KORE11", .182, .30)
    pares = [kore, _com_renda("AAAA11", .09, .95), _com_renda("BBBB11", .095, .92)]

    resultado = build_selection_explanations([kore], pares)[0]
    forcas = " ".join(resultado["strengths"])
    ressalvas = " ".join(resultado["caveats"])

    assert "DY de 12 meses" not in forcas
    assert "renda recorrente" in ressalvas
    # O DY divulgado continua citado — ao lado, como contexto, não como mérito.
    assert "18.2%" in ressalvas


def test_renda_recorrente_acima_da_mediana_e_forca_com_o_dy_ao_lado():
    forte = _com_renda("FORT11", .11, .98)
    pares = [forte, _com_renda("AAAA11", .09, .60), _com_renda("BBBB11", .085, .55)]

    forcas = " ".join(build_selection_explanations([forte], pares)[0]["strengths"])

    assert "renda recorrente" in forcas
    assert "10.8%" in forcas
    assert "DY divulgado 11.0%" in forcas


def test_relatorio_detalhado_mede_a_renda_pela_parcela_recorrente():
    kore = {**_com_renda("KORE11", .182, .30), "pvp": .95}
    pares = [kore, _com_renda("AAAA11", .09, .95)]

    relatorio = build_selection_reports([kore], pares)[0]
    fatos = " ".join(relatorio["facts"])

    assert "renda recorrente 5.5%" in fatos
    assert "DY 12m divulgado 18.2%" in fatos
    # A comparação com o tipo é da renda recorrente, não do yield bruto.
    assert "-1.5% vs. mediana do tipo" in fatos
