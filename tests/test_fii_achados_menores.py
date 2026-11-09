"""Achado 9 da revisão da Seleção de FIIs: três correções pequenas.

1. P/VP da carteira é média harmônica, não aritmética.
2. A nota de cessão do piso de cardinalidade nomeia o estouro de tempo do MILP
   em vez de culpar bandas e tetos.
3. O manifesto da metodologia descreve o encolhimento ao neutro do A-106.
"""
import pytest
import scipy.optimize

from core.fii_methodology import MacroScenario, methodology_manifest
from core.fii_portfolio_v4 import PortfolioPolicy, optimize_diligence_portfolio
from core.fii_presentation import pvp_da_carteira

# ── 1. P/VP harmônico ────────────────────────────────────────────────────────

def test_pvp_da_carteira_e_harmonico():
    # 50/50 em P/VP 0,5 e 1,5: o aritmético dava 1,0; a carteira compra
    # patrimônio com 25% de desconto.
    assert pvp_da_carteira([(0.5, .5), (1.5, .5)]) == pytest.approx(0.75)


def test_pvp_igual_em_todos_e_o_proprio_pvp():
    assert pvp_da_carteira([(0.9, .2), (0.9, .3), (0.9, .5)]) == pytest.approx(0.9)


@pytest.mark.parametrize("invalido", [None, 0.0, -1.0, float("nan"), "abc"])
def test_pvp_ausente_ou_invalido_sai_e_o_peso_renormaliza(invalido):
    # Antes, `pvp or 0` no Dashboard Geral puxava o agregado para baixo.
    assert pvp_da_carteira([(invalido, .5), (1.2, .5)]) == pytest.approx(1.2)


def test_sem_pvp_medido_e_none_e_nunca_zero():
    assert pvp_da_carteira([(None, .5), (0, .5)]) is None
    assert pvp_da_carteira([]) is None


# ── 2. Estouro de tempo do MILP ──────────────────────────────────────────────

def _candidato(i: int) -> dict:
    regioes = ("Norte", "Nordeste", "Centro-Oeste", "Sudeste", "Sul")
    return {"ticker": f"F{i:03d}11", "tipo": "tijolo", "type_score": 80 - i,
            "confidence": .9, "coverage": .95, "publication_status": "validated",
            "dy_12m": .10, "liquidez_diaria": 3_000_000,
            "manager": f"gestor-{i}", "sector": f"setor-{i}",
            "tenants": {f"locatario-{i}": 1.0}, "regions": {regioes[i % 5]: 1.0}}


def _cenario_que_cede_o_piso():
    # Mesmo arranjo de test_fii_portfolio_v4: 6 líquidos e 8 ilíquidos, com
    # orçamento de ilíquidos que só comporta 5 -- o piso de 12 é inviável.
    linhas = [_candidato(i) for i in range(14)]
    for linha in linhas[6:]:
        linha["liquidez_diaria"] = 0
    politica = PortfolioPolicy(max_assets=12, min_assets=12, min_distinct_types=1,
                               max_single_type=1.0, max_asset=.2)
    return linhas, MacroScenario(selic=11, ipca=4, selic_change_12m=-2.5), politica


def _nota_do_piso(resultado):
    return next(n for n in resultado.get("viability_notes") or []
                if "piso de cardinalidade cedido" in n)


def test_piso_cedido_por_inviabilidade_culpa_as_restricoes():
    linhas, cenario, politica = _cenario_que_cede_o_piso()
    nota = _nota_do_piso(optimize_diligence_portfolio(linhas, cenario, policy=politica))

    assert "bandas, tetos de concentração" in nota
    assert "limite de" not in nota


def test_piso_cedido_por_estouro_de_tempo_diz_que_foi_o_tempo(monkeypatch):
    real = scipy.optimize.milp

    def milp_que_estoura(*args, **kwargs):
        # A mesma resolução, mas o solver "desiste" por tempo onde provaria
        # a inviabilidade: status 1, success False -- o sinal que o scipy dá
        # quando `time_limit` estoura.
        resultado = real(*args, **kwargs)
        if not resultado.success:
            resultado.status = 1
        return resultado

    monkeypatch.setattr(scipy.optimize, "milp", milp_que_estoura)
    linhas, cenario, politica = _cenario_que_cede_o_piso()
    resultado = optimize_diligence_portfolio(linhas, cenario, policy=politica)
    nota = _nota_do_piso(resultado)

    assert resultado["items"]
    assert "esgotou o limite de 20 s" in nota
    assert "bandas, tetos de concentração" not in nota


# ── 3. Manifesto ─────────────────────────────────────────────────────────────

def test_manifesto_descreve_o_encolhimento_ao_neutro():
    politica = methodology_manifest()["missing_data_policy"]

    assert "never_zero_or_neutral" not in politica
    assert "neutral_50" in politica and "never_zero" in politica
