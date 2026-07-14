from core.fii_entity_resolution import match_entity, normalize_entity_name


def test_entity_resolution_prioritizes_regulatory_identifier():
    candidates = [{"id": 7, "canonical_name": "Empresa Diferente",
                   "legal_identifier": "12.345.678/0001-90"}]
    result = match_entity("Nome qualquer", candidates,
                          raw_identifier="12.345.678/0001-90")
    assert result.canonical_id == 7
    assert result.status == "accepted"
    assert result.confidence == 1


def test_entity_normalization_removes_accents_and_legal_suffixes():
    assert normalize_entity_name("Imobiliária São João S.A.") == "sao joao"
