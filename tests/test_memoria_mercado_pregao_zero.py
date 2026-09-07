"""O pregão zero precisa ser o pregão daquele evento.

``SeriePrecos.indice_do_pregao`` devolve o primeiro pregão *em ou após* a data,
e a convenção está certa: fato de sábado só entra no preço na segunda. O que
faltava era fundo. Sem ele, um evento anterior ao início da série casava com a
primeira linha existente -- a quatorze anos de distância -- e saía daqui um
``EventoMedido`` completo e confiante sobre outro dia.

O número que motivou o corte: das 3.000 datas-ex mais antigas de
``market.dividends`` (1995 a 2005), **2.067** recebiam
``data_pregao_zero = 2010-01-04``, o primeiro pregão de
``market.b3_security_history``. Todas foram "medidas", nenhuma deu erro.
"""
from __future__ import annotations

from datetime import date, timedelta

from core.memoria_mercado import retornos as ret
from tests.apoio_memoria import INICIO, dias_uteis, serie


def _serie_longa():
    return serie("PETR4", dias_uteis(400))


def test_evento_anterior_ao_inicio_da_serie_nao_vira_medicao():
    """O caso real: 14 anos antes do primeiro pregão, e medido assim mesmo."""
    muito_antes = INICIO - timedelta(days=14 * 365)
    assert ret.medir_evento(chave="d", simbolo="PETR4", tipo_evento="dividendo",
                            data_evento=muito_antes,
                            ativo=_serie_longa()) is None


def test_fim_de_semana_e_feriado_emendado_continuam_medindo():
    """O corte não pode desligar a convenção que ele limita.

    A série sintética só tem dias de semana; um evento de sábado casa com a
    segunda-feira seguinte e continua sendo o mesmo evento.
    """
    sabado = INICIO + timedelta(days=5)          # INICIO é uma segunda
    assert sabado.weekday() == 5
    medido = ret.medir_evento(chave="d", simbolo="PETR4",
                              tipo_evento="fato_relevante",
                              data_evento=sabado, ativo=_serie_longa())
    assert medido is not None
    assert medido.data_pregao_zero == sabado + timedelta(days=2)


def test_a_tolerancia_e_declarada_e_o_limite_e_exato():
    """Um dia dentro mede; um dia fora não. O corte é o parâmetro, não o acaso."""
    dias = [INICIO, INICIO + timedelta(days=40)] + dias_uteis(
        200, INICIO + timedelta(days=41))
    calendario = serie("XPTO3", dias)

    dentro = INICIO + timedelta(days=40 - ret.TOLERANCIA_PREGAO_ZERO_DIAS)
    fora = dentro - timedelta(days=1)

    assert ret.medir_evento(chave="a", simbolo="XPTO3", tipo_evento="dividendo",
                            data_evento=dentro, ativo=calendario) is not None
    assert ret.medir_evento(chave="b", simbolo="XPTO3", tipo_evento="dividendo",
                            data_evento=fora, ativo=calendario) is None


def test_evento_posterior_ao_fim_da_serie_continua_sem_medicao():
    """Este caminho já existia e o corte não pode tê-lo mexido."""
    depois = date(2099, 1, 4)
    assert ret.medir_evento(chave="c", simbolo="PETR4", tipo_evento="dividendo",
                            data_evento=depois, ativo=_serie_longa()) is None
