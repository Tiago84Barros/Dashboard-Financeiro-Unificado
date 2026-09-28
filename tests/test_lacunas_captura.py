"""Adaptadores: excecao na fronteira de rota e limitacoes declaradas no render."""
import json
from dataclasses import dataclass

import pytest

from core.lacunas import destino, registrar_excecao, registrar_limitacoes, registro


@pytest.fixture
def arquivo(tmp_path, monkeypatch):
    caminho = tmp_path / "eventos.jsonl"
    monkeypatch.setattr(destino, "ARQUIVO_LOCAL", caminho)
    monkeypatch.setenv("LACUNAS_DESTINO", "local")
    registro._limpar_vistas()
    yield caminho
    registro._limpar_vistas()


def _eventos(caminho):
    if not caminho.exists():
        return []
    return [json.loads(linha) for linha in caminho.read_text(encoding="utf-8").splitlines()]


def _funcao_que_quebra():
    raise KeyError("postgresql://u:senha@host/db coluna_secreta")


def _capturar():
    try:
        _funcao_que_quebra()
    except KeyError as exc:
        return exc


# ── registrar_excecao ────────────────────────────────────────────────────────

def test_excecao_vira_tipo_e_frame_mais_interno_sem_linha(arquivo):
    registrar_excecao(_capturar(), rota="FIIs")
    (ev,) = _eventos(arquivo)
    assert ev["fonte"] == "excecao"
    assert ev["codigo"] == "KeyError"
    assert ev["modulo"] == "tests/test_lacunas_captura.py:_funcao_que_quebra"
    assert "rota FIIs" in ev["mensagem"]


def test_mensagem_da_excecao_nunca_sai(arquivo):
    registrar_excecao(_capturar())
    texto = arquivo.read_text(encoding="utf-8")
    assert "senha" not in texto
    assert "coluna_secreta" not in texto


def test_mesma_excecao_em_linha_diferente_e_a_mesma_lacuna(arquivo):
    registrar_excecao(_capturar())
    registrar_excecao(_capturar())
    assert len(_eventos(arquivo)) == 1


def test_excecao_fora_do_projeto(arquivo):
    registrar_excecao(ValueError("x"))  # sem traceback
    (ev,) = _eventos(arquivo)
    assert ev["modulo"] == "fora do projeto"


def test_falha_interna_nao_propaga(arquivo, monkeypatch):
    import core.erro_diagnostico as ed
    monkeypatch.setattr(ed, "identidade_do_erro", lambda exc: 1 / 0)
    registrar_excecao(_capturar())  # nao levanta


# ── registrar_limitacoes ─────────────────────────────────────────────────────

@dataclass
class _Bloco:
    limitacoes: tuple = ()
    alertas: tuple = ()


def test_limitacoes_e_alertas_do_objeto(arquivo):
    registrar_limitacoes(_Bloco(("sem VPA",), ("liquidez baixa",)),
                         modulo="design/x.py:f", entidade="hglg11")
    evs = _eventos(arquivo)
    assert [e["mensagem"] for e in evs] == ["sem VPA", "liquidez baixa"]
    assert {e["fonte"] for e in evs} == {"motor"}
    assert {e["entidade"] for e in evs} == {"HGLG11"}
    assert {e["modulo"] for e in evs} == {"design/x.py:f"}


def test_aceita_sequencia_e_texto(arquivo):
    registrar_limitacoes(["a", "", "a", "b"], modulo="m")
    registrar_limitacoes("c", modulo="m")
    assert [e["mensagem"] for e in _eventos(arquivo)] == ["a", "b", "c"]


def test_numeros_diferentes_sao_a_mesma_lacuna(arquivo):
    registrar_limitacoes(["faltam 12 meses"], modulo="m")
    registrar_limitacoes(["faltam 13 meses"], modulo="m")
    assert len(_eventos(arquivo)) == 1


def test_objeto_vazio_ou_none_nao_grava(arquivo):
    registrar_limitacoes(None, modulo="m")
    registrar_limitacoes(_Bloco(), modulo="m")
    registrar_limitacoes(object(), modulo="m")
    assert _eventos(arquivo) == []
