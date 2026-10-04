"""INF-M4 / B3-05 (auditoria 04/10/2026): `except` cego em caminho de decisão.

O defeito comum: uma falha no meio do cálculo virava `pass` (ou um zero) e o
número seguia para a tela como se estivesse íntegro — resiliência zerada com
a versão do score exibida, portão de risco aprovando todo mundo, preço do
yfinance entrando no backtest sem rótulo. Cada teste aqui provoca a falha de
verdade e confere as três coisas que a correção promete: o cálculo não
mente (não fica meio aplicado), a degradação fica marcada no resultado e o
log guarda a causa.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest


def _explode(*_a, **_k):
    raise RuntimeError("falha provocada pelo teste")


def _mult() -> pd.DataFrame:
    return pd.DataFrame([
        {"Ticker": "AAA3", "P/L": 6.0, "ROE": 0.25, "SEGMENTO": "S"},
        {"Ticker": "BBB3", "P/L": 9.0, "ROE": 0.18, "SEGMENTO": "S"},
        {"Ticker": "CCC3", "P/L": 14.0, "ROE": 0.10, "SEGMENTO": "S"},
        {"Ticker": "DDD3", "P/L": 20.0, "ROE": 0.05, "SEGMENTO": "S"},
    ])


_PESOS = {"P/L": (0.5, False), "ROE": (0.5, True)}


# ── B3-05: resiliência ─────────────────────────────────────────────────────

def _score(df_hist=None):
    from views.empresas_b3 import _score_universo
    df = _mult()
    return _score_universo(df, df["Ticker"].tolist(), _PESOS, df_hist,
                           group_col_prefer="SEGMENTO")


def _neutraliza_resiliencia(monkeypatch):
    import core.resilience_score as rs
    monkeypatch.setattr(rs, "historical_reversion_adj",
                        lambda df, *_a, **_k: pd.Series(0.0, index=df.index))
    monkeypatch.setattr(rs, "valuation_history_adj",
                        lambda df, *_a, **_k: pd.Series(0.0, index=df.index))
    monkeypatch.setattr(rs, "financial_health_penalty",
                        lambda df, *_a, **_k: pd.Series(0.0, index=df.index))


def test_falha_na_resiliencia_nao_derruba_o_ranking_e_fica_marcada(monkeypatch, caplog):
    """Antes o `except` chamava `df.setdefault` (inexistente em DataFrame):
    a falha virava AttributeError e o ranking inteiro caía."""
    import core.resilience_score as rs
    monkeypatch.setattr(rs, "financial_health_penalty", _explode)

    with caplog.at_level(logging.ERROR, logger="views.empresas_b3"):
        out = _score()

    assert len(out) == 4
    # Sem veredito de saúde: NaN, não 0 ("balanço saudável").
    for col in ("hist_bonus", "val_hist_bonus", "health_penalty"):
        assert out[col].isna().all()
    # A marca sobrevive ao sort_values/reset_index do fim da função.
    codigos = [d["codigo"] for d in out.attrs.get("degradacoes", [])]
    assert codigos == ["tela.b3.score_sem_resiliencia"]
    assert "RuntimeError" in out.attrs["degradacoes"][0]["mensagem"]
    assert any("resiliência" in r.getMessage() for r in caplog.records)


def test_falha_em_b_desfaz_a_e_c_ja_aplicados(monkeypatch):
    """Tudo ou nada: A (+50%) aplicado e B falhando não pode deixar o bônus
    de A na nota."""
    hist = {"AAA3": pd.DataFrame({"Data": ["2023-12-31"], "ROE": [0.2]})}

    _neutraliza_resiliencia(monkeypatch)
    base = _score(hist).set_index("Ticker")["score_raw"]

    import core.resilience_score as rs
    monkeypatch.setattr(rs, "historical_reversion_adj",
                        lambda df, *_a, **_k: pd.Series(0.5, index=df.index))
    monkeypatch.setattr(rs, "financial_health_penalty", _explode)
    degradado = _score(hist).set_index("Ticker")["score_raw"]

    pd.testing.assert_series_equal(degradado.sort_index(), base.sort_index())


def test_resiliencia_integra_nao_marca_nada(monkeypatch):
    _neutraliza_resiliencia(monkeypatch)
    out = _score()
    assert not out.attrs.get("degradacoes")
    assert (out["health_penalty"] == 0.0).all()


def test_tabela_de_resiliencia_nao_pinta_saudavel_sem_calculo():
    """A tabela usava `p >= 20 / p >= 8 / senão Saudável`: NaN caía em
    "Saudável". O ramo novo vem antes."""
    import inspect

    import views.empresas_b3 as b3
    corpo = inspect.getsource(b3)
    i = corpo.index('_res_df["saude"] = _res_df["health_penalty"].apply(')
    trecho = corpo[i:i + 300]
    assert "pd.isna(p)" in trecho
    assert trecho.index("pd.isna(p)") < trecho.index("Saudável")


def test_avisar_degradacoes_nomeia_na_tela(monkeypatch):
    import views.empresas_b3 as b3
    chamadas = []
    monkeypatch.setattr(b3, "aviso_lacuna",
                        lambda msg, **kw: chamadas.append((msg, kw)))
    df = pd.DataFrame({"x": [1]})
    b3._marcar_degradacao(df, "tela.b3.teste", "parte do cálculo falhou")
    b3._marcar_degradacao(df, "tela.b3.teste", "repetida não duplica")
    assert b3._avisar_degradacoes(df, entidade="AAA3") == 1
    assert chamadas[0][1]["codigo"] == "tela.b3.teste"
    assert chamadas[0][1]["nivel"] == "warning"
    assert chamadas[0][1]["entidade"] == "AAA3"
    # Filtro por etapa: o Score de Entrada não repete o aviso do ranking.
    assert b3._avisar_degradacoes(df, codigos=("tela.b3.outro",)) == 0


# ── B3-05: procedência dos preços ──────────────────────────────────────────

def _yf_falso(tickers, **_k):
    idx = pd.date_range("2024-01-31", periods=3, freq="ME")
    cols = pd.MultiIndex.from_product([["Close"], list(tickers)])
    return pd.DataFrame(np.full((3, len(tickers)), 10.0), index=idx, columns=cols)


def test_preco_do_yfinance_sai_rotulado_com_a_causa(monkeypatch, caplog):
    import views.empresas_b3 as b3
    monkeypatch.setattr(b3._mr, "load_precos_mensais", _explode)
    monkeypatch.setattr(b3.yf, "download", _yf_falso)

    with caplog.at_level(logging.ERROR, logger="views.empresas_b3"):
        df = b3._batch_yf_precos_mensais(("AAA3", "BBB3"), "1y")

    assert list(df.columns) == ["AAA3", "BBB3"]
    assert df.attrs["fonte_precos"] == "yfinance"
    assert df.attrs["fonte_precos_erro"] == "RuntimeError"
    assert any("market" in r.getMessage() for r in caplog.records)

    avisos = []
    monkeypatch.setattr(b3, "aviso_lacuna", lambda msg, **kw: avisos.append((msg, kw)))
    b3._avisar_fonte_precos(df, "o backtest")
    assert avisos and avisos[0][1]["codigo"] == "tela.b3.precos_fallback_yfinance"
    assert "yfinance" in avisos[0][0] and "RuntimeError" in avisos[0][0]


def test_preco_do_market_nao_gera_aviso(monkeypatch):
    import views.empresas_b3 as b3
    idx = pd.date_range("2024-01-31", periods=3, freq="ME")
    monkeypatch.setattr(b3._mr, "load_precos_mensais",
                        lambda _t: pd.DataFrame({"AAA3": [1.0, 2.0, 3.0]}, index=idx))
    df = b3._batch_yf_precos_mensais(("AAA3",), "5y")
    assert df.attrs["fonte_precos"] == "market.historical_prices"
    avisos = []
    monkeypatch.setattr(b3, "aviso_lacuna", lambda msg, **kw: avisos.append(msg))
    b3._avisar_fonte_precos(df, "o backtest")
    assert avisos == []


def test_duas_fontes_falhando_viram_indisponivel(monkeypatch):
    import views.empresas_b3 as b3
    monkeypatch.setattr(b3._mr, "load_precos_mensais", _explode)
    monkeypatch.setattr(b3.yf, "download", _explode)
    df = b3._batch_yf_precos_mensais(("AAA3", "BBB3"), "1y")
    assert df.empty
    assert df.attrs["fonte_precos"] == "indisponivel"
    assert "yfinance: RuntimeError" in df.attrs["fonte_precos_erro"]


def test_procedencia_viaja_para_o_resultado_guardado_na_sessao():
    import views.empresas_b3 as b3
    origem = pd.DataFrame({"a": [1]})
    origem.attrs.update(fonte_precos="yfinance", fonte_precos_erro="OperationalError")
    destino = b3._herdar_fonte_precos(pd.DataFrame({"b": [2]}), origem)
    assert destino.attrs["fonte_precos"] == "yfinance"
    assert destino.attrs["fonte_precos_erro"] == "OperationalError"


# ── Portão de risco do Score de Entrada ────────────────────────────────────

def test_risco_indisponivel_nao_se_le_como_sem_risco(monkeypatch):
    import core.risk_logit as rl
    import views.empresas_b3 as b3
    monkeypatch.setattr(rl, "distress_risk_score", _explode)

    df_scored = _mult().assign(score=[80.0, 70.0, 50.0, 20.0])
    out = b3._compute_score_entrada(df_scored, {})

    assert out["risk_probability"].isna().all()
    assert (out["risk_driver"] == "indisponivel").all()
    codigos = [d["codigo"] for d in out.attrs.get("degradacoes", [])]
    assert "tela.b3.score_entrada_sem_risco" in codigos


# ── Dossiê: nota das trilhas no lugar da oficial ───────────────────────────

def test_dossie_nomeia_historico_e_motor_oficial_indisponiveis(monkeypatch):
    import core.b3_company_score as score_mod
    import core.b3_data as _db
    import views.empresas_b3 as b3

    universo = pd.DataFrame({"Ticker": ["AAA3", "BBB3"], "DY": [0.08, 0.09]})
    monkeypatch.setattr(_db, "load_multiplos_todos", lambda: universo.copy())
    monkeypatch.setattr(_db, "load_multiplos_historico_batch", _explode)

    def _trilhas(df):
        out = df.copy()
        out["score"] = 61.0
        out["coverage"] = 1.0
        return out

    monkeypatch.setattr(score_mod, "score_cross_section", _trilhas)
    monkeypatch.setattr(b3, "_score_universo", _explode)

    linha, _ref = b3._b3_peer_scores(
        "AAA3", pd.Series({"DY": 0.08}), pd.DataFrame({"ticker": ["AAA3", "BBB3"]}))

    assert linha["score"] == pytest.approx(61.0)  # a média das trilhas segue…
    codigos = [d["codigo"] for d in linha.attrs["degradacoes"]]
    # …mas a tela sabe que não é a nota do ranking.
    assert codigos == ["tela.b3.dossie_sem_historico",
                       "tela.b3.dossie_nota_das_trilhas"]


# ── Backtest: Markowitz híbrido que falha ──────────────────────────────────

def _hist_sintetico() -> dict[str, pd.DataFrame]:
    anos = list(range(2018, 2025))

    def _df(roe: float) -> pd.DataFrame:
        return pd.DataFrame({"Data": [f"{a}-12-31" for a in anos],
                             "ROE": [roe] * len(anos), "DY": [0.05] * len(anos)})

    return {"AAA3": _df(0.30), "BBB3": _df(0.10), "CCC3": _df(0.20)}


def _precos_sinteticos() -> pd.DataFrame:
    idx = pd.date_range("2021-01-31", "2023-12-31", freq="ME")
    rng = np.random.default_rng(7)
    base = np.cumprod(1 + rng.normal(0.01, 0.05, (len(idx), 3)), axis=0) * 10
    return pd.DataFrame(base, index=idx, columns=["AAA3", "BBB3", "CCC3"])


def test_backtest_registra_ano_em_que_o_markowitz_falhou(monkeypatch, caplog):
    import core.markowitz as mk
    from views.empresas_b3 import _simular_backtest
    monkeypatch.setattr(mk, "min_variance_capped", _explode)

    with caplog.at_level(logging.ERROR, logger="views.empresas_b3"):
        df_bt, _top, _n = _simular_backtest(
            _precos_sinteticos(), pd.DataFrame(), _hist_sintetico(),
            ["AAA3", "BBB3", "CCC3"],
            aporte=1000.0, data_inicio=pd.Timestamp("2021-01-01"),
            taxa_selic_aa=0.10, pesos={"ROE": (1.0, True)}, tk_grupos=None,
            top_n_max=3, usar_gamma=True, cap=0.50,
            use_markowitz=True,
        )

    assert not df_bt.empty
    falhas = df_bt.attrs["markowitz_falhas"]
    assert falhas and all("RuntimeError" in f for f in falhas)
    assert any("Markowitz" in r.getMessage() for r in caplog.records)


# ── Fora da tela B3: o log passa a guardar a causa ─────────────────────────

def test_analise_da_carteira_loga_a_causa_sem_vazar_para_a_tela(monkeypatch, caplog):
    import core.b3_data as _b3_data
    import core.portfolio_db_analysis as mod

    def _bug(*_a, **_k):
        raise KeyError("coluna_que_sumiu")

    monkeypatch.setattr(_b3_data, "load_multiplos_todos", _bug, raising=False)
    with caplog.at_level(logging.ERROR, logger="core.portfolio_db_analysis"):
        saida = mod.analise_acoes_db(["BBAS3"])

    assert saida["erro"] == mod._SEM_BANCO           # a tela não muda
    rec = [r for r in caplog.records if "ações B3" in r.getMessage()]
    assert rec and rec[0].exc_info[0] is KeyError    # o log nomeia o passo real


def test_validacao_fii_indisponivel_fecha_o_portao_e_loga(monkeypatch, caplog):
    import core.market_read as _mr
    import core.portfolio_db_analysis as mod

    inputs = pd.DataFrame([{"ticker": "AAAA11"}])
    monkeypatch.setattr(_mr, "load_fii_methodology_inputs", lambda: inputs)
    monkeypatch.setattr(_mr, "load_fii_validation_status", _explode)
    capturado = {}

    import core.fii_methodology as fm

    def _score(rows, validation_status):
        capturado["status"] = validation_status
        return []

    monkeypatch.setattr(fm, "score_fiis_by_type", _score)
    with caplog.at_level(logging.ERROR, logger="core.portfolio_db_analysis"):
        mod.analise_fiis_db(["AAAA11"])

    assert capturado["status"] == "unvalidated"
    assert any("validação FII" in r.getMessage() for r in caplog.records)


def test_negociabilidade_eua_indisponivel_fica_no_log(monkeypatch, caplog):
    import core.us_data as us
    monkeypatch.setattr(us._read, "load_us_giro_diario", _explode)
    frame = pd.DataFrame({"symbol": ["AAPL"], "score": [70.0]})

    with caplog.at_level(logging.ERROR, logger="core.us_data"):
        out = us._anexa_negociabilidade_e_ciclo(frame)

    assert out is frame                     # a aba não zera
    assert any("Negociabilidade" in r.getMessage() for r in caplog.records)


def test_piso_de_negociabilidade_ausente_vira_aviso_na_carteira():
    """views/portfolio_b3: sem giro/classes irmãs o piso não roda; antes isso
    era indistinguível de "nenhuma troca necessária"."""
    import inspect

    import views.portfolio_b3 as pb3
    corpo = inspect.getsource(pb3)
    i = corpo.index("if not (_giro and _irmas):")
    trecho = corpo[i:i + 600]
    assert "liq_avisos.append(" in trecho
    assert "não aplicado" in trecho
