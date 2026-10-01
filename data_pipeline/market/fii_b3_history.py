"""Histórico oficial B3 (COTAHIST) para universo sem viés de sobrevivência.

Os arquivos não são ajustados por proventos. Eles servem como security master,
preço negociado e evidência de que o ticker existia na data; o backtest combina
retornos com proventos separadamente quando disponíveis e reporta a cobertura.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from data_pipeline.market import b3_cotahist
from data_pipeline.market.repository import save_raw_payload
from data_pipeline.utils.db_utils import get_pipeline_engine

URLS = (
    "https://bvmf.bmfbovespa.com.br/InstDados/SerHist/COTAHIST_A{year}.ZIP",
    "https://www.b3.com.br/pesquisapregao/download?filelist=COTAHIST_A{year}.ZIP",
)
CACHE_ROOT = Path("local_staging/fii_b3_cotahist")
SOURCE = "b3_cotahist"
PARSER_NAME = "b3_cotahist_fixed_width"
PARSER_VERSION = "1.1.0"
PARSER_SCHEMA_VERSION = "cotahist-layout-2020-r2"
_LOGGER = logging.getLogger(__name__)
_BATCH_SIZE = 1_000


def _problema_do_zip(content: bytes) -> str | None:
    """Motivo para recusar a resposta, ou ``None`` se é um COTAHIST legível.

    Começar com ``PK`` não basta. Em 29/09/2026 a URL de reserva da B3 devolveu
    HTTP 200 com um ZIP de 22 bytes, só o diretório central e nenhum arquivo;
    ele passou por válido, sobrescreveu o cache de 72 MB e a carga saiu
    "completed" com zero linhas.
    """
    import zipfile

    if not content.startswith(b"PK"):
        return "resposta não ZIP"
    try:
        membros = zipfile.ZipFile(io.BytesIO(content)).infolist()
    except zipfile.BadZipFile as exc:
        return f"ZIP ilegível ({exc})"
    if not any(m.file_size > 0 for m in membros):
        return f"ZIP vazio ({len(content)} bytes)"
    return None


def fetch_year(year: int, timeout: int = 180) -> tuple[bytes, str, dict[str, str]]:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    cache = CACHE_ROOT / f"COTAHIST_A{year}.ZIP"
    session = requests.Session()
    session.headers["User-Agent"] = "DashboardFinanceiro/1.0 (+b3-cotahist)"
    session.mount("https://", HTTPAdapter(max_retries=Retry(
        total=2, backoff_factor=1.0, status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}), respect_retry_after_header=True,
    )))
    errors: list[str] = []
    for template in URLS:
        url = template.format(year=int(year))
        try:
            response = session.get(url, timeout=timeout)
            if response.status_code == 404:
                continue
            response.raise_for_status()
            content = response.content
            problema = _problema_do_zip(content)
            if problema:
                errors.append(f"{url}: {problema}")
                continue
            cache.parent.mkdir(parents=True, exist_ok=True)
            provisorio = cache.with_suffix(".tmp")
            provisorio.write_bytes(content)
            provisorio.replace(cache)
            headers = {key: str(value) for key, value in response.headers.items()
                       if key.lower() in {"etag", "last-modified", "content-length"}}
            return content, url, headers
        except requests.RequestException as exc:
            errors.append(f"{url}: {exc}")
    if cache.exists():
        return cache.read_bytes(), URLS[0].format(year=int(year)), {
            "cache-fallback": "true",
            "cache-fallback-motivo": "; ".join(errors)[:400] or "404 nas URLs"}
    raise RuntimeError("; ".join(errors) or f"COTAHIST {year} indisponível")


def parse_cotahist(content: bytes) -> list[dict]:
    """Cotas de FII (BDI 12, mercado a vista) do COTAHIST.

    A leitura do layout mora em :mod:`data_pipeline.market.b3_cotahist`, que le
    o arquivo inteiro e recebe o filtro por parametro. Aqui so se diz qual
    filtro. As chaves devolvidas sao as mesmas de antes da separacao -- `bdi` e
    `fator_cotacao`, que o leitor generico traz, nao entram porque
    `market.fii_b3_security_history` nao tem essas colunas e carregar campo
    morto em 606 mil linhas de payload JSON nao e de graca.
    """
    linhas = b3_cotahist.ler_linhas(content, bdi_codes=(b3_cotahist.BDI_FII,))
    return [{chave: valor for chave, valor in linha.items()
             if chave not in ("bdi", "fator_cotacao")}
            for linha in linhas]


def _parser_hash() -> str:
    """Impressao do codigo que realmente le o arquivo.

    O layout de 245 colunas saiu deste modulo para `b3_cotahist`. Continuar
    hasheando so este arquivo registraria em `market.fii_parser_versions` uma
    impressao que nao cobre o parser: mudar uma posicao de coluna la deixaria
    o `code_sha256` daqui intacto, e a procedencia diria que nada mudou.
    """
    partes = b"".join(Path(arquivo).read_bytes()
                      for arquivo in (__file__, b3_cotahist.__file__))
    return hashlib.sha256(partes).hexdigest()


def _register_parser(conn) -> None:
    conn.execute(text("""
        INSERT INTO market.fii_parser_versions (
            parser_name,parser_version,schema_version,code_sha256,status,activated_at
        ) VALUES (:name,:version,:schema,:sha,'active',now())
        ON CONFLICT (parser_name,parser_version) DO UPDATE SET
            schema_version=EXCLUDED.schema_version,code_sha256=EXCLUDED.code_sha256,
            status='active',activated_at=now()
    """), {"name": PARSER_NAME, "version": PARSER_VERSION,
             "schema": PARSER_SCHEMA_VERSION, "sha": _parser_hash()})


def _ano_fechado_carregado(engine, year: int) -> bool:
    """O ZIP de um ano só muda até o último pregão dele.

    Uma carga concluída a partir de 02/01 do ano seguinte já leu o arquivo
    definitivo; baixá-lo de novo (~90 MB) só para achar o mesmo sha é
    desperdício. É o que deixa a rotina diária pedir dois anos -- e assim
    pegar os pregões finais de dezembro na virada -- sem pagar o download do
    ano anterior todo dia.
    """
    with engine.connect() as conn:
        return bool(conn.execute(text("""
            SELECT EXISTS (
                SELECT 1 FROM market.fii_b3_archive_loads
                WHERE archive_year=:year AND status='completed'
                  AND parser_name=:parser AND parser_version=:version
                  AND completed_at >= make_date(:year + 1, 1, 2)
            )
        """), {"year": year, "parser": PARSER_NAME,
                 "version": PARSER_VERSION}).scalar())


def ingest_b3_history(*, years: int = 10) -> dict:
    engine = get_pipeline_engine()
    if engine is None:
        return {"status": "failed", "errors": ["banco indisponível"]}
    current = datetime.now(timezone.utc).year
    report = {"status": "completed", "archives": 0, "skipped": 0,
              "rows": 0, "tickers": set(), "errors": []}
    with engine.begin() as conn:
        columns = {row[0] for row in conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='market' AND table_name='fii_b3_archive_loads'
        """))}
        if not {"parser_name", "parser_version"}.issubset(columns):
            return {**report, "status": "failed",
                    "errors": ["migration 037 pendente"], "tickers": 0}
        _register_parser(conn)
    for year in range(current - max(int(years), 1) + 1, current + 1):
        sha: str | None = None
        try:
            if year < current and _ano_fechado_carregado(engine, year):
                report["archives"] += 1
                report["skipped"] += 1
                _LOGGER.info("COTAHIST %s — ano fechado já carregado, sem download", year)
                continue
            content, url, headers = fetch_year(year)
            if headers.get("cache-fallback") and year == current:
                # O ano corrente em cache é o do último download que deu certo:
                # carregá-lo em silêncio marcaria a rotina como em dia com a
                # fita parada. Carrega assim mesmo, mas o relatório sai parcial.
                report["errors"].append({"year": year, "error": (
                    "download do COTAHIST falhou; carregado o ZIP em cache, "
                    "que pode estar velho: "
                    + headers.get("cache-fallback-motivo", "motivo não informado"))})
            rows = parse_cotahist(content)
            if not rows:
                # Todo ano do COTAHIST tem FII desde 2010; zero linhas é
                # arquivo errado, não ano vazio. Registrar 'completed' faria o
                # carimbo da rotina dizer que a fita está em dia.
                raise RuntimeError(f"COTAHIST {year} sem nenhuma linha de FII")
            collected = datetime.now(timezone.utc)
            sha = hashlib.sha256(content).hexdigest()
            with engine.connect() as conn:
                completed = conn.execute(text("""
                    SELECT status='completed'
                    FROM market.fii_b3_archive_loads
                    WHERE archive_year=:year AND archive_sha256=:sha
                      AND parser_name=:parser AND parser_version=:version
                """), {"year": year, "sha": sha, "parser": PARSER_NAME,
                         "version": PARSER_VERSION}).scalar()
            if completed:
                report["archives"] += 1
                report["skipped"] += 1
                report["rows"] += len(rows)
                report["tickers"].update(row["ticker"] for row in rows)
                _LOGGER.info("COTAHIST %s — arquivo já concluído, ignorado", year)
                continue
            with engine.begin() as conn:
                raw_id = save_raw_payload(
                    conn, None, "b3/cotahist", {"year": year, "sha256": sha, "rows": len(rows)},
                    request_params={"year": year}, response_headers=headers,
                    collected_at=collected, source=SOURCE,
                    request_fingerprint=hashlib.sha256(url.encode()).hexdigest(),
                )
                conn.execute(text("""
                    INSERT INTO market.fii_b3_archive_loads (
                        archive_year,archive_sha256,source_url,expected_rows,loaded_rows,
                        status,raw_payload_id,started_at,updated_at,error_message,
                        parser_name,parser_version
                    ) VALUES (:year,:sha,:url,:expected,0,'running',:raw,now(),now(),NULL,
                              :parser,:version)
                    ON CONFLICT (archive_year,archive_sha256,parser_name,parser_version)
                    DO UPDATE SET
                        source_url=EXCLUDED.source_url,
                        expected_rows=EXCLUDED.expected_rows,
                        status='running',raw_payload_id=EXCLUDED.raw_payload_id,
                        updated_at=now(),error_message=NULL
                """), {"year": year, "sha": sha, "url": url,
                        "expected": sum(1 for row in rows
                                        if row.get("close") and row["close"] > 0),
                        "raw": raw_id, "parser": PARSER_NAME,
                        "version": PARSER_VERSION})
            payload = [{**row, "source_url": url, "raw_payload_id": raw_id,
                        "collected_at": collected.isoformat(), "archive_sha256": sha}
                       for row in rows if row.get("close") and row["close"] > 0]
            for offset in range(0, len(payload), _BATCH_SIZE):
                batch = payload[offset:offset + _BATCH_SIZE]
                with engine.begin() as conn:
                    # O ZIP do ano corrente muda a cada pregão, e o sha entra na
                    # chave única: sem esta troca, cada recarga duplicaria o ano
                    # inteiro e todo leitor por (ticker, trade_date) somaria em
                    # dobro. Mesma transação do INSERT, para não haver janela
                    # com o pregão faltando ou repetido.
                    conn.execute(text("""
                        DELETE FROM market.fii_b3_security_history h
                        USING jsonb_to_recordset(CAST(:rows AS jsonb))
                              AS x(ticker text, trade_date date)
                        WHERE h.ticker = x.ticker AND h.trade_date = x.trade_date
                          AND h.archive_sha256 <> :sha
                    """), {"rows": json.dumps(batch, ensure_ascii=False), "sha": sha})
                    conn.execute(text("""
                        INSERT INTO market.fii_b3_security_history (
                            ticker,trade_date,issuer_short_name,specification,isin,open,high,low,
                            average,close,trades,quantity,financial_volume,source,source_url,
                            raw_payload_id,collected_at,archive_sha256
                        ) SELECT ticker,trade_date,issuer_short_name,specification,isin,open,high,low,
                            average,close,trades,quantity,financial_volume,:source,source_url,
                            raw_payload_id,collected_at,archive_sha256
                        FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS x(
                            ticker text,trade_date date,issuer_short_name text,specification text,
                            isin text,open numeric,high numeric,low numeric,average numeric,
                            close numeric,trades integer,quantity bigint,financial_volume numeric,
                            source_url text,raw_payload_id bigint,collected_at timestamptz,
                            archive_sha256 text
                        ) ON CONFLICT (ticker,trade_date,archive_sha256) DO NOTHING
                    """), {"source": SOURCE, "rows": json.dumps(batch, ensure_ascii=False)})
                _LOGGER.info("COTAHIST %s — %s/%s linhas persistidas",
                             year, min(offset + len(batch), len(payload)), len(payload))
                with engine.begin() as conn:
                    conn.execute(text("""
                        UPDATE market.fii_b3_archive_loads
                        SET loaded_rows=:loaded,updated_at=now()
                        WHERE archive_year=:year AND archive_sha256=:sha
                          AND parser_name=:parser AND parser_version=:version
                    """), {"loaded": min(offset + len(batch), len(payload)),
                            "year": year, "sha": sha, "parser": PARSER_NAME,
                            "version": PARSER_VERSION})
            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE market.fii_b3_archive_loads
                    SET loaded_rows=:loaded,status='completed',updated_at=now(),completed_at=now()
                    WHERE archive_year=:year AND archive_sha256=:sha
                      AND parser_name=:parser AND parser_version=:version
                """), {"loaded": len(payload), "year": year, "sha": sha,
                         "parser": PARSER_NAME, "version": PARSER_VERSION})
            report["archives"] += 1
            report["rows"] += len(rows)
            report["tickers"].update(row["ticker"] for row in rows)
        except Exception as exc:
            _LOGGER.exception("Falha no COTAHIST %s", year)
            if sha:
                try:
                    with engine.begin() as conn:
                        conn.execute(text("""
                            UPDATE market.fii_b3_archive_loads
                            SET status='failed',updated_at=now(),error_message=:error
                            WHERE archive_year=:year AND archive_sha256=:sha
                              AND parser_name=:parser AND parser_version=:version
                        """), {"year": year, "sha": sha,
                                 "parser": PARSER_NAME, "version": PARSER_VERSION,
                                 "error": str(exc)[:500]})
                except Exception:
                    pass
            report["errors"].append({"year": year, "error": str(exc)[:500]})
    report["tickers"] = len(report["tickers"])
    if report["archives"] == 0:
        report["status"] = "failed"
    elif report["errors"]:
        report["status"] = "partial"
    return report
