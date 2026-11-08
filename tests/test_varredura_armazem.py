"""Varredura do armazém antes de afirmar lacuna: o ponto único.

O que se prende: a varredura escolhe direto ou túnel como os leitores de
detalhe; falha e túnel ausente viram texto que diz "não consultado", nunca
vazio nem "não existe"; o resultado é reaproveitado por alguns minutos; e a
lacuna declarada pela LLM leva o que o armazém tem da entidade, sem uma ida
nova ao armazém.
"""
from __future__ import annotations

import pathlib
import sys
from datetime import date

import pytest

_RAIZ = pathlib.Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

import core.armazem_remoto as ar  # noqa: E402
import core.varredura_armazem as var  # noqa: E402


def _inventario(classe, tickers, *, tem=("PETR4",)):
    fontes = {"pregao": {"rotulo": "pregão diário da B3 (COTAHIST)", "estado": "lida"},
              "proventos": {"rotulo": "proventos", "estado": "lida"},
              "noticias": {"rotulo": "notícias do acervo local", "estado": "sem_banco"}}
    ativos = {tk: ({"pregao": {"n": 2480, "desde": date(2016, 1, 4), "ate": date(2026, 11, 7)}}
                   if tk in tem else {}) for tk in tickers}
    return {"classe": classe, "tickers": list(tickers), "fontes": fontes, "ativos": ativos}


class _Engine:
    def __init__(self):
        self.descartada = False

    def dispose(self):
        self.descartada = True


@pytest.fixture(autouse=True)
def _memoria_limpa():
    var._limpar_memoria()
    ar._limpar_memoria()
    yield
    var._limpar_memoria()


def test_le_direto_quando_o_app_aponta_para_o_armazem(monkeypatch):
    engines = {"mercado": _Engine(), "noticias": _Engine(), "memoria": None}
    chamadas = []

    def _ler(eng, classe, tickers):
        chamadas.append((eng, classe, list(tickers)))
        return _inventario(classe, tickers)

    monkeypatch.setattr(var, "_engines_diretas", lambda: engines)
    monkeypatch.setattr("core.armazem_inventario.ler_inventario", _ler)
    texto = var.bloco_varredura("b3", ["petr4", "XXXX3"])
    assert "VARREDURA DO ARMAZÉM LOCAL (lido direto)" in texto
    assert "2.480 linhas, de 04/01/2016 a 07/11/2026" in texto
    assert "XXXX3" in texto and "NÃO está confirmada" in texto
    assert chamadas == [(engines, "b3", ["PETR4", "XXXX3"])]
    # As engines abertas só para a varredura são descartadas; a do app, não.
    assert engines["noticias"].descartada and not engines["mercado"].descartada


def test_reaproveita_o_que_ja_leu_e_so_busca_o_que_falta(monkeypatch):
    pedidos = []

    def _ler(eng, classe, tickers):
        pedidos.append(list(tickers))
        return _inventario(classe, tickers)

    monkeypatch.setattr(var, "_engines_diretas", lambda: {"mercado": _Engine(),
                                                          "noticias": None, "memoria": None})
    monkeypatch.setattr("core.armazem_inventario.ler_inventario", _ler)
    relogio = [1_000.0]
    monkeypatch.setattr(var, "_relogio", lambda: relogio[0])
    var.bloco_varredura("b3", ["PETR4"])
    texto = var.bloco_varredura("b3", ["PETR4", "VALE3"])
    assert pedidos == [["PETR4"], ["VALE3"]]
    assert "  PETR4:" in texto and "  VALE3:" in texto
    relogio[0] += var.VALIDADE_S + 1
    var.bloco_varredura("b3", ["PETR4"])
    assert pedidos[-1] == ["PETR4"]  # venceu: relido


def test_pelo_tunel_quando_o_app_nao_alcanca_o_armazem(monkeypatch):
    monkeypatch.setattr(var, "_engines_diretas",
                        lambda: {"mercado": None, "noticias": None, "memoria": None})
    monkeypatch.setattr(ar, "inventario_ativos",
                        lambda classe, tickers: _inventario(classe, list(tickers)))
    texto = var.bloco_varredura("b3", ["PETR4"])
    assert "(lido pelo túnel)" in texto and "CONHECE este ativo" in texto


def test_tunel_ausente_e_tunel_caido_dizem_nao_consultado(monkeypatch):
    monkeypatch.setattr(var, "_engines_diretas",
                        lambda: {"mercado": None, "noticias": None, "memoria": None})
    monkeypatch.setattr(ar, "inventario_ativos", lambda classe, tickers: None)
    texto = var.bloco_varredura("us", ["AAPL"])
    assert "túnel não configurado" in texto
    assert 'diga "não consultado", não "não existe"' in texto
    assert "AAPL" in texto

    def _cai(classe, tickers):
        raise ar.ArmazemRemotoIndisponivel("sem resposta (ConnectTimeout) — PC ou túnel desligado?")

    monkeypatch.setattr(ar, "inventario_ativos", _cai)
    texto = var.bloco_varredura("us", ["AAPL"])
    assert "não feita nesta resposta (sem resposta (ConnectTimeout)" in texto
    assert "não existe" in texto  # a frase da regra, não uma afirmação de ausência
    assert var.resumo_para_tela("us", "AAPL").startswith("Armazém local não consultado (")


def test_classe_desconhecida_levanta_em_vez_de_calar():
    with pytest.raises(ValueError):
        var.bloco_varredura("cripto", ["BTC"])
    assert var.bloco_varredura("b3", []) == ""


def test_resumo_para_tela_nos_tres_estados(monkeypatch):
    monkeypatch.setattr(var, "_engines_diretas",
                        lambda: {"mercado": None, "noticias": None, "memoria": None})
    monkeypatch.setattr(ar, "inventario_ativos",
                        lambda classe, tickers: _inventario(classe, list(tickers)))
    conhecido = var.resumo_para_tela("b3", "petr4")
    assert conhecido.startswith("Varredura feita: o armazém local conhece este ativo. ")
    assert "pregão diário da B3 (COTAHIST) 2.480 até 07/11/2026" in conhecido
    assert "sem proventos" in conhecido and "não consultada: notícias" in conhecido
    assert conhecido.endswith(".")
    desconhecido = var.resumo_para_tela("b3", "XXXX3")
    assert desconhecido == ("Varredura feita: o armazém local também não tem nenhuma linha "
                            "sobre XXXX3 nas fontes consultadas.")
    monkeypatch.setattr(ar, "inventario_ativos", lambda classe, tickers: None)
    var._limpar_memoria()
    assert "túnel não configurado" in var.resumo_para_tela("b3", "PETR4")
    assert var.resumo_para_tela("b3", "") == ""


def test_nota_para_lacuna_so_le_a_memoria(monkeypatch):
    def _nao_devia(*a, **k):
        pytest.fail("a nota da lacuna não vai ao armazém")

    monkeypatch.setattr(var, "_engines_diretas", _nao_devia)
    assert var.nota_para_lacuna("PETR4") == ""
    assert var.nota_para_lacuna(None) == ""

    monkeypatch.setattr(var, "_engines_diretas",
                        lambda: {"mercado": None, "noticias": None, "memoria": None})
    monkeypatch.setattr(ar, "inventario_ativos",
                        lambda classe, tickers: _inventario(classe, list(tickers)))
    var.bloco_varredura("b3", ["PETR4", "XXXX3"])
    monkeypatch.setattr(var, "_engines_diretas", _nao_devia)
    nota = var.nota_para_lacuna("petr4.sa")
    assert nota.startswith("armazém local: pregão diário da B3 (COTAHIST) 2.480 até 07/11/2026")
    assert len(nota) <= 160
    assert var.nota_para_lacuna("XXXX3").startswith("armazém local: sem pregão")
    assert var.nota_para_lacuna("VALE3") == ""  # não varrido


def test_lacuna_da_llm_leva_a_varredura_da_entidade(monkeypatch, tmp_path):
    import json

    from core.lacunas import destino, registro
    from core.lacunas.llm import processar_resposta

    caminho = tmp_path / "eventos.jsonl"
    monkeypatch.setattr(destino, "ARQUIVO_LOCAL", caminho)
    monkeypatch.setenv("LACUNAS_DESTINO", "local")
    registro._limpar_vistas()
    monkeypatch.setattr(var, "_engines_diretas",
                        lambda: {"mercado": None, "noticias": None, "memoria": None})
    monkeypatch.setattr(ar, "inventario_ativos",
                        lambda classe, tickers: _inventario(classe, list(tickers)))
    var.bloco_varredura("b3", ["PETR4"])
    resposta = ('ok\n<lacunas>\n{"codigo": "b3.pregao", "mensagem": "sem liquidez diária", '
                '"entidade": "PETR4"}\n{"codigo": "x", "mensagem": "sem Focus"}\n</lacunas>')
    assert processar_resposta(resposta, modulo="views.empresas_b3") == "ok"
    eventos = [json.loads(l) for l in caminho.read_text(encoding="utf-8").splitlines()]
    por_codigo = {e["codigo"]: e["mensagem"] for e in eventos}
    assert por_codigo["llm.b3.pregao"].startswith("sem liquidez diária [armazém local: pregão")
    assert por_codigo["llm.x"] == "sem Focus"  # sem entidade, sem nota
    registro._limpar_vistas()
