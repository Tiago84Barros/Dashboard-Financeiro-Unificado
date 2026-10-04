from datetime import date

import pytest

from core.investimentos import crescimento_patrimonio, crescimento_proventos


def _s(mes, vm, label=None):
    return {"mes_str": mes, "valor_mercado": vm, "label": label or mes}


def test_patrimonio_dobra_em_dois_anos():
    c = crescimento_patrimonio([_s("2024-10", 100.0), _s("2025-10", 150.0), _s("2026-10", 200.0)])
    assert c["taxa"] == pytest.approx(2 ** 0.5 - 1)
    assert c["anos"] == pytest.approx(2.0)
    assert (c["de"], c["ate"]) == ("2024-10", "2026-10")


def test_patrimonio_ignora_foto_inicial_zerada():
    c = crescimento_patrimonio([_s("2023-10", 0.0), _s("2024-10", 100.0), _s("2025-10", 110.0)])
    assert c["de"] == "2024-10"
    assert c["taxa"] == pytest.approx(0.10)


def test_patrimonio_com_menos_de_um_ano_nao_anualiza():
    assert crescimento_patrimonio([_s("2026-01", 100.0), _s("2026-10", 200.0)]) is None
    assert crescimento_patrimonio([_s("2026-10", 100.0)]) is None
    assert crescimento_patrimonio([]) is None


def _a(ano, total):
    return {"ano": ano, "total": total}


def test_proventos_usa_so_anos_completos():
    hist = [_a(2019, 5.0), _a(2020, 100.0), _a(2021, 150.0), _a(2022, 400.0), _a(2026, 999.0)]
    # 2019 começou em dezembro (parcial) e 2026 é o ano corrente: ficam de fora.
    c = crescimento_proventos(hist, date(2019, 12, 10), hoje=date(2026, 10, 4))
    assert (c["de"], c["ate"]) == (2020, 2022)
    assert c["taxa"] == pytest.approx(1.0)


def test_proventos_mantem_primeiro_ano_iniciado_em_janeiro():
    c = crescimento_proventos([_a(2024, 100.0), _a(2025, 121.0)], date(2024, 1, 15),
                              hoje=date(2026, 10, 4))
    assert c["de"] == 2024
    assert c["taxa"] == pytest.approx(0.21)


def test_proventos_sem_dois_anos_completos():
    assert crescimento_proventos([_a(2025, 100.0), _a(2026, 50.0)], date(2025, 1, 2),
                                 hoje=date(2026, 10, 4)) is None
    assert crescimento_proventos([], None) is None
