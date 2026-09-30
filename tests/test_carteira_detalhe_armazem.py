"""Os chats de Investimentos → Análise recebem o detalhe do armazém.

Até aqui eles recebiam macro e notícias, mas não a liquidez, o preço, os
proventos, os trimestres e o score mês a mês que as abas de cada classe já
davam à LLM para a mesma pergunta sobre o mesmo ativo.
"""
import inspect

import pandas as pd

from core import llm_context_global_armazem as mod
from core.contexto_mercado import classe_conjuntura
from core.llm_carteira import escopo_da_classe, regras_da_analise


def _posicoes():
    return [
        {"ticker": "wege3", "classe": "Ações BR", "valor_mercado": 900.0},
        {"ticker": "PETR4", "classe": "Ações BR", "valor_mercado": 300.0},
        {"ticker": "BOVA11", "classe": "ETF", "valor_mercado": 200.0},
        {"ticker": "IVVB11", "classe": "ETF", "moeda": "USD", "valor_mercado": 150.0},
        {"ticker": "XPLG11", "classe": "FII", "valor_mercado": 400.0},
        {"ticker": "NVDA", "classe": "Ações EUA", "valor_mercado": 500.0},
        {"ticker": "Tesouro IPCA+ 2035", "classe": "Tesouro", "valor_mercado": 1000.0},
        {"ticker": "", "classe": "Ações BR", "valor_mercado": 10.0},
        {"ticker": "ABCD3", "classe": "Ações BR", "valor_mercado": None},
    ]


def test_classe_conjuntura_leva_etf_cotado_fora_do_real_ao_exterior():
    assert classe_conjuntura({"classe": "ETF"}) == "b3"
    assert classe_conjuntura({"classe": "ETF", "moeda": "usd"}) == "us"
    assert classe_conjuntura({"classe": "FII", "moeda": "USD"}) == "fii"
    assert classe_conjuntura({"classe": "Tesouro"}) is None
    assert classe_conjuntura({}) is None


def test_quadro_das_posicoes_mapeia_classe_e_descarta_o_que_nao_tem_detalhe():
    df = mod.quadro_das_posicoes(_posicoes())
    por_ativo = dict(zip(df["symbol"], df["asset_class"]))
    assert por_ativo == {"WEGE3": "b3", "PETR4": "b3", "BOVA11": "b3",
                         "IVVB11": "us", "XPLG11": "fii", "NVDA": "us",
                         "ABCD3": "b3"}
    assert df.loc[df["symbol"] == "ABCD3", "weight_global"].item() == 0.0


def test_na_aba_da_classe_vale_a_classe_da_aba_e_nao_o_rotulo():
    """A sub-aba Ações pega todo rótulo com "ação"; o mapa só conhece "Ações BR"."""
    posicoes = [{"ticker": "ITUB4", "classe": "Ações", "valor_mercado": 10.0},
                {"ticker": "AAPL34", "classe": "BDR", "pais": "US", "valor_mercado": 5.0}]
    assert mod.quadro_das_posicoes(posicoes[:1]).empty
    df = mod.quadro_das_posicoes(posicoes, classe="us")
    assert set(df["asset_class"]) == {"us"} and len(df) == 2


def test_quadro_vazio_tem_as_colunas_que_o_seletor_le():
    df = mod.quadro_das_posicoes([])
    assert df.empty and list(df.columns) == ["symbol", "asset_class", "weight_global"]
    assert mod.ativos_para_detalhe(df, "oi") == {}


def test_chat_da_classe_nao_recebe_detalhe_de_outra_classe():
    df = mod.quadro_das_posicoes(_posicoes())
    # XPLG11 (da carteira) e HGLG11 (de fora) são FIIs: não entram no chat de ações.
    escolha = mod.ativos_para_detalhe(df, "troco XPLG11 por HGLG11 ou por VALE3?",
                                      max_por_classe=mod.MAX_NA_ABA_DA_CLASSE,
                                      classes=("b3",))
    assert list(escolha) == ["b3"]
    assert escolha["b3"] == ["VALE3", "WEGE3", "PETR4", "BOVA11", "ABCD3"]


def test_limite_da_aba_da_classe_e_o_menor_dos_leitores():
    from core import llm_context_b3, llm_context_fii, llm_context_us

    assert mod.MAX_NA_ABA_DA_CLASSE == min(llm_context_b3._MAX_DETALHE,
                                           llm_context_fii._MAX_DETALHE,
                                           llm_context_us._MAX_DETALHE)


def test_bloco_da_classe_chama_so_o_leitor_dela(monkeypatch):
    chamadas = {}

    def _leitor(classe):
        return lambda simbolos: chamadas.setdefault(classe, list(simbolos)) and f"detalhe {classe}"

    monkeypatch.setattr(mod, "_leitores",
                        lambda: {c: _leitor(c) for c in ("b3", "fii", "us")})
    bloco = mod.bloco_detalhe_armazem(mod.quadro_das_posicoes(_posicoes()), "e o NVDA?",
                                      max_por_classe=mod.MAX_NA_ABA_DA_CLASSE,
                                      classes=("us",))
    assert chamadas == {"us": ["NVDA", "IVVB11"]}
    assert "AÇÕES AMERICANAS (NVDA, IVVB11)" in bloco and "detalhe us" in bloco
    assert "até 5 por classe" in bloco


def test_visao_geral_recebe_todas_as_classes(monkeypatch):
    chamadas = {}
    monkeypatch.setattr(mod, "_leitores", lambda: {
        c: (lambda s, c=c: chamadas.setdefault(c, list(s)) and "ok")
        for c in ("b3", "fii", "us")})
    mod.bloco_detalhe_armazem(mod.quadro_das_posicoes(_posicoes()), "como estou?")
    assert chamadas == {"b3": ["WEGE3", "PETR4", "BOVA11", "ABCD3"],
                        "fii": ["XPLG11"], "us": ["NVDA", "IVVB11"]}


def test_a_view_anexa_o_detalhe_nos_dois_chats():
    """Fiação: o contexto da classe e o da Visão Geral chamam o seletor.

    Os dois ``build_context`` são closures dentro de funções de tela que
    precisam de Streamlit e banco; a checagem de comportamento está nos testes
    acima, e aqui fica só a garantia de que a tela os usa.
    """
    from views import investimentos

    fonte = inspect.getsource(investimentos._bloco_analise_classe)
    assert "bloco_detalhe_armazem(" in fonte
    assert "classes=(conj,)" in fonte and "MAX_NA_ABA_DA_CLASSE" in fonte
    assert "quadro_das_posicoes(posicoes_classe, classe=conj)" in fonte

    fonte_modulo = inspect.getsource(investimentos)
    assert "def _contexto_geral(" in fonte_modulo
    assert "build_context=_contexto_geral" in fonte_modulo
    assert "bloco_detalhe_armazem(quadro_das_posicoes(posicoes), pergunta)" in fonte_modulo


def test_prompt_da_analise_explica_o_bloco():
    for geral in (False, True):
        regras = regras_da_analise(geral=geral)
        assert "10. Quando presente, o bloco DETALHE DO ARMAZÉM POR CLASSE" in regras
        assert "ativo fora dele não tem esse detalhe" in regras
    # A Visão Geral já não afirma que detalhe nenhum está no contexto.
    assert "NÃO está neste contexto" not in escopo_da_classe("geral")
    assert "DETALHE DO ARMAZÉM POR CLASSE" in escopo_da_classe("geral")


def test_quadro_aceita_dataframe_de_posicoes_sem_erro():
    # As posições chegam como list[dict]; pd.DataFrame.to_dict("records") é o
    # caminho de quem as monta a partir de um quadro.
    registros = pd.DataFrame(_posicoes()).to_dict("records")
    assert len(mod.quadro_das_posicoes(registros)) == 7
