from datetime import date

import pandas as pd
import pytest

import core.carteira20_desempenho as cd
import core.carteira20_medicao as cm

EXEC = date(2026, 10, 26)
D1 = date(2026, 10, 27)
NOV = date(2026, 11, 30)
D2 = date(2026, 12, 1)
DEZ = date(2026, 12, 30)
TICKERS = {"A": "AAAA3", "B": "BBBB4", "C": "CCCC3", "D": "DDDD3"}
IPCA = {date(2026, 10, 1): 0.5, date(2026, 11, 1): 0.3, date(2026, 12, 1): 0.4}


def _pregoes(linhas):
    """(id, data, preço) ou (id, data, preço, k, caixa)."""
    return pd.DataFrame([(*x, 1.0, 0.0) if len(x) == 3 else x for x in linhas],
                        columns=["id", "data", "preco", "k", "caixa"])


def _base(precos, dias, mult=None):
    mult = mult or {}
    return [(i, d, p * mult.get((i, d), 1.0)) for i, p in precos.items() for d in dias]


def _rev(carteira=("A", "B"), universo=("A", "B", "C"), custos=None, seq=1, execucao=EXEC):
    return cm.Revisao(seq, execucao, list(carteira), list(universo),
                      custos if custos is not None else {i: (0.0, 0.0) for i in carteira},
                      (0.0, 0.0))


def _cdi(*dias, taxa=0.05):
    return {d: taxa for d in dias}


def test_fechamentos_comecam_no_mes_seguinte_ao_da_execucao():
    assert cm.fechamentos(EXEC, date(2027, 1, 31)) == [NOV, DEZ, date(2027, 1, 29)]
    # 30/10/2026 é o último pregão de outubro, mas o primeiro mês fecha em 30/11
    assert date(2026, 10, 30) not in cm.fechamentos(EXEC, NOV)
    assert cm.fechamentos(EXEC, date(2026, 11, 29)) == []


def test_fator_ipca_pro_rata_no_mes_da_execucao_e_vazio_sem_divulgacao():
    f = cm.fator_ipca(EXEC, NOV, IPCA)
    assert f == pytest.approx(1.005 ** (5 / 31) * 1.003)
    assert cm.fator_ipca(EXEC, date(2027, 1, 29), IPCA) is None


def test_teorica_e_a_curva_diaria_no_fechamento():
    """Sem revisão no meio, a curva teórica no fechamento é a curva diária naquele dia."""
    precos = {"A": 10.0, "B": 20.0, "C": 40.0}
    mult = {("A", D1): 1.02, ("B", D1): 0.97, ("C", D1): 1.10,
            ("A", NOV): 1.05, ("B", NOV): 0.95, ("C", NOV): 1.20}
    linhas = _base(precos, [EXEC, D1, NOV], mult)
    # provento de B em 30/11: R$ 0,50 por ação, reinvestido na teórica
    linhas = [(i, d, p, 1.0, 0.5 if (i, d) == ("B", NOV) else 0.0) for i, d, p in linhas]
    pregoes = _pregoes(linhas)
    custos = {"A": (0.002, 0.001), "B": (0.001, 0.003)}
    bova = pd.Series({EXEC: 100.0, D1: 101.0, NOV: 104.0})
    cdi = _cdi(EXEC, D1)
    r = cm.calcular([_rev(custos=custos)], pregoes, bova, cdi, IPCA, ate=NOV, tickers=TICKERS)
    assert [m["fechamento"] for m in r["meses"]] == ["2026-11-30"]
    teo = r["meses"][0]["teorica"]

    p = pregoes.sort_values(["id", "data"]).copy()
    p["p_ant"] = p.groupby("id")["preco"].shift(1)
    p["ret"] = (p["k"] * p["preco"] + p["caixa"]) / p["p_ant"] - 1
    ret = p.dropna(subset=["ret"])[["id", "data", "ret"]]
    carteira = [{"id": "A", "ticker": "AAAA3"}, {"id": "B", "ticker": "BBBB4"}]
    universo = carteira + [{"id": "C", "ticker": "CCCC3"}]
    diaria = cd.calcular(EXEC, ret, bova, cdi, carteira, universo, precos=precos,
                         custos=custos, custo_bova11=(0.0, 0.0))
    for nome in ("carteira20", "pesos_iguais", "bova11", "cdi"):
        assert teo[nome]["liquido"] == pytest.approx(diaria["curvas"][nome][-1], abs=0.01), nome


def test_aporte_entra_depois_da_medicao_e_vale_para_todos():
    precos = {"A": 10.0, "B": 20.0, "C": 40.0}
    pregoes = _pregoes(_base(precos, [EXEC, D1, NOV, D2, DEZ]))
    bova = pd.Series({EXEC: 100.0, D1: 100.0, NOV: 100.0, D2: 100.0, DEZ: 100.0})
    cdi = _cdi(EXEC, D1, NOV, D2, taxa=0.0)
    r = cm.calcular([_rev()], pregoes, bova, cdi, IPCA, ate=DEZ)
    nov, dez = r["meses"]
    assert nov["aportes"]["aportado"] == 1000.0 and dez["aportes"]["aportado"] == 1500.0
    assert dez["teorica"]["aportado"] == 1000.0
    # CDI parado: o resgate devolve exatamente o aportado
    assert dez["aportes"]["cdi"]["liquido"] == 1500.0
    # BOVA11 a R$ 100,30 com a corretagem: 9 cotas na execução, o troco de 97,30
    # mais os 500 compram mais 5 em 30/11
    bova_dez = dez["aportes"]["bova11"]
    assert bova_dez["caixa"] == pytest.approx(97.3 + 500 - 5 * 100.3, abs=0.01)
    assert bova_dez["patrimonio"] == pytest.approx(14 * 100 + bova_dez["caixa"], abs=0.01)
    # a Carteira 20 gastou o aporte em ações inteiras, sem venda
    c = dez["aportes"]["carteira20"]
    assert c["patrimonio"] > 1490 and c["caixa"] < 20 and c["ir_pago"] == 0
    assert {p["id"] for p in r["posicoes_aportes"]} == {"A", "B"}
    assert r["proximo_fechamento"] == "2027-01-29"


def test_provento_cai_em_caixa_nos_aportes_e_volta_para_a_acao_na_teorica():
    linhas = [("A", EXEC, 10.0), ("B", EXEC, 10.0), ("C", EXEC, 10.0),
              ("A", NOV, 9.0, 1.0, 1.0), ("B", NOV, 10.0), ("C", NOV, 10.0)]
    bova = pd.Series({EXEC: 100.0, NOV: 100.0})
    r = cm.calcular([_rev()], _pregoes(linhas), bova, _cdi(EXEC), IPCA, ate=NOV)
    ap, teo = r["meses"][0]["aportes"], r["meses"][0]["teorica"]
    qa = cd.comprar_em_lotes({"A": 10.03, "B": 10.03}, 1000.0)[0]["A"]  # a compra da inaugural
    assert ap["carteira20"]["proventos"] == pytest.approx(qa * 1.0)
    assert teo["carteira20"]["proventos"] == pytest.approx(qa * 1.0)
    # mesmo dinheiro: na teórica virou ação (9 + 1 = 10 por antiga), nos aportes, caixa
    assert ap["carteira20"]["patrimonio"] == pytest.approx(teo["carteira20"]["patrimonio"], abs=0.01)
    assert ap["carteira20"]["caixa"] - teo["carteira20"]["caixa"] == pytest.approx(qa * 1.0)


def test_desdobramento_multiplica_as_acoes_sem_mudar_o_valor():
    linhas = [("A", EXEC, 10.0), ("B", EXEC, 10.0), ("C", EXEC, 10.0),
              ("A", NOV, 5.0, 2.0, 0.0), ("B", NOV, 10.0), ("C", NOV, 10.0)]
    bova = pd.Series({EXEC: 100.0, NOV: 100.0})
    r = cm.calcular([_rev()], _pregoes(linhas), bova, _cdi(EXEC), IPCA, ate=NOV)
    sem = cm.calcular([_rev()], _pregoes([(i, d, 10.0) for i in "ABC" for d in (EXEC, NOV)]),
                      bova, _cdi(EXEC), IPCA, ate=NOV)
    for curva in ("aportes", "teorica"):
        assert (r["meses"][0][curva]["carteira20"]["liquido"]
                == sem["meses"][0][curva]["carteira20"]["liquido"])


def test_ir_por_preco_medio_isencao_e_prejuizo():
    c = cm.Carteira(reinveste=False)
    c.caixa = 100_000.0
    c.comprar("A", 1000, 10.0)
    c.comprar("A", 1000, 20.0)            # preço médio 15 (mais a corretagem)
    c.vender("A", 100, 30.0)              # R$ 3 mil vendidos: isento
    c.fechar_mes()
    assert c.ir_pago == 0 and c.prejuizo == 0
    c.vender("A", 1900, 5.0)              # R$ 9,5 mil vendidos com prejuízo: acumula
    c.fechar_mes()
    perda = c.prejuizo
    assert perda > 0 and c.ir_pago == 0
    c.comprar("B", 2000, 10.0)
    c.vender("B", 2000, 20.0)             # R$ 40 mil vendidos: tributado, com o prejuízo abatido
    ganho = c._ganho_mes
    c.fechar_mes()
    assert c.ir_pago == pytest.approx(0.15 * max(0.0, ganho - perda))
    assert c.prejuizo == pytest.approx(max(0.0, perda - ganho))


def test_isento_nao_gasta_o_prejuizo_acumulado():
    c = cm.Carteira(reinveste=False, prejuizo=500.0)
    c.caixa = 10_000.0
    c.comprar("A", 100, 10.0)
    c.vender("A", 100, 15.0)
    c.fechar_mes()
    assert c.ir_pago == 0 and c.prejuizo == 500.0


def test_etf_paga_ir_no_resgate_sem_isencao():
    c = cm.Carteira(reinveste=False, isento_ate=0.0)
    c.caixa = 1000.0
    cm.comprar_cotas(c, 100.0, 0.0)
    venda = 9 * 110 * (1 - cm.CUSTO_LADO)
    esperado = venda - 0.15 * (venda - 9 * 100.3) + c.caixa
    assert c.liquidacao({cm.BOVA11: 110.0}, {}) == pytest.approx(esperado)


def test_revisao_vende_o_que_saiu_e_iguala_as_vinte():
    abr = date(2027, 4, 30)
    mai = date(2027, 5, 3)
    dias = [EXEC, NOV, DEZ, date(2027, 1, 29), date(2027, 2, 26), date(2027, 3, 31), abr, mai]
    precos = {"A": 10.0, "B": 10.0, "C": 10.0, "D": 10.0}
    mult = {("A", d): 2.0 for d in dias[1:]}  # A dobra e passa do peso
    pregoes = _pregoes(_base(precos, dias, mult))
    bova = pd.Series({d: 100.0 for d in dias})
    cdi = _cdi(*dias, taxa=0.0)
    nova = _rev(carteira=("A", "C"), universo=("A", "C", "D"), seq=2, execucao=mai)
    r = cm.calcular([_rev(), nova], pregoes, bova, cdi, IPCA | {date(2027, m, 1): 0.0
                                                                   for m in range(1, 6)},
                    ate=mai)
    abril = r["meses"][-1]
    assert abril["fechamento"] == "2027-04-30" and not r["avisos"]
    pos = {p["id"]: p for p in r["posicoes_aportes"]}
    assert set(pos) == {"A", "C"}                       # B saiu
    assert pos["A"]["valor"] == pytest.approx(pos["C"]["valor"], rel=0.02)


def test_fechamento_sem_pregao_na_fonte_encerra_a_conta():
    linhas = [("A", EXEC, 10.0), ("B", EXEC, 10.0), ("C", EXEC, 10.0),
              ("A", NOV, 10.0), ("C", NOV, 10.0), ("B", D1, 10.0)]
    bova = pd.Series({EXEC: 100.0, D1: 100.0, NOV: 100.0})
    r = cm.calcular([_rev()], _pregoes(linhas), bova, _cdi(EXEC, D1), IPCA, ate=NOV,
                    tickers=TICKERS)
    assert r["meses"] == []
    assert any("30/11/2026 ainda sem o pregão de BBBB4" in a for a in r["avisos"])


def test_universo_sem_pregao_esvazia_so_os_pesos_iguais():
    linhas = [("A", EXEC, 10.0), ("B", EXEC, 10.0), ("C", EXEC, 10.0),
              ("A", NOV, 10.0), ("B", NOV, 10.0), ("C", D1, 10.0)]
    bova = pd.Series({EXEC: 100.0, D1: 100.0, NOV: 100.0})
    r = cm.calcular([_rev()], _pregoes(linhas), bova, _cdi(EXEC, D1), IPCA, ate=NOV,
                    tickers=TICKERS)
    mes = r["meses"][0]
    assert mes["aportes"]["pesos_iguais"] is None and mes["teorica"]["pesos_iguais"] is None
    assert mes["aportes"]["carteira20"]["liquido"] > 0
    assert any("pesos iguais sem medição" in a and "CCCC3" in a for a in r["avisos"])


def test_cdi_sem_taxa_para_a_medicao():
    pregoes = _pregoes(_base({"A": 10.0, "B": 10.0, "C": 10.0}, [EXEC, D1, NOV]))
    bova = pd.Series({EXEC: 100.0, D1: 100.0, NOV: 100.0})
    r = cm.calcular([_rev()], pregoes, bova, _cdi(EXEC), IPCA, ate=NOV)
    assert r["meses"] == []
    assert any("CDI sem a taxa de 27/10/2026" in a for a in r["avisos"])


def test_mes_sem_ipca_divulgado_fica_sem_valor_real():
    pregoes = _pregoes(_base({"A": 10.0, "B": 10.0, "C": 10.0}, [EXEC, NOV]))
    bova = pd.Series({EXEC: 100.0, NOV: 100.0})
    r = cm.calcular([_rev()], pregoes, bova, _cdi(EXEC), {date(2026, 10, 1): 0.5}, ate=NOV)
    mes = r["meses"][0]
    assert mes["ipca_fator"] is None and mes["aportes"]["aportado_real"] is None


def test_componentes_da_extensao_refazem_o_retorno():
    from core import carteira20_retornos as cr

    linhas = []
    for k, dia in enumerate(pd.bdate_range("2026-09-01", "2026-10-30")):
        for t in ("AAAA3", *(f"X{n:03d}3" for n in range(25))):
            close = 10.0 * (1 + 0.001 * k)
            if t == "AAAA3" and dia >= pd.Timestamp("2026-10-20"):
                close *= 5.5          # salto sem evento: retorno vazio
            linhas.append({"ticker": t, "trade_date": dia, "bdi": "02",
                           "specification": "ON NM", "close": close, "fator_cotacao": 1})
    pregoes = cr.pregoes_de_acoes(linhas, {x["ticker"] for x in linhas})
    ids = {t: t for t in pregoes["ticker"].unique()}
    vazio = pd.DataFrame(columns=["ticker", "ex_date", "payment_date", "amount", "type"])
    r = cr.calcular(pregoes, vazio, ids, date(2026, 10, 1), componentes=True)
    assert list(r.columns[:4]) == ["id", "data", "ret", "suspeito"]
    r = r.sort_values(["id", "data"])
    r["p_ant"] = r.groupby("id")["preco"].shift(1)
    ok = r["ret"].notna() & r["p_ant"].notna()
    refeito = (r["k"] * r["preco"] + r["caixa"]) / r["p_ant"] - 1
    assert (refeito[ok] - r.loc[ok, "ret"]).abs().max() < 1e-12
    salto = r[r["ret"].isna() & r["p_ant"].notna()]
    assert len(salto) == 1
    assert (salto["k"] * salto["preco"]).iloc[0] == pytest.approx(salto["p_ant"].iloc[0])


# -- publicador ------------------------------------------------------------------

def _registros():
    import core.carteira20 as c20

    regs = c20.ler(c20.REGISTRO)
    if not c20.revisoes(regs):
        pytest.skip("registro sem revisão publicada")
    return regs


def _fontes(regs, dias=(EXEC, NOV)):
    import core.carteira20 as c20

    corpo = c20.revisoes(regs)[0]["corpo"]
    ids = {c["id"] for c in corpo["carteira"]} | {u["id"] for u in corpo["universo"]["pontuacoes"]}
    pregoes = _pregoes([(i, d, 20.0) for i in sorted(ids) for d in dias])
    return {"componentes": lambda ids_, desde: pregoes,
            "bova": lambda desde: pd.Series({d: 150.0 for d in dias}),
            "cdi": lambda ini, fim: ({d: 0.05 for d in dias}, "teste"),
            "lotes": lambda execucao, carteira: {"custos": {}, "custo_bova11": (0.0, 0.0)},
            "ipca": lambda ini, fim, publicado: (dict(IPCA), "teste")}


def test_montar_grava_protocolo_revisoes_e_ipca_usado():
    from datetime import datetime

    import core.carteira20 as c20
    from scripts import carteira20_medicao as pub

    regs = _registros()
    art = pub.montar(regs, agora=datetime(2026, 12, 2, 20, tzinfo=c20.FUSO_B3), **_fontes(regs))
    assert art["protocolo"]["hash"] == regs[0]["hash"]
    assert [r["seq"] for r in art["revisoes"]] == [r["registro"]["seq"]
                                                    for r in c20.revisoes(regs)]
    assert [m["fechamento"] for m in art["meses"]] == ["2026-11-30"]
    assert art["ipca"]["2026-11-01"] == 0.3
    assert art["regras"]["aporte_mensal"] == 500.0
    # preço parado e CDI de dois dias: a Carteira 20 perde só os custos
    assert art["meses"][0]["teorica"]["carteira20"]["liquido"] < 1000.0


def test_antes_da_inaugural_nao_ha_o_que_medir():
    from datetime import datetime

    import core.carteira20 as c20
    from scripts import carteira20_medicao as pub

    regs = _registros()
    assert pub.montar(regs, agora=datetime(2026, 10, 26, 12, tzinfo=c20.FUSO_B3),
                      **_fontes(regs)) is None


def test_rodada_que_mede_menos_meses_nao_grava(monkeypatch, tmp_path):
    from scripts import carteira20_medicao as pub

    destino = tmp_path / "medicao.json"
    destino.write_text('{"meses": [{"fechamento": "2026-11-30"}, {"fechamento": "2026-12-30"}]}',
                       encoding="utf-8")
    monkeypatch.setattr(pub, "ARTEFATO", destino)
    monkeypatch.setattr(pub, "montar", lambda regs, **kw: {
        "meses": [{"fechamento": "2026-11-30", "aportes": {k: None for k in cm.CARTEIRAS}
                   | {"aportado": 1000.0}}], "avisos": [], "proximo_fechamento": "2026-12-30"})
    with pytest.raises(SystemExit, match="tem 2 fechamento"):
        pub.main([])
    assert "2026-12-30" in destino.read_text(encoding="utf-8")


def test_componentes_da_pesquisa_refazem_o_retorno_total(monkeypatch, tmp_path):
    from scripts import carteira20_medicao as pub

    df = pd.DataFrame({
        "id_ativo": ["A", "A", "A", "A"],
        "data": pd.to_datetime(["2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"]),
        "ret_total": [None, 0.10, None, (2 * 6.0 + 0.4) / 11.0 - 1],
        "preco_fechamento": [10.0, 11.0, 11.0 * 5.5, 6.0],
        "fator_acoes": [None, None, None, 2.0],
        "distribuicao_especie": [None, None, None, 0.0],
        "provento_dinheiro": [None, None, None, 0.4],
        "provento_imputado": [None, None, None, 0.0],
    })
    arq = tmp_path / "b3_retornos_diarios.parquet"
    df.to_parquet(arq)
    monkeypatch.setattr(pub.diario, "_parquet_pesquisa", lambda: arq)
    c = pub.componentes_da_pesquisa({"A"}, date(2026, 10, 1)).set_index("data")
    # dia de retorno vazio: valor parado (k compensa o salto), sem dinheiro
    assert c.loc[date(2026, 10, 22), "k"] * c.loc[date(2026, 10, 22), "preco"] == pytest.approx(11.0)
    assert c.loc[date(2026, 10, 22), "caixa"] == 0.0
    assert c.loc[date(2026, 10, 23), "k"] == 2.0 and c.loc[date(2026, 10, 23), "caixa"] == 0.4


def test_ipca_mantem_o_publicado_e_busca_so_o_que_falta():
    from scripts import carteira20_medicao as pub

    pedidos = []

    def baixar(ini, fim):
        pedidos.append((ini, fim))
        return {date(2026, 11, 1): 0.31}, None

    serie, fonte = pub.ipca_mensal(EXEC, date(2026, 12, 15),
                                   publicado={date(2026, 10, 1): 0.5}, baixar=baixar)
    assert serie[date(2026, 10, 1)] == 0.5 and serie[date(2026, 11, 1)] == 0.31
    assert date(2026, 12, 1) not in serie
    assert pedidos and "medição publicada" in fonte
