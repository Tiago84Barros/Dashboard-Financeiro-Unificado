"""A fita de FIIs (COTAHIST, BDI 12) precisa aguentar ser recarregada todo dia.

Até 29/09/2026 ela foi carregada uma vez só (15/07) e ninguém a agendou. Ao
entrar na rotina diária, três coisas passam a importar:

* o ZIP do ano corrente muda a cada pregão, e `archive_sha256` faz parte da
  chave única -- sem trocar as linhas do sha anterior, cada recarga duplicaria o
  ano e todo leitor por (ticker, pregão) somaria em dobro;
* download que falha e cai no cache carregaria a fita velha como se fosse nova;
* o ano fechado não pode custar ~90 MB de download por dia.

O primeiro caso só aparece com o `ON CONFLICT` executando de verdade, por isso
o banco é descartável no armazém local, como em `test_b3_precos_reingestao`.
Sem armazém de pé, os testes são pulados.
"""
import io
import uuid
import zipfile
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text

from data_pipeline.market import fii_b3_history as fbh

ANO = datetime.now(timezone.utc).year


def _linha(ticker: str, data: str) -> str:
    linha = list(" " * 245)
    for inicio, fim, valor in (
        (0, 2, "01"), (2, 10, data), (10, 12, "12"), (12, 24, ticker),
        (24, 27, "010"), (27, 39, "FUNDO TESTE"),
        (56, 69, "0000000010000"), (69, 82, "0000000011000"),
        (82, 95, "0000000009000"), (95, 108, "0000000010000"),
        (108, 121, "0000000010500"), (147, 152, "00010"),
        (152, 170, "000000000000001000"), (170, 188, "000000000010500000"),
        (230, 242, "BRTESTCTF000"),
    ):
        linha[inicio:fim] = list(str(valor).ljust(fim - inicio)[:fim - inicio])
    return "".join(linha)


def _zip(*datas: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as compactado:
        compactado.writestr(f"COTAHIST_A{ANO}.TXT",
                            "\n".join(_linha("TEST11", d) for d in datas))
    return buffer.getvalue()


_DDL = """
CREATE SCHEMA market;
CREATE TABLE market.fii_parser_versions (
    parser_name text, parser_version text, schema_version text,
    code_sha256 text, status text, activated_at timestamptz,
    PRIMARY KEY (parser_name, parser_version));
CREATE TABLE market.fii_b3_archive_loads (
    archive_year int, archive_sha256 text, source_url text,
    expected_rows int, loaded_rows int, status text, raw_payload_id bigint,
    started_at timestamptz, updated_at timestamptz, completed_at timestamptz,
    error_message text, parser_name text, parser_version text,
    UNIQUE (archive_year, archive_sha256, parser_name, parser_version));
CREATE TABLE market.fii_b3_security_history (
    ticker text, trade_date date, issuer_short_name text, specification text,
    isin text, open numeric, high numeric, low numeric, average numeric,
    close numeric, trades int, quantity bigint, financial_volume numeric,
    source text, source_url text, raw_payload_id bigint,
    collected_at timestamptz, archive_sha256 text,
    UNIQUE (ticker, trade_date, archive_sha256));
"""


@pytest.fixture
def banco(monkeypatch):
    try:
        from scripts.construir_memoria_mercado import warehouse_url
        url = warehouse_url()
    except Exception as erro:  # noqa: BLE001
        pytest.skip(f"sem armazem local ({erro})")
    nome = f"teste_fii_b3_{uuid.uuid4().hex[:10]}"
    administrador = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with administrador.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{nome}"'))
    except Exception as erro:  # noqa: BLE001
        administrador.dispose()
        pytest.skip(f"armazem local nao respondeu ({erro})")
    engine = create_engine(url.rsplit("/", 1)[0] + f"/{nome}")
    with engine.begin() as conn:
        conn.exec_driver_sql(_DDL)
    monkeypatch.setattr(fbh, "get_pipeline_engine", lambda: engine)
    monkeypatch.setattr(fbh, "save_raw_payload", lambda *a, **k: None)
    try:
        yield engine
    finally:
        engine.dispose()
        with administrador.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{nome}" WITH (FORCE)'))
        administrador.dispose()


def _servir(monkeypatch, conteudo: bytes, *, do_cache: bool = False) -> list[int]:
    pedidos: list[int] = []

    def fetch(year, timeout=180):
        pedidos.append(year)
        return conteudo, "https://teste/COTAHIST", (
            {"cache-fallback": "true"} if do_cache else {})

    monkeypatch.setattr(fbh, "fetch_year", fetch)
    return pedidos


def test_recarga_com_um_pregao_a_mais_nao_duplica(banco, monkeypatch):
    _servir(monkeypatch, _zip(f"{ANO}0105", f"{ANO}0106"))
    assert fbh.ingest_b3_history(years=1)["status"] == "completed"
    _servir(monkeypatch, _zip(f"{ANO}0105", f"{ANO}0106", f"{ANO}0107"))
    assert fbh.ingest_b3_history(years=1)["status"] == "completed"

    with banco.connect() as conn:
        linhas, pares, shas = conn.execute(text("""
            SELECT count(*), count(DISTINCT (ticker, trade_date)),
                   count(DISTINCT archive_sha256)
            FROM market.fii_b3_security_history
        """)).one()
    assert (linhas, pares, shas) == (3, 3, 1)


def test_download_que_cai_no_cache_sai_parcial(banco, monkeypatch):
    _servir(monkeypatch, _zip(f"{ANO}0105"), do_cache=True)
    relatorio = fbh.ingest_b3_history(years=1)
    # Carrega o que tem, mas não deixa a rotina marcar a fita como em dia.
    assert relatorio["status"] == "partial"
    assert "cache" in relatorio["errors"][0]["error"]
    with banco.connect() as conn:
        assert conn.execute(text(
            "SELECT count(*) FROM market.fii_b3_security_history")).scalar() == 1


@pytest.mark.parametrize("concluido_em, baixa", [
    (f"{ANO}-01-03", False),   # depois da virada: arquivo definitivo
    (f"{ANO - 1}-12-20", True),  # antes: faltam os pregões finais
])
def test_ano_fechado_so_e_baixado_se_a_carga_nao_viu_o_arquivo_final(
        banco, monkeypatch, concluido_em, baixa):
    with banco.begin() as conn:
        conn.execute(text("""
            INSERT INTO market.fii_b3_archive_loads (archive_year, archive_sha256,
                status, completed_at, parser_name, parser_version)
            VALUES (:ano, 'x', 'completed', CAST(:quando AS timestamptz), :p, :v)
        """), {"ano": ANO - 1, "quando": concluido_em,
               "p": fbh.PARSER_NAME, "v": fbh.PARSER_VERSION})
    pedidos = _servir(monkeypatch, _zip(f"{ANO}0105"))
    fbh.ingest_b3_history(years=2)
    assert ((ANO - 1) in pedidos) is baixa
    assert ANO in pedidos


def test_anos_recentes_das_acoes_incluem_o_anterior_so_em_janeiro():
    from datetime import date

    from scripts.ingerir_precos_b3 import _anos_do_argumento

    assert list(_anos_do_argumento("recentes", date(2027, 1, 4))) == [2026, 2027]
    assert list(_anos_do_argumento("recentes", date(2026, 9, 29))) == [2026]


def test_rotina_baixa_antes_de_reler_o_cache():
    from core.publicacao_agenda import POR_CHAVE

    alvo = POR_CHAVE["b3_pregao"]
    assert alvo.passos[0][:2] == ("run_market_ingest.py", "fiis-b3-history")
    assert "--cache-apenas" in alvo.passos[1]


_ZIP_VAZIO = b"PK\x05\x06" + b"\x00" * 18  # o que a reserva da B3 devolveu em 29/09


def test_zip_vazio_e_recusado_sem_sobrescrever_o_cache(tmp_path, monkeypatch):
    bom = _zip(f"{ANO}0105")
    (tmp_path / f"COTAHIST_A{ANO}.ZIP").write_bytes(bom)
    monkeypatch.setattr(fbh, "CACHE_ROOT", tmp_path)

    class Resposta:
        status_code = 200
        content = _ZIP_VAZIO
        headers: dict = {}

        def raise_for_status(self):
            pass

    import requests
    monkeypatch.setattr(requests.Session, "get", lambda self, url, timeout: Resposta())
    conteudo, _, cabecalhos = fbh.fetch_year(ANO)
    assert cabecalhos["cache-fallback"] == "true"
    assert "ZIP vazio" in cabecalhos["cache-fallback-motivo"]
    assert conteudo == bom
    assert (tmp_path / f"COTAHIST_A{ANO}.ZIP").read_bytes() == bom


def test_problema_do_zip():
    assert fbh._problema_do_zip(_ZIP_VAZIO).startswith("ZIP vazio")
    assert fbh._problema_do_zip(b"<html>") == "resposta não ZIP"
    assert fbh._problema_do_zip(b"PK\x03\x04lixo").startswith("ZIP ilegível")
    assert fbh._problema_do_zip(_zip(f"{ANO}0105")) is None


def test_arquivo_sem_linha_de_fii_nao_conclui(banco, monkeypatch):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as compactado:
        compactado.writestr("COTAHIST_A.TXT", "00COTAHIST\n")
    _servir(monkeypatch, buffer.getvalue())
    relatorio = fbh.ingest_b3_history(years=1)
    assert relatorio["status"] == "failed"
    with banco.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM market.fii_b3_archive_loads "
                                 "WHERE status='completed'")).scalar() == 0
