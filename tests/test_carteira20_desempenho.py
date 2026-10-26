from datetime import date, datetime

import pandas as pd
import pytest

import core.carteira20 as c20
import core.carteira20_desempenho as cd
from scripts import carteira20_desempenho as pub

EXEC = date(2026, 10, 26)
DIAS = [date(2026, 10, 27), date(2026, 10, 28), date(2026, 10, 29)]
CARTEIRA = [{"id": "A", "ticker": "AAAA3"}, {"id": "B", "ticker": "BBBB4"}]
UNIVERSO = CARTEIRA + [{"id": "C", "ticker": "CCCC3"}]


def _retornos(linhas):
    return pd.DataFrame(linhas, columns=["id", "data", "ret"])


def _bova(valores):
    return pd.Series(valores, index=[EXEC, *DIAS][:len(valores)], dtype=float)


def _cdi(dias=(EXEC, *DIAS[:2]), taxa=0.05):
    return {d: taxa for d in dias}


def test_base_da_execucao_ja_desconta_a_compra():
    ret = _retornos([("A", DIAS[0], 0.0), ("B", DIAS[0], 0.0), ("C", DIAS[0], 0.0)])
    r = cd.calcular(EXEC, ret, _bova([100.0, 100.0]), _cdi(), CARTEIRA, UNIVERSO)
    assert r["pregoes"] == 1 and r["datas"] == ["2026-10-26", "2026-10-27"]
    venda = cd.CAPITAL * (1 - cd.CUSTO_LADO) ** 2
    assert r["curvas"]["carteira20"][0] == pytest.approx(venda, abs=1e-4)
    assert r["curvas"]["bova11"][0] == pytest.approx(venda, abs=1e-4)
    assert r["curvas"]["cdi"][0] == cd.CAPITAL


def test_buy_and_hold_pesos_iguais_e_ir_dos_comparadores():
    ret = _retornos([("A", DIAS[0], 0.10), ("B", DIAS[0], -0.10), ("C", DIAS[0], 0.40),
                     ("A", DIAS[1], 0.10), ("B", DIAS[1], 0.0), ("C", DIAS[1], 0.0)])
    r = cd.calcular(EXEC, ret, _bova([100.0, 110.0, 121.0]), _cdi(), CARTEIRA, UNIVERSO)
    por_acao = cd.CAPITAL * (1 - cd.CUSTO_LADO) / 2
    bruto = por_acao * 1.21 + por_acao * 0.9
    # abaixo de R$ 20 mil de venda no mês: ação isenta
    assert r["curvas"]["carteira20"][-1] == pytest.approx(bruto * (1 - cd.CUSTO_LADO), abs=1e-3)
    venda_etf = cd.CAPITAL * (1 - cd.CUSTO_LADO) ** 2 * 1.21
    assert r["curvas"]["bova11"][-1] == pytest.approx(
        venda_etf - (venda_etf - cd.CAPITAL) * cd.ALIQUOTA_IR, abs=1e-3)
    bruto_cdi = cd.CAPITAL * 1.0005 ** 2
    assert r["curvas"]["cdi"][-1] == pytest.approx(
        bruto_cdi - (bruto_cdi - cd.CAPITAL) * cd.ALIQUOTA_IR, abs=1e-3)
    ativos = {a["ticker"]: a for a in r["ativos"]}
    assert ativos["AAAA3"]["retorno"] == pytest.approx(0.21)
    assert ativos["AAAA3"]["peso_atual"] + ativos["BBBB4"]["peso_atual"] == pytest.approx(1.0)
    assert ativos["AAAA3"]["peso_atual"] == pytest.approx(1.21 / 2.11)


def test_isencao_so_vale_ate_vinte_mil():
    abaixo = cd._liquido_acoes(pd.Series([1100.0]))
    assert abaixo.iloc[0] == pytest.approx(1100 * (1 - cd.CUSTO_LADO))
    acima = cd._liquido_acoes(pd.Series([25_000.0]))
    venda = 25_000 * (1 - cd.CUSTO_LADO)
    assert acima.iloc[0] == pytest.approx(venda - (venda - cd.CAPITAL) * cd.ALIQUOTA_IR)


def test_lacuna_do_cdi_corta_as_curvas_e_avisa():
    ret = _retornos([(i, d, 0.01) for d in DIAS for i in "ABC"])
    r = cd.calcular(EXEC, ret, _bova([100.0, 101.0, 102.0, 103.0]), _cdi(dias=(EXEC,)),
                    CARTEIRA, UNIVERSO)
    assert r["pregoes"] == 1 and r["datas"][-1] == "2026-10-27"
    assert all(len(v) == 2 for v in r["curvas"].values())
    assert any("CDI sem a taxa de 27/10/2026; as curvas param em 27/10/2026" in a
               for a in r["avisos"])


def test_pregao_pulado_pelo_bova11_tambem_exige_cdi():
    # o intervalo 26/10 → 28/10 cobre o 27/10, que só existe no calendário das ações
    ret = _retornos([(i, d, 0.0) for d in DIAS[:2] for i in "ABC"])
    bova = pd.Series([100.0, 100.0], index=[EXEC, DIAS[1]], dtype=float)
    r = cd.calcular(EXEC, ret, bova, _cdi(dias=(EXEC, DIAS[1])), CARTEIRA, UNIVERSO)
    assert r["pregoes"] == 0
    assert any("CDI sem a taxa de 27/10/2026" in a for a in r["avisos"])


def test_acao_da_carteira_sem_dado_impede_a_curva():
    ret = _retornos([("A", DIAS[0], 0.05), ("C", DIAS[0], 0.0)])
    r = cd.calcular(EXEC, ret, _bova([100.0, 100.0]), _cdi(), CARTEIRA, UNIVERSO)
    assert r["pregoes"] == 0 and r["curvas"] == {} and r["ativos"] == []
    assert any("BBBB4" in a for a in r["avisos"])


def test_buraco_no_meio_e_dia_sem_negocio_mas_o_fim_da_fonte_corta_a_curva():
    # B não negocia em DIAS[1] (o retorno de DIAS[2] cobre o intervalo) e para
    # de aparecer depois de DIAS[2]; A segue até DIAS[3]
    dias = [*DIAS, date(2026, 10, 30)]
    ret = _retornos([("A", d, 0.01) for d in dias]
                    + [("B", DIAS[0], 0.0), ("B", DIAS[2], 0.02)]
                    + [("C", d, 0.0) for d in dias])
    bova = pd.Series(100.0, index=[EXEC, *dias], dtype=float)
    r = cd.calcular(EXEC, ret, bova, _cdi(dias=(EXEC, *dias)), CARTEIRA, UNIVERSO)
    assert r["datas"][-1] == "2026-10-29" and r["pregoes"] == 3
    assert any("BBBB4 sem negócio na fonte depois de 29/10/2026" in a for a in r["avisos"])
    assert {a["ticker"]: a["retorno"] for a in r["ativos"]}["BBBB4"] == pytest.approx(0.02)


def test_pregao_sem_bova11_nao_apaga_o_retorno_das_acoes():
    # BOVA11 sem fechamento em DIAS[0]: A sobe 10% nele e fica parada em DIAS[1]
    ret = _retornos([(i, DIAS[0], 0.10 if i == "A" else 0.0) for i in "ABC"]
                    + [(i, DIAS[1], 0.0) for i in "ABC"])
    bova = pd.Series([100.0, 103.0], index=[EXEC, DIAS[1]], dtype=float)
    r = cd.calcular(EXEC, ret, bova, _cdi(), CARTEIRA, UNIVERSO)
    assert r["datas"] == ["2026-10-26", "2026-10-28"]
    assert {a["ticker"]: a["retorno"] for a in r["ativos"]}["AAAA3"] == pytest.approx(0.10)
    assert any("BOVA11 sem fechamento em 27/10/2026" in a for a in r["avisos"])


def test_lacuna_no_universo_esvazia_so_a_curva_de_pesos_iguais():
    ret = _retornos([(i, d, 0.01) for d in DIAS[:2] for i in "AB"] + [("C", DIAS[0], 0.0)])
    r = cd.calcular(EXEC, ret, _bova([100.0, 100.0, 100.0]), _cdi(), CARTEIRA, UNIVERSO)
    assert r["pregoes"] == 2
    assert r["curvas"]["pesos_iguais"][-1] is None and r["curvas"]["pesos_iguais"][1] is not None
    assert all(v is not None for v in r["curvas"]["carteira20"])
    assert any("pesos iguais só até 27/10/2026" in a for a in r["avisos"])
    sem_c = cd.calcular(EXEC, ret[ret["id"] != "C"], _bova([100.0, 100.0, 100.0]), _cdi(),
                        CARTEIRA, UNIVERSO)
    assert sem_c["curvas"]["pesos_iguais"] is None
    assert any("CCCC3 sem nenhum pregão" in a for a in sem_c["avisos"])
    assert cd.retorno_acumulado([1000.0, None]) == [0.0, None]


def test_sem_pregao_depois_da_execucao_nao_publica():
    r = cd.calcular(EXEC, _retornos([]), _bova([100.0]), {}, CARTEIRA, UNIVERSO)
    assert r["pregoes"] == 0 and r["curvas"] == {}


def test_sem_fechamento_na_execucao_e_erro():
    ret = _retornos([("A", DIAS[0], 0.0)])
    bova = pd.Series([100.0], index=[DIAS[0]], dtype=float)
    with pytest.raises(ValueError, match="pregão de execução"):
        cd.calcular(EXEC, ret, bova, _cdi(), CARTEIRA, UNIVERSO)


def test_amostrar_diario_no_primeiro_ano_e_mensal_depois():
    curtas = [f"2026-11-{d:02d}" for d in range(1, 20)]
    assert cd.amostrar(curtas) == list(range(len(curtas)))
    longas = [d.date().isoformat() for d in pd.bdate_range("2026-10-26", periods=400)]
    idx = cd.amostrar(longas)
    assert idx[0] == 0 and idx[-1] == len(longas) - 1
    meses = [longas[i][:7] for i in idx[1:]]
    assert len(meses) == len(set(meses))  # um ponto por mês
    assert cd.retorno_acumulado([1000.0, 1100.0]) == [0.0, pytest.approx(0.1)]


def test_montar_grava_revisao_vigente_e_regras():
    regs = c20.ler(c20.REGISTRO)
    atual = c20.vigente(regs)
    if not atual:
        pytest.skip("registro sem revisão publicada")
    ids = [c["id"] for c in atual["corpo"]["carteira"]]
    ret = _retornos([(i, DIAS[0], 0.01) for i in ids])
    art = pub.montar(regs, retornos=lambda ids_, desde: ret,
                     bova=lambda desde: _bova([100.0, 101.0]),
                     cdi=lambda a, b: (_cdi(), "teste"),
                     agora=datetime(2026, 10, 27, 21, tzinfo=c20.FUSO_B3))
    assert art["revisao"]["hash"] == atual["registro"]["hash"]
    assert art["revisao"]["pregao_execucao"] == "2026-10-26"
    assert art["revisao"]["corpo_sha256"] == cd.impressao_corpo(atual["corpo"])
    assert art["dados_ate"] == "2026-10-27" and art["pregoes"] == 1
    assert art["regras"]["custo_por_lado"] == cd.CUSTO_LADO
    assert set(art["curvas"]) == set(cd.SERIES)


def test_acao_que_so_negocia_depois_do_corte_do_cdi_entra_com_fator_um():
    # B só negocia em 28/10 e o CDI só cobre 26/10: a curva para em 27/10
    ret = _retornos([("A", DIAS[0], 0.01), ("A", DIAS[1], 0.0), ("C", DIAS[0], 0.0),
                     ("C", DIAS[1], 0.0), ("B", DIAS[1], 0.02)])
    r = cd.calcular(EXEC, ret, _bova([100.0, 100.0, 100.0]), _cdi(dias=(EXEC,)),
                    CARTEIRA, UNIVERSO)
    assert r["datas"] == [EXEC.isoformat(), DIAS[0].isoformat()]
    assert {a["id"]: a["retorno"] for a in r["ativos"]} == {"A": 0.01, "B": 0.0}


def test_nenhuma_acao_negociou_ate_o_corte():
    ret = _retornos([(i, DIAS[1], 0.02) for i in "ABC"])
    r = cd.calcular(EXEC, ret, _bova([100.0, 101.0, 102.0]), _cdi(dias=(EXEC,)),
                    CARTEIRA, UNIVERSO)
    assert r["pregoes"] == 1
    assert [a["retorno"] for a in r["ativos"]] == [0.0, 0.0]
    assert r["curvas"]["pesos_iguais"] == r["curvas"]["carteira20"]


def test_publicador_grava_sem_a_curva_de_pesos_iguais(monkeypatch, tmp_path, capsys):
    regs = c20.ler(c20.REGISTRO)
    if not c20.vigente(regs):
        pytest.skip("registro sem revisão publicada")
    art = {"pregoes": 1, "dados_ate": "2026-10-27", "avisos": [],
           "curvas": {"carteira20": [994.0, 1001.0], "pesos_iguais": None}}
    monkeypatch.setattr(pub, "montar", lambda registros, execucao=None: art)
    monkeypatch.setattr(pub, "ROOT", tmp_path)
    monkeypatch.setattr(pub, "ARTEFATO", tmp_path / "d.json")
    assert pub.main([]) == 0
    assert "'pesos_iguais': None" in capsys.readouterr().out
    assert (tmp_path / "d.json").exists()


def test_montar_sem_pregao_devolve_none():
    regs = c20.ler(c20.REGISTRO)
    if not c20.vigente(regs):
        pytest.skip("registro sem revisão publicada")
    art = pub.montar(regs, retornos=lambda ids_, desde: _retornos([]),
                     bova=lambda desde: _bova([100.0]), cdi=lambda a, b: ({}, "teste"),
                     agora=datetime(2026, 10, 26, 12, tzinfo=c20.FUSO_B3))
    assert art is None


def _cotahist(tickers, dias, close=10.0, bdi="02"):
    return [{"ticker": t, "trade_date": d.isoformat(), "bdi": bdi, "specification": "ON      NM",
             "close": close, "fator_cotacao": 1} for t in tickers for d in dias]


def test_acoes_juntam_a_pesquisa_ate_o_fim_dela_e_a_extensao_depois():
    fim = date(2026, 10, 22)
    pregoes = [d.date() for d in pd.bdate_range("2026-09-01", "2026-10-27")]
    chamadas = {}

    def cotahist(desde, hoje):
        chamadas["cotahist"] = desde
        return _cotahist(["AAAA3", "BBBB4"], pregoes)

    def proventos(tickers, desde):
        chamadas["proventos"] = (tickers, desde)
        return pd.DataFrame(columns=["ticker", "ex_date", "payment_date", "amount", "type"])

    # a pesquisa traz um dia além do fim declarado: fica de fora
    pesquisa = _retornos([("A", date(2026, 10, 21), 0.01), ("A", date(2026, 10, 22), 0.02),
                          ("A", date(2026, 10, 23), 0.99)]).assign(suspeito=False)
    df = pub.retornos_das_acoes({"A", "B"}, date(2026, 10, 20), pesquisa=lambda ids, d: pesquisa,
                                fim_pesquisa=lambda: fim,
                                cadeias=lambda ids: {"AAAA3": "A", "BBBB4": "B"},
                                cotahist=cotahist, proventos=proventos, hoje=date(2026, 10, 27))
    assert chamadas["cotahist"] == fim - pub.JANELA_PREGOES
    assert chamadas["proventos"] == ({"AAAA3", "BBBB4"}, fim - pub.JANELA_PROVENTOS)
    a = df[df["id"] == "A"].set_index("data")["ret"]
    assert a[date(2026, 10, 22)] == 0.02 and a[date(2026, 10, 23)] == 0.0
    assert sorted(df.loc[df["id"] == "B", "data"]) == [date(2026, 10, 23), date(2026, 10, 26),
                                                        date(2026, 10, 27)]
    assert "até 22/10/2026" in df.attrs["fonte"] and "COTAHIST" in df.attrs["fonte"]


def test_extensao_comeca_na_execucao_quando_ela_e_depois_da_pesquisa():
    pregoes = [d.date() for d in pd.bdate_range("2026-09-01", "2026-10-28")]
    chamadas = {}

    def cotahist(desde, hoje):
        chamadas["desde"] = desde
        return _cotahist(["AAAA3"], pregoes)

    df = pub.retornos_das_acoes({"A"}, EXEC, pesquisa=lambda ids, d: _retornos([]).assign(suspeito=False),
                                fim_pesquisa=lambda: date(2026, 10, 22),
                                cadeias=lambda ids: {"AAAA3": "A"}, cotahist=cotahist,
                                proventos=lambda t, d: pd.DataFrame(
                                    columns=["ticker", "ex_date", "payment_date", "amount", "type"]),
                                hoje=date(2026, 10, 28))
    assert chamadas["desde"] == EXEC - pub.JANELA_PREGOES
    assert sorted(df["data"]) == DIAS[:2]


def test_bova11_e_o_fechamento_oficial_do_cotahist():
    linhas = (_cotahist(["BOVA11"], [date(2026, 10, 23), *DIAS[:2]], close=150.0, bdi="14")
              + _cotahist(["BOVA11"], [EXEC], close=999.0, bdi="96")       # fracionário
              + _cotahist(["XXXX11"], [EXEC], close=1.0, bdi="14")
              + _cotahist(["BOVA11"], [EXEC], close=151.0, bdi="14"))
    s = pub.bova11_do_cotahist(EXEC, cotahist=lambda d, h: linhas, hoje=DIAS[1])
    assert list(s.index) == [EXEC, *DIAS[:2]]
    assert s[EXEC] == 151.0 and s[DIAS[0]] == 150.0


def test_zip_do_cotahist_ausente_e_erro(monkeypatch, tmp_path):
    monkeypatch.setenv("CARTEIRA20_COTAHIST_DIR", str(tmp_path))
    monkeypatch.setattr(pub, "_linhas_do_zip", lambda caminho, mtime: ({"ano": caminho[-8:-4]},))
    (tmp_path / "COTAHIST_A2026.ZIP").write_bytes(b"")
    with pytest.raises(SystemExit, match="COTAHIST_A2025.ZIP"):
        pub.linhas_do_cotahist(date(2025, 12, 1), date(2026, 10, 27))
    assert pub.linhas_do_cotahist(date(2026, 9, 1), date(2026, 10, 27)) == [{"ano": "2026"}]


def test_zip_do_ano_novo_pode_faltar_nos_primeiros_dias_de_janeiro(monkeypatch, tmp_path):
    monkeypatch.setenv("CARTEIRA20_COTAHIST_DIR", str(tmp_path))
    monkeypatch.setattr(pub, "_linhas_do_zip", lambda caminho, mtime: ({"ano": caminho[-8:-4]},))
    (tmp_path / "COTAHIST_A2026.ZIP").write_bytes(b"")
    assert pub.linhas_do_cotahist(date(2026, 12, 1), date(2027, 1, 5)) == [{"ano": "2026"}]
    with pytest.raises(SystemExit, match="COTAHIST_A2027.ZIP"):
        pub.linhas_do_cotahist(date(2026, 12, 1), date(2027, 1, 20))


def test_montar_registra_a_fonte_das_acoes():
    regs = c20.ler(c20.REGISTRO)
    atual = c20.vigente(regs)
    if not atual:
        pytest.skip("registro sem revisão publicada")
    ids = [c["id"] for c in atual["corpo"]["carteira"]]
    ret = _retornos([(i, DIAS[0], 0.01) for i in ids])
    ret.attrs["fonte"] = "pesquisa até X; depois, COTAHIST"
    art = pub.montar(regs, retornos=lambda ids_, desde: ret,
                     bova=lambda desde: _bova([100.0, 101.0]),
                     cdi=lambda a, b: (_cdi(), "teste"),
                     agora=datetime(2026, 10, 27, 21, tzinfo=c20.FUSO_B3))
    assert art["fontes"]["acoes"] == "pesquisa até X; depois, COTAHIST"
    assert "COTAHIST" in art["fontes"]["bova11"]
