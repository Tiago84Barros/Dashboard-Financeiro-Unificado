"""Cinco carteiras fundamentalistas da B3 (puras) e a régua compartilhada."""
from __future__ import annotations

import pandas as pd
import pytest

import core.b3_estrategias_fundamentalistas as fe


def _empresa(ticker: str, **campos) -> dict:
    """Saudável e cara por padrão; cada teste distorce o que interessa."""
    base = {
        "Ticker": ticker,
        "P/L": 15.0, "P/VP": 2.0, "DY": 0.03, "ROE": 0.15,
        "ROIC": 0.15, "Margem_Operacional": 0.18, "Margem_Liquida": 0.12,
        "Endividamento_Total": 0.8, "Liquidez_Corrente": 1.8, "P_FCO": 10.0,
        "EV_EBIT": 8.0,
    }
    base.update(campos)
    return base


def _hist(roics: dict[int, float]) -> pd.DataFrame:
    return pd.DataFrame({"Data": [pd.Timestamp(a, 12, 31) for a in roics],
                         "ROIC": list(roics.values())})


# ── 1. Rota de valor ────────────────────────────────────────────────────────
def test_rota_valor_compra_so_oportunidade_do_universo():
    df = pd.DataFrame([
        _empresa("BOA3", **{"P/L": 5.0, "P/VP": 0.8, "DY": 0.09}),
        _empresa("CARA3"),
        _empresa("TRAP3", **{"P/L": 4.0, "P/VP": 0.5, "P_FCO": -3.0}),
        _empresa("FORA3", **{"P/L": 5.0, "P/VP": 0.8, "DY": 0.09}),
    ])
    assert fe.carteira_rota_valor(df, ["BOA3", "CARA3", "TRAP3"]) == ["BOA3"]


def test_rota_valor_sem_retrato_e_lista_vazia():
    assert fe.carteira_rota_valor(None, ["X3"]) == []
    assert fe.carteira_rota_valor(pd.DataFrame(), ["X3"]) == []


# ── 2. Fórmula mágica ───────────────────────────────────────────────────────
def test_formula_magica_soma_postos_e_respeita_o_minimo():
    linhas = [_empresa(f"E{i:02d}3", ROIC=0.05 + 0.01 * i, EV_EBIT=20.0 - i)
              for i in range(12)]
    df = pd.DataFrame(linhas)
    nomes = fe.carteira_formula_magica(df, df["Ticker"], min_nomes=3, fracao=0.25)
    # Maior ROIC e menor EV_EBIT andam juntos: o topo é E11, E10, E09.
    assert nomes == ["E093", "E103", "E113"]


def test_formula_magica_tira_financeira_e_ev_ebit_invalido():
    df = pd.DataFrame([
        _empresa("BANC4", ROIC=0.50, EV_EBIT=2.0),
        _empresa("NEG3", ROIC=0.40, EV_EBIT=-3.0),
        _empresa("SEM3", ROIC=None, EV_EBIT=4.0),
        _empresa("OK3", ROIC=0.10, EV_EBIT=9.0),
    ])
    nomes = fe.carteira_formula_magica(
        df, df["Ticker"], setor_de={"BANC4": "Financeiro", "OK3": "Consumo"})
    assert nomes == ["OK3"]


def test_formula_magica_poucos_elegiveis_leva_todos():
    df = pd.DataFrame([_empresa("A3"), _empresa("B3", ROIC=0.3)])
    assert fe.carteira_formula_magica(df, ["A3", "B3"]) == ["A3", "B3"]


# ── 3. Qualidade constante ──────────────────────────────────────────────────
SELIC = {a: 0.10 for a in range(2010, 2025)}


def test_persistencia_conta_so_anos_da_janela_com_selic():
    h = _hist({2014: 0.30, 2016: 0.12, 2017: 0.08, 2018: 0.20, 2019: 0.25, 2020: 0.30})
    acima, medidos = fe.persistencia_roic(h, 2020, SELIC)
    # Janela 2014..2020: seis anos com ROIC, cinco acima de 10%.
    assert (acima, medidos) == (5, 6)
    # Ano sem Selic e sem padrão não conta.
    acima, medidos = fe.persistencia_roic(h, 2020, {2020: 0.10})
    assert (acima, medidos) == (1, 1)


def test_lucro_cresceu_exige_positivo_e_maior_que_tres_anos_antes():
    serie = [{"ano": 2017, "lucro_mi": 100.0}, {"ano": 2020, "lucro_mi": 150.0}]
    assert fe.lucro_cresceu(serie, 2020)
    assert not fe.lucro_cresceu([{"ano": 2017, "lucro_mi": 200.0},
                                 {"ano": 2020, "lucro_mi": 150.0}], 2020)
    assert not fe.lucro_cresceu([{"ano": 2017, "lucro_mi": -50.0},
                                 {"ano": 2020, "lucro_mi": -10.0}], 2020)
    assert not fe.lucro_cresceu([{"ano": 2020, "lucro_mi": 150.0}], 2020)


def _caso_qualidade():
    bom = {a: 0.20 for a in range(2014, 2021)}
    df = pd.DataFrame([
        _empresa("BOA3"),
        _empresa("DIVIDA3", Endividamento_Total=4.0),
        _empresa("CURTA3"),
        _empresa("FRACA3"),
        _empresa("CAIU3"),
    ])
    hist = {"BOA3": _hist(bom), "DIVIDA3": _hist(bom),
            "CURTA3": _hist({2019: 0.2, 2020: 0.2}),
            "FRACA3": _hist({a: (0.20 if a % 2 else 0.05) for a in range(2014, 2021)}),
            "CAIU3": _hist(bom)}
    cresce = [{"ano": 2017, "lucro_mi": 100.0}, {"ano": 2020, "lucro_mi": 140.0}]
    pl = {"BOA3": cresce, "DIVIDA3": cresce, "CURTA3": cresce, "FRACA3": cresce,
          "CAIU3": [{"ano": 2017, "lucro_mi": 100.0}, {"ano": 2020, "lucro_mi": 90.0}]}
    return df, hist, pl


def test_qualidade_constante_passa_so_quem_cumpre_tudo():
    df, hist, pl = _caso_qualidade()
    nomes = fe.carteira_qualidade_constante(
        df, df["Ticker"], hist, pl, 2020, SELIC)
    assert nomes == ["BOA3"]


def test_qualidade_constante_ausencia_de_dado_de_solvencia_nao_reprova():
    df, hist, pl = _caso_qualidade()
    df.loc[df["Ticker"] == "BOA3", "Liquidez_Corrente"] = None
    nomes = fe.carteira_qualidade_constante(df, ["BOA3"], hist, pl, 2020, SELIC)
    assert nomes == ["BOA3"]


# ── 4. Qualidade segurada ───────────────────────────────────────────────────
def test_segurar_refaz_a_cada_tres_e_no_meio_so_tira():
    formar_chamado = []

    def formar(s):
        formar_chamado.append(s)
        return {2017: ["A", "B", "C"], 2020: ["D"]}.get(s, ["Z"])

    def manter(s, lista):
        return [t for t in lista if not (s == 2019 and t == "B")]

    out = fe.segurar([2017, 2018, 2019, 2020, 2021], formar, manter)
    assert formar_chamado == [2017, 2020]
    assert out == {2017: ["A", "B", "C"], 2018: ["A", "B", "C"], 2019: ["A", "C"],
                   2020: ["D"], 2021: ["D"]}


def test_segurar_carteira_que_zerou_e_refeita_na_safra_seguinte():
    out = fe.segurar([2017, 2018, 2019],
                     formar=lambda s: ["A"] if s == 2017 else ["N"],
                     manter=lambda s, lista: [])
    assert out == {2017: ["A"], 2018: [], 2019: ["N"]}


def test_manter_qualidade_tira_quem_saiu_ou_quebrou():
    df = pd.DataFrame([_empresa("A3"), _empresa("B3", Endividamento_Total=4.0)])
    assert fe.manter_qualidade(["A3", "B3", "C3"], ["A3", "B3"], df) == ["A3"]


# ── Régua compartilhada e decisão ───────────────────────────────────────────
def test_diferenca_pareada_e_decisao():
    from scripts.medir_estrategias_fundamentalistas_b3 import decidir, diferenca_pareada

    controle = [{"safra": 2017, "liquido": 0.10}, {"safra": 2018},
                {"safra": 2019, "liquido": 0.05}]
    braco = [{"safra": 2017, "liquido": 0.12}, {"safra": 2018, "liquido": 0.3},
             {"safra": 2019, "liquido": 0.01}]
    par = diferenca_pareada(controle, braco, "liquido")
    assert [p["safra"] for p in par] == [2017, 2019]
    assert par[0]["dif"] == pytest.approx(0.02)

    ok = {"vs_equal_weight": {"media": 0.03, "ic95": [0.01, 0.05],
                              "media_sem_melhor_safra": 0.02}}
    assert decidir(ok, {"media": 0.01})["candidata"]
    assert not decidir(ok, {"media": -0.01})["candidata"]
    cruza = {"vs_equal_weight": {"media": 0.03, "ic95": [-0.01, 0.05],
                                 "media_sem_melhor_safra": 0.02}}
    assert not decidir(cruza, {"media": 0.01})["candidata"]
    uma_safra = {"vs_equal_weight": {"media": 0.03, "ic95": [0.01, 0.05],
                                     "media_sem_melhor_safra": -0.01}}
    assert not decidir(uma_safra, {"media": 0.01})["candidata"]


def test_medir_escolha_vazia_vira_caixa_e_mesma_regua(monkeypatch):
    """A régua extraída de medir_nota_fatores_b3 continua a mesma conta."""
    import core.b3_oos_carteira as oos
    import scripts.medir_nota_fatores_b3 as nf_script

    chamadas = {}

    def falso_retorno(safra_cart, precos, **kw):
        return {"mensuravel": True, "retorno_selic": 0.10,
                "retorno_equal_weight": 0.08}

    def falsa_cadeia(itens, cost_cfg):
        chamadas["itens"] = itens
        return [{"medida": True, "liquido": 0.10, "bruto": 0.10,
                 "liquido_com_ir": 0.09, "giro": 0.0, "custo_pct": 0.0}
                for _ in itens]

    monkeypatch.setattr("core.b3_safras.retorno_da_safra", falso_retorno)
    monkeypatch.setattr(oos, "retornos_por_ticker", lambda *a, **k: {})
    monkeypatch.setattr(oos, "simular_custos", falsa_cadeia)
    por_safra = [{"safra": 2017, "universo": ["A3", "B3"], "saidas": {}},
                 {"safra": 2018, "universo": ["A3", "B3"], "saidas": {}}]
    out = nf_script.medir_escolha(
        por_safra, lambda p: [] if p["safra"] == 2017 else ["A3"],
        precos=None, selic_macro={}, taxa=0.1, selic_mensal={}, cost_cfg=None,
        comeca_em=2017)
    assert [t["safra"] for t in out["safras"]] == [2017, 2018]
    assert out["safras"][0]["n_carteira"] == 0
    assert out["safras"][1]["excesso_ew"] == pytest.approx(0.02)
    assert len(chamadas["itens"]) == 2


def test_medir_escolha_aceita_outra_cadeia_e_leva_os_extras(monkeypatch):
    """§26: ``simular`` troca a cadeia; giro de venda e pesos seguem na tabela."""
    import core.b3_oos_carteira as oos
    import scripts.medir_nota_fatores_b3 as nf_script

    monkeypatch.setattr("core.b3_safras.retorno_da_safra",
                        lambda *a, **k: {"mensuravel": True, "retorno_selic": 0.10,
                                         "retorno_equal_weight": 0.08})
    monkeypatch.setattr(oos, "retornos_por_ticker", lambda *a, **k: {})
    monkeypatch.setattr(oos, "simular_custos",
                        lambda *a: pytest.fail("a cadeia padrão não pode rodar"))

    def outra(itens, cost_cfg):
        return [{"medida": True, "liquido": 0.10, "bruto": 0.10,
                 "liquido_com_ir": 0.09, "giro": 0.0, "custo_pct": 0.0,
                 "giro_venda": 0.25, "peso_maior": 0.6, "peso_menor": 0.4}
                for _ in itens]

    por_safra = [{"safra": a, "universo": ["A3", "B3"], "saidas": {}}
                 for a in (2017, 2018, 2019)]
    out = nf_script.medir_escolha(
        por_safra, lambda p: ["A3", "B3"], precos=None, selic_macro={}, taxa=0.1,
        selic_mensal={}, cost_cfg=None, comeca_em=2017, simular=outra)
    assert out["safras"][1]["giro_venda"] == 0.25
    assert out["safras"][1]["peso_maior"] == 0.6
    assert out["giro_venda_medio_anual"] == pytest.approx(0.25)


def test_selic_ate_e_point_in_time():
    from scripts.medir_estrategias_fundamentalistas_b3 import selic_ate

    serie = {2012: 0.08, 2014: 0.11, 2020: 0.02}
    assert selic_ate(serie, 2014) == 0.11
    assert selic_ate(serie, 2016) == 0.11  # última antes, nunca o futuro
    assert selic_ate(serie, 2011) is None


def test_qualidade_sem_padrao_ignora_ano_antes_da_serie():
    """Ano anterior à série de Selic não entra na contagem nem no limiar."""
    h = _hist({a: 0.20 for a in range(2014, 2021)})
    acima, medidos = fe.persistencia_roic(h, 2020, {a: 0.10 for a in range(2017, 2021)})
    assert (acima, medidos) == (4, 4)


# ── 5. Líder segurado ───────────────────────────────────────────────────────
def _pl(lucros: dict[int, float]) -> list[dict]:
    return [{"ano": a, "lucro_mi": v} for a, v in lucros.items()]


def test_lider_segurado_esta_entre_as_estrategias():
    assert fe.LIDER_SEGURADO in fe.ESTRATEGIAS
    assert len(fe.ESTRATEGIAS) == 5
    assert "cinco braços" in fe.REGRA_DE_DECISAO


def test_motivos_de_saida_tres_degradacoes():
    df = pd.DataFrame([
        _empresa("BOA3"),
        _empresa("QUEBRA3", Liquidez_Corrente=0.5, Endividamento_Total=5.0, P_FCO=-3.0),
        _empresa("PREJ3"),
    ])
    pl = {"BOA3": _pl({2020: 10.0}), "PREJ3": _pl({2020: -1.0})}
    m = fe.motivos_de_saida(["BOA3", "QUEBRA3", "PREJ3", "SUMIU3"],
                            ["BOA3", "QUEBRA3", "PREJ3"], df, pl, 2020)
    assert m["BOA3"] == []
    assert fe.FALHA_SOLVENCIA in m["QUEBRA3"]
    assert m["PREJ3"] == [fe.PREJUIZO]
    assert m["SUMIU3"] == [fe.SAIU_DO_UNIVERSO]


def test_motivos_de_saida_dado_ausente_nao_degrada():
    # Sem retrato, sem série de lucro e sem o lucro do último exercício.
    m = fe.motivos_de_saida(["X3", "Y3"], ["X3", "Y3"], None,
                            {"Y3": _pl({2018: -5.0})}, 2020)
    assert m == {"X3": [], "Y3": []}
    # Lucro zero conta como prejuízo (regra: lucro de N-1 <= 0).
    m0 = fe.motivos_de_saida(["Z3"], ["Z3"], None, {"Z3": _pl({2020: 0.0})}, 2020)
    assert m0["Z3"] == [fe.PREJUIZO]


def test_segurar_lideres_soma_novos_e_so_tira_quem_degradou():
    lideres = {2018: ["A3", "B3"], 2019: ["C3"], 2020: ["A3", "D3"]}
    degradou = {2019: {"B3"}, 2020: set()}
    vistos = []

    def manter(s, lista):
        vistos.append((s, list(lista)))
        return [t for t in lista if t not in degradou.get(s, set())]

    out = fe.segurar_lideres([2020, 2018, 2019], lambda s: lideres[s], manter)
    assert out == {2018: ["A3", "B3"], 2019: ["A3", "C3"], 2020: ["A3", "C3", "D3"]}
    # A primeira safra não tem carteira anterior; as outras recebem a anterior.
    assert vistos == [(2019, ["A3", "B3"]), (2020, ["A3", "C3"])]


def test_segurar_lideres_sem_lideres_mantem_os_antigos():
    out = fe.segurar_lideres([2018, 2019], lambda s: ["A3"] if s == 2018 else [],
                             lambda s, lista: lista)
    assert out == {2018: ["A3"], 2019: ["A3"]}


def test_manter_lideres_usa_os_motivos():
    pl = {"PREJ3": _pl({2020: -1.0})}
    assert fe.manter_lideres(["OK3", "PREJ3", "FORA3"], ["OK3", "PREJ3"], None,
                             pl, 2020) == ["OK3"]


# ── 1b. Rota de valor enxuta (top-N) ────────────────────────────────────────
def _oportunidades(n: int) -> pd.DataFrame:
    # Quanto menor o P/L e o P/VP e maior o DY, maior a margem de valor.
    return pd.DataFrame([
        _empresa(f"O{i:02d}3", **{"P/L": 5.0 + 0.3 * i, "P/VP": 0.8 + 0.03 * i,
                                  "DY": 0.09 - 0.002 * i})
        for i in range(n)])


def test_topn_e_subconjunto_da_rota_completa_e_respeita_n():
    df = _oportunidades(8)
    completa = fe.carteira_rota_valor(df, df["Ticker"])
    top3 = fe.carteira_rota_valor_topn(df, df["Ticker"], 3)
    assert len(top3) == 3 and set(top3) <= set(completa)


def test_topn_maior_que_as_oportunidades_fica_com_todas():
    df = _oportunidades(4)
    assert fe.carteira_rota_valor_topn(df, df["Ticker"], 10) ==         fe.carteira_rota_valor(df, df["Ticker"])


def test_topn_fica_com_os_de_maior_valor_score():
    from core.b3_value_route import OPORTUNIDADE, rank_value_opportunities
    df = _oportunidades(8)
    r = rank_value_opportunities(df)
    r = r[r["classificacao"] == OPORTUNIDADE]
    melhores = set(r.sort_values(["valor_score", "margem_valor", "Ticker"],
                                 ascending=[False, False, True])["Ticker"].head(3))
    assert set(fe.carteira_rota_valor_topn(df, df["Ticker"], 3)) == melhores


def test_topn_nao_depende_da_ordem_das_linhas():
    df = _oportunidades(8)
    a = fe.carteira_rota_valor_topn(df, df["Ticker"], 4)
    b = fe.carteira_rota_valor_topn(df.iloc[::-1], df["Ticker"], 4)
    assert a == b


def test_topn_sem_retrato_ou_n_invalido_e_vazio():
    assert fe.carteira_rota_valor_topn(None, ["X3"], 10) == []
    assert fe.carteira_rota_valor_topn(_oportunidades(3), ["O003"], 0) == []
    assert fe.rotulo_rota_topn(15) == "rota_valor_top15"


# ── 6. Pequenas baratas ─────────────────────────────────────────────────────
def test_pequenas_sao_as_que_o_piso_corta():
    assert fe.pequenas(["a3", "B3X", "C3"], ["B3X"]) == ["A3", "C3"]
    assert fe.pequenas(["A3"], ["A3"]) == []


def test_pequenas_baratas_so_compra_oportunidade_entre_as_pequenas():
    barata = {"P/L": 5.0, "P/VP": 0.8, "DY": 0.09}
    df = pd.DataFrame([
        _empresa("PEQB3", **barata),          # pequena e barata: entra
        _empresa("PEQC3"),                    # pequena e cara: fora
        _empresa("GRDB3", **barata),          # barata, mas grande: fora
    ])
    sem_piso = ["PEQB3", "PEQC3", "GRDB3"]
    com_piso = ["GRDB3"]
    assert fe.carteira_pequenas_baratas(df, sem_piso, com_piso) == ["PEQB3"]
    # A rota sem piso (informativa) pega as duas baratas.
    assert fe.carteira_rota_valor(df, sem_piso) == ["GRDB3", "PEQB3"]


def test_pequenas_baratas_sem_retrato_e_vazia():
    assert fe.carteira_pequenas_baratas(None, ["A3"], []) == []


# ── 7. Dividendo de 6% (Bazin) ──────────────────────────────────────────────
def _hist_dy(dys: dict[int, float]) -> pd.DataFrame:
    return pd.DataFrame({"Data": [pd.Timestamp(a, 12, 31) for a in dys],
                         "DY": list(dys.values())})


def test_dy_medio_exige_os_tres_anos():
    assert fe.dy_medio(_hist_dy({2021: 0.05, 2022: 0.06, 2023: 0.07}), 2023) == \
        pytest.approx(0.06)
    assert fe.dy_medio(_hist_dy({2022: 0.06, 2023: 0.07}), 2023) is None
    # Ano posterior a ano_max não entra na janela.
    assert fe.dy_medio(_hist_dy({2020: 0.1, 2021: 0.1, 2022: 0.1, 2023: 0.0}),
                       2022) == pytest.approx(0.1)


def test_dy_incrivel_conta_como_ausente():
    assert fe.dy_medio(_hist_dy({2021: 0.07, 2022: 5.0, 2023: 0.07}), 2023) is None
    assert fe.dy_medio(_hist_dy({2021: 0.07, 2022: -0.01, 2023: 0.07}), 2023) is None


def test_dy_com_duas_linhas_no_ano_vale_a_mais_nova():
    h = pd.DataFrame({"Data": [pd.Timestamp(2023, 12, 31), pd.Timestamp(2023, 6, 30),
                               pd.Timestamp(2022, 12, 31), pd.Timestamp(2021, 12, 31)],
                      "DY": [0.09, 0.01, 0.06, 0.06]})
    assert fe.dy_medio(h, 2023) == pytest.approx(0.07)


def test_dividendo_bazin_corta_por_media_e_por_solvencia():
    df = pd.DataFrame([
        _empresa("PAGA3"),
        _empresa("POUCO3"),
        _empresa("QUEBR3", **{"P_FCO": -3.0}),
        _empresa("SEMH3"),
    ])
    hist = {
        "paga3": _hist_dy({2021: 0.06, 2022: 0.05, 2023: 0.08}),   # média 6,3%
        "POUCO3": _hist_dy({2021: 0.06, 2022: 0.05, 2023: 0.06}),  # média 5,7%
        "QUEBR3": _hist_dy({2021: 0.10, 2022: 0.10, 2023: 0.10}),
    }
    universo = ["PAGA3", "POUCO3", "QUEBR3", "SEMH3"]
    falhas = fe.falhas_de_solvencia(df, universo)
    assert falhas["QUEBR3"] and not falhas["PAGA3"]
    assert fe.carteira_dividendo_bazin(df, universo, hist, 2023) == ["PAGA3"]


def test_dividendo_bazin_no_limite_de_6_entra():
    hist = {"X3": _hist_dy({2021: 0.06, 2022: 0.06, 2023: 0.06})}
    assert fe.carteira_dividendo_bazin(None, ["X3"], hist, 2023) == ["X3"]


def test_regra_da_secao_23_registrada():
    texto = fe.REGRA_DE_DECISAO_PEQUENAS_DIVIDENDO
    assert "(a)" in texto and "(b)" in texto and "(c)" in texto
    assert fe.DY_ALVO_BAZIN == 0.06 and fe.ANOS_DY_BAZIN == 3


# ── §24: o dividendo de 6% em outra janela ──────────────────────────────────
def _resumo(media, lo, sem_melhor, n=4):
    return {"media": media, "ic95": [lo, 0.5], "n_safras": n,
            "media_sem_melhor_safra": sem_melhor}


def test_outra_janela_confirma_com_ic_acima_de_zero_e_sem_melhor_positiva():
    r = fe.leitura_outra_janela(_resumo(0.05, 0.01, 0.03))
    assert r["leitura"] == fe.CONFIRMA
    assert all(r["testes"].values())


def test_outra_janela_nao_contradiz_quando_so_o_ic_cruza_o_zero():
    r = fe.leitura_outra_janela(_resumo(0.05, -0.02, 0.03))
    assert r["leitura"] == fe.NAO_CONTRADIZ
    assert not r["testes"]["a_excesso_ew_ic_acima_de_zero"]
    assert r["testes"]["b_sem_melhor_safra_positiva"]


def test_outra_janela_contradiz_com_media_ou_sem_melhor_negativa():
    assert fe.leitura_outra_janela(_resumo(-0.01, -0.05, -0.02))["leitura"] == fe.CONTRADIZ
    # Média positiva puxada por uma safra só: contradiz.
    assert fe.leitura_outra_janela(_resumo(0.04, -0.03, -0.01))["leitura"] == fe.CONTRADIZ
    # Média zero não é "acima de zero".
    assert fe.leitura_outra_janela(_resumo(0.0, -0.01, 0.01))["leitura"] == fe.CONTRADIZ


def test_outra_janela_poucas_safras_e_inconclusiva_nao_contradiz():
    assert fe.leitura_outra_janela(_resumo(-0.05, None, None, n=2))["leitura"] == fe.INCONCLUSIVA
    assert fe.leitura_outra_janela(None)["leitura"] == fe.INCONCLUSIVA


def test_outra_janela_le_o_resumo_de_verdade():
    import core.b3_oos_carteira as oos
    linhas = [{"safra": s, "excesso_ew": v}
              for s, v in zip(range(2013, 2017), (0.04, 0.06, 0.05, 0.03))]
    r = fe.leitura_outra_janela(oos.resumir(linhas, "excesso_ew"))
    assert r["n_safras"] == 4 and r["leitura"] == fe.CONFIRMA


def test_regra_da_secao_24_registrada():
    texto = fe.REGRA_DE_DECISAO_DIVIDENDO_OUTRA_JANELA
    assert "2013" in texto and "2016" in texto and "(c)" in texto
    assert (fe.PRIMEIRA_SAFRA_OUTRA_JANELA, fe.ULTIMA_SAFRA_OUTRA_JANELA) == (2013, 2016)


# ── §25: o dividendo de 6% sobre a rota de valor ────────────────────────────
def test_regra_da_secao_25_registrada():
    assert fe.VERSAO >= "fundamentalistas-1.5.0"
    texto = fe.REGRA_DE_DECISAO_SOBREPOSICAO_ROTA
    assert "(a)" in texto and "(b)" in texto and "LÍQUIDA" in texto


def test_sobreposicao_conta_comuns_e_fracoes():
    s = fe.sobreposicao(["A3", "B3", "C3", "D3"], ["c3", "D3", "E3"])
    assert (s["n_rota"], s["n_dividendo"], s["n_comum"], s["n_uniao"]) == (4, 3, 2, 5)
    assert s["fracao_do_dividendo_na_rota"] == 2 / 3
    assert s["fracao_da_rota_no_dividendo"] == 0.5
    assert s["jaccard"] == 2 / 5
    assert s["comuns"] == ["C3", "D3"]


def test_sobreposicao_com_carteira_vazia_nao_divide_por_zero():
    s = fe.sobreposicao([], [])
    assert s["n_comum"] == 0
    assert s["fracao_do_dividendo_na_rota"] is None and s["jaccard"] is None
    assert fe.sobreposicao(["A3"], [])["fracao_do_dividendo_na_rota"] is None


def test_uniao_e_fora_da_rota():
    assert fe.carteira_uniao(["B3", "A3"], ["a3", "C3"]) == ["A3", "B3", "C3"]
    assert fe.carteira_fora_de(["A3", "C3", "D3"], ["c3", "B3"]) == ["A3", "D3"]
    assert fe.carteira_fora_de(["A3"], ["A3"]) == []


def _par(media, lo, hi, sem_melhor, n=9):
    return {"media": media, "ic95": [lo, hi], "n_safras": n,
            "media_sem_melhor_safra": sem_melhor}


def test_sobreposicao_acrescenta_so_com_ic_acima_de_zero_e_sem_melhor_positiva():
    assert fe.leitura_sobreposicao(_par(0.02, 0.005, 0.04, 0.01))["leitura"] == fe.ACRESCENTA
    # IC acima de zero, mas puxado por uma safra só: não se distingue.
    assert (fe.leitura_sobreposicao(_par(0.02, 0.001, 0.04, -0.001))["leitura"]
            == fe.NAO_SE_DISTINGUE)


def test_sobreposicao_tira_so_com_ic_abaixo_de_zero():
    assert fe.leitura_sobreposicao(_par(-0.03, -0.05, -0.01, -0.02))["leitura"] == fe.TIRA
    assert (fe.leitura_sobreposicao(_par(-0.03, -0.06, 0.01, -0.02))["leitura"]
            == fe.NAO_SE_DISTINGUE)


def test_sobreposicao_inconclusiva_com_pouca_amostra_ou_sem_resumo():
    assert fe.leitura_sobreposicao(_par(0.05, 0.01, 0.09, 0.03, n=2))["leitura"] == fe.INCONCLUSIVA
    assert fe.leitura_sobreposicao(None)["leitura"] == fe.INCONCLUSIVA


# ── §26: dividendo segurado, equilíbrio pelo aporte ──────────────────────────
from core.transaction_costs import CostConfig  # noqa: E402

_SEM_CUSTO = CostConfig.desligado()


def test_encher_por_baixo_leva_os_menores_ao_mesmo_nivel():
    compras = fe.encher_por_baixo({"A3": 50.0, "B3": 10.0}, ["A3", "B3", "C3"], 30.0)
    assert compras == pytest.approx({"C3": 20.0, "B3": 10.0})
    assert "A3" not in compras  # quem está acima não recebe


def test_encher_por_baixo_com_dinheiro_de_sobra_iguala_todos():
    pos = {"A3": 50.0, "B3": 10.0}
    compras = fe.encher_por_baixo(pos, ["a3", "b3", "c3"], 200.0)
    assert sum(compras.values()) == pytest.approx(200.0)
    finais = {t: pos.get(t, 0.0) + compras[t] for t in ("A3", "B3", "C3")}
    assert max(finais.values()) == pytest.approx(min(finais.values()))


def test_encher_por_baixo_sem_dinheiro_ou_sem_nome_nao_compra():
    assert fe.encher_por_baixo({"A3": 1.0}, ["A3"], 0.0) == {}
    assert fe.encher_por_baixo({"A3": 1.0}, [], 10.0) == {}


def _safra(ano, rets, **extra):
    return {"safra": ano, "pesos": {t: 1.0 / len(rets) for t in rets},
            "retornos": dict(rets), **extra}


def test_simular_aportes_primeira_safra_em_pesos_iguais_e_sem_aporte():
    out = fe.simular_aportes([_safra(2017, {"A3": 1.0, "B3": 0.0})], _SEM_CUSTO)
    assert out[0]["peso_maior"] == pytest.approx(0.5)
    assert out[0]["peso_menor"] == pytest.approx(0.5)
    assert out[0]["liquido"] == pytest.approx(0.5)
    assert out[0]["giro"] == pytest.approx(1.0)


def test_simular_aportes_nao_vende_quem_subiu_e_aporta_em_quem_ficou_atras():
    out = fe.simular_aportes([_safra(2017, {"A3": 1.0, "B3": 0.0}),
                              _safra(2018, {"A3": 0.0, "B3": 0.0})], _SEM_CUSTO)
    s = out[1]
    # 150 em abril: A3=100, B3=50. O aporte de 15 vai todo para B3.
    assert s["giro_venda"] == pytest.approx(0.0)
    assert s["giro"] == pytest.approx(15.0 / 165.0)
    assert s["peso_maior"] == pytest.approx(100.0 / 165.0)
    assert s["peso_menor"] == pytest.approx(65.0 / 165.0)
    # O aporte não conta como ganho.
    assert s["liquido"] == pytest.approx(0.0)


def test_simular_aportes_vende_so_quem_saiu_e_compra_o_novo():
    out = fe.simular_aportes([_safra(2017, {"A3": 1.0, "B3": 0.0}),
                              _safra(2018, {"A3": 0.0, "C3": 0.0})], _SEM_CUSTO)
    s = out[1]
    assert s["giro_venda"] == pytest.approx(50.0 / 150.0)
    # B3 saiu (50) + aporte (15) = 65, todos para C3; A3 (100) fica como está.
    assert s["giro"] == pytest.approx(65.0 / 165.0)
    assert s["peso_maior"] == pytest.approx(100.0 / 165.0)


def test_simular_aportes_safra_em_caixa_e_fora_da_cadeia():
    out = fe.simular_aportes(
        [_safra(2017, {"A3": 0.0}), {"safra": 2018, "pesos": {}, "rendimento_caixa": 0.10},
         {"safra": 2019, "pesos": {}}], _SEM_CUSTO)
    assert out[1]["caixa"] is True
    assert out[1]["giro_venda"] == pytest.approx(1.0)
    assert out[1]["liquido"] == pytest.approx(0.10)
    assert out[2] == {"safra": 2019, "medida": False}


def test_simular_aportes_conta_custo_e_ir_da_venda():
    cfg = CostConfig.brasil_pf_default()
    out = fe.simular_aportes([_safra(2017, {"A3": 0.0, "B3": 1.0}),
                              _safra(2018, {"A3": 0.0})], cfg, capital=1_000_000.0)
    assert out[0]["custo_pct"] > 0
    assert out[1]["ir_pct"] > 0  # vendeu B3 com lucro, acima da isenção
    assert out[1]["liquido_com_ir"] < out[1]["liquido"]


def test_quem_fica_ignora_dividendo_e_so_tira_por_degradacao():
    motivos = {"A3": [], "B3": [fe.PREJUIZO], "C3": [fe.FALHA_SOLVENCIA]}
    # A3 pode ter DY caindo: sem motivo de saúde, fica.
    assert fe.quem_fica(motivos) == ["A3"]
    assert fe.quem_fica(motivos, fe.MOTIVOS_SO_SOLVENCIA) == ["A3", "B3"]


def test_regra_da_26_fixa_aporte_e_proibe_venda_para_rebalancear():
    regra = fe.REGRA_DE_DECISAO_DIVIDENDO_SEGURADO
    assert fe.VERSAO == "fundamentalistas-1.6.0"
    assert fe.APORTE_DIVIDENDO_SEGURADO == pytest.approx(0.10)
    assert "ninguém é vendido para voltar ao alvo" in regra
    assert "refeitos em abril" not in regra
