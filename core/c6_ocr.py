"""
Fallback de OCR para extratos C6 em PDF só-imagem.

Os extratos do C6 baixados via "Microsoft Print to PDF" saem rasterizados
(0 caracteres extraíveis, só imagens). Quando pdfplumber/pypdf não retornam
texto, este módulo renderiza as páginas e roda OCR (Tesseract via pytesseract),
devolvendo o texto em linhas — no mesmo formato que o parser C6 espera
("DD/MM DD/MM Tipo Descrição Valor").

Tesseract é leve e instala bem no Streamlit Cloud via packages.txt
(tesseract-ocr, tesseract-ocr-por). Tudo aqui é opcional: se as dependências
não estiverem disponíveis, `ocr_extract_text` devolve ("", 0) e o chamador
mantém o comportamento atual.
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import shutil
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Multiplicador de DPI na renderização. 3.0 dá boa precisão sem estourar memória.
_RENDER_SCALE = 3.0

_lang_lock = threading.Lock()
_ocr_lang: "str | None" = None

# Cache simples por hash do arquivo, para não rodar OCR duas vezes
# (a UI chama preview e, depois, a importação com o mesmo PDF).
_text_cache: "dict[str, tuple[str, int]]" = {}
_CACHE_MAX = 8


def _discover_tesseract_command(*, environ=None, which=shutil.which) -> str | None:
    """Localiza o binário sem depender do PATH da automação no Windows."""
    found = which("tesseract")
    if found:
        return str(found)
    values = {
        str(key).lower(): value
        for key, value in (os.environ if environ is None else environ).items()
    }
    roots = [
        values.get("programfiles"),
        values.get("programfiles(x86)"),
        str(Path(values["localappdata"]) / "Programs")
        if values.get("localappdata") else None,
    ]
    for root in roots:
        if not root:
            continue
        candidate = Path(root) / "Tesseract-OCR" / "tesseract.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def ocr_available() -> bool:
    """True se as dependências de OCR (pytesseract + binário + pypdfium2) existem."""
    try:
        import pypdfium2  # noqa: F401
        import pytesseract

        command = _discover_tesseract_command()
        if command:
            pytesseract.pytesseract.tesseract_cmd = command
        pytesseract.get_tesseract_version()
    except Exception:
        return False
    return True


def ocr_extract_page_texts(
    file_bytes: bytes, *, render_scale: float = _RENDER_SCALE,
    max_seconds: float | None = None, page_limit: int | None = None,
) -> tuple[list[str], int, bool]:
    """Executa OCR por página e devolve resultado parcial antes do deadline."""
    if not file_bytes or not ocr_available():
        return [], 0, False
    import pypdfium2 as pdfium
    import pytesseract

    started = time.monotonic()
    lang = _resolve_lang()
    config = "--psm 4 --oem 1"
    try:
        pdf = pdfium.PdfDocument(io.BytesIO(file_bytes))
        total_pages = len(pdf)
        selected_pages = min(total_pages, max(int(page_limit or total_pages), 1))
        page_texts: list[str] = []
        for index in range(selected_pages):
            remaining = None
            if max_seconds is not None:
                remaining = max(float(max_seconds), .1) - (
                    time.monotonic() - started
                )
                if remaining <= 0:
                    break
            image = pdf[index].render(
                scale=max(min(float(render_scale), 3.0), .75),
                grayscale=True,
            ).to_pil().convert("L")
            try:
                try:
                    data = pytesseract.image_to_data(
                        image, lang=lang, config=config,
                        output_type=pytesseract.Output.DICT,
                        **({"timeout": max(remaining, .1)}
                           if remaining is not None else {}),
                    )
                except RuntimeError:
                    break
            finally:
                image.close()
            page_texts.append("\n".join(_lines_from_tsv(data)))
        return page_texts, total_pages, len(page_texts) == selected_pages
    except Exception:
        logger.warning("[c6_ocr] falha no OCR paginado.", exc_info=True)
        return [], 0, False


def _resolve_lang() -> str:
    """Usa 'por' se disponível; senão 'eng'. Resolvido uma vez por processo."""
    global _ocr_lang
    if _ocr_lang is not None:
        return _ocr_lang
    with _lang_lock:
        if _ocr_lang is None:
            import pytesseract

            try:
                langs = set(pytesseract.get_languages(config=""))
            except Exception:
                langs = set()
            _ocr_lang = "por" if "por" in langs else "eng"
    return _ocr_lang


def _normalize(text: str) -> str:
    # "R$" lido com erro de OCR (RS, Rs, R5) -> "R$".
    text = re.sub(r"\bR[\$S5s]", "R$", text)
    # Vírgula decimal lida como ponto: R$2.000.00 -> R$2.000,00
    # (troca o ponto que antecede exatamente os 2 dígitos finais do valor).
    text = re.sub(r"(R\$\s?[\d.]*\d)\.(\d{2})(?!\d)", r"\1,\2", text)
    return text


def _lines_from_tsv(data: dict) -> list[str]:
    """Reconstrói linhas a partir do image_to_data do Tesseract, agrupando
    palavras por (block, par, line) e ordenando por posição horizontal."""
    n = len(data.get("text", []))
    groups: "dict[tuple, list[tuple[int, str]]]" = {}
    for i in range(n):
        word = (data["text"][i] or "").strip()
        if not word:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if conf < 0:  # -1 = bloco sem texto reconhecido
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        groups.setdefault(key, []).append((int(data["left"][i]), word))

    lines: list[str] = []
    for key in sorted(groups.keys()):
        words = [w for _, w in sorted(groups[key], key=lambda t: t[0])]
        if words:
            lines.append(_normalize(" ".join(words)))
    return lines


def ocr_extract_text(file_bytes: bytes) -> tuple[str, int]:
    """Renderiza o PDF e roda OCR. Devolve (texto, n_paginas).

    Devolve ("", 0) se as dependências de OCR não estiverem disponíveis ou se
    ocorrer qualquer erro — nunca levanta exceção para o chamador.
    """
    if not file_bytes:
        return "", 0

    key = hashlib.sha256(file_bytes).hexdigest()
    cached = _text_cache.get(key)
    if cached is not None:
        return cached

    if not ocr_available():
        logger.info("[c6_ocr] dependências de OCR ausentes; fallback indisponível.")
        return "", 0

    try:
        import pypdfium2 as pdfium
        import pytesseract

        lang = _resolve_lang()
        # psm 4 (coluna única de texto) + oem 1 (LSTM) deram o menor erro neste
        # extrato — medido por reconciliação contra os totais mensais impressos.
        config = "--psm 4 --oem 1"
        pdf = pdfium.PdfDocument(io.BytesIO(file_bytes))
        n_pages = len(pdf)
        page_texts: list[str] = []
        for i in range(n_pages):
            # Tons de cinza ajudam o Tesseract neste extrato (texto cinza-claro).
            img = pdf[i].render(scale=_RENDER_SCALE, grayscale=True).to_pil().convert("L")
            try:
                data = pytesseract.image_to_data(
                    img, lang=lang, config=config,
                    output_type=pytesseract.Output.DICT,
                )
            finally:
                img.close()
            lines = _lines_from_tsv(data)
            if lines:
                page_texts.append("\n".join(lines))
        text_value = "\n".join(page_texts)
    except Exception:
        logger.warning("[c6_ocr] falha no OCR do extrato.", exc_info=True)
        return "", 0

    out = (text_value, n_pages)
    if len(_text_cache) >= _CACHE_MAX:
        _text_cache.clear()
    _text_cache[key] = out
    logger.info("[c6_ocr] OCR concluído: %s páginas, %s caracteres (lang=%s).",
                n_pages, len(text_value), _ocr_lang)
    return out
