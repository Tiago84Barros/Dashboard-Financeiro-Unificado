"""Guardas da medição de alcance do evento sobre a carteira.

O teste que mais importa aqui é o da coluna ausente. A tentação de tratar
``country`` inexistente como "0% de exposição geográfica" produz o defeito que
este projeto já registrou em outras formas: a regra fica certa, a entrada fica
errada, e o motor aprova com confiança. Aqui isso está travado por teste, e não
por comentário.
"""
from __future__ import annotations

import pandas as pd
import pytest

from core.eventos_extremos import exposicao as ex


def carteira() -> pd.DataFrame:
    """Cinco posições, pesos somando 1,0, todas as dimensões preenchidas."""
    return pd.DataFrame([
        {"symbol": "ITUB4", "weight_global": 0.30, "sector": "Bancos",
         "country": "BR", "currency": "BRL", "asset_class": "acoes"},
        {"symbol": "BBDC4", "weight_global": 0.20, "sector": "Bancos",
         "country": "BR", "currency": "BRL", "asset_class": "acoes"},
        {"symbol": "PETR4", "weight_global": 0.20, "sector": "Energia",
         "country": "BR", "currency": "BRL", "asset_class": "acoes"},
        {"symbol": "AAPL", "weight_global": 0.20, "sector": "Tecnologia",
         "country": "US", "currency": "USD", "asset_class": "acoes"},
        {"symbol": "IVVB11", "weight_global": 0.10, "sector": "Indice",
         "country": "US", "currency": "USD", "asset_class": "etf"},
    ])


# -- Direta -------------------------------------------------------------------
def test_exposicao_direta_e_o_peso_dos_ativos_nomeados():
    r = ex.medir(carteira(), ex.Alvo.de(tickers=["ITUB4"]))
    assert r.direta == pytest.approx(0.30)
    assert r.ativos_diretos == ("ITUB4",)


def test_ticker_nomeado_fora_da_carteira_e_zero_com_limitacao():
    """Zero aqui é medido: o ativo existe, a carteira não o tem."""
    r = ex.medir(carteira(), ex.Alvo.de(tickers=["VALE3"]))
    assert r.direta == pytest.approx(0.0)
    assert any("não estão nesta carteira" in lim for lim in r.limitacoes)


def test_ticker_e_comparado_sem_depender_de_caixa_ou_espaco():
    r = ex.medir(carteira(), ex.Alvo.de(tickers=[" itub4 "]))
    assert r.direta == pytest.approx(0.30)


# -- Indireta -----------------------------------------------------------------
def test_contagio_setorial_entra_descontado():
    """BBDC4 (20%) é do setor mas não foi nomeado: entra por 0,60."""
    r = ex.medir(carteira(), ex.Alvo.de(tickers=["ITUB4"], setores=["Bancos"]))
    assert r.direta == pytest.approx(0.30)
    assert r.indireta == pytest.approx(0.20 * ex.FATOR_CONTAGIO["sector"])
    assert r.por_dimensao["sector"] == pytest.approx(0.20)


def test_ativo_atingido_por_duas_dimensoes_conta_uma_vez_pelo_maior_fator():
    """Somar dimensões daria exposição acima do peso do próprio ativo."""
    alvo = ex.Alvo.de(setores=["Bancos"], paises=["BR"], moedas=["BRL"])
    r = ex.medir(carteira(), alvo)
    # ITUB4+BBDC4+PETR4 = 70% do peso, todos BR/BRL; os bancos pegam 0,60.
    esperado = (0.30 + 0.20) * 0.60 + 0.20 * 0.45
    assert r.indireta == pytest.approx(esperado)
    assert r.indireta <= 0.70, "contágio não pode superar o peso atingido"


def test_ativo_nomeado_nao_e_contado_de_novo_como_contagio():
    r = ex.medir(carteira(), ex.Alvo.de(tickers=["ITUB4"], setores=["Bancos"]))
    assert r.por_dimensao["sector"] == pytest.approx(0.20), \
        "ITUB4 já entrou na direta e não pode reaparecer na indireta"


def test_evento_sem_dimensao_de_contagio_tem_indireta_zero_medida():
    r = ex.medir(carteira(), ex.Alvo.de(tickers=["ITUB4"]))
    assert r.indireta == pytest.approx(0.0)


# -- Ausência de dado nunca vira ausência de risco -----------------------------
def test_coluna_ausente_nao_vira_exposicao_zero():
    """Regressão do defeito que o projeto já registrou em outras formas."""
    df = carteira().drop(columns=["country"])
    r = ex.medir(df, ex.Alvo.de(paises=["BR"]))
    assert r.por_dimensao["country"] is None
    assert r.indireta is None, "sem a coluna, o contágio não foi medido"
    assert any("country" in lim for lim in r.limitacoes)


def test_coluna_presente_e_inteiramente_nula_e_tao_inutil_quanto_ausente():
    df = carteira()
    df["country"] = None
    r = ex.medir(df, ex.Alvo.de(paises=["BR"]))
    assert r.por_dimensao["country"] is None
    assert r.indireta is None


def test_uma_dimensao_medida_e_outra_nao_mede_o_que_da_e_avisa():
    df = carteira().drop(columns=["currency"])
    r = ex.medir(df, ex.Alvo.de(setores=["Bancos"], moedas=["BRL"]))
    assert r.por_dimensao["sector"] == pytest.approx(0.50)
    assert r.por_dimensao["currency"] is None
    assert r.indireta is not None, "o que deu para medir foi medido"
    assert any("currency" in lim for lim in r.limitacoes)


def test_carteira_vazia_nao_e_carteira_protegida():
    r = ex.medir(pd.DataFrame(), ex.Alvo.de(tickers=["ITUB4"]))
    assert r.direta is None and r.indireta is None
    assert r.limitacoes


def test_quadro_sem_a_coluna_de_peso_parece_falha_e_nao_zero():
    """Falha de leitura tem que parecer falha: `.empty` não a pega."""
    df = carteira().drop(columns=["weight_global"])
    r = ex.medir(df, ex.Alvo.de(tickers=["ITUB4"]))
    assert r.direta is None
    assert any("weight_global" in lim for lim in r.limitacoes)


def test_pesos_somando_zero_nao_sustentam_conclusao():
    df = carteira()
    df["weight_global"] = 0.0
    r = ex.medir(df, ex.Alvo.de(tickers=["ITUB4"]))
    assert r.direta is None


def test_evento_sem_alvo_nenhum_e_desconhecido_e_nao_inofensivo():
    """Zero aqui faria o motor concluir que a carteira está a salvo."""
    r = ex.medir(carteira(), ex.Alvo.de())
    assert r.direta is None and r.indireta is None
    assert any("não é atribuível" in lim for lim in r.limitacoes)


def test_pesos_nao_normalizados_viram_fracao_do_total():
    df = carteira()
    df["weight_global"] = df["weight_global"] * 1000.0
    r = ex.medir(df, ex.Alvo.de(tickers=["ITUB4"]))
    assert r.direta == pytest.approx(0.30)


# -- Total e leitura ----------------------------------------------------------
def test_total_nao_passa_de_cem_por_cento():
    alvo = ex.Alvo.de(tickers=["ITUB4", "BBDC4", "PETR4", "AAPL", "IVVB11"],
                      setores=["Bancos"])
    assert ex.medir(carteira(), alvo).total == pytest.approx(1.0)


def test_total_e_none_quando_nada_foi_medido():
    assert ex.medir(pd.DataFrame(), ex.Alvo.de(tickers=["X"])).total is None


def test_descrever_cita_o_numero_e_os_ativos():
    texto = "\n".join(ex.medir(carteira(),
                               ex.Alvo.de(tickers=["ITUB4"])).descrever())
    assert "30.0%" in texto and "ITUB4" in texto


def test_descrever_diz_o_que_nao_foi_medido():
    df = carteira().drop(columns=["country"])
    texto = "\n".join(ex.medir(df, ex.Alvo.de(paises=["BR"])).descrever())
    assert "não medida" in texto and "limitação" in texto


# -- Ponte para a evidência ---------------------------------------------------
def test_para_evidencia_leva_exposicao_e_limitacoes():
    r = ex.medir(carteira(), ex.Alvo.de(tickers=["ITUB4"], setores=["Bancos"]))
    e = ex.para_evidencia(r, liquidez_disponivel=0.25)
    assert e.bruto_de("exposicao_direta") == pytest.approx(0.30)
    assert e.bruto_de("exposicao_indireta") == pytest.approx(0.12)
    assert e.valor_de("liquidez_disponivel") == pytest.approx(0.75)


def test_para_evidencia_nao_inventa_medicao_que_nao_recebeu():
    """Componente sem medição sai da média e entra na cobertura."""
    r = ex.medir(carteira(), ex.Alvo.de(tickers=["ITUB4"]))
    e = ex.para_evidencia(r)
    assert e.valor_de("perda_simulada") is None
    assert e.cobertura < 1.0


def test_para_evidencia_de_carteira_nao_medida_nao_vira_carteira_ilesa():
    r = ex.medir(pd.DataFrame(), ex.Alvo.de(tickers=["ITUB4"]))
    e = ex.para_evidencia(r)
    assert e.valor_de("exposicao_direta") is None
    assert e.limitacoes
