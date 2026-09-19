"""O bloqueador de viabilidade precisa nomear a causa dominante.

Um periodo em que o otimizador nao entregou nada e um periodo em que ele
entregou carteira e a publicacao foi recusada contam igual contra a fracao
viavel -- mas mandam procurar defeito em motores diferentes. Dizer
"otimizador inviavel" quando a causa foi o portao de transparencia manda
quem le auditar o codigo errado.
"""
from __future__ import annotations

from core.fii_validation import ValidationThresholds, validate_methodology


def _valida(backtest: dict) -> list[str]:
    return validate_methodology(backtest, {}, thresholds=ValidationThresholds())["blockers"]


def _base(**extra) -> dict:
    corpo = {
        "status": "calculated",
        "optimizer_feasible_fraction": 0.89,
        "optimizer_attempted_periods": 118,
        "optimizer_successful_periods": 105,
    }
    corpo.update(extra)
    return corpo


def test_recusa_de_publicacao_dominante_nomeia_a_cobertura() -> None:
    recusados = [f"2017-{mes:02d}-01" for mes in range(5, 11)] + [
        "2018-05-01", "2018-06-01", "2018-07-01"]
    blockers = _valida(_base(publication_refused_periods=recusados))
    alvo = [b for b in blockers if "sem carteira publicável" in b]
    assert alvo, blockers
    assert "9 deles com carteira montada" in alvo[0]
    assert "2017-05-01 a 2018-07-01" in alvo[0]
    assert not [b for b in blockers if b.startswith("otimizador robusto inviável")]


def test_sem_recusa_a_mensagem_continua_culpando_o_otimizador() -> None:
    blockers = _valida(_base(publication_refused_periods=[]))
    assert "otimizador robusto inviável em períodos históricos demais" in blockers
    assert not [b for b in blockers if "sem carteira publicável" in b]


def test_recusa_minoritaria_nao_rouba_a_mensagem() -> None:
    # 2 recusas contra 11 periodos em que o otimizador nao entregou nada: a
    # causa dominante continua sendo o motor, e a mensagem tem de dizer isso.
    blockers = _valida(_base(publication_refused_periods=["2017-05-01", "2017-06-01"]))
    assert "otimizador robusto inviável em períodos históricos demais" in blockers


def test_fracao_acima_do_piso_nao_bloqueia() -> None:
    blockers = _valida(_base(optimizer_feasible_fraction=0.99,
                             optimizer_successful_periods=117,
                             publication_refused_periods=["2017-05-01"]))
    assert not [b for b in blockers if "publicável" in b or "otimizador robusto" in b]
