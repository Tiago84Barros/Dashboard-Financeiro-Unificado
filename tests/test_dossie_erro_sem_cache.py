"""Dossiê que falha: sem cache do erro e sem o texto da exceção.

``build_dossie`` (B3) devolvia o dicionário de erro de dentro do
``st.cache_data(ttl=3600)``: um soluço do banco virava uma hora de ativo sem
dossiê. E o ``erro`` era ``str(exc)[:300]`` nos dois mercados -- texto que vai
para o prompt da LLM, para o resumo do parecer de reserva (tela) e para a aba
Restrições, e que num erro de conexão traz host e usuário do banco. Nada aqui
toca banco: as leituras são trocadas por funções sintéticas.
"""
from __future__ import annotations

import pytest

import core.dossie_b3 as dossie
import core.us_dossie as ud

_SEGREDO = ('connection to server at "db.exemplo.supabase.co" failed: '
            'password authentication failed for user "postgres.abc"')


@pytest.fixture
def leituras(monkeypatch):
    """Leituras sintéticas; ``_series_anuais`` falha nas ``falhas`` primeiras."""
    estado = {"chamadas": 0, "falhas": 0}

    def _serie(tk, *a, **k):
        estado["chamadas"] += 1
        if estado["chamadas"] <= estado["falhas"]:
            raise RuntimeError(_SEGREDO)
        return []

    monkeypatch.setattr(dossie, "_series_anuais", _serie)
    for nome, valor in {"_trimestres": {}, "_precos": {}, "_dividendos": {},
                        "_metricas_snapshot": {}, "_eventos_societarios": {"n_docs": 0},
                        "_market_cap": None, "_valuation": {}, "_ident": {},
                        "_sensibilidade_juros": {}, "_checks": [],
                        "_atualidade": {}}.items():
        monkeypatch.setattr(dossie, nome, lambda *a, _v=valor, **k: _v)
    dossie.build_dossie.clear()
    yield estado
    dossie.build_dossie.clear()


def test_falha_nao_fica_no_cache(leituras):
    leituras["falhas"] = 1
    primeiro = dossie.build_dossie("TEST3")
    assert primeiro["erro"] == dossie.DOSSIE_INDISPONIVEL
    assert primeiro["erro_tipo"] == "RuntimeError"

    segundo = dossie.build_dossie("TEST3")
    assert "erro" not in segundo
    assert leituras["chamadas"] == 2  # tentou o banco de novo

    dossie.build_dossie("test3.sa")
    assert leituras["chamadas"] == 2  # o acerto, esse sim, fica no cache


def test_texto_da_excecao_nao_sai_do_log(leituras):
    leituras["falhas"] = 1
    d = dossie.build_dossie("TEST3")
    assert "supabase" not in repr(d) and "postgres" not in repr(d)
    assert "supabase" not in dossie.dossie_to_text(d)

    parecer, _ = dossie.gerar_parecer_empresa("TEST3", dossie=d)
    assert "supabase" not in repr(parecer)
    assert dossie.DOSSIE_INDISPONIVEL in parecer["resumo"]


def test_eua_tambem_nao_carrega_a_excecao(monkeypatch):
    import core.us_read as ur

    def _falha(sym):
        raise RuntimeError(_SEGREDO)

    monkeypatch.setattr(ur, "load_company_bundle", _falha)
    d = ud.build_dossie("aapl")
    assert d["symbol"] == "AAPL" and d["erro_tipo"] == "RuntimeError"
    assert "supabase" not in repr(d)
    assert "supabase" not in ud.dossie_to_text(d)


def test_contexto_do_parecer_nomeia_a_falha_sem_o_texto(monkeypatch):
    import core.contexto_mercado as cm
    import core.llm_context_b3 as lcb

    def _falha(*a, **k):
        raise RuntimeError(_SEGREDO)

    monkeypatch.setattr(cm, "bloco_contexto_mercado", _falha)
    monkeypatch.setattr(lcb, "get_warehouse_detail_context", _falha)
    texto = dossie.contexto_mercado_para_parecer("TEST3")
    assert "CONTEXTO DE MERCADO: falha ao montar (RuntimeError)" in texto
    assert "DETALHE DO ARMAZÉM: falha ao montar (RuntimeError)" in texto
    assert "supabase" not in texto and "postgres" not in texto
