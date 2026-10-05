"""Item 12 da auditoria do app4 (04/10/2026): FII-02, FII-03 e FII-07.

- FII-02: o selo "validado" aparecia sem o IC do excesso sobre o IFIX, que no
  run 90 (v6.10.0) vai de −0,127 a +0,380 p.p./mês. O piso no limite inferior
  é AVISO, não bloqueio — o portão não muda e o run 90 continua aprovado.
- FII-03: 4 dos 6 tetos de look-through ficavam inativos em silêncio por
  cobertura abaixo de 80%; e o teto `max_manager` usa o CNPJ do ADMINISTRADOR.
- FII-07: a tela dizia "v6.7" com a metodologia em 6.10.0 e o modelo
  integrado em 6.8.0.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import views.fiis as fiis
from core.fii_methodology import METHODOLOGY_VERSION, methodology_manifest
from core.fii_portfolio_v4 import LIMITS, ROTULO_DIMENSAO, tetos_inativos
from core.fii_validation import (
    PISO_IC_EXCESSO,
    leitura_do_excesso,
    texto_do_excesso,
)
from core.validacao_motor import _vantagem_fii

_RAIZ = Path(__file__).resolve().parents[1]

# Números do run 90 no armazém (market.fii_validation_runs, 70 períodos).
_RUN_90 = {"backtest": {
    "periods": 70, "mean_excess": 0.001356,
    "excess_bootstrap": {"n": 70, "mean": 0.001356,
                         "lower": -0.001269, "upper": 0.003798},
}}


def _run(lower, upper, mean=0.002):
    return {"backtest": {"periods": 70, "mean_excess": mean,
                         "excess_bootstrap": {"n": 70, "mean": mean,
                                              "lower": lower, "upper": upper}}}


# ── FII-02: excesso e seu IC ─────────────────────────────────────────────────

def test_run_90_tem_excesso_nao_significativo():
    leitura = leitura_do_excesso(_RUN_90)
    assert leitura["disponivel"] is True
    assert leitura["significativo"] is False
    assert leitura["periodos"] == 70
    assert leitura["piso"] == PISO_IC_EXCESSO == 0.0
    texto = texto_do_excesso(leitura)
    assert "+0,136 p.p./mês" in texto
    assert "−0,127 a +0,380" in texto
    assert texto.endswith("excesso não significativo")


def test_ic_inteiro_acima_do_piso_e_significativo():
    leitura = leitura_do_excesso(_run(0.0012, 0.0045))
    assert leitura["significativo"] is True
    assert texto_do_excesso(leitura).endswith("— significativo")


def test_aceita_o_backtest_direto_ou_o_metrics_inteiro():
    assert (leitura_do_excesso(_RUN_90["backtest"])
            == leitura_do_excesso(_RUN_90))


@pytest.mark.parametrize("metrics", [
    None, {}, {"backtest": {}},
    _run(float("nan"), float("nan")),
    {"backtest": {"excess_bootstrap": {"lower": None, "upper": 0.01}}},
])
def test_ausencia_nao_vira_nao_significativo(metrics):
    """'Não significativo' é afirmação sobre um número; sem número, indisponível."""
    leitura = leitura_do_excesso(metrics)
    assert leitura["disponivel"] is False
    assert leitura["significativo"] is False
    assert texto_do_excesso(leitura) == "IC do excesso sobre o IFIX indisponível neste run"


def test_card_do_protocolo_mostra_o_ic_e_o_aviso_ao_lado_do_selo():
    card = fiis._card_do_protocolo_pit("passed", _RUN_90)
    assert "Aprovado · excesso não significativo" in card
    assert "−0,127 a +0,380" in card
    # Verde é "sem ressalva" nesta tela; a ressalva pinta o card de âmbar.
    assert "var(--app-primary)" not in card
    assert card.count("<div") == card.count("</div>")


def test_card_do_protocolo_com_excesso_significativo_continua_verde():
    card = fiis._card_do_protocolo_pit("passed", _run(0.0012, 0.0045))
    assert "não significativo" not in card
    assert "— significativo" in card
    assert "var(--app-primary)" in card


def test_piso_e_aviso_nao_bloqueio():
    """O portão de validação não ganhou o piso: o run 90 segue aprovado e a
    vitrine de FIIs (destravada no PR #486) segue publicando."""
    fonte = (_RAIZ / "core" / "fii_validation.py").read_text(encoding="utf-8")
    corpo = fonte.split("def validate_methodology", 1)[1]
    assert "PISO_IC_EXCESSO" not in corpo
    assert "leitura_do_excesso" not in corpo


def test_metadado_do_cabecalho_resume_o_ic():
    assert fiis._metadado_do_excesso({"metrics": _RUN_90}) == (
        "IC 95% -0,13 a +0,38 p.p./mês · não significativo")
    assert fiis._metadado_do_excesso(
        {"metrics": _run(0.0012, 0.0045)}) == "IC 95% +0,12 a +0,45 p.p./mês"
    assert fiis._metadado_do_excesso({"metrics": {}}) == "IC indisponível"
    assert fiis._metadado_do_excesso({}) == "IC indisponível"


def test_validacao_do_motor_usa_o_mesmo_piso(monkeypatch):
    """Guarda duplicada diverge: mover o piso move o veredito do motor também."""
    assert _vantagem_fii(_run(0.0012, 0.0045)).ok is True
    monkeypatch.setattr("core.fii_validation.PISO_IC_EXCESSO", 0.002)
    assert _vantagem_fii(_run(0.0012, 0.0045)).ok is False


# ── FII-03: tetos inativos e o rótulo do administrador ──────────────────────

def _resultado(coberturas: dict[str, float], minimo: float = .80) -> dict:
    return {
        "dimension_coverage": {
            dim: {"coverage": cob, "labels": [], "limit": .25}
            for dim, cob in coberturas.items()
        },
        "unresolved_dimensions": sorted(d for d, c in coberturas.items() if c < minimo),
        "policy": {"min_dimension_coverage": minimo},
    }


_COBERTURA_04_10 = {"manager": .97, "sector": 1.0, "tenant": 0.0, "debtor": .084,
                    "issuer": .91, "indexer": .579, "region": .708}


def test_tetos_inativos_lista_os_quatro_da_carteira_de_04_10():
    inativos = tetos_inativos(_resultado(_COBERTURA_04_10))
    assert [t["rotulo"] for t in inativos] == [
        "inquilino", "devedor", "indexador", "região"]
    devedor = inativos[1]
    assert devedor["cobertura"] == pytest.approx(.084)
    assert devedor["minimo"] == pytest.approx(.80)
    assert devedor["teto"] == pytest.approx(.25)


def test_minimo_e_o_da_politica_aplicada_apos_cessao():
    """A cessão de forma baixa o mínimo; o teto que voltou a valer sai da lista."""
    inativos = tetos_inativos(_resultado(_COBERTURA_04_10, minimo=.55))
    assert [t["rotulo"] for t in inativos] == ["inquilino", "devedor"]
    assert all(t["minimo"] == pytest.approx(.55) for t in inativos)


def test_sem_cobertura_registrada_nao_inventa_teto_inativo():
    assert tetos_inativos({}) == []
    assert tetos_inativos(_resultado({d: 1.0 for d in LIMITS})) == []


def test_aviso_da_tela_nomeia_cobertura_minimo_e_teto():
    aviso = fiis._aviso_de_tetos_inativos(tetos_inativos(_resultado(_COBERTURA_04_10)))
    assert aviso.startswith("**4 tetos de concentração inativos**")
    assert "devedor (cobertura 8.4% contra mínimo de 80%; teto de 25% não aplicado)" in aviso
    assert "inquilino (cobertura 0.0%" in aviso
    assert "pode concentrar" in aviso
    assert fiis._aviso_de_tetos_inativos([]) == ""


def test_frase_da_tela_nomeia_so_as_dimensoes():
    """O detalhe (cobertura, mínimo) vai para Restrições; a tela fica com o risco."""
    frase = fiis._aviso_de_concentracao_livre(tetos_inativos(_resultado(_COBERTURA_04_10)))
    assert "inquilino" in frase and "devedor" in frase
    assert "cobertura" not in frase and "%" not in frase
    assert fiis._aviso_de_concentracao_livre([]) == ""


def test_teto_manager_e_do_administrador_nao_da_gestora():
    """As duas fontes da exposição (CVM `CNPJ_Administrador`, brapi
    `administratorCnpj`) só trazem o administrador."""
    assert ROTULO_DIMENSAO["manager"] == "administrador"
    assert set(ROTULO_DIMENSAO) == set(LIMITS)
    doc = (_RAIZ / "views" / "documentacao.py").read_text(encoding="utf-8")
    assert "Teto por gestora" not in doc
    assert "por gestora e por administradora" not in doc


def test_legenda_antiga_com_nomes_internos_saiu_da_tela():
    fonte = (_RAIZ / "views" / "fiis.py").read_text(encoding="utf-8")
    assert "Look-through adicional ainda sem cobertura suficiente" not in fonte
    assert "tetos_inativos(result)" in fonte


# ── FII-07: versão única ─────────────────────────────────────────────────────

def test_tela_nao_tem_versao_fixa_da_metodologia():
    fonte = "\n".join(
        linha for linha in
        (_RAIZ / "views" / "fiis.py").read_text(encoding="utf-8").splitlines()
        if not linha.lstrip().startswith("#")
    )
    assert not re.search(r"""["'][^"'\n]*\bv6\.\d""", fonte)
    # Desde 05/10/2026 a versão sai da tela e vai para o registro do
    # administrador; continua vindo da mesma constante, nunca de literal.
    assert 'f"Seleção Integrada de FIIs · v{METHODOLOGY_VERSION}"' not in fonte
    assert 'detalhe_tecnico(f"Metodologia de FIIs {METHODOLOGY_VERSION}."' in fonte


def test_versoes_do_modelo_e_do_manifesto_seguem_a_metodologia():
    from core.fii_integrated_model import INTEGRATED_MODEL_VERSION
    assert INTEGRATED_MODEL_VERSION == METHODOLOGY_VERSION
    assert methodology_manifest()["integrated_pipeline"]["eligibility_version"] == METHODOLOGY_VERSION


def test_b3_changelog_termina_na_versao_vigente():
    """No B3 a versão já sai de uma constante só; a guarda impede que o
    changelog e o rótulo divirjam numa próxima subida."""
    from core.b3_methodology import SCORE_VERSION
    from views.empresas_b3 import SCORE_VERSION_CHANGELOG
    assert list(SCORE_VERSION_CHANGELOG)[-1] == SCORE_VERSION
