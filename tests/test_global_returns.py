"""Retornos mensais dos ativos da carteira e relatorio de cobertura."""
import numpy as np
import pandas as pd
import pytest

from core.global_portfolio.returns import MIN_OBS, retornos_mensais


def _posicoes():
    return pd.DataFrame([
        {"asset_class": "b3", "symbol": "PETR4", "weight_global": 0.3},
        {"asset_class": "fii", "symbol": "HGLG11", "weight_global": 0.3},
        {"asset_class": "us", "symbol": "AAPL", "weight_global": 0.4},
    ])


def _precos(tickers, meses=36):
    idx = pd.date_range("2023-01-31", periods=meses, freq="ME")
    rng = np.random.default_rng(7)
    return pd.DataFrame(
        {t: 100 * np.cumprod(1 + rng.normal(0.01, 0.05, meses)) for t in tickers},
        index=idx,
    )


def _loader(disponiveis=("PETR4", "HGLG11"), meses=36):
    def carregar(tickers):
        alvo = [t for t in tickers if t in disponiveis]
        return _precos(alvo, meses) if alvo else pd.DataFrame()
    return carregar


def _fx_loader(*, retorno_mensal=0.0, meses=36, deslocamento_dias=0,
               source="asset_quotes:USDBRL", inicio="2023-01-31"):
    """USD/BRL point-in-time sintético: BRL por USD, sem rede/banco."""
    def carregar(moedas):
        assert moedas == ("USD",)
        idx = pd.date_range(inicio, periods=meses, freq="ME")
        idx = idx - pd.Timedelta(days=deslocamento_dias)
        serie = pd.Series(
            5.0 * np.cumprod(np.repeat(1.0 + retorno_mensal, meses)),
            index=idx,
            name="USD",
        )
        serie.attrs.update({"source": source, "convention": "BRL_per_USD"})
        return {"USD": serie}
    return carregar


def test_us_entra_na_busca_de_precos():
    """A serie mensal dos EUA agora existe (core.us_read.load_precos_mensais_us),
    entao AAPL vira candidato como qualquer b3/fii e chega ate o loader.
    """
    pedidos = []

    def espiao(tickers):
        pedidos.append(tuple(sorted(tickers)))
        return _precos([t for t in tickers if t != "AAPL"])

    retornos_mensais(_posicoes(), loader=espiao, fx_loader=_fx_loader())
    assert pedidos == [("AAPL", "HGLG11", "PETR4")]


def test_us_sem_serie_disponivel_aparece_como_sem_serie():
    """AAPL segue sem serie aqui porque o loader de teste (_loader) so tem
    dados para PETR4/HGLG11 - nao porque a classe `us` esteja excluida.
    """
    _, cob = retornos_mensais(_posicoes(), loader=_loader(), fx_loader=_fx_loader())
    assert cob.simbolos_sem_serie == ("AAPL",)
    assert cob.simbolos_com_serie == ("HGLG11", "PETR4")


def test_peso_coberto_e_a_fracao_com_serie():
    _, cob = retornos_mensais(_posicoes(), loader=_loader(), fx_loader=_fx_loader())
    assert cob.peso_coberto == pytest.approx(0.6)


def test_retornos_sao_variacao_percentual_mensal():
    ret, _ = retornos_mensais(
        _posicoes(), loader=_loader(meses=25), fx_loader=_fx_loader(meses=25))
    assert list(ret.columns) == ["HGLG11", "PETR4"]
    assert len(ret) == 24            # 25 precos -> 24 retornos
    assert ret.abs().max().max() < 1.0


def test_simbolo_com_serie_curta_e_descartado():
    ret, cob = retornos_mensais(
        _posicoes(), loader=_loader(meses=MIN_OBS),
        fx_loader=_fx_loader(meses=MIN_OBS))
    # MIN_OBS precos -> MIN_OBS-1 retornos, abaixo do piso
    assert ret.empty
    assert cob.simbolos_com_serie == ()
    assert set(cob.simbolos_sem_serie) == {"AAPL", "HGLG11", "PETR4"}


def test_meses_reflete_o_tamanho_do_quadro():
    ret, cob = retornos_mensais(
        _posicoes(), loader=_loader(meses=30), fx_loader=_fx_loader(meses=30))
    assert cob.meses == len(ret) == 29


def test_quadro_vazio_nao_levanta():
    vazio = pd.DataFrame(columns=["asset_class", "symbol", "weight_global"])
    ret, cob = retornos_mensais(vazio, loader=_loader(), fx_loader=_fx_loader())
    assert ret.empty
    assert cob.peso_coberto == 0.0
    assert cob.simbolos_com_serie == ()


def test_colunas_em_ordem_deterministica():
    ret, _ = retornos_mensais(
        _posicoes(), loader=_loader(("HGLG11", "PETR4")), fx_loader=_fx_loader())
    assert list(ret.columns) == sorted(ret.columns)


def test_gap_interior_vira_nan_e_nao_zero_fabricado():
    """Um preco ausente no meio da serie nao pode virar 0% de retorno.

    fill_method='pad' (default antigo do pandas) forward-preenche o preco
    faltante antes de calcular a variacao, fabricando um retorno de 0% que
    parece um mes real de calmaria. O gap tem que sobreviver como NaN.
    """
    idx = pd.date_range("2023-01-31", periods=25, freq="ME")
    precos_petr4 = [100.0 + i for i in range(25)]
    precos_petr4[10] = np.nan  # gap interior — nao e o primeiro preco da serie
    precos_hglg11 = [50.0 + i * 0.5 for i in range(25)]
    quadro = pd.DataFrame({"PETR4": precos_petr4, "HGLG11": precos_hglg11}, index=idx)

    def loader(tickers):
        return quadro[[t for t in tickers if t in quadro.columns]]

    ret, cob = retornos_mensais(_posicoes(), loader=loader, fx_loader=_fx_loader(meses=25))

    mes_do_gap = idx[10]
    mes_seguinte = idx[11]     # tambem depende do preco ausente (preco anterior = NaN)
    assert mes_do_gap not in ret.index
    assert mes_seguinte not in ret.index
    assert not ret.isna().any().any()

    # Os dois meses saem da janela COMUM: nenhum painel varia pesos em silêncio.
    assert len(ret) == 22
    assert cob.meses_removidos_intersecao == (
        mes_do_gap.date().isoformat(), mes_seguinte.date().isoformat())
    # mesmo com o gap a serie continua acima do MIN_OBS e permanece coberta
    assert "PETR4" in cob.simbolos_com_serie


def test_us_entra_nas_classes_com_preco():
    from core.global_portfolio.returns import _CLASSES_COM_PRECO
    assert "us" in _CLASSES_COM_PRECO


def test_retornos_juntam_series_das_duas_fontes_alinhadas_por_mes():
    idx = pd.date_range("2022-01-31", periods=30, freq="ME")
    br = pd.DataFrame({"PETR4": range(100, 130)}, index=idx).astype(float)
    us = pd.DataFrame({"AAPL": range(200, 230)}, index=idx).astype(float)

    df = pd.DataFrame([
        {"asset_class": "b3", "symbol": "PETR4", "weight_global": 0.5, "payload": {}},
        {"asset_class": "us", "symbol": "AAPL", "weight_global": 0.5, "payload": {}},
    ])

    ret, cob = retornos_mensais(df, loader=lambda tks: pd.concat(
        [br[[c for c in br.columns if c in tks]],
         us[[c for c in us.columns if c in tks]]], axis=1),
        fx_loader=_fx_loader(meses=30, inicio="2022-01-31"))
    assert set(ret.columns) == {"PETR4", "AAPL"}
    assert cob.peso_coberto == 1.0
    assert not ret.isna().all().any(), "coluna toda NaN indica desalinhamento de indice"


def test_retorno_usd_em_brl_combina_ativo_e_apreciacao_cambial():
    idx = pd.date_range("2023-01-31", periods=25, freq="ME")
    precos = pd.DataFrame({"AAPL": 100 * np.cumprod(np.repeat(1.10, 25))}, index=idx)
    pos = pd.DataFrame([
        {"asset_class": "us", "symbol": "AAPL", "weight_global": 1.0},
    ])

    ret, cob = retornos_mensais(
        pos, loader=lambda _: precos,
        fx_loader=_fx_loader(retorno_mensal=0.20, meses=25),
    )

    # (1 + 10%) * (1 + 20%) - 1 = 32%; somar daria 30%.
    assert ret["AAPL"].dropna().to_numpy() == pytest.approx([0.32] * 24)
    assert cob.base_currency == "BRL"
    assert cob.fx_evidence[0].convention == "BRL_per_USD"
    assert cob.fx_evidence[0].source == "asset_quotes:USDBRL"


def test_retorno_usd_em_brl_combina_depreciacao_cambial():
    idx = pd.date_range("2023-01-31", periods=25, freq="ME")
    precos = pd.DataFrame({"AAPL": 100 * np.cumprod(np.repeat(1.10, 25))}, index=idx)
    pos = pd.DataFrame([
        {"asset_class": "us", "symbol": "AAPL", "weight_global": 1.0},
    ])

    ret, _ = retornos_mensais(
        pos, loader=lambda _: precos,
        fx_loader=_fx_loader(retorno_mensal=-0.10, meses=25),
    )

    assert ret["AAPL"].dropna().to_numpy() == pytest.approx([-0.01] * 24)


def test_fx_ausente_exclui_ativo_estrangeiro_e_explica_cobertura():
    pos = pd.DataFrame([
        {"asset_class": "b3", "symbol": "PETR4", "weight_global": 0.4},
        {"asset_class": "us", "symbol": "AAPL", "weight_global": 0.6},
    ])
    precos = _precos(("PETR4", "AAPL"), meses=25)

    ret, cob = retornos_mensais(
        pos, loader=lambda _: precos, fx_loader=lambda _: {})

    assert list(ret.columns) == ["PETR4"]
    assert cob.simbolos_sem_fx == ("AAPL",)
    assert cob.peso_coberto == pytest.approx(0.4)


def test_fx_stale_falha_fechado_e_informa_idade_maxima():
    pos = pd.DataFrame([
        {"asset_class": "us", "symbol": "AAPL", "weight_global": 1.0},
    ])
    precos = _precos(("AAPL",), meses=25)

    ret, cob = retornos_mensais(
        pos,
        loader=lambda _: precos,
        fx_loader=_fx_loader(meses=25, deslocamento_dias=20),
        max_fx_age_days=7,
    )

    assert ret.empty
    assert cob.simbolos_fx_stale == ("AAPL",)
    assert cob.peso_coberto == 0.0
    assert cob.fx_evidence[0].max_age_days == 7
    # 25 preços fornecem 24 retornos candidatos; o primeiro preço é somente
    # denominador da primeira variação e não entra na cobertura cambial.
    assert cob.fx_evidence[0].meses_candidatos == 24
    assert cob.fx_evidence[0].stale_months == 24


def test_reconciliacao_retorno_convertido_igual_preco_local_vezes_fx():
    idx = pd.date_range("2023-01-31", periods=25, freq="ME")
    local = pd.Series(100 * np.cumprod(np.repeat(1.03, 25)), index=idx)
    fx = pd.Series(5 * np.cumprod(np.repeat(1.02, 25)), index=idx, name="USD")
    fx.attrs.update({"source": "fixture", "convention": "BRL_per_USD"})
    pos = pd.DataFrame([
        {"asset_class": "us", "symbol": "AAPL", "weight_global": 1.0},
    ])

    ret, _ = retornos_mensais(
        pos,
        loader=lambda _: local.rename("AAPL").to_frame(),
        fx_loader=lambda _: {"USD": fx},
    )
    esperado = (local * fx).pct_change(fill_method=None).dropna()

    pd.testing.assert_series_equal(
        ret["AAPL"].dropna(), esperado, check_names=False, rtol=1e-12, atol=1e-12)


def test_loader_fx_padrao_preserva_timestamp_fonte_e_convenção(monkeypatch):
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import StaticPool

    from core.global_portfolio.returns import _default_fx_loader

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE assets (id TEXT PRIMARY KEY, ticker TEXT)"))
        conn.execute(text(
            "CREATE TABLE asset_quotes (asset_id TEXT, timestamp TEXT, close REAL)"))
        conn.execute(text("INSERT INTO assets VALUES ('fx-id', 'USDBRL')"))
        conn.execute(text(
            "INSERT INTO asset_quotes VALUES "
            "('fx-id', '2026-07-31T00:00:00+00:00', 5.50), "
            "('fx-id', '2026-08-14T00:00:00+00:00', 5.40)"))
    monkeypatch.setattr("core.database.get_engine", lambda: engine)

    serie = _default_fx_loader(("USD",))["USD"]

    assert serie.iloc[-1] == pytest.approx(5.40)
    assert serie.index[-1] == pd.Timestamp("2026-08-14")
    assert serie.attrs == {
        "source": "asset_quotes:USDBRL", "convention": "BRL_per_USD"}


def test_moeda_base_nao_suportada_falha_explicitamente():
    with pytest.raises(ValueError, match="moeda-base"):
        retornos_mensais(_posicoes(), base_currency="USD")


def test_loader_fx_padrao_falha_fechado_quando_banco_indisponivel(monkeypatch):
    from core.global_portfolio.returns import _default_fx_loader

    def indisponivel():
        raise RuntimeError("banco offline")

    monkeypatch.setattr("core.database.get_engine", indisponivel)
    assert _default_fx_loader(("USD",)) == {}


def test_gap_fx_interno_remove_mes_e_seguinte_e_preserva_pesos_40_60():
    from core.global_portfolio.risk import retorno_do_portfolio

    idx = pd.date_range("2023-01-31", periods=25, freq="ME")
    precos = pd.DataFrame({
        "PETR4": 100 * np.cumprod(np.repeat(1.01, 25)),
        "AAPL": 100 * np.cumprod(np.repeat(1.02, 25)),
    }, index=idx)
    fx = pd.Series(5 * np.cumprod(np.repeat(1.005, 25)), index=idx, name="USD")
    fx.iloc[10] = np.nan
    fx.attrs.update({"source": "fixture", "convention": "BRL_per_USD"})
    pos = pd.DataFrame([
        {"asset_class": "b3", "symbol": "PETR4", "weight_global": 0.4},
        {"asset_class": "us", "symbol": "AAPL", "weight_global": 0.6},
    ])

    ret, cob = retornos_mensais(
        pos, loader=lambda _: precos, fx_loader=lambda _: {"USD": fx})
    serie = retorno_do_portfolio(ret, {"PETR4": 0.4, "AAPL": 0.6})
    retorno_us_brl = (1.02 * 1.005) - 1.0
    esperado = 0.4 * 0.01 + 0.6 * retorno_us_brl

    assert idx[10] not in ret.index and idx[11] not in ret.index
    assert not ret.isna().any().any()
    assert len(ret) == len(serie) == 22
    assert serie.to_numpy() == pytest.approx([esperado] * 22)
    assert cob.peso_coberto == pytest.approx(1.0)
    assert cob.meses_removidos_intersecao == (
        idx[10].date().isoformat(), idx[11].date().isoformat())
    assert cob.periodo_comum == (
        idx[1].date().isoformat(), idx[-1].date().isoformat())


@pytest.mark.parametrize("taxa_invalida", [np.nan, np.inf, -np.inf, 0.0, -5.0])
def test_fx_invalido_remove_mes_e_retorno_dependente_da_janela_comum(taxa_invalida):
    """Taxa FX não-positiva ou não-finita é ausência, nunca retorno publicado."""
    idx = pd.date_range("2023-01-31", periods=25, freq="ME")
    precos = pd.DataFrame({
        "PETR4": 100 * np.cumprod(np.repeat(1.01, 25)),
        "AAPL": 100 * np.cumprod(np.repeat(1.02, 25)),
    }, index=idx)
    fx = pd.Series(5 * np.cumprod(np.repeat(1.005, 25)), index=idx, name="USD")
    fx.iloc[10] = taxa_invalida
    fx.attrs.update({"source": "fixture", "convention": "BRL_per_USD"})
    pos = pd.DataFrame([
        {"asset_class": "b3", "symbol": "PETR4", "weight_global": 0.4},
        {"asset_class": "us", "symbol": "AAPL", "weight_global": 0.6},
    ])

    ret, cob = retornos_mensais(
        pos, loader=lambda _: precos, fx_loader=lambda _: {"USD": fx})

    assert idx[10] not in ret.index and idx[11] not in ret.index
    assert np.isfinite(ret.to_numpy(dtype=float)).all()
    assert cob.meses_removidos_intersecao == (
        idx[10].date().isoformat(), idx[11].date().isoformat())
    assert cob.fx_evidence[0].stale_months == 2


def test_min_obs_e_reaplicado_depois_da_intersecao():
    """O segundo piso avalia a amostra comum, não cada ativo isoladamente."""
    # Cada ativo tem MIN_OBS retornos, mas deslocados um mês: a interseção
    # contém MIN_OBS - 1 retornos e não sustenta o painel comparável.
    idx = pd.date_range("2023-01-31", periods=MIN_OBS + 2, freq="ME")
    precos = pd.DataFrame({
        "PETR4": [*range(100, 100 + len(idx) - 1), np.nan],
        "VALE3": [np.nan, *range(200, 200 + len(idx) - 1)],
    }, index=idx).astype(float)
    assert (precos.pct_change(fill_method=None).notna().sum() == MIN_OBS).all()
    pos = pd.DataFrame([
        {"asset_class": "b3", "symbol": "PETR4", "weight_global": 0.4},
        {"asset_class": "b3", "symbol": "VALE3", "weight_global": 0.6},
    ])

    ret, cob = retornos_mensais(pos, loader=lambda _: precos)

    assert ret.empty
    assert cob.peso_coberto == 0.0
    assert set(cob.simbolos_sem_serie) == {"PETR4", "VALE3"}


def test_last_observed_at_ignora_fx_bruto_futuro_nao_usado():
    idx = pd.date_range("2023-01-31", periods=25, freq="ME")
    precos = pd.DataFrame({"AAPL": range(100, 125)}, index=idx).astype(float)
    fx_idx = idx.append(pd.DatetimeIndex([pd.Timestamp("2035-12-31")]))
    fx = pd.Series([5.0] * len(fx_idx), index=fx_idx, name="USD")
    fx.attrs.update({"source": "fixture", "convention": "BRL_per_USD"})
    pos = pd.DataFrame([
        {"asset_class": "us", "symbol": "AAPL", "weight_global": 1.0},
    ])

    _, cob = retornos_mensais(
        pos, loader=lambda _: precos, fx_loader=lambda _: {"USD": fx})

    assert cob.fx_evidence[0].last_observed_at.startswith(
        idx[-1].date().isoformat())
