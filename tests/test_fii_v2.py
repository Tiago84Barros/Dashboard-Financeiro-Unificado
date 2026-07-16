from datetime import date
import json

import pytest

from data_pipeline.market import fii_v2


REQUESTED = "2026-07-12T12:00:00Z"


def test_indicators_classify_and_create_pit_metrics():
    payload = {"requestedAt": REQUESTED, "fiis": [{
        "symbol": "KFOF11", "asOfDate": "2026-06-01", "segmentType": "fof",
        "name": "Fundo", "cnpj": "123", "price": 90, "navPerShare": 100,
        "priceToNav": .90, "dividendYield12m": .11, "equity": 1000,
        "totalInvestors": 50, "segmentoAtuacao": "Fundo de Fundos",
        "tipoGestao": "Ativa",
    }]}
    result = fii_v2.normalize_indicators(payload, raw_payload_id=7)
    assert result["fii_updates"][0]["tipo"] == "fof"
    metrics = {row["metric_name"]: row for row in result["observations"]}
    assert metrics["nav_discount"]["value_numeric"] == pytest.approx(.10)
    assert metrics["pvp"]["reference_date"] == "2026-06-01"
    assert metrics["pvp"]["raw_payload_id"] == 7


def test_portfolio_normalizes_issuer_and_holding_without_calling_them_debtors():
    payload = {"requestedAt": REQUESTED, "fiis": [{
        "symbol": "MXRF11", "referenceDate": "2026-03-31", "version": 2,
        "financialAssets": [
            {"identifier": "CRI-A", "issuerCnpj": "111", "value": 60, "confidential": False},
            {"identifier": "CRI-B", "issuerCnpj": "222", "value": 40, "confidential": False},
        ],
        "fundHoldings": [{"issuerCnpj": "333", "value": 20, "confidential": False}],
    }]}
    result = fii_v2.normalize_portfolio(payload, raw_payload_id=9)
    assert {row["exposure_type"] for row in result["exposures"]} == {"issuer", "holding"}
    assert abs(sum(row["exposure_weight"] for row in result["exposures"]
                   if row["exposure_type"] == "issuer") - 1) < 1e-9
    concentration = next(row for row in result["observations"]
                         if row["metric_name"] == "issuance_concentration")
    assert concentration["value_numeric"] == .6


def test_portfolio_infers_type_only_when_composition_is_unambiguous():
    payload = {"requestedAt": REQUESTED, "fiis": [{
        "symbol": "REIT11", "referenceDate": "2026-03-31", "version": 1,
        "allocations": [{"assetClass": "cri", "count": 1, "value": 10}],
        "financialAssets": [], "fundHoldings": [],
    }]}
    result = fii_v2.normalize_portfolio(payload)
    assert result["type_inferences"] == [{"ticker": "REIT11", "tipo": "papel",
                                          "reference_date": date(2026, 3, 31),
                                          "raw_payload_id": None}]


def test_portfolio_classifies_multiple_structural_classes_as_hybrid():
    payload = {"requestedAt": REQUESTED, "fiis": [{
        "symbol": "MIXD11", "referenceDate": "2026-03-31", "version": 1,
        "allocations": [{"assetClass": "cri", "count": 1, "value": 50},
                        {"assetClass": "property", "count": 1, "value": 50}],
    }]}
    result = fii_v2.normalize_portfolio(payload)
    assert result["type_inferences"][0]["tipo"] == "hibrido"
    assert fii_v2.normalize_type("Híbrido") == "hibrido"


def test_portfolio_history_creates_retrospective_asset_class_exposures():
    payload = {"requestedAt": REQUESTED, "history": [{
        "symbol": "MXRF11", "referenceDate": "2025-12-31", "version": 2,
        "summary": {"totalItems": 3, "declaredValue": 100,
                    "properties": {"count": 0, "vacancyRate": None},
                    "financialAssets": {"count": 3, "declaredValue": 100}},
        "allocations": [{"assetClass": "cri", "count": 2, "value": 80},
                        {"assetClass": "cash", "count": 1, "value": 20}],
    }]}
    result = fii_v2.normalize_portfolio_history(payload, raw_payload_id=10)
    weights = {row["exposure_name"]: row["exposure_weight"]
               for row in result["exposures"]}
    assert weights == {"cri": .8, "cash": .2}
    assert all(row["availability_quality"] == "retrospective_backfill"
               for row in result["exposures"])


def test_properties_create_revenue_weighted_property_diversification():
    payload = {"requestedAt": REQUESTED, "fiis": [{
        "symbol": "KNRI11", "referenceDate": "2026-06-30", "version": 1,
        "properties": [
            {"name": "Imóvel A", "revenueShare": .6, "address": "São Paulo SP"},
            {"name": "Imóvel B", "revenueShare": .4, "address": "Curitiba PR"},
        ],
    }]}

    result = fii_v2.normalize_properties(payload)
    metrics = {row["metric_name"]: row for row in result["observations"]}

    assert metrics["property_diversification"]["value_numeric"] == pytest.approx(.48)
    metadata = json.loads(metrics["property_diversification"]["metadata_json"])
    assert metadata["formula"] == "1-HHI"


def test_properties_quarantine_negative_revenue_share():
    payload = {"requestedAt": REQUESTED, "fiis": [{
        "symbol": "VISC11", "referenceDate": "2026-06-30", "version": 1,
        "properties": [{"name": "Shopping", "revenueShare": -.0006,
                        "address": "Fortaleza CE"}],
    }]}

    result = fii_v2.normalize_properties(payload)

    assert result["properties"][0]["pct_receita"] is None
    assert not any(row["metric_name"] == "property_diversification"
                   for row in result["observations"])


def test_annual_and_financial_reports_preserve_delivery_and_document_url():
    annual = fii_v2.normalize_annual_reports({"requestedAt": REQUESTED, "reports": [{
        "symbol": "KNRI11", "year": 2025, "referenceDate": "2025-12-31",
        "fields": {"Data_Referencia": "2025-12-31", "Data_Entrega": "2026-03-30",
                   "Versao": "2", "Mandato": "Renda", "Tipo_Gestao": "Ativa"},
    }]})
    mandate = next(row for row in annual["observations"] if row["metric_name"] == "mandate")
    assert mandate["availability_quality"] == "verified_publication"
    assert mandate["knowledge_at"] == mandate["source_published_at"]

    financial = fii_v2.normalize_financials({"requestedAt": REQUESTED, "financials": [{
        "symbol": "KNRI11", "year": 2025, "referenceDate": "2025-12-31",
        "documentType": "DFIN", "fields": {
            "Data_Referencia": "2025-12-31", "Data_Entrega": "2026-03-31",
            "Versao": "1", "Link_Download": "https://example.test/doc.pdf",
            "Parecer_Auditor": "Sem ressalvas"},
    }]})
    assert financial["documents"][0]["source_url"].endswith("doc.pdf")
    assert financial["observations"][0]["value_text"] == "Sem ressalvas"


def test_reports_derive_leverage_fee_and_recurrence():
    reports = []
    for month in range(1, 8):
        reports.append({"symbol": "KNRI11", "referenceDate": f"2026-{month:02d}-01",
                        "version": 1, "totalAssets": 1000, "totalLiabilities": 100,
                        "adminFeeRate": .001, "monthlyDividendYield": .01})
    observations = fii_v2.normalize_reports({"requestedAt": REQUESTED, "reports": reports})
    latest = {row["metric_name"]: row for row in observations}
    assert latest["leverage"]["value_numeric"] == .1
    assert latest["admin_fee_rate_annual"]["value_numeric"] == .012
    assert latest["income_recurrence"]["value_numeric"] == 1.0


def test_income_growth_requires_real_three_year_window():
    monthly = {"AAAA11": {}}
    for year in (2023, 2024, 2025):
        for month in range(1, 13):
            monthly["AAAA11"][date(year, month, 1)] = 1.0 if year == 2023 else 1.1
    rows = fii_v2.income_metrics_from_monthly(monthly, as_of=date(2025, 12, 15))
    metrics = {row["metric_name"]: row for row in rows}
    assert metrics["income_recurrence"]["value_numeric"] > .95
    assert metrics["income_growth_per_share_3y"]["value_numeric"] > 0


def test_historical_prices_preserve_pit_quality_and_lineage():
    payload = {"requestedAt": REQUESTED, "fiis": [{
        "symbol": "KNRI11", "historicalDataPrice": [
            {"date": 1767139200, "open": 140, "high": 142, "low": 139,
             "close": 141, "adjustedClose": 142.5, "volume": 12345},
        ],
    }]}
    rows = fii_v2.normalize_historical(payload, raw_payload_id=42)
    assert len(rows) == 1
    assert rows[0]["raw_payload_id"] == 42
    assert rows[0]["source"] == "brapi_fii_v2"
    assert rows[0]["availability_quality"] == "retrospective_backfill"
    assert rows[0]["content_hash"]
    assert rows[0]["knowledge_at"] == rows[0]["available_at"]


def test_portfolio_emits_strong_cri_security_key_for_cvm_reconciliation():
    payload = {"requestedAt": REQUESTED, "fiis": [{
        "symbol": "PAPR11", "referenceDate": "2026-06-30", "version": 1,
        "financialAssets": [{
            "identifier": "CRI A", "issuerCnpj": "12.345.678/0001-90",
            "issue": "2", "series": "003", "value": 100,
            "confidential": False,
        }, {
            "identifier": "CRI A repetido", "issuerCnpj": "12.345.678/0001-90",
            "issue": "2", "series": "003", "value": 50,
            "confidential": False,
        }],
    }]}
    result = fii_v2.normalize_portfolio(payload, raw_payload_id=43)
    security = next(row for row in result["exposures"]
                    if row["exposure_type"] == "security")
    assert security["exposure_name"] == "12345678000190|2|3"
    assert security["exposure_weight"] == 1
    assert len([row for row in result["exposures"]
                if row["exposure_type"] == "security"]) == 1
