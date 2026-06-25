import core.market_health as mh


def test_completeness_rows_basic():
    present = {"ROE": 90, "DY": 50, "Liquidez_Corrente": 70}
    rows = mh.completeness_rows(present, total=100)
    by = {r["campo"]: r for r in rows}
    # ordem canônica e todas as métricas-chave presentes
    assert [r["campo"] for r in rows] == mh._KEY_METRICS
    assert by["ROE"]["pct"] == 90.0 and by["ROE"]["preenchidos"] == 90
    assert by["DY"]["pct"] == 50.0
    # métrica ausente do dict -> 0
    assert by["P/L"]["preenchidos"] == 0 and by["P/L"]["pct"] == 0.0


def test_completeness_rows_zero_total():
    rows = mh.completeness_rows({}, total=0)
    assert all(r["pct"] == 0.0 and r["total"] == 0 for r in rows)
    assert len(rows) == len(mh._KEY_METRICS)
