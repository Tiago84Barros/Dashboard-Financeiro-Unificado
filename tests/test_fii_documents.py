import hashlib
from datetime import date

import pytest

from data_pipeline.market.fii_documents import (
    PARSER_VERSION,
    DocumentParserTimeoutError,
    DocumentTooLargeError,
    _BatchDeadline,
    _download,
    _effective_document_reference,
    _extract_development_projects,
    _extract_evidence,
    _extract_pdf_text_isolated,
    _find_reusable_extraction,
    _HostCircuitBreaker,
    _layout_signature,
    _normalize_document_types,
    _provisional_candidates,
    _retry_delay_seconds,
    _storage,
)


def test_effective_document_reference_never_uses_observation_date_as_period():
    explicit = _effective_document_reference({
        "reference_date": date(2026, 6, 30),
        "source_url": "https://ri.test/Relatorio-1T2023.pdf",
    })
    inferred = _effective_document_reference({
        "reference_date": None,
        "source_url": "https://ri.test/Relatorio-2T2023.pdf",
    })
    unknown = _effective_document_reference({
        "reference_date": None,
        "source_url": "https://ri.test/2024/05/Relatorio-sem-periodo.pdf",
    })

    assert explicit == (date(2026, 6, 30), "document_metadata")
    assert inferred == (date(2023, 6, 30), "source_label")
    assert unknown == (None, "unknown")


def test_document_evidence_uses_methodology_names_and_page_numbers():
    pages = ["Resumo sem métricas.",
             "Vacância física 7,5% | WAULT 4,2 anos | Cap Rate 9,1% | LTV 45%"]
    evidence = _extract_evidence("\n".join(pages), pages)
    rows = {row["metric_name"]: row for row in evidence}

    assert PARSER_VERSION == "1.6.6"
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


def test_tenant_concentration_requires_explicit_largest_tenant_language():
    ambiguous = _extract_evidence(
        "Alocação por Segmento de Locatários. "
        "Locatários estratégicos e cap rate de 9,4%. "
        "Alocação por Inquilino. Contratos atípicos 81%.",
        fii_type="tijolo",
    )
    explicit = _extract_evidence(
        "O inquilino de maior peso representa 11,9% da receita do fundo.",
        fii_type="tijolo",
    )

    assert "tenant_concentration" not in {
        row["metric_name"] for row in ambiguous
    }
    tenant = next(
        row for row in explicit if row["metric_name"] == "tenant_concentration"
    )
    assert tenant["normalized_value"] == pytest.approx(.119)


def test_only_unambiguous_stable_evidence_is_provisionally_promoted():
    evidence = _extract_evidence(
        "Vacância física 7,5% | WAULT 4,2 anos | LTV 40% | LTV 55%"
    )

    assert not _provisional_candidates(
        evidence, extraction_confidence=.90, layout_changed=False
    )
    selected = _provisional_candidates(
        evidence, extraction_confidence=.90, layout_changed=False, enabled=True
    )

    assert {row["metric_name"] for row in selected} == {
        "vacancia_fisica", "wault_anos"
    }
    assert not _provisional_candidates(
        evidence, extraction_confidence=.90, layout_changed=True, enabled=True
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
        assert kwargs["headers"]["Accept-Encoding"] == "identity"
        assert kwargs["headers"]["Connection"] == "close"
        return Response()

    monkeypatch.setattr("data_pipeline.market.fii_documents.requests.get", fake_get)
    with pytest.raises(DocumentTooLargeError):
        _download("https://example.test/report.pdf", max_bytes=10)


def test_document_download_respects_attempt_limit(monkeypatch):
    calls = []

    def timeout(*args, **kwargs):
        calls.append(kwargs)
        raise __import__("requests").Timeout("indisponivel")

    monkeypatch.setattr("data_pipeline.market.fii_documents.requests.get", timeout)
    monkeypatch.setattr("data_pipeline.market.fii_documents.time.sleep", lambda _: None)
    with pytest.raises(__import__("requests").Timeout):
        _download("https://example.test/report.pdf", attempts=2)
    assert len(calls) == 2


def test_document_download_total_deadline_caps_per_attempt_timeout(monkeypatch):
    requests = __import__("requests")
    captured = []

    def timeout(*args, **kwargs):
        captured.append(kwargs["timeout"])
        raise requests.Timeout("synthetic timeout")

    monkeypatch.setattr("data_pipeline.market.fii_documents.requests.get", timeout)
    monkeypatch.setattr("data_pipeline.market.fii_documents.time.sleep", lambda _: None)

    with pytest.raises(requests.Timeout):
        _download(
            "https://example.test/report.pdf",
            timeout=60,
            attempts=1,
            max_elapsed_seconds=5,
        )

    assert captured == [pytest.approx(5, abs=.1)]


def test_isolated_parser_terminates_process_after_hard_timeout():
    class FakeQueue:
        def get(self, timeout=None):
            raise __import__("queue").Empty

        def close(self):
            return None

        def join_thread(self):
            return None

    class FakeProcess:
        exitcode = None

        def __init__(self):
            self.alive = True
            self.terminated = False

        def start(self):
            return None

        def join(self, timeout=None):
            return None

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.terminated = True
            self.alive = False

    class FakeContext:
        def __init__(self):
            self.process = FakeProcess()
            self.queue = FakeQueue()

        def Queue(self, maxsize):
            assert maxsize == 1
            return self.queue

        def Process(self, **kwargs):
            assert kwargs["daemon"] is True
            return self.process

    context = FakeContext()

    with pytest.raises(DocumentParserTimeoutError, match="prazo de extração"):
        _extract_pdf_text_isolated(
            b"%PDF-synthetic",
            timeout=1,
            mp_context=context,
        )

    assert context.process.terminated is True


def test_batch_deadline_reserves_time_for_claim_release():
    now = [100.0]
    deadline = _BatchDeadline(
        75,
        release_reserve_seconds=5,
        clock=lambda: now[0],
    )

    assert deadline.work_budget(25) == pytest.approx(25)
    now[0] = 168.0
    assert deadline.work_budget(25) == pytest.approx(2)
    assert deadline.exhausted() is False
    now[0] = 170.1
    assert deadline.work_budget(25) == 0
    assert deadline.exhausted() is True


def test_duplicate_content_reuse_requires_same_ticker_and_finished_run():
    expected = {
        "version_id": 41,
        "document_id": 17,
        "run_id": 53,
        "status": "needs_review",
    }

    class Mappings:
        def first(self):
            return expected

    class Result:
        def mappings(self):
            return Mappings()

    class Connection:
        def __init__(self):
            self.params = None

        def execute(self, statement, params):
            self.params = params
            return Result()

    connection = Connection()

    row = _find_reusable_extraction(
        connection,
        content_sha256="a" * 64,
        current_document_id=99,
        ticker="KNRE11",
    )

    assert row == expected
    assert connection.params == {
        "sha": "a" * 64,
        "document": 99,
        "ticker": "KNRE11",
        "name": "fii_public_report",
        "parser": PARSER_VERSION,
    }


def test_document_type_filter_is_normalized_and_deduplicated():
    assert _normalize_document_types([
        " relat gerencial ", "RELAT GERENCIAL", "Informe RI", "",
    ]) == ["INFORME RI", "RELAT GERENCIAL"]
    assert _normalize_document_types(None) == []


def test_document_download_retries_transient_http_with_retry_after(monkeypatch):
    requests = __import__("requests")
    calls = []
    sleeps = []

    class Unavailable:
        status_code = 503
        headers = {"Retry-After": "2"}

        def raise_for_status(self):
            raise requests.HTTPError("temporariamente indisponível", response=self)

        def close(self):
            return None

    class Available:
        headers = {"Content-Type": "application/pdf"}

        def raise_for_status(self):
            return None

        @staticmethod
        def iter_content(chunk_size):
            yield b"%PDF-ok"

        def close(self):
            return None

    def fake_get(*args, **kwargs):
        calls.append(kwargs)
        return Unavailable() if len(calls) == 1 else Available()

    monkeypatch.setattr("data_pipeline.market.fii_documents.requests.get", fake_get)
    monkeypatch.setattr("data_pipeline.market.fii_documents.time.sleep", sleeps.append)
    monkeypatch.setattr("data_pipeline.market.fii_documents.random.uniform", lambda *_: 0)

    content, mime = _download("https://example.test/report.pdf", attempts=2)

    assert content == b"%PDF-ok"
    assert mime == "application/pdf"
    assert len(calls) == 2
    assert sleeps == [2.0]


def test_document_download_retries_incomplete_stream_without_partial_content(
    monkeypatch,
):
    requests = __import__("requests")
    calls = []

    class Incomplete:
        headers = {"Content-Type": "application/pdf"}

        def raise_for_status(self):
            return None

        @staticmethod
        def iter_content(chunk_size):
            yield b"%PDF-partial"
            raise requests.exceptions.ChunkedEncodingError("stream interrompido")

        def close(self):
            return None

    class Complete:
        headers = {"Content-Type": "application/pdf"}

        def raise_for_status(self):
            return None

        @staticmethod
        def iter_content(chunk_size):
            yield b"%PDF-complete"

        def close(self):
            return None

    def fake_get(*args, **kwargs):
        calls.append(kwargs)
        return Incomplete() if len(calls) == 1 else Complete()

    monkeypatch.setattr("data_pipeline.market.fii_documents.requests.get", fake_get)
    monkeypatch.setattr("data_pipeline.market.fii_documents.time.sleep", lambda _: None)
    monkeypatch.setattr("data_pipeline.market.fii_documents.random.uniform", lambda *_: 0)

    content, _ = _download("https://example.test/report.pdf", attempts=2)

    assert content == b"%PDF-complete"
    assert len(calls) == 2


def test_retry_delay_adds_bounded_jitter_and_respects_retry_after(monkeypatch):
    class Response:
        headers = {"Retry-After": "7"}

    monkeypatch.setattr(
        "data_pipeline.market.fii_documents.random.uniform", lambda *_: .5
    )
    assert _retry_delay_seconds(0, Response()) == pytest.approx(7.5)


def test_host_circuit_breaker_opens_only_after_consecutive_transient_failures():
    circuit = _HostCircuitBreaker(threshold=3)
    host = "fnet.example.test"

    assert not circuit.failure(host, transient=True)
    circuit.success(host)
    assert not circuit.failure(host, transient=True)
    assert not circuit.failure(host, transient=False)
    assert not circuit.failure(host, transient=True)
    assert circuit.failure(host, transient=True)
    assert circuit.is_open(host)


def test_document_download_rejects_non_https_before_network(monkeypatch):
    monkeypatch.setattr(
        "data_pipeline.market.fii_documents.requests.get",
        lambda *_args, **_kwargs: pytest.fail("rede não deveria ser acessada"),
    )
    with pytest.raises(ValueError, match="URL HTTPS"):
        _download("http://example.test/report.pdf")


def test_hash_only_storage_does_not_persist_new_binary(monkeypatch, tmp_path):
    monkeypatch.setenv("FII_DOCUMENT_CACHE", str(tmp_path))
    # _storage curto-circuita para 'remote_only' dentro do Actions (o runner é
    # efêmero e não deve materializar cache). Aqui o alvo é a política de
    # retenção, então o ambiente é neutralizado — senão o teste passa na
    # máquina do dev e reprova no CI.
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    content = b"%PDF-auditable"
    sha = hashlib.sha256(content).hexdigest()

    backend, key, existed = _storage(
        content, sha, ".pdf", retain_binary=False
    )

    assert (backend, key, existed) == ("source_hash", None, False)
    assert not list(tmp_path.rglob("*.pdf"))


def test_storage_no_github_actions_nao_materializa_cache(monkeypatch, tmp_path):
    """O ramo específico do CI existia sem cobertura — agora é verificado."""
    monkeypatch.setenv("FII_DOCUMENT_CACHE", str(tmp_path))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    content = b"%PDF-runner"
    sha = hashlib.sha256(content).hexdigest()

    assert _storage(content, sha, ".pdf") == ("remote_only", None, False)
    assert not list(tmp_path.rglob("*.pdf"))


def test_development_report_extracts_aggregate_metrics_with_value_nature():
    # Escapes tornam o fixture independente da code page do checkout no Windows.
    pages = [
        "34 ativos formam a carteira atual do fundo. 7 conclu\u00eddos. "
        "14 em obras. 12 em pr\u00e9-lan\u00e7amento.",
        "R$ 1,149 bilh\u00e3o de VGV N\u00e3o Lan\u00e7ado. "
        "4.651 unidades previstas. 214.429 m\u00b2 de \u00e1rea vend\u00e1vel.",
        "R$ 293,2 milh\u00f5es a receber. "
        "R$ 878,8 milh\u00f5es em unidades em estoque.",
    ]
    rows = {
        row["metric_name"]: row
        for row in _extract_evidence("\n".join(pages), pages, fii_type="hibrido")
    }

    assert rows["development_active_project_count"]["normalized_value"] == 34
    assert rows["development_planned_units"]["normalized_value"] == 4651
    assert rows["development_sellable_area_sqm"]["normalized_value"] == 214429
    assert rows["development_potential_vgv_brl"]["normalized_value"] == pytest.approx(
        1_149_000_000
    )
    assert rows["development_inventory_brl"]["value_nature"] == "manager_estimate"
    assert rows["development_receivables_brl"]["value_nature"] == "manager_reported"


def test_development_project_table_preserves_estimates_as_estimates():
    page = """
    Classificação Ativo Imobiliário Percentual em Carteira Local Fase
    % Obras % Vendas TIR Esperada (a.a.) Resultado Esperado (R$ MM)
    Incorporação Residencial Golden Boituva 4,2% Boituva - SP Obras
    93% 92% 13,3% 18,244
    Urbanização Reserva da Ilha 6,0% Sertaneja - PR Obras
    45% 11% 19,8% 119,466
    """

    rows = _extract_development_projects([page])

    assert len(rows) == 2
    golden = next(row for row in rows if row["project_name"] == "Golden Boituva")
    assert golden["portfolio_weight"] == pytest.approx(.042)
    assert golden["construction_progress"] == pytest.approx(.93)
    assert golden["sales_progress"] == pytest.approx(.92)
    assert golden["expected_irr"] == pytest.approx(.133)
    assert golden["expected_result_brl"] == pytest.approx(18_244_000)
    assert golden["value_nature"] == "manager_estimate"


def test_qualitative_findings_exclude_glossary_and_keep_fund_specific_risk():
    from data_pipeline.market.fii_documents import _extract_document_findings

    page = (
        "Risco de Crédito: Possibilidade de inadimplência dos devedores dos CRIs. "
        "High Yield: Classificação de CRIs de maior risco de crédito e retorno. "
        "A carteira do fundo enfrenta risco de atraso nas obras e aumento de custos."
    )

    rows = _extract_document_findings([page])

    assert [row["claim_text"] for row in rows] == [
        "A carteira do fundo enfrenta risco de atraso nas obras e aumento de custos."
    ]


def test_qualitative_findings_do_not_treat_risk_word_as_material_event():
    from data_pipeline.market.fii_documents import _extract_document_findings

    page = (
        "Cotas Mezanino: Classe intermediária de risco e retorno. "
        "São as primeiras a receber amortizações e, por isso, possuem menor risco. "
        "O cenário ficou mais difícil, com risco de pressão cambial e aumento de custos."
    )

    rows = _extract_document_findings([page])

    assert [row["claim_text"] for row in rows] == [
        "O cenário ficou mais difícil, com risco de pressão cambial e aumento de custos."
    ]
