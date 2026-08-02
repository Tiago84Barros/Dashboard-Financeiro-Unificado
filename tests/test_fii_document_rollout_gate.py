from scripts.check_fii_document_rollout_gate import evaluate_rollout


def test_rollout_gate_blocks_small_or_low_confidence_sample():
    result = evaluate_rollout({
        "attempted": 20,
        "extracted": 18,
        "low_confidence": 7,
        "processing": 0,
        "provisional": 0,
        "document_pit_violations": 0,
        "document_duplicates": 0,
        "document_empty_evidence": 0,
        "cvm_pit_violations": 0,
        "cvm_duplicates": 0,
        "cvm_empty_values": 0,
    })

    assert result["allowed"] is False
    assert "minimum_attempts" in result["blockers"]
    assert "low_confidence_rate" in result["blockers"]


def test_rollout_gate_allows_clean_sample_at_threshold():
    result = evaluate_rollout({
        "attempted": 50,
        "extracted": 46,
        "low_confidence": 9,
        "processing": 0,
        "provisional": 0,
        "document_pit_violations": 0,
        "document_duplicates": 0,
        "document_empty_evidence": 0,
        "cvm_pit_violations": 0,
        "cvm_duplicates": 0,
        "cvm_empty_values": 0,
    })

    assert result["allowed"] is True
    assert result["success_rate"] == 0.92
    assert result["low_confidence_rate"] == 0.195652
