# -*- coding: utf-8 -*-
"""SCORE-06: coleta truncada nao pode virar onda de encerramentos."""
from __future__ import annotations

from datetime import date

from core.fii_saidas import STATUS_SAIDA, derivar_saidas


def _foto(n, prefixo="FII", extras=()):
    return {f"{prefixo}{i:04d}11" for i in range(n)} | set(extras)


def test_ticker_que_some_entre_duas_fotos_completas_vira_saida():
    fotos = {date(2026, 1, 1): _foto(100, extras=["XPTO11"]),
             date(2026, 2, 1): _foto(100)}
    diag = derivar_saidas(fotos)
    assert diag.ok
    assert [s["ticker"] for s in diag.saidas] == ["XPTO11"]
    assert diag.saidas[0]["active_status"] == STATUS_SAIDA


def test_saida_e_datada_na_foto_em_que_o_ticker_ja_sumiu():
    """A data e quando SOUBEMOS da ausencia, nao quando o fundo acabou."""
    fotos = {date(2026, 1, 1): _foto(50, extras=["XPTO11"]),
             date(2026, 3, 1): _foto(50)}
    saida = derivar_saidas(fotos).saidas[0]
    assert saida["reference_date"] == date(2026, 3, 1)
    assert saida["visto_por_ultimo_em"] == date(2026, 1, 1)


def test_coleta_truncada_nao_gera_nenhuma_saida():
    """O caso real de 28/08/2026: 1.029 tickers e depois 393."""
    fotos = {date(2026, 7, 12): _foto(1029), date(2026, 7, 14): _foto(393)}
    diag = derivar_saidas(fotos)
    assert diag.saidas == []
    assert diag.descartadas and diag.descartadas[0][0] == date(2026, 7, 14)
    assert "sem duas fotos" in diag.motivo


def test_duas_truncadas_seguidas_nao_se_validam():
    """Piso relativo a MAIOR foto, nao a vizinha — senao a segunda truncada passa."""
    fotos = {date(2026, 1, 1): _foto(1000),
             date(2026, 2, 1): _foto(400),
             date(2026, 3, 1): _foto(390)}
    diag = derivar_saidas(fotos)
    assert diag.saidas == []
    assert len(diag.descartadas) == 2


def test_oscilacao_normal_do_universo_continua_comparavel():
    fotos = {date(2026, 1, 1): _foto(1000),
             date(2026, 2, 1): _foto(1000) - {"FII001911"}}
    diag = derivar_saidas(fotos)
    assert [s["ticker"] for s in diag.saidas] == ["FII001911"]


def test_ausencia_observada_e_diferente_de_falta_de_observacao():
    """Sem esta distincao, o portao so pode dar False para sempre."""
    sem_dados = derivar_saidas({})
    assert "nenhum snapshot" in sem_dados.motivo

    uma_foto = derivar_saidas({date(2026, 1, 1): _foto(500)})
    assert "sem duas fotos comparaveis" in uma_foto.motivo.replace("á", "a")

    estaveis = derivar_saidas({date(2026, 1, 1): _foto(500),
                               date(2026, 2, 1): _foto(500)})
    assert "OBSERVADA" in estaveis.motivo


def test_causa_nunca_e_inventada():
    """Sumir da listagem e' delisted; liquidacao/incorporacao exigem documento."""
    fotos = {date(2026, 1, 1): _foto(20, extras=["ZZZZ11"]),
             date(2026, 2, 1): _foto(20)}
    assert {s["active_status"] for s in derivar_saidas(fotos).saidas} == {"delisted"}


def test_foto_truncada_nao_data_saida_mas_desmente_uma():
    """Assimetria: a coleta parcial nao sabe quem falta, entao nao pode declarar
    encerramento -- mas quem ela mostra listado esta vivo. Sem isso, o fundo que
    reaparece numa coleta parcial fica encerrado para sempre."""
    fotos = {
        date(2026, 1, 1): _foto(100, extras=["XPTO11"]),
        date(2026, 2, 1): _foto(100),                 # XPTO11 some
        date(2026, 3, 1): _foto(30, extras=["XPTO11"]),  # parcial: reaparece
    }
    diag = derivar_saidas(fotos)
    assert [d for d, _n, _c in diag.descartadas] == [date(2026, 3, 1)]
    assert diag.saidas == []


def test_visto_por_ultimo_considera_a_foto_truncada():
    """A coleta parcial nao data a saida, mas e prova de vida: usar a foto
    comparavel anterior encurtaria a vida do fundo em um periodo inteiro."""
    fotos = {
        date(2026, 1, 1): _foto(100, extras=["XPTO11"]),
        date(2026, 2, 1): _foto(30, extras=["XPTO11"]),   # parcial, com XPTO11
        date(2026, 3, 1): _foto(100),                     # XPTO11 some
    }
    diag = derivar_saidas(fotos)
    assert [s["ticker"] for s in diag.saidas] == ["XPTO11"]
    s = diag.saidas[0]
    assert s["reference_date"] == date(2026, 3, 1)
    assert s["visto_por_ultimo_em"] == date(2026, 2, 1)
