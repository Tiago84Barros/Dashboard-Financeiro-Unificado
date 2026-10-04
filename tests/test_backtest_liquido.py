"""Retorno líquido dos backtests (auditoria app4, item 19: FII-10, EUA-J).

O rastreador de IR é a peça que decide quanto imposto um giro paga; os testes
fixam as contas à mão, para que um erro de sinal no custo de aquisição não
passe como "imposto um pouco diferente".
"""
from __future__ import annotations

import pandas as pd
import pytest

from core.backtest_liquido import (
    DIVIDEND_YIELD_EUA_PREMISSA,
    EMOLUMENTOS_B3_BPS_POR_PONTA,
    IOF_INGRESSO_EXTERIOR,
    IOF_REMESSA_EXTERIOR,
    IR_FII_GANHO,
    RETENCAO_DIVIDENDOS_EUA,
    RastreadorIR,
    custo_ponta_b3,
    liquido_eua_brl,
    liquido_safras_b3,
    premissas_b3,
    premissas_fii,
)
from core.ir_renda_variavel import ALIQUOTA
from core.transaction_costs import SPREAD_BPS_LARGE_CAP_DEF, SPREAD_BPS_SMALL_CAP_DEF


def test_aliquota_de_fii_vem_da_apuracao_real():
    """Uma fonte de alíquota só: a mesma da apuração do DARF (PR #331)."""
    assert IR_FII_GANHO == float(ALIQUOTA["fii"]) == 0.20


def test_compra_inicial_nao_paga_imposto():
    rastreador = RastreadorIR(0.20)
    assert rastreador.rebalancear({"A": 0.5, "B": 0.5}) == 0.0


def test_venda_com_ganho_paga_aliquota_sobre_o_ganho_realizado():
    rastreador = RastreadorIR(0.20)
    rastreador.rebalancear({"A": 0.5, "B": 0.5})
    # A sobe 20%, B fica parado: carteira vale 1,10.
    rastreador.evoluir({"A": 0.20, "B": 0.0})
    # Zera A. Em fração da carteira de hoje: A vale 0,6/1,1 e custou 0,5/1,1.
    ir = rastreador.rebalancear({"B": 1.0})
    ganho = (0.6 - 0.5) / 1.1
    assert ir == pytest.approx(0.20 * ganho)


def test_provento_reinvestido_entra_no_custo_e_nao_e_tributado_de_novo():
    """FII: o rendimento é isento. Se ele virasse ganho na venda, o imposto
    incidiria sobre o que a lei isenta."""
    rastreador = RastreadorIR(0.20)
    rastreador.rebalancear({"A": 1.0})
    # Retorno total de 10% todo ele de rendimento: preço parado.
    rastreador.evoluir({"A": 0.10}, renda={"A": 0.10})
    assert rastreador.rebalancear({"B": 1.0}) == pytest.approx(0.0)


def test_prejuizo_compensa_ganho_seguinte():
    rastreador = RastreadorIR(0.20)
    rastreador.rebalancear({"A": 0.5, "B": 0.5})
    rastreador.evoluir({"A": -0.20, "B": 0.0})
    # Vende A com prejuízo: nada a pagar, prejuízo guardado.
    assert rastreador.rebalancear({"B": 1.0}) == 0.0
    assert rastreador.prejuizo == pytest.approx(0.1 / 0.9)
    rastreador.evoluir({"B": 0.30})
    # Vende 20% de B: ganho realizado (0,2 x 0,3/1,3) menor que o prejuízo
    # guardado (0,1/0,9 renormalizado por 1,3).
    ir = rastreador.rebalancear({"B": 0.8, "C": 0.2})
    assert ir == 0.0
    assert rastreador.prejuizo > 0


def test_prejuizo_em_fracao_encolhe_quando_a_carteira_cresce():
    """O prejuízo é um valor em reais; como fração da carteira ele cai quando
    ela cresce. Sem renormalizar, um prejuízo antigo abateria ganho demais."""
    rastreador = RastreadorIR(0.20)
    rastreador.rebalancear({"A": 0.5, "B": 0.5})
    rastreador.evoluir({"A": -0.20, "B": 0.0})
    rastreador.rebalancear({"B": 1.0})
    guardado = rastreador.prejuizo
    rastreador.evoluir({"B": 1.0})          # carteira dobra
    rastreador.rebalancear({"B": 1.0})      # nada vendido
    assert rastreador.prejuizo == pytest.approx(guardado / 2)


def test_premissas_fii_saem_em_bps_e_declaram_isencao():
    premissas = premissas_fii(.0015, .0010)
    assert premissas["custo_transacao_bps"] == 15
    assert premissas["slippage_bps"] == 10
    assert premissas["ir_ganho_capital"] == 0.20
    assert premissas["rendimento_isento"] is True
    assert premissas["ir_na_liquidacao_final"] is False


# ── integração com o walk-forward do FII ─────────────────────────────────────

def _fixture_fii(*, com_preco: bool, parcela_preco: float = 1.0):
    """Mesmo universo de `test_fii_validation_v4`, com giro forçado."""
    snapshots = []
    types = ["tijolo", "papel", "fof", "hibrido"] * 3
    decisions = ("2025-01-31", "2025-02-28", "2025-03-31")
    for decision_index, decision in enumerate(decisions):
        for index, fii_type in enumerate(types):
            # A ordem do score se inverte a cada decisão: o otimizador troca a
            # carteira e realiza ganho -- sem giro não haveria o que tributar.
            score = 80 - index if decision_index % 2 == 0 else 69 + index
            row = {
                "ticker": f"F{index:03d}11", "tipo": fii_type,
                "type_score": score, "confidence": .90,
                "coverage": .95, "dy_12m": .10, "pvp": .90,
                "income_recurrence": .90,
                "liquidez_diaria": 3_000_000, "history_months": 36,
                "max_drawdown": -.15, "duration_anos": 3.0, "leverage": .05,
                "vacancia_fisica": .05, "delinquency": .01, "ltv": .55,
                "manager": f"manager-{index}", "sector": f"sector-{index}",
            }
            if fii_type in {"tijolo", "hibrido"}:
                row.update(tenants={f"tenant-{index}": 1.0},
                           regions={f"region-{index}": 1.0})
            if fii_type in {"papel", "hibrido"}:
                row.update(debtors={f"debtor-{index}": 1.0},
                           issuers={f"issuer-{index}": 1.0},
                           indexers={f"indexer-{index}": 1.0})
            snapshots.append({
                "reference_date": decision, "available_at": decision,
                "ticker": row["ticker"], "fii_type": fii_type,
                "score": row["type_score"], "confidence": row["confidence"],
                "coverage": row["coverage"],
                "availability_quality": "verified_publication",
                "portfolio_input_json": row,
            })
    return_dates = pd.date_range("2023-01-31", "2025-04-30", freq="ME")
    linhas = []
    for date in return_dates:
        for index in range(12):
            total = .02 + index / 1_000 + (date.month % 3) / 1_000_000
            linha = {"date": date, "ticker": f"F{index:03d}11", "total_return": total}
            if com_preco:
                linha["price_return"] = total * parcela_preco
            linhas.append(linha)
    returns = pd.DataFrame(linhas)
    benchmark = pd.Series(.005, index=return_dates)
    scenarios = {d: {"selic": 12.0, "ipca": 4.5} for d in decisions}
    return pd.DataFrame(snapshots), returns, benchmark, scenarios


def _rodar(**kwargs):
    from core.fii_validation import robust_optimizer_point_in_time_backtest
    snapshots, returns, benchmark, scenarios = _fixture_fii(**kwargs)
    return robust_optimizer_point_in_time_backtest(
        snapshots, returns, benchmark, scenarios,
        transaction_cost=.0015, slippage=.0010,
    )


def test_fii_liquido_e_o_padrao_e_fica_abaixo_do_apos_custos_com_giro():
    result = _rodar(com_preco=True)
    assert result["status"] == "calculated", result
    assert result["ir_calculado"] is True
    obs = result["observations"]
    assert sum(o["turnover"] for o in obs[1:]) > 0, "fixture sem giro não testa IR"
    for o in obs:
        assert o["portfolio_return"] == pytest.approx(
            o["portfolio_return_apos_custos"] - o["ir_periodo"])
        assert o["portfolio_return_apos_custos"] <= o["portfolio_return_bruto"]
    assert result["mean_ir_periodo"] > 0
    assert result["mean_excess"] < result["mean_excess_apos_custos"] \
        <= result["mean_excess_bruto"]
    assert result["premissas_liquido"]["ir_ganho_capital"] == 0.20


def test_fii_retorno_todo_de_rendimento_nao_paga_ir():
    """Rendimento isento: com preço parado, o líquido é o após-custos."""
    result = _rodar(com_preco=True, parcela_preco=0.0)
    assert result["ir_calculado"] is True
    assert result["mean_ir_periodo"] == pytest.approx(0.0)
    assert result["mean_excess"] == pytest.approx(result["mean_excess_apos_custos"])


def test_fii_sem_coluna_de_preco_nao_inventa_imposto_e_avisa():
    result = _rodar(com_preco=False)
    assert result["status"] == "calculated", result
    assert result["ir_calculado"] is False
    assert result["mean_excess"] == pytest.approx(result["mean_excess_apos_custos"])


def test_monthly_returns_separa_preco_de_rendimento():
    from data_pipeline.market.fii_pit import _monthly_returns
    prices = pd.DataFrame({
        "ticker": ["A"] * 2,
        "date": ["2026-01-30", "2026-02-27"],
        "close": [100.0, 101.0],
        "adjusted_close": [100.0, 102.0],
    })
    dividends = pd.DataFrame({"ticker": ["A"], "event_date": ["2026-02-10"],
                              "ex_date": [None], "payment_date": [None],
                              "amount": [1.0]})
    result = _monthly_returns(prices, dividends, as_of="2026-03-15")
    linha = result.iloc[-1]
    assert linha["total_return"] == pytest.approx(0.02)
    assert linha["price_return"] == pytest.approx(0.01)


def test_tela_de_fii_mostra_as_premissas_que_o_calculo_usou():
    from views.fiis import _texto_premissas_liquido_fii
    texto = _texto_premissas_liquido_fii(premissas_fii(.0015, .0010))
    assert "15 bps" in texto and "10 bps" in texto
    assert "20%" in texto and "isento" in texto


# ── EUA em reais ─────────────────────────────────────────────────────────────

def _fx(**meses):
    return pd.Series({pd.Timestamp(k.replace("m", "-")) + pd.offsets.MonthEnd(0): v
                      for k, v in meses.items()})


def test_eua_um_periodo_conta_a_mao():
    """Um período só, sem giro anterior: retenção, câmbio e as duas pontas de
    IOF; o IR ainda não incide (nada vendido)."""
    fx = _fx(**{"2020m06": 5.0, "2021m06": 5.5})
    periodos = [{"date": "2020-06-30", "pesos": {"A": 1.0}, "turnover": 1.0,
                 "fwd": {"A": 0.10}, "fwd_preco": {}, "ew_usd": 0.05}]
    res = liquido_eua_brl(periodos, fx)
    assert res["ok"], res
    retido = RETENCAO_DIVIDENDOS_EUA * DIVIDEND_YIELD_EUA_PREMISSA
    custo = 1.0 * 20 / 10_000
    esperado = ((1 + 0.10 - retido - custo) * 1.1
                * (1 - IOF_REMESSA_EXTERIOR) * (1 - IOF_INGRESSO_EXTERIOR) - 1)
    assert res["portfolio_brl_liquido"]["ann_return"] == pytest.approx(esperado)
    assert res["portfolio_brl_bruto"]["ann_return"] == pytest.approx(1.10 * 1.1 - 1)
    assert res["equal_weight_brl"]["ann_return"] == pytest.approx(1.05 * 1.1 - 1)
    assert res["ir_medio"] == 0.0


def test_eua_ganho_cambial_e_tributado_no_rebalanceamento():
    """Ação parada em dólar com o real perdendo 20%: vender realiza ganho em
    reais, e a Lei 14.754 tributa esse ganho."""
    fx = _fx(**{"2020m06": 5.0, "2021m06": 6.0, "2022m06": 6.0})
    periodos = [
        {"date": "2020-06-30", "pesos": {"A": 1.0}, "turnover": 1.0,
         "fwd": {"A": 0.0}, "fwd_preco": {"A": 0.0}, "ew_usd": 0.0},
        {"date": "2021-06-30", "pesos": {"B": 1.0}, "turnover": 1.0,
         "fwd": {"B": 0.0}, "fwd_preco": {"B": 0.0}, "ew_usd": 0.0},
    ]
    res = liquido_eua_brl(periodos, fx)
    assert res["ok"], res
    # Ganho em fração da carteira na venda: (1,2 - 1,0) / 1,2.
    assert res["ir_medio"] * 2 == pytest.approx(0.15 * 0.2 / 1.2)
    assert res["premissas"]["dividendo"] == "observado no painel"


def test_eua_sem_cambio_nomeia_o_mes_em_vez_de_assumir_constante():
    fx = _fx(**{"2020m06": 5.0})
    periodos = [{"date": "2020-06-30", "pesos": {"A": 1.0}, "turnover": 1.0,
                 "fwd": {"A": 0.1}, "fwd_preco": {}, "ew_usd": 0.0}]
    res = liquido_eua_brl(periodos, fx)
    assert res["ok"] is False
    assert "2021-06" in res["motivo"]


def test_walk_forward_com_cambio_nao_mexe_nas_chaves_brutas():
    """O bootstrap guardado em data/vantagem_oos.json lê as chaves brutas: o
    bloco em reais é acréscimo, não substituição."""
    from core.us_backtest import walk_forward
    linhas = []
    for ano in range(2015, 2021):
        for i in range(6):
            linhas.append({"date": pd.Timestamp(f"{ano}-06-30"),
                           "symbol": f"S{(i + ano) % 6}", "score": float(i),
                           "fwd_return": 0.02 * i + (ano % 3) * 0.01})
    painel = pd.DataFrame(linhas)
    fx = pd.Series(
        {pd.Timestamp(f"{a}-06-30"): 3.0 + 0.2 * (a - 2015) for a in range(2015, 2022)})
    sem = walk_forward(painel, top_n=3, periods_per_year=1)
    com = walk_forward(painel, top_n=3, periods_per_year=1, cambio_usdbrl=fx)
    for chave in ("portfolio", "equal_weight", "excess_ann_vs_ew",
                  "bootstrap_excess", "rank_ic"):
        assert com[chave] == sem[chave]
    assert "liquido" not in sem
    liq = com["liquido"]
    assert liq["ok"], liq
    assert liq["portfolio_brl_liquido"]["ann_return"]         < liq["portfolio_brl_bruto"]["ann_return"]
    assert liq["custo_medio"] > 0 and liq["retencao_media"] > 0
    assert liq["iof_total"] == pytest.approx(
        IOF_REMESSA_EXTERIOR + IOF_INGRESSO_EXTERIOR)


def test_usdbrl_mensal_completa_a_mensal_com_a_diaria():
    from core.us_read import _serie_mensal
    mensal = _serie_mensal(pd.DataFrame({"data": ["2025-06-30"], "valor": [5.4571]}),
                           "data", "valor")
    diaria = _serie_mensal(pd.DataFrame({
        "data": ["2025-06-27", "2025-06-30", "2026-06-29", "2026-06-30"],
        "valor": [5.47, 5.4784, 5.30, 5.31]}), "data", "valor")
    serie = mensal.combine_first(diaria)
    assert serie[pd.Timestamp("2025-06-30")] == 5.4571      # mensal manda
    assert serie[pd.Timestamp("2026-06-30")] == 5.31        # diária completa


def test_tela_dos_eua_mostra_as_premissas_que_o_calculo_usou():
    from core.backtest_liquido import premissas_eua
    from views.empresas_americanas import _texto_premissas_liquido_us
    texto = _texto_premissas_liquido_us(premissas_eua() | {"dividendo": "premissa de yield"})
    for trecho in ("10 bps", "30%", "1,5%", "15%", "1,1%", "0,38%", "Lei 14.754"):
        assert trecho in texto, trecho


# ── B3: safras anuais encadeadas (B3-08) ─────────────────────────────────────

_PONTA_SMALL = (EMOLUMENTOS_B3_BPS_POR_PONTA + SPREAD_BPS_SMALL_CAP_DEF / 2) / 1e4
_PONTA_LARGE = (EMOLUMENTOS_B3_BPS_POR_PONTA + SPREAD_BPS_LARGE_CAP_DEF / 2) / 1e4


def test_b3_custo_por_ponta_separa_large_de_small():
    assert custo_ponta_b3("PETR4") == pytest.approx(_PONTA_LARGE)
    assert custo_ponta_b3("XPTO3") == pytest.approx(_PONTA_SMALL)
    assert custo_ponta_b3("PETR4") < custo_ponta_b3("XPTO3")


def test_b3_primeira_safra_paga_so_a_compra():
    out = liquido_safras_b3([({"XPTO3": 0.5, "ABCD3": 0.5},
                              {"XPTO3": 0.1, "ABCD3": 0.1})])
    assert out[0]["giro"] == pytest.approx(0.0)
    assert out[0]["ir"] == pytest.approx(0.0)
    assert out[0]["custo"] == pytest.approx(_PONTA_SMALL)


def test_b3_troca_total_realiza_o_ganho_da_safra_anterior():
    """Ganho de 10% numa posição única, vendida inteira no abril seguinte:
    ganho realizado = 1 − 1/1,1 da carteira; IR = 15% disso; compra e venda
    pagam uma ponta cada."""
    out = liquido_safras_b3([({"XPTO3": 1.0}, {"XPTO3": 0.10}),
                             ({"ABCD3": 1.0}, {"ABCD3": 0.0})])
    assert out[1]["giro"] == pytest.approx(1.0)
    assert out[1]["custo"] == pytest.approx(2 * _PONTA_SMALL)
    assert out[1]["ir"] == pytest.approx(0.15 * (1 - 1 / 1.1))


def test_b3_giro_sai_da_carteira_derivada_nao_do_alvo_anterior():
    """Mesmo alvo 50/50 nos dois anos, mas um papel dobrou: a carteira chega
    em 2/3–1/3 e o rebalanceamento vende 1/6 dela. Comparar alvo com alvo
    daria giro zero e IR zero."""
    pesos = {"XPTO3": 0.5, "ABCD3": 0.5}
    out = liquido_safras_b3([(pesos, {"XPTO3": 1.0, "ABCD3": 0.0}),
                             (pesos, {"XPTO3": 0.0, "ABCD3": 0.0})])
    assert out[1]["giro"] == pytest.approx(2 / 3 - 1 / 2)
    assert out[1]["custo"] == pytest.approx(2 * (1 / 6) * _PONTA_SMALL)
    # Vendido 1/6 de uma posição de 2/3 com custo 1/3: metade do vendido é
    # ganho.
    assert out[1]["ir"] == pytest.approx(0.15 * (1 / 6) * 0.5)


def test_b3_prejuizo_de_uma_safra_compensa_o_ganho_da_seguinte():
    out = liquido_safras_b3([({"XPTO3": 1.0}, {"XPTO3": -0.5}),
                             ({"ABCD3": 1.0}, {"ABCD3": 1.0}),
                             ({"EFGH3": 1.0}, {"EFGH3": 0.0})])
    assert out[1]["ir"] == pytest.approx(0.0)   # vendeu com prejuízo
    # Prejuízo de 0,5 da carteira de 0,5 = 100% dela; renormalizado pela
    # carteira que dobrou, vale 0,5 contra um ganho de 0,5: zera.
    assert out[2]["ir"] == pytest.approx(0.0)


def test_b3_safra_nao_mensuravel_fica_none_e_nao_quebra_a_cadeia():
    out = liquido_safras_b3([({"XPTO3": 1.0}, None),
                             ({"XPTO3": 1.0}, {"XPTO3": 0.1})])
    assert out[0] is None
    assert out[1]["custo"] == pytest.approx(_PONTA_SMALL)


def test_premissas_b3_declaram_ir_sem_isencao_e_dividendo_no_preco():
    p = premissas_b3()
    assert p["ir_ganho_capital"] == pytest.approx(float(ALIQUOTA["comum"]))
    assert p["isencao_20k_aplicada"] is False
    assert p["dividendo_separado_do_preco"] is False
    assert p["meio_spread_small_bps"] > p["meio_spread_large_bps"]
