# -*- coding: utf-8 -*-
"""A convencao de retorno de deslistagem, e o que ela nao pode fazer."""
from __future__ import annotations

import pytest

from core.us_convencao_saida import (
    CENARIO_PADRAO,
    CENARIOS,
    frase_convencao,
    retorno_de_saida,
)
from core.us_saida_causa import ADQUIRIDA, INDEFINIDO, SUMIU


def test_indefinido_sai_da_conta_em_todo_cenario() -> None:
    # A regra que a primeira versao desta medicao quebrou.
    for nome in CENARIOS:
        assert retorno_de_saida(INDEFINIDO, nome) is None


def test_causa_ausente_e_tratada_como_indefinida() -> None:
    # NULL no banco significa "ainda nao perguntei" -- nunca um desfecho.
    for valor in (None, "", "outra_coisa"):
        assert retorno_de_saida(valor) is None


def test_aquisicao_nao_recebe_premio() -> None:
    # De proposito: subestimar o desfecho bom impede que a correcao infle o
    # excesso medido.
    for nome in ("piso", "crsp"):
        assert retorno_de_saida(ADQUIRIDA, nome) == 0.0


def test_falencia_e_uma_banda_e_nao_um_numero() -> None:
    piso = retorno_de_saida(SUMIU, "piso")
    crsp = retorno_de_saida(SUMIU, "crsp")
    assert piso == -1.0 and crsp == -0.30
    assert piso < crsp, "o piso precisa ser o pior dos dois"


def test_descartar_e_uma_escolha_nomeada() -> None:
    assert all(retorno_de_saida(c, "descartar") is None
               for c in (ADQUIRIDA, SUMIU, INDEFINIDO))
    assert "fora" in frase_convencao("descartar")


def test_cenario_desconhecido_falha_alto() -> None:
    with pytest.raises(ValueError):
        retorno_de_saida(SUMIU, "otimista")


def test_frase_e_derivada_da_tabela() -> None:
    f = frase_convencao(CENARIO_PADRAO)
    assert "-30%" in f and "0%" in f and "indefinida excluída" in f
