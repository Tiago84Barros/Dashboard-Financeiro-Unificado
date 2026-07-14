import pytest

from data_pipeline.market.fii_documents import (
    PARSER_VERSION, _extract_evidence, _layout_signature,
)


def test_document_evidence_uses_methodology_names_and_page_numbers():
    pages = ["Resumo sem métricas.",
             "Vacância física 7,5% | WAULT 4,2 anos | Cap Rate 9,1% | LTV 45%"]
    evidence = _extract_evidence("\n".join(pages), pages)
    rows = {row["metric_name"]: row for row in evidence}

    assert PARSER_VERSION == "1.1.0"
    assert rows["vacancia_fisica"]["normalized_value"] == pytest.approx(.075)
    assert rows["wault_anos"]["normalized_value"] == pytest.approx(4.2)
    assert rows["cap_rate_implicito"]["normalized_value"] == pytest.approx(.091)
    assert rows["ltv"]["normalized_value"] == pytest.approx(.45)
    assert all(row["page_number"] == 2 for row in rows.values())


def test_layout_signature_ignores_numeric_value_changes():
    left = _layout_signature(["Vacância física 5,0% e LTV 40%"], "")
    right = _layout_signature(["Vacância física 8,0% e LTV 55%"], "")
    assert left == right
