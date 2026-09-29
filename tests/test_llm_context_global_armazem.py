"""O chat do Portfólio Global recebe o detalhe do armazém de cada classe."""
import inspect

import pandas as pd
import pytest

from core import llm_context_global_armazem as mod


def _df():
    return pd.DataFrame([
        {"symbol": "PETR4", "asset_class": "b3", "weight_global": 0.05},
        {"symbol": "WEGE3", "asset_class": "b3", "weight_global": 0.10},
        {"symbol": "B3SA3", "asset_class": "b3", "weight_global": 0.02},
        {"symbol": "LEVE3", "asset_class": "b3", "weight_global": 0.03},
        {"symbol": "BRAP4", "asset_class": "b3", "weight_global": 0.01},
        {"symbol": "XPLG11", "asset_class": "fii", "weight_global": 0.04},
        {"symbol": "KNHF11", "asset_class": "fii", "weight_global": 0.06},
        {"symbol": "NVDA", "asset_class": "us", "weight_global": 0.08},
        {"symbol": "MET", "asset_class": "us", "weight_global": 0.01},
        {"symbol": "EAT", "asset_class": "us", "weight_global": float("nan")},
    ])


def test_sem_citacao_vai_o_maior_peso_com_teto_por_classe():
    escolha = mod.ativos_para_detalhe(_df(), "como está o patrimônio?")
    assert escolha["b3"] == ["WEGE3", "PETR4", "LEVE3", "B3SA3"]
    assert escolha["fii"] == ["KNHF11", "XPLG11"]
    assert escolha["us"] == ["NVDA", "MET", "EAT"]


def test_citado_passa_na_frente_mesmo_com_peso_menor():
    escolha = mod.ativos_para_detalhe(_df(), "vale manter brap4 e xplg11?")
    assert escolha["b3"][0] == "BRAP4"
    assert "B3SA3" not in escolha["b3"]  # o teto corta o de menor peso
    assert escolha["fii"][0] == "XPLG11"


def test_ticker_americano_so_casa_em_maiusculas():
    # "met" e "eat" em texto corrido não são a MetLife nem a Brinker.
    escolha = mod.ativos_para_detalhe(_df(), "a meta que eu met eat", max_por_classe=1)
    assert escolha["us"] == ["NVDA"]
    escolha = mod.ativos_para_detalhe(_df(), "e a MET?", max_por_classe=1)
    assert escolha["us"] == ["MET"]


def test_citado_fora_da_carteira_entra_na_sua_classe():
    escolha = mod.ativos_para_detalhe(_df(), "trocar PETR4 por VALE3 e XPLG11 por HGLG11?")
    assert escolha["b3"][:2] == ["PETR4", "VALE3"]
    assert escolha["fii"][:2] == ["XPLG11", "HGLG11"]


def test_carteira_vazia_nao_chama_leitor(monkeypatch):
    monkeypatch.setattr(mod, "_leitores", lambda: pytest.fail("não devia ler"))
    assert mod.bloco_detalhe_armazem(pd.DataFrame(), "oi") == ""


def test_falha_de_uma_classe_nao_apaga_as_outras(monkeypatch):
    chamadas = {}

    def leitor(classe):
        def ler(simbolos):
            chamadas[classe] = list(simbolos)
            if classe == "fii":
                raise RuntimeError("conexão recusada\ndetalhe interno")
            return f"detalhe {classe}"
        return ler

    monkeypatch.setattr(mod, "_leitores", lambda: {c: leitor(c) for c in ("b3", "fii", "us")})
    bloco = mod.bloco_detalhe_armazem(_df(), "e a NVDA?")
    assert chamadas["us"][0] == "NVDA"
    assert "detalhe b3" in bloco and "detalhe us" in bloco
    assert "--- FUNDOS IMOBILIÁRIOS (KNHF11, XPLG11) ---" in bloco
    assert "indisponível agora (conexão recusada)" in bloco
    assert "detalhe interno" not in bloco
    assert bloco.startswith("=== DETALHE DO ARMAZÉM POR CLASSE ===")


def test_leitores_sao_os_das_abas_de_cada_classe():
    from core import llm_context_b3, llm_context_fii, llm_context_us

    leitores = mod._leitores()
    assert leitores["b3"] is llm_context_b3.get_warehouse_detail_context
    assert leitores["fii"] is llm_context_fii.get_warehouse_detail_context
    assert leitores["us"] is llm_context_us.get_warehouse_detail_context


def test_o_chat_da_tela_anexa_o_detalhe():
    from views import portfolio_global

    fonte = inspect.getsource(portfolio_global._painel_chat)
    assert "bloco_detalhe_armazem(df, pergunta)" in fonte
    # Depois do bloco de mercado e antes da chamada ao modelo.
    assert (fonte.index("bloco_contexto_mercado(") < fonte.index("bloco_detalhe_armazem(df")
            < fonte.index("chat_com_portfolio_global("))
