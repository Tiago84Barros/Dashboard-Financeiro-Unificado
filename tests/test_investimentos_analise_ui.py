"""Contratos da seção Investimentos: abas Análise, Dashboard e Histórico."""
from __future__ import annotations

import inspect
from datetime import date
from pathlib import Path

import core.proventos as prov
import views.investimentos as inv

_FONTE = (Path(__file__).resolve().parents[1] / "views" / "investimentos.py").read_text(
    encoding="utf-8",
)


# ── Aba Análise · Visão Geral ────────────────────────────────────────────────

def test_visao_geral_virou_subabas():
    corpo = inspect.getsource(inv._tab_analise)
    assert 'vg_resumo, vg_destaques, vg_concentracao = st.tabs([' in corpo
    for sub in ("with vg_resumo:", "with vg_destaques:", "with vg_concentracao:"):
        assert sub in corpo, sub


def test_proventos_por_ativo_e_brinson_sairam():
    corpo = inspect.getsource(inv._tab_analise)
    assert "Proventos por Ativo" not in corpo
    assert "Brinson" not in corpo
    assert "attribution" not in corpo


def test_modulo_de_attribution_foi_removido_do_projeto():
    """Era usado só pela Decomposição de Retorno."""
    raiz = Path(__file__).resolve().parents[1]
    assert not (raiz / "core" / "attribution.py").exists()
    assert "attribution" not in _FONTE


# ── Aba Análise · Stress ─────────────────────────────────────────────────────

def test_stress_apresenta_perda_com_sinal_unico():
    """perda_pct do core é negativo; a tela fala em perda e usa o módulo."""
    corpo = inspect.getsource(inv._tab_analise)
    trecho = corpo[corpo.index("with ts:"):]
    assert 'perda_pct = abs(float(pior.get("perda_pct", 0.0))) * 100' in trecho
    assert 'perda_abs = abs(float(pior.get("perda_absoluta", 0.0)))' in trecho


def test_stress_expoe_a_perda_por_classe():
    """por_classe já vinha do core e a tela descartava."""
    trecho = inspect.getsource(inv._tab_analise)
    trecho = trecho[trecho.index("with ts:"):]
    assert 'pior.get("por_classe")' in trecho
    assert "Contribuição" in trecho


def test_stress_ordena_do_pior_para_o_menor():
    trecho = inspect.getsource(inv._tab_analise)
    trecho = trecho[trecho.index("with ts:"):]
    assert 'ordenados = sorted(resultados, key=lambda r: r["perda_pct"])' in trecho


# ── Aba Dashboard · Dependências macro ───────────────────────────────────────

def test_faixas_de_sensibilidade():
    assert inv._faixa_sensibilidade(85)[0] == "Alta"
    assert inv._faixa_sensibilidade(55)[0] == "Moderada"
    assert inv._faixa_sensibilidade(20)[0] == "Baixa"


def test_dependencias_macro_declaram_que_nao_somam_cem():
    corpo = inspect.getsource(inv._tab_dashboard)
    assert "não somam 100%" in corpo
    assert "escala própria" in corpo


def test_grafico_macro_usa_escala_fixa_de_zero_a_cem():
    corpo = inspect.getsource(inv._fig_dependencias_macro)
    assert '"range": [0, 128]' in corpo
    assert "não é fatia da carteira" in corpo


def test_sensibilidade_da_classe_reflete_o_coeficiente():
    """A sensibilidade exibida é o coeficiente fixo da classe, em 0–100."""
    assert inv._sensibilidade_da_classe("Ações BR", "Bolsa Brasil") == 85
    assert inv._sensibilidade_da_classe("Tesouro Direto", "Inflação / IPCA") == 65
    assert inv._sensibilidade_da_classe("Ações BR", "fator inexistente") == 0.0


def test_contribuicao_dos_ativos_soma_a_exposicao_do_fator():
    """A âncora que a tela promete ao usuário tem de ser verdadeira."""
    posicoes = [
        {"ticker": "AAAA3", "nome": "A", "classe": "Ações BR",
         "pct_carteira": 60.0, "valor_mercado": 600.0},
        {"ticker": "TSELIC2029", "nome": "T", "classe": "Tesouro Direto",
         "pct_carteira": 40.0, "valor_mercado": 400.0},
    ]
    por_classe = [
        {"nome": "Ações BR", "pct_carteira": 60.0},
        {"nome": "Tesouro Direto", "pct_carteira": 40.0},
    ]
    df = inv._calc_dependencias_macro_ativos(posicoes)
    deps = {d["fator"]: d["exposicao"] for d in inv._calc_dependencias_macro(por_classe)}
    for fator in inv._MACRO_FATORES:
        assert abs(df[fator].sum() - deps[fator]) < 0.05, fator


def test_contribuicao_e_peso_vezes_sensibilidade():
    posicoes = [{"ticker": "AAAA3", "nome": "A", "classe": "Ações BR",
                 "pct_carteira": 5.0, "valor_mercado": 500.0}]
    df = inv._calc_dependencias_macro_ativos(posicoes)
    sens = inv._sensibilidade_da_classe("Ações BR", "Bolsa Brasil") / 100
    assert abs(float(df["Bolsa Brasil"].iloc[0]) - 5.0 * sens) < 1e-6


# ── Aba Histórico · proventos ────────────────────────────────────────────────

def _evento(ano: int, mes: int, total: float) -> dict:
    return {"ticker": "AAAA3", "nome": "A", "classe": "Ações BR", "cor": "#fff",
            "tipo": "dividend", "label_tipo": "Dividendo", "amount_per_unit": 1.0,
            "quantity": total, "total_amount": total, "ex_date": None,
            "payment_date": date(ano, mes, 10)}


def test_historico_anual_cobre_todos_os_anos():
    """Regressão: o gráfico anual mostrava só 2025-2026 com base desde 2019."""
    eventos = [_evento(ano, mes, 100.0)
               for ano in range(2019, 2027) for mes in (3, 9)]
    anual = prov._historico_anual(eventos)
    assert [linha["ano"] for linha in anual] == list(range(2019, 2027))
    assert all(linha["total"] == 200.0 for linha in anual)
    assert all(linha["num_eventos"] == 2 for linha in anual)


def test_historico_mensal_segue_cortado_em_doze():
    """O corte é correto para o gráfico mensal — e é a causa do bug anual."""
    eventos = [_evento(ano, mes, 10.0)
               for ano in range(2019, 2027) for mes in range(1, 13)]
    assert len(prov._historico_mensal(eventos)) == 12
    assert len(prov._historico_anual(eventos)) == 8


def test_dicionario_de_proventos_expoe_a_serie_anual():
    dados = prov._montar_dict([_evento(2019, 5, 50.0), _evento(2026, 5, 70.0)],
                              date(2026, 8, 4))
    assert [linha["ano"] for linha in dados["historico_anual"]] == [2019, 2026]
    assert dados["historico_anual"][0]["label"] == "2019"


def test_view_do_historico_usa_a_serie_anual_completa():
    corpo = inspect.getsource(inv._tab_historico)
    assert 'hist_anual = proventos.get("historico_anual", [])' in corpo
    assert 'serie_prov = hist_prov if visao_prov == "Mensal" else hist_anual' in corpo


def test_historico_anual_vazio_nao_quebra():
    assert prov._historico_anual([]) == []
    assert prov._historico_anual([{"payment_date": None, "total_amount": 10.0}]) == []
