"""Testes do fallback de OCR de extratos C6 (parte que não depende das libs)."""
from core.c6_ocr import _normalize


def test_normalize_fixes_currency_symbol_ocr_errors():
    assert _normalize("RS 50,00") == "R$ 50,00"
    assert _normalize("Rs1.200,00") == "R$1.200,00"


def test_normalize_fixes_decimal_dot_read_as_thousands():
    # Vírgula decimal lida como ponto pelo OCR.
    assert _normalize("-R$2.000.00") == "-R$2.000,00"
    assert _normalize("R$92.87") == "R$92,87"


def test_normalize_keeps_correct_brazilian_values():
    assert _normalize("R$ 18.084,75") == "R$ 18.084,75"
    assert _normalize("R$ 1.700,00") == "R$ 1.700,00"
    assert _normalize("-R$200,43") == "-R$200,43"
