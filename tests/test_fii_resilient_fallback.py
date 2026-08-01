from data_pipeline.market import fii_resilient_fallback as fallback


def test_resilient_fallback_runs_monthly_without_postprocess_when_circuit_open(
    monkeypatch,
):
    profiles = iter([
        {"reference_date": "2026-05-01", "tickers": 300},
        {"reference_date": "2026-06-01", "tickers": 320},
    ])
    calls = []
    monkeypatch.setattr(fallback, "_engine", lambda: object())
    monkeypatch.setattr(
        fallback, "active_document_host_circuits",
        lambda **_: [{"host": "official.test"}],
    )
    monkeypatch.setattr(
        fallback, "structured_monthly_profile", lambda **_: next(profiles),
    )
    monkeypatch.setattr(
        "data_pipeline.market.fii_cvm_structured.ingest_cvm_structured",
        lambda **kwargs: calls.append(kwargs) or {"status": "completed"},
    )
    monkeypatch.setattr(
        "data_pipeline.market.fii_documents.process_pending_documents",
        lambda **_: {"attempted": 2, "failed": 0},
    )

    report = fallback.run_resilient_fallback(document_options={"limit": 2})

    assert report["status"] == "completed"
    assert report["fallback_triggered"] is True
    assert calls == [{
        "years": 1, "kinds": ("monthly",), "run_postprocess": False,
    }]
    assert report["quality_after"]["reference_date"] == "2026-06-01"
    assert report["policy"]["score_promotion"] is False


def test_resilient_fallback_skips_structured_without_circuit(monkeypatch):
    monkeypatch.setattr(fallback, "_engine", lambda: object())
    monkeypatch.setattr(
        fallback, "active_document_host_circuits", lambda **_: [],
    )
    monkeypatch.setattr(
        fallback, "structured_monthly_profile",
        lambda **_: {"reference_date": "2026-06-01"},
    )
    monkeypatch.setattr(
        "data_pipeline.market.fii_cvm_structured.ingest_cvm_structured",
        lambda **_: (_ for _ in ()).throw(AssertionError("não deveria coletar")),
    )

    report = fallback.run_resilient_fallback(process_documents=False)

    assert report["status"] == "completed"
    assert report["fallback_triggered"] is False
    assert report["structured"]["status"] == "skipped"


def test_resilient_fallback_runs_immediately_for_circuit_opened_by_batch(
    monkeypatch,
):
    circuit_snapshots = iter([[], [{"host": "unstable.test"}]])
    calls = []
    monkeypatch.setattr(fallback, "_engine", lambda: object())
    monkeypatch.setattr(
        fallback, "active_document_host_circuits",
        lambda **_: next(circuit_snapshots),
    )
    monkeypatch.setattr(
        fallback, "structured_monthly_profile",
        lambda **_: {"reference_date": "2026-06-01"},
    )
    monkeypatch.setattr(
        "data_pipeline.market.fii_cvm_structured.ingest_cvm_structured",
        lambda **kwargs: calls.append(kwargs) or {"status": "completed"},
    )
    monkeypatch.setattr(
        "data_pipeline.market.fii_documents.process_pending_documents",
        lambda **_: {"attempted": 2, "failed": 2},
    )

    report = fallback.run_resilient_fallback(document_options={"limit": 2})

    assert report["fallback_triggered"] is True
    assert report["active_circuits"] == [{"host": "unstable.test"}]
    assert len(calls) == 1
    assert report["status"] == "partial"


def test_transient_document_failure_is_warning_when_cvm_fallback_succeeds(
    monkeypatch,
):
    circuit_snapshots = iter([[], [{"host": "unstable.test"}]])
    monkeypatch.setattr(fallback, "_engine", lambda: object())
    monkeypatch.setattr(
        fallback, "active_document_host_circuits",
        lambda **_: next(circuit_snapshots),
    )
    monkeypatch.setattr(
        fallback, "structured_monthly_profile",
        lambda **_: {"reference_date": "2026-06-01"},
    )
    monkeypatch.setattr(
        "data_pipeline.market.fii_cvm_structured.ingest_cvm_structured",
        lambda **_: {"status": "completed"},
    )
    monkeypatch.setattr(
        "data_pipeline.market.fii_documents.process_pending_documents",
        lambda **_: {"attempted": 2, "failed": 2, "transient_failed": 2},
    )

    report = fallback.run_resilient_fallback(document_options={"limit": 2})

    assert report["status"] == "warning"
    assert report["compensated_transient_failure"] is True
