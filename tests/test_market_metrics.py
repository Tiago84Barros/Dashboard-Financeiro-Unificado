import data_pipeline.market.metrics as mx


_F = {
    "revenue": 1000.0, "net_income": 200.0, "ebit": 300.0, "ebitda": 350.0,
    "total_assets": 2000.0, "equity": 1000.0, "cash": 100.0,
    "gross_debt": 500.0, "net_debt": 400.0, "fco": 250.0,
    "market_cap": 2000.0,
    "div_ttm": 3.0, "price": 100.0, "eps": 10.0,  # base POR AÇÃO p/ DY e Payout
    "current_assets": 600.0, "current_liabilities": 400.0,  # Liquidez = 1.5
}


def test_compute_snapshot_core_ratios():
    s = {k: v for k, (v, _m) in mx.compute_snapshot(_F).items()}
    assert abs(s["Margem_Liquida"] - 0.20) < 1e-6      # 200/1000
    assert abs(s["Margem_Operacional"] - 0.30) < 1e-6  # 300/1000
    assert abs(s["ROE"] - 0.20) < 1e-6                 # 200/1000
    assert abs(s["ROA"] - 0.10) < 1e-6                 # 200/2000
    assert abs(s["ROIC"] - 300 / 1400) < 1e-6          # 300/(1000+500-100)
    assert abs(s["Endividamento_Total"] - 0.50) < 1e-6 # 500/1000
    assert abs(s["Liquidez_Corrente"] - 1.50) < 1e-6   # 600/400
    assert abs(s["P/L"] - 10.0) < 1e-6                 # 2000/200
    assert abs(s["P/VP"] - 2.0) < 1e-6                 # 2000/1000
    assert abs(s["EV_EBIT"] - 8.0) < 1e-6              # (2000+400)/300
    assert abs(s["P_FCO"] - 8.0) < 1e-6                # 2000/250
    assert abs(s["DY"] - 0.03) < 1e-6                  # 3/100 (por ação)
    assert abs(s["Payout"] - 0.30) < 1e-6             # 3/10  (dps/LPA)


def test_compute_snapshot_drops_out_of_range():
    # margem impossível (>100%) e divisão por zero → omitidos
    s = mx.compute_snapshot({"revenue": 10.0, "net_income": 50.0,   # margem 500%
                             "equity": 0.0, "market_cap": 100.0})
    assert "Margem_Liquida" not in s   # 5.0 fora da faixa [-1,1]
    assert "ROE" not in s              # div por zero


def test_compute_snapshot_partial_inputs():
    # só com receita e lucro → calcula só o possível, sem quebrar
    s = mx.compute_snapshot({"revenue": 100.0, "net_income": 10.0})
    assert abs(s["Margem_Liquida"][0] - 0.10) < 1e-6
    assert "ROE" not in s and "P/L" not in s


def test_to_metric_rows_shape():
    rows = mx.to_metric_rows("PETR4", mx.compute_snapshot(_F))
    assert all(r["ticker"] == "PETR4" and r["period"] == "ttm" for r in rows)
    assert all(r["source"] == "market.compute" for r in rows)
    names = {r["metric_name"] for r in rows}
    assert {"ROE", "P/L", "Margem_Liquida"} <= names


def test_to_metric_rows_annual_low_conf():
    rows = mx.to_metric_rows("PETR4", mx.compute_snapshot(_F),
                             period="annual", year=2024, low_conf=mx.ANNUAL_APPROX)
    by = {r["metric_name"]: r for r in rows}
    assert all(r["period"] == "annual" and r["year"] == 2024 for r in rows)
    # valuation aproximado: confiança menor + sufixo ~aprox no método
    assert by["P/L"]["confidence_score"] == 60.0
    assert by["P/L"]["calculation_method"].endswith("~aprox")
    # fundamentais exatos: confiança cheia, sem sufixo
    assert by["ROE"]["confidence_score"] == 85.0
    assert "~aprox" not in by["ROE"]["calculation_method"]


# ── SINAIS: condições que a faixa coerente descartava como se fossem ausência ──

def test_sinal_patrimonio_negativo():
    """gross_debt/equity com PL negativo dá razão negativa e some na faixa (0, 20)."""
    s = mx.compute_snapshot({**_F, "equity": -500.0})
    assert s["Patrimonio_Negativo"][0] == 1.0
    assert "Endividamento_Total" not in s      # continua indefinível como razão


def test_sinal_endividamento_fora_de_faixa():
    s = mx.compute_snapshot({**_F, "equity": 10.0, "gross_debt": 300.0})
    assert s["Endividamento_Fora_De_Faixa"][0] == 1.0


def test_empresa_sadia_nao_emite_sinal():
    s = mx.compute_snapshot(_F)
    assert not {"Patrimonio_Negativo", "Endividamento_Fora_De_Faixa",
                "FCO_Negativo"} & set(s)


def test_fco_negativo_exige_confirmacao_por_prejuizo():
    """ISAE4 real: transmissora sob IFRIC 12, FCO -R$1,2bi com EBIT de R$4,1bi.

    A contraprestação da concessão entra em INVESTIMENTO, não em operação — e
    banco tem saída operacional por originação de crédito. Das 84 empresas com
    FCO ≤ 0 medidas em 30/07/2026, 31 eram lucrativas (Financeiro liderava com
    21). Sinal sem confirmação excluiria setores inteiros por artefato contábil.
    """
    lucrativa = mx.compute_snapshot(
        {**_F, "fco": -1.2e9, "ebit": 4.1e9, "net_income": 2.5e9})
    assert "FCO_Negativo" not in lucrativa

    com_prejuizo = mx.compute_snapshot(
        {**_F, "fco": -1.2e9, "ebit": -4.1e9, "net_income": -2.5e9})
    assert com_prejuizo["FCO_Negativo"][0] == 1.0


def test_payout_zero_e_valor_medido_nao_ausencia():
    """Empresa que retém todo o lucro tem payout 0 — informação, não lacuna."""
    s = mx.compute_snapshot({**_F, "div_ttm": 0.0})
    assert s["Payout"][0] == 0.0
    assert "DY" not in s          # DY zero segue tratado como ausente (por desenho)

    sem_dado = mx.compute_snapshot({**_F, "div_ttm": None})
    assert "Payout" not in sem_dado


def test_denominador_negativo_nao_vira_retorno_positivo():
    """RAIZ4 real: prejuízo de R$27bi sobre patrimônio de -R$8,3bi dava ROE +328%.

    Das 45 empresas com patrimônio negativo em 30/07/2026, 32 exibiam ROE
    POSITIVO — a faixa (-3, 5) aceita o número, porque os dois sinais se
    cancelam. Para o ranking, desastre virava destaque.
    """
    s = mx.compute_snapshot({**_F, "net_income": -27.1e9, "equity": -8.27e9,
                             "gross_debt": 82.7e9, "cash": 10e9,
                             "ebit": -8.58e9, "total_assets": 105e9})
    assert "ROE" not in s
    assert s["Patrimonio_Negativo"][0] == 1.0
    # ROA tem denominador positivo (ativo total): o sinal é confiável e fica.
    assert s["ROA"][0] < 0


def test_capital_investido_negativo_suprime_roic():
    s = mx.compute_snapshot({**_F, "equity": -900.0, "gross_debt": 100.0,
                             "cash": 300.0})     # -900+100-300 < 0
    assert "ROIC" not in s
