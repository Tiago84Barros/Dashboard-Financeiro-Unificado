from datetime import date

import core.cvm_ipe as ipe

# CSV sintético no formato do IPE (CVM Dados Abertos): ;-separado, latin-1.
_CSV = (
    "CNPJ_Companhia;Categoria;Codigo_CVM;Data_Entrega;Data_Referencia;Especie;"
    "Link_Download;Modalidade;Nome_Companhia;Protocolo;Tipo;Tipo_Apresentacao;Versao;Assunto\n"
    "00.000.000/0001-00;Fato Relevante;9512;2026-02-10;2026-02-10;;"
    "https://www.rad.cvm.gov.br/down?doc=1;AP;WEG SA;1;Fato Relevante;;1;Aquisição\n"
    "11.111.111/0001-11;Comunicado ao Mercado;906;2026-02-11;2026-02-11;;"
    "https://www.rad.cvm.gov.br/down?doc=2;AP;PETROBRAS;2;Comunicado;;1;Dividendos\n"
    "22.222.222/0001-22;Assembleia;9512;2026-02-12;2026-02-12;;"
    "https://www.rad.cvm.gov.br/down?doc=3;CA;WEG SA;3;AGO;;1;Cancelada\n"
    "33.333.333/0001-33;Regulamento;9512;2026-02-13;2026-02-13;;"
    "https://www.rad.cvm.gov.br/down?doc=4;AP;WEG SA;4;Regulamento;;1;Irrelevante\n"
    "44.444.444/0001-44;Fato Relevante;9999;2026-02-14;2026-02-14;;"
    "https://www.rad.cvm.gov.br/down?doc=5;AP;FORA DO UNIVERSO;5;Fato Relevante;;1;X\n"
).encode("latin-1")


def test_ipe_csv_url():
    assert ipe.ipe_csv_url(2026).endswith("ipe_cia_aberta_2026.csv")


def test_parse_ipe_csv():
    rows = ipe.parse_ipe_csv(_CSV)
    assert len(rows) == 5
    r0 = rows[0]
    assert r0["codigo_cvm"] == 9512
    assert r0["categoria"] == "Fato Relevante"
    assert r0["modalidade"] == "AP"
    assert r0["data_entrega"] == date(2026, 2, 10)
    assert r0["url"].endswith("doc=1")


def test_is_relevant_category():
    assert ipe.is_relevant_category("Fato Relevante")
    assert ipe.is_relevant_category("Comunicado ao Mercado")
    assert not ipe.is_relevant_category("Regulamento")


def test_filter_docs_keeps_universe_relevant_not_cancelled():
    rows = ipe.parse_ipe_csv(_CSV)
    cod_map = {9512: "WEGE3", 906: "PETR4"}  # 9999 fora do universo
    docs = ipe.filter_docs(rows, cod_map)
    urls = {d["url"][-5:] for d in docs}
    # doc=1 (WEGE3 Fato Relevante) e doc=2 (PETR4 Comunicado) entram;
    # doc=3 cancelado, doc=4 categoria irrelevante, doc=5 fora do universo → fora
    assert "doc=1" in urls and "doc=2" in urls
    assert "doc=3" not in urls and "doc=4" not in urls and "doc=5" not in urls
    tickers = {d["ticker"] for d in docs}
    assert tickers == {"WEGE3", "PETR4"}


def test_metadata_text_has_key_fields():
    rows = ipe.parse_ipe_csv(_CSV)
    docs = ipe.filter_docs(rows, {9512: "WEGE3"})
    txt = ipe.metadata_text(docs[0])
    assert "WEGE3" in txt and "Fato Relevante" in txt and "Aquisição" in txt


def test_chunk_text():
    assert ipe.chunk_text("") == []
    assert ipe.chunk_text("curto") == ["curto"]
    big = "x" * 3000
    chunks = ipe.chunk_text(big, size=1200, overlap=150)
    assert len(chunks) >= 3
    assert all(len(c) <= 1200 for c in chunks)


def test_sha256_deterministic():
    assert ipe.sha256("a", "b") == ipe.sha256("a", "b")
    assert ipe.sha256("a", "b") != ipe.sha256("a", "c")


# ── extração de texto completo ────────────────────────────────────────────────

def test_extract_text_empty():
    assert ipe.extract_text(b"") == ""


def test_extract_text_html():
    html = b"<html><body><h1>Fato Relevante</h1><p>A empresa comunica algo.</p>" \
           b"<script>ignore()</script></body></html>"
    txt = ipe.extract_text(html)
    assert "Fato Relevante" in txt and "comunica algo" in txt
    assert "ignore" not in txt  # script removido


def test_extract_text_zip_with_html():
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("doc.html", "<html><body>Resultado trimestral lucro de 100</body></html>")
    txt = ipe.extract_text(buf.getvalue())
    assert "Resultado trimestral" in txt


def test_rate_limited_classification():
    assert ipe.is_rate_limited(ipe.RateLimited("HTTP 429")) is True
    assert ipe.is_rate_limited(ipe.DocFetchError("HTTP 404")) is False
    assert ipe.is_rate_limited(ValueError("x")) is False
