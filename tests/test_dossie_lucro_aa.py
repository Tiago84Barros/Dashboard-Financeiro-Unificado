"""Variação a/a do lucro com prejuízo na base (core/dossie_b3).

``ult / ant - 1`` inverte o sentido quando ``ant`` é prejuízo: de -100 para
-50 dava -50% e o dossiê dizia "lucro caiu 50%"; a virada de -100 para +50
dava -150%. Nada aqui toca banco: ``_rows`` é trocado por linhas sintéticas.
"""
from __future__ import annotations

import pytest

import core.dossie_b3 as dossie
from core.dossie_b3 import _checks, variacao_lucro_aa


@pytest.mark.parametrize("ant, ult, pct, leitura", [
    (100.0, 50.0, -50.0, None),
    (100.0, 130.0, 30.0, None),
    (100.0, -20.0, -120.0, "virou_prejuizo"),
    (-100.0, -50.0, None, "prejuizo_reduzido"),
    (-100.0, 0.0, None, "prejuizo_reduzido"),
    (-100.0, 50.0, None, "virada_para_lucro"),
    (-50.0, -100.0, None, "prejuizo_ampliado"),
    (-50.0, -50.0, None, "prejuizo_estavel"),
    (0.0, 10.0, None, "base_zero"),
    (None, 10.0, None, None),
    (10.0, None, None, None),
])
def test_variacao_lucro_aa(ant, ult, pct, leitura):
    assert variacao_lucro_aa(ant, ult) == (pct, leitura)


def _linhas(lucros, receitas=None):
    """Cinco trimestres consecutivos 2025T1..2026T1, mais recente primeiro."""
    receitas = receitas or [1_000e6] * 5
    trimestres = [(2025, 1), (2025, 2), (2025, 3), (2025, 4), (2026, 1)]
    linhas = [{"year": a, "quarter": q, "revenue": r, "net_income": lucro}
              for (a, q), r, lucro in zip(trimestres, receitas, lucros)]
    return linhas[::-1]


def _yoy(monkeypatch, lucros, receitas=None):
    monkeypatch.setattr(dossie, "_rows", lambda *a, **k: _linhas(lucros, receitas))
    return dossie._trimestres("TEST3")["yoy"]


def test_prejuizo_reduzido_nao_vira_queda_de_lucro(monkeypatch):
    yoy = _yoy(monkeypatch, [-100e6, 1e6, 1e6, 1e6, -50e6])
    assert "lucro_yoy_pct" not in yoy
    assert yoy["lucro_yoy_leitura"] == "prejuizo_reduzido"
    flags = _checks([], {"yoy": yoy}, {}, {}, {"n_docs": 3}, {})
    assert not any("MOMENTUM" in f for f in flags)


def test_virada_para_lucro_nao_vira_queda(monkeypatch):
    yoy = _yoy(monkeypatch, [-100e6, 1e6, 1e6, 1e6, 50e6])
    assert "lucro_yoy_pct" not in yoy
    assert yoy["lucro_yoy_leitura"] == "virada_para_lucro"
    flags = _checks([], {"yoy": yoy}, {}, {}, {"n_docs": 3}, {})
    assert not any("MOMENTUM" in f for f in flags)


def test_lucro_que_vira_prejuizo_e_nomeado(monkeypatch):
    yoy = _yoy(monkeypatch, [50e6, 1e6, 1e6, 1e6, -20e6])
    assert yoy["lucro_yoy_pct"] == -140.0
    flags = _checks([], {"yoy": yoy}, {}, {}, {"n_docs": 3}, {})
    momentum = [f for f in flags if "MOMENTUM" in f]
    assert len(momentum) == 1 and "virou prejuízo" in momentum[0]


def test_prejuizo_ampliado_e_sinal_de_momentum(monkeypatch):
    yoy = _yoy(monkeypatch, [-50e6, 1e6, 1e6, 1e6, -100e6])
    flags = _checks([], {"yoy": yoy}, {}, {}, {"n_docs": 3}, {})
    momentum = [f for f in flags if "MOMENTUM" in f]
    assert len(momentum) == 1 and "prejuízo" in momentum[0]


def test_queda_de_lucro_positivo_segue_sinalizada(monkeypatch):
    yoy = _yoy(monkeypatch, [50e6, 1e6, 1e6, 1e6, 25e6])
    assert yoy["lucro_yoy_pct"] == -50.0
    assert "lucro_yoy_leitura" not in yoy
    flags = _checks([], {"yoy": yoy}, {}, {}, {"n_docs": 3}, {})
    assert any("caiu -50.0%" in f for f in flags)


def test_receita_com_base_negativa_nao_tem_variacao(monkeypatch):
    yoy = _yoy(monkeypatch, [1e6] * 5, receitas=[-5e6, 1e6, 1e6, 1e6, 10e6])
    assert "receita_yoy_pct" not in yoy


def test_texto_do_dossie_nao_imprime_none(monkeypatch):
    yoy = _yoy(monkeypatch, [-100e6, 1e6, 1e6, 1e6, -50e6])
    texto = dossie.dossie_to_text({
        "ticker": "TEST3",
        "trimestres": {"serie": [{"ano": 2026, "tri": 1, "receita_mi": 1000.0,
                                  "lucro_mi": -50.0}], "yoy": yoy},
    })
    linha = next(x for x in texto.splitlines() if "Último tri a/a" in x)
    assert "None" not in linha
    assert "prejuízo menor" in linha
