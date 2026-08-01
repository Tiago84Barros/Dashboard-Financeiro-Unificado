import pytest

from data_pipeline.market import fii_ri_documents as ri


ORIGINAL_ASSERT_PUBLIC_HOST = ri._assert_public_host


@pytest.fixture(autouse=True)
def no_real_dns(monkeypatch):
    monkeypatch.setattr(ri, "_assert_public_host", lambda host: None)


def test_validate_official_url_requires_https_allowlist_and_no_credentials():
    assert (
        ri.validate_official_url(
            "https://ri.example.com/reports", "example.com"
        )
        == "https://ri.example.com/reports"
    )
    with pytest.raises(ValueError, match="HTTPS"):
        ri.validate_official_url("http://ri.example.com/reports", "example.com")
    with pytest.raises(ValueError, match="credenciais"):
        ri.validate_official_url(
            "https://user:secret@ri.example.com/reports", "example.com"
        )
    with pytest.raises(ValueError, match="fora"):
        ri.validate_official_url("https://evil.test/report.pdf", "example.com")


def test_public_host_validation_rejects_non_global_dns_ranges(monkeypatch):
    monkeypatch.setattr(
        ri.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (ri.socket.AF_INET, ri.socket.SOCK_STREAM, 6, "", ("100.64.0.1", 443))
        ],
    )
    ORIGINAL_ASSERT_PUBLIC_HOST.cache_clear()

    with pytest.raises(ValueError, match="não público"):
        ORIGINAL_ASSERT_PUBLIC_HOST("ri.example.test")


def test_discover_pdf_links_keeps_only_report_pdfs_on_official_host():
    html = b"""
      <a href="/docs/MFII11-relatorio-2026-06.pdf">Relatorio mensal junho 2026</a>
      <a href="/docs/regulamento.pdf">Regulamento</a>
      <a href="/docs/MCEM11-relatorio-2026-06.pdf">Relatorio mensal MCEM11</a>
      <a href="https://evil.test/MFII11-relatorio.pdf">Relatorio externo</a>
      <a href="/docs/MFII11-relatorio-2026-06.pdf#page=2">Duplicado</a>
    """

    rows = ri.discover_pdf_links(
        html,
        page_url="https://ri.example.com/fundos/mfii11",
        allowed_host="example.com",
        ticker="MFII11",
    )

    assert len(rows) == 1
    assert rows[0].url == "https://ri.example.com/docs/MFII11-relatorio-2026-06.pdf"
    assert rows[0].reference_date.isoformat() == "2026-06-01"
    assert rows[0].natural_key.startswith("official-ri:")


def test_discovery_can_trust_tickerless_link_only_on_explicit_single_fund_page():
    html = b'<a href="/docs/relatorio-202606.pdf">Relatorio mensal</a>'

    assert not ri.discover_pdf_links(
        html,
        page_url="https://ri.example.com/fundos",
        allowed_host="example.com",
        ticker="MFII11",
    )
    rows = ri.discover_pdf_links(
        html,
        page_url="https://ri.example.com/fundos/mfii11",
        allowed_host="example.com",
        ticker="MFII11",
        single_fund_page=True,
    )
    assert len(rows) == 1
    assert rows[0].reference_date.isoformat() == "2026-06-01"


def test_discovery_uses_fund_scope_without_cross_assigning_other_panels():
    html = b"""
      <div data-content="MFII11 - Merito Desenvolvimento">
        <a href="/docs/relatorio-202606.pdf">Relatorio mensal</a>
      </div>
      <div data-content="MCEM11 - Merito Cemiterios">
        <a href="/docs/relatorio-cemiterios-202606.pdf">Relatorio mensal</a>
      </div>
    """

    rows = ri.discover_pdf_links(
        html,
        page_url="https://ri.example.com/fundos",
        allowed_host="example.com",
        ticker="MFII11",
    )

    assert [row.url for row in rows] == [
        "https://ri.example.com/docs/relatorio-202606.pdf"
    ]


def test_safe_get_rejects_redirect_outside_allowlist():
    class Response:
        is_redirect = True
        is_permanent_redirect = False
        headers = {"Location": "https://evil.test/report.pdf"}

        def close(self):
            return None

    class Session:
        def get(self, *args, **kwargs):
            return Response()

    with pytest.raises(ValueError, match="fora"):
        ri._safe_get(
            "https://ri.example.com/reports",
            allowed_host="example.com",
            session=Session(),
        )


def test_wordpress_media_discovery_keeps_only_fund_reports():
    payload = [
        {
            "date_gmt": "2026-07-15T17:53:00",
            "slug": "mfii11-relatoriomensal-202606",
            "title": {"rendered": "MFII11 Relatório Mensal"},
            "source_url": "https://ri.example.com/media/MFII11_RelatorioMensal_202606.pdf",
        },
        {
            "date_gmt": "2026-07-15T17:53:00",
            "slug": "mcem11-relatoriomensal-202606",
            "title": {"rendered": "MCEM11 Relatório Mensal"},
            "source_url": "https://ri.example.com/media/MCEM11_RelatorioMensal_202606.pdf",
        },
    ]

    rows = ri.discover_wordpress_media(
        payload, allowed_host="example.com", ticker="MFII11"
    )

    assert len(rows) == 1
    assert rows[0].reference_date.isoformat() == "2026-06-01"
    assert rows[0].source_published_at.isoformat() == "2026-07-15T17:53:00+00:00"


def test_discovery_does_not_mislabel_informe_as_management_report():
    payload = [{
        "date_gmt": "2026-07-15T17:53:00",
        "slug": "mfii11-informe-mensal-202606",
        "title": {"rendered": "MFII11 Informe Mensal"},
        "source_url": "https://ri.example.com/media/MFII11_Informe_202606.pdf",
    }]

    rows = ri.discover_wordpress_media(
        payload, allowed_host="example.com", ticker="MFII11"
    )

    assert len(rows) == 1
    assert rows[0].document_type == "INFORME RI"
