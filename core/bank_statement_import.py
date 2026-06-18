"""
Importacao de extratos bancarios em PDF para o Controle Financeiro.

Fluxo:
- extrai movimentos de PDF C6 Bank;
- classifica usando apenas categorias existentes;
- grava uma tabela intermediaria de auditoria/revisao;
- publica em `transactions` somente movimentos classificados.
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
import threading
import unicodedata
from datetime import date, datetime
from typing import Any

from sqlalchemy import text

from core.config import settings
from core.database import get_engine

logger = logging.getLogger(__name__)

BANK_STATEMENT_SOURCE = "import"
BANK_STATEMENT_KIND = "bank_statement_pdf"
SUPPORTED_BANKS = ("C6 Bank",)
VALOR_ALTO_FINANCIAMENTO = 1000.0
_BANK_STATEMENT_ACCOUNT_TYPES = ("checking", "savings", "digital_wallet")

# Flag de depuracao do importador de extrato. Quando True, registra no logger
# (nivel INFO) diagnosticos da extracao/parsing. Nunca expoe dados sensiveis
# de forma permanente em producao.
DEBUG_IMPORT_EXTRATO = False

# Datas: aceita dd/mm/yyyy, dd/mm/yy e dd/mm (ano inferido do extrato).
_DATE_FULL_RE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")
_DATE_ANY_RE = re.compile(r"\b(\d{2}/\d{2}(?:/\d{2,4})?)\b")
# Compatibilidade: codigo legado usa _DATE_RE (datas completas).
_DATE_RE = _DATE_FULL_RE
# Valores BRL: opcional R$, sinal, milhar com ponto, decimal com virgula.
_MONEY_RE = re.compile(
    r"(?:[-+]\s*)?(?:R\$\s*)?(?:[-+]\s*)?"
    r"(?:\d{1,3}(?:\.\d{3})*,\d{2}|\d+(?:[,.]\d{2}))"
)
# Indicador de debito/credito ao lado do valor (ex.: "1.200,00 D" / "500,00 C").
_DC_FLAG_RE = re.compile(r"(\d[\d.,]*\d|\d)\s*([DC])\b")

DDL_SQL = [
    """
    CREATE TABLE IF NOT EXISTS bank_statement_movements (
        id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id                    UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        account_id                 UUID REFERENCES accounts(id) ON DELETE SET NULL,
        transaction_id             UUID REFERENCES transactions(id) ON DELETE SET NULL,
        banco                      TEXT NOT NULL,
        conta                      TEXT,
        data_movimento             DATE NOT NULL,
        data_lancamento            DATE,
        tipo_original_banco        TEXT,
        descricao_original         TEXT NOT NULL,
        descricao_normalizada      TEXT NOT NULL,
        valor                      NUMERIC(15,2) NOT NULL,
        direcao                    TEXT NOT NULL,
        categoria_id               UUID REFERENCES categories(id) ON DELETE SET NULL,
        subcategoria_id            UUID,
        categoria_sugerida_texto   TEXT,
        subcategoria_sugerida_texto TEXT,
        categoria_confirmada_id    UUID REFERENCES categories(id) ON DELETE SET NULL,
        subcategoria_confirmada_id UUID,
        confianca_classificacao    NUMERIC(5,2),
        status_classificacao       TEXT NOT NULL DEFAULT 'pendente',
        origem_arquivo             TEXT,
        hash_lancamento            TEXT NOT NULL,
        created_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (user_id, hash_lancamento)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bank_statement_classification_rules (
        id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id               UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        banco                 TEXT,
        tipo_original_banco   TEXT,
        palavra_chave         TEXT NOT NULL,
        descricao_normalizada TEXT,
        category_id           UUID NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
        subcategoria_id       UUID,
        created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bank_statement_movements_user_date
    ON bank_statement_movements (user_id, data_movimento DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bank_statement_movements_user_status
    ON bank_statement_movements (user_id, status_classificacao)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bank_statement_rules_user_bank
    ON bank_statement_classification_rules (user_id, banco)
    """,
    """
    DO $$ BEGIN
        IF NOT (
            SELECT relrowsecurity FROM pg_class
            WHERE relname = 'bank_statement_movements'
              AND relnamespace = 'public'::regnamespace
        ) THEN
            ALTER TABLE bank_statement_movements ENABLE ROW LEVEL SECURITY;
        END IF;
    END; $$
    """,
    """
    DO $$ BEGIN
        IF NOT (
            SELECT relrowsecurity FROM pg_class
            WHERE relname = 'bank_statement_classification_rules'
              AND relnamespace = 'public'::regnamespace
        ) THEN
            ALTER TABLE bank_statement_classification_rules ENABLE ROW LEVEL SECURITY;
        END IF;
    END; $$
    """,
    """
    DO $$ BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE schemaname='public'
              AND tablename='bank_statement_movements'
              AND policyname='bank_statement_movements_owner_all'
        ) THEN
            CREATE POLICY bank_statement_movements_owner_all ON bank_statement_movements
                USING (user_id = auth.uid())
                WITH CHECK (user_id = auth.uid());
        END IF;
    END; $$
    """,
    """
    DO $$ BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE schemaname='public'
              AND tablename='bank_statement_classification_rules'
              AND policyname='bank_statement_rules_owner_all'
        ) THEN
            CREATE POLICY bank_statement_rules_owner_all ON bank_statement_classification_rules
                USING (user_id = auth.uid())
                WITH CHECK (user_id = auth.uid());
        END IF;
    END; $$
    """,
]


def _norm(value: object) -> str:
    text_value = unicodedata.normalize("NFKD", str(value or ""))
    text_value = text_value.encode("ascii", "ignore").decode("ascii")
    text_value = re.sub(r"[^a-zA-Z0-9]+", " ", text_value).casefold().strip()
    return " ".join(text_value.split())


def _parse_date(value: str, ano_referencia: int | None = None) -> date | None:
    raw = str(value or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except (TypeError, ValueError):
            continue
    # Data sem ano (dd/mm): usa o ano de referencia do extrato (ou o atual).
    m = re.fullmatch(r"(\d{2})/(\d{2})", raw)
    if m:
        try:
            ano = ano_referencia or date.today().year
            return date(ano, int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def _infer_statement_year(text_value: str) -> int | None:
    """Ano predominante de datas completas no extrato (para datas dd/mm)."""
    anos = [int(m.split("/")[-1]) for m in _DATE_FULL_RE.findall(text_value or "")]
    if not anos:
        return None
    return max(set(anos), key=anos.count)


def _parse_money(value: object) -> float:
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    negative = "-" in raw
    clean = (
        raw.replace("R$", "")
        .replace("+", "")
        .replace("-", "")
        .replace(" ", "")
        .strip()
    )
    if "," in clean and "." in clean:
        clean = clean.replace(".", "").replace(",", ".")
    elif "," in clean:
        clean = clean.replace(",", ".")
    try:
        amount = float(clean)
    except ValueError:
        return 0.0
    return -amount if negative else amount


def _fmt_hash_amount(value: float) -> str:
    return f"{float(value or 0):.2f}"


def build_bank_statement_hash(row: dict) -> str:
    """Hash idempotente conforme banco, data, valor, tipo e descricao normalizada."""
    raw = "|".join(
        [
            _norm(row.get("banco") or "C6 Bank"),
            str(row.get("data_movimento") or ""),
            _fmt_hash_amount(float(row.get("valor") or 0.0)),
            _norm(row.get("tipo_original_banco") or ""),
            row.get("descricao_normalizada") or _norm(row.get("descricao_original")),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _infer_bank_type(description: str, amount: float) -> str:
    norm = _norm(description)
    known = [
        ("entrada pix", "Entrada PIX"),
        ("saida pix", "Saida PIX"),
        ("pix enviado", "Saida PIX"),
        ("pix recebido", "Entrada PIX"),
        ("debito de cartao", "Debito de Cartao"),
        ("utilidade de cartao", "Utilidade de Cartao"),
        ("outros gastos", "Outros gastos"),
        ("resgate de cdb", "Resgate de CDB"),
        ("boleto", "Boleto"),
        ("pagamento", "Pagamento"),
        ("entradas", "Entradas"),
    ]
    for key, label in known:
        if key in norm:
            return label
    return "Entrada" if amount >= 0 else "Saida"


def _direction_for(bank_type: str, description: str, amount: float) -> str:
    norm = _norm(f"{bank_type} {description}")
    if "resgate de cdb" in norm:
        return "entrada"
    if amount < 0 or any(
        term in norm
        for term in (
            "saida pix",
            "pix enviado",
            "pagamento",
            "boleto",
            "debito de cartao",
            "outros gastos",
            "utilidade de cartao",
        )
    ):
        return "saida"
    return "entrada"


def _normalize_signed_amount(bank_type: str, description: str, raw_amount: float) -> float:
    direction = _direction_for(bank_type, description, raw_amount)
    amount = abs(float(raw_amount or 0.0))
    return amount if direction == "entrada" else -amount


def _clean_statement_description(line: str, amount_span: tuple[int, int]) -> str:
    left = line[: amount_span[0]]
    right = line[amount_span[1] :]
    text_value = f"{left} {right}"
    text_value = _DATE_ANY_RE.sub(" ", text_value)
    # Remove indicador D/C isolado e "R$" residuais.
    text_value = re.sub(r"\b([DC])\b", " ", text_value)
    text_value = text_value.replace("R$", " ")
    return " ".join(text_value.replace("|", " ").split())


# Linhas que nunca sao movimentacao (cabecalho, saldo, rodape).
_IGNORE_LINE_TERMS = (
    "saldo do dia", "saldo anterior", "saldo inicial", "saldo final",
    "saldo em conta", "saldo disponivel", "extrato", "periodo",
    "data lancamento", "data descricao", "documento valor",
    "agencia conta", "central de atendimento", "ouvidoria",
    "pagina", "demonstrativo", "total de creditos", "total de debitos",
)


def _is_ignorable_line(clean: str) -> bool:
    norm = _norm(clean)
    # Cabecalho de resumo do mes (ex.: "Janeiro 2025 (...) Entradas: R$ ...
    # Saidas: R$ ..."): traz "entradas" e "saidas" juntos, o que nunca ocorre
    # numa linha de movimentacao real. Ignora para nao virar transacao falsa.
    if "entradas" in norm and "saidas" in norm:
        return True
    return any(term in norm for term in _IGNORE_LINE_TERMS)


def _parse_c6_line(
    line: str,
    banco: str,
    origem_arquivo: str | None,
    ano_referencia: int | None = None,
    reject_reasons: dict | None = None,
) -> dict | None:
    def _reject(reason: str) -> None:
        if reject_reasons is not None:
            reject_reasons[reason] = reject_reasons.get(reason, 0) + 1

    clean = " ".join(str(line or "").split())
    if not clean:
        return None
    if _is_ignorable_line(clean):
        _reject("linha de cabecalho/saldo/rodape")
        return None
    if not _DATE_ANY_RE.search(clean):
        _reject("sem data reconhecida")
        return None

    dates = [_parse_date(value, ano_referencia) for value in _DATE_ANY_RE.findall(clean)]
    dates = [value for value in dates if value is not None]
    if not dates:
        _reject("data invalida")
        return None

    money_matches = list(_MONEY_RE.finditer(clean))
    if not money_matches:
        _reject("sem valor monetario")
        return None
    amount_match = money_matches[-1]
    raw_amount = _parse_money(amount_match.group(0))
    if raw_amount == 0 and "0,00" not in amount_match.group(0):
        _reject("valor zero/invalido")
        return None

    # Indicador D/C do C6 (ex.: "1.200,00 D") sobrepoe o sinal textual.
    dc_match = _DC_FLAG_RE.search(clean[amount_match.start():])
    dc_flag = dc_match.group(2).upper() if dc_match else None

    description = _clean_statement_description(clean, amount_match.span())
    if len(_norm(description)) < 3:
        _reject("descricao muito curta")
        return None

    bank_type = _infer_bank_type(description, raw_amount)
    if dc_flag == "D":
        amount = -abs(raw_amount)
        direction = "saida"
    elif dc_flag == "C":
        amount = abs(raw_amount)
        direction = "entrada"
    else:
        amount = _normalize_signed_amount(bank_type, description, raw_amount)
        direction = _direction_for(bank_type, description, amount)

    row = {
        "banco": banco,
        "conta": None,
        "data_movimento": dates[0],
        "data_lancamento": dates[1] if len(dates) > 1 else None,
        "tipo_original_banco": bank_type,
        "descricao_original": description[:500],
        "descricao_normalizada": _norm(description),
        "valor": round(amount, 2),
        "direcao": direction,
        "categoria_id": None,
        "subcategoria_id": None,
        "categoria_sugerida_texto": None,
        "subcategoria_sugerida_texto": None,
        "status_classificacao": "pendente",
        "confianca_classificacao": 0.0,
        "origem_arquivo": origem_arquivo,
    }
    row["hash_lancamento"] = build_bank_statement_hash(row)
    return row


def _merge_wrapped_lines(text_value: str) -> list[str]:
    lines = [" ".join(line.split()) for line in str(text_value or "").splitlines()]
    lines = [line for line in lines if line]
    merged: list[str] = []
    buffer = ""
    for line in lines:
        starts_with_date = bool(_DATE_ANY_RE.search(line))
        has_money = bool(_MONEY_RE.search(line))
        if starts_with_date and has_money:
            if buffer:
                merged.append(buffer)
                buffer = ""
            merged.append(line)
        elif starts_with_date:
            if buffer:
                merged.append(buffer)
            buffer = line
        elif buffer:
            buffer = f"{buffer} {line}"
            if _MONEY_RE.search(buffer):
                merged.append(buffer)
                buffer = ""
    if buffer:
        merged.append(buffer)
    return merged


def parse_c6_bank_text(text_value: str, file_name: str | None = None) -> dict:
    """Extrai movimentos de texto ja obtido do PDF C6 Bank."""
    rows: list[dict] = []
    seen_hashes: set[str] = set()
    errors: list[str] = []
    reject_reasons: dict[str, int] = {}

    ano_ref = _infer_statement_year(text_value)
    candidate_lines = _merge_wrapped_lines(text_value)

    for line in candidate_lines:
        row = _parse_c6_line(
            line, "C6 Bank", file_name,
            ano_referencia=ano_ref, reject_reasons=reject_reasons,
        )
        if not row:
            continue
        if row["hash_lancamento"] in seen_hashes:
            reject_reasons["duplicada"] = reject_reasons.get("duplicada", 0) + 1
            continue
        seen_hashes.add(row["hash_lancamento"])
        rows.append(row)

    n_chars = len((text_value or "").strip())
    if not rows:
        if n_chars < 20:
            errors.append(
                "O PDF nao retornou texto extraivel. Verifique se ele nao esta "
                "escaneado, protegido ou em formato de imagem."
            )
        elif candidate_lines:
            errors.append(
                f"Foram lidas {len(candidate_lines)} linha(s) do PDF, mas nenhuma "
                "passou nas regras de identificacao de movimentacoes. Verifique se "
                "o banco selecionado corresponde ao modelo do extrato enviado."
            )
        else:
            errors.append(
                "O PDF foi lido, mas nenhuma linha compativel com movimentacao "
                "bancaria foi encontrada."
            )

    diagnostics = {
        "ano_referencia": ano_ref,
        "n_chars": n_chars,
        "n_linhas_candidatas": len(candidate_lines),
        "n_movimentos_validos": len(rows),
        "motivos_descarte": reject_reasons,
    }
    if DEBUG_IMPORT_EXTRATO:
        logger.info("[extrato] parse %s", diagnostics)

    return {
        "ok": bool(rows) and not errors,
        "bank": "C6 Bank",
        "rows": rows,
        "errors": errors,
        "summary": summarize_bank_movements(rows, "C6 Bank"),
        "parse_diagnostics": diagnostics,
    }


def _clean_extracted_text(text_value: str) -> str:
    """Remove caracteres invisiveis e normaliza espacos preservando quebras."""
    if not text_value:
        return ""
    # Remove zero-width / BOM / soft hyphen e normaliza NBSP -> espaco.
    for ch in ("​", "‌", "‍", "﻿", "­"):
        text_value = text_value.replace(ch, "")
    text_value = text_value.replace(" ", " ").replace("\t", " ")
    return text_value


def _extract_with_pdfplumber(file_bytes: bytes) -> tuple[str, int]:
    import pdfplumber

    parts: list[str] = []
    n_pages = 0
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        n_pages = len(pdf.pages)
        for page in pdf.pages:
            txt = page.extract_text() or ""
            if not txt:
                # Layout em colunas: tenta extracao baseada em palavras com
                # tolerancia maior, que costuma recuperar linhas que o
                # extract_text padrao perde.
                try:
                    txt = page.extract_text(x_tolerance=1.5, y_tolerance=3.0) or ""
                except Exception:
                    txt = ""
            if txt:
                parts.append(txt)
    return "\n".join(parts), n_pages


def _extract_with_pypdf(file_bytes: bytes) -> tuple[str, int]:
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader  # type: ignore
        except ImportError:
            return "", 0
    reader = PdfReader(io.BytesIO(file_bytes))
    parts = [(page.extract_text() or "") for page in reader.pages]
    return "\n".join(parts), len(reader.pages)


def extract_bank_statement_text(file_bytes: bytes) -> dict:
    """Extrai texto do PDF com fallback entre bibliotecas.

    Retorna dict com: text, n_pages, engine, n_chars, scanned (bool/None) e
    qualquer erro de leitura. Nao levanta excecao para PDF sem texto — sinaliza
    via 'scanned'/'n_chars' para o chamador exibir mensagem adequada.
    """
    diag: dict[str, Any] = {
        "text": "", "n_pages": 0, "engine": None,
        "n_chars": 0, "scanned": None, "read_error": None, "ocr_used": False,
    }

    best_text = ""
    best_engine = None
    best_pages = 0
    seen_pages = 0
    for engine_name, fn in (("pdfplumber", _extract_with_pdfplumber), ("pypdf", _extract_with_pypdf)):
        try:
            txt, n_pages = fn(file_bytes)
        except ImportError as exc:
            diag["read_error"] = f"{engine_name}: {exc}"
            continue
        except Exception as exc:  # PDF corrompido/protegido por uma das libs
            diag["read_error"] = f"{engine_name}: {exc}"
            continue
        seen_pages = max(seen_pages, n_pages)
        txt = _clean_extracted_text(txt)
        if len(txt.strip()) > len(best_text.strip()):
            best_text, best_engine, best_pages = txt, engine_name, n_pages
        # Texto suficiente para parsear: nao precisa do fallback.
        if len(best_text.strip()) >= 40:
            break

    # Fallback de OCR: PDF sem texto pesquisavel (escaneado / "Print to PDF" /
    # imagem). Recupera o texto rasterizado quando as libs de OCR existem.
    if len(best_text.strip()) < 20 and seen_pages > 0:
        try:
            from core.c6_ocr import ocr_extract_text

            ocr_text, ocr_pages = ocr_extract_text(file_bytes)
        except Exception as exc:
            diag["read_error"] = f"ocr: {exc}"
            ocr_text, ocr_pages = "", 0
        ocr_text = _clean_extracted_text(ocr_text)
        if len(ocr_text.strip()) > len(best_text.strip()):
            best_text = ocr_text
            best_engine = "ocr-tesseract"
            best_pages = ocr_pages or seen_pages
            diag["ocr_used"] = True

    diag.update(
        text=best_text,
        engine=best_engine,
        n_pages=best_pages or seen_pages,
        n_chars=len(best_text.strip()),
        scanned=((best_pages or seen_pages) > 0 and len(best_text.strip()) < 20),
    )
    if DEBUG_IMPORT_EXTRATO:
        logger.info(
            "[extrato] engine=%s paginas=%s chars=%s scanned=%s",
            diag["engine"], diag["n_pages"], diag["n_chars"], diag["scanned"],
        )
    return diag


def _extract_pdf_text(file_bytes: bytes) -> str:
    """Compatibilidade: retorna apenas o texto extraido."""
    return extract_bank_statement_text(file_bytes).get("text", "")


def parse_c6_bank_pdf(file_bytes: bytes, file_name: str | None = None) -> dict:
    diag = extract_bank_statement_text(file_bytes)
    text_value = diag.get("text") or ""

    # PDF sem texto pesquisavel (escaneado / protegido / imagem).
    if diag.get("n_chars", 0) < 20:
        if diag.get("read_error"):
            msg = (
                "Nao foi possivel ler o PDF "
                f"({diag['read_error']}). Verifique se o arquivo nao esta "
                "protegido ou corrompido."
            )
        else:
            msg = (
                "O PDF nao retornou texto extraivel. Verifique se ele nao esta "
                "escaneado, protegido ou em formato de imagem."
            )
        return {
            "ok": False, "bank": "C6 Bank", "rows": [], "errors": [msg],
            "summary": summarize_bank_movements([], "C6 Bank"),
            "diagnostics": diag,
        }

    parsed = parse_c6_bank_text(text_value, file_name=file_name)
    parsed["diagnostics"] = {k: v for k, v in diag.items() if k != "text"}
    return parsed


def summarize_bank_movements(rows: list[dict], bank: str = "C6 Bank") -> dict:
    if not rows:
        return {
            "bank": bank,
            "rows": 0,
            "entradas": 0,
            "saidas": 0,
            "total_entradas": 0.0,
            "total_saidas": 0.0,
            "classificados": 0,
            "pendentes": 0,
            "periodo_inicio": None,
            "periodo_fim": None,
        }
    dates = [row["data_movimento"] for row in rows if row.get("data_movimento")]
    entradas = [float(row.get("valor") or 0.0) for row in rows if float(row.get("valor") or 0.0) > 0]
    saidas = [float(row.get("valor") or 0.0) for row in rows if float(row.get("valor") or 0.0) < 0]
    pendentes = sum(1 for row in rows if row.get("status_classificacao") == "pendente")
    return {
        "bank": bank,
        "rows": len(rows),
        "entradas": len(entradas),
        "saidas": len(saidas),
        "total_entradas": round(sum(entradas), 2),
        "total_saidas": round(sum(abs(value) for value in saidas), 2),
        "classificados": len(rows) - pendentes,
        "pendentes": pendentes,
        "periodo_inicio": min(dates) if dates else None,
        "periodo_fim": max(dates) if dates else None,
    }


def _category_name(category: dict) -> str:
    return str(category.get("nome") or category.get("name") or "")


def _category_type(category: dict) -> str:
    return str(category.get("tipo") or category.get("type") or "")


def _find_category(
    categories: list[dict],
    aliases: list[str],
    preferred_types: tuple[str, ...] = (),
) -> dict | None:
    norm_aliases = [_norm(alias) for alias in aliases if _norm(alias)]
    if not norm_aliases:
        return None

    candidates: list[tuple[int, int, dict]] = []
    for category in categories:
        name_norm = _norm(_category_name(category))
        type_norm = _norm(_category_type(category))
        if preferred_types and type_norm not in preferred_types:
            continue
        score = None
        best_alias_idx = len(norm_aliases)
        for alias_idx, alias in enumerate(norm_aliases):
            if name_norm == alias:
                score = 0
                best_alias_idx = alias_idx
                break
            if alias in name_norm:
                if score is None or 1 < score:
                    score = 1
                    best_alias_idx = alias_idx
            elif name_norm and name_norm in alias and len(name_norm) >= 5:
                if score is None or 2 < score:
                    score = 2
                    best_alias_idx = alias_idx
        if score is not None:
            candidates.append((best_alias_idx, score, category))

    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1], _category_name(item[2])))
        return candidates[0][2]

    if preferred_types:
        return _find_category(categories, aliases, ())
    return None


def _is_boleto_or_pagamento(row: dict) -> bool:
    text_value = _norm(f"{row.get('tipo_original_banco')} {row.get('descricao_original')}")
    return "boleto" in text_value or "pagamento" in text_value


def _find_other_category(categories: list[dict], direction: str) -> dict | None:
    if direction == "entrada":
        return _find_category(
            categories,
            ["Outros", "Outros Rendimentos", "Rendimentos", "Receita", "Entrada"],
            ("income",),
        )
    return _find_category(
        categories,
        ["Outros", "Outras Despesas", "Outros Gastos", "Saida", "Despesas"],
        ("expense",),
    )


def _other_classification(row: dict, categories: list[dict], confidence: float, reason: str) -> dict:
    category = _find_other_category(categories, row.get("direcao") or "saida")
    return _classification_payload(row, category, "Outros", "sugerida", confidence, reason)


def _classification_payload(
    row: dict,
    category: dict | None,
    suggested_text: str,
    status: str,
    confidence: float,
    reason: str,
) -> dict:
    category_id = category.get("id") if category else None
    category_name = _category_name(category) if category else None
    return {
        **row,
        "categoria_id": category_id,
        "categoria_nome": category_name,
        "categoria_sugerida_texto": suggested_text,
        "status_classificacao": status if category_id else "pendente",
        "confianca_classificacao": round(float(confidence if category_id else 0.0), 2),
        "classificacao_motivo": reason,
    }


def _rule_category(rule: dict, categories: list[dict]) -> dict | None:
    category_id = str(rule.get("category_id") or rule.get("categoria_id") or "")
    for category in categories:
        if str(category.get("id")) == category_id:
            return category
    return None


def _apply_saved_rules(row: dict, categories: list[dict], saved_rules: list[dict]) -> dict | None:
    desc_norm = row.get("descricao_normalizada") or ""
    bank_norm = _norm(row.get("banco"))
    type_norm = _norm(row.get("tipo_original_banco"))
    for rule in saved_rules or []:
        rule_bank = _norm(rule.get("banco"))
        rule_type = _norm(rule.get("tipo_original_banco"))
        if rule_bank and rule_bank != bank_norm:
            continue
        if rule_type and rule_type != type_norm:
            continue
        keyword = _norm(rule.get("palavra_chave"))
        exact = _norm(rule.get("descricao_normalizada"))
        if (keyword and keyword in desc_norm) or (exact and exact == desc_norm):
            category = _rule_category(rule, categories)
            if category:
                return _classification_payload(
                    row,
                    category,
                    _category_name(category),
                    "confirmada",
                    0.99,
                    "Regra salva pelo usuario",
                )
    return None


def classify_bank_movement(
    row: dict,
    categories: list[dict],
    saved_rules: list[dict] | None = None,
) -> dict:
    """Classifica uma movimentacao usando somente categorias recebidas."""
    manual = _apply_saved_rules(row, categories, saved_rules or [])
    if manual:
        return manual

    desc = row.get("descricao_normalizada") or _norm(row.get("descricao_original"))
    abs_value = abs(float(row.get("valor") or 0.0))

    # Salario: Pix recebido do Santander ou do Tiago acima de R$ 10.000,00.
    if (
        row.get("direcao") == "entrada"
        and abs_value > 10000.0
        and ("santander" in desc or "tiago" in desc)
    ):
        category = _find_category(
            categories,
            ["Salario", "Renda principal", "Remuneracao", "Receita fixa", "Receita", "Entrada"],
            ("income",),
        )
        return _classification_payload(
            row, category, "Salario", "sugerida", 0.98,
            "Regra explicita: salario (Santander/Tiago > R$ 10.000,00)",
        )

    # Regras por destinatario de Pix enviado (definidas pelo usuario).
    if row.get("direcao") == "saida":
        _ALIASES_FINANCIAMENTO = [
            "Financiamento", "Emprestimos e Financiamentos", "Dividas",
            "Imovel", "Moradia", "Casa", "Investimentos",
        ]
        # Pix enviado para Adelaide acima de R$ 3.000,00 -> Financiamento.
        if "adelaide" in desc and abs_value > 3000.0:
            category = _find_category(categories, _ALIASES_FINANCIAMENTO, ("expense", "transfer", "income"))
            return _classification_payload(
                row, category, "Financiamento", "sugerida", 0.95,
                "Regra explicita: Pix para Adelaide > R$ 3.000,00 (financiamento)",
            )

        # Pagamento para Costa Marques (construtora/incorporadora) -> Financiamento.
        if "costa marques" in desc:
            category = _find_category(categories, _ALIASES_FINANCIAMENTO, ("expense", "transfer", "income"))
            return _classification_payload(
                row, category, "Financiamento", "sugerida", 0.95,
                "Regra explicita: Costa Marques (financiamento)",
            )

        destinatario_rules = (
            (("luciana",), ["Saude"], "Saude"),
            (("laredo", "bruno de almeida laredo"), ["Educacao"], "Educacao"),
            (("moisaniel",), ["Internet"], "Internet"),
            (
                ("gizeli",),
                ["Despesas domesticas", "Despesas Domesticas", "Despesas com a casa", "Casa"],
                "Despesas domesticas",
            ),
        )
        for keywords, aliases, suggested in destinatario_rules:
            if any(keyword in desc for keyword in keywords):
                category = _find_category(categories, aliases, ("expense",))
                return _classification_payload(
                    row, category, suggested, "sugerida", 0.95,
                    "Regra explicita: destinatario do Pix",
                )

        # Transferencias para corretoras = investimento. Usa palavra inteira
        # (evita falso-positivo, ex.: "Frederico" contem "rico").
        desc_tokens = set(desc.split())
        if "rico" in desc_tokens:
            category = _find_category(
                categories,
                ["Renda Fixa", "Renda Fixa (CDB)", "Renda Fixa CDB", "Investimentos", "Investimento"],
                ("transfer", "expense", "income"),
            )
            return _classification_payload(
                row, category, "Renda Fixa", "sugerida", 0.95,
                "Regra explicita: transferencia para Rico (investimento RF)",
            )
        if "nomad" in desc_tokens:
            category = _find_category(
                categories,
                ["Exterior", "Investimentos no Exterior", "Internacional", "Investimentos", "Investimento"],
                ("transfer", "expense", "income"),
            )
            return _classification_payload(
                row, category, "Exterior", "sugerida", 0.95,
                "Regra explicita: transferencia para Nomad (investimento Exterior)",
            )

    if _is_boleto_or_pagamento(row) and any(
        term in desc for term in ("euroville", "euro ville", "salinas resort", "salinas", "resort")
    ):
        category = _find_category(
            categories,
            ["Condominio", "Moradia", "Casa", "Imovel", "Despesas fixas", "Saida"],
            ("expense",),
        )
        return _classification_payload(row, category, "Condominio", "sugerida", 0.95, "Regra explicita: condominio")

    if (
        _is_boleto_or_pagamento(row)
        and any(term in desc for term in ("reserva do lago", "reserva lago", "res do lago"))
        and abs_value >= VALOR_ALTO_FINANCIAMENTO
    ):
        category = _find_category(
            categories,
            [
                "Financiamento",
                "Emprestimos e Financiamentos",
                "Dividas",
                "Imovel",
                "Moradia",
                "Casa",
                "Investimentos",
            ],
            ("expense", "transfer", "income"),
        )
        return _classification_payload(row, category, "Financiamento", "sugerida", 0.93, "Regra explicita: financiamento")

    if "resgate de cdb" in desc:
        return _other_classification(row, categories, 0.90, "Regra definida: resgate CDB como entrada em Outros")

    if "secretaria do tesouro nacional" in desc or "tesouro nacional" in desc:
        return _other_classification(row, categories, 0.88, "Regra definida: Tesouro Nacional como saida em Outros")

    # Posto/combustivel: vence o "Debito de Cartao" generico (ex.: compra no
    # debito "Debito de Cartao POSTO...") e o abastecimento em geral.
    # "posto" so no inicio de palavra para nao casar "imposto".
    if re.search(r"(?<![a-z])posto", desc) or any(
        k in desc for k in ("pana vip", "para vip", "pit stop", "abastece")
    ):
        category = _find_category(
            categories, ["Combustivel", "Transporte", "Carro", "Veiculo"], ("expense",)
        )
        return _classification_payload(
            row, category, "Combustivel", "sugerida", 0.86, "Regra definida: posto (combustivel)"
        )

    # Pagamento de fatura/cartao (ex.: "PGTO FAT CARTAO C6", "pagamento de fatura").
    pagamento_cartao_keywords = (
        "pgto fat cartao", "pgto fatura", "pagamento de fatura", "pagamento fatura",
        "pag fatura", "fatura cartao", "fatura do cartao", "fatura c6",
        "pagamento de cartao", "pagamento cartao", "cartao de credito",
    )
    if any(k in desc for k in pagamento_cartao_keywords):
        category = _find_category(
            categories,
            ["Pagamento de Cartao", "Pagamento de Fatura", "Cartao de Credito", "Cartao"],
            ("expense", "transfer"),
        )
        return _classification_payload(row, category, "Pagamento de Cartao", "sugerida", 0.92, "Regra definida: pagamento de cartao/fatura")

    if "debito de cartao" in desc or "debito de cartao" in _norm(row.get("tipo_original_banco")):
        category = _find_category(
            categories,
            ["Pagamento de Cartao", "Pagamento de Fatura", "Cartao de Credito", "Cartao"],
            ("expense", "transfer"),
        )
        return _classification_payload(row, category, "Pagamento de Cartao", "sugerida", 0.90, "Regra definida: debito de cartao")

    auto_rules: list[tuple[tuple[str, ...], list[str], tuple[str, ...], str, float]] = [
        (("tim celular",), ["Telefone / Internet", "Assinaturas", "Moradia"], ("expense",), "Telefone / Internet", 0.86),
        (("equatorial",), ["Luz", "Energia", "Moradia", "Casa", "Contas fixas"], ("expense",), "Luz", 0.86),
        (("ifood",), ["Alimentacao", "Restaurante", "Delivery", "Lanche"], ("expense",), "Alimentacao", 0.84),
        (("drogaria", "farmacia"), ["Saude", "Farmacia", "Medicamentos"], ("expense",), "Saude", 0.86),
        (("estacionamento",), ["Transporte", "Estacionamento", "Carro", "Veiculo"], ("expense",), "Transporte", 0.83),
    ]
    for keywords, aliases, preferred_types, suggested, confidence in auto_rules:
        if any(keyword in desc for keyword in keywords):
            category = _find_category(categories, aliases, preferred_types)
            return _classification_payload(row, category, suggested, "sugerida", confidence, "Regra automatica por palavra-chave")

    return _other_classification(row, categories, 0.65, "Fallback por direcao: Outros")


def classify_bank_movements(rows: list[dict], categories: list[dict], saved_rules: list[dict] | None = None) -> list[dict]:
    return [classify_bank_movement(row, categories, saved_rules or []) for row in rows]


# Garante que o DDL rode apenas uma vez por processo (evita deadlock por
# ALTER TABLE ENABLE ROW LEVEL SECURITY executado em paralelo por múltiplas
# sessões Streamlit que compartilham o mesmo worker).
_schema_initialized: bool = False
_schema_init_lock = threading.Lock()


def _ensure_tables(conn) -> None:
    global _schema_initialized
    if _schema_initialized:
        return
    with _schema_init_lock:
        if _schema_initialized:
            return
        for ddl in DDL_SQL:
            conn.execute(text(ddl))
        _schema_initialized = True


def _owner_id() -> str:
    owner = settings.OWNER_USER_ID
    if not owner:
        raise RuntimeError("OWNER_USER_ID nao configurado.")
    return str(owner)


def _maybe_owner_id() -> str | None:
    return str(settings.OWNER_USER_ID) if settings.OWNER_USER_ID else None


def _is_bank_statement_account_type(value: object) -> bool:
    normalized = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    return normalized in set(_BANK_STATEMENT_ACCOUNT_TYPES)


def _resolve_bank_statement_account(conn, owner: str, account_id: str | None = None):
    if account_id:
        account = conn.execute(
            text(
                """
                SELECT id::text, name, type
                FROM accounts
                WHERE id = CAST(:account_id AS uuid)
                  AND user_id = CAST(:uid AS uuid)
                  AND active = TRUE
                LIMIT 1
                """
            ),
            {"account_id": account_id, "uid": owner},
        ).fetchone()
        if account and _is_bank_statement_account_type(getattr(account, "type", "")):
            return account

    return conn.execute(
        text(
            """
            SELECT id::text, name, type
            FROM accounts
            WHERE user_id = CAST(:uid AS uuid)
              AND active = TRUE
              AND type IN ('checking', 'savings', 'digital_wallet')
            ORDER BY
                CASE type
                    WHEN 'checking' THEN 0
                    WHEN 'digital_wallet' THEN 1
                    WHEN 'savings' THEN 2
                    ELSE 9
                END,
                name
            LIMIT 1
            """
        ),
        {"uid": owner},
    ).fetchone()


def _fetch_categories(conn, owner: str) -> list[dict]:
    rows = conn.execute(
        text(
            """
            SELECT id::text, name, type, parent_id::text
            FROM categories
            WHERE user_id = CAST(:uid AS uuid) OR user_id IS NULL
            ORDER BY name
            """
        ),
        {"uid": owner},
    ).fetchall()
    return [{"id": r.id, "nome": r.name, "tipo": r.type, "parent_id": r.parent_id} for r in rows]


def _fetch_rules(conn, owner: str, banco: str = "C6 Bank") -> list[dict]:
    _ensure_tables(conn)
    rows = conn.execute(
        text(
            """
            SELECT id::text, banco, tipo_original_banco, palavra_chave,
                   descricao_normalizada, category_id::text, subcategoria_id::text
            FROM bank_statement_classification_rules
            WHERE user_id = CAST(:uid AS uuid)
              AND (banco IS NULL OR banco = :banco)
            ORDER BY created_at DESC
            """
        ),
        {"uid": owner, "banco": banco},
    ).fetchall()
    return [
        {
            "id": r.id,
            "banco": r.banco,
            "tipo_original_banco": r.tipo_original_banco,
            "palavra_chave": r.palavra_chave,
            "descricao_normalizada": r.descricao_normalizada,
            "category_id": r.category_id,
            "subcategoria_id": r.subcategoria_id,
        }
        for r in rows
    ]


def get_bank_statement_accounts() -> list[dict]:
    if settings.MOCK_MODE:
        return [{"id": "mock-checking", "nome": "Conta Corrente", "tipo": "checking"}]
    engine = get_engine()
    if engine is None:
        return []
    owner = _maybe_owner_id()
    if not owner:
        return []
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id::text, name, type
                FROM accounts
                WHERE user_id = CAST(:uid AS uuid)
                  AND active = TRUE
                  AND type IN ('checking', 'savings', 'digital_wallet')
                ORDER BY
                    CASE type
                        WHEN 'checking' THEN 0
                        WHEN 'digital_wallet' THEN 1
                        WHEN 'savings' THEN 2
                        ELSE 9
                    END,
                    name
                """
            ),
            {"uid": owner},
        ).fetchall()
    return [{"id": r.id, "nome": r.name, "tipo": r.type} for r in rows]


def get_bank_statement_categories() -> list[dict]:
    if settings.MOCK_MODE:
        return []
    engine = get_engine()
    if engine is None:
        return []
    owner = _maybe_owner_id()
    if not owner:
        return []
    with engine.connect() as conn:
        return _fetch_categories(conn, owner)


def preview_bank_statement_pdf(file_bytes: bytes, file_name: str | None = None, banco: str = "C6 Bank") -> dict:
    parsed = parse_c6_bank_pdf(file_bytes, file_name=file_name)
    rows = parsed.get("rows", [])
    if not rows:
        return parsed

    if settings.MOCK_MODE:
        categories: list[dict] = []
        rules: list[dict] = []
    else:
        engine = get_engine()
        if engine is None:
            categories, rules = [], []
        else:
            owner = _maybe_owner_id()
            if not owner:
                categories, rules = [], []
            else:
                with engine.begin() as conn:
                    _ensure_tables(conn)
                    categories = _fetch_categories(conn, owner)
                    rules = _fetch_rules(conn, owner, banco)

    classified = classify_bank_movements(rows, categories, rules)
    parsed["rows"] = classified
    parsed["summary"] = summarize_bank_movements(classified, banco)
    return parsed


def _transaction_type_for(row: dict, category: dict | None = None) -> str:
    cat_name = _category_name(category or {})
    cat_type = _category_type(category or {})
    cat_norm = _norm(cat_name)
    suggested = _norm(row.get("categoria_sugerida_texto"))
    if any(term in suggested for term in ("pagamento de cartao", "debito de cartao")):
        return "expense"
    if cat_type == "transfer" or any(term in cat_norm for term in ("investimento", "transferencia")):
        return "transfer"
    if "investimento" in suggested or "resgate" in suggested:
        return "transfer"
    if row.get("direcao") == "entrada":
        return "income"
    return "expense"


def _insert_transaction(conn, owner: str, row: dict, account_id: str, category: dict | None) -> str | None:
    tx_row = conn.execute(
        text(
            """
            INSERT INTO transactions (
                user_id, account_id, category_id, description, amount,
                due_date, payment_date, type, status, source
            )
            VALUES (
                CAST(:uid AS uuid), CAST(:account_id AS uuid), CAST(:category_id AS uuid),
                :description, :amount, :due_date, :payment_date, :type, 'settled', :source
            )
            RETURNING id::text
            """
        ),
        {
            "uid": owner,
            "account_id": account_id,
            "category_id": row.get("categoria_id"),
            "description": str(row.get("descricao_original") or "")[:255],
            "amount": round(float(row.get("valor") or 0.0), 2),
            "due_date": row.get("data_movimento"),
            "payment_date": row.get("data_lancamento"),
            "type": _transaction_type_for(row, category),
            "source": BANK_STATEMENT_SOURCE,
        },
    ).fetchone()
    return tx_row.id if tx_row else None


def import_bank_statement_pdf(
    file_bytes: bytes,
    file_name: str,
    account_id: str | None = None,
    banco: str = "C6 Bank",
) -> dict:
    if settings.MOCK_MODE:
        return {"ok": False, "message": "Modo mock ativo; importacao nao executada."}

    engine = get_engine()
    if engine is None:
        return {"ok": False, "message": "Banco nao configurado."}

    owner = _owner_id()
    parsed = parse_c6_bank_pdf(file_bytes, file_name=file_name)
    if parsed.get("errors"):
        return {"ok": False, "message": "; ".join(parsed["errors"][:5]), "errors": parsed["errors"]}
    if not parsed.get("rows"):
        return {"ok": False, "message": "Extrato sem linhas validas."}

    inserted = 0
    skipped = 0
    published = 0
    pending = 0

    with engine.begin() as conn:
        _ensure_tables(conn)
        account = _resolve_bank_statement_account(conn, owner, account_id)
        if not account:
            return {
                "ok": False,
                "message": (
                    "Nenhuma conta tecnica de movimentacao foi encontrada para publicar no Controle Financeiro. "
                    "Use uma conta do tipo checking, savings ou digital_wallet."
                ),
            }
        account_id = account.id

        categories = _fetch_categories(conn, owner)
        rules = _fetch_rules(conn, owner, banco)
        rows = classify_bank_movements(parsed["rows"], categories, rules)
        category_by_id = {str(category["id"]): category for category in categories}

        for row in rows:
            params = {
                "uid": owner,
                "account_id": account_id,
                "banco": row.get("banco") or banco,
                "conta": getattr(account, "name", None),
                "data_movimento": row.get("data_movimento"),
                "data_lancamento": row.get("data_lancamento"),
                "tipo_original_banco": row.get("tipo_original_banco"),
                "descricao_original": row.get("descricao_original"),
                "descricao_normalizada": row.get("descricao_normalizada"),
                "valor": round(float(row.get("valor") or 0.0), 2),
                "direcao": row.get("direcao"),
                "categoria_id": row.get("categoria_id"),
                "subcategoria_id": row.get("subcategoria_id"),
                "categoria_sugerida_texto": row.get("categoria_sugerida_texto"),
                "subcategoria_sugerida_texto": row.get("subcategoria_sugerida_texto"),
                "confianca_classificacao": row.get("confianca_classificacao"),
                "status_classificacao": row.get("status_classificacao") or "pendente",
                "origem_arquivo": file_name,
                "hash_lancamento": row.get("hash_lancamento"),
            }
            inserted_row = conn.execute(
                text(
                    """
                    INSERT INTO bank_statement_movements (
                        user_id, account_id, banco, conta, data_movimento, data_lancamento,
                        tipo_original_banco, descricao_original, descricao_normalizada, valor,
                        direcao, categoria_id, subcategoria_id, categoria_sugerida_texto,
                        subcategoria_sugerida_texto, confianca_classificacao,
                        status_classificacao, origem_arquivo, hash_lancamento
                    )
                    VALUES (
                        CAST(:uid AS uuid), CAST(:account_id AS uuid), :banco, :conta,
                        :data_movimento, :data_lancamento, :tipo_original_banco,
                        :descricao_original, :descricao_normalizada, :valor, :direcao,
                        CAST(:categoria_id AS uuid), CAST(:subcategoria_id AS uuid),
                        :categoria_sugerida_texto, :subcategoria_sugerida_texto,
                        :confianca_classificacao, :status_classificacao,
                        :origem_arquivo, :hash_lancamento
                    )
                    ON CONFLICT (user_id, hash_lancamento) DO NOTHING
                    RETURNING id::text
                    """
                ),
                params,
            ).fetchone()
            if not inserted_row:
                skipped += 1
                continue

            inserted += 1
            if row.get("categoria_id"):
                category = category_by_id.get(str(row.get("categoria_id")))
                tx_id = _insert_transaction(conn, owner, row, account_id, category)
                if tx_id:
                    conn.execute(
                        text(
                            """
                            UPDATE bank_statement_movements
                            SET transaction_id = CAST(:tx_id AS uuid), updated_at = NOW()
                            WHERE id = CAST(:movement_id AS uuid)
                            """
                        ),
                        {"tx_id": tx_id, "movement_id": inserted_row.id},
                    )
                    published += 1
            else:
                pending += 1

    try:
        from core.controle import _clear_controle_caches

        _clear_controle_caches()
    except Exception:
        logger.debug("Nao foi possivel limpar caches do controle financeiro.", exc_info=True)

    summary = summarize_bank_movements(rows, banco)
    summary.update({"inserted": inserted, "skipped": skipped, "published": published, "pending": pending})
    return {
        "ok": True,
        "message": (
            f"{inserted} movimento(s) importado(s); {published} publicado(s) no Controle Financeiro; "
            f"{pending} pendente(s); {skipped} duplicado(s) ignorado(s)."
        ),
        "summary": summary,
        "rows": rows,
    }


def import_bank_statement_rows(
    rows: list[dict],
    file_name: str,
    banco: str = "C6 Bank",
) -> dict:
    """Grava movimentos JA classificados/editados (vindos da prévia editável),
    sem reparsear o PDF nem reclassificar. Recalcula a descrição normalizada e
    o hash a partir dos valores atuais para manter a idempotência."""
    if settings.MOCK_MODE:
        return {"ok": False, "message": "Modo mock ativo; importacao nao executada."}

    engine = get_engine()
    if engine is None:
        return {"ok": False, "message": "Banco nao configurado."}
    if not rows:
        return {"ok": False, "message": "Nada para importar."}

    owner = _owner_id()
    inserted = skipped = published = pending = 0

    with engine.begin() as conn:
        _ensure_tables(conn)
        account = _resolve_bank_statement_account(conn, owner, None)
        if not account:
            return {
                "ok": False,
                "message": (
                    "Nenhuma conta tecnica de movimentacao foi encontrada para publicar no Controle Financeiro. "
                    "Use uma conta do tipo checking, savings ou digital_wallet."
                ),
            }
        account_id = account.id
        categories = _fetch_categories(conn, owner)
        category_by_id = {str(category["id"]): category for category in categories}

        prepared: list[dict] = []
        for raw in rows:
            row = {**raw}
            row["descricao_normalizada"] = _norm(row.get("descricao_original"))
            row["valor"] = round(float(row.get("valor") or 0.0), 2)
            row["hash_lancamento"] = build_bank_statement_hash(row)
            prepared.append(row)

            params = {
                "uid": owner,
                "account_id": account_id,
                "banco": row.get("banco") or banco,
                "conta": getattr(account, "name", None),
                "data_movimento": row.get("data_movimento"),
                "data_lancamento": row.get("data_lancamento"),
                "tipo_original_banco": row.get("tipo_original_banco"),
                "descricao_original": row.get("descricao_original"),
                "descricao_normalizada": row.get("descricao_normalizada"),
                "valor": row["valor"],
                "direcao": row.get("direcao"),
                "categoria_id": row.get("categoria_id"),
                "subcategoria_id": row.get("subcategoria_id"),
                "categoria_sugerida_texto": row.get("categoria_sugerida_texto"),
                "subcategoria_sugerida_texto": row.get("subcategoria_sugerida_texto"),
                "confianca_classificacao": row.get("confianca_classificacao"),
                "status_classificacao": row.get("status_classificacao") or "pendente",
                "origem_arquivo": file_name,
                "hash_lancamento": row["hash_lancamento"],
            }
            inserted_row = conn.execute(
                text(
                    """
                    INSERT INTO bank_statement_movements (
                        user_id, account_id, banco, conta, data_movimento, data_lancamento,
                        tipo_original_banco, descricao_original, descricao_normalizada, valor,
                        direcao, categoria_id, subcategoria_id, categoria_sugerida_texto,
                        subcategoria_sugerida_texto, confianca_classificacao,
                        status_classificacao, origem_arquivo, hash_lancamento
                    )
                    VALUES (
                        CAST(:uid AS uuid), CAST(:account_id AS uuid), :banco, :conta,
                        :data_movimento, :data_lancamento, :tipo_original_banco,
                        :descricao_original, :descricao_normalizada, :valor, :direcao,
                        CAST(:categoria_id AS uuid), CAST(:subcategoria_id AS uuid),
                        :categoria_sugerida_texto, :subcategoria_sugerida_texto,
                        :confianca_classificacao, :status_classificacao,
                        :origem_arquivo, :hash_lancamento
                    )
                    ON CONFLICT (user_id, hash_lancamento) DO NOTHING
                    RETURNING id::text
                    """
                ),
                params,
            ).fetchone()
            if not inserted_row:
                skipped += 1
                continue

            inserted += 1
            if row.get("categoria_id"):
                category = category_by_id.get(str(row.get("categoria_id")))
                tx_id = _insert_transaction(conn, owner, row, account_id, category)
                if tx_id:
                    conn.execute(
                        text(
                            """
                            UPDATE bank_statement_movements
                            SET transaction_id = CAST(:tx_id AS uuid), updated_at = NOW()
                            WHERE id = CAST(:movement_id AS uuid)
                            """
                        ),
                        {"tx_id": tx_id, "movement_id": inserted_row.id},
                    )
                    published += 1
            else:
                pending += 1

    try:
        from core.controle import _clear_controle_caches

        _clear_controle_caches()
    except Exception:
        logger.debug("Nao foi possivel limpar caches do controle financeiro.", exc_info=True)

    summary = summarize_bank_movements(prepared, banco)
    summary.update({"inserted": inserted, "skipped": skipped, "published": published, "pending": pending})
    return {
        "ok": True,
        "message": (
            f"{inserted} movimento(s) importado(s); {published} publicado(s) no Controle Financeiro; "
            f"{pending} pendente(s); {skipped} duplicado(s) ignorado(s)."
        ),
        "summary": summary,
        "rows": prepared,
    }


def get_bank_statement_review_rows(
    status: str | None = None,
    ano: int | None = None,
    mes: int | None = None,
    banco: str | None = None,
    limit: int = 250,
) -> list[dict]:
    if settings.MOCK_MODE:
        return []
    engine = get_engine()
    if engine is None:
        return []
    owner = _maybe_owner_id()
    if not owner:
        return []
    where = ["m.user_id = CAST(:uid AS uuid)"]
    params: dict[str, Any] = {"uid": owner, "limit": int(limit)}
    if status and status != "Todos":
        where.append("m.status_classificacao = :status")
        params["status"] = status
    if ano:
        where.append("EXTRACT(YEAR FROM m.data_movimento)::int = :ano")
        params["ano"] = int(ano)
    if mes:
        where.append("EXTRACT(MONTH FROM m.data_movimento)::int = :mes")
        params["mes"] = int(mes)
    if banco and banco != "Todos":
        where.append("m.banco = :banco")
        params["banco"] = banco

    with engine.begin() as conn:
        _ensure_tables(conn)
        rows = conn.execute(
            text(
                f"""
                SELECT
                    m.id::text, m.account_id::text, m.transaction_id::text,
                    m.banco, m.conta, m.data_movimento, m.data_lancamento,
                    m.tipo_original_banco, m.descricao_original, m.descricao_normalizada,
                    m.valor, m.direcao, m.categoria_id::text,
                    COALESCE(c.name, '') AS categoria_nome,
                    m.categoria_confirmada_id::text,
                    COALESCE(cc.name, '') AS categoria_confirmada_nome,
                    m.categoria_sugerida_texto,
                    m.confianca_classificacao,
                    m.status_classificacao,
                    m.origem_arquivo,
                    m.hash_lancamento
                FROM bank_statement_movements m
                LEFT JOIN categories c  ON c.id  = m.categoria_id
                LEFT JOIN categories cc ON cc.id = m.categoria_confirmada_id
                WHERE {' AND '.join(where)}
                ORDER BY m.data_movimento DESC, m.created_at DESC
                LIMIT :limit
                """
            ),
            params,
        ).fetchall()

    return [dict(row._mapping) for row in rows]


def confirm_bank_statement_movement(
    movement_id: str,
    category_id: str,
    account_id: str | None = None,
    save_rule: bool = False,
    palavra_chave: str | None = None,
) -> tuple[bool, str]:
    if settings.MOCK_MODE:
        return False, "Modo mock ativo; confirmacao nao executada."
    engine = get_engine()
    if engine is None:
        return False, "Banco nao configurado."
    owner = _owner_id()

    with engine.begin() as conn:
        _ensure_tables(conn)
        movement = conn.execute(
            text(
                """
                SELECT *
                FROM bank_statement_movements
                WHERE id = CAST(:movement_id AS uuid)
                  AND user_id = CAST(:uid AS uuid)
                LIMIT 1
                """
            ),
            {"movement_id": movement_id, "uid": owner},
        ).fetchone()
        if not movement:
            return False, "Movimento nao encontrado."

        category = conn.execute(
            text(
                """
                SELECT id::text, name, type
                FROM categories
                WHERE id = CAST(:category_id AS uuid)
                  AND (user_id = CAST(:uid AS uuid) OR user_id IS NULL)
                LIMIT 1
                """
            ),
            {"category_id": category_id, "uid": owner},
        ).fetchone()
        if not category:
            return False, "Categoria inexistente ou indisponivel para o usuario."

        preferred_account_id = account_id or str(movement.account_id or "")
        account = _resolve_bank_statement_account(conn, owner, preferred_account_id or None)
        if not account:
            return False, (
                "Nenhuma conta tecnica de movimentacao foi encontrada para publicar no Controle Financeiro. "
                "Use uma conta do tipo checking, savings ou digital_wallet."
            )
        final_account_id = account.id

        row = {
            "descricao_original": movement.descricao_original,
            "valor": float(movement.valor or 0.0),
            "data_movimento": movement.data_movimento,
            "data_lancamento": movement.data_lancamento,
            "direcao": movement.direcao,
            "categoria_id": category.id,
            "categoria_sugerida_texto": category.name,
        }
        category_dict = {"id": category.id, "nome": category.name, "tipo": category.type}

        if movement.transaction_id:
            conn.execute(
                text(
                    """
                    UPDATE transactions
                    SET category_id = CAST(:category_id AS uuid),
                        account_id = CAST(:account_id AS uuid),
                        type = :type
                    WHERE id = CAST(:tx_id AS uuid)
                      AND user_id = CAST(:uid AS uuid)
                    """
                ),
                {
                    "category_id": category.id,
                    "account_id": final_account_id,
                    "type": _transaction_type_for(row, category_dict),
                    "tx_id": str(movement.transaction_id),
                    "uid": owner,
                },
            )
            tx_id = str(movement.transaction_id)
        else:
            tx_id = _insert_transaction(conn, owner, row, final_account_id, category_dict)

        conn.execute(
            text(
                """
                UPDATE bank_statement_movements
                SET account_id = CAST(:account_id AS uuid),
                    transaction_id = CAST(:tx_id AS uuid),
                    categoria_id = CAST(:category_id AS uuid),
                    categoria_confirmada_id = CAST(:category_id AS uuid),
                    categoria_sugerida_texto = :category_name,
                    status_classificacao = 'confirmada',
                    confianca_classificacao = 1.00,
                    updated_at = NOW()
                WHERE id = CAST(:movement_id AS uuid)
                  AND user_id = CAST(:uid AS uuid)
                """
            ),
            {
                "account_id": final_account_id,
                "tx_id": tx_id,
                "category_id": category.id,
                "category_name": category.name,
                "movement_id": movement_id,
                "uid": owner,
            },
        )

        if save_rule:
            keyword = (palavra_chave or "").strip() or " ".join(str(movement.descricao_original or "").split()[:4])
            conn.execute(
                text(
                    """
                    INSERT INTO bank_statement_classification_rules (
                        user_id, banco, tipo_original_banco, palavra_chave,
                        descricao_normalizada, category_id
                    )
                    VALUES (
                        CAST(:uid AS uuid), :banco, :tipo_original_banco, :palavra_chave,
                        :descricao_normalizada, CAST(:category_id AS uuid)
                    )
                    """
                ),
                {
                    "uid": owner,
                    "banco": movement.banco,
                    "tipo_original_banco": movement.tipo_original_banco,
                    "palavra_chave": keyword[:160],
                    "descricao_normalizada": _norm(keyword),
                    "category_id": category.id,
                },
            )

    try:
        from core.controle import _clear_controle_caches

        _clear_controle_caches()
    except Exception:
        logger.debug("Nao foi possivel limpar caches do controle financeiro.", exc_info=True)
    return True, ""


def update_bank_statement_movement(
    movement_id: str,
    category_id: str | None = None,
    descricao: str | None = None,
    valor: float | None = None,
    direcao: str | None = None,
    account_id: str | None = None,
) -> tuple[bool, str]:
    """Edita um movimento de extrato importado (descricao, direcao, valor e/ou
    categoria) e mantem a transacao publicada em sincronia.

    - Com categoria: confirma o movimento e publica/atualiza a transacao.
    - Sem categoria: mantem 'pendente'; se ja houver transacao vinculada,
      atualiza descricao/valor/tipo dela.
    """
    if settings.MOCK_MODE:
        return False, "Modo mock ativo; edicao nao executada."
    engine = get_engine()
    if engine is None:
        return False, "Banco nao configurado."
    owner = _owner_id()

    try:
        with engine.begin() as conn:
            _ensure_tables(conn)
            mv = conn.execute(
                text(
                    """
                    SELECT * FROM bank_statement_movements
                    WHERE id = CAST(:id AS uuid) AND user_id = CAST(:uid AS uuid)
                    LIMIT 1
                    """
                ),
                {"id": movement_id, "uid": owner},
            ).fetchone()
            if not mv:
                return False, "Movimento nao encontrado."

            new_desc = (descricao if descricao is not None else mv.descricao_original) or ""
            new_dir = (direcao or mv.direcao or "saida")
            base_val = valor if valor is not None else float(mv.valor or 0.0)
            magnitude = abs(float(base_val or 0.0))
            signed = round(magnitude if new_dir == "entrada" else -magnitude, 2)

            category = None
            if category_id:
                category = conn.execute(
                    text(
                        """
                        SELECT id::text, name, type FROM categories
                        WHERE id = CAST(:cid AS uuid)
                          AND (user_id = CAST(:uid AS uuid) OR user_id IS NULL)
                        LIMIT 1
                        """
                    ),
                    {"cid": category_id, "uid": owner},
                ).fetchone()
                if not category:
                    return False, "Categoria inexistente ou indisponivel."

            account = _resolve_bank_statement_account(
                conn, owner, account_id or str(mv.account_id or "") or None
            )
            account_final = account.id if account else mv.account_id

            conn.execute(
                text(
                    """
                    UPDATE bank_statement_movements
                    SET descricao_original = :desc,
                        descricao_normalizada = :descn,
                        valor = :valor,
                        direcao = :dir,
                        categoria_id = CAST(:cid AS uuid),
                        categoria_confirmada_id = CAST(:ccid AS uuid),
                        categoria_sugerida_texto = :cnome,
                        status_classificacao = :status,
                        confianca_classificacao = :conf,
                        account_id = CAST(:acc AS uuid),
                        updated_at = NOW()
                    WHERE id = CAST(:id AS uuid) AND user_id = CAST(:uid AS uuid)
                    """
                ),
                {
                    "desc": new_desc[:500],
                    "descn": _norm(new_desc),
                    "valor": signed,
                    "dir": new_dir,
                    "cid": category.id if category else None,
                    "ccid": category.id if category else None,
                    "cnome": category.name if category else mv.categoria_sugerida_texto,
                    "status": "confirmada" if category else "pendente",
                    "conf": 1.0 if category else 0.0,
                    "acc": account_final,
                    "id": movement_id,
                    "uid": owner,
                },
            )

            row = {
                "descricao_original": new_desc,
                "valor": signed,
                "data_movimento": mv.data_movimento,
                "data_lancamento": mv.data_lancamento,
                "direcao": new_dir,
                "categoria_id": category.id if category else None,
                "categoria_sugerida_texto": category.name if category else None,
            }
            category_dict = (
                {"id": category.id, "nome": category.name, "tipo": category.type}
                if category else None
            )

            if mv.transaction_id:
                if category:
                    conn.execute(
                        text(
                            """
                            UPDATE transactions
                            SET description = :d, amount = :a, type = :t,
                                category_id = CAST(:cid AS uuid),
                                account_id = CAST(:acc AS uuid)
                            WHERE id = CAST(:tx AS uuid) AND user_id = CAST(:uid AS uuid)
                            """
                        ),
                        {
                            "d": new_desc[:255], "a": signed,
                            "t": _transaction_type_for(row, category_dict),
                            "cid": category.id, "acc": account_final,
                            "tx": str(mv.transaction_id), "uid": owner,
                        },
                    )
                else:
                    conn.execute(
                        text(
                            """
                            UPDATE transactions
                            SET description = :d, amount = :a, type = :t
                            WHERE id = CAST(:tx AS uuid) AND user_id = CAST(:uid AS uuid)
                            """
                        ),
                        {
                            "d": new_desc[:255], "a": signed,
                            "t": _transaction_type_for(row, None),
                            "tx": str(mv.transaction_id), "uid": owner,
                        },
                    )
            elif category:
                tx_id = _insert_transaction(conn, owner, row, account_final, category_dict)
                if tx_id:
                    conn.execute(
                        text(
                            """
                            UPDATE bank_statement_movements
                            SET transaction_id = CAST(:tx AS uuid), updated_at = NOW()
                            WHERE id = CAST(:id AS uuid)
                            """
                        ),
                        {"tx": tx_id, "id": movement_id},
                    )
    except Exception as exc:
        return False, f"Erro ao editar movimento: {exc}"

    try:
        from core.controle import _clear_controle_caches

        _clear_controle_caches()
    except Exception:
        logger.debug("Nao foi possivel limpar caches do controle financeiro.", exc_info=True)
    return True, ""
