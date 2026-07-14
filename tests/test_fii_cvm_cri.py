from datetime import datetime, timezone
import io
import json
import zipfile

import pytest

from data_pipeline.market.fii_cvm_cri import _duration_years, parse_cri_archive, security_key
from data_pipeline.market.fii_cvm_structured import CvmArchive


def _archive(files: dict[str, str]) -> CvmArchive:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zipped:
        for name, content in files.items():
            zipped.writestr(name, content.encode("latin-1"))
    return CvmArchive("cri", 2026, "https://cvm.test/cri.zip", buffer.getvalue(),
                      datetime(2026, 7, 12, tzinfo=timezone.utc), {}, "sha")


def test_security_key_requires_regulatory_identifier():
    assert security_key("12.345.678/0001-90", "02", "003") == "12345678000190|2|3"
    assert security_key("", "2", "3", "BRTESTCRI123") == "BRTESTCRI123"
    assert security_key("", "", "", "CRI informal") is None


def test_duration_parser_accepts_decimal_months_without_matching_decimal_tail():
    value = _duration_years({}, {"Duration_Carteira": "2 ano(s) 4.317769645158309 mes(es)"},
                            datetime(2026, 1, 1).date(), None)
    assert value == pytest.approx(2 + 4.317769645158309 / 12)


def test_parse_cri_archive_extracts_credit_risk_and_profiles():
    common = "12.345.678/0001-90;CERT1;2026-06-30;1"
    files = {
        "inf_mensal_cri_geral_2026.csv":
            "CNPJ_Emissora;Codigo_Identificacao_Certificado;Data_Referencia;Versao;"
            "Data_Entrega;Numero_Emissao;Anos_Duration_Carteira;Meses_Duration_Carteira;"
            "Indice_LTV;Taxas_Medias_Indexadores_Creditos_Vinculados\n"
            + common + ";2026-07-05;2;3;6;55;IPCA + 7,5%\n",
        "inf_mensal_cri_classe_2026.csv":
            "CNPJ_Emissora;Codigo_Identificacao_Certificado;Data_Referencia;Versao;"
            "Numero_Serie;Classe;Data_Vencimento;Classificacao_Risco_Atual;"
            "Indice_Subordinacao_Minimo;Taxas_Indexadores;Taxa_Juros;Valor_Certificados\n"
            + common + ";3;Senior;2030-06-30;AA+;20;IPCA;IPCA + 7,5%;800\n",
        "inf_mensal_cri_creditos_2026.csv":
            "CNPJ_Emissora;Codigo_Identificacao_Certificado;Data_Referencia;Versao;"
            "A_Vencer;Nao_Pagos;Duration_Carteira\n" + common + ";990;10;3 anos 6 meses\n",
        "inf_mensal_cri_carteira_2026.csv":
            "CNPJ_Emissora;Codigo_Identificacao_Certificado;Data_Referencia;Versao;"
            "Creditos_Vinculados;Creditos_Vinculados_Inadimplentes\n"
            + common + ";1000;10\n",
        "inf_mensal_cri_cedente_devedor_2026.csv":
            "CNPJ_Emissora;Codigo_Identificacao_Certificado;Data_Referencia;Versao;"
            "Tipo;CNPJ;Percentual\n"
            + common + ";Devedor;11.111.111/0001-11;60\n"
            + common + ";Devedor;22.222.222/0001-22;40\n",
    }
    parsed = parse_cri_archive(_archive(files), raw_payload_id=9, release_id=3)
    rows = {row["metric_name"]: row for row in parsed["observations"]}

    assert parsed["securities"] == 1
    assert rows["duration_anos"]["value_numeric"] == pytest.approx(3.5)
    assert rows["ltv"]["value_numeric"] == pytest.approx(.55)
    assert rows["rating_quality"]["value_numeric"] == pytest.approx(.85)
    assert rows["subordination_protection"]["value_numeric"] == pytest.approx(.20)
    assert rows["delinquency"]["value_numeric"] == pytest.approx(.01)
    assert rows["credit_spread"]["value_numeric"] == pytest.approx(.075)
    assert json.loads(rows["indexer_profile"]["value_json"]) == {"IPCA": 1.0}
    assert json.loads(rows["debtor_profile"]["value_json"]) == {
        "11111111000111": .6, "22222222000122": .4,
    }
    assert all(row["knowledge_at"] == datetime(2026, 7, 6, 2, 59, 59,
                                                tzinfo=timezone.utc)
               for row in parsed["observations"])
