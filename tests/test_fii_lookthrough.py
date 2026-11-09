import pytest

from core.fii_lookthrough import (
    dimension_is_applicable,
    has_observed_dimension,
    normalized_dimension_mapping,
    summarize_lookthrough_coverage,
    supplementary_evidence_score,
)


def test_lookthrough_counts_only_explicit_named_exposures():
    rows = [
        {
            "ticker": "TIJO11", "tipo": "tijolo", "sector": "Logística",
            "regions": {"SP": .7, "MG": .3},
            "tenant_concentration": .25,
        },
        {
            "ticker": "PAPR11", "tipo": "papel",
            "issuers": {"Emissor A": .6, "Emissor B": .4},
            "indexers": {"IPCA": 1.0},
            "debtor_diversification": .4,
        },
    ]

    result = summarize_lookthrough_coverage(rows)

    assert result["dimensions"]["sector"]["coverage"] == 1
    assert result["dimensions"]["issuer"]["coverage"] == 1
    assert result["dimensions"]["region"]["coverage"] == 1
    assert result["dimensions"]["indexer"]["coverage"] == 1
    assert result["dimensions"]["tenant"]["coverage"] == 0
    assert result["dimensions"]["debtor"]["coverage"] == 0
    assert has_observed_dimension(rows[0], "tenant") is False
    assert has_observed_dimension(rows[1], "debtor") is False


def test_lookthrough_required_dimensions_fail_closed():
    result = summarize_lookthrough_coverage([
        {"ticker": "TIJO11", "tipo": "tijolo"},
        {"ticker": "PAPR11", "tipo": "papel"},
    ])

    assert result["required_ready"] is False
    assert set(result["required_blockers"]) == {"sector", "issuer"}
    assert result["dimensions"]["tenant"]["source_limitation"]


def test_lookthrough_ignores_invalid_and_non_positive_weights():
    row = {
        "ticker": "PAPR11",
        "tipo": "papel",
        "issuers": {"": .5, "Emissor": 0, "Outro": "inválido"},
    }

    assert has_observed_dimension(row, "issuer") is False


def test_regions_are_canonicalized_and_aggregated_by_ibge_macroregion():
    row = {
        "ticker": "TIJO11",
        "tipo": "tijolo",
        "regions": {"SP": .40, "Sudeste": .30, "RJ": .30, "Inválida": .50},
    }

    assert normalized_dimension_mapping(row, "region") == {"Sudeste": 1.0}
    assert has_observed_dimension(row, "region") is True


def test_hybrid_dimension_applicability_follows_material_economic_exposure():
    property_hybrid = {
        "ticker": "PROP11", "tipo": "hibrido",
        "pct_imoveis": .26, "pct_papel": 0.0,
        "regions": {"SP": 1.0},
    }
    credit_hybrid = {
        "ticker": "CRED11", "tipo": "hibrido",
        "pct_imoveis": 0.0, "pct_papel": .66,
        "debtors": {"Devedor": 1.0},
        "indexers": {"IPCA": 1.0},
    }

    assert dimension_is_applicable(property_hybrid, "region") is True
    assert dimension_is_applicable(property_hybrid, "debtor") is False
    assert dimension_is_applicable(property_hybrid, "indexer") is False
    assert dimension_is_applicable(credit_hybrid, "region") is False
    assert dimension_is_applicable(credit_hybrid, "tenant") is False
    assert dimension_is_applicable(credit_hybrid, "debtor") is True
    assert supplementary_evidence_score(property_hybrid) == 1.0
    assert supplementary_evidence_score(credit_hybrid) == 1.0


def test_missing_hybrid_composition_preserves_conservative_applicability():
    row = {"ticker": "HBRD11", "tipo": "hibrido"}

    assert dimension_is_applicable(row, "region") is True
    assert dimension_is_applicable(row, "debtor") is True


def test_manager_dimension_is_applicable_to_every_fii_type():
    """`max_manager` so morde se a dimensao for aplicavel a algum fundo.

    Sem `manager` em APPLICABLE_TYPES a mascara de aplicabilidade era zero
    em qualquer carteira, a cobertura caia para 0.0 e o teto de
    concentracao por gestora era descartado antes de virar restricao.
    """
    for fii_type in ("tijolo", "papel", "fof", "hibrido"):
        row = {"ticker": "XPTO11", "tipo": fii_type,
               "manager": "61809182000130"}
        assert dimension_is_applicable(row, "manager") is True, fii_type



def test_cnpj_sem_zero_a_esquerda_consolida_o_mesmo_emissor():
    # brapi grava 2773542000122; CVM grava 02773542000122. É a mesma
    # securitizadora e o teto de emissor precisa enxergar uma só.
    row = {"issuers": {"2773542000122": 0.12, "02.773.542/0001-22": 0.08}}
    assert normalized_dimension_mapping(row, "issuer") == {
        "02773542000122": pytest.approx(0.20),
    }


def test_cpf_e_nome_nao_sao_preenchidos_como_cnpj():
    row = {"debtors": {"12345678901": 0.1, " Devedor X ": 0.2}}
    assert normalized_dimension_mapping(row, "debtor") == {
        "12345678901": 0.1, "Devedor X": 0.2,
    }


# --- Setor de inquilino em fundo sem imóvel para renda (validação PIT 6.10.0) --
# O run 105 recusou 16 de 115 carteiras porque fundos de obra, terreno ou
# cotas (TGAR11, MFII11, VGHF11) ficavam na máscara de setor sem que a CVM
# jamais publique inquilino para eles.


def test_fundo_sem_imovel_para_renda_sai_da_mascara_de_setor_e_inquilino():
    obra = {"ticker": "OBRA11", "tipo": "tijolo", "pct_imoveis_renda": .03,
            "regions": {"SP": 1.0}}

    assert dimension_is_applicable(obra, "sector") is False
    assert dimension_is_applicable(obra, "tenant") is False
    # Região sai da tabela de imóveis, que lista obra e terreno: continua.
    assert dimension_is_applicable(obra, "region") is True


def test_hibrido_de_cotas_sem_imovel_para_renda_sai_da_mascara_de_setor():
    """Sem ``pct_imoveis`` no PIT o híbrido era aplicável por cautela."""
    cotas = {"ticker": "COTA11", "tipo": "hibrido", "pct_imoveis_renda": 0.0}

    assert dimension_is_applicable(cotas, "sector") is False
    # A regra nova não toca as dimensões de crédito.
    assert dimension_is_applicable(cotas, "issuer") is True


def test_setor_observado_mantem_o_fundo_na_mascara():
    """Fração baixa com setor declarado: o dado existe e conta como cobertura."""
    row = {"ticker": "SPE11", "tipo": "tijolo", "pct_imoveis_renda": .05,
           "sector": "Logística"}

    assert dimension_is_applicable(row, "sector") is True


@pytest.mark.parametrize("valor", [None, .20, .95])
def test_fracao_ausente_ou_material_preserva_a_aplicabilidade(valor):
    row = {"ticker": "RENT11", "tipo": "tijolo"}
    if valor is not None:
        row["pct_imoveis_renda"] = valor

    assert dimension_is_applicable(row, "sector") is True
    assert dimension_is_applicable(row, "tenant") is True


def test_cobertura_de_setor_na_carteira_e_no_diagnostico_e_a_mesma():
    """Os dois caminhos que medem cobertura usam a mesma regra."""
    from core.fii_portfolio_v4 import _dimension_observation_masks

    pool = [
        {"ticker": "LOGI11", "tipo": "tijolo", "pct_imoveis_renda": .9,
         "sector": "Logística"},
        {"ticker": "OBRA11", "tipo": "hibrido", "pct_imoveis_renda": .02},
        {"ticker": "PAPR11", "tipo": "papel", "issuers": {"A": 1.0}},
    ]
    known, applicable = _dimension_observation_masks(pool, "sector")
    resumo = summarize_lookthrough_coverage(pool)["dimensions"]["sector"]

    assert known.tolist() == [1.0, 0.0, 0.0]
    assert applicable.tolist() == [1.0, 0.0, 0.0]
    assert resumo["coverage"] == known.sum() / applicable.sum() == 1.0

    # Sem a fração observada, o mesmo fundo de obra volta a derrubar a
    # cobertura para 0,5 -- o formato exato das recusas do run 105.
    sem_fracao = [dict(row) for row in pool]
    sem_fracao[1].pop("pct_imoveis_renda")
    known, applicable = _dimension_observation_masks(sem_fracao, "sector")
    assert known.sum() / applicable.sum() == .5


def _exp(kind: str, name: str, weight: float = 1.0) -> dict:
    return {"exposure_type": kind, "exposure_name": name, "exposure_weight": weight}


def test_segmento_cadastral_so_preenche_setor_que_falta():
    """`segment` (cadastro do informe mensal) e reserva do `sector` (inquilinos).

    O setor por inquilino e o que a metodologia mede; o cadastral e um rotulo
    unico declarado pelo administrador. Quando os dois existem, vale o
    inquilino, mesmo que o cadastral seja mais recente.
    """
    from core.fii_lookthrough import apply_exposures

    com_inquilino: dict = {"ticker": "A11"}
    apply_exposures(com_inquilino, [
        _exp("sector", "Varejo", .6), _exp("sector", "Logística", .4),
        _exp("segment", "Lajes Corporativas"),
    ])
    assert com_inquilino["sector"] == "Varejo"
    assert com_inquilino["segment"] == "Lajes Corporativas"

    sem_inquilino: dict = {"ticker": "B11"}
    apply_exposures(sem_inquilino, [
        _exp("segment", "Lajes Corporativas"),
        _exp("issuer", "Emissor A", .7), _exp("issuer", "Emissor B", .3),
    ])
    assert sem_inquilino["sector"] == "Lajes Corporativas"
    assert sem_inquilino["issuers"] == {"Emissor A": .7, "Emissor B": .3}


def test_segmento_cadastral_nao_sobrescreve_setor_ja_conhecido_na_linha():
    """No estado corrente a linha ja chega com o setor do cadastro da brapi."""
    from core.fii_lookthrough import apply_exposures

    row: dict = {"ticker": "C11", "sector": "Shoppings"}
    apply_exposures(row, [_exp("segment", "Lajes Corporativas")])
    assert row["sector"] == "Shoppings"
