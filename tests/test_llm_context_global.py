"""Contexto do chat do Portfólio Global (core/llm_context_global.py).

O que estes testes protegem: o contexto é a ÚNICA coisa que o LLM enxerga.
Um número ausente escrito como "0" ou um bloco silenciosamente vazio produz
resposta confiante e errada, sem erro visível em lugar nenhum.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pytest

from core.global_portfolio.returns import Cobertura
from core.llm_context_global import build_global_portfolio_context


def _df() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "asset_class": "b3", "symbol": "PETR4", "name": "Petrobras",
            "sector_raw": "Petróleo", "sector": "Energia", "segment": "",
            "currency": "BRL", "country": "BR", "weight_class": 0.5,
            "weight_global": 0.30, "valor_brl": 30000.0,
            "payload": {"fundamentals": {"P/L": 4.2, "P/VP": 1.1, "DY": 0.12},
                        "metrics": {"score": 78.0}},
        },
        {
            "asset_class": "us", "symbol": "AAPL", "name": "Apple",
            "sector_raw": "Tech", "sector": "Tecnologia", "segment": "",
            "currency": "USD", "country": "US", "weight_class": 1.0,
            "weight_global": 0.45, "valor_brl": 45000.0,
            "payload": {"fundamentals": {"pe": 30.0, "roe": 0.9},
                        "metrics": {"us_score": 81.0}},
        },
        {
            "asset_class": "fii", "symbol": "MXRF11", "name": "Maxi Renda",
            "sector_raw": "Papel", "sector": "Recebíveis", "segment": "",
            "currency": "BRL", "country": "BR", "weight_class": 1.0,
            "weight_global": 0.25, "valor_brl": 25000.0,
            "payload": {"fundamentals": {"pvp": 0.98, "dy_12m": 0.13},
                        "metrics": {"fii_score": 66.0}},
        },
    ])


@dataclass
class _Papel:
    symbol: str
    papeis: tuple
    evidencias: tuple = ()
    indeterminados: tuple = ()
    justificativa: str = ""


@dataclass
class _Acao:
    symbol: str
    acao: str
    peso_atual: float
    peso_sugerido: float
    score: float | None
    componentes: dict
    analisadores: frozenset
    custo_estimado: float
    custo_calibrado: bool


def test_contexto_traz_composicao_alvo_e_desvio():
    texto = build_global_portfolio_context(
        _df(), alvos={"b3": 0.4, "us": 0.4, "fii": 0.2}, total_brl=100000.0)

    assert "PETR4" in texto and "AAPL" in texto and "MXRF11" in texto
    assert "30,00%" in texto          # peso global de PETR4
    assert "alvo 40,00%" in texto     # alvo da B3
    assert "desvio" in texto


def test_classe_com_alvo_mas_sem_posicao_aparece_no_contexto():
    """Alvo sem posição é justamente a pergunta que o usuário faz ao chat.
    Se o bloco só listasse as classes presentes, o buraco sumiria."""
    df = _df()
    texto = build_global_portfolio_context(
        df[df["asset_class"] != "fii"], alvos={"b3": 0.4, "us": 0.4, "fii": 0.2})

    assert "real 0,00%" in texto


def test_ausencia_nunca_vira_zero():
    """`None` precisa chegar ao modelo como 'ausente'. Escrito como 0 ele
    entraria em qualquer média que o modelo fizesse."""
    df = _df()
    df.loc[df["symbol"] == "AAPL", "valor_brl"] = None
    texto = build_global_portfolio_context(df)

    assert "ausente" in texto
    assert "Patrimônio total informado: ausente" in texto


def test_sem_serie_mensal_o_bloco_de_risco_diz_o_que_falta():
    texto = build_global_portfolio_context(_df(), retornos=pd.DataFrame(), pesos={})

    assert "Sem série mensal suficiente para risco e correlação." in texto


def test_cobertura_nomeia_os_ativos_fora_da_estatistica():
    cobertura = Cobertura(
        simbolos_com_serie=("PETR4", "AAPL"), simbolos_sem_serie=("MXRF11",),
        peso_coberto=0.75, meses=60, periodo_comum=("2021-01", "2025-12"),
    )
    texto = build_global_portfolio_context(_df(), cobertura=cobertura)

    assert "MXRF11" in texto
    assert "SEM série de preço" in texto
    assert "2021-01 a 2025-12" in texto


def test_papeis_e_recomendacoes_entram_como_recebidos():
    papeis = [_Papel("PETR4", ("renda",)), _Papel("AAPL", (), indeterminados=("crescimento",))]
    acoes = [
        _Acao("PETR4", "reduzir", 0.30, 0.25, 0.4, {"risco": 0.4},
              frozenset({"risk"}), 0.001, True),
        _Acao("AAPL", "indeterminado", 0.45, 0.45, None, {}, frozenset(), 0.0, True),
    ]
    texto = build_global_portfolio_context(_df(), papeis=papeis, acoes=acoes)

    assert "NENHUM papel com evidência suficiente" in texto
    assert "indeterminado: crescimento" in texto
    assert "PETR4: reduzir" in texto
    assert "score ausente" in texto


def test_custo_vai_em_reais_porque_e_em_reais_que_o_motor_calcula():
    """`advisor._resolver_custo` multiplica o delta de peso pelo patrimônio:
    `custo_estimado` está em R$. Formatado como percentual, R$ 120,00 viraria
    '12000%' e o modelo concluiria que o custo inviabiliza tudo."""
    acoes = [_Acao("PETR4", "reduzir", 0.30, 0.25, 0.4, {"risco": 0.4},
                   frozenset({"risk"}), 120.0, True)]
    texto = build_global_portfolio_context(_df(), acoes=acoes)

    assert "custo R$ 120,00" in texto


def test_custo_nao_calibrado_nunca_aparece_como_numero():
    """O motor devolve NaN de propósito quando a classe não tem custo
    calibrado; formatado como número viraria um custo que parece apurado."""
    acoes = [_Acao("MXRF11", "manter", 0.25, 0.30, 0.2, {"risco": 0.2},
                   frozenset({"risk"}), float("nan"), False)]
    texto = build_global_portfolio_context(_df(), acoes=acoes)

    assert "custo não calibrado" in texto
    assert "nan" not in texto.lower()


def test_custo_do_indeterminado_nao_vira_zero_reais():
    """O motor grava 0.0 ali só porque nunca tentou calcular; 'R$ 0,00'
    leria como movimento de graça."""
    acoes = [_Acao("AAPL", "indeterminado", 0.45, 0.45, None, {}, frozenset(), 0.0, True)]
    texto = build_global_portfolio_context(_df(), acoes=acoes)

    assert "não aplicável" in texto
    assert "R$ 0,00" not in texto


def test_risco_por_ativo_sai_como_fatia_comparavel_com_o_peso():
    """`contribuicao` vem em unidade de volatilidade (soma = sigma_p). Crua,
    ela é sempre um número pequeno ao lado do peso, e o modelo concluiria que
    todo ativo carrega menos risco do que seu peso — o oposto do que a
    decomposição diz."""
    import numpy as np

    rng = np.random.default_rng(0)
    idx = pd.period_range("2021-01", "2025-12", freq="M").astype(str)
    ret = pd.DataFrame(rng.normal(0.01, 0.05, (len(idx), 3)), index=idx,
                       columns=["PETR4", "AAPL", "MXRF11"])
    pesos = {"PETR4": 0.30, "AAPL": 0.45, "MXRF11": 0.25}

    texto = build_global_portfolio_context(_df(), retornos=ret, pesos=pesos)

    assert "do risco" in texto
    bloco = texto.split("=== RISCO E CORRELACAO ===")[1]
    fatias = [float(linha.split("% do risco")[0].split(": ")[-1].replace(",", "."))
              for linha in bloco.splitlines() if "% do risco" in linha]
    assert len(fatias) == 3
    assert abs(sum(fatias) - 100.0) < 0.1


def test_quadro_vazio_nao_quebra_e_diz_que_esta_vazio():
    texto = build_global_portfolio_context(pd.DataFrame())

    assert "Ativos com posição: 0" in texto
    assert "Nenhuma posição." in texto


@pytest.mark.parametrize("valor", [None, "", "  "])
def test_classe_desconhecida_nao_derruba_o_builder(valor):
    df = _df()
    df.loc[df["symbol"] == "AAPL", "asset_class"] = valor
    assert build_global_portfolio_context(df)
