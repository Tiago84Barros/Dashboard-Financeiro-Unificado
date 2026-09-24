"""Veredito do teste histórico dos EUA no topo da aba Criação."""
from views.empresas_americanas import _veredito_validacao_us


def _res(periodos, excesso):
    return {"ok": True, "n_periods": len(periodos),
            "rank_ic": {"mean": .05, "t_stat": 3.7},
            "excess_ann_vs_ew": excesso, "excess_periods": periodos}


def test_excesso_que_cruza_zero_vira_aviso_mesmo_com_ic_significativo():
    nivel, texto = _veredito_validacao_us(
        _res([.10, -.08, .03, -.05, .02, .01], .005))
    assert nivel == "warning"
    assert "não se converteu" in texto
    assert "não foi testada" in texto


def test_excesso_consistente_nao_e_rebaixado():
    nivel, texto = _veredito_validacao_us(
        _res([.04, .05, .03, .045, .05, .04], .042))
    assert nivel == "info"
    assert "não se converteu" not in texto
    # A ressalva sobre a carteira não testada vale nos dois casos.
    assert "não foi testada" in texto


def test_sem_serie_de_excesso_nao_inventa_veredito():
    assert _veredito_validacao_us({"ok": False}) is None
    assert _veredito_validacao_us(_res([.01, .02], .015)) is None
