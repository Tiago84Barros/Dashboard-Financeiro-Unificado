"""Serviço só-leitura do armazém e o cliente que a produção usa.

Sobe o servidor de verdade em 127.0.0.1 (o guarda de rede do conftest deixa
loopback passar) com as rotas trocadas por dublês: nada toca o Postgres. O que
se prende é o que abriria o armazém ou faria a falha parecer ausência de dado:
token ignorado, rota de escrita, página de erro lida como lista vazia.
"""
from __future__ import annotations

import pathlib
import sys
import threading
from datetime import datetime, timezone

import pytest

_RAIZ = pathlib.Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

import core.armazem_remoto as ar  # noqa: E402
from scripts import servir_armazem_leitura as srv  # noqa: E402

TOKEN = "t" * 40


@pytest.fixture
def servidor(monkeypatch):
    monkeypatch.setitem(srv.ROTAS, "/noticias/recentes", lambda p: (200, {
        "itens": [{"titulo": "Copom", "limite": p.get("limite"),
                   "publicado_em": datetime(2026, 9, 24, 13, 5, tzinfo=timezone.utc)}]}))
    monkeypatch.setitem(srv.ROTAS, "/macro/recente",
                        lambda _p: (200, {"fatos": [{"indicator": "IBC-Br"}]}))

    def _quebra(_p):
        raise RuntimeError("banco fora\nsegunda linha")

    monkeypatch.setitem(srv.ROTAS, "/saude", _quebra)
    http = srv.ThreadingHTTPServer(("127.0.0.1", 0), srv.fabricar_handler(TOKEN))
    threading.Thread(target=http.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{http.server_address[1]}"
    monkeypatch.setattr(ar, "_config", lambda: (url, TOKEN))
    yield url
    http.shutdown()
    http.server_close()


def test_cliente_le_noticias_e_macro(servidor):
    itens = ar.noticias_recentes(7, dias=2)
    assert itens[0]["titulo"] == "Copom"
    assert itens[0]["limite"] == ["7"]
    assert itens[0]["publicado_em"].startswith("2026-09-24T13:05")
    assert ar.macro_recente() == [{"indicator": "IBC-Br"}]


def test_token_errado_e_recusado(servidor, monkeypatch):
    monkeypatch.setattr(ar, "_config", lambda: (servidor, "x" * 40))
    with pytest.raises(ar.ArmazemRemotoIndisponivel, match="HTTP 401"):
        ar.noticias_recentes()


def test_sem_token_nenhum_e_recusado(servidor):
    import requests

    assert requests.get(f"{servidor}/macro/recente", timeout=5).status_code == 401


def test_escrita_nao_existe(servidor):
    import requests

    resp = requests.post(f"{servidor}/noticias/recentes", timeout=5,
                         headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 405


def test_falha_da_rota_vira_503_nomeado(servidor):
    with pytest.raises(ar.ArmazemRemotoIndisponivel, match="HTTP 503: RuntimeError: banco fora$"):
        ar._ler("/saude")


def test_rota_inexistente(servidor):
    with pytest.raises(ar.ArmazemRemotoIndisponivel, match="HTTP 404"):
        ar._ler("/espelho/finance_transactions")


def test_nao_configurado_e_none_e_nao_chama_rede(monkeypatch):
    monkeypatch.setattr(ar, "_config", lambda: ("", ""))
    assert ar.configurado() is False
    assert ar.noticias_recentes() is None
    assert ar.macro_recente() is None


def test_porta_fechada_e_indisponivel_e_nao_vazio(monkeypatch):
    """PC desligado: tem de levantar, nunca devolver lista vazia."""
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]
    monkeypatch.setattr(ar, "_config", lambda: (f"http://127.0.0.1:{porta}", TOKEN))
    with pytest.raises(ar.ArmazemRemotoIndisponivel, match="túnel desligado"):
        ar.noticias_recentes()


def test_resposta_html_do_tunel_nao_vira_lista_vazia(monkeypatch):
    class _Resp:
        status_code = 200

        def json(self):
            raise ValueError("html")

    import requests

    monkeypatch.setattr(ar, "_config", lambda: ("https://x", TOKEN))
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())
    with pytest.raises(ar.ArmazemRemotoIndisponivel, match="não é JSON"):
        ar.noticias_recentes()


@pytest.mark.parametrize("cabecalho,esperado", [
    (f"Bearer {TOKEN}", True),
    (f"Bearer {TOKEN}x", False),
    (TOKEN, False),
    (None, False),
    ("Bearer ", False),
])
def test_token_confere(cabecalho, esperado):
    assert srv.token_confere(cabecalho, TOKEN) is esperado


def test_token_vazio_no_servidor_nunca_autoriza():
    assert srv.token_confere("Bearer ", "") is False


def test_parametros_tem_teto():
    assert srv._numero({"limite": ["999999"]}, "limite", 150, 500) == 500
    assert srv._numero({"dias": ["abc"]}, "dias", 3, 30) == 3
    assert srv._numero({"dias": ["nan"]}, "dias", 3, 30) == 3
    assert srv._numero({"dias": ["-4"]}, "dias", 3, 30) == 0


def test_servidor_nao_sobe_com_token_curto(monkeypatch, capsys):
    import core.config as cfg

    monkeypatch.setattr(cfg, "_get_secret", lambda k, d="": "curto")
    assert srv.main([]) == 2
    assert "ARMAZEM_API_TOKEN" in capsys.readouterr().err


def test_sessao_do_banco_e_somente_leitura(monkeypatch):
    capturado = {}

    import sqlalchemy

    monkeypatch.setattr(sqlalchemy, "create_engine",
                        lambda url, **kw: capturado.update(kw) or object())
    monkeypatch.setattr(srv, "_ENGINES", {})
    srv.engine_leitura("postgresql://x/y")
    assert "default_transaction_read_only=on" in capturado["connect_args"]["options"]
