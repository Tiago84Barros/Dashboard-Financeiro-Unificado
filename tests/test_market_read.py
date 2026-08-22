import types

import pandas as pd

import core.b3_data as facade
import core.market_read as mr

# ── pivot puro ────────────────────────────────────────────────────────────────

def test_pivot_metrics_wide_shape_and_missing_cols():
    long_df = pd.DataFrame([
        {"Ticker": "PETR4", "year": 2025, "metric_name": "ROE", "metric_value": 0.26},
        {"Ticker": "PETR4", "year": 2025, "metric_name": "P/L", "metric_value": 4.8},
        {"Ticker": "PETR4", "year": 2025, "metric_name": "DY", "metric_value": 0.073},
    ])
    wide = mr._pivot_metrics(long_df)
    assert list(wide["Ticker"]) == ["PETR4"]
    assert abs(float(wide.iloc[0]["ROE"]) - 0.26) < 1e-9
    assert abs(float(wide.iloc[0]["P/L"]) - 4.8) < 1e-9
    # todas as colunas canônicas presentes; Liquidez_Corrente ausente -> NaN
    for c in mr._MULT_COLS:
        assert c in wide.columns
    assert pd.isna(wide.iloc[0]["Liquidez_Corrente"])


def test_pivot_metrics_empty():
    wide = mr._pivot_metrics(pd.DataFrame())
    assert wide.empty and "P/L" in wide.columns


# ── facade pós-cutover: financeiro/setores SEMPRE market.* ────────────────────
# O gate de cobertura e o _dispatch por flag foram APOSENTADOS (cutover
# concluído; tabelas legadas de fundamentos dropadas). A flag
# MARKET_READ_SOURCE é só informativa (read_source) e não muda a origem.

def _fakes(monkeypatch):
    legacy = types.SimpleNamespace(
        load_setores=lambda *a, **k: "LEGACY_SET",
        load_multiplos_todos=lambda *a, **k: "LEGACY_MT",
        load_macro_history=lambda *a, **k: "LEGACY_MACRO",
    )
    market = types.SimpleNamespace(
        load_setores=lambda *a, **k: "MARKET_SET",
        load_multiplos_todos=lambda *a, **k: "MARKET_MT",
    )
    monkeypatch.setattr(facade, "_legacy", legacy)
    monkeypatch.setattr(facade, "_market", market)


def test_read_source_default_legacy(monkeypatch):
    monkeypatch.delenv("MARKET_READ_SOURCE", raising=False)
    assert facade.read_source() == "legacy"


def test_read_source_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("MARKET_READ_SOURCE", "banana")
    assert facade.read_source() == "legacy"


def test_financeiro_sempre_market_para_qualquer_flag(monkeypatch):
    _fakes(monkeypatch)
    for src in ("legacy", "market", "compare"):
        monkeypatch.setenv("MARKET_READ_SOURCE", src)
        assert facade.load_multiplos_todos() == "MARKET_MT"  # nunca "LEGACY_MT"


def test_financeiro_market_erro_retorna_vazio_nao_legado(monkeypatch):
    _fakes(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("market down")
    facade._market.load_multiplos_todos = boom
    out = facade.load_multiplos_todos()
    # vazio (nulo = ausente), JAMAIS cai no legado public.multiplos (dropada)
    assert hasattr(out, "empty") and out.empty


def test_setores_prefere_market(monkeypatch):
    # setores prefere market.* (herança de setor ON->PN pela raiz de 4 letras)
    _fakes(monkeypatch)
    monkeypatch.setenv("MARKET_READ_SOURCE", "legacy")
    assert facade.load_setores() == "MARKET_SET"


def test_setores_fallback_legado_quando_market_falha(monkeypatch):
    _fakes(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("market down")
    facade._market.load_setores = boom
    # setores é REFERÊNCIA (não-financeiro): mantém fallback ao legado
    assert facade.load_setores() == "LEGACY_SET"


def test_macro_segue_no_legado(monkeypatch):
    _fakes(monkeypatch)
    # macro/selic/snapshot são de outros domínios — seguem no legado
    assert facade.load_macro_history() == "LEGACY_MACRO"


# ── A-009: proveniência do fallback legado não pode ficar silenciosa ──────────
# core.b3_db._resolve_url() tem prioridade de URL própria (SUPABASE_DB_URL_B3 >
# SUPABASE_DB_URL > settings.db_url) que ignora um DATABASE_URL sobrescrito no
# processo. Em vez de unificar (quebraria scripts de ingestão que dependem
# desse desvio para apontar a staging local), load_setores() marca em attrs
# quando os dados vieram do legado, para a UI avisar o usuário mesmo quando o
# fallback "funciona" (retorna dado não-vazio de uma fonte diferente).

def test_setores_marca_proveniencia_do_fallback_legado(monkeypatch):
    _fakes(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("market down")
    facade._market.load_setores = boom
    df_legacy = pd.DataFrame({"ticker": ["PETR4"], "SETOR": ["Petróleo"]})
    facade._legacy.load_setores = lambda *a, **k: df_legacy

    out = facade.load_setores()
    assert out.attrs.get("fallback_legado") is True
    # não deve mutar o objeto original devolvido pelo legado
    assert "fallback_legado" not in df_legacy.attrs


def test_setores_sem_fallback_nao_marca_proveniencia(monkeypatch):
    _fakes(monkeypatch)
    # market funciona: não passa pelo legado, não há attrs de fallback
    out = facade.load_setores()
    assert out == "MARKET_SET"  # string dos fakes: nem tem .attrs, e tudo bem


def test_setores_fallback_com_retorno_nao_dataframe_nao_quebra(monkeypatch):
    # Guarda de regressão: alguns testes/fakes fazem load_setores legado
    # devolver algo que não é DataFrame (ex.: "LEGACY_SET" em _fakes) — a
    # marcação de proveniência não pode lançar exceção nesse caso.
    _fakes(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("market down")
    facade._market.load_setores = boom
    assert facade.load_setores() == "LEGACY_SET"


def test_market_active_sempre_true(monkeypatch):
    # Cutover concluído: reparos defensivos das telas ficam sempre OFF.
    for src in ("legacy", "market", "compare"):
        monkeypatch.setenv("MARKET_READ_SOURCE", src)
        assert facade.market_active() is True


def test_pivot_suprime_razoes_sobre_patrimonio_negativo():
    """MWET4 real: ROE de +4,23 com PL negativo, vindo de `brapi_trailing`.

    A guarda tem de ficar na LEITURA porque calculated_metrics tem mais de uma
    fonte para a mesma métrica: o ETL calcula e a brapi entrega a dela pronta.
    Proteger só o cálculo deixava passar a versão da brapi.
    """
    import pandas as pd

    from core.market_read import _pivot_metrics

    longo = pd.DataFrame([
        {"Ticker": "MWET4", "year": 2025, "metric_name": "ROE", "metric_value": 4.23},
        {"Ticker": "MWET4", "year": 2025, "metric_name": "P/VP", "metric_value": 2.1},
        {"Ticker": "MWET4", "year": 2025, "metric_name": "Patrimonio_Negativo",
         "metric_value": 1.0},
        {"Ticker": "MWET4", "year": 2025, "metric_name": "ROA", "metric_value": -0.18},
        {"Ticker": "BOA3", "year": 2025, "metric_name": "ROE", "metric_value": 0.20},
        {"Ticker": "BOA3", "year": 2025, "metric_name": "P/VP", "metric_value": 1.5},
    ])
    largo = _pivot_metrics(longo).set_index("Ticker")

    assert pd.isna(largo.at["MWET4", "ROE"])
    assert pd.isna(largo.at["MWET4", "P/VP"])
    # ROA tem denominador positivo (ativo total) — o sinal é confiável e fica.
    assert largo.at["MWET4", "ROA"] == -0.18
    # Empresa sadia não é tocada.
    assert largo.at["BOA3", "ROE"] == 0.20
    assert largo.at["BOA3", "P/VP"] == 1.5
