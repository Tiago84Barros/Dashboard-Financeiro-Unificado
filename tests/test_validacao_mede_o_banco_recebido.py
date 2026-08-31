# -*- coding: utf-8 -*-
"""Um relatório, três módulos, um banco só.

`confianca_secao` recebe um `engine` e o repassa a `validacao_b3(engine=...)`.
`validacao_fii()` e `validacao_us()` não tinham por onde recebê-lo: abriam
`core.database.get_engine()` por conta própria. O resultado é um relatório em
que a linha da B3 fala do banco que quem chamou escolheu e as linhas de FII e
EUA falam do banco de produção — sem que nada no texto avise.

Isso não é zelo de teste. É a mesma família de
"medir a fonte que a decisão lê": o número sai certo para cada consulta
isolada e errado como comparação, que é a única coisa que a seção de rigor
existe para fazer. `tests/conftest.py` já documentava a assimetria e a
contornava com patch em `get_engine` — contornar o defeito é conviver com ele.

O teste não verifica resultado: verifica PROCEDÊNCIA. Um engine sentinela que
grita quando alguém abre conexão nele prova qual dos dois bancos cada portão
consultou.
"""
from __future__ import annotations

import pytest

import core.validacao_motor as vm


class _EngineSentinela:
    """Engine falso: registra quem o usou e falha como uma fonte fora do ar.

    Falhar é de propósito — todo portão do motor trata fonte inalcançável como
    "não apurado", então o teste isola a pergunta "chegou até aqui?" da
    pergunta "o que respondeu?".
    """

    def __init__(self) -> None:
        self.usos = 0

    def connect(self):
        self.usos += 1
        raise RuntimeError("engine sentinela: fonte indisponivel de proposito")


@pytest.fixture()
def proibir_producao(monkeypatch):
    """Qualquer uso de `get_engine()` vira falha explícita, não silêncio."""
    chamadas: list[int] = []

    def _proibido():
        chamadas.append(1)
        raise AssertionError(
            "validacao abriu o banco de producao em vez de usar o engine "
            "recebido")

    monkeypatch.setattr("core.database.get_engine", _proibido)
    return chamadas


def test_validacao_us_usa_o_engine_recebido(proibir_producao):
    e = _EngineSentinela()
    estado = vm.validacao_us(history_available=True, engine=e)
    assert e.usos > 0, "o portao de deslistadas nao consultou o engine recebido"
    assert estado.aprovada is True


def test_validacao_fii_usa_o_engine_recebido(proibir_producao):
    e = _EngineSentinela()
    vm.validacao_fii(engine=e)
    assert e.usos > 0, "o portao de saidas nao consultou o engine recebido"


def test_comparacao_de_rigor_propaga_para_os_tres(proibir_producao):
    """A comparação é o consumidor que mais depende de fonte única."""
    e = _EngineSentinela()
    linhas = vm.comparacao_de_rigor(engine=e)
    assert len(linhas) == 3
    assert e.usos > 0


def test_sem_engine_continua_lendo_a_producao(monkeypatch):
    """A assinatura ganha um parâmetro; o comportamento padrão não muda."""
    e = _EngineSentinela()
    monkeypatch.setattr("core.database.get_engine", lambda: e)
    vm.validacao_us(history_available=True)
    assert e.usos > 0
