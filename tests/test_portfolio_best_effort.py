from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from core.fii_methodology import MacroScenario
from core.fii_portfolio_v4 import PortfolioPolicy
from core.portfolio_best_effort import allocate_partial
from core.portfolio_review_routes import b3_review, fii_review, us_review
from core.us_portfolio_creation import USPortfolioCreationParams


def test_partial_does_not_renormalize_above_caps():
    result = allocate_partial([{"ticker": "A", "quality": 90}, {"ticker": "B", "quality": 60}],
                              [.10, .10])
    assert result["allocated_weight"] == pytest.approx(.20)
    assert result["unallocated_weight"] == pytest.approx(.80)
    assert max(row["weight"] for row in result["items"]) <= .10


def test_joint_constraints_and_cardinality_preserve_capital():
    result = allocate_partial([{"ticker": str(i), "quality": i} for i in range(20)],
                              [.10]*20, upper_constraints=[(np.ones(20), .35)],
                              max_assets=3, min_weight=.02)
    assert result["allocated_weight"] == pytest.approx(.30)
    assert len(result["items"]) == 3
    assert {row["ticker"] for row in result["items"]} == {"17", "18", "19"}


def test_empty_and_invalid_inputs():
    assert allocate_partial([], [])["unallocated_weight"] == 1
    with pytest.raises(ValueError):
        allocate_partial([{"ticker": "A", "quality": float("nan")}], [.10])


def test_time_limit_keeps_verified_incumbent_without_claiming_optimality(monkeypatch):
    import core.portfolio_best_effort as allocator

    monkeypatch.setattr(allocator, "milp", lambda *_args, **_kwargs:
                        SimpleNamespace(success=False, x=np.array([.1, 1.])))
    result = allocate_partial([{"ticker": "A", "quality": 80}], [.1])
    assert result["items"]
    assert result["solver_status"] == "feasible"
    assert result["unallocated_weight"] == pytest.approx(.9)


def test_fii_17_candidates_have_partial_portfolio_with_shared_credit_exposure():
    from tests.test_fii_portfolio_v4 import _candidate

    kinds = ["tijolo"]*4 + ["papel"]*10 + ["fof"]*2 + ["hibrido"]
    rows = [_candidate(i, kind) for i, kind in enumerate(kinds)]
    for row in rows:
        row["income_recurrence"] = .9
        if row["tipo"] in {"papel", "hibrido"}:
            row["issuers"] = {"shared": 1.0}
    result = fii_review(rows, PortfolioPolicy(max_assets=14, max_asset=.1),
                        MacroScenario(selic=15, ipca=4))
    assert result["items"]
    assert 0 < result["allocated_weight"] < 1
    assert sum(row["weight"] for row in result["items"]
               if row["tipo"] in {"papel", "hibrido"}) <= .1+1e-6
    assert all(row["weight"] <= .1 for row in result["items"])
    assert result["allocated_weight"] + result["unallocated_weight"] == pytest.approx(1)
    assert not result["can_publish"]


def test_fii_unknown_critical_exposure_and_low_confidence_do_not_receive_weight():
    rows = [{"ticker": "UNKNOWN", "tipo": "papel", "type_score": 90, "confidence": .9},
            {"ticker": "LOW", "tipo": "fof", "type_score": 90, "confidence": .1}]
    result = fii_review(rows, PortfolioPolicy(), MacroScenario(selic=15, ipca=4))
    assert not result["items"]
    assert result["unallocated_weight"] == 1


def test_us_industry_not_approved_does_not_hide_safe_individual_candidate():
    frame = pd.DataFrame([
        {"symbol": "SAFE", "entry_score": 45, "entry_status": "Observação",
         "sector_group": "Saúde", "industry_group": "Hospitais"},
        {"symbol": "RISK", "entry_score": 90, "entry_status": "Excluída",
         "sector_group": "Saúde", "industry_group": "Hospitais"}])
    result = us_review(frame, USPortfolioCreationParams(max_weight=.08))
    assert [row["ticker"] for row in result["items"]] == ["SAFE"]
    assert result["unallocated_weight"] == pytest.approx(.92)


def test_b3_historical_segment_gate_is_soft_but_critical_health_is_hard(monkeypatch):
    import core.b3_holdings_health as health

    monkeypatch.setattr(health, "check_holdings", lambda *_args, **_kwargs: [
        SimpleNamespace(ticker="SAFE3", nivel=health.OK),
        SimpleNamespace(ticker="RISK3", nivel=health.CRITICO)])
    results = [{"score_proximo": {"SAFE3": 60, "RISK3": 90}, "setor": "Saúde"}]
    guard = {ticker: {"score_entrada": 50, "status_entrada": "Observação"}
             for ticker in ("SAFE3", "RISK3")}
    result = b3_review(results, pd.DataFrame(), guard, cap=.1, sector_cap=.25,
                       cycle_cap=.4, selic=15)
    assert [row["ticker"] for row in result["items"]] == ["SAFE3"]
    assert result["unallocated_weight"] == pytest.approx(.9)
    assert not b3_review(results, pd.DataFrame(), guard, cap=.1, sector_cap=.25,
                         cycle_cap=.4, selic=15, vetoed={"SAFE3"})["items"]


def test_us_real_creation_route_returns_review_without_changing_original_approval(monkeypatch):
    import core.us_portfolio_creation as creation

    eligible = pd.DataFrame([{"symbol": "SAFE", "entry_score": 45, "entry_status": "Observação",
                              "sector_group": "Saúde", "industry_group": "Hospitais"}])
    audit = pd.DataFrame([{"industry_group": "Hospitais", "status": "Observação",
                           "avg_entry_score": 45}])
    monkeypatch.setattr(creation, "prepare_eligible_universe", lambda *_: (eligible, pd.DataFrame()))
    monkeypatch.setattr(creation, "build_industry_audit", lambda *_: audit)
    result = creation.build_portfolio_creation(eligible)
    assert result["review_portfolio"]["items"][0]["ticker"] == "SAFE"
    assert result["industry_audit"].iloc[0]["status"] == "Observação"
    assert not result["can_publish"]


def test_review_component_renders_partial_weights_and_empty_capital():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string('''
from core.portfolio_best_effort import allocate_partial
from design.portfolio_review import render_portfolio_review
result = allocate_partial([{"ticker": "SYNTH", "quality": 90}], [.1])
render_portfolio_review(result, key="synthetic")
render_portfolio_review(allocate_partial([], []), key="empty")
''').run(timeout=20)
    assert not app.exception
    # Os KPIs saem como cards HTML (um st.markdown por composição), não st.metric.
    cards = [m.value for m in app.markdown if "pr-resumo" in m.value]
    assert len(cards) == 2
    assert "10.0%" in cards[0] and "90.0%" in cards[0] and "SYNTH" in cards[0]
    assert "100.0%" in cards[1] and 'class="pr-card"' not in cards[1]
    assert app.dataframe[0].value.iloc[0]["Ativo"] == "SYNTH"


# ── Rota alternativa da B3: líderes por segmento, score do segmento ─────────
# Até 14/11/2026 `b3_review` juntava TODA empresa de todo segmento e ordenava
# pelo Score de Entrada -- percentil intra-segmento, que não compara setores --,
# lendo o `score_proximo` só para descartar quem não o tinha.

def _saude_ok(monkeypatch):
    import core.b3_holdings_health as health

    monkeypatch.setattr(health, "check_holdings", lambda _df, tickers, **_k: [
        SimpleNamespace(ticker=t, nivel=health.OK) for t in tickers])


def _seg(nome, setor, scores, *, est=1.2, selic=1.0, ew=1.0):
    return {"setor": setor, "subsetor": nome, "segmento": nome,
            "score_proximo": scores, "val_est": est, "val_selic": selic, "val_ew": ew}


def _tickers(result):
    return sorted(row["ticker"] for row in result["items"])


def test_b3_review_pega_so_o_lider_de_cada_segmento_pelo_score_do_segmento(monkeypatch):
    _saude_ok(monkeypatch)
    papel = _seg("Papel", "Materiais", {"LIDR3": 90, "SEGU3": 80})
    hosp = _seg("Hospitais", "Saúde", {"HOSP3": 70})
    # O Score de Entrada do segundo colocado é maior: antes, ele entrava.
    guard = {"LIDR3": {"score_entrada": 50, "status_entrada": "Observação"},
             "SEGU3": {"score_entrada": 99, "status_entrada": "Aprovada"},
             "HOSP3": {"score_entrada": 40, "status_entrada": "Observação"}}
    result = b3_review([papel, hosp], pd.DataFrame(), guard, cap=.5, sector_cap=1,
                       cycle_cap=1, selic=.15)
    assert _tickers(result) == ["HOSP3", "LIDR3"]
    lider = next(r for r in result["items"] if r["ticker"] == "LIDR3")
    assert lider["score_segmento"] == 90 and lider["segmento"] == "Papel"


def test_b3_review_prefere_segmentos_aprovados_ao_score_mais_alto(monkeypatch):
    _saude_ok(monkeypatch)
    reprovado = _seg("A", "S1", {"AAAA3": 99}, est=3.0)   # maior score e margem
    aprov_b = _seg("B", "S2", {"BBBB3": 55})
    aprov_c = _seg("C", "S3", {"CCCC3": 60})
    result = b3_review([reprovado, aprov_b, aprov_c], pd.DataFrame(), {}, cap=.5,
                       sector_cap=1, cycle_cap=1, selic=.15, approved=[aprov_b, aprov_c])
    assert _tickers(result) == ["BBBB3", "CCCC3"]
    assert all(row["aprovado"] for row in result["items"])
    assert any("2 de 2 posições" in r for r in result["reasons"])


def test_b3_review_ordena_nao_aprovados_pela_margem_do_backtest(monkeypatch):
    _saude_ok(monkeypatch)
    fraco = _seg("Fraco", "S1", {"FRAC3": 99}, est=0.9)    # perde da Selic
    forte = _seg("Forte", "S2", {"FORT3": 40}, est=1.1)    # bate os dois por 10%
    result = b3_review([fraco, forte], pd.DataFrame(), {}, cap=1, sector_cap=1,
                       cycle_cap=1, selic=.15)
    assert _tickers(result) == ["FORT3"]
    assert result["items"][0]["evidencia_segmento"] == pytest.approx(10.0)


def test_b3_review_veto_cede_a_vaga_ao_proximo_do_mesmo_segmento(monkeypatch):
    _saude_ok(monkeypatch)
    seg = _seg("Papel", "Materiais", {"LIDR3": 90, "SEGU3": 80, "TERC3": 70})
    outro = _seg("Hosp", "Saúde", {"HOSP3": 50})
    guard = {"SEGU3": {"score_entrada": 20, "status_entrada": "Excluída"}}
    result = b3_review([seg, outro], pd.DataFrame(), guard, cap=.5, sector_cap=1,
                       cycle_cap=1, selic=.15,
                       barrado=lambda tk, _setor: tk == "LIDR3")
    # LIDR3 barrado pela Inteligência, SEGU3 excluído pelo Score de Entrada:
    # a vaga do segmento vai para TERC3, e o segmento não ganha segunda vaga.
    assert _tickers(result) == ["HOSP3", "TERC3"]
    terc = next(r for r in result["items"] if r["ticker"] == "TERC3")
    assert terc["lider_segmento"] is False


def test_b3_review_inteligencia_indisponivel_nao_veta_e_e_nomeada(monkeypatch):
    _saude_ok(monkeypatch)

    def _falha(_tk, _setor):
        raise RuntimeError("fora do ar")

    result = b3_review([_seg("A", "S1", {"AAAA3": 80})], pd.DataFrame(), {}, cap=1,
                       sector_cap=1, cycle_cap=1, selic=.15, barrado=_falha)
    assert _tickers(result) == ["AAAA3"]
    assert any("AAAA3 (RuntimeError)" in r for r in result["reasons"])


def test_b3_review_ticker_em_dois_segmentos_ocupa_uma_vaga(monkeypatch):
    _saude_ok(monkeypatch)
    a = _seg("A", "S1", {"DUPL3": 90, "OUTR3": 50})
    b = _seg("B", "S2", {"DUPL3": 95, "BBBB3": 60})
    result = b3_review([a, b], pd.DataFrame(), {}, cap=.5, sector_cap=1, cycle_cap=1,
                       selic=.15)
    assert _tickers(result) == ["BBBB3", "DUPL3"]


def test_card_da_revisao_b3_mostra_o_score_do_segmento_e_nao_a_prioridade():
    from design.portfolio_review import _backtest, _html_card, _score

    row = {"ticker": "LIDR3", "weight": .25, "quality": 7.0, "score_segmento": 88.4,
           "setor": "Materiais", "segmento": "Papel", "aprovado": False,
           "evidencia_segmento": -3.25}
    assert _score(row) == 88.4
    assert _backtest(row) == "Segmento não aprovado · margem -3.2%"
    html = _html_card(1, row)
    assert "88.4" in html and "Segmento não aprovado" in html
    # Rotas de FII e EUA não trazem a situação do backtest: nada muda nelas.
    assert _backtest({"ticker": "X", "weight": .1, "entry_score": 50}) is None


def test_b3_review_empate_de_evidencia_nao_desempata_pelo_score(monkeypatch):
    """Empate de margem cai na chave do segmento: o score de um segmento não
    pode comprar prioridade de capital sobre o de outro (achado do Codex no
    PR #751)."""
    _saude_ok(monkeypatch)
    alto = _seg("Zeta", "S2", {"ALTO3": 99}, est=1.1)
    baixo = _seg("Alfa", "S1", {"BAIX3": 40}, est=1.1)
    result = b3_review([alto, baixo], pd.DataFrame(), {}, cap=1, sector_cap=1,
                       cycle_cap=1, selic=.15)
    assert _tickers(result) == ["BAIX3"], "o score 99 venceu o empate de margem"
    sem_medida = [_seg("Zeta", "S2", {"ALTO3": 99}, est=None),
                  _seg("Alfa", "S1", {"BAIX3": 40}, est=None)]
    result = b3_review(sem_medida, pd.DataFrame(), {}, cap=1, sector_cap=1,
                       cycle_cap=1, selic=.15)
    assert _tickers(result) == ["BAIX3"]


def test_cards_b3_de_mesmo_peso_seguem_a_prioridade_e_nao_o_score():
    """Aprovado com score 40 vem antes de reprovado com score 99 no mesmo
    peso: a ordem dos cards é a do otimizador (achado do Codex no PR #751)."""
    from design.portfolio_review import _ordenados

    itens = [{"ticker": "REPR3", "weight": .25, "quality": 1.0, "score_segmento": 99,
              "aprovado": False},
             {"ticker": "APRV3", "weight": .25, "quality": 2.0, "score_segmento": 40,
              "aprovado": True}]
    assert [r["ticker"] for r in _ordenados(itens)] == ["APRV3", "REPR3"]
    # Fora da B3 o score observado continua desempatando.
    fii = [{"ticker": "B11", "weight": .5, "type_score": 50},
           {"ticker": "A11", "weight": .5, "type_score": 80}]
    assert [r["ticker"] for r in _ordenados(fii)] == ["A11", "B11"]
