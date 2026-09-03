from datetime import date, datetime, time, timedelta, timezone

import pandas as pd
import pytest

from scripts.verificar_frescor_vitrines import (
    IDADE_MAXIMA,
    VERIFICADORES,
    _conferir_quadro,
    _idade_em_dias,
)

EXIGIDAS = ("dy_12m", "pvp")


def _quadro(**colunas) -> pd.DataFrame:
    return pd.DataFrame(colunas or {"dy_12m": [1.0], "pvp": [0.9]})


def test_erro_de_leitura_reprova_mesmo_com_linhas():
    """O quadro pode vir populado e ainda assim não servir.

    É o caso do PR #190: a vitrine venceu, a leitura devolveu linhas sem as
    colunas de métrica e a tela creditou a falha aos filtros de elegibilidade.
    """
    quadro = _quadro()
    quadro.attrs["load_error"] = "snapshot vencido"
    resultado = _conferir_quadro("fii", quadro, EXIGIDAS, 1)
    assert not resultado["ok"] and "não pôde ser lida" in resultado["detalhe"]


def test_faltar_coluna_de_decisao_reprova():
    resultado = _conferir_quadro("fii", _quadro(dy_12m=[1.0]), EXIGIDAS, 1)
    assert not resultado["ok"] and "pvp" in resultado["detalhe"]


def test_quadro_vazio_reprova():
    assert not _conferir_quadro("fii", pd.DataFrame(), EXIGIDAS, 1)["ok"]


def test_idade_acima_do_limite_do_modulo_reprova():
    assert _conferir_quadro("fii", _quadro(), EXIGIDAS, IDADE_MAXIMA["fii"])["ok"]
    assert not _conferir_quadro("fii", _quadro(), EXIGIDAS,
                                IDADE_MAXIMA["fii"] + 1)["ok"]


def test_idade_desconhecida_nao_reprova_sozinha():
    """Sem carimbo de publicação não dá para afirmar que venceu.

    Reprovar por ausência de medida transformaria "não sei" em "está velho" --
    e o alarme que dispara sem evidência é o primeiro a ser ignorado.
    """
    assert _conferir_quadro("fii", _quadro(), EXIGIDAS, None)["ok"]


@pytest.mark.parametrize("valor", ["ontem", "", None, 42, object()])
def test_carimbo_ilegivel_vira_none_em_vez_de_explodir(valor):
    assert _idade_em_dias(valor) is None


def test_idade_conta_a_partir_de_datas_e_textos():
    """A idade é a mesma a qualquer hora do dia -- ver ``core.frescor``.

    Este teste falhava de forma determinística das 21h à meia-noite no fuso do
    usuário, quando o dia em UTC já tinha virado e o dia local não. O verificador
    de vitrines roda no fim do dia; era exatamente ali que ele reportava tudo um
    dia mais velho do que era.
    """
    ontem = datetime.now(timezone.utc) - timedelta(days=1)
    assert _idade_em_dias(ontem) == 1
    assert _idade_em_dias(ontem.isoformat()) == 1
    assert _idade_em_dias(date.today()) == 0
    assert _idade_em_dias(datetime.combine(date.today(), time(23, 30))) == 0


def test_limite_definido_para_todo_modulo_verificado():
    """Um módulo sem limite levantaria KeyError dentro do verificador."""
    assert set(VERIFICADORES) == set(IDADE_MAXIMA)


def test_fii_tem_o_limite_mais_apertado():
    """A metodologia de FII recusa snapshot com mais de 4 dias.

    Um limite frouxo aqui deixaria passar exatamente a vitrine que a tela já
    vai recusar -- verificação que aprova o que a decisão reprova não verifica
    nada.
    """
    assert IDADE_MAXIMA["fii"] <= 4
    assert IDADE_MAXIMA["fii"] < IDADE_MAXIMA["us"]
