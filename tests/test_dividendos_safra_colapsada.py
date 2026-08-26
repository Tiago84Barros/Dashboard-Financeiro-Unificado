"""A-131: `market.dividends` guarda duas safras do MESMO pagamento.

O que o dado mostra (medido no Supabase em 26/08/2026, RELG11 e HGLG11):

  ex=2025-09-05  pay=2025-09-12  amt=0,80   criada 27/06   <- calendario real
  ex=2025-09-01  pay=2025-09-01  amt=0,80   criada 25/07   <- safra colapsada

A segunda safra entrou em bloco (23 a 25/07/2026), sempre com data-ex no dia 1
e sempre com ``payment_date = ex_date``. Nenhum evento real da B3 paga no
mesmo dia em que fica ex: a mediana da defasagem na tabela e de 14 dias. O dia
colapsado nao e uma data, e a ausencia de uma.

RELG11 paga 0,80 por mes e carrega 11 linhas reais mais 10 colapsadas. Quem
soma a coluna recebe quase o dobro da renda: medido sobre os FIIs investiveis,
187 fundos ficam inflados, mediana +35,8%, maximo +90,9%. HGLG11 -- um dos
maiores do pais -- sai 64,7% acima.

O `core/portfolio/adapters/fii.py` ACHAVA que ja tratava isso: ele faz
``MIN(amount) GROUP BY ticker, ano, event_date, type``. Nao pega, e o motivo e
exatamente o que torna as duas safras duas safras -- o ``event_date`` delas
DIFERE (01/09 contra 12/09). Deduplicar pela coluna em que as copias divergem
nao deduplica nada.

POR QUE A REGRA E POR MES DE PAGAMENTO, E NAO POR VALOR PROXIMO
---------------------------------------------------------------
A tentacao e casar as copias por valor. HGLG11 mostra por que nao serve: entre
as colapsadas ha 0,9574 e 1,0734 ao lado de 1,1000 reais. Uma tolerancia
apertada deixa as duas passarem e o fundo continua inflado; uma tolerancia
larga comeca a apagar evento legitimo. O mes do pagamento e a chave estavel --
a copia colapsada e sempre projetada para dentro do mes em que o dinheiro caiu.

O QUE A REGRA NAO FAZ
---------------------
Nao apaga linha do banco: e filtro de leitura. A safra colapsada continua na
tabela como evidencia, e uma linha colapsada SEM gemea de calendario real
sobrevive -- sao 20 mil linhas de historico antigo em que a brapi nunca deu as
duas datas, e descarta-las perderia evento de verdade.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from core.dividend_types import (descarta_safra_colapsada, eh_safra_colapsada,
                                 sql_safra_canonica)


def _linha(ex, pay, amount, ticker="RELG11", tipo="RENDIMENTO"):
    return {"ticker": ticker, "type": tipo, "amount": amount,
            "ex_date": dt.date.fromisoformat(ex),
            "payment_date": dt.date.fromisoformat(pay)}


# --------------------------------------------------------------------------
# o discriminador
# --------------------------------------------------------------------------

@pytest.mark.parametrize("ex, pay, esperado", [
    ("2025-09-01", "2025-09-01", True),   # a safra de 25/07: pagou no dia ex
    ("2025-09-05", "2025-09-12", False),  # calendario real: 7 dias de defasagem
    ("2026-06-30", "2026-07-14", False),
])
def test_pagamento_no_proprio_dia_ex_marca_a_safra_colapsada(ex, pay, esperado):
    assert eh_safra_colapsada(dt.date.fromisoformat(ex),
                              dt.date.fromisoformat(pay)) is esperado


def test_data_ausente_nao_e_colapsada():
    """Sem uma das datas nao da para afirmar colapso -- e ausencia, nao prova."""
    assert eh_safra_colapsada(None, dt.date(2025, 9, 1)) is False
    assert eh_safra_colapsada(dt.date(2025, 9, 1), None) is False


# --------------------------------------------------------------------------
# a regra de descarte
# --------------------------------------------------------------------------

def test_relg11_perde_as_copias_e_mantem_os_eventos_reais():
    """O caso medido: 3 meses, cada um com a real e a colapsada."""
    df = pd.DataFrame([
        _linha("2025-09-01", "2025-09-01", 0.80),
        _linha("2025-09-05", "2025-09-12", 0.80),
        _linha("2025-10-01", "2025-10-01", 0.80),
        _linha("2025-10-07", "2025-10-14", 0.80),
        _linha("2025-11-01", "2025-11-01", 0.80),
        _linha("2025-11-07", "2025-11-14", 0.80),
    ])
    out = descarta_safra_colapsada(df)
    assert len(out) == 3, "sobra um evento por mes, o de calendario real"
    assert out["amount"].sum() == pytest.approx(2.40)
    assert (out["payment_date"] != out["ex_date"]).all()


def test_valor_divergente_na_copia_nao_a_salva():
    """HGLG11: colapsadas de 0,9574 e 1,0734 ao lado de 1,1000 reais.

    Casar por valor deixaria as duas passarem. A chave e o mes do pagamento.
    """
    df = pd.DataFrame([
        _linha("2025-11-01", "2025-11-01", 0.9574, ticker="HGLG11"),
        _linha("2025-10-31", "2025-11-14", 1.1000, ticker="HGLG11"),
        _linha("2026-04-01", "2026-04-01", 1.0734, ticker="HGLG11"),
        _linha("2026-04-01", "2026-04-01", 1.1000, ticker="HGLG11"),
        _linha("2026-03-31", "2026-04-15", 1.1000, ticker="HGLG11"),
    ])
    out = descarta_safra_colapsada(df)
    assert len(out) == 2
    assert out["amount"].sum() == pytest.approx(2.20)


def test_colapsada_sem_gemea_real_sobrevive():
    """20 mil linhas antigas so existem na forma colapsada. Descartar por
    formato apagaria evento que nao tem substituto."""
    df = pd.DataFrame([
        _linha("2019-03-01", "2019-03-01", 0.55),
        _linha("2019-04-01", "2019-04-01", 0.55),
    ])
    out = descarta_safra_colapsada(df)
    assert len(out) == 2


def test_o_mes_do_pagamento_e_a_chave_e_nao_o_da_data_ex():
    """RELG11 fica ex em 08/06 e paga em 15/06; a copia e projetada para 01/06.

    Ja o par de HGLG11 fica ex em 31/10 e paga em 14/11 -- a copia cai em
    01/11. Se a chave fosse o mes do EX, esse par nunca se encontraria.
    """
    df = pd.DataFrame([
        _linha("2025-11-01", "2025-11-01", 1.10, ticker="HGLG11"),
        _linha("2025-10-31", "2025-11-14", 1.10, ticker="HGLG11"),
    ])
    out = descarta_safra_colapsada(df)
    assert len(out) == 1
    assert out.iloc[0]["ex_date"] == dt.date(2025, 10, 31)


def test_tipos_diferentes_no_mesmo_mes_nao_se_cancelam():
    """Amortizacao e rendimento no mesmo mes sao dois eventos. A real de um
    tipo nao pode derrubar a colapsada do outro."""
    df = pd.DataFrame([
        _linha("2026-06-01", "2026-06-01", 66.33, tipo="AMORTIZAÇÃO"),
        _linha("2026-06-08", "2026-06-15", 0.80, tipo="RENDIMENTO"),
    ])
    assert len(descarta_safra_colapsada(df)) == 2


def test_tickers_diferentes_nao_se_cancelam():
    df = pd.DataFrame([
        _linha("2025-09-01", "2025-09-01", 0.80, ticker="RELG11"),
        _linha("2025-09-05", "2025-09-12", 1.10, ticker="HGLG11"),
    ])
    assert len(descarta_safra_colapsada(df)) == 2


def test_quadro_vazio_e_sem_colunas_nao_quebra():
    assert descarta_safra_colapsada(pd.DataFrame()).empty
    df = pd.DataFrame([{"ticker": "X", "amount": 1.0}])
    assert len(descarta_safra_colapsada(df)) == 1, "sem as datas, nada a julgar"


# --------------------------------------------------------------------------
# a versao SQL, que e a que roda nos caminhos de decisao
# --------------------------------------------------------------------------

def test_sql_protege_contra_nulo():
    """`payment_date = ex_date` e NULL quando uma das datas falta, e `NOT NULL`
    tambem e NULL -- a linha sumiria do WHERE em silencio. O predicado precisa
    de COALESCE, senao o filtro apaga o que nao sabe julgar."""
    assert "COALESCE" in sql_safra_canonica().upper()


def test_sql_casa_por_mes_de_pagamento_e_por_tipo():
    sql = sql_safra_canonica("d").lower()
    assert "date_trunc('month'" in sql and "payment_date" in sql
    assert "type is not distinct from" in sql, "tipo nulo tem de casar com nulo"
    assert "ticker" in sql


def test_sql_aceita_alias_proprio():
    assert "div1.ticker" in sql_safra_canonica("div1")
