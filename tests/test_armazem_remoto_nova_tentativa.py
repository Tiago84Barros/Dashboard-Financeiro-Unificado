"""Resposta cortada no túnel tenta de novo uma vez antes de virar falha.

Em 20/11/2026 uma resposta da ``/sql`` chegou cortada
(``ChunkedEncodingError``) com o PC, o armazém e o túnel de pé. Sem segunda
tentativa, um corte só punha a B3 inteira na cópia congelada.

O que se prende:

* queda de conexão ou resposta cortada vai ao túnel mais uma vez;
* tempo de leitura esgotado não repete: dobraria a espera da tela;
* duas quedas seguidas viram a falha de sempre, com o número de tentativas;
* resposta HTTP de erro não repete: o servidor respondeu.

Nada sai para a rede: ``requests.post``/``requests.get`` são dublês.
"""
from __future__ import annotations

import pytest
import requests

import core.armazem_remoto as ar


class _Resposta:
    def __init__(self, status: int = 200, corpo: dict | None = None):
        self.status_code = status
        self._corpo = corpo if corpo is not None else {"ok": True}

    def json(self):
        return self._corpo


@pytest.fixture
def rede(monkeypatch):
    """Fila de resultados: exceção levanta, resposta devolve."""
    fila: list = []
    chamadas: list[str] = []
    esperas: list[float] = []

    def _pedido(metodo):
        def _f(url, **kw):
            chamadas.append(metodo)
            item = fila.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return _f

    monkeypatch.setattr(requests, "post", _pedido("post"))
    monkeypatch.setattr(requests, "get", _pedido("get"))
    monkeypatch.setattr(ar, "_esperar", esperas.append)
    return fila, chamadas, esperas


@pytest.mark.parametrize("queda", [
    requests.exceptions.ChunkedEncodingError("Connection broken: IncompleteRead"),
    requests.ConnectionError("Connection aborted"),
])
def test_queda_tenta_de_novo_e_entrega(rede, queda):
    fila, chamadas, esperas = rede
    fila.extend([queda, _Resposta(corpo={"linhas": [1]})])
    corpo = ar._buscar("https://x", "t", "/sql", None, enviar={"sql": "select 1"})
    assert corpo == {"linhas": [1]}
    assert chamadas == ["post", "post"]
    assert esperas == [ar.ESPERA_ENTRE_TENTATIVAS_S]


def test_rota_de_leitura_get_tambem_tenta_de_novo(rede):
    fila, chamadas, _ = rede
    fila.extend([requests.exceptions.ChunkedEncodingError("cortou"), _Resposta()])
    assert ar._buscar("https://x", "t", "/macro", None) == {"ok": True}
    assert chamadas == ["get", "get"]


def test_tempo_de_leitura_esgotado_nao_repete(rede):
    fila, chamadas, esperas = rede
    fila.extend([requests.ReadTimeout("75 s"), _Resposta()])
    with pytest.raises(ar.ArmazemRemotoIndisponivel) as exc:
        ar._buscar("https://x", "t", "/sql", None, enviar={"sql": "select 1"})
    assert chamadas == ["post"] and esperas == []
    assert "ReadTimeout" in str(exc.value) and "tentativas" not in str(exc.value)
    assert exc.value.fora_do_ar


def test_duas_quedas_viram_falha_com_o_numero_de_tentativas(rede):
    fila, chamadas, _ = rede
    corte = requests.exceptions.ChunkedEncodingError("cortou")
    fila.extend([corte, corte, _Resposta()])
    with pytest.raises(ar.ArmazemRemotoIndisponivel) as exc:
        ar._buscar("https://x", "t", "/sql", None, enviar={"sql": "select 1"})
    assert len(chamadas) == ar.TENTATIVAS_REDE == 2
    assert "ChunkedEncodingError, 2 tentativas" in str(exc.value)
    assert exc.value.fora_do_ar


def test_erro_http_nao_repete(rede):
    fila, chamadas, _ = rede
    fila.extend([_Resposta(status=503, corpo={"erro": "fora"}), _Resposta()])
    with pytest.raises(ar.ArmazemRemotoIndisponivel):
        ar._buscar("https://x", "t", "/sql", None, enviar={"sql": "select 1"})
    assert chamadas == ["post"]
