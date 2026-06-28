"""Saneamento de NaN/Infinity ao serializar metrics_json/params_json (JSONB)."""
import json

import numpy as np

from core.b3_portfolio_model import _clean_nan, _safe_json


def test_safe_json_saneia_nan_inf_e_e_valido():
    # regressão: alpha_selic_medio=NaN quebrava o INSERT (psycopg2 InvalidTextRepresentation)
    m = {"num_empresas": 9, "alpha_selic_medio": float("nan"),
         "inf": float("inf"), "neg_inf": float("-inf"), "ok": 59.89}
    out = _safe_json(m, {})
    d = json.loads(out)  # não deve levantar — JSON válido
    assert d["alpha_selic_medio"] is None
    assert d["inf"] is None and d["neg_inf"] is None
    assert d["ok"] == 59.89


def test_clean_nan_preserva_tipos():
    src = {"i": 9, "npi": np.int64(11), "f": 1.5, "npf": np.float64(2.5),
           "b": True, "s": "x", "none": None,
           "nan": np.float64("nan"), "lst": [1, float("nan"), 3]}
    out = _clean_nan(src)
    assert out["i"] == 9 and isinstance(out["i"], int)
    assert out["npi"] == 11 and isinstance(out["npi"], int)   # int preservado (não vira 11.0)
    assert out["f"] == 1.5 and out["npf"] == 2.5
    assert out["b"] is True                                    # bool não vira 1
    assert out["s"] == "x" and out["none"] is None
    assert out["nan"] is None
    assert out["lst"] == [1, None, 3]


def test_safe_json_tipos_nao_nativos_viram_string():
    # default=str: datas/objetos não-JSON viram string, mantendo o JSON válido
    import datetime as dt
    out = _safe_json({"d": dt.date(2026, 6, 28)}, {})
    assert json.loads(out)["d"] == "2026-06-28"
