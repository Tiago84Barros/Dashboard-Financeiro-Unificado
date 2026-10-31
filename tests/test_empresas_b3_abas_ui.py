"""Contratos de interface das abas de Empresas B3."""
from __future__ import annotations

import inspect
from pathlib import Path

import views.empresas_b3 as b3

_FONTE = (Path(__file__).resolve().parents[1] / "views" / "empresas_b3.py").read_text(
    encoding="utf-8",
)


# ── Empresas por Setor ───────────────────────────────────────────────────────

def test_aba_empresas_por_setor_nao_tem_busca_por_ticker():
    corpo = inspect.getsource(b3._tab_empresas)
    assert "render_company_search" not in corpo
    assert "b3_busca" not in corpo
    # A navegação continua existindo: o card leva à Análise de Empresa.
    assert "render_sector_grid" in corpo


def test_codigo_da_busca_nao_ficou_orfao_no_modulo():
    assert "render_company_search" not in _FONTE
    assert "filter_market_companies" not in _FONTE
    assert "b3_ticker_sel" in _FONTE   # seleção pelo card segue viva


# ── Fim da Análise Avançada (30/10/2026) ─────────────────────────────────────

def test_aviso_de_ferramenta_educacional_foi_removido():
    for trecho in ("Ferramenta de análise educacional",
                   "Ferramenta de Análise Educacional",
                   "não constitui consultoria de valores mobiliários"):
        assert trecho not in _FONTE


def test_analise_avancada_saiu_da_barra_e_do_modulo():
    from design.market_companies import NAV_ITEMS_B3
    assert [r for _, r in NAV_ITEMS_B3] == [
        "Empresas por Setor", "Análise de Empresa", "Criação de Portfólio",
        "Avaliação de Portfólio", "Carteira 20",
    ]
    assert not hasattr(b3, "_tab_avancada")
    # Nenhum widget da aba sobrou (as chaves dela começavam com b3_av_).
    assert "b3_av_" not in _FONTE


def test_criacao_e_avaliacao_nao_se_apresentam_como_etapas():
    raiz = Path(__file__).resolve().parents[1] / "views"
    for nome in ("portfolio_b3.py", "analise_portfolio_b3.py"):
        texto = (raiz / nome).read_text(encoding="utf-8")
        assert "Etapa " not in texto, nome
        assert " de 3 ·" not in texto, nome
