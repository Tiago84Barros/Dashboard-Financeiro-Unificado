"""
Fallback de OCR para extratos C6 em PDF só-imagem.

Os extratos do C6 baixados via "Microsoft Print to PDF" saem rasterizados
(0 caracteres extraíveis, só imagens). Quando pdfplumber/pypdf não retornam
texto, este módulo renderiza as páginas e roda OCR (rapidocr-onnxruntime,
offline), devolvendo o texto em linhas — no mesmo formato que o parser C6
espera ("DD/MM DD/MM Tipo Descrição Valor").

Tudo aqui é opcional: se as dependências de OCR não estiverem instaladas,
`ocr_extract_text` devolve ("", 0) e o chamador mantém o comportamento atual.
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
import statistics
import threading

logger = logging.getLogger(__name__)

# Multiplicador de DPI na renderização. 3.0 dá boa precisão sem estourar memória.
_RENDER_SCALE = 3.0

# Singleton do motor de OCR (carrega os modelos ONNX uma vez por processo).
_ocr_engine = None
_ocr_lock = threading.Lock()

# Cache simples por hash do arquivo, para não rodar OCR duas vezes
# (a UI chama preview e, depois, a importação com o mesmo PDF).
_text_cache: "dict[str, tuple[str, int]]" = {}
_CACHE_MAX = 8


def ocr_available() -> bool:
    """True se as bibliotecas de OCR estiverem instaladas."""
    try:
        import pypdfium2  # noqa: F401
        import rapidocr_onnxruntime  # noqa: F401
    except Exception:
        return False
    return True


def _get_engine():
    global _ocr_engine
    if _ocr_engine is not None:
        return _ocr_engine
    with _ocr_lock:
        if _ocr_engine is None:
            from rapidocr_onnxruntime import RapidOCR
            _ocr_engine = RapidOCR()
    return _ocr_engine


def _normalize(text: str) -> str:
    # "R$" lido com erro de OCR (RS, Rs, R5) -> "R$".
    text = re.sub(r"\bR[\$S5s]", "R$", text)
    # Vírgula decimal lida como ponto: R$2.000.00 -> R$2.000,00
    # (troca o ponto que antecede exatamente os 2 dígitos finais do valor).
    text = re.sub(r"(R\$\s?[\d.]*\d)\.(\d{2})(?!\d)", r"\1,\2", text)
    return text


def _group_lines(result) -> list[str]:
    """Agrupa as caixas do OCR em linhas (por y). Se uma linha acabar com mais
    de um valor monetário (duas transações coladas), re-separa em sub-linhas."""
    items = []
    for box, text, _conf in result or []:
        if not text or not text.strip():
            continue
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        items.append({
            "x0": min(xs),
            "y0": min(ys), "y1": max(ys),
            "cy": (min(ys) + max(ys)) / 2,
            "h": max(ys) - min(ys),
            "t": text.strip(),
        })
    if not items:
        return []
    items.sort(key=lambda d: d["cy"])
    med_h = statistics.median(d["h"] for d in items)
    thr = med_h * 0.6

    raw_lines, cur = [], [items[0]]
    for it in items[1:]:
        if abs(it["cy"] - cur[-1]["cy"]) <= thr:
            cur.append(it)
        else:
            raw_lines.append(cur)
            cur = [it]
    raw_lines.append(cur)

    money = re.compile(r"R[\$S5s]\s?\d")

    def emit(ln) -> str:
        ln.sort(key=lambda d: d["x0"])
        return _normalize(" ".join(d["t"] for d in ln))

    out: list[str] = []
    for ln in raw_lines:
        if sum(1 for d in ln if money.search(d["t"])) <= 1:
            out.append(emit(ln))
            continue
        # Duas transações coladas numa linha: re-separa por gaps de y mais finos.
        sub = sorted(ln, key=lambda d: d["cy"])
        groups, cluster = [], [sub[0]]
        sthr = med_h * 0.35
        for it in sub[1:]:
            if abs(it["cy"] - cluster[-1]["cy"]) <= sthr:
                cluster.append(it)
            else:
                groups.append(cluster)
                cluster = [it]
        groups.append(cluster)
        out.extend(emit(g) for g in groups)
    return out


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

        ocr = _get_engine()
        pdf = pdfium.PdfDocument(io.BytesIO(file_bytes))
        n_pages = len(pdf)
        page_texts: list[str] = []
        for i in range(n_pages):
            img = pdf[i].render(scale=_RENDER_SCALE).to_pil().convert("RGB")
            try:
                import numpy as np

                result, _ = ocr(np.asarray(img))
            finally:
                img.close()
            lines = _group_lines(result)
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
    logger.info("[c6_ocr] OCR concluído: %s páginas, %s caracteres.",
                n_pages, len(text_value))
    return out
