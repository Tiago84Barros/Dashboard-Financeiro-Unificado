"""Inventário de um ativo no armazém: o que ele tem, fonte por fonte.

Nada toca o Postgres: a leitura roda sobre engines de mentira que respondem
por trecho do SQL, e o servidor do túnel sobe em 127.0.0.1 com a leitura
trocada por dublê. O que se prende é o que faria a LLM ler ausência onde há
dado: fonte que falha calando as outras, banco não configurado lido como
"zero linhas", data em texto (o túnel entrega ISO) quebrando o resumo, URL de
conexão vazando no motivo.
"""
from __future__ import annotations

import json
import pathlib
import sys
import threading
from datetime import date, datetime, timezone

import pytest

_RAIZ = pathlib.Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

import core.armazem_inventario as inv  # noqa: E402
import core.armazem_remoto as ar  # noqa: E402
from scripts import servir_armazem_leitura as srv  # noqa: E402

TOKEN = "t" * 40


class _Conexao:
    def __init__(self, respostas):
        self.respostas = respostas

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        texto = str(sql)
        for trecho, linhas in self.respostas.items():
            if trecho in texto:
                if isinstance(linhas, Exception):
                    raise linhas
                saida = linhas
                break
        else:
            saida = []
        assert params["t"] == [t.upper() for t in params["t"]]

        class _R:
            def mappings(self_inner):
                return self_inner

            def all(self_inner):
                return saida

        return _R()


class _Engine:
    def __init__(self, respostas):
        self.respostas = respostas
        self.conexoes = 0

    def connect(self):
        self.conexoes += 1
        return _Conexao(self.respostas)


def _linha(simbolo, n, desde=None, ate=None):
    return {"simbolo": simbolo, "n": n, "desde": desde, "ate": ate}


def _engines_b3():
    mercado = _Engine({
        "market.b3_security_history": [
            _linha("PETR4", 2480, date(2016, 1, 4), date(2026, 11, 7))],
        "market.income_statements": [_linha("PETR4", 15, 2011, 2025)],
        "market.dividends": [_linha("PETR4", 120, date(2012, 3, 1), date(2026, 8, 20))],
        "public.docs_corporativos": RuntimeError(
            'relation "public.docs_corporativos" does not exist\nLINE 1: SELECT ...'),
    })
    memoria = _Engine({"memoria_mercado.eventos_medidos": [
        _linha("PETR4", 6, date(2019, 3, 1), date(2025, 3, 1))]})
    return {"mercado": mercado, "noticias": None, "memoria": memoria}


def test_le_cada_fonte_sozinha_e_marca_quem_falhou():
    engines = _engines_b3()
    lido = inv.ler_inventario(engines, "b3", ["petr4", "xxxx3", "PETR4"])
    assert lido["tickers"] == ["PETR4", "XXXX3"]
    assert lido["ativos"]["PETR4"]["pregao"]["n"] == 2480
    assert lido["ativos"]["PETR4"]["eventos_resultado"]["n"] == 6
    assert lido["ativos"]["XXXX3"] == {}
    assert lido["fontes"]["pregao"]["estado"] == "lida"
    assert lido["fontes"]["noticias"]["estado"] == "sem_banco"
    assert lido["fontes"]["documentos_cvm"]["estado"] == "falha"
    assert "does not exist" in lido["fontes"]["documentos_cvm"]["motivo"]
    # A falha de uma fonte não parou as seguintes do mesmo banco.
    assert lido["fontes"]["proventos"]["estado"] == "lida"
    # Uma conexão por fonte do banco: a consulta que falha não suja a próxima.
    assert engines["mercado"].conexoes == 6


def test_motivo_nao_leva_url_de_conexao_nem_o_sql_apontado():
    motivo = inv._motivo(RuntimeError(
        "could not connect to postgresql://user:senha@host:5433/db\nLINE 2: x"))
    assert "senha" not in motivo and "host" not in motivo
    assert motivo.startswith("RuntimeError: could not connect to")
    assert inv._motivo(RuntimeError('erro LINE 1: SELECT * FROM x')) == "RuntimeError: erro"


def test_classe_desconhecida_e_recusada():
    with pytest.raises(ValueError):
        inv.ler_inventario({}, "cripto", ["BTC"])


def test_resumo_separa_tem_nao_tem_e_nao_consultada():
    lido = inv.ler_inventario(_engines_b3(), "b3", ["PETR4", "XXXX3"])
    texto = inv.resumo_para_prompt(lido, origem="lido direto")
    assert texto.startswith("VARREDURA DO ARMAZÉM LOCAL (lido direto)")
    assert "pregão diário da B3 (COTAHIST): 2.480 linhas, de 04/01/2016 a 07/11/2026" in texto
    assert "demonstrativos anuais e trimestrais: 15 linhas, de 2011 a 2025" in texto
    assert "métricas calculadas: nenhuma linha" in texto
    assert "notícias do acervo local: não consultada (banco desta fonte não configurado" in texto
    assert "documentos CVM/IPE do emissor (texto no RAG): não consultada (RuntimeError" in texto
    assert "CONHECE este ativo (4 de 6 fontes consultadas com linha)" in texto
    # XXXX3: zero nas lidas, mas duas fontes não consultadas -- ausência em aberto.
    bloco_x = texto.split("  XXXX3:")[1]
    assert "nenhuma linha nas 6 fontes consultadas; 2 fonte(s) não consultada(s)" in bloco_x
    assert "NÃO está confirmada" in bloco_x


def test_ausencia_confirmada_so_com_todas_as_fontes_lidas():
    engines = {"mercado": _Engine({}), "noticias": _Engine({}), "memoria": _Engine({})}
    lido = inv.ler_inventario(engines, "b3", ["XXXX3"])
    assert inv.conhece(lido, "XXXX3") is False
    texto = inv.resumo_para_prompt(lido, origem="x")
    assert "NÃO tem nenhuma linha sobre este ativo em nenhuma das 8 fontes consultadas" in texto


def test_nenhuma_fonte_lida_deixa_a_existencia_em_aberto():
    lido = inv.ler_inventario({"mercado": None, "noticias": None, "memoria": None},
                              "us", ["AAPL"])
    assert inv.conhece(lido, "AAPL") is None
    texto = inv.resumo_para_prompt(lido, origem="x")
    assert "nenhuma fonte pôde ser consultada" in texto
    assert "não trate como ausência" in texto
    assert inv.resumo_curto(lido, "AAPL").startswith("armazém local: não consultada:")


def test_resumo_igual_com_datas_em_texto_do_tunel():
    lido = inv.ler_inventario(_engines_b3(), "b3", ["PETR4"])
    lido["ativos"]["PETR4"]["pregao"]["ate"] = datetime(2026, 11, 7, 21, 0, tzinfo=timezone.utc)
    via_tunel = json.loads(json.dumps(lido, default=srv._json_padrao))
    assert (inv.resumo_para_prompt(lido, origem="x")
            == inv.resumo_para_prompt(via_tunel, origem="x"))
    assert inv.resumo_curto(lido, "PETR4") == inv.resumo_curto(via_tunel, "PETR4")


def test_resumo_curto_cabe_numa_linha_e_diz_o_que_falta():
    lido = inv.ler_inventario(_engines_b3(), "b3", ["PETR4"])
    curto = inv.resumo_curto(lido, "PETR4", limite=600)
    assert curto.startswith("armazém local: pregão diário da B3 (COTAHIST) 2.480 até 07/11/2026")
    assert "sem preço histórico do coletor (brapi), métricas calculadas" in curto
    assert ("não consultada: documentos CVM/IPE do emissor (texto no RAG), notícias do "
            "acervo local") in curto
    assert "\n" not in curto
    assert len(inv.resumo_curto(lido, "PETR4")) <= 220
    assert len(inv.resumo_curto(lido, "PETR4", limite=80)) <= 80


def test_sem_tickers_nao_consulta_e_nao_escreve():
    engines = _engines_b3()
    lido = inv.ler_inventario(engines, "fii", [])
    assert engines["mercado"].conexoes == 0
    assert inv.resumo_para_prompt(lido, origem="x") == ""
    assert inv.resumo_para_prompt(None, origem="x") == ""


def test_documentos_cvm_contam_pela_raiz_do_emissor():
    """Os documentos ficam sob UMA classe (PETR3); a análise de PETR4 tem de
    vê-los. Mesma convenção de ``rag_b3._ticker_root``."""
    from core.rag_b3 import _ticker_root

    for tk in ("PETR4", "petr3", "PETR11", "TAEE11", "B3SA3", "XPLG11"):
        assert inv.raiz_emissor(tk) == _ticker_root(tk)

    pedidos = []

    class _Docs(_Engine):
        def connect(self):
            conexao = super().connect()
            execute = conexao.execute

            def _execute(sql, params):
                if "public.docs_corporativos" in str(sql):
                    pedidos.append(list(params["t"]))
                return execute(sql, params)

            conexao.execute = _execute
            return conexao

    mercado = _Docs({"public.docs_corporativos": [
        _linha("PETR", 37, date(2019, 5, 2), date(2026, 10, 30))]})
    lido = inv.ler_inventario({"mercado": mercado, "noticias": None, "memoria": None},
                              "b3", ["PETR4", "PETR3", "VALE3"])
    assert pedidos == [["PETR", "VALE"]]  # a raiz, não a classe
    assert lido["ativos"]["PETR4"]["documentos_cvm"]["n"] == 37
    assert lido["ativos"]["PETR3"]["documentos_cvm"]["n"] == 37
    assert "documentos_cvm" not in lido["ativos"]["VALE3"]
    texto = inv.resumo_para_prompt(lido, origem="x")
    assert "documentos CVM/IPE do emissor (texto no RAG): 37 linhas, de 02/05/2019 a 30/10/2026" in texto


def test_toda_fonte_declara_simbolo_n_desde_e_ate():
    for classe, fontes in inv.FONTES.items():
        assert classe in inv.CLASSES
        for fonte in fontes:
            assert fonte.banco in inv.BANCOS
            for coluna in ("AS simbolo", "AS n", "AS desde", "AS ate"):
                assert coluna in fonte.sql, (classe, fonte.chave, coluna)
            assert "= ANY(:t)" in fonte.sql
            assert "GROUP BY 1" in fonte.sql


# ── rota do túnel e cliente ──────────────────────────────────────────────────

def test_rota_recusa_classe_errada_e_pedido_largo_demais():
    assert srv.rota_ativo_inventario({"classe": ["cripto"], "tickers": ["BTC"]})[0] == 400
    assert srv.rota_ativo_inventario({"classe": ["b3"]})[0] == 400
    muitos = ",".join(f"T{i}" for i in range(inv.TICKERS_MAX + 1))
    assert srv.rota_ativo_inventario({"classe": ["b3"], "tickers": [muitos]})[0] == 400


def test_rota_isola_o_banco_cuja_url_falha(monkeypatch):
    """``_url_eua`` consulta o Docker e pode levantar (serviço subiu antes do
    Docker). Isso não pode virar 503: o cliente pausaria todas as rotas por
    60 s, e as fontes dos outros bancos ainda respondem."""
    recebido = {}

    def _ler(engines, classe, tickers):
        recebido.update(engines=engines)
        return {"classe": classe, "tickers": tickers, "fontes": {}, "ativos": {}}

    def _docker_fora():
        raise RuntimeError("docker inspect falhou")

    monkeypatch.setattr(srv, "_url_eua", _docker_fora)
    monkeypatch.setattr(srv, "_url_noticias", lambda: "postgresql://n")
    monkeypatch.setattr(srv, "_url_memoria", lambda: "")
    monkeypatch.setattr(srv, "engine_leitura", lambda url: f"engine:{url}")
    monkeypatch.setattr(inv, "ler_inventario", _ler)
    status, _ = srv.rota_ativo_inventario({"classe": ["b3"], "tickers": ["PETR4"]})
    assert status == 200
    assert recebido["engines"] == {"mercado": None, "noticias": "engine:postgresql://n",
                                   "memoria": None}
    # Todos os bancos sem URL ou com URL em falha: aí sim 503.
    monkeypatch.setattr(srv, "_url_noticias", lambda: "")
    assert srv.rota_ativo_inventario({"classe": ["b3"], "tickers": ["PETR4"]})[0] == 503


def test_predicados_das_series_usam_a_coluna_nua():
    """``upper(ticker) = ANY(...)`` descartaria o índice e varreria
    ``market_us.prices_daily`` inteira a cada varredura."""
    for classe, fontes in inv.FONTES.items():
        for fonte in fontes:
            if fonte.chave == "noticias":
                continue  # jsonb expandido: não há índice a preservar
            assert "upper(" not in fonte.sql, (classe, fonte.chave)


def test_rota_sem_armazem_responde_503(monkeypatch):
    for nome in ("_url_eua", "_url_noticias", "_url_memoria"):
        monkeypatch.setattr(srv, nome, lambda: "")
    status, corpo = srv.rota_ativo_inventario({"classe": ["us"], "tickers": ["AAPL"]})
    assert status == 503 and "não configurado" in corpo["erro"]


def test_rota_passa_so_os_bancos_configurados(monkeypatch):
    recebido = {}

    def _ler(engines, classe, tickers):
        recebido.update(engines=engines, classe=classe, tickers=tickers)
        return {"classe": classe, "tickers": tickers, "fontes": {}, "ativos": {}}

    monkeypatch.setattr(srv, "_url_eua", lambda: "postgresql://m")
    monkeypatch.setattr(srv, "_url_noticias", lambda: "")
    monkeypatch.setattr(srv, "_url_memoria", lambda: "")
    monkeypatch.setattr(srv, "engine_leitura", lambda url: f"engine:{url}")
    monkeypatch.setattr(inv, "ler_inventario", _ler)
    status, corpo = srv.rota_ativo_inventario({"classe": ["B3"], "tickers": ["petr4,VALE3"]})
    assert status == 200 and corpo["inventario"]["tickers"] == ["PETR4", "VALE3"]
    assert recebido["engines"] == {"mercado": "engine:postgresql://m",
                                   "noticias": None, "memoria": None}
    assert recebido["classe"] == "b3"


@pytest.fixture
def tunel(monkeypatch):
    lido = inv.ler_inventario(_engines_b3(), "b3", ["PETR4"])
    monkeypatch.setitem(srv.ROTAS, "/ativo/inventario",
                        lambda p: (200, {"inventario": lido, "pedido": p}))
    http = srv.ThreadingHTTPServer(("127.0.0.1", 0), srv.fabricar_handler(TOKEN))
    threading.Thread(target=http.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{http.server_address[1]}"
    monkeypatch.setattr(ar, "_config", lambda: (url, TOKEN))
    ar._limpar_memoria()
    yield url
    http.shutdown()
    http.server_close()


def test_cliente_traz_o_inventario_pelo_tunel(tunel):
    lido = ar.inventario_ativos("b3", ["petr4"])
    assert lido["ativos"]["PETR4"]["pregao"]["n"] == 2480
    assert lido["ativos"]["PETR4"]["pregao"]["ate"] == "2026-11-07"
    texto = inv.resumo_para_prompt(lido, origem="lido pelo túnel")
    assert "2.480 linhas, de 04/01/2016 a 07/11/2026" in texto


def test_cliente_sem_configuracao_devolve_none(monkeypatch):
    monkeypatch.setattr(ar, "_config", lambda: ("", ""))
    assert ar.inventario_ativos("b3", ["PETR4"]) is None
    assert ar.inventario_ativos("b3", []) == {}
