from datetime import datetime, timezone
import io
import zipfile

import pytest

from data_pipeline.market.fii_cvm_structured import (
    CvmArchive, _validate_parsed_archive, fetch_archive, parse_archive,
)


def _archive(kind: str, files: dict[str, str], year: int = 2026) -> CvmArchive:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zipped:
        for name, content in files.items():
            zipped.writestr(name, content.encode("latin-1"))
    raw = buffer.getvalue()
    return CvmArchive(kind, year, "https://cvm.test/archive.zip", raw,
                      datetime(2026, 7, 12, tzinfo=timezone.utc), {}, "sha")


def test_monthly_creates_regulatory_metrics_and_leverage():
    general = (
        "CNPJ_Fundo_Classe;Data_Referencia;Versao;Data_Entrega\n"
        "12.345.678/0001-00;2026-06-30;1;2026-07-05\n")
    complement = (
        "CNPJ_Fundo_Classe;Data_Referencia;Versao;Valor_Ativo;Patrimonio_Liquido;"
        "Valor_Patrimonial_Cotas;Total_Numero_Cotistas;Percentual_Dividend_Yield_Mes\n"
        "12.345.678/0001-00;2026-06-30;1;1000;800;80;100;0.01\n")
    assets = (
        "CNPJ_Fundo_Classe;Data_Referencia;Versao;Total_Passivo;Disponibilidades;"
        "Imoveis_Renda_Acabados;CRI;FII\n"
        "12.345.678/0001-00;2026-06-30;1;200;100;500;300;100\n")
    parsed = parse_archive(_archive("monthly", {
        "inf_mensal_fii_geral_2026.csv": general,
        "inf_mensal_fii_complemento_2026.csv": complement,
        "inf_mensal_fii_ativo_passivo_2026.csv": assets,
    }), {"12345678000100": "TEST11"}, 7)
    values = {row["metric_name"]: row.get("value_numeric") for row in parsed["observations"]}
    assert values["leverage"] == .2
    assert values["nav_per_share"] == 80
    assert values["total_investors"] == 100
    assert all(row["availability_quality"] == "verified_publication"
               for row in parsed["observations"])


def test_quarterly_derives_expiry_indexer_tenant_and_duration():
    common = "12.345.678/0001-00;2026-06-30;1"
    files = {
        "inf_trimestral_fii_geral_2026.csv":
            "CNPJ_Fundo_Classe;Data_Referencia;Versao;Data_Entrega\n" + common + ";2026-07-05\n",
        "inf_trimestral_fii_complemento_2026.csv":
            "CNPJ_Fundo_Classe;Data_Referencia;Versao;Percentual_Vencimento_Receita_FII_Faixa_Ate_3Meses;"
            "Percentual_Vencimento_Receita_FII_Faixa_3a6Meses;Percentual_Indexador_Receita_FII_IPCA;"
            "Percentual_Indexador_Receita_FII_IGPM\n" + common + ";0.1;0.2;0.6;0.4\n",
        "inf_trimestral_fii_ativo_2026.csv":
            "CNPJ_Fundo_Classe;Data_Referencia;Versao;CNPJ_Emissor;Emissao;Serie;Nome_Ativo;Data_Vencimento;Valor\n"
            + common + ";1;E1;S1;CRI A;2029-06-30;600\n"
            + common + ";2;E2;S2;CRI B;2030-06-30;400\n",
        "inf_trimestral_fii_imovel_2026.csv":
            "CNPJ_Fundo_Classe;Data_Referencia;Versao;Nome_Imovel;Percentual_Receitas_FII;Percentual_Vacancia\n"
            + common + ";Imóvel A;0.6;0.05\n" + common + ";Imóvel B;0.4;0.10\n",
        "inf_trimestral_fii_imovel_renda_acabado_inquilino_2026.csv":
            "CNPJ_Fundo_Classe;Data_Referencia;Versao;Setor_Atuacao;Percentual_Receitas_FII\n"
            + common + ";Logística;0.7\n" + common + ";Varejo;0.3\n",
    }
    parsed = parse_archive(_archive("quarterly", files), {"12345678000100": "TEST11"}, 8)
    values = {row["metric_name"]: row.get("value_numeric") for row in parsed["observations"]}
    assert round(values["lease_expiry_concentration_24m"], 6) == .3
    assert round(values["indexer_diversification"], 2) == .48
    assert values["tenant_concentration"] == .7
    assert values["duration_anos"] > 3
    assert round(values["property_diversification"], 2) == .48


def test_financials_maps_auditor_opinion_to_quality():
    csv = ("CNPJ_Fundo_Classe;Data_Referencia;Data_Entrega;Versao;Parecer_Auditor;Link_Download\n"
           "12.345.678/0001-00;2025-12-31;2026-03-30;1;Sem ressalva e sem ênfase;https://x.test/a.pdf\n")
    raw = csv.encode("latin-1")
    archive = CvmArchive("financials", 2026, "https://cvm.test/dfin.csv", raw,
                         datetime(2026, 7, 12, tzinfo=timezone.utc), {}, "sha")
    parsed = parse_archive(archive, {"12345678000100": "TEST11"}, 9)
    values = {row["metric_name"]: row.get("value_numeric") for row in parsed["observations"]}
    assert values["auditor_opinion_quality"] == 1
    assert parsed["documents"][0]["ticker"] == "TEST11"


def test_eventual_documents_measure_disclosure_regularity():
    rows = [
        "CNPJ_FUNDO_CLASSE;TP_FUNDO_CLASSE;DT_COMPTC;DT_RECEB;TP_DOC;ID_DOC;LINK_ARQ"
    ]
    for month in range(1, 13):
        rows.append(
            f"12.345.678/0001-00;FII;2025-{month:02d}-28;2025-{month:02d}-28;"
            f"RELAT GERENCIAL;{month};https://x.test/{month}.pdf"
        )
    rows.append(
        "98.765.432/0001-00;FIAGRO;2025-12-31;2025-12-31;"
        "RELAT GERENCIAL;99;https://x.test/99.pdf"
    )
    raw = ("\n".join(rows) + "\n").encode("latin-1")
    archive = CvmArchive("eventual", 2025, "https://cvm.test/eventual.csv", raw,
                         datetime(2026, 7, 12, tzinfo=timezone.utc), {}, "sha")
    parsed = parse_archive(archive, {
        "12345678000100": "TEST11", "98765432000100": "AGRO11"
    }, 10)
    values = {row["metric_name"]: row.get("value_numeric")
              for row in parsed["observations"]}
    assert values["cvm_event_quality"] == 1
    assert values["cvm_event_document_count"] == 12
    assert len(parsed["documents"]) == 12
    assert all(row["reference_date"] == "2025-12-28"
               for row in parsed["observations"])
    assert all(row["reference_date"] <= row["available_at"][:10]
               for row in parsed["observations"])


def test_monthly_supports_legacy_cnpj_fundo_layout_before_2021():
    general = (
        "CNPJ_Fundo;Data_Referencia;Versao;Data_Entrega\n"
        "12.345.678/0001-00;2020-12-31;1;2021-01-08\n")
    complement = (
        "CNPJ_Fundo;Data_Referencia;Versao;Patrimonio_Liquido;"
        "Valor_Patrimonial_Cotas;Total_Numero_Cotistas\n"
        "12.345.678/0001-00;2020-12-31;1;800;80;100\n")
    assets = (
        "CNPJ_Fundo;Data_Referencia;Versao;Valor_Ativo;Total_Passivo\n"
        "12.345.678/0001-00;2020-12-31;1;1000;200\n")
    parsed = parse_archive(_archive("monthly", {
        "inf_mensal_fii_geral_2020.csv": general,
        "inf_mensal_fii_complemento_2020.csv": complement,
        "inf_mensal_fii_ativo_passivo_2020.csv": assets,
    }, year=2020), {"12345678000100": "TEST11"}, 7)
    values = {row["metric_name"]: row.get("value_numeric")
              for row in parsed["observations"]}
    assert values["nav_per_share"] == 80
    assert values["leverage"] == .2


def test_cvm_archive_uses_conditional_cache_on_not_modified(tmp_path, monkeypatch):
    cache = tmp_path / "monthly" / "2026.zip"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"PKcached")
    cache.with_suffix(".zip.headers.json").write_text(
        '{"ETag":"abc","Last-Modified":"Mon, 13 Jul 2026 10:00:00 GMT"}',
        encoding="utf-8")

    class Response:
        status_code = 304

    class Session:
        def __init__(self):
            self.headers = {}

        @staticmethod
        def mount(*args, **kwargs):
            return None

        @staticmethod
        def get(url, timeout, headers):
            assert headers == {"If-None-Match": "abc",
                               "If-Modified-Since": "Mon, 13 Jul 2026 10:00:00 GMT"}
            return Response()

    monkeypatch.setattr("requests.Session", Session)
    archive = fetch_archive("monthly", 2026, cache_root=tmp_path)
    assert archive is not None
    assert archive.from_cache is True
    assert archive.content == b"PKcached"


def test_cvm_archive_quality_gate_rejects_empty_structured_partition():
    with pytest.raises(ValueError, match="sem cobertura"):
        _validate_parsed_archive(
            _archive("monthly", {"empty_2026.csv": "a;b\n"}),
            {"observations": [], "exposures": [], "documents": [], "contexts": 0})
