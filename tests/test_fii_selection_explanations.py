from core.fii_selection_explanations import build_selection_explanations


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
