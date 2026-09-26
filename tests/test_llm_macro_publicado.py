"""Chats de carteira e de ativo leem o macro publicado quando o Docker não responde.

Antes, a conjuntura (``ponte``) e o recorte por ativo (``_local_macro_block``)
pediam só o Docker: em produção todo chat dizia "sem macro" ao lado da tela que
mostrava o ajuste macro calculado com o arquivo publicado.
"""
from __future__ import annotations

from datetime import timedelta

from core import llm_context_ativo as lca
from core.conjuntura import ponte as P
from core.macro_data import database as macro_db
from tests.test_macro_insumos_publicados import _PUBLICADO, _arquivo

_DEPOIS = _PUBLICADO + timedelta(hours=5)


def test_engines_pede_a_fonte_com_fallback(monkeypatch):
    arquivo = _arquivo()
    monkeypatch.setattr(macro_db, "get_macro_source", lambda: arquivo)
    assert P._engines()[0] is arquivo


def test_conjuntura_calcula_impacto_com_o_arquivo():
    ctx = P.carregar(asset_class="b3", ativos={"PETR4": "Petróleo"}, as_of=_DEPOIS,
                     macro_engine=_arquivo())
    assert "macro" in ctx.componentes_disponiveis
    assert ctx.impactos_macro


def test_conjuntura_nomeia_macro_ausente():
    ctx = P.carregar(asset_class="b3", ativos={"PETR4": "Petróleo"}, as_of=_DEPOIS)
    assert any("sem arquivo publicado recente" in x for x in ctx.limitacoes)


def test_bloco_nao_tenta_fechar_o_arquivo(monkeypatch):
    # O arquivo não é engine: ``dispose`` nele derrubava o bloco inteiro.
    monkeypatch.setattr(P, "_engines", lambda: (_arquivo(), None, None))
    texto = P.bloco_para_prompt(asset_class="b3", ativos={"PETR4": "Petróleo"},
                                as_of=_DEPOIS)
    assert "não foi possível montá-lo" not in texto


def test_recorte_por_ativo_usa_o_arquivo(monkeypatch):
    from core.macro_data import portfolio_context as pc

    arquivo = _arquivo()
    real = pc.load_portfolio_macro_snapshot
    # O recorte consulta "agora"; fixa um instante depois da publicação sintética.
    monkeypatch.setattr(pc, "load_portfolio_macro_snapshot",
                        lambda fonte, **k: real(fonte, as_of=_DEPOIS, **k))
    monkeypatch.setattr(macro_db, "get_macro_source", lambda: arquivo)
    texto = lca._local_macro_block("b3", "PETR4", "Petróleo")
    assert texto and "indisponível" not in texto
    assert "PETR4" in texto
    # O cabeçalho diz de onde veio; antes afirmava "Docker local" para o arquivo.
    assert "(Macro publicado em 26/09/2026)" in texto


def test_recorte_por_ativo_nomeia_a_ausencia(monkeypatch):
    monkeypatch.setattr(macro_db, "get_macro_source", lambda: None)
    texto = lca._local_macro_block("b3", "PETR4", "Petróleo")
    assert "indisponível" in texto and "neutra" in texto
