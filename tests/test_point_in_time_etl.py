"""Point-in-time no ETL market (migração 019) — auditoria 2026-07, rodada 2."""
import datetime as dt

from data_pipeline.market import normalize as nz
from data_pipeline.market import repository as repo


_QUOTE = {
    "symbol": "TST3",
    "incomeStatementHistory": [{
        "endDate": "2024-12-31", "totalRevenue": 100.0, "netIncome": 10.0,
    }],
    "incomeStatementHistoryQuarterly": [{
        "endDate": "2025-09-30", "totalRevenue": 30.0, "netIncome": 3.0,
    }],
    "balanceSheetHistory": [{
        "endDate": "2024-12-31", "totalAssets": 500.0,
    }],
    "cashflowHistory": [{
        "endDate": "2024-12-31", "totalCashFromOperatingActivities": 20.0,
    }],
}


def test_normalize_inclui_period_end_date_e_proveniencia():
    inc = nz.income_rows(_QUOTE)
    bal = nz.balance_rows(_QUOTE)
    cf = nz.cashflow_rows(_QUOTE)
    anual = [r for r in inc if r["period"] == "annual"]
    assert anual and anual[0]["period_end_date"] == dt.date(2024, 12, 31)
    assert anual[0]["year"] == 2024 and anual[0]["quarter"] == 0
    tri = [r for r in inc if r["period"] == "quarterly"]
    assert tri and tri[0]["period_end_date"] == dt.date(2025, 9, 30)
    assert tri[0]["quarter"] == 3
    for rows in (bal, cf):
        assert rows and rows[0]["period_end_date"] == dt.date(2024, 12, 31)
        # placeholder de proveniência: o ingest preenche com o id do payload
        assert "raw_payload_id" in rows[0] and rows[0]["raw_payload_id"] is None


def test_first_seen_at_nunca_atualiza_no_conflito():
    # first_seen_at é o proxy de available_at: não pode estar nas colunas de
    # UPDATE do ON CONFLICT (re-ingestão apagaria o histórico de
    # disponibilidade) nem nas linhas normalizadas.
    for t in ("income_statements", "balance_sheets", "cash_flow_statements"):
        assert "first_seen_at" not in repo._UPDATE_COLS[t]
        assert "period_end_date" in repo._UPDATE_COLS[t]
        assert "raw_payload_id" in repo._UPDATE_COLS[t]
    for rows in (nz.income_rows(_QUOTE), nz.balance_rows(_QUOTE),
                 nz.cashflow_rows(_QUOTE)):
        assert all("first_seen_at" not in r for r in rows)
