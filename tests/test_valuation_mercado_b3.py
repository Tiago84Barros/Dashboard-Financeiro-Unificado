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


def _quadros(tickers, *, meses, precos=None, lpas=None, vol_ano=2019,
             anos=range(2015, 2021)):
    """Fita mensal, volume do ano anterior e LPA anual de um universo sintético."""
    precos = precos or {}
    lpas = lpas or {}
    fech = pd.DataFrame([{"ticker": tk, "data": d, "close": precos.get((tk, d), 10.0)}
                         for tk in tickers for d in meses])
    vol = pd.DataFrame([{"ticker": tk, "ano": vol_ano, "vol": 1000.0 - i}
                        for i, tk in enumerate(tickers)])
    lpa = pd.DataFrame([{"ticker": tk, "ano": ano, "lpa": lpas.get((tk, ano), 1.0)}
                        for tk in tickers for ano in anos])
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
    lpas[(tickers[2], 2019)] = -20.0                    # prejuízo maior que o preço: entra
    lpas[(tickers[3], 2019)] = -1.0                     # prejuízo menor: entra
    del lpas[(tickers[4], 2019)]
    fech, vol, lpa = _quadros(tickers, meses=meses, lpas=lpas)
    lpa = lpa[~((lpa["ticker"] == tickers[4]) & (lpa["ano"] == 2019))]
    fech = fech[~((fech["ticker"] == tickers[5]) & (fech["data"] == meses[1]))]
    m = pv.mercado_b3(fech, vol, lpa, ano_inicial=2020)
    assert m["serie"][0] == ["2020-03-31", 20.0, 5.0, 25]
    assert m["serie"][1][:2] == ["2020-04-30", 10.0]
    u = m["ultimo"]
    assert u["exercicio"] == 2019 and u["validas"] == 21 and u["negativas"] == 2
    assert u["implausiveis"] == tickers[:2]
    assert u["sem_lpa"] == [tickers[4]] and u["sem_preco"] == [tickers[5]]
    assert u["rebaseadas"] == {}


def test_prejuizo_maior_que_o_preco_entra_e_so_o_sinal_pesa():
    # CSAN3 2025: prejuízo real maior que o preço caía como "escala errada" e
    # puxava a mediana para cima. Na mediana do lucro ÷ preço só pesam o sinal
    # e a ordem do prejuízo; LPA 1000x continua caindo pelo lado positivo.
    tickers = _nomes(21)
    meses = [date(2020, 5, 29)]
    series = []
    for prejuizo in (-1.0, -30.0):
        lpas = {(tk, 2019): (prejuizo if i < 10 else 1.0)
                for i, tk in enumerate(tickers)}
        m = pv.mercado_b3(*_quadros(tickers, meses=meses, lpas=lpas), ano_inicial=2020)
        assert m["ultimo"]["validas"] == 21 and m["ultimo"]["negativas"] == 10
        assert m["ultimo"]["implausiveis"] == []
        series.append(m["serie"])
    assert series[0] == series[1] == [["2020-05-29", 10.0, 10.0, 21]]


def _evento(data, fator):
    from data_pipeline.market.b3_saidas import EventoCapital
    return EventoCapital(pd.Timestamp(data), fator, "marcador_b3")


def test_desdobramento_depois_da_dfp_divide_o_lpa():
    # SBSP3: LPA 2025 de 12,37 na base de 683 milhões de ações; o 1:5 de
    # 29/04/2026 levou o preço a 32,99 e, sem rebase, o P/L a 2,7 -- dentro
    # da faixa, então ninguém o pegava. Aqui o universo inteiro desdobra.
    tickers = _nomes(25)
    meses = [date(2020, 4, 30), date(2020, 5, 29)]
    precos = {(tk, meses[1]): 2.0 for tk in tickers}
    fech, vol, lpa = _quadros(tickers, meses=meses, precos=precos)
    sem = pv.mercado_b3(fech, vol, lpa, ano_inicial=2020)
    assert [p[1] for p in sem["serie"]] == [10.0, 2.0]
    eventos = {tk: [_evento("2020-05-15", 5.0)] for tk in tickers}
    com = pv.mercado_b3(fech, vol, lpa, eventos=eventos, ano_inicial=2020)
    assert [p[1] for p in com["serie"]] == [10.0, 10.0]
    assert com["ultimo"]["rebaseadas"] == {tk: 5.0 for tk in tickers}


def test_rebase_so_pega_o_evento_entre_a_dfp_e_o_pregao():
    # ASAI3: 1:5 em 12/08/2021. O LPA de 2020 (DFP de fev/2021) vem na base
    # velha e é dividido de agosto/2021 a março/2022; o de 2021 (DFP de
    # 2022) já sai na base nova, e de abril/2022 em diante nada se divide.
    # Evento até 31/03 do ano seguinte ao exercício conta como já na DFP.
    tickers = ["ASAI3"] + _nomes(20)
    meses = [date(2021, 3, 31), date(2021, 7, 30), date(2021, 8, 31),
             date(2022, 3, 31), date(2022, 4, 29)]
    fech, _, lpa = _quadros(tickers, meses=meses, anos=range(2018, 2022))
    vol = pd.DataFrame([{"ticker": tk, "ano": ano, "vol": 1000.0 - i}
                        for ano in (2020, 2021) for i, tk in enumerate(tickers)])
    # o 2:1 de março/2021 vale para o exercício 2019, não para o 2020
    eventos = {"ASAI3": [_evento("2021-08-12", 5.0)],
               tickers[1]: [_evento("2021-03-15", 2.0)]}

    def rebaseadas(d):
        m = pv.mercado_b3(fech[fech["data"] == d], vol, lpa, eventos=eventos,
                          ano_inicial=2021)
        return m["ultimo"]["exercicio"], m["ultimo"]["rebaseadas"]

    assert rebaseadas(meses[0]) == (2019, {tickers[1]: 2.0})
    assert rebaseadas(meses[1]) == (2020, {})
    assert rebaseadas(meses[2]) == (2020, {"ASAI3": 5.0})
    assert rebaseadas(meses[3]) == (2020, {"ASAI3": 5.0})
    assert rebaseadas(meses[4]) == (2021, {})


def test_desdobramentos_le_a_marca_da_b3():
    marcas = pd.DataFrame([
        # SBSP3: 1:5 em 29/04/2026; a marca dura dias e volta depois de um ATZ
        ("SBSP3", "2026-04-29", "ON  EB  NM", 32.99, 167.00),
        ("SBSP3", "2026-04-30", "ON  EB  NM", 33.40, 32.99),
        ("SBSP3", "2026-05-06", "ON  EB  NM", 33.10, 32.20),
        # marca intermitente num pregão de 5%: não é bonificação de 5%
        ("SBSP3", "2025-12-26", "ON  EJB NM", 100.00, 105.20),
        # marca de juros 29 dias antes não esconde o 1:5 (o dado de 2026)
        ("SBSP3", "2026-03-31", "ON  EJB NM", 154.10, 158.24),
        ("MGLU3", "2024-05-27", "ON  EG  NM", 13.00, 1.30),       # 10:1
        # dia seguinte de +85% ainda marcado: o evento já foi aceito
        ("MGLU3", "2024-05-28", "ON  EG  NM", 2.40, 1.30),
        ("HAPV3", "2025-06-06", "ON  EG  NM", 39.91, 2.70),       # 15:1, sem redondo
        ("CPFE3", "2011-06-29", "ON  EBG NM", 22.22, 44.50),      # B e G juntos
        ("WEGE3", "2018-04-02", "ON  EB  NM", 30.00, 40.00),      # bonificação 1,33
        ("XPTO3", "2020-01-02", "ON  EB  NM", 10.00, 17.00),      # salto 1,7 sem redondo
        # bonificação marcada até 19 dias antes do grupamento 3:1
        ("CIEL3", "2011-05-11", "ON  EB  NM", 13.16, 13.27),
        ("CIEL3", "2011-05-30", "ON  EG  NM", 39.00, 13.10),
    ], columns=["ticker", "data", "espec", "close", "anterior"])
    ev = {tk: [(e.data.date().isoformat(), round(e.fator, 4), e.status) for e in lst]
          for tk, lst in pv.desdobramentos(marcas).items()}
    assert ev == {
        "SBSP3": [("2026-04-29", 5.0, "marcador_b3")],
        "MGLU3": [("2024-05-27", 0.1, "marcador_b3")],
        "HAPV3": [("2025-06-06", 0.0677, "marcador_b3")],
        "CPFE3": [("2011-06-29", 2.0, "marcador_b3")],
        "CIEL3": [("2011-05-30", 0.3333, "marcador_b3")],
    }
    assert pv.desdobramentos(marcas.iloc[:0]) == {}


def test_mes_com_poucas_validas_fica_fora_da_serie():
    tickers = _nomes(pv.MERCADO_MIN_VALIDAS - 1)
    fech, vol, lpa = _quadros(tickers, meses=[date(2020, 5, 29)])
    m = pv.mercado_b3(fech, vol, lpa, ano_inicial=2020)
    assert m["serie"] == [] and m["ultimo"] is None
    assert m["descartado"]["data"] == "2020-05-29"
    assert m["descartado"]["validas"] == pv.MERCADO_MIN_VALIDAS - 1


def _lpa_mil_vezes(tickers, n, ano=2019):
    """LPA de ``n`` tickers 1000x maior: o que o normalizador velho gravou."""
    lpas = {(tk, ano): 1.0 for tk in tickers}
    lpas.update({(tk, ano): 1000.0 for tk in tickers[:n]})
    return lpas


def test_mes_recente_fora_da_serie_e_nomeado_e_recusa_publicar():
    # 26-27/10/2026: demonstrações regravadas com o LPA 1000x tiraram de abril
    # em diante da série calculada do armazém, e `ultimo` seguia descrevendo
    # março -- ponto parado em silêncio por sete meses.
    tickers = _nomes(30)
    meses = [date(2020, 3, 31), date(2020, 4, 30), date(2020, 5, 29)]
    fech, vol, lpa = _quadros(tickers, meses=meses, lpas=_lpa_mil_vezes(tickers, 15))
    m = pv.mercado_b3(fech, vol, lpa, ano_inicial=2020)
    assert [p[0] for p in m["serie"]] == ["2020-03-31"]
    assert m["ultimo"]["data"] == "2020-03-31"
    d = m["descartado"]
    assert d["data"] == "2020-05-29" and d["validas"] == 15
    assert d["implausiveis"] == tickers[:15]
    motivo = pv.motivo_para_recusar("2020-05-29", m, date(2020, 6, 1))
    assert "2020-05-29 ficou fora da série" in motivo
    assert "15 de 30 ações válidas" in motivo
    assert "a série pararia em 2020-03-31" in motivo


def test_implausiveis_demais_no_ultimo_mes_recusa_publicar():
    tickers = _nomes(50)
    n = pv.MERCADO_IMPLAUSIVEIS_MAX + 1
    fech, vol, lpa = _quadros(tickers, meses=[date(2020, 5, 29)],
                              lpas=_lpa_mil_vezes(tickers, n))
    m = pv.mercado_b3(fech, vol, lpa, ano_inicial=2020)
    assert m["descartado"] is None and m["ultimo"]["validas"] == 50 - n
    motivo = pv.motivo_para_recusar("2020-05-29", m, date(2020, 6, 1))
    assert f"{n} ações com LPA em escala errada em 2020-05-29" in motivo


def test_mes_normal_publica_e_fita_parada_recusa():
    tickers = _nomes(50)
    fech, vol, lpa = _quadros(tickers, meses=[date(2020, 5, 29)],
                              lpas=_lpa_mil_vezes(tickers, pv.MERCADO_IMPLAUSIVEIS_MAX))
    m = pv.mercado_b3(fech, vol, lpa, ano_inicial=2020)
    assert pv.motivo_para_recusar("2020-05-29", m, date(2020, 6, 1)) is None
    assert "fita da B3 parada" in pv.motivo_para_recusar(
        "2020-05-29", m, date(2020, 5, 29) + pd.Timedelta(days=pv.FITA_MAXIMA_DIAS + 1))
    assert "fita da B3 parada" in pv.motivo_para_recusar(None, m, date(2020, 6, 1))
    assert "série vazia" in pv.motivo_para_recusar(
        "2020-05-29", {"serie": [], "ultimo": None, "descartado": None}, date(2020, 6, 1))


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
