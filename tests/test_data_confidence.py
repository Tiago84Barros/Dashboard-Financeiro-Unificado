import core.data_confidence as dc


CY = 2026  # ano de referência fixo nos testes


def test_annual_recency_factor():
    assert dc.annual_recency_factor(None, CY) == 0.0
    assert dc.annual_recency_factor(2026, CY) == 1.0
    assert dc.annual_recency_factor(2025, CY) == 1.0   # atraso 1 = fechamento normal
    assert dc.annual_recency_factor(2024, CY) == 0.6
    assert dc.annual_recency_factor(2023, CY) == 0.3
    assert dc.annual_recency_factor(2018, CY) == 0.1


def test_price_freshness_factor():
    assert dc.price_freshness_factor(None) == 0.0
    assert dc.price_freshness_factor(0) == 1.0
    assert dc.price_freshness_factor(3) == 1.0
    assert dc.price_freshness_factor(30) == 0.0
    assert dc.price_freshness_factor(100) == 0.0
    # decaimento monotônico entre 3 e 30 dias
    meio = dc.price_freshness_factor(16)
    assert 0.4 < meio < 0.6


def test_confidence_label_faixas():
    assert dc.confidence_label(90) == "Alta"
    assert dc.confidence_label(75) == "Alta"
    assert dc.confidence_label(74.9) == "Média"
    assert dc.confidence_label(55) == "Média"
    assert dc.confidence_label(54.9) == "Baixa"


def test_score_ticker_completo_alta():
    # ticker ideal: todas as métricas-chave, demonstração corrente, preço de hoje, sem flags
    r = dc.score_ticker(
        {"n_key_ttm": 9, "ymax": 2025, "dias_preco": 0, "n_flags": 0}, CY)
    assert r["score"] == 100.0
    assert r["label"] == "Alta"
    assert r["cobertura"] == 100.0 and r["frescor"] == 100.0 and r["integridade"] == 100.0


def test_score_ticker_caso_axia_sem_fundamentos():
    # caso AXIA5 pós-fix: DY ttm ok e preço fresco, mas SEM demonstração anual
    # (0 income_statements) → cobertura/frescor penalizados, não zerados.
    r = dc.score_ticker(
        {"n_key_ttm": 3, "ymax": None, "dias_preco": 1, "n_flags": 0}, CY)
    assert r["label"] in ("Média", "Baixa")
    assert r["frescor"] < 100.0          # sem fator anual, frescor não é cheio
    assert 0 < r["score"] < 75


def test_score_ticker_flags_derrubam_integridade():
    base = {"n_key_ttm": 9, "ymax": 2025, "dias_preco": 0, "n_flags": 0}
    limpo = dc.score_ticker(base, CY)
    com_flags = dc.score_ticker({**base, "n_flags": 3}, CY)
    assert com_flags["integridade"] == 0.0        # 3 * 0.34 > 1 → zera
    assert com_flags["score"] < limpo["score"]


def test_score_ticker_clamp_e_key_total():
    # n_key_ttm acima do total não estoura 100; key_total custom respeitado
    r = dc.score_ticker(
        {"n_key_ttm": 50, "ymax": 2026, "dias_preco": 0, "n_flags": 0}, CY, key_total=9)
    assert r["cobertura"] <= 100.0 and r["score"] <= 100.0


def test_summarize_confidence():
    scored = [
        {"ticker": "AAAA3", "score": 90.0, "label": "Alta"},
        {"ticker": "BBBB3", "score": 60.0, "label": "Média"},
        {"ticker": "CCCC3", "score": 40.0, "label": "Baixa"},
        {"ticker": "DDDD3", "score": 80.0, "label": "Alta"},
    ]
    s = dc.summarize_confidence(scored)
    assert s["n"] == 4 and s["alta"] == 2 and s["media_faixa"] == 1 and s["baixa"] == 1
    assert s["media"] == 67.5
    assert dc.summarize_confidence([]) == {
        "n": 0, "media": 0.0, "alta": 0, "media_faixa": 0, "baixa": 0}
