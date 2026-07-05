"""Backtest v2.22 — rebalance em abril, modo com vendas e rank-IC.

Auditoria 2026-07, rodada 2 (pendências 2, 3 e 4 + timing de publicação).
"""
import numpy as np
import pandas as pd
import pytest


def _cfg_on():
    from core.transaction_costs import CostConfig
    return CostConfig.brasil_pf_default()


# ── _executar_rebalance_vendas (unidade) ─────────────────────────────────────

def test_rebalance_vendas_realinha_e_respeita_isencao():
    from views.empresas_b3 import _executar_rebalance_vendas
    cotas  = {"AAA3": 100.0, "BBB3": 0.0}
    custo  = {"AAA3": 1000.0, "BBB3": 0.0}      # preço médio A = 10
    precos = {"AAA3": 20.0, "BBB3": 10.0}       # carteira = 2.000, 100% em A
    pesos  = {"AAA3": 0.5, "BBB3": 0.5}
    ir, fv, fc = _executar_rebalance_vendas(cotas, custo, pesos, precos, _cfg_on())
    # vendeu R$ 1.000 (≤ isenção mensal de 20k) → IR zero; custos de bps > 0
    assert ir == 0.0
    assert fv > 0 and fc > 0
    v_a = cotas["AAA3"] * precos["AAA3"]
    v_b = cotas["BBB3"] * precos["BBB3"]
    assert v_a / (v_a + v_b) == pytest.approx(0.5, abs=0.01)


def test_rebalance_vendas_ir_binario_sobre_lucro_liquido_mensal():
    # Regra RFB (IN 1.585/2015): vendas do mês > 20k ⇒ TODO o ganho líquido
    # é tributável (não pro-rata). Vende 40k com lucro 20k ⇒ IR = 15% × 20k.
    from views.empresas_b3 import _executar_rebalance_vendas
    cotas  = {"AAA3": 4000.0, "BBB3": 0.0}
    custo  = {"AAA3": 40_000.0}                 # avg 10; posição vale 80k
    precos = {"AAA3": 20.0, "BBB3": 10.0}
    pesos  = {"AAA3": 0.5, "BBB3": 0.5}
    ir, _fv, _fc = _executar_rebalance_vendas(cotas, custo, pesos, precos, _cfg_on())
    assert ir == pytest.approx(0.15 * 20_000.0)


def test_rebalance_vendas_conserva_valor():
    # Invariante financeiro: valor_antes == valor_depois + custos + IR —
    # o rebalance não cria nem destrói dinheiro, só paga fricção.
    from views.empresas_b3 import _executar_rebalance_vendas
    cotas  = {"AAA3": 3000.0, "BBB3": 500.0, "CCC3": 0.0}
    custo  = {"AAA3": 30_000.0, "BBB3": 4_000.0, "CCC3": 0.0}
    precos = {"AAA3": 15.0, "BBB3": 12.0, "CCC3": 8.0}
    pesos  = {"AAA3": 0.4, "BBB3": 0.3, "CCC3": 0.3}
    v_antes = sum(cotas[t] * precos[t] for t in cotas)
    ir, fv, fc = _executar_rebalance_vendas(cotas, custo, pesos, precos, _cfg_on())
    v_depois = sum(cotas.get(t, 0.0) * precos[t] for t in precos)
    assert v_depois + ir + fv + fc == pytest.approx(v_antes, rel=1e-9)
    assert ir >= 0 and fv >= 0 and fc >= 0


def test_rebalance_vendas_config_desligada_realinha_exato():
    from core.transaction_costs import CostConfig
    from views.empresas_b3 import _executar_rebalance_vendas
    cotas  = {"AAA3": 100.0, "BBB3": 0.0}
    custo  = {"AAA3": 1000.0}
    precos = {"AAA3": 20.0, "BBB3": 10.0}
    pesos  = {"AAA3": 0.5, "BBB3": 0.5}
    ir, fv, fc = _executar_rebalance_vendas(
        cotas, custo, pesos, precos, CostConfig.desligado())
    assert ir == 0.0 and fv == 0.0 and fc == 0.0
    assert cotas["AAA3"] * 20.0 == pytest.approx(cotas["BBB3"] * 10.0, rel=1e-6)


# ── Integração: rebalance em abril + modo com vendas ────────────────────────

def _hist_sintetico() -> dict[str, pd.DataFrame]:
    anos = list(range(2018, 2025))

    def _df(roe: float) -> pd.DataFrame:
        return pd.DataFrame({
            "Data": [f"{a}-12-31" for a in anos],
            "ROE": [roe] * len(anos),
            "DY": [0.05] * len(anos),
        })

    return {"AAA3": _df(0.30), "BBB3": _df(0.10), "CCC3": _df(0.20)}


def _precos_sinteticos() -> pd.DataFrame:
    idx = pd.date_range("2021-01-31", "2023-12-31", freq="ME")
    base = np.linspace(10.0, 20.0, len(idx))
    return pd.DataFrame(
        {"AAA3": base, "BBB3": base * 1.1, "CCC3": base * 0.9}, index=idx)


def test_backtest_inicia_em_abril_pos_publicacao():
    from views.empresas_b3 import _simular_backtest
    df_bt, _top, _n = _simular_backtest(
        _precos_sinteticos(), pd.DataFrame(), _hist_sintetico(),
        ["AAA3", "BBB3", "CCC3"],
        aporte=1000.0, data_inicio=pd.Timestamp("2021-01-01"),
        taxa_selic_aa=0.10, pesos={"ROE": (1.0, True)}, tk_grupos=None,
        top_n_max=2, usar_gamma=False,
    )
    assert not df_bt.empty
    d0 = pd.to_datetime(df_bt["Data"].iloc[0])
    # série começa em abril: FY N−1 publicado até 31/03 (sem antecipação)
    assert (d0.year, d0.month) == (2021, 4)
    assert df_bt.attrs["rebal_month"] == 4
    assert df_bt.attrs["com_vendas"] is False


def test_backtest_modo_vendas_roda_e_reporta_totais():
    from views.empresas_b3 import _simular_backtest
    df_bt, _top, _n = _simular_backtest(
        _precos_sinteticos(), pd.DataFrame(), _hist_sintetico(),
        ["AAA3", "BBB3", "CCC3"],
        aporte=1000.0, data_inicio=pd.Timestamp("2021-01-01"),
        taxa_selic_aa=0.10, pesos={"ROE": (1.0, True)}, tk_grupos=None,
        top_n_max=2, usar_gamma=False,
        cost_cfg=_cfg_on(), rebalance_com_vendas=True,
    )
    assert not df_bt.empty
    assert df_bt.attrs["com_vendas"] is True
    assert df_bt.attrs["ir_pago"] >= 0.0
    assert df_bt.attrs["custos_rebal"] >= 0.0
    assert float(df_bt["Estratégia"].iloc[-1]) > 0.0


# ── Rank-IC ──────────────────────────────────────────────────────────────────

def test_rank_ic_detecta_sinal_construido():
    from views.empresas_b3 import _rank_ic_por_ano
    anos = list(range(2018, 2024))
    n_tk = 6
    hist = {
        f"TK{i}3": pd.DataFrame({
            "Data": [f"{a}-12-31" for a in anos],
            "ROE": [0.05 * (i + 1)] * len(anos),
            "DY": [0.04] * len(anos),
        })
        for i in range(n_tk)
    }
    idx = pd.date_range("2021-01-31", "2023-12-31", freq="ME")
    prec = {}
    for i in range(n_tk):
        g = (1.0 + 0.05 * i) ** (1.0 / 12.0)   # retorno anual cresce com o ROE
        prec[f"TK{i}3"] = 10.0 * (g ** np.arange(len(idx)))
    df_ic = _rank_ic_por_ano(
        pd.DataFrame(prec, index=idx), hist, list(prec),
        {"ROE": (1.0, True)}, min_n=5,
    )
    # sinal construído: score (ROE) ordena exatamente os retornos ⇒ IC ≈ 1
    assert not df_ic.empty
    assert (df_ic["Rank-IC"] > 0.9).all()
    assert (df_ic["Spread (pp)"] > 0).all()
