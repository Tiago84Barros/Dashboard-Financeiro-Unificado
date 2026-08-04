"""Contratos de interface das abas Empresas por Setor e Análise Avançada."""
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


# ── Análise Avançada ─────────────────────────────────────────────────────────

def test_aviso_de_ferramenta_educacional_foi_removido():
    for trecho in ("Ferramenta de análise educacional",
                   "Ferramenta de Análise Educacional",
                   "não constitui consultoria de valores mobiliários"):
        assert trecho not in _FONTE


def test_analise_avancada_so_calcula_apos_confirmacao():
    corpo = inspect.getsource(b3._tab_avancada)
    porta = corpo.index('key="b3_av_run"')
    # O portão vem depois dos filtros e antes de qualquer carga pesada.
    assert corpo.index('key="b3_av_setor"') < porta
    assert corpo.index('key="b3_av_cheapness"') < porta
    assert porta < corpo.index("load_multiplos_todos(")
    assert porta < corpo.index("_score_universo(")
    # Sem confirmação a função retorna antes de calcular.
    assert 'if _executada != _assinatura_filtros:' in corpo
    assert corpo.index('if _executada != _assinatura_filtros:') < corpo.index(
        "load_multiplos_todos(")


def test_mudar_filtro_invalida_o_resultado_ja_calculado():
    corpo = inspect.getsource(b3._tab_avancada)
    assinatura = corpo[corpo.index("_assinatura_filtros = _stable_signature({"):]
    assinatura = assinatura[:assinatura.index("})")]
    # Todo filtro que muda o ranking precisa entrar na assinatura.
    for campo in ("sel_set", "sel_sub", "sel_seg", "sel_perf", "liq_min_rs",
                  "usar_pesos_setor", "usar_auto_calib", "usar_fama_macbeth",
                  "fm_alpha", "pesos_usuario_raw", "cheapness_weight_av"):
        assert campo in assinatura, campo


def test_empresas_reprovadas_nao_sao_listadas():
    corpo = inspect.getsource(b3._tab_avancada)
    assert "Empresas excluídas por completude de dados" not in corpo
    assert "excluidas_dados" not in corpo
    # Os cards do universo mostram só quem pontuou.
    assert "tks_sem_mult" not in corpo
    assert "show_tks = df_scored[\"Ticker\"].tolist()" in corpo


def test_indicadores_opcionais_nascem_colapsados():
    for funcao in (b3._render_governo_e_resiliencia, b3._render_classe_mais_liquida):
        corpo = inspect.getsource(funcao)
        assert "st.expander(" in corpo, funcao.__name__
        assert "expanded=False" in corpo, funcao.__name__
        # _sec_hdr renderiza título sempre visível — é o que se está trocando.
        assert "_sec_hdr(" not in corpo, funcao.__name__
