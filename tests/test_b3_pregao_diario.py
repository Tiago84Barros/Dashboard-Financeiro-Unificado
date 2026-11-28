from datetime import date

import pytest

from data_pipeline.market import b3_precos


def test_fita_diaria_recusa_zip_com_outro_pregao(monkeypatch):
    monkeypatch.setattr(b3_precos.b3_cotahist, "ler_linhas",
                        lambda _zip: [{"trade_date": "2026-11-25", "close": 12}])
    with pytest.raises(ValueError, match="26"):
        b3_precos.linhas_do_pregao(b"pacote", date(2026, 11, 26))


def test_fita_diaria_preserva_close_unitario(monkeypatch):
    monkeypatch.setattr(b3_precos.b3_cotahist, "ler_linhas", lambda _zip: [
        {"trade_date": "2026-11-26", "close": 430, "fator_cotacao": 100},
    ])
    rows = b3_precos.linhas_do_pregao(b"pacote", date(2026, 11, 26))
    assert rows[0]["close_unitario"] == 4.3

