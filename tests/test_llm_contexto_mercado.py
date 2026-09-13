"""O contexto do chat precisa carregar o que o banco já tem.

O defeito medido, na tela: perguntado sobre a carteira, o app respondia que "o
contexto fornecido contém estritamente dados quantitativos e operacionais da sua
carteira, não contendo notícias recentes, dados macroeconômicos ou fatos
relevantes do cenário brasileiro atual". A frase descrevia o prompt com
precisão. O banco, não: ``core.noticias`` publica noticiário, ``public.macro``
guarda a série de Selic/IPCA/câmbio e ``core.conjuntura`` junta os dois com
procedência.

Estes testes cobram o que separa "a LLM não tem o dado" de "a LLM tem o dado e
disse que não tem": todo construtor de contexto que fala de mercado precisa
levar o bloco, e todo bloco precisa dizer em voz alta quando a fonte faltou —
bloco que some ensina o modelo a supor cenário calmo.
"""
from __future__ import annotations

import pytest

from core import llm_context_mercado as M

# ── macro do país ────────────────────────────────────────────────────────────

def test_serie_anual_vira_texto_com_as_tres_taxas():
    texto = M.bloco_macro_pais({
        2024: {"selic": 0.1075, "ipca": 0.0462, "cambio": 5.44},
        2025: {"selic": 0.1475, "ipca": 0.0483, "cambio": 5.88},
    })
    assert "10,75%" not in texto  # o bloco usa ponto decimal, como o resto
    assert "10.75%" in texto and "14.75%" in texto
    assert "2024" in texto and "2025" in texto


def test_a_variacao_ano_a_ano_sai_nomeada():
    """Dizer que a Selic "subiu" é o que transforma dois números em cenário."""
    texto = M.bloco_macro_pais({
        2024: {"selic": 0.1075}, 2025: {"selic": 0.1475},
    })
    assert "subiu" in texto
    assert "10.75% → 14.75%" in texto


def test_as_duas_convencoes_de_unidade_dao_o_mesmo_numero():
    """``public.macro`` grava ora 0.1075 ora 10.75 e só a Selic é normalizada.

    Se o bloco não tratasse as duas, o mesmo país apareceria com juro de 10,75%
    num ano e de 1075% no seguinte.
    """
    fracao = M.bloco_macro_pais({2025: {"ipca": 0.0483}})
    inteiro = M.bloco_macro_pais({2025: {"ipca": 4.83}})
    assert "4.83%" in fracao and "4.83%" in inteiro


def test_banco_vazio_vira_frase_e_nao_bloco_vazio():
    """Bloco ausente é indistinguível de país sem risco macro."""
    texto = M.bloco_macro_pais({})
    assert texto
    assert "não trate isso como cenário estável" in texto.lower()


def test_falha_de_leitura_aparece_como_falha(monkeypatch):
    import core.b3_db as b3_db

    def _explode():
        raise RuntimeError("could not connect to server")

    monkeypatch.setattr(b3_db, "load_macro_history", _explode)
    texto = M.bloco_macro_pais()
    assert "indisponível" in texto
    assert "could not connect" in texto


def test_o_grao_anual_viaja_junto():
    """Sem esta ressalva a Selic de 2026 chega ao modelo como a taxa de ontem."""
    texto = M.bloco_macro_pais({2026: {"selic": 0.15}})
    assert "anual" in texto.lower()
    assert "não a apresente como a taxa de hoje" in texto.lower()


# ── roteamento de classe ─────────────────────────────────────────────────────

@pytest.mark.parametrize("classe,esperado", [
    ("acoes", "b3"), ("fiis", "fii"), ("exterior", "us"),
])
def test_a_classe_da_tela_vira_a_classe_da_conjuntura(classe, esperado,
                                                     monkeypatch):
    visto = {}

    def _falso(*, asset_class, ativos, estruturais=None, max_itens=12, **_k):
        visto["classe"] = asset_class
        return "BLOCO"

    import core.conjuntura as C
    monkeypatch.setattr(C, "bloco_para_prompt", _falso)
    assert M.bloco_conjuntura(classe=classe, ativos={"X": "Setor"}) == "BLOCO"
    assert visto["classe"] == esperado


def test_tesouro_nao_e_forcado_em_nenhuma_classe_de_renda_variavel():
    """Título público não tem setor nem emissor com noticiário próprio.

    Roteá-lo para "b3" produziria cobertura macro fantasma: o motor mediria um
    setor que a posição não tem.
    """
    assert M.bloco_conjuntura(classe="tesouro", ativos={"LFT": ""}) == ""


def test_carteira_sem_ativo_nao_inventa_bloco():
    assert M.bloco_conjuntura(classe="acoes", ativos={}) == ""


def test_conjuntura_vazia_e_dita_e_nao_omitida(monkeypatch):
    import core.conjuntura as C
    monkeypatch.setattr(C, "bloco_para_prompt", lambda **_k: "")
    texto = M.bloco_conjuntura(classe="acoes", ativos={"PETR4": "Petróleo"})
    assert "ausência de notícia não é notícia neutra" in texto.lower()


def test_conjuntura_que_explode_nao_derruba_o_prompt(monkeypatch):
    import core.conjuntura as C

    def _explode(**_k):
        raise RuntimeError("acervo fora do ar")

    monkeypatch.setattr(C, "bloco_para_prompt", _explode)
    texto = M.bloco_conjuntura(classe="acoes", ativos={"PETR4": "Petróleo"})
    assert "não foi possível montá-lo" in texto
    assert "nem como conjuntura neutra" in texto


# ── os construtores que estavam mudos ────────────────────────────────────────

def test_contexto_da_carteira_passou_a_levar_mercado(monkeypatch):
    """A tela da evidência: era ela que respondia "só dados quantitativos"."""
    import core.llm_context_mercado as mod
    monkeypatch.setattr(mod, "bloco_conjuntura",
                        lambda **_k: "CONTEXTO CONJUNTURAL: PETR4 sob pressão")
    monkeypatch.setattr(mod, "bloco_macro_pais",
                        lambda *_a, **_k: "MACRO DO PAÍS: Selic=15.00%")

    from core.llm_context_carteira import build_carteira_classe_context

    texto = build_carteira_classe_context(
        "acoes",
        [{"ticker": "PETR4", "valor_mercado": 1000.0}],
        db={"linhas": [{"ticker": "PETR4", "setor": "Petróleo"}]},
    )
    assert "CONTEXTO CONJUNTURAL" in texto
    assert "MACRO DO PAÍS" in texto
    assert "fazem parte deste contexto" in texto.lower()


def test_o_setor_da_comparacao_com_o_banco_alimenta_a_conjuntura(monkeypatch):
    """Sem setor não há impacto macro setorial — e o setor já estava na tela."""
    visto = {}
    import core.llm_context_mercado as mod

    def _espiao(*, classe=None, ativos=None, **_k):
        visto.update(ativos or {})
        return ""

    monkeypatch.setattr(mod, "bloco_mercado", _espiao)
    from core.llm_context_carteira import build_carteira_classe_context

    build_carteira_classe_context(
        "acoes",
        [{"ticker": "PETR4", "valor_mercado": 1.0},
         {"ticker": "MGLU3", "valor_mercado": 1.0}],
        db={"linhas": [{"ticker": "PETR4", "setor": "Petróleo"}]},
    )
    assert visto["PETR4"] == "Petróleo"
    # Quem o banco não conhece entra com setor vazio em vez de ficar de fora:
    # sem setor perde-se o macro, ficando de fora perde-se também o noticiário.
    assert visto["MGLU3"] == ""


def test_contexto_financeiro_passou_a_levar_macro_do_pais(monkeypatch):
    import core.llm_context_financeiro as F
    monkeypatch.setattr(F, "bloco_macro_pais",
                        lambda *_a, **_k: "MACRO DO PAÍS: Selic=15.00%")

    texto, _meta = F.build_financas_chat_context(
        user_question="como estou?",
        dados_mes={"receitas": 10000.0, "despesas": 4000.0, "categorias": []},
        historico=[], hist_anual={}, gastos_categoria_anual=[],
        gastos_cartao={}, evolucao={}, investido_mes=3000.0,
        ano_ref=2026, mes_ref=9,
    )
    assert "MACRO DO PAÍS" in texto
    assert "antes de dizer que não há" in texto


def test_o_financeiro_nao_diz_mais_que_o_saldo_subtrai_aporte():
    """A definição antiga contradizia core.fluxo_caixa_mes e ensinava o erro."""
    import core.llm_context_financeiro as F

    texto, _meta = F.build_financas_chat_context(
        user_question="", dados_mes={"receitas": 1.0, "despesas": 0.0,
                                     "categorias": []},
        historico=[], hist_anual={}, gastos_categoria_anual=[],
        gastos_cartao={}, evolucao={}, investido_mes=0.0,
        ano_ref=2026, mes_ref=9,
    )
    assert "já subtrai os investimentos" not in texto
    assert "não subtrai" in texto.lower()


def test_contexto_global_pede_conjuntura_uma_vez_por_classe(monkeypatch):
    """Bancos da B3 e bancos dos EUA não têm a mesma sensibilidade macro.

    Um pedido só, com as três classes misturadas, devolveria o impacto de uma
    delas para todas.
    """
    import pandas as pd

    import core.llm_context_mercado as mod
    pedidos = []

    def _espiao(*, asset_class=None, ativos=None, **_k):
        pedidos.append((asset_class, tuple(sorted(ativos or {}))))
        return f"BLOCO {asset_class}"

    monkeypatch.setattr(mod, "bloco_conjuntura", _espiao)
    monkeypatch.setattr(mod, "bloco_macro_pais", lambda *_a, **_k: "MACRO")

    from core.llm_context_global import build_global_portfolio_context

    df = pd.DataFrame([
        {"symbol": "ITUB4", "asset_class": "b3", "sector": "Bancos",
         "country": "BR", "currency": "BRL",
         "weight_global": 0.5, "weight_class": 1.0, "valor_brl": 100.0},
        {"symbol": "JPM", "asset_class": "us", "sector": "Financials",
         "country": "US", "currency": "USD",
         "weight_global": 0.5, "weight_class": 1.0, "valor_brl": 100.0},
    ])
    texto = build_global_portfolio_context(df)
    assert ("b3", ("ITUB4",)) in pedidos
    assert ("us", ("JPM",)) in pedidos
    assert "BLOCO b3" in texto and "BLOCO us" in texto and "MACRO" in texto


def test_global_sem_posicao_nao_inventa_conjuntura():
    import pandas as pd

    from core.llm_context_global import build_global_portfolio_context

    texto = build_global_portfolio_context(pd.DataFrame())
    assert "CONJUNTURA (noticiário" not in texto
