import numpy as np
import pandas as pd
import pytest

from core.correlation_analysis import (
    DEFAULT_CORR_PERIOD,
    MIN_CORR_MONTHS,
    calcular_correlacao_mensal,
    classificar_correlacao,
    correlacao_media_ponderada,
    converter_precos_para_brl,
    intervalo_confianca_correlacao,
    retornos_mensais,
)


def _daily_prices(months=25):
    idx = pd.date_range("2024-01-01", periods=months, freq="MS")
    idx = pd.date_range(idx.min(), idx.max() + pd.offsets.MonthEnd(0), freq="D")
    mensal = pd.factorize(idx.to_period("M"))[0]
    fator = 100.0 * np.power(1.01, mensal)
    return pd.DataFrame({"A": fator, "B": fator * 2.0}, index=idx)


def test_fonte_diaria_e_convertida_para_retorno_mensal():
    rets = retornos_mensais(_daily_prices(), min_obs=18)
    assert len(rets) < 30
    assert len(rets) >= MIN_CORR_MONTHS
    assert rets["A"].dropna().iloc[-1] == pytest.approx(0.01)


def test_correlacao_exige_dezoito_meses_sobrepostos_por_par():
    precos = _daily_prices()
    precos.loc[precos.index < "2025-08-01", "B"] = np.nan
    result = calcular_correlacao_mensal(precos, min_obs=18)
    assert result["corr"].empty


def test_diagnostico_reporta_cobertura_pairwise():
    result = calcular_correlacao_mensal(_daily_prices(), min_obs=18)
    assert result["frequency"] == "mensal"
    assert result["min_obs"] == 18
    assert result["corr"].loc["A", "B"] == pytest.approx(1.0)
    assert result["overlap"].loc["A", "B"] >= 18


def test_ausencia_nao_e_preenchida_com_retorno_zero():
    precos = _daily_prices()
    mask = (precos.index >= "2024-06-01") & (precos.index < "2024-07-01")
    precos.loc[mask, "B"] = np.nan
    rets = retornos_mensais(precos, min_obs=1)
    junho = pd.Timestamp("2024-06-30")
    assert pd.isna(rets.loc[junho, "B"])


def test_politica_padrao_exige_janela_mais_longa():
    assert DEFAULT_CORR_PERIOD == "5y"
    assert MIN_CORR_MONTHS == 24


def test_preco_usd_e_convertido_com_cambio_historico_para_brl():
    idx = pd.to_datetime(["2025-01-02", "2025-02-03"])
    precos = pd.DataFrame({"BR": [100.0, 110.0], "US": [100.0, 100.0]}, index=idx)
    usd_brl = pd.Series([5.0, 6.0], index=idx)

    result = converter_precos_para_brl(
        precos,
        {"BR": "BRL", "US": "USD"},
        {"USD": usd_brl},
    )

    assert result["prices"]["BR"].tolist() == [100.0, 110.0]
    assert result["prices"]["US"].tolist() == [500.0, 600.0]
    assert result["converted"] == ["US"]
    assert result["missing_fx"] == []


def test_ativo_estrangeiro_sem_cambio_permanece_ausente():
    idx = pd.to_datetime(["2025-01-02", "2025-02-03"])
    precos = pd.DataFrame({"US": [100.0, 105.0]}, index=idx)

    result = converter_precos_para_brl(precos, {"US": "USD"}, {})

    assert result["prices"]["US"].isna().all()
    assert result["converted"] == []
    assert result["missing_fx"] == ["US"]


def test_cambio_antigo_nao_e_propagado_indefinidamente():
    precos = pd.DataFrame(
        {"US": [100.0]},
        index=pd.to_datetime(["2025-01-20"]),
    )
    usd_brl = pd.Series(
        [5.0],
        index=pd.to_datetime(["2025-01-01"]),
    )

    result = converter_precos_para_brl(
        precos,
        {"US": "USD"},
        {"USD": usd_brl},
        max_gap_days=7,
    )

    assert pd.isna(result["prices"].iloc[0, 0])


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        (0.82, "Alta positiva"),
        (0.55, "Moderada positiva"),
        (-0.82, "Alta inversa"),
        (-0.55, "Moderada inversa"),
        (0.05, "Baixa dependência"),
    ],
)
def test_classificacao_informa_intensidade_e_direcao(valor, esperado):
    assert classificar_correlacao(valor) == esperado


def test_correlacao_media_usa_pesos_da_carteira():
    corr = pd.DataFrame(
        [
            [1.0, 0.9, 0.1],
            [0.9, 1.0, 0.2],
            [0.1, 0.2, 1.0],
        ],
        index=["A", "B", "C"],
        columns=["A", "B", "C"],
    )
    pesos = {"A": 0.80, "B": 0.15, "C": 0.05}
    esperado = (
        0.80 * 0.15 * 0.9
        + 0.80 * 0.05 * 0.1
        + 0.15 * 0.05 * 0.2
    ) / (0.80 * 0.15 + 0.80 * 0.05 + 0.15 * 0.05)

    assert correlacao_media_ponderada(corr, pesos) == pytest.approx(esperado)


def test_intervalo_de_confianca_exige_mais_de_tres_observacoes():
    assert intervalo_confianca_correlacao(0.5, 3) is None
    inferior, superior = intervalo_confianca_correlacao(0.5, 24)
    assert inferior < 0.5 < superior


def test_tabela_distingue_correlacao_inversa_e_expoe_incerteza():
    from views.investimentos import _corr_pairs

    corr = pd.DataFrame(
        [[1.0, -0.55], [-0.55, 1.0]],
        index=["A", "B"],
        columns=["A", "B"],
    )
    overlap = pd.DataFrame(
        [[24, 24], [24, 24]],
        index=["A", "B"],
        columns=["A", "B"],
    )

    pares = _corr_pairs(corr, overlap)

    assert pares.iloc[0]["Correlação"] == pytest.approx(-0.55)
    assert pares.iloc[0]["Leitura"] == "Moderada inversa"
    assert pares.iloc[0]["Observações"] == 24
    assert pares.iloc[0]["IC 95%"] != "—"


def test_build_envia_ao_provedor_apenas_simbolos_e_moedas_publicas(monkeypatch):
    import views.investimentos as view

    recebido = {}

    def fake_loader(symbol_map):
        recebido["symbol_map"] = symbol_map
        return {
            "corr": pd.DataFrame(),
            "returns": pd.DataFrame(),
            "symbols_ok": [],
        }

    monkeypatch.setattr(view, "_load_corr_precos", fake_loader)
    posicoes = [
        {
            "ticker": "PETR3",
            "classe": "Ações",
            "pais": "BR",
            "moeda": "BRL",
            "valor_mercado": 8_000.0,
            "quantidade": 123.0,
        },
        {
            "ticker": "SPY",
            "classe": "ETF",
            "pais": "US",
            "moeda": "USD",
            "valor_mercado": 2_000.0,
            "quantidade": 7.0,
        },
    ]

    result = view._build_corr_data(posicoes)

    assert recebido["symbol_map"] == (
        ("PETR3", "PETR3.SA", "BRL"),
        ("SPY", "SPY", "USD"),
    )
    assert result["weights"] == {"PETR3": 8_000.0, "SPY": 2_000.0}
    assert all(len(item) == 3 for item in recebido["symbol_map"])


def test_hgre11_usa_simbolo_canonico_e_fixture_ativa():
    from core.investimentos import _MOCK_POSICOES_RAW
    from views.investimentos import _yf_symbol_for_pos

    simbolo = _yf_symbol_for_pos(
        {
            "ticker": "HGRE11",
            "classe": "FII",
            "pais": "BR",
            "moeda": "BRL",
        }
    )
    fixtures = [item for item in _MOCK_POSICOES_RAW if item[0] == "HGRE11"]

    assert simbolo == "HGRE11.SA"
    assert len(fixtures) == 1
    assert fixtures[0][1] == "Pátria Escritórios FII"
    assert fixtures[0][2:5] == ("reit", "real_estate", "BRL")
