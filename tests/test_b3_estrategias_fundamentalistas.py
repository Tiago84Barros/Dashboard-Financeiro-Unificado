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
