"""Normalização dos itens da carteira-modelo americana antes do INSERT.

Os itens chegam de ``DataFrame.to_dict("records")``: célula ausente é float NaN,
não None. Sem saneamento, NaN vira a string "nan" numa coluna VARCHAR e quebra o
JSONB — o mesmo defeito já coberto para a B3 em test_b3_portfolio_model.py.
"""
import json

import numpy as np

from core.us_portfolio_model import (
    _clean_nan,
    _normalize_weight,
    _plan_hash,
    _safe_json,
    _symbol_of,
    _text,
)


def _items() -> list[dict]:
    return [
        {"symbol": "AAPL", "name": "Apple Inc.", "sector_group": "Tecnologia",
         "industry_group": "Hardware", "weight": 0.6, "entry_score": 71.5},
        {"symbol": "msft", "name": "Microsoft", "sector_group": "Tecnologia",
         "industry_group": "Software", "weight": 0.4, "entry_score": 68.0},
    ]


def test_text_descarta_nan_e_vazio():
    assert _text(float("nan"), "Apple") == "Apple"
    assert _text(np.float64("nan"), None, "  ") is None
    assert _text("nan") is None
    assert _text(None, "", "Microsoft") == "Microsoft"


def test_symbol_of_normaliza_caixa_e_aceita_ticker():
    assert _symbol_of({"symbol": " msft "}) == "MSFT"
    assert _symbol_of({"ticker": "aapl"}) == "AAPL"
    assert _symbol_of({"symbol": float("nan"), "ticker": "nvda"}) == "NVDA"
    assert _symbol_of({}) == ""


def test_normalize_weight_soma_um():
    pesos = _normalize_weight(_items())
    assert set(pesos) == {"AAPL", "MSFT"}
    assert abs(sum(pesos.values()) - 1.0) < 1e-9


def test_normalize_weight_sem_pesos_cai_em_equal_weight():
    pesos = _normalize_weight([{"symbol": "A", "weight": 0}, {"symbol": "B"}])
    assert pesos == {"A": 0.5, "B": 0.5}


def test_normalize_weight_ignora_nan():
    pesos = _normalize_weight([{"symbol": "A", "weight": float("nan")},
                               {"symbol": "B", "weight": 1.0}])
    assert pesos["B"] == 1.0


def test_plan_hash_e_deterministico_e_independente_da_ordem():
    itens = _items()
    assert _plan_hash(itens, {"top_n": 20}) == _plan_hash(itens[::-1], {"top_n": 20})
    assert _plan_hash(itens, {"top_n": 20}) != _plan_hash(itens, {"top_n": 25})


def test_safe_json_saneia_nan_e_infinito():
    saida = json.loads(_safe_json(
        {"entry_score": float("nan"), "inf": float("inf"), "ok": 71.5}, {}))
    assert saida["entry_score"] is None
    assert saida["inf"] is None
    assert saida["ok"] == 71.5


def test_clean_nan_preserva_tipos():
    saida = _clean_nan({"i": np.int64(7), "b": True, "f": np.float64(2.5),
                        "nan": np.float64("nan")})
    assert saida["i"] == 7 and isinstance(saida["i"], int)
    assert saida["b"] is True
    assert saida["f"] == 2.5
    assert saida["nan"] is None
