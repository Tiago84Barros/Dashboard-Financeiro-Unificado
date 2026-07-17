import pytest

from data_pipeline.market.fii_documents import (
    PARSER_VERSION, DocumentTooLargeError, _download, _extract_evidence,
    _layout_signature, _provisional_candidates,
)


def test_document_evidence_uses_methodology_names_and_page_numbers():
    pages = ["Resumo sem métricas.",
             "Vacância física 7,5% | WAULT 4,2 anos | Cap Rate 9,1% | LTV 45%"]
    evidence = _extract_evidence("\n".join(pages), pages)
    rows = {row["metric_name"]: row for row in evidence}

    assert PARSER_VERSION == "1.5.0"
    assert rows["vacancia_fisica"]["normalized_value"] == pytest.approx(.075)
    assert rows["wault_anos"]["normalized_value"] == pytest.approx(4.2)
    assert rows["implied_cap_rate"]["normalized_value"] == pytest.approx(.091)
    assert rows["ltv"]["normalized_value"] == pytest.approx(.45)
    assert all(row["page_number"] == 2 for row in rows.values())


def test_document_evidence_respects_fii_type_profile():
    text = "Vacância física 7,5% | LTV 45% | spread IPCA + 7,0%"
    tijolo = _extract_evidence(text, fii_type="tijolo")
    papel = _extract_evidence(text, fii_type="papel")

    assert {row["metric_name"] for row in tijolo} == {"vacancia_fisica"}
    assert {row["metric_name"] for row in papel} == {"ltv", "credit_spread"}


def test_only_unambiguous_stable_evidence_is_provisionally_promoted():
    evidence = _extract_evidence(
        "Vacância física 7,5% | WAULT 4,2 anos | LTV 40% | LTV 55%"
    )

    selected = _provisional_candidates(
        evidence, extraction_confidence=.90, layout_changed=False
    )

    assert {row["metric_name"] for row in selected} == {
        "vacancia_fisica", "wault_anos"
    }
    assert not _provisional_candidates(
        evidence, extraction_confidence=.90, layout_changed=True
    )


def test_layout_signature_ignores_numeric_value_changes():
    left = _layout_signature(["Vacância física 5,0% e LTV 40%"], "")
    right = _layout_signature(["Vacância física 8,0% e LTV 55%"], "")
    assert left == right


def test_document_download_enforces_streaming_size_limit(monkeypatch):
    class Response:
        headers = {"Content-Type": "application/pdf"}

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def iter_content(chunk_size):
            assert chunk_size > 0
            yield b"%PDF" + b"x" * 8

    def fake_get(*args, **kwargs):
        assert kwargs["stream"] is True
        return Response()

    monkeypatch.setattr("data_pipeline.market.fii_documents.requests.get", fake_get)
    with pytest.raises(DocumentTooLargeError):
        _download("https://example.test/report.pdf", max_bytes=10)
