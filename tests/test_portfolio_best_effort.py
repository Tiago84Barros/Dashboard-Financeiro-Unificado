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
    # Sem prioridade de alocação (item fora de rota de revisão), o score
    # observado continua desempatando.
    fii = [{"ticker": "B11", "weight": .5, "type_score": 50},
           {"ticker": "A11", "weight": .5, "type_score": 80}]
    assert [r["ticker"] for r in _ordenados(fii)] == ["A11", "B11"]


# --- Metas por faixa e revisão de FIIs pela mesma seleção da carteira normal ---


def _dois_grupos(n_por_grupo=4):
    rows = ([{"ticker": f"A{i}", "quality": 10 - i} for i in range(n_por_grupo)]
            + [{"ticker": f"B{i}", "quality": 1 - i/10} for i in range(n_por_grupo)])
    grupo_a = np.array([1.0]*n_por_grupo + [0.0]*n_por_grupo)
    return rows, grupo_a


def test_metas_mudam_a_composicao_sem_perder_capital():
    rows, grupo_a = _dois_grupos()
    livre = allocate_partial(rows, [.25]*8)
    assert {r["ticker"] for r in livre["items"]} == {"A0", "A1", "A2", "A3"}
    com_meta = allocate_partial(rows, [.25]*8, target_bands=[
        (grupo_a, 0, .5), (1 - grupo_a, .5, 1)])
    assert com_meta["allocated_weight"] == pytest.approx(1)
    assert com_meta["target_deviation"] == pytest.approx(0, abs=1e-7)
    # Dentro da faixa, a qualidade ainda escolhe: os dois melhores de cada grupo.
    assert {r["ticker"] for r in com_meta["items"]} == {"A0", "A1", "B0", "B1"}


def test_meta_nao_veta_capital_e_o_desvio_fica_medido():
    rows, grupo_a = _dois_grupos()
    # O grupo B só comporta 20%: cumprir o piso de 50% zeraria capital.
    caps = [.25]*4 + [.05]*4
    result = allocate_partial(rows, caps, target_bands=[(1 - grupo_a, .5, 1),
                                                        (grupo_a, 0, .5)])
    assert result["allocated_weight"] == pytest.approx(1)
    assert sum(r["weight"] for r in result["items"] if r["ticker"][0] == "B") \
        == pytest.approx(.2)
    # Piso de B 30 p.p. abaixo e teto de A 30 p.p. acima: o desvio aparece.
    assert result["target_deviation"] == pytest.approx(.6)


def test_meta_invalida_e_recusada():
    rows, grupo_a = _dois_grupos()
    with pytest.raises(ValueError):
        allocate_partial(rows, [.25]*8, target_bands=[(grupo_a, .6, .4)])
    with pytest.raises(ValueError):
        allocate_partial(rows, [.25]*8, target_bands=[(grupo_a[:3], 0, .5)])


def _universo_fii():
    """Papel com percentis altos, tijolo com percentis baixos: no tipo, os
    dois vão de ponta a ponta, mas o número de um não compara com o do outro."""
    from tests.test_fii_portfolio_v4 import _candidate

    kinds = ["tijolo"]*6 + ["papel"]*12 + ["fof"]*3 + ["hibrido"]*3
    rows = [_candidate(i, kind) for i, kind in enumerate(kinds)]
    for row in rows:
        row["income_recurrence"] = .9
        row["type_score"] = {"papel": 99, "tijolo": 40}.get(row["tipo"], 60) - (
            int(row["ticker"][1:4]) % 6)
        if row["tipo"] in {"tijolo", "hibrido"}:
            row.update(tenant_concentration=.2, lease_expiry_concentration_24m=.1)
    return rows


def test_revisao_fii_respeita_as_bandas_quando_ha_capital_para_isso():
    """Achado de 15/11/2026: a revisão punha 70% num tipo cuja banda máxima
    era 45%, porque o percentil dentro do tipo decidia sozinho entre tipos."""
    from core.fii_methodology import tactical_type_bands

    scenario = MacroScenario(selic=10.5, ipca=4)
    result = fii_review(_universo_fii(), PortfolioPolicy(), scenario)
    assert result["allocated_weight"] == pytest.approx(1)
    assert result["target_deviation"] == pytest.approx(0, abs=1e-6)
    for kind, (lo, hi) in tactical_type_bands(scenario).items():
        actual = result["category_targets"][kind]["actual"]
        assert lo - 1e-6 <= actual <= hi + 1e-6, (kind, actual, lo, hi)


def test_revisao_e_carteira_normal_concordam_nas_bandas_por_tipo():
    """Equivalência entre os caminhos: num universo em que a carteira normal
    fecha, a revisão cai dentro das mesmas bandas efetivas."""
    from core.fii_portfolio_v4 import optimize_diligence_portfolio

    scenario = MacroScenario(selic=10.5, ipca=4)
    rows = _universo_fii()
    normal = optimize_diligence_portfolio(rows, scenario)
    assert normal["items"], normal.get("blockers")
    bandas = normal["macro_bands"]
    revisao = fii_review(rows, PortfolioPolicy(), scenario)
    for kind, (lo, hi) in bandas.items():
        assert revisao["category_targets"][kind]["minimum"] == pytest.approx(lo)
        assert revisao["category_targets"][kind]["maximum"] == pytest.approx(hi)
        actual = revisao["category_targets"][kind]["actual"]
        assert lo - 1e-6 <= actual <= hi + 1e-6, (kind, actual, lo, hi)


def test_revisao_fii_usa_a_utilidade_da_carteira_normal():
    """Ponto único: a prioridade da revisão é ``utilidade_fundamental``, com
    perda em cenário adverso e incerteza, e não uma conta própria."""
    from core.fii_portfolio_v4 import utilidade_fundamental

    rows = _universo_fii()
    policy = PortfolioPolicy()
    result = fii_review(rows, policy, MacroScenario(selic=10.5, ipca=4))
    por_ticker = {row["ticker"]: row for row in rows}
    for item in result["items"]:
        esperado = utilidade_fundamental([por_ticker[item["ticker"]]], policy)[0]
        assert item["quality"] == pytest.approx(esperado)
    # A penalidade de incerteza entra: mesma linha, menos confiança. A conta
    # antiga da revisão só perdia .30 * .2; agora perde também a penalidade (e
    # o que a confiança mexe no cenário adverso).
    base = dict(rows[6])
    incerto = {**base, "confidence": .7}
    alto, baixo = utilidade_fundamental([base, incerto], policy)
    assert alto - baixo >= .2*(.30 + policy.uncertainty_penalty) - 1e-9


def test_bandas_da_revisao_fii_sao_as_adaptadas_da_carteira_normal():
    """Tipo sem candidato tem meta zero, como na carteira normal; a meta
    original dele não pode virar desvio impossível de cumprir."""
    rows = [row for row in _universo_fii() if row["tipo"] in {"tijolo", "papel"}]
    result = fii_review(rows, PortfolioPolicy(), MacroScenario(selic=10.5, ipca=4))
    assert result["category_targets"]["fof"]["minimum"] == 0
    assert result["category_targets"]["fof"]["maximum"] == 0


def test_cards_fii_de_mesmo_peso_seguem_a_utilidade_e_nao_o_percentil():
    """Gêmeo do achado do Codex no PR #751: na revisão de FIIs o percentil
    não compara tipos, então ele não desempata cards de tipos diferentes."""
    from design.portfolio_review import _ordenados

    itens = [{"ticker": "PAPL11", "tipo": "papel", "weight": .1, "type_score": 99,
              "quality": .50},
             {"ticker": "TIJO11", "tipo": "tijolo", "weight": .1, "type_score": 40,
              "quality": .60}]
    assert [r["ticker"] for r in _ordenados(itens)] == ["TIJO11", "PAPL11"]


# ── Rota alternativa dos EUA: líderes por indústria, alvo da carteira normal ─
# Até 15/11/2026 `us_review` juntava toda elegível de toda indústria, sem cota
# por indústria, e maximizava o `entry_score` -- percentil DENTRO da indústria.
# O otimizador encostava os pesos no teto por ativo (cerca de 13 nomes a 8%
# contra 26 na carteira normal). Estes testes amarram a revisão à mesma regra.

_SETORES_US = ["Technology", "Healthcare", "Industrials", "Utilities", "Energy",
               "Basic Materials", "Consumer Defensive", "Consumer Cyclical"]


def _universo_us(setores=_SETORES_US, ruido=6.0):
    from tests.test_us_portfolio_creation import _universe

    universo = _universe(groups=16, names_per_group=6)
    universo["sector"] = [setores[int(s[1:3]) % len(setores)] for s in universo["symbol"]]
    rng = np.random.default_rng(7)
    for coluna in ("score_quality", "score_growth", "score_solidity",
                   "score_capital_efficiency", "score_valuation", "score_shareholder"):
        universo[coluna] = universo[coluna] + rng.normal(0, ruido, len(universo))
    return universo


@pytest.mark.parametrize("weighting", ["equal", "score", "inverse_vol"])
def test_us_review_reproduz_a_carteira_normal_quando_ha_industria_aprovada(weighting):
    from core.us_portfolio_creation import build_portfolio_creation

    params = USPortfolioCreationParams(apply_quality_floor=False, weighting=weighting)
    normal = build_portfolio_creation(_universo_us(), params)
    assert normal["review_portfolio"] is None and not normal["holdings"].empty
    revisao = us_review(normal["eligible"], params, normal["industry_audit"])
    pesos_normal = normal["holdings"].set_index("symbol")["weight"].to_dict()
    pesos_revisao = {row["symbol"]: row["weight"] for row in revisao["items"]}
    assert set(pesos_revisao) == set(pesos_normal)
    assert pesos_revisao == pytest.approx(pesos_normal, abs=1e-6)
    assert all(row["aprovado"] for row in revisao["items"])


def test_us_review_com_teto_de_setor_inviavel_mantem_as_candidatas_e_deixa_caixa():
    from core.us_portfolio_creation import build_portfolio_creation

    params = USPortfolioCreationParams(apply_quality_floor=False)
    # Três setores contra teto de 25%: a carteira normal sai vazia e cai na revisão.
    result = build_portfolio_creation(_universo_us(setores=_SETORES_US[:3]), params)
    revisao = result["review_portfolio"]
    assert result["holdings"].empty and revisao is not None
    assert ({row["symbol"] for row in revisao["items"]}
            <= set(result["candidates"]["symbol"]))
    assert all(row["aprovado"] for row in revisao["items"])
    assert revisao["allocated_weight"] <= 3 * params.max_sector_weight + 1e-6
    assert revisao["allocated_weight"] + revisao["unallocated_weight"] == pytest.approx(1)


def test_us_review_sem_aprovada_respeita_cota_e_ordena_industrias_pela_margem():
    from dataclasses import replace

    from core.us_portfolio_creation import build_portfolio_creation

    base = USPortfolioCreationParams(apply_quality_floor=False)
    params = replace(base, min_entry_score=99)
    result = build_portfolio_creation(_universo_us(), params)
    revisao = result["review_portfolio"]
    assert revisao is not None and revisao["items"]
    itens = pd.DataFrame(revisao["items"])
    assert itens.groupby("industry_group").size().max() <= params.leaders_per_industry
    assert not itens["aprovado"].any()
    # Nenhum ativo encosta no teto por ativo: o peso segue o alvo da carteira
    # normal, não o maior score.
    assert len(itens) > 1 / params.max_weight
    assert itens["weight"].max() < params.max_weight - 1e-6
    # A fila de indústrias segue a margem até o corte, não o score de um líder.
    audit = result["industry_audit"].set_index("industry_group")
    margem = np.minimum(audit["avg_entry_score"] - params.min_entry_score,
                        audit["score_edge"] - params.min_score_edge)
    ordem = itens.sort_values("quality", ascending=False)["industry_group"].drop_duplicates()
    assert list(margem.loc[ordem]) == sorted(margem.loc[ordem], reverse=True)
    # Com cota por indústria, a revisão fica perto da carteira normal.
    normal = build_portfolio_creation(_universo_us(), base)["holdings"]
    comum = set(normal["symbol"]) & set(itens["symbol"])
    assert len(comum) >= .8 * len(normal)


def test_card_dos_eua_mostra_a_situacao_da_industria_e_nao_backtest_de_segmento():
    from design.portfolio_review import _backtest

    row = {"ticker": "U001", "industry_group": "Industry 0", "aprovado": False,
           "situacao_grupo": "Indústria em observação · margem -3.0 pts"}
    assert _backtest(row) == "Indústria em observação · margem -3.0 pts"
    assert "Segmento" not in _backtest({**row, "aprovado": True,
                                        "situacao_grupo": "Indústria aprovada"})


def test_us_review_piso_que_zera_as_aprovadas_nao_abre_vaga_para_reprovada(monkeypatch):
    """Achado do Codex no PR #757: decidir "há aprovada" pelos blocos que
    sobreviveram ao piso deixava uma reprovada impedir a rede final."""
    import core.us_quality_floor as floor
    from core.us_portfolio_creation import build_industry_audit, select_industry_leaders

    params = USPortfolioCreationParams(apply_quality_floor=True)
    from core.us_portfolio_creation import prepare_eligible_universe
    eligible, _ = prepare_eligible_universe(_universo_us(), params)
    audit = build_industry_audit(eligible, params)
    aprovadas = set(audit.loc[audit["status"].eq("Aprovada"), "industry_group"].astype(str))
    assert aprovadas and aprovadas != set(audit["industry_group"].astype(str))
    # O piso reprova tudo nas aprovadas e aprova tudo nas demais.
    monkeypatch.setattr(floor, "apply_with_substitution",
                        lambda sel, ranked, frame, pesos, grupo, log, **_:
                        [] if grupo in aprovadas else list(sel))
    normal = select_industry_leaders(eligible, audit, params)
    revisao = us_review(eligible, params, audit)
    assert set(normal["industry_group"].astype(str)) <= aprovadas
    assert {row["industry_group"] for row in revisao["items"]} <= aprovadas
    assert {row["symbol"] for row in revisao["items"]} <= set(normal["symbol"])
    assert revisao["quality_floor_log"].get("carteira_preservada")


def _avaliador_que_veta(vetados, chamadas):
    from core.inteligencia_ativos import avaliacao as av

    critico = av.Alerta("dívida bruta de 4,00x o patrimônio", True)

    def avaliar(ticker):
        chamadas.append(str(ticker))
        alertas = (critico,) if str(ticker) in vetados else ()
        return av.Avaliacao(av.GERAL, (av.Dimensao(av.QUALIDADE, av.ADEQUADA),
                                       av.Dimensao(av.VALUATION, av.JUSTO),
                                       av.Dimensao(av.MERCADO, av.NEUTRO)), alertas)

    return avaliar


def test_rede_final_passa_os_restaurados_pelo_portao_da_inteligencia(monkeypatch):
    """Achado do Codex no PR #757: com o piso reprovando tudo, a rede final
    devolvia líderes pré-piso que nunca passaram pelo avaliador. As duas
    rotas usam a mesma rede; nenhuma deixa entrar o nome que ele barra."""
    import core.us_quality_floor as floor
    from core.us_portfolio_creation import (
        build_industry_audit,
        prepare_eligible_universe,
        select_industry_leaders,
    )

    params = USPortfolioCreationParams(apply_quality_floor=True)
    eligible, _ = prepare_eligible_universe(_universo_us(), params)
    audit = build_industry_audit(eligible, params)
    aprovadas = sorted(set(audit.loc[audit["status"].eq("Aprovada"), "industry_group"]))
    assert aprovadas
    monkeypatch.setattr(floor, "apply_with_substitution",
                        lambda sel, ranked, frame, pesos, grupo, log, **_: [])
    lider = (eligible[eligible["industry_group"].eq(aprovadas[0])]
             .sort_values("entry_score", ascending=False)["symbol"].iloc[0])
    chamadas: list[str] = []
    avaliador = _avaliador_que_veta({lider}, chamadas)

    normal = select_industry_leaders(eligible, audit, params, avaliador)
    assert len(chamadas) == len(set(chamadas)), "um veredito por ticker"
    revisao = us_review(eligible, params, audit, avaliador)

    assert normal.attrs["quality_floor_log"].get("carteira_preservada")
    assert lider not in set(normal["symbol"])
    assert lider not in {row["symbol"] for row in revisao["items"]}
    # A vaga do vetado vai ao próximo da mesma indústria, como no laço.
    assert normal["industry_group"].eq(aprovadas[0]).sum() == params.leaders_per_industry
    assert [v["tk"] for v in normal.attrs["inteligencia_log"]["vetados"]] == [lider]
    assert {row["symbol"] for row in revisao["items"]} <= set(normal["symbol"])


def test_us_review_e_carteira_normal_desempatam_igual_na_fronteira_do_top_n():
    """Achado do Codex no PR #757: com empate nos dois scores na fronteira
    do top_n, a ordem dos blocos (alfabética na normal, margem da auditoria
    na revisão) decidia quem entrava."""
    from dataclasses import replace

    from core.us_portfolio_creation import (
        build_industry_audit,
        prepare_eligible_universe,
        select_industry_leaders,
    )

    params = USPortfolioCreationParams(apply_quality_floor=False)
    eligible, _ = prepare_eligible_universe(_universo_us(), params)
    audit = build_industry_audit(eligible, params)
    aprovadas = set(audit.loc[audit["status"].eq("Aprovada"), "industry_group"])
    vagas = len(aprovadas) * params.leaders_per_industry
    assert vagas > 2
    empatado = eligible.copy()
    dentro = empatado["industry_group"].isin(aprovadas)
    empatado.loc[dentro, ["entry_score", "fundamental_score"]] = [70.0, 60.0]
    params = replace(params, top_n=vagas - 1)

    normal = select_industry_leaders(empatado, audit, params)
    revisao = us_review(empatado, params, audit)

    assert len(normal) == vagas - 1
    assert {row["symbol"] for row in revisao["items"]} == set(normal["symbol"])
    # O desempate é o ticker, não a ordem em que as indústrias chegam.
    assert normal["symbol"].str.upper().is_monotonic_increasing
