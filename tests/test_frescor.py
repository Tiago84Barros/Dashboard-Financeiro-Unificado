from datetime import date, datetime, timedelta, timezone

import pytest

from core import frescor
from core.publicacao_agenda import POR_CHAVE


def _dias_atras(n: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=n)


def test_alvo_sai_da_agenda_e_nao_de_numero_repetido():
    """Duas fontes para o mesmo número divergem sem dar erro.

    Se o alvo fosse copiado, mudar a cadência de `us_snapshot` de 7 para 30 dias
    deixaria a tela avisando "vencida" todo dia 11 -- para sempre, sobre uma
    vitrine perfeitamente dentro do prazo que a própria agenda define.
    """
    for modulo, chave in frescor.ALVO_DO_MODULO.items():
        assert frescor.idade_alvo(modulo) == POR_CHAVE[chave].cadencia_dias


def test_todo_modulo_aponta_para_um_alvo_com_cadencia_de_dias():
    """Alvo por versão (`cadencia_dias=None`) rebentaria a soma do limite."""
    for chave in frescor.ALVO_DO_MODULO.values():
        assert POR_CHAVE[chave].cadencia_dias is not None


def test_limite_e_maior_que_o_alvo():
    for modulo in frescor.ALVO_DO_MODULO:
        assert frescor.idade_limite(modulo) > frescor.idade_alvo(modulo)


def test_fii_e_o_mais_apertado_porque_tem_validade_dura_no_codigo():
    """`fii_methodology` recusa snapshot com mais de 4 dias.

    Um limite de tela mais frouxo que o do motor deixaria a tela dizer "está tudo
    bem" sobre a vitrine que o motor já está recusando.
    """
    assert frescor.idade_limite("fii") == 4
    assert frescor.idade_limite("fii") < frescor.idade_limite("us")


@pytest.mark.parametrize("valor", ["ontem", "", None, 42, object(), [1]])
def test_carimbo_ilegivel_vira_none_em_vez_de_zero(valor):
    """Zero afirma frescor; carimbo ilegível não afirma nada.

    Um selo que diz "publicada hoje" porque não conseguiu ler a data é pior do
    que selo nenhum -- ele transforma ausência de medida em prova de atualidade.
    """
    assert frescor.idade_em_dias(valor) is None


def test_idade_aceita_datetime_texto_e_date():
    assert frescor.idade_em_dias(_dias_atras(3)) == 3
    assert frescor.idade_em_dias(_dias_atras(3).isoformat()) == 3
    assert frescor.idade_em_dias(date.today()) == 0
    assert frescor.idade_em_dias(_dias_atras(1).replace(tzinfo=None)) == 1


def test_no_prazo_nao_e_atrasada_nem_vencida():
    s = frescor.selo("us", _dias_atras(frescor.idade_alvo("us")))
    assert not s["atrasada"] and not s["vencida"]


def test_entre_o_alvo_e_o_limite_e_atrasada_mas_ainda_vale():
    s = frescor.selo("us", _dias_atras(frescor.idade_alvo("us") + 1))
    assert s["atrasada"] and not s["vencida"]


def test_acima_do_limite_e_vencida():
    s = frescor.selo("us", _dias_atras(frescor.idade_limite("us") + 1))
    assert s["vencida"]
    assert "indicativo" in s["texto"]


def test_sem_carimbo_nao_afirma_que_esta_velha():
    """"Não sei" e "está velho" são estados diferentes.

    Marcar vencida por ausência de medida transformaria falta de carimbo em
    alarme, e alarme que dispara sem evidência é o primeiro a ser ignorado.
    """
    s = frescor.selo("b3", None)
    assert not s["vencida"] and not s["atrasada"]
    assert s["idade"] is None
    assert "não é possível dizer" in s["texto"]


def test_carimbo_no_futuro_e_denunciado_e_nao_vira_frescor():
    """Foi assim que a idade da B3 mediu -121 dias.

    A coluna `data` do quadro de múltiplos é 31/12 do exercício de referência --
    uma data contábil no futuro durante todo o ano corrente. Lida como carimbo de
    publicação, ela aprovaria qualquer atraso para sempre.
    """
    s = frescor.selo("b3", datetime.now(timezone.utc) + timedelta(days=121))
    assert s["idade"] < 0
    assert not s["vencida"]
    assert "futuro" in s["texto"]
    assert frescor.resumo_curto(s) == "carimbo inválido"


@pytest.mark.parametrize("dias,esperado", [(0, "hoje"), (1, "há 1 dia"), (2, "há 2 dias")])
def test_resumo_curto_no_prazo(dias, esperado):
    assert frescor.resumo_curto(frescor.selo("us", _dias_atras(dias))) == esperado


def test_resumo_curto_marca_vencida_e_sem_carimbo():
    vencida = frescor.selo("us", _dias_atras(frescor.idade_limite("us") + 5))
    assert "(vencida)" in frescor.resumo_curto(vencida)
    assert frescor.resumo_curto(frescor.selo("us", None)) == "sem carimbo"


def test_carimbo_de_modulo_desconhecido_falha_alto():
    """Módulo novo sem fonte de carimbo tem de dar erro, não devolver None.

    `None` silencioso viraria "sem carimbo" na tela -- indistinguível de banco
    fora do ar, e ninguém iria atrás.
    """
    with pytest.raises(KeyError):
        frescor.carimbo_do_modulo("cripto")
