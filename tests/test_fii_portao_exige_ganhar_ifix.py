"""O portão PIT de FIIs exige que a carteira ganhe do IFIX na média.

Decisão do usuário de 19/11/2026. O run 153 (v6.8, 116 períodos) saiu
"passed" com excesso médio de −0,189 p.p./mês, e a vitrine publicou a
carteira-modelo com o selo de aprovada. O portão atestava só o protocolo.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.fii_validation import (
    PISO_EXCESSO_MEDIO,
    PISO_IC_EXCESSO,
    bloqueio_por_excesso,
    leitura_do_excesso,
    validate_methodology,
)

_RAIZ = Path(__file__).resolve().parents[1]


def _run(media: float | None, lower: float = -0.005, upper: float = 0.005) -> dict:
    """Backtest que passa em todos os outros critérios do portão."""
    corpo = {
        "status": "calculated",
        "periods": 116,
        "mean_coverage": 0.95,
        "rank_stability": 0.70,
        "annualized_turnover": 1.2,
        "verified_snapshot_fraction": 0.99,
        "return_observation_coverage": 0.99,
        "constraint_violation_periods": 0,
        "excess_bootstrap": {"lower": lower, "upper": upper, "n": 116},
    }
    if media is not None:
        corpo["mean_excess"] = media
    return corpo


_REGIMES = {"alta": {"periods": 30}, "queda": {"periods": 40}, "lateral": {"periods": 46}}


def _valida(backtest: dict) -> dict:
    return validate_methodology(backtest, _REGIMES)


def test_media_positiva_passa_mesmo_sem_significancia():
    # Retrato do run 90: ganha na média, IC cruza o zero. O piso do IC segue
    # aviso, então isto continua aprovado.
    resultado = _valida(_run(0.00136, lower=-0.00127, upper=0.00380))
    assert resultado["status"] == "passed", resultado["blockers"]


def test_run_153_perde_do_ifix_e_bloqueia():
    resultado = _valida(_run(-0.00189, lower=-0.00460, upper=0.00080))
    assert resultado["status"] == "blocked"
    assert resultado["blockers"] == [
        "carteira não ganha do IFIX: excesso médio líquido de −0,189 p.p./mês "
        "no walk-forward"
    ]


def test_empate_com_o_indice_nao_e_ganhar():
    resultado = _valida(_run(PISO_EXCESSO_MEDIO))
    assert resultado["status"] == "blocked"
    assert any("não ganha do IFIX" in b for b in resultado["blockers"])


def test_sem_media_medida_bloqueia():
    backtest = _run(None)
    backtest["excess_bootstrap"].pop("mean", None)
    resultado = _valida(backtest)
    assert resultado["status"] == "blocked"
    assert any("excesso médio sobre o IFIX indisponível" in b
               for b in resultado["blockers"])


def test_media_cai_para_a_do_bootstrap_quando_falta_a_da_manchete():
    backtest = _run(None)
    backtest["excess_bootstrap"]["mean"] = 0.0009
    assert _valida(backtest)["status"] == "passed"


def test_backtest_nao_calculado_nao_ganha_bloqueador_repetido():
    blockers = validate_methodology({"status": "blocked", "blockers": ["sem dados"]}, {})["blockers"]
    assert not [b for b in blockers if "IFIX" in b]
    assert bloqueio_por_excesso({"status": "blocked"}) == []


def test_portao_e_tela_leem_a_mesma_media():
    """Guarda duplicada diverge: o portão usa `leitura_do_excesso`, a mesma
    função do card e do motor."""
    backtest = _run(-0.0001)
    assert leitura_do_excesso(backtest)["media"] == pytest.approx(-0.0001)
    assert bloqueio_por_excesso(backtest)


def test_piso_do_ic_segue_aviso():
    """O piso de significância (IC inferior > 0) não entrou no portão."""
    fonte = (_RAIZ / "core" / "fii_validation.py").read_text(encoding="utf-8")
    portao = fonte.split("def bloqueio_por_excesso", 1)[1].split("\ndef ", 1)[0]
    corpo = fonte.split("def validate_methodology", 1)[1].split("\ndef ", 1)[0]
    assert "PISO_IC_EXCESSO" not in portao
    assert "PISO_IC_EXCESSO" not in corpo
    assert "PISO_EXCESSO_MEDIO" in portao
    assert "bloqueio_por_excesso" in corpo
    assert PISO_IC_EXCESSO == 0.0


def test_card_diz_o_que_o_selo_atesta():
    import views.fiis as fiis

    novo = fiis._card_do_protocolo_pit("passed", {"backtest": _run(0.00136, -0.00127, 0.0038)})
    assert "excesso médio positivo sobre o IFIX" in novo
    assert "significância não exigida" in novo
    # Certificado 3.5.0 aprovado com média negativa: o card não repete a
    # promessa nova sobre ele.
    velho = fiis._card_do_protocolo_pit("passed", {"backtest": _run(-0.00189, -0.0046, 0.0008)})
    assert "anterior ao portão que exige ganhar do IFIX" in velho
    assert "excesso médio positivo" not in velho
