from datetime import date

import pytest
import requests

from data_pipeline.market import fii_fundamentus as fundamentus


def test_parse_reports_uses_official_id_and_month_end_natural_key():
    html = b"""
      <table><tbody>
        <tr><td><span>06/2026</span></td><td><a
          href="https://fnet.bmfbovespa.com.br/fnet/publico/downloadDocumento?id=1237729&amp;CodigoTipoInstituicao=1"
        >Download</a></td></tr>
      </tbody></table>
    """

    result = fundamentus.parse_fundamentus_reports(html, ticker="brcr11")

    assert result.rejected_links == 0
    assert len(result.candidates) == 1
    row = result.candidates[0]
    assert row.reference_date == date(2026, 6, 30)
    assert row.source_url.endswith("downloadDocumento?id=1237729")
    assert row.natural_key == "BRCR11|RELAT GERENCIAL|1237729"


def test_parse_reports_deduplicates_document_id_and_rejects_unsafe_links():
    html = b"""
      <table><tbody>
        <tr><td>06/2026</td><td><a href="http://fnet.bmfbovespa.com.br/fnet/publico/downloadDocumento?id=1">bad</a></td></tr>
        <tr><td>06/2026</td><td><a href="https://evil.test/fnet/publico/downloadDocumento?id=2">bad</a></td></tr>
        <tr><td>06/2026</td><td><a href="https://fnet.bmfbovespa.com.br/other/downloadDocumento?id=3">bad</a></td></tr>
        <tr><td>06/2026</td><td><a href="https://fnet.bmfbovespa.com.br/fnet/publico/downloadDocumento?id=4&amp;x=1">bad</a></td></tr>
        <tr><td>05/2026</td><td><a href="https://fnet.bmfbovespa.com.br/fnet/publico/downloadDocumento?id=5">ok</a></td></tr>
        <tr><td>05/2026</td><td><a href="https://fnet.bmfbovespa.com.br/fnet/publico/downloadDocumento?id=5&amp;CodigoTipoInstituicao=1">dup</a></td></tr>
      </tbody></table>
    """

    result = fundamentus.parse_fundamentus_reports(html, ticker="MFII11")

    assert [row.document_id for row in result.candidates] == [5]
    assert result.rejected_links == 4


def test_parse_rejects_link_without_reference_month_and_malformed_html_is_safe():
    html = b"""
      <table><tr><td>sem periodo<td><a href="https://fnet.bmfbovespa.com.br/fnet/publico/downloadDocumento?id=9">Download
    """

    result = fundamentus.parse_fundamentus_reports(html, ticker="MFII11")

    assert result.candidates == ()
    assert result.rejected_links == 1


@pytest.mark.parametrize("url", [
    "http://fnet.bmfbovespa.com.br/fnet/publico/downloadDocumento?id=1",
    "https://user:pass@fnet.bmfbovespa.com.br/fnet/publico/downloadDocumento?id=1",
    "https://fnet.bmfbovespa.com.br/fnet/publico/downloadDocumento?id=0",
    "https://fnet.bmfbovespa.com.br/fnet/publico/downloadDocumento?id=1#x",
])
def test_canonical_url_validation_rejects_unsafe_values(url):
    with pytest.raises(ValueError):
        fundamentus.canonicalize_fundosnet_url(url)


def test_fetch_retries_timeout_with_bounded_attempts():
    class Session:
        def __init__(self):
            self.calls = 0

        def get(self, *args, **kwargs):
            self.calls += 1
            raise requests.Timeout("synthetic timeout")

    session = Session()
    sleeps = []

    with pytest.raises(requests.Timeout):
        fundamentus._fetch_index(
            fundamentus.fundamentus_reports_url("MFII11"),
            attempts=2,
            session=session,
            sleeper=sleeps.append,
        )

    assert session.calls == 2
    assert sleeps == [0.5]


def test_fetch_rejects_redirect_outside_exact_index_host():
    class Response:
        is_redirect = True
        is_permanent_redirect = False

        def __init__(self):
            self.headers = {
                "Location": "https://evil.test/fii_relatorios.php?papel=MFII11"
            }

        def close(self):
            return None

    class Session:
        def get(self, *args, **kwargs):
            return Response()

    with pytest.raises(ValueError, match="fora do host"):
        fundamentus._fetch_index(
            fundamentus.fundamentus_reports_url("MFII11"), session=Session(),
        )


def test_fetch_rejects_non_html_payload():
    class Response:
        is_redirect = False
        is_permanent_redirect = False

        def __init__(self):
            self.headers = {"Content-Type": "application/pdf"}

        def raise_for_status(self):
            return None

        def close(self):
            return None

    class Session:
        def get(self, *args, **kwargs):
            return Response()

    with pytest.raises(ValueError, match="MIME inesperado"):
        fundamentus._fetch_index(
            fundamentus.fundamentus_reports_url("MFII11"), session=Session(),
        )


def test_persist_dry_run_only_reads_and_reports_overlap():
    result = fundamentus.parse_fundamentus_reports(
        b'<tr><td>06/2026</td><td><a href="https://fnet.bmfbovespa.com.br/fnet/publico/downloadDocumento?id=10">x</a></td></tr>',
        ticker="MFII11",
    )

    class Mapping:
        def one(self):
            return {"existing": 1, "conflicts": 0}

    class Result:
        def mappings(self):
            return Mapping()

    class Connection:
        def execute(self, *args, **kwargs):
            return Result()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    class Engine:
        def connect(self):
            return Connection()

        def begin(self):
            raise AssertionError("dry-run não pode abrir transação de escrita")

    summary = fundamentus.persist_discovery(result, engine=Engine(), write=False)

    assert summary == {
        "discovered": 1, "existing": 1, "new": 0, "inserted": 0,
        "identity_verified": True, "identity_conflicts": 0,
    }


def test_persist_write_requires_official_identity_overlap():
    result = fundamentus.parse_fundamentus_reports(
        b'<tr><td>06/2026</td><td><a href="https://fnet.bmfbovespa.com.br/fnet/publico/downloadDocumento?id=10">x</a></td></tr>',
        ticker="MFII11",
    )

    class Mapping:
        def one(self):
            return {"existing": 0, "conflicts": 0}

    class Result:
        def mappings(self):
            return Mapping()

    class Connection:
        def execute(self, *args, **kwargs):
            return Result()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    class Engine:
        def connect(self):
            return Connection()

        def begin(self):
            raise AssertionError("identidade não verificada não pode escrever")

    with pytest.raises(ValueError, match="sem sobreposição oficial"):
        fundamentus.persist_discovery(result, engine=Engine(), write=True)
