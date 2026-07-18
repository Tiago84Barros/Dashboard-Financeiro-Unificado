"""Teste: CIK sem fatos XBRL não cria empresa-fantasma."""
from data_pipeline.us import ingest


class _EmptyFactsProvider:
    """Perfil existe (holding nova), mas nenhuma demonstração."""
    pre_normalized = True
    calls_made = 0

    def get_profile(self, symbol):
        return {"symbol": symbol, "companyName": "Shell Holdings Corp",
                "exchangeShortName": "NYSE"}

    def get_income_statements(self, *a, **k):
        return []

    def get_balance_sheets(self, *a, **k):
        return []

    def get_cash_flow_statements(self, *a, **k):
        return []


class _FakeConn:
    def execute(self, *a, **k):
        raise AssertionError("não deve gravar nada para CIK vazio")


class _FakeEngine:
    def begin(self):
        # context manager que só permite o log_error (via repo), não upserts de empresa
        eng = self

        class _Ctx:
            def __enter__(self_):
                return _LoggingConn()

            def __exit__(self_, *exc):
                return False
        return _Ctx()


class _LoggingConn:
    """Aceita só o INSERT de ingestion_errors; qualquer upsert de empresa falha o teste."""
    def execute(self, stmt, *a, **k):
        sql = str(stmt).lower()
        assert "ingestion_errors" in sql, f"gravou algo indevido: {sql[:60]}"
        return None


def test_cik_vazio_nao_cria_empresa():
    from data_pipeline.us import normalize  # noqa: F401 (garante import ok)
    res = ingest.ingest_symbol(_EmptyFactsProvider(), _FakeEngine(), "XOM",
                               with_prices=False)
    assert res["ok"] is False
    assert "vazio" in res["reason"] or "demonstra" in res["reason"]
