"""Memória do cliente do túnel: resposta boa reaproveitada, falha não repetida.

Um relatório de carteira com N empresas monta o contexto N+1 vezes: uma por
empresa e uma para o consolidado. Sem memória, eram N+1 leituras pelo túnel com
o PC ligado e N+1 timeouts com ele desligado.

O que se prende:

* cada ticker vai ao túnel uma vez por corte; a carteira reaproveita as empresas;
* corte diferente não reaproveita — o carimbo "Corte:" do prompt não pode mentir;
* ticker sem notícia também é resposta guardada;
* falha de quem está fora do ar pausa as tentativas e diz há quanto tempo falhou;
* erro 4xx não pausa nada: é defeito do pedido, não serviço caído;
* sem configuração continua ``None``, sem memória no meio;
* o relatório inteiro usa um só corte.

Nada aqui sai para a rede: ``_buscar`` e o relógio são dublês.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

import core.armazem_remoto as ar

CORTE = datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc)


class _Relogio:
    def __init__(self) -> None:
        self.agora = 1000.0

    def __call__(self) -> float:
        return self.agora


@pytest.fixture
def relogio(monkeypatch):
    r = _Relogio()
    monkeypatch.setattr(ar, "_relogio", r)
    return r


@pytest.fixture
def tunel(monkeypatch, relogio):
    """Túnel configurado com servidor dublê que registra cada pedido."""
    pedidos: list[tuple[str, dict]] = []
    estado: dict = {"falha": None}

    def buscar(url, token, caminho, params):
        pedidos.append((caminho, dict(params or {})))
        if estado["falha"] is not None:
            raise estado["falha"]
        if caminho == "/noticias/ativos":
            tickers = params["tickers"].split(",")
            return {"linhas": [
                {"simbolo": tk, "titulo": f"{tk}-{n}"}
                for tk in tickers if tk != "SEMNOTICIA" for n in (1, 2)]}
        if caminho == "/noticias/recentes":
            return {"itens": [{"titulo": "manchete"}]}
        return {"fatos": [{"serie": "selic"}]}

    monkeypatch.setattr(ar, "_config", lambda: ("https://tunel.test", "tok"))
    monkeypatch.setattr(ar, "_buscar", buscar)
    return pedidos, estado


def _tickers_pedidos(pedidos) -> list[list[str]]:
    return [p["tickers"].split(",") for c, p in pedidos if c == "/noticias/ativos"]


def test_carteira_reaproveita_o_que_as_empresas_trouxeram(tunel):
    pedidos, _ = tunel
    for tk in ("PETR4", "VALE3"):
        ar.noticias_por_ativo([tk], as_of=CORTE, janela_dias=7)
    linhas = ar.noticias_por_ativo(["PETR4", "VALE3", "ITUB4"],
                                   as_of=CORTE, janela_dias=7)
    # Só o ticker novo foi ao túnel na leitura da carteira.
    assert _tickers_pedidos(pedidos) == [["PETR4"], ["VALE3"], ["ITUB4"]]
    assert [linha["titulo"] for linha in linhas] == [
        "PETR4-1", "PETR4-2", "VALE3-1", "VALE3-2", "ITUB4-1", "ITUB4-2"]


def test_ticker_sem_noticia_tambem_fica_guardado(tunel):
    pedidos, _ = tunel
    assert ar.noticias_por_ativo(["SEMNOTICIA"], as_of=CORTE, janela_dias=7) == []
    assert ar.noticias_por_ativo(["semnoticia"], as_of=CORTE, janela_dias=7) == []
    assert len(_tickers_pedidos(pedidos)) == 1


def test_corte_ou_janela_diferente_nao_reaproveita(tunel):
    pedidos, _ = tunel
    ar.noticias_por_ativo(["PETR4"], as_of=CORTE, janela_dias=7)
    ar.noticias_por_ativo(["PETR4"], as_of=CORTE.replace(minute=1), janela_dias=7)
    ar.noticias_por_ativo(["PETR4"], as_of=CORTE, janela_dias=14)
    assert len(_tickers_pedidos(pedidos)) == 3


def test_resposta_vence(tunel, relogio):
    pedidos, _ = tunel
    ar.noticias_por_ativo(["PETR4"], as_of=CORTE, janela_dias=7)
    ar.noticias_recentes(150, 3)
    relogio.agora += ar.VALIDADE_S
    ar.noticias_por_ativo(["PETR4"], as_of=CORTE, janela_dias=7)
    ar.noticias_recentes(150, 3)
    assert len(pedidos) == 4


def test_quem_recebe_nao_altera_a_memoria(tunel):
    ar.noticias_por_ativo(["PETR4"], as_of=CORTE, janela_dias=7)[0]["titulo"] = "X"
    ar.noticias_recentes(150, 3)[0]["titulo"] = "X"
    assert ar.noticias_por_ativo(["PETR4"], as_of=CORTE, janela_dias=7)[0]["titulo"] == "PETR4-1"
    assert ar.noticias_recentes(150, 3)[0]["titulo"] == "manchete"


def test_macro_e_manchetes_guardados_por_parametro(tunel):
    pedidos, _ = tunel
    ar.macro_recente()
    ar.macro_recente()
    ar.noticias_recentes(150, 3)
    ar.noticias_recentes(150, 7)
    assert [c for c, _ in pedidos] == [
        "/macro/recente", "/noticias/recentes", "/noticias/recentes"]


def test_fora_do_ar_custa_uma_tentativa_por_pausa(tunel, relogio):
    pedidos, estado = tunel
    estado["falha"] = ar.ArmazemRemotoIndisponivel("sem resposta (ConnectTimeout)")
    with pytest.raises(ar.ArmazemRemotoIndisponivel):
        ar.noticias_por_ativo(["PETR4"], as_of=CORTE, janela_dias=7)
    relogio.agora += 20
    # Qualquer rota: o que caiu foi o serviço, não o endpoint.
    for chamada in (lambda: ar.noticias_por_ativo(["VALE3"], as_of=CORTE, janela_dias=7),
                    ar.macro_recente, lambda: ar.noticias_recentes(150, 3)):
        with pytest.raises(ar.ArmazemRemotoIndisponivel) as exc:
            chamada()
        assert "ConnectTimeout" in str(exc.value) and "falhou há 20 s" in str(exc.value)
    assert len(pedidos) == 1

    relogio.agora += ar.PAUSA_APOS_FALHA_S
    estado["falha"] = None
    assert ar.macro_recente() == [{"serie": "selic"}]
    assert len(pedidos) == 2
    # Voltou: a pausa acabou para todos.
    ar.noticias_recentes(150, 3)
    assert len(pedidos) == 3


def test_erro_do_pedido_nao_pausa_o_servico(tunel):
    pedidos, estado = tunel
    estado["falha"] = ar.ArmazemRemotoIndisponivel("HTTP 400: informe tickers",
                                                   fora_do_ar=False)
    with pytest.raises(ar.ArmazemRemotoIndisponivel):
        ar.macro_recente()
    estado["falha"] = None
    assert ar.macro_recente() == [{"serie": "selic"}]
    assert len(pedidos) == 2


@pytest.mark.parametrize("status,pausa", [(530, True), (503, True), (401, False), (400, False)])
def test_status_http_decide_se_pausa(monkeypatch, status, pausa):
    class Resp:
        status_code = status

        def json(self):
            return {"erro": "x"}

    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())
    with pytest.raises(ar.ArmazemRemotoIndisponivel) as exc:
        ar._buscar("https://tunel.test", "tok", "/macro/recente", None)
    assert exc.value.fora_do_ar is pausa


def test_sem_configuracao_continua_none(monkeypatch):
    monkeypatch.setattr(ar, "_config", lambda: ("", ""))

    def nao_chame(*a, **k):
        raise AssertionError("sem configuração não sai para a rede")

    monkeypatch.setattr(ar, "_buscar", nao_chame)
    assert ar.noticias_por_ativo(["PETR4"], as_of=CORTE, janela_dias=7) is None
    assert ar.macro_recente() is None


def test_relatorio_passa_um_corte_so(monkeypatch):
    import core.conjuntura as conj
    import core.contexto_mercado as cm

    cortes: list = []
    monkeypatch.setattr(conj, "bloco_para_prompt",
                        lambda **k: cortes.append(k["as_of"]) or "B")
    monkeypatch.setattr(cm, "_macro_supabase_cache", lambda: [])
    monkeypatch.setattr(cm, "_macro_local", lambda: [])
    monkeypatch.setattr(cm, "_manchetes_acervo", lambda limite: [])
    cm.conjuntura_da_empresa("b3", "PETR4", "Petróleo", as_of=CORTE)
    cm.conjuntura_da_carteira("b3", [{"ticker": "PETR4"}], as_of=CORTE)
    assert cortes == [CORTE, CORTE]
