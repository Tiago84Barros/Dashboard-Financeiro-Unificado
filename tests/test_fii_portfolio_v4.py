import pytest

from core.fii_methodology import MacroScenario, tactical_type_bands
from core.fii_portfolio_v4 import (
    PortfolioPolicy,
    _candidate_pool,
    _dimension_matrix,
    optimize_diligence_portfolio,
    portfolio_constraint_violations,
)


def _candidate(i: int, fii_type: str, complete: bool = True) -> dict:
    regions = ("Norte", "Nordeste", "Centro-Oeste", "Sudeste", "Sul")
    row = {
        "ticker": f"F{i:03d}11", "tipo": fii_type, "type_score": 80 - i,
        "confidence": .9, "coverage": .95, "publication_status": "validated",
        "dy_12m": .10, "liquidez_diaria": 3_000_000,
        "manager": f"gestor-{i}", "sector": f"setor-{i}",
    }
    if fii_type in ("tijolo", "hibrido"):
        row.update(tenants={f"locatario-{i}": 1.0}, regions={regions[i % len(regions)]: 1.0})
    if fii_type in ("papel", "hibrido"):
        row.update(debtors={f"devedor-{i}": 1.0}, issuers={f"emissor-{i}": 1.0},
                   indexers={f"indexador-{i}": 1.0})
    if not complete:
        row.pop("manager")
    return row


def test_hybrid_coverage_excludes_non_material_economic_side():
    rows = [
        _candidate(0, "hibrido") | {"pct_imoveis": .30, "pct_papel": 0.0},
        _candidate(1, "hibrido") | {"pct_imoveis": 0.0, "pct_papel": .70},
    ]
    rows[0].pop("debtors")
    rows[0].pop("issuers")
    rows[0].pop("indexers")
    rows[1].pop("sector")
    rows[1].pop("tenants")
    rows[1].pop("regions")

    _, _, debtor_coverage = _dimension_matrix(rows, "debtor")
    _, region_labels, region_coverage = _dimension_matrix(rows, "region")

    assert debtor_coverage == 1.0
    assert region_coverage == 1.0
    assert region_labels == ["Norte"]


def test_optimizer_respects_asset_limit_and_reports_scenarios():
    types = ["tijolo", "papel", "fof", "hibrido"] * 3
    rows = [_candidate(i, fii_type) for i, fii_type in enumerate(types)]
    result = optimize_diligence_portfolio(
        rows, MacroScenario(selic=11, ipca=4, selic_change_12m=-2.5),
        policy=PortfolioPolicy(max_assets=12, max_asset=.15),
    )
    assert result["items"]
    assert max(item["weight"] for item in result["items"]) <= .15001
    assert abs(sum(item["weight"] for item in result["items"]) - 1) < 1e-6
    assert {"selic_alta", "vacancia", "credito"}.issubset(result["scenario_returns"])


def test_default_uncertainty_cap_allows_diligence_portfolio_without_publication():
    types = ["tijolo", "papel", "fof", "hibrido"] * 3
    rows = [_candidate(i, fii_type) | {"confidence": .68,
                                      "publication_status": "diligence_only"}
            for i, fii_type in enumerate(types)]

    result = optimize_diligence_portfolio(rows, MacroScenario(selic=15, ipca=4.5))

    assert result["items"]
    assert not result["can_publish"]
    assert result["weighted_uncertainty"] <= .35 + 1e-6


def test_candidate_preselection_keeps_enough_confidence_for_final_constraint():
    types = ["tijolo", "papel", "fof", "hibrido"] * 4
    rows = [_candidate(i, fii_type) for i, fii_type in enumerate(types)]
    for row in rows[:12]:
        row["confidence"] = .55
        row["type_score"] += 30

    result = optimize_diligence_portfolio(
        rows, MacroScenario(selic=12, ipca=4.5),
        policy=PortfolioPolicy(max_assets=12, max_weighted_uncertainty=.35),
    )

    assert result["items"]
    assert result["weighted_uncertainty"] <= .35 + 1e-6


def test_infeasible_confidence_is_reported_as_missing_data_prerequisite():
    types = ["tijolo", "papel", "fof", "hibrido"] * 3
    rows = [
        _candidate(i, fii_type) | {"confidence": .40}
        for i, fii_type in enumerate(types)
    ]

    result = optimize_diligence_portfolio(
        rows, MacroScenario(selic=12, ipca=4.5),
    )

    assert result["items"] == []
    assert result["failure_stage"] == "data_prerequisites"


def test_optimizer_adapts_bands_when_one_type_has_no_candidate():
    rows = [_candidate(i, fii_type) for i, fii_type in enumerate(
        ["tijolo", "papel", "fof"] * 4
    )]

    result = optimize_diligence_portfolio(
        rows, MacroScenario(selic=12, ipca=4.5),
    )

    assert result["items"]
    assert "hibrido" in result["band_adaptation"]["unavailable_types"]
    assert result["macro_bands"]["hibrido"] == (0.0, 0.0)
    assert max(
        sum(item["weight"] for item in result["items"] if item["tipo"] == fii_type)
        for fii_type in {"tijolo", "papel", "fof"}
    ) <= .70 + 1e-6


def test_optimizer_blocks_with_only_one_eligible_type():
    rows = [_candidate(i, "tijolo") for i in range(12)]

    result = optimize_diligence_portfolio(
        rows, MacroScenario(selic=12, ipca=4.5),
    )

    assert result["items"] == []
    assert "menos de 2 categorias" in result["blockers"][0]


def test_turnover_penalty_retains_feasible_previous_holdings():
    types = ["tijolo", "papel", "fof", "hibrido"] * 3
    rows = [_candidate(i, fii_type) for i, fii_type in enumerate(types)]
    previous = {row["ticker"]: 1 / len(rows) for row in rows}
    perturbed = [
        row | {"type_score": row["type_score"] + (0.05 if index % 2 else 0)}
        for index, row in enumerate(rows)
    ]

    result = optimize_diligence_portfolio(
        perturbed, MacroScenario(selic=12, ipca=4.5),
        policy=PortfolioPolicy(turnover_penalty=.05),
        previous_weights=previous,
    )

    selected = {item["ticker"] for item in result["items"]}
    assert result["items"]
    assert len(selected.intersection(previous)) >= 8


def test_missing_exposure_coverage_blocks_publication():
    types = ["tijolo", "papel", "fof", "hibrido"] * 3
    rows = [_candidate(i, fii_type) for i, fii_type in enumerate(types)]
    for row in rows[:8]:
        row.pop("sector")
    result = optimize_diligence_portfolio(rows, MacroScenario(selic=12, ipca=5),
                                          policy=PortfolioPolicy(max_assets=12))
    assert not result["can_publish"]
    assert "sector" in result.get("unresolved_dimensions", [])


def test_preselection_reserves_documented_assets_for_required_coverage():
    types = ["tijolo", "papel", "fof", "hibrido"] * 6
    rows = [_candidate(i, fii_type) for i, fii_type in enumerate(types)]
    for row in rows[:12]:
        row.pop("sector", None)
        if row["tipo"] in {"papel", "hibrido"}:
            row.pop("issuers", None)
        row["type_score"] += 30

    result = optimize_diligence_portfolio(
        rows, MacroScenario(selic=12, ipca=5),
        policy=PortfolioPolicy(max_assets=12),
    )

    assert result["items"]
    assert result["can_publish"]
    assert result["dimension_coverage"]["sector"]["coverage"] >= .80
    assert result["dimension_coverage"]["issuer"]["coverage"] >= .80
    assert all(item["weight"] >= .02 - 1e-6 for item in result["items"])


def test_preselection_applies_observed_limits_before_coverage_crosses_threshold():
    types = ["tijolo", "papel", "fof", "hibrido"] * 4
    rows = [_candidate(i, fii_type) for i, fii_type in enumerate(types)]
    property_rows = [row for row in rows if row["tipo"] in {"tijolo", "hibrido"}]
    for row in property_rows:
        row["regions"] = {"Sudeste": 1.0}
    property_rows[-1]["regions"] = {"Sul": 1.0}
    property_rows[-1]["type_score"] = 1
    property_rows[0].pop("regions")
    property_rows[1].pop("regions")

    scenario = MacroScenario(selic=12, ipca=5)
    _, diagnostics = _candidate_pool(
        rows,
        tactical_type_bands(scenario),
        PortfolioPolicy(max_assets=12, max_region=.35),
        scenario,
    )

    selected_weights = diagnostics["selected_weights"]
    sudeste = sum(
        selected_weights.get(row["ticker"], 0.0)
        for row in property_rows
        if row.get("regions") == {"Sudeste": 1.0}
    )
    assert diagnostics["universe_dimension_coverage"]["region"] < .80
    assert sudeste <= .35 + 1e-8


def test_sector_coverage_applies_only_to_property_funds():
    types = ["tijolo", "papel", "fof", "hibrido"] * 3
    rows = [_candidate(i, fii_type) for i, fii_type in enumerate(types)]
    for row in rows:
        if row["tipo"] in {"papel", "fof"}:
            row.pop("sector", None)

    result = optimize_diligence_portfolio(
        rows, MacroScenario(selic=12, ipca=5),
        policy=PortfolioPolicy(max_assets=12),
    )

    assert result["items"]
    assert result["can_publish"]
    assert result["dimension_coverage"]["sector"]["coverage"] == 1.0


def test_manager_without_historical_identity_is_conditional_and_explicit():
    types = ["tijolo", "papel", "fof", "hibrido"] * 3
    rows = [_candidate(i, fii_type) for i, fii_type in enumerate(types)]
    for row in rows:
        row.pop("manager", None)

    result = optimize_diligence_portfolio(
        rows, MacroScenario(selic=12, ipca=5),
        policy=PortfolioPolicy(max_assets=12),
    )

    assert result["items"]
    assert result["can_publish"]
    assert "manager" in result["unresolved_dimensions"]
    assert "manager" not in result["unresolved_critical_dimensions"]


def test_structurally_unavailable_tenant_identity_is_explicit_but_not_blocking():
    types = ["tijolo", "papel", "fof", "hibrido"] * 3
    rows = [_candidate(i, fii_type) for i, fii_type in enumerate(types)]
    for row in rows:
        row.pop("tenants", None)

    result = optimize_diligence_portfolio(
        rows, MacroScenario(selic=12, ipca=5),
        policy=PortfolioPolicy(max_assets=12),
    )

    assert result["items"]
    assert result["can_publish"]
    assert "tenant" in result["unresolved_dimensions"]
    assert "tenant" not in result["unresolved_critical_dimensions"]


def test_optimizer_finds_feasible_seed_when_equal_weights_break_illiquid_cap():
    types = ["tijolo", "papel", "fof", "hibrido"] * 3
    rows = [_candidate(i, fii_type) for i, fii_type in enumerate(types)]
    for row in rows:
        if row["tipo"] == "hibrido":
            row["liquidez_diaria"] = 100_000
    result = optimize_diligence_portfolio(
        rows, MacroScenario(selic=12, ipca=5),
        policy=PortfolioPolicy(max_assets=12, max_illiquid=.10),
    )
    assert result["items"]
    illiquid_weight = sum(item["weight"] for item in result["items"]
                           if item["liquidez_diaria"] < 1_000_000)
    assert illiquid_weight <= .10001


def test_candidate_reservation_diversifies_sector_when_band_exceeds_sector_cap():
    types = ["tijolo", "papel", "fof", "hibrido"] * 3
    rows = [_candidate(i, fii_type) for i, fii_type in enumerate(types)]
    paper = [row for row in rows if row["tipo"] == "papel"]
    paper[0]["sector"] = "CRI"
    paper[1]["sector"] = "CRI"
    paper[2]["sector"] = "Agro"
    paper[2]["type_score"] = 1
    result = optimize_diligence_portfolio(
        rows, MacroScenario(selic=15, ipca=4, selic_change_12m=1),
        policy=PortfolioPolicy(max_assets=12, max_sector=.25),
    )
    assert result["items"]
    assert any(item["ticker"] == paper[2]["ticker"] for item in result["items"])


def test_optimizer_uses_observed_correlation_with_explicit_coverage():
    types = ["tijolo", "papel", "fof", "hibrido"] * 3
    rows = [_candidate(i, fii_type) for i, fii_type in enumerate(types)]
    tickers = [row["ticker"] for row in rows]
    correlation = {
        ticker: {other: (1.0 if ticker == other else .35) for other in tickers}
        for ticker in tickers
    }

    result = optimize_diligence_portfolio(
        rows, MacroScenario(selic=12, ipca=5),
        policy=PortfolioPolicy(max_assets=12),
        correlation_matrix=correlation, correlation_penalty=.12,
    )

    assert result["items"]
    assert result["correlation_risk"] is not None
    assert result["correlation_info"]["coverage"] == 1.0
    assert result["correlation_penalty"] == .12


def test_final_portfolio_is_revalidated_after_weight_normalization():
    policy = PortfolioPolicy(max_asset=.15, max_assets=12)
    bands = {"tijolo": (.25, .55), "papel": (.20, .50),
             "fof": (.05, .20), "hibrido": (.05, .20)}
    items = [_candidate(i, fii_type) | {"weight": weight}
             for i, (fii_type, weight) in enumerate([
                 ("tijolo", .30), ("tijolo", .20),
                 ("papel", .20), ("papel", .10),
                 ("fof", .10), ("hibrido", .10),
             ])]

    violations = portfolio_constraint_violations(items, bands, policy)

    assert "peso individual acima do limite" in violations


def test_final_portfolio_rejects_dust_positions_below_economic_minimum():
    policy = PortfolioPolicy(min_asset_weight=.02)
    bands = {"tijolo": (0.0, 1.0), "papel": (0.0, 1.0),
             "fof": (0.0, 1.0), "hibrido": (0.0, 1.0)}
    items = [
        _candidate(0, "tijolo") | {"weight": .99},
        _candidate(1, "papel") | {"weight": .01},
    ]

    violations = portfolio_constraint_violations(items, bands, policy)

    assert "peso individual abaixo do mínimo econômico" in violations


def test_low_correlation_coverage_blocks_publication_when_penalty_is_enabled():
    types = ["tijolo", "papel", "fof", "hibrido"] * 3
    rows = [_candidate(i, fii_type) for i, fii_type in enumerate(types)]
    correlation = {rows[0]["ticker"]: {rows[1]["ticker"]: .4}}

    result = optimize_diligence_portfolio(
        rows, MacroScenario(selic=12, ipca=5),
        policy=PortfolioPolicy(max_assets=12, min_correlation_coverage=.80),
        correlation_matrix=correlation, correlation_penalty=.12,
    )

    assert result["items"]
    assert not result["can_publish"]
    assert any("cobertura de correlação" in reason for reason in result["blockers"])
    assert result["trailing_yield_12m"] == result["expected_yield"]


@pytest.mark.parametrize("macro", [
    MacroScenario(selic=15, ipca=4.5, selic_change_12m=1.5),
    MacroScenario(selic=9, ipca=3.5, selic_change_12m=-3.0),
    MacroScenario(selic=13, ipca=7.0, selic_change_12m=0.5),
])
def test_portfolio_matrix_remains_feasible_across_macro_regimes(macro):
    types = ["tijolo", "papel", "fof", "hibrido"] * 3
    rows = [
        _candidate(i, fii_type) | {
            "pvp": .9, "duration_anos": 3.5, "leverage": .08,
            "vacancia_fisica": .06, "delinquency": .01, "ltv": .55,
        }
        for i, fii_type in enumerate(types)
    ]

    result = optimize_diligence_portfolio(
        rows, macro, policy=PortfolioPolicy(max_assets=12, max_asset=.15),
    )

    assert result["items"]
    assert result["constraint_violations"] == []
    assert abs(sum(item["weight"] for item in result["items"]) - 1) < 1e-6
    assert 0 < result["effective_assets"] <= 12


def test_macro_context_changes_utility_without_breaking_constraints():
    types = ["tijolo", "papel", "fof", "hibrido"] * 3
    rows = [_candidate(i, fii_type) for i, fii_type in enumerate(types)]
    impacts = {
        row["ticker"]: (100.0 if index % 2 else -100.0)
        for index, row in enumerate(rows)
    }

    result = optimize_diligence_portfolio(
        rows, MacroScenario(selic=12, ipca=5),
        policy=PortfolioPolicy(max_assets=12, max_asset=.15),
        macro_impacts=impacts, macro_mode="moderate",
    )

    assert result["items"]
    assert result["constraint_violations"] == []
    assert result["macro_mode"] == "moderate"
    assert result["macro_coverage"] == 1.0
    assert abs(sum(item["weight"] for item in result["items"]) - 1) < 1e-6
    assert max(abs(item["macro_score_adjustment"]) for item in result["items"]) <= 10


def test_fundo_sem_protecao_divulgada_nao_passa_de_metade_do_teto():
    """A verificação incide sobre o peso FINAL: já houve teto de 15%
    respeitado em toda chamada e violado em 27,8% no acumulado."""
    import numpy as np

    from core.fii_portfolio_v4 import PortfolioPolicy, _teto_por_ativo

    policy = PortfolioPolicy(max_asset=.15)
    rows = [
        {"ticker": "OPACO11", "tipo": "tijolo"},
        {"ticker": "ABERTO11", "tipo": "tijolo",
         "tenant_concentration": .10, "lease_expiry_concentration_24m": .10},
        {"ticker": "PAPEL11", "tipo": "papel"},
    ]
    assert np.allclose(_teto_por_ativo(rows, policy), [.075, .15, .15])


def test_excesso_de_opacidade_e_reportado_para_o_fundo_sem_bloquear():
    """Peso acima da metade do teto do spec não é violação bloqueante
    (portfolio_constraint_violations): é consequência explícita e nomeada
    de ``opacidade_excedente``, contra o valor absoluto ``max_asset * .5``,
    nunca contra o teto que o afrouxamento por viabilidade tenha alargado."""
    from core.fii_portfolio_v4 import (
        PortfolioPolicy,
        opacidade_excedente,
        portfolio_constraint_violations,
    )

    policy = PortfolioPolicy(max_asset=.15)
    itens = [
        {"ticker": "OPACO11", "tipo": "tijolo", "weight": .12, "confidence": .8,
         "protecao_nao_divulgada": True},
        {"ticker": "PAPEL11", "tipo": "papel", "weight": .88, "confidence": .8,
         "protecao_nao_divulgada": False},
    ]

    excedentes = opacidade_excedente(itens, policy)
    assert len(excedentes) == 1
    assert excedentes[0]["ticker"] == "OPACO11"
    assert excedentes[0]["teto_spec"] == policy.max_asset / 2
    assert excedentes[0]["peso_final"] == .12

    # .12 < .15 (o teto absoluto do ativo): não é violação bloqueante.
    violacoes = portfolio_constraint_violations(itens, {}, policy)
    assert not any("OPACO11" in v for v in violacoes)


def test_pesos_finais_de_fundos_opacos_respeitam_a_metade_do_teto_absoluto():
    """Requisito 4 (teste 5, reescrito): os PESOS FINAIS do otimizador contra
    o valor absoluto ``policy.max_asset / 2``, num universo viável com o
    teto reduzido — sem precisar de nenhum afrouxamento por viabilidade."""
    policy = PortfolioPolicy(max_assets=12)
    rows = [_candidate(i, "tijolo") for i in range(6)]
    rows += [_candidate(i, "papel") for i in range(6, 9)]
    rows += [_candidate(9, "fof")]
    rows.append(_candidate(10, "hibrido") | {
        "tenant_concentration": .10, "lease_expiry_concentration_24m": .10,
    })

    result = optimize_diligence_portfolio(
        rows, MacroScenario(selic=12, ipca=5), policy=policy)

    assert result["items"]
    assert result["can_publish"]
    assert result["protecao_excedida"] == []
    assert not any("afrouxado" in nota for nota in (result.get("viability_notes") or []))
    opacos = [item for item in result["items"] if item["protecao_nao_divulgada"]]
    assert opacos
    for item in opacos:
        assert item["weight"] <= policy.max_asset / 2 + 1e-6


def test_afrouxamento_por_interacao_com_outro_limite_e_minimo_e_aparece_no_resultado():
    """Requisito 4 (teste separado): num universo apertado em que a
    interação do desconto de opacidade com outro limite pré-existente
    (aqui, devedor/emissor únicos por papel) inviabilizaria a carteira, o
    afrouxamento (``_afrouxa_ate_viavel``) cede pelo mínimo necessário — não
    desliga o desconto inteiro — e essa consequência aparece de forma
    legível no resultado (``viability_notes``). Nenhuma assertiva aqui se
    compara ao teto afrouxado em si: só confirma que o afrouxamento foi
    parcial (pesos ficam entre a metade e o teto cheio, nunca travados
    exatamente no teto cheio de .15 para todos os ativos)."""
    from core.fii_portfolio_v4 import _candidate_pool

    policy = PortfolioPolicy(max_assets=12)
    papel_rows = [_candidate(i, "papel") for i in range(3)]
    tijolo_rows = [_candidate(i, "tijolo") for i in range(10, 18)]
    bands = {"papel": (.30, .50), "tijolo": (.50, .90)}

    selected, info = _candidate_pool(
        papel_rows + tijolo_rows, bands, policy, MacroScenario(selic=12, ipca=5))

    assert selected
    assert info["status"] == "milp_feasible"
    notas = info.get("viability_notes") or []
    assert any("afrouxado" in nota for nota in notas)

    pesos_tijolo = [
        weight for ticker, weight in info["selected_weights"].items()
        if ticker.startswith("F0") and int(ticker[1:4]) >= 10
    ]
    assert pesos_tijolo
    # Afrouxamento minimo: os pesos ultrapassam a metade do teto (.075) mas
    # nao ficam presos no teto cheio (.15) — sinal de que o desconto cedeu
    # parcialmente, nao foi desligado por completo.
    assert all(peso > .075 + 1e-6 for peso in pesos_tijolo)
    assert not all(peso >= .15 - 1e-6 for peso in pesos_tijolo)


def test_carteira_reporta_o_yield_recorrente_alem_do_divulgado():
    from core.fii_portfolio_v4 import _resumo_de_renda

    itens = [
        {"weight": .5, "dy_12m": .1817, "income_recurrence": .5825},
        {"weight": .5, "dy_12m": .1379, "income_recurrence": .8886},
    ]
    resumo = _resumo_de_renda(itens)
    assert resumo["trailing_yield_12m"] == round((.1817 + .1379) / 2, 6)
    assert resumo["recurrent_yield_12m"] == round((.1058 + .1225) / 2, 6)


def test_opacidade_cede_quando_inviabilizaria_a_banda_do_tipo():
    """Custo que zera a carteira deixou de ser custo e virou veto.

    Precedente no próprio arquivo: max_weighted_uncertainty foi de .30 para .35
    porque tornava o LP inviável no universo real.
    """
    from core.fii_portfolio_v4 import (
        PortfolioPolicy,
        _afrouxa_teto_por_viabilidade,
        _teto_por_ativo,
    )

    policy = PortfolioPolicy(max_asset=.15)
    # Cinco tijolos, todos opacos: 5 x .075 = .375 contra um piso de banda .40.
    rows = [{"ticker": f"T{i}11", "tipo": "tijolo"} for i in range(5)]
    rows += [{"ticker": "P11", "tipo": "papel"}]
    bands = {"tijolo": (.40, .60), "papel": (.15, .35)}

    caps, notas = _afrouxa_teto_por_viabilidade(
        _teto_por_ativo(rows, policy), rows, policy, bands)
    assert caps[:5].sum() >= .40
    assert caps.max() <= policy.max_asset
    assert any("tijolo" in nota for nota in notas)


def test_afrouxamento_nao_ocorre_quando_ha_folga():
    """Com folga, a opacidade continua custando: relaxar sempre apagaria a regra."""
    from core.fii_portfolio_v4 import (
        PortfolioPolicy,
        _afrouxa_teto_por_viabilidade,
        _teto_por_ativo,
    )

    policy = PortfolioPolicy(max_asset=.15)
    rows = [{"ticker": "OPACO11", "tipo": "tijolo"}]
    rows += [{"ticker": f"OK{i}11", "tipo": "tijolo",
              "tenant_concentration": .10,
              "lease_expiry_concentration_24m": .10} for i in range(4)]
    bands = {"tijolo": (.40, .60)}

    caps, notas = _afrouxa_teto_por_viabilidade(
        _teto_por_ativo(rows, policy), rows, policy, bands)
    assert caps[0] == .075
    assert notas == []


def test_teto_por_ativo_sempre_comporta_uma_carteira_inteira():
    """Soma dos tetos abaixo de 1 devolve carteira vazia sem dizer por quê."""
    import numpy as np

    from core.fii_portfolio_v4 import (
        PortfolioPolicy,
        _afrouxa_teto_por_viabilidade,
        _teto_por_ativo,
    )

    policy = PortfolioPolicy(max_asset=.15, max_assets=12)
    rows = [{"ticker": f"T{i}11", "tipo": "tijolo"} for i in range(12)]
    caps, notas = _afrouxa_teto_por_viabilidade(
        _teto_por_ativo(rows, policy), rows, policy, {})
    assert np.sort(caps)[::-1][:policy.max_assets].sum() >= 1.0
    assert notas


def test_renda_recorrente_ausente_sai_do_calculo_em_vez_de_entrar_como_zero():
    """Fundo sem `income_recurrence` rebaixava a renda da carteira com 0%.

    O readmitido por "renda recorrente ausente" entrava no ponderado valendo
    zero, sem renormalizar o peso dos que têm o dado e sem avisar. Ausência de
    medição não é medição de ausência.
    """
    from core.fii_portfolio_v4 import _resumo_de_renda

    itens = [
        {"weight": .5, "dy_12m": .12, "income_recurrence": .90},
        {"weight": .5, "dy_12m": .12},
    ]
    resumo = _resumo_de_renda(itens)

    assert resumo["recurrent_yield_12m"] == .108
    assert resumo["recurrent_yield_coverage"] == .5


def test_carteira_sem_nenhuma_recorrencia_nao_publica_zero_por_cento():
    from core.fii_portfolio_v4 import _resumo_de_renda

    resumo = _resumo_de_renda([{"weight": 1.0, "dy_12m": .12}])

    assert resumo["recurrent_yield_12m"] is None
    assert resumo["recurrent_yield_coverage"] == 0.0


def test_sem_piso_o_desempate_do_objetivo_favorece_menos_ativos():
    # Teto individual alto (.5) permite que a soma de 100% seja atingida com
    # poucos ativos. O desempate `+1e-6` por ativo selecionado no objetivo
    # (core/fii_portfolio_v4.py) empurra o solver para o mínimo que soma
    # 100% respeitando as bandas — não para o teto de cardinalidade.
    types = ["tijolo", "papel", "fof", "hibrido"] * 4
    rows = [_candidate(i, fii_type) for i, fii_type in enumerate(types)]
    scenario = MacroScenario(selic=11, ipca=4, selic_change_12m=-2.5)
    policy = PortfolioPolicy(max_assets=12, max_asset=.5, min_asset_weight=.02,
                              min_assets=0)

    resultado = optimize_diligence_portfolio(rows, scenario, policy=policy)

    assert resultado["items"]
    assert len(resultado["items"]) < 12


def test_piso_de_cardinalidade_e_respeitado_quando_viavel():
    # Reaproveita a estrutura de
    # test_sem_piso_o_desempate_do_objetivo_favorece_menos_ativos, mas afrouxa
    # todos os tetos de concentração por dimensão (só max_asset=.3 segue
    # ativo) para dar ao desempate do objetivo espaço de sobra para escolher
    # poucos ativos. A versão anterior deste teste (min_assets=8, tetos
    # default) passava com o piso completamente desligado — o cenário
    # devolvia 9 ativos por conta própria — e não provava nada sobre o piso
    # (achado CRÍTICO da revisão de 8982e0c). Por isso a primeira asserção
    # abaixo roda o MESMO cenário com min_assets=0 e confirma, por execução,
    # que ele devolve menos que o piso exigido a seguir: só então o piso=8
    # é uma prova de que a restrição é quem produz o resultado.
    types = ["tijolo", "papel", "fof", "hibrido"] * 4
    rows = [_candidate(i, fii_type) for i, fii_type in enumerate(types)]
    scenario = MacroScenario(selic=11, ipca=4, selic_change_12m=-2.5)
    tetos_afrouxados = dict(
        max_assets=12, min_asset_weight=.02, max_asset=.3,
        max_manager=1.0, max_sector=1.0, max_tenant=1.0, max_debtor=1.0,
        max_issuer=1.0, max_indexer=1.0, max_region=1.0, max_illiquid=1.0,
        min_dimension_coverage=0.0, min_distinct_types=1, max_single_type=1.0,
    )

    sem_piso = optimize_diligence_portfolio(
        rows, scenario, policy=PortfolioPolicy(**tetos_afrouxados, min_assets=0))
    assert len(sem_piso["items"]) < 8, (
        "cenário não serve para provar o piso: sem piso já devolve "
        f"{len(sem_piso['items'])} ativos, que já é >= 8"
    )

    resultado = optimize_diligence_portfolio(
        rows, scenario, policy=PortfolioPolicy(**tetos_afrouxados, min_assets=8))

    assert len(resultado["items"]) >= 8
    assert not resultado.get("blockers")


def test_piso_de_cardinalidade_cede_em_degraus_quando_inviavel_e_nunca_zera():
    # 6 líquidos (teto .2 cada, capacidade 1.2 — sobra sem precisar de
    # ilíquido) e 8 ilíquidos (liquidez abaixo do piso, somados no máximo
    # a max_illiquid=.10). Cada ilíquido selecionado carrega pelo menos
    # min_asset_weight (.02), então o orçamento de .10 só comporta 5
    # ilíquidos ao mesmo tempo. Um piso de 12 exige 6 ilíquidos (12 - 6
    # líquidos) — estoura o orçamento e fica infactível; o piso tem que
    # ceder em degraus até caber (11 = 5 ilíquidos, dentro do teto), nunca
    # devolver carteira vazia.
    rows = [_candidate(i, "tijolo") for i in range(14)]
    for row in rows[6:]:
        row["liquidez_diaria"] = 0
    scenario = MacroScenario(selic=11, ipca=4, selic_change_12m=-2.5)
    policy = PortfolioPolicy(max_assets=12, min_assets=12, min_distinct_types=1,
                              max_single_type=1.0, max_asset=.2)

    resultado = optimize_diligence_portfolio(rows, scenario, policy=policy)

    assert resultado["items"]
    assert len(resultado["items"]) < 12
    assert any("piso de cardinalidade cedido" in nota
               for nota in resultado.get("viability_notes") or [])


def test_piso_padrao_nao_excede_o_teto_quando_top_n_e_pequeno():
    # core/fii_validation.py:417 constrói PortfolioPolicy(max_assets=int(top_n))
    # sem min_assets explícito — o piso default (10) não pode exigir mais
    # ativos do que o próprio teto acabou de definir, ou o backtest com top_n
    # pequeno (usado em testes e no PIT) fica infactível por construção.
    # fof e tetos de concentração soltos isolam exatamente essa questão: sem
    # eles, um único ativo já tropeça em max_manager/max_sector (cada linha
    # tem gestor e setor próprios), o que teria nada a ver com o piso.
    rows = [
        {"ticker": f"F{i:03d}11", "tipo": "fof", "type_score": 80 - i,
         "confidence": .9, "coverage": .95, "publication_status": "validated",
         "dy_12m": .10, "liquidez_diaria": 3_000_000, "manager": f"gestor-{i}",
         "sector": f"setor-{i}"}
        for i in range(4)
    ]
    scenario = MacroScenario(selic=11, ipca=4, selic_change_12m=-2.5)
    policy = PortfolioPolicy(max_assets=1, min_distinct_types=1, max_asset=1.0,
                              max_single_type=1.0, max_manager=1.0, max_sector=1.0)

    resultado = optimize_diligence_portfolio(rows, scenario, policy=policy)

    assert resultado["items"]
    assert len(resultado["items"]) <= 1
    # O que o clamp em core/fii_portfolio_v4.py:500 realmente evita: sem ele,
    # o laço de degraus ainda chega à mesma contagem final (o clamp não é
    # necessário para a CONTAGEM), mas grava uma nota de cessão falsa —
    # "piso cedido de 12 para 1" quando não houve cessão nenhuma, foi
    # max_assets=1 por desenho — e gasta até 11 tentativas de MILP
    # desperdiçadas por chamada. A contagem sozinha (asserção acima) não
    # pega essa regressão; só a ausência de nota pega.
    assert not [n for n in (resultado.get("viability_notes") or [])
                if "piso de cardinalidade cedido" in n]
