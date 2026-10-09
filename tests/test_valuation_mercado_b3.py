"""Bolsa cara ou barata: P/L mediano do mercado B3 no contexto das LLMs.

O que se prende aqui é o que faria a LLM chamar a bolsa de cara ou barata sem
base: LPA do exercício que ainda não tinha saído no mês (olhar o futuro),
universo escolhido com o volume do próprio ano, duas classes da mesma empresa
contando dobrado, LPA em escala errada entrando na mediana, e a ressalva "não
é o P/L do Ibovespa" longe do número.
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

import core.contexto_mercado as cm  # noqa: E402
import core.trajetoria_mercado as tm  # noqa: E402
import scripts.publish_valuation_historico as pv  # noqa: E402


def _quadros(tickers, *, meses, precos=None, lpas=None, vol_ano=2019):
    """Fita mensal, volume do ano anterior e LPA anual de um universo sintético."""
    precos = precos or {}
    lpas = lpas or {}
    fech = pd.DataFrame([{"ticker": tk, "data": d, "close": precos.get((tk, d), 10.0)}
                         for tk in tickers for d in meses])
    vol = pd.DataFrame([{"ticker": tk, "ano": vol_ano, "vol": 1000.0 - i}
                        for i, tk in enumerate(tickers)])
    lpa = pd.DataFrame([{"ticker": tk, "ano": ano, "lpa": lpas.get((tk, ano), 1.0)}
                        for tk in tickers for ano in range(2015, 2021)])
    return fech, vol, lpa


def _nomes(n):
    return [f"T{i:03d}3" for i in range(n)]


def test_universo_e_do_ano_anterior_e_uma_classe_por_empresa():
    vol = pd.DataFrame([
        {"ticker": "PETR4", "ano": 2019, "vol": 900.0},
        {"ticker": "PETR3", "ano": 2019, "vol": 800.0},
        {"ticker": "BOVA11", "ano": 2019, "vol": 999.0},     # ETF: sem demonstração
        {"ticker": "WEGE3", "ano": 2019, "vol": 100.0},
        {"ticker": "ABEV3", "ano": 2020, "vol": 5000.0},     # volume do próprio ano
    ])
    u = pv.universo_mercado(vol, {"PETR4", "PETR3", "WEGE3", "ABEV3"}, n=2)
    assert u[2020] == ["PETR4", "WEGE3"]
    assert u[2021] == ["ABEV3"]


def test_lpa_do_exercicio_so_entra_depois_de_marco():
    assert pv.exercicio_publicado(pd.Period("2020-03", "M")) == 2018
    assert pv.exercicio_publicado(pd.Period("2020-04", "M")) == 2019


def test_mediana_ponto_no_tempo_e_quem_ficou_fora():
    tickers = _nomes(25)
    meses = [date(2020, 3, 31), date(2020, 4, 30)]
    lpas = {(tk, 2018): 0.5 for tk in tickers}          # P/L 20 em março
    lpas.update({(tk, 2019): 1.0 for tk in tickers})    # P/L 10 de abril em diante
    lpas[(tickers[0], 2019)] = 0.0001                   # escala errada: P/L 100.000
    lpas[(tickers[1], 2019)] = 50.0                     # escala errada: P/L 0,2
    lpas[(tickers[2], 2019)] = -20.0                    # prejuízo maior que o preço
    lpas[(tickers[3], 2019)] = -1.0                     # prejuízo plausível: entra
    del lpas[(tickers[4], 2019)]
    fech, vol, lpa = _quadros(tickers, meses=meses, lpas=lpas)
    lpa = lpa[~((lpa["ticker"] == tickers[4]) & (lpa["ano"] == 2019))]
    fech = fech[~((fech["ticker"] == tickers[5]) & (fech["data"] == meses[1]))]
    m = pv.mercado_b3(fech, vol, lpa, ano_inicial=2020)
    assert m["serie"][0] == ["2020-03-31", 20.0, 5.0, 25]
    assert m["serie"][1][:2] == ["2020-04-30", 10.0]
    u = m["ultimo"]
    assert u["exercicio"] == 2019 and u["validas"] == 20 and u["negativas"] == 1
    assert u["implausiveis"] == tickers[:3]
    assert u["sem_lpa"] == [tickers[4]] and u["sem_preco"] == [tickers[5]]


def test_mes_com_poucas_validas_fica_fora_da_serie():
    tickers = _nomes(pv.MERCADO_MIN_VALIDAS - 1)
    fech, vol, lpa = _quadros(tickers, meses=[date(2020, 5, 29)])
    assert pv.mercado_b3(fech, vol, lpa, ano_inicial=2020) == {"serie": [], "ultimo": None}


def _mercado(pls, *, ultimo=None):
    serie = [[f"{2016 + i // 12}-{i % 12 + 1:02d}-28", pl, 100 / pl, 40]
             for i, pl in enumerate(pls)]
    return {"serie": serie, "ultimo": ultimo or {
        "data": serie[-1][0], "exercicio": 2025, "universo": 50, "validas": 39,
        "negativas": 3, "sem_lpa": ["VALE3", "BBAS3"], "sem_preco": [],
        "implausiveis": ["VIVA3"]}}


def _juro(valor):
    return tm.Resumo(data=date(2026, 10, 7), valor=valor, atras={}, inicio=date(2026, 9, 8),
                     n=22, minimo=valor, maximo=valor, percentil=50.0)


def test_linha_cola_a_ressalva_ao_percentil_e_nomeia_quem_ficou_fora():
    pls = [15.0] * 100 + [20.0]
    linhas = tm.linhas_valuation_b3(_mercado(pls), {"TIPCA2035": _juro(7.0)},
                                    origem="gerado em 08/10/2026", hoje=date(2024, 5, 30))
    assert "20,0x em 28/05/2024" in linhas[0]
    assert ("hoje no percentil 100 de 8,3 anos, mediana das 50 mais negociadas, "
            "NÃO o P/L do Ibovespa") in linhas[0]
    assert "39 de 50 ações na conta, 3 com prejuízo" in linhas[1]
    assert "sem LPA do exercício 2025 na base: VALE3, BBAS3" in linhas[1]
    assert "LPA em escala errada: VIVA3" in linhas[1]
    assert "lucro ÷ preço mediano 5,00% − Tesouro IPCA+ 2035" in linhas[2]
    assert "= -2,00 p.p. (mediana das 50 mais negociadas, NÃO o Ibovespa" in linhas[2]


def test_premio_cai_para_o_juro_real_mais_curto_e_avisa_sem_juro():
    m = _mercado([10.0] * 30)
    so_2029 = tm.linhas_valuation_b3(m, {"TIPCA2029": _juro(6.0)})
    assert "Tesouro IPCA+ 2029" in so_2029[2] and "= +4,00 p.p." in so_2029[2]
    assert "sem o juro real" in tm.linhas_valuation_b3(m, {})[2]


def test_arquivo_sem_a_serie_e_leitor_que_quebra_sao_nomeados():
    assert "sem a série mercado_b3" in tm.linhas_valuation_b3({}, {})[0]

    def _quebra():
        raise RuntimeError("gz ruim")

    texto = "\n".join(tm.linhas_trajetoria(valuation=_quebra, hoje=date(2026, 10, 8)))
    assert "P/L mediano das 50 ações mais negociadas da B3 (ponto-no-tempo): " \
           "falha na leitura (gz ruim)" in texto
    texto = "\n".join(tm.linhas_trajetoria(
        valuation=lambda: (None, "arquivo ausente"), hoje=date(2026, 10, 8)))
    assert "(ponto-no-tempo): indisponível (arquivo ausente)" in texto


def test_contexto_le_a_serie_do_arquivo_publicado(monkeypatch):
    import core.inteligencia_ativos.fontes_valuation as fv

    monkeypatch.setattr(fv, "arquivo", lambda: {"gerado_em": "2026-10-08T22:00:00+00:00",
                                                "mercado_b3": {"serie": []}})
    assert cm._valuation_publicado() == ({"serie": []}, "gerado em 08/10/2026")
    monkeypatch.setattr(fv, "arquivo", lambda: {"gerado_em": "x", "b3": {}})
    assert "ainda sem a série mercado_b3" in cm._valuation_publicado()[1]
    monkeypatch.setattr(fv, "arquivo", dict)
    assert "ausente ou ilegível" in cm._valuation_publicado()[1]


def test_serie_mensal_diz_a_data_quando_o_ponto_fica_longe_do_alvo():
    s = tm.Serie("BOVA11", "x", "preco", "R$ ", mensal=True, atraso_max=10)
    serie = {date(2026, 7, 31): 170.0, date(2026, 8, 31): 175.0,
             date(2026, 9, 30): 184.0, date(2026, 10, 7): 204.0}
    texto = tm.linha(s, tm.resumir(serie), hoje=date(2026, 10, 8))
    assert "há 1 mês R$ 175,00 em 31/08/2026 (+16,6%)" in texto
    assert "ÚLTIMO PONTO" not in texto
    assert "ÚLTIMO PONTO HÁ 11 DIAS" in tm.linha(s, tm.resumir(serie), hoje=date(2026, 10, 18))


def test_sql_roda_no_armazem_de_verdade():
    """As consultas só se provam executando; só leitura, pulado sem armazém."""
    try:
        from sqlalchemy import create_engine, text

        from scripts.publish_fii_selection_from_local import _warehouse_url
        engine = create_engine(_warehouse_url())
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as erro:  # noqa: BLE001
        pytest.skip(f"sem armazem local ({erro})")
    try:
        with engine.connect() as conn:
            mercado = pv.coletar_mercado_b3(conn)
            # o espelho local do Supabase tem market.historical_prices
            hist = tm.resumos_sql(conn, tm._FONTE_HISTORICO, tm.HISTORICO)
    finally:
        engine.dispose()
    assert len(mercado["serie"]) > 120
    assert all(1 < pl < 60 for _, pl, _, _ in mercado["serie"] if pl)
    assert mercado["ultimo"]["universo"] == pv.UNIVERSO_MERCADO
    assert hist["BOVA11"].n > 100 and hist["BOVA11"].inicio.year <= 2017
