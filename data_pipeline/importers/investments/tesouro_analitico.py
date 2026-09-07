"""Importador do Extrato Analítico do Tesouro Direto (.xlsx).

Diferença para o Extrato Consolidado
------------------------------------
O Consolidado é uma fotografia da posição: título, quantidade, valor. Ele
alimenta ``portfolio_position_snapshots`` e não sustenta marcação a mercado,
porque não diz a que taxa cada compra foi feita.

O Analítico é **um arquivo por título**, com uma linha por aplicação: data,
quantidade, PU de compra, taxa contratada, dias corridos, IR, IOF, taxas e
valor de resgate. É a granularidade que faltava — e por isso ele grava em
tabela própria (``tesouro_lots``), não em snapshot: cada linha é um lote com
identidade e taxa próprias, e agregá-las destruiria exatamente o dado que o
arquivo trouxe.

Este importador não cria compra nem venda em ``investment_transactions``: a B3
segue canônica para operações. Ele descreve a composição de uma posição que já
existe.
"""
from __future__ import annotations

import io
import logging
import re
import unicodedata
from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from core.config import settings

from .common import (
    finalize_summary,
    get_or_create_asset,
    make_external_id,
    make_summary,
    safe_error,
)
from .tesouro_direto import tesouro_security_key

logger = logging.getLogger(__name__)

SOURCE = "tesouro_analitico"
PREFIX = "tdanl"
MAX_FILE_BYTES = 5 * 1024 * 1024

_RE_VENCIMENTO = re.compile(r"VENCIMENTO:\s*(\d{2}/\d{2}/\d{4})")
_RE_TITULO = re.compile(r"EXTRATO\s+ANAL[IÍ]TICO\s*[-–—]\s*(.+)", re.IGNORECASE)
_RE_GERADO = re.compile(r"gerado em\s*(\d{2}/\d{2}/\d{4})", re.IGNORECASE)
_RE_CUSTODIA = re.compile(r"AGENTE DE CUST[OÓ]DIA:\s*(.+)", re.IGNORECASE)
_RE_DATA = re.compile(r"^\d{2}/\d{2}/\d{4}$")


def _txt(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    return unicodedata.normalize("NFKD", _txt(value)).encode("ascii", "ignore").decode().upper()


def _num(value: Any) -> float | None:
    """Número em formato BR. Devolve None para vazio — nunca zero."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    raw = _txt(value).replace("R$", "").replace("%", "").replace(" ", "")
    if not raw or raw == "-":
        return None
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def _data(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = _txt(value)
    if not _RE_DATA.match(raw):
        return None
    try:
        return datetime.strptime(raw, "%d/%m/%Y").date()
    except ValueError:
        return None


def parse_cabecalho(rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    """Extrai título, vencimento, data de geração e custodiante do topo."""
    info: dict[str, Any] = {
        "titulo": None, "vencimento": None, "gerado_em": None, "custodiante": None,
    }
    for row in rows[:12]:
        for cell in row:
            texto = _txt(cell)
            if not texto:
                continue
            if info["titulo"] is None:
                match = _RE_TITULO.search(texto)
                if match:
                    info["titulo"] = match.group(1).strip()
            if info["vencimento"] is None:
                match = _RE_VENCIMENTO.search(_norm(texto))
                if match:
                    info["vencimento"] = _data(match.group(1))
            if info["custodiante"] is None:
                match = _RE_CUSTODIA.search(texto)
                if match:
                    info["custodiante"] = match.group(1).strip()
    for row in rows:
        for cell in row:
            match = _RE_GERADO.search(_txt(cell))
            if match:
                info["gerado_em"] = _data(match.group(1))
                return info
    return info


# Posição das colunas no layout oficial. A leitura é posicional porque o
# cabeçalho vem quebrado em duas linhas com quebra de linha dentro da célula —
# casar por texto seria mais frágil, não menos.
_COL = {
    "data_aplicacao": 0, "quantidade": 1, "preco_aplicacao": 2, "valor_investido": 3,
    "taxa_contratada": 4, "rent_anualizada": 5, "rent_acumulada": 6, "valor_bruto": 7,
    "dias_corridos": 8, "aliquota_ir": 9, "ir": 10, "iof": 11,
    "taxa_b3": 12, "taxa_instituicao": 13, "valor_liquido": 14,
}


def parse_lotes(rows: list[tuple[Any, ...]]) -> tuple[list[dict], int]:
    """Lê as linhas de aplicação. A linha 'Total' é descartada, não somada.

    Uma linha sem data válida, sem quantidade positiva ou sem valor investido
    positivo não vira lote: sem esses três não existe aplicação para marcar.
    """
    lotes: list[dict] = []
    ignoradas = 0
    for row in rows:
        if not row:
            continue
        primeira = _norm(row[_COL["data_aplicacao"]] if len(row) > 0 else None)
        if primeira.startswith("TOTAL"):
            continue
        data_aplicacao = _data(row[_COL["data_aplicacao"]])
        if data_aplicacao is None:
            continue
        def col(nome: str) -> Any:
            idx = _COL[nome]
            return row[idx] if len(row) > idx else None

        quantidade = _num(col("quantidade"))
        preco = _num(col("preco_aplicacao"))
        investido = _num(col("valor_investido"))
        if not quantidade or quantidade <= 0 or not investido or investido <= 0:
            ignoradas += 1
            continue
        # A quantidade sai do extrato com 2 casas; o valor investido e o PU vêm
        # inteiros. Recompor a quantidade pela divisão preserva a precisão que o
        # arredondamento do arquivo perdeu, e a diferença aparece no PU do lote.
        if preco and preco > 0:
            quantidade = investido / preco
        lotes.append({
            "data_aplicacao": data_aplicacao,
            "quantidade": quantidade,
            "preco_aplicacao": preco,
            "valor_investido": investido,
            "taxa_contratada": _txt(col("taxa_contratada")) or None,
            "rentabilidade_acumulada": _num(col("rent_acumulada")),
            "valor_bruto": _num(col("valor_bruto")),
            "dias_corridos": int(_num(col("dias_corridos")) or 0) or None,
            "aliquota_ir": _num(col("aliquota_ir")),
            "ir": _num(col("ir")),
            "iof": _num(col("iof")),
            "taxa_b3": _num(col("taxa_b3")),
            "taxa_instituicao": _num(col("taxa_instituicao")),
            "valor_liquido": _num(col("valor_liquido")),
        })
    return lotes, ignoradas


def parse_arquivo(file_bytes: bytes) -> tuple[dict[str, Any], list[dict], int]:
    """Parse puro de um Extrato Analítico: cabeçalho + lotes. Sem banco."""
    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
    sheet = None
    for nome in workbook.sheetnames:
        if "ANALITICO" in _norm(nome):
            sheet = workbook[nome]
            break
    if sheet is None:
        sheet = workbook[workbook.sheetnames[0]]
    rows = list(sheet.iter_rows(values_only=True))
    cabecalho = parse_cabecalho(rows)
    if not cabecalho.get("titulo") or not cabecalho.get("vencimento"):
        raise ValueError("cabecalho sem titulo ou vencimento — nao e um Extrato Analitico")
    lotes, ignoradas = parse_lotes(rows)
    return cabecalho, lotes, ignoradas


# ─────────────────────────────────────────────────────────────────────────────
# Persistência
# ─────────────────────────────────────────────────────────────────────────────

DDL_TESOURO_LOTS = """
CREATE TABLE IF NOT EXISTS tesouro_lots (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id            UUID NOT NULL,
    asset_id           UUID,
    security_key       VARCHAR(32) NOT NULL,
    title_name         VARCHAR(120) NOT NULL,
    maturity_date      DATE NOT NULL,
    custodian          VARCHAR(120),
    report_date        DATE NOT NULL,
    application_date   DATE NOT NULL,
    quantity           NUMERIC(20, 8) NOT NULL,
    unit_price         NUMERIC(20, 6),
    invested_value     NUMERIC(20, 2) NOT NULL,
    contracted_rate    VARCHAR(40),
    accrued_return_pct NUMERIC(12, 4),
    gross_value        NUMERIC(20, 2),
    elapsed_days       INTEGER,
    ir_rate            NUMERIC(6, 3),
    ir_amount          NUMERIC(20, 2),
    iof_amount         NUMERIC(20, 2),
    fee_b3             NUMERIC(20, 2),
    fee_institution    NUMERIC(20, 2),
    net_value          NUMERIC(20, 2),
    external_id        VARCHAR(64) NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

_INDICES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_tesouro_lots_external_id ON tesouro_lots(external_id)",
    "CREATE INDEX IF NOT EXISTS ix_tesouro_lots_user_key ON tesouro_lots(user_id, security_key, report_date)",
)


def ensure_schema(engine: Engine) -> None:
    """Cria a tabela e os índices. Idempotente — roda em toda importação."""
    with engine.begin() as conn:
        conn.execute(text(DDL_TESOURO_LOTS))
        for stmt in _INDICES:
            conn.execute(text(stmt))


def _external_id(security_key: str, report_date: date, lote: dict) -> str:
    return make_external_id(PREFIX, [
        security_key, report_date.isoformat(), lote["data_aplicacao"].isoformat(),
        f"{lote['valor_investido']:.2f}", f"{lote.get('preco_aplicacao') or 0:.6f}",
    ])


def _existe(conn: Connection, external_id: str) -> bool:
    return conn.execute(
        text("SELECT 1 FROM tesouro_lots WHERE external_id = :ext LIMIT 1"),
        {"ext": external_id},
    ).fetchone() is not None


def _gravar(
    conn: Connection, user_id: str, asset_id: str | None,
    cabecalho: dict, security_key: str, report_date: date, lote: dict, external_id: str,
) -> None:
    conn.execute(text("""
        INSERT INTO tesouro_lots (
            user_id, asset_id, security_key, title_name, maturity_date, custodian,
            report_date, application_date, quantity, unit_price, invested_value,
            contracted_rate, accrued_return_pct, gross_value, elapsed_days,
            ir_rate, ir_amount, iof_amount, fee_b3, fee_institution, net_value, external_id
        ) VALUES (
            :user_id, :asset_id, :security_key, :title_name, :maturity_date, :custodian,
            :report_date, :application_date, :quantity, :unit_price, :invested_value,
            :contracted_rate, :accrued_return_pct, :gross_value, :elapsed_days,
            :ir_rate, :ir_amount, :iof_amount, :fee_b3, :fee_institution, :net_value, :external_id
        )
    """), {
        "user_id": user_id, "asset_id": asset_id, "security_key": security_key,
        "title_name": cabecalho["titulo"][:120], "maturity_date": cabecalho["vencimento"],
        "custodian": (cabecalho.get("custodiante") or "")[:120] or None,
        "report_date": report_date, "application_date": lote["data_aplicacao"],
        "quantity": lote["quantidade"], "unit_price": lote.get("preco_aplicacao"),
        "invested_value": lote["valor_investido"], "contracted_rate": lote.get("taxa_contratada"),
        "accrued_return_pct": lote.get("rentabilidade_acumulada"), "gross_value": lote.get("valor_bruto"),
        "elapsed_days": lote.get("dias_corridos"), "ir_rate": lote.get("aliquota_ir"),
        "ir_amount": lote.get("ir"), "iof_amount": lote.get("iof"), "fee_b3": lote.get("taxa_b3"),
        "fee_institution": lote.get("taxa_instituicao"), "net_value": lote.get("valor_liquido"),
        "external_id": external_id,
    })


def _normalizar_entrada(files: Any) -> list[tuple[str, bytes]]:
    if isinstance(files, (bytes, bytearray)):
        return [("extrato.xlsx", bytes(files))]
    if isinstance(files, tuple) and len(files) == 2:
        return [(str(files[0]), bytes(files[1]))]
    if isinstance(files, list):
        saida = []
        for item in files:
            if isinstance(item, tuple) and len(item) == 2:
                saida.append((str(item[0]), bytes(item[1])))
            elif isinstance(item, (bytes, bytearray)):
                saida.append(("extrato.xlsx", bytes(item)))
        return saida
    return []


def parse(files: Any, engine: Engine) -> dict[str, Any]:
    """Importa um ou vários Extratos Analíticos — um arquivo por título."""
    summary = make_summary(SOURCE)
    user_id = settings.OWNER_USER_ID
    if not user_id:
        summary["errors"].append("OWNER_USER_ID nao configurado.")
        return finalize_summary(summary)

    entradas = _normalizar_entrada(files)
    if not entradas:
        summary["errors"].append("Nenhum arquivo recebido.")
        return finalize_summary(summary)

    try:
        ensure_schema(engine)
    except Exception as exc:  # noqa: BLE001
        summary["errors"].append(f"Nao foi possivel preparar tesouro_lots: {safe_error(exc)}")
        return finalize_summary(summary)

    for nome, conteudo in entradas:
        rotulo = nome[:60]
        if not conteudo:
            summary["errors"].append(f"{rotulo}: arquivo vazio.")
            continue
        if len(conteudo) > MAX_FILE_BYTES:
            summary["errors"].append(f"{rotulo}: excede o limite de 5 MB.")
            continue
        try:
            cabecalho, lotes, ignoradas = parse_arquivo(conteudo)
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(f"{rotulo}: arquivo invalido ({safe_error(exc)}).")
            continue

        summary["rows_skipped"] += ignoradas
        if not lotes:
            summary["files_skipped"] += 1
            summary["files_skipped_notes"].append(f"{rotulo}: nenhuma aplicacao encontrada.")
            continue

        report_date = cabecalho.get("gerado_em") or date.today()
        security_key = tesouro_security_key(cabecalho["titulo"], cabecalho["vencimento"])
        try:
            with engine.connect() as conn, conn.begin():
                asset_id = get_or_create_asset(
                    conn, ticker=security_key, name=cabecalho["titulo"][:120],
                    asset_class="fixed_income", currency="BRL",
                )
                for lote in lotes:
                    ext = _external_id(security_key, report_date, lote)
                    if _existe(conn, ext):
                        summary["duplicates_skipped"] += 1
                        continue
                    _gravar(conn, user_id, asset_id, cabecalho, security_key, report_date, lote, ext)
                    summary["positions_imported"] += 1
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(f"{rotulo}: falha ao gravar ({safe_error(exc)}).")

    logger.info(
        "tesouro_analitico: %s lotes, %s duplicados, %s erros",
        summary["positions_imported"], summary["duplicates_skipped"], len(summary["errors"]),
    )
    return finalize_summary(summary)
