"""Valor de mercado dos EUA na escala da época: retroajuste em vez de descarte.

``market_cap_history`` multiplica o preço já ajustado por split pelas ações do
ponto-no-tempo, que não são retroajustadas. Até 08/11/2026 o publicador
descartava tudo antes do último degrau da contagem, e 2.852 das 4.350
empresas ficavam sem um ano de P/L. O que se prende aqui: o degrau de split é
retroajustado (pelo registro em ``market_us.splits`` ou pela razão medida,
quando é uma que a bolsa pratica), o degrau que não é split fica como está,
o mês isolado errado continua saindo, e o histórico de P/L anterior ao split
passa a existir com o valor certo. Nada toca o Postgres.
"""
from __future__ import annotations

import pathlib
import sys
from datetime import date

import pandas as pd
import pytest

_RAIZ = pathlib.Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from scripts import publish_valuation_historico as pub  # noqa: E402

BI = 1_000_000_000.0


def _meses(n: int, inicio=(2018, 1)) -> list[pd.Timestamp]:
    return list(pd.period_range(f"{inicio[0]}-{inicio[1]:02d}", periods=n, freq="M")
                .to_timestamp(how="end").normalize())


def _serie(simbolo: str, acoes_por_mes: list[float], close: float = 25.0,
           inicio=(2018, 1)) -> tuple[pd.DataFrame, pd.DataFrame]:
    """``market_cap_history`` como o armazém grava: preço ajustado × ações PIT."""
    meses = _meses(len(acoes_por_mes), inicio)
    cap = pd.DataFrame({"symbol": simbolo, "date": meses,
                        "market_cap": [close * a for a in acoes_por_mes]})
    precos = pd.DataFrame({"symbol": simbolo, "mes": meses, "close": close})
    return cap, precos


def test_split_registrado_retroajusta_tudo_antes_do_primeiro_balanco_pos_split():
    # 48 meses: 1 bi de ações até o mês 29 (split 4:1 no mês 27, balanço com a
    # contagem nova só no mês 30), 4 bi daí em diante. Com o preço já ajustado
    # (25 constante), o armazém gravava 25 bi antes e 100 bi depois.
    acoes = [1 * BI] * 30 + [4 * BI] * 18
    cap, precos = _serie("AAPL", acoes)
    splits = pd.DataFrame([{"symbol": "AAPL", "split_date": date(2020, 4, 15),
                            "numerator": 4, "denominator": 1}])
    limpo, contagem = pub.capitalizacao_limpa(cap, precos, splits)
    assert len(limpo) == 48, "nenhum mês é descartado: o degrau é corrigido, não cortado"
    assert (limpo["market_cap"].round(0) == 100 * BI).all()
    assert contagem["degraus_retroajustados"] == 1
    assert contagem["meses_retroajustados"] == 30
    assert contagem["capitalizacao_fora_do_preco"] == 0


def test_sem_registro_de_split_a_razao_medida_vale_se_for_razao_de_bolsa():
    acoes = [1 * BI] * 20 + [2 * BI] * 20
    cap, precos = _serie("WEG", acoes)
    limpo, contagem = pub.capitalizacao_limpa(cap, precos, None)
    assert (limpo["market_cap"].round(0) == 50 * BI).all()
    assert contagem["degraus_retroajustados"] == 1


def test_emissao_nao_e_split_e_fica_como_esta():
    # 1,6x de um trimestre para o outro: nenhuma razão de bolsa casa (3/2 fica
    # a 6,7%). É emissão: o valor de mercado anterior era menor de verdade.
    acoes = [1 * BI] * 20 + [1.6 * BI] * 20
    cap, precos = _serie("SPAC", acoes)
    limpo, contagem = pub.capitalizacao_limpa(cap, precos, None)
    assert contagem["degraus_retroajustados"] == 0
    assert contagem["degraus_mantidos"] == 1
    assert limpo["market_cap"].iloc[0] == pytest.approx(25 * BI)
    assert limpo["market_cap"].iloc[-1] == pytest.approx(40 * BI)
    # Nos arredores do degrau a mediana móvel mistura os dois regimes, mas
    # 1,6x fica dentro do desvio de mês isolado (1,5x contra a mediana).
    assert len(limpo) == 40


def test_grupamento_e_dois_splits_acumulam():
    # 1:10 (grupamento) no mês 10 e depois 2:1 no mês 30; a contagem anterior
    # aos dois degraus leva os dois fatores.
    acoes = [10 * BI] * 10 + [1 * BI] * 20 + [2 * BI] * 10
    cap, precos = _serie("REV", acoes)
    limpo, contagem = pub.capitalizacao_limpa(cap, precos, None)
    assert contagem["degraus_retroajustados"] == 2
    assert (limpo["market_cap"].round(0) == 50 * BI).all()


def test_mes_isolado_errado_continua_saindo_e_contado():
    acoes = [1 * BI] * 40
    acoes[20] = 3 * BI  # contagem errada de um mês só: não é degrau, é pico
    cap, precos = _serie("PICO", acoes)
    limpo, contagem = pub.capitalizacao_limpa(cap, precos, None)
    assert contagem["degraus_retroajustados"] == 0 and contagem["degraus_mantidos"] == 0
    assert contagem["capitalizacao_fora_do_preco"] == 1
    assert len(limpo) == 39
    assert (limpo["market_cap"].round(0) == 25 * BI).all()


def test_mes_sem_preco_sai_e_e_contado():
    acoes = [1 * BI] * 12
    cap, precos = _serie("SEMP", acoes)
    precos = precos.iloc[:-2]  # os dois últimos meses sem fechamento
    limpo, contagem = pub.capitalizacao_limpa(cap, precos, None)
    assert contagem["sem_preco_no_mes"] == 2 and len(limpo) == 10


def test_split_registrado_em_sentido_contrario_nao_e_usado():
    """Registro de desdobramento não explica um degrau para baixo."""
    acoes = [10 * BI] * 10 + [1 * BI] * 10
    cap, precos = _serie("INV", acoes)
    splits = pd.DataFrame([{"symbol": "INV", "split_date": date(2018, 9, 1),
                            "numerator": 10, "denominator": 1}])
    assert pub._split_registrado(splits, "INV", cap["date"].iloc[10], 0.1) is None
    # Sem o registro no sentido certo, vale a razão medida (1/10 é de bolsa).
    _, contagem = pub.capitalizacao_limpa(cap, precos, splits)
    assert contagem["degraus_retroajustados"] == 1


def test_dois_splits_registrados_na_janela_casam_cada_um_com_o_seu_degrau():
    # 2:1 no mês 9 (contagem nova no mês 12) e 3:1 no mês 18 (nova no mês
    # 21): regimes 1 → 2 → 6. O registro escolhido é o que explica o salto.
    acoes = [1 * BI] * 12 + [2 * BI] * 9 + [6 * BI] * 15
    cap, precos = _serie("DUP", acoes)
    meses = _meses(len(acoes))
    splits = pd.DataFrame([
        {"symbol": "DUP", "split_date": meses[18].date(), "numerator": 3, "denominator": 1},
        {"symbol": "DUP", "split_date": meses[9].date(), "numerator": 2, "denominator": 1},
    ])
    limpo, contagem = pub.capitalizacao_limpa(cap, precos, splits)
    assert contagem["degraus_retroajustados"] == 2
    assert (limpo["market_cap"].round(0) == 150 * BI).all()
    assert pub._split_registrado(splits, "DUP", meses[21], 3.05) == 3
    assert pub._split_registrado(splits, "DUP", meses[12], 2.02) == 2
    # Registro que não explica o salto medido não é usado.
    assert pub._split_registrado(splits, "DUP", meses[21], 10.0) is None


def test_salto_no_ultimo_mes_sem_confirmacao_nao_reescreve_o_historico():
    """Contagem errada só no mês mais recente: antes, o filtro de mês isolado
    a tirava; a nova regra não pode promovê-la a regime e multiplicar todo o
    histórico por 3."""
    acoes = [1 * BI] * 39 + [3 * BI]
    cap, precos = _serie("FIM", acoes)
    limpo, contagem = pub.capitalizacao_limpa(cap, precos, None)
    assert contagem["degraus_retroajustados"] == 0
    assert contagem["capitalizacao_fora_do_preco"] == 1 and len(limpo) == 39
    assert (limpo["market_cap"].round(0) == 25 * BI).all()


def test_salto_no_ultimo_mes_com_split_registrado_e_aplicado():
    acoes = [1 * BI] * 39 + [4 * BI]
    cap, precos = _serie("NOVO", acoes)
    meses = _meses(len(acoes))
    splits = pd.DataFrame([{"symbol": "NOVO", "split_date": meses[37].date(),
                            "numerator": 4, "denominator": 1}])
    limpo, contagem = pub.capitalizacao_limpa(cap, precos, splits)
    assert contagem["degraus_retroajustados"] == 1 and len(limpo) == 40
    assert (limpo["market_cap"].round(0) == 100 * BI).all()


def test_historico_de_pl_passa_a_existir_antes_do_split():
    acoes = [1 * BI] * 30 + [4 * BI] * 18
    cap, precos = _serie("AAPL", acoes)
    limpo, _ = pub.capitalizacao_limpa(cap, precos, None)
    anuais = pd.DataFrame([
        {"symbol": "AAPL", "fiscal_year": 2018, "reference_date": date(2018, 12, 31),
         "net_income": 5 * BI, "equity": 20 * BI, "dividends_paid": -1 * BI},
        {"symbol": "AAPL", "fiscal_year": 2019, "reference_date": date(2019, 12, 31),
         "net_income": 5 * BI, "equity": 20 * BI, "dividends_paid": -1 * BI},
        {"symbol": "AAPL", "fiscal_year": 2021, "reference_date": date(2021, 12, 31),
         "net_income": 5 * BI, "equity": 20 * BI, "dividends_paid": -1 * BI},
    ])
    hist = pub.historico_eua(anuais, limpo)["AAPL"]
    # Mesmo lucro e mesmo valor de mercado real nos três anos: P/L igual, 20x,
    # antes e depois do split. Com o descarte antigo, 2018 e 2019 não existiam.
    assert dict(hist["p_l"]) == {2018: 20.0, 2019: 20.0, 2021: 20.0}
    assert dict(hist["p_vp"]) == {2018: 5.0, 2019: 5.0, 2021: 5.0}
    assert dict(hist["dividend_yield"]) == {2018: 1.0, 2019: 1.0, 2021: 1.0}


def test_parece_split_distingue_razao_de_bolsa_de_emissao():
    assert pub._parece_split(2.0) and pub._parece_split(0.25) and pub._parece_split(1.5)
    assert pub._parece_split(4.08)  # 4:1 medido com a recompra do trimestre
    assert not pub._parece_split(1.6) and not pub._parece_split(1.3)
    assert not pub._parece_split(0.0)


def test_metodo_publicado_descreve_o_retroajuste_e_nao_o_descarte():
    assert "multiplicado pela razão do split" in pub.METODO["eua"]
    assert "tudo antes do último degrau" not in pub.METODO["eua"]
