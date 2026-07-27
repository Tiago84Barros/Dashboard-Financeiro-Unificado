"""Teto por setor na carteira — a proteção que só o B3 não tinha (puro).

Ancorado na carteira real de 27/07/2026: WEGE3 · BRAP3 · LEVE3 · UNIP6, quatro
segmentos distintos e apenas dois setores, 100% cíclicos.
"""
from __future__ import annotations

import pytest

from core.portfolio_constraints import (
    project_sector_capped, sector_cap_feasibility,
)


def _pesos(*tickers: str) -> dict[str, float]:
    return {t: 1.0 / len(tickers) for t in tickers}


# ── viabilidade ──────────────────────────────────────────────────────────────

def test_dois_setores_nao_comportam_teto_de_30_pct():
    viavel, motivo = sector_cap_feasibility({"A": 2, "B": 2}, cap=0.35,
                                            group_cap=0.30)
    assert viavel is False
    assert "comporta no máximo 60%" in motivo


def test_quatro_setores_com_teto_de_30_pct_e_viavel():
    viavel, motivo = sector_cap_feasibility({"A": 1, "B": 1, "C": 1, "D": 1},
                                            cap=0.35, group_cap=0.30)
    assert viavel is True and motivo == ""


def test_capacidade_limitada_pelo_teto_do_ativo_nao_so_do_grupo():
    # 1 ativo por setor com cap de 20% → capacidade do setor é 20%, não 40%
    viavel, motivo = sector_cap_feasibility({"A": 1, "B": 1, "C": 1}, cap=0.20,
                                            group_cap=0.40)
    assert viavel is False
    assert "60%" in motivo


# ── projeção ─────────────────────────────────────────────────────────────────

def test_setor_concentrado_e_reduzido_ao_teto():
    pesos = _pesos("A1", "A2", "A3", "B1")        # 75% no setor A
    setores = {"A1": "Cíclico", "A2": "Cíclico", "A3": "Cíclico", "B1": "Defensivo"}
    out, avisos = project_sector_capped(pesos, setores, cap=0.40, group_cap=0.60)
    peso_a = sum(out[t] for t in ("A1", "A2", "A3"))
    assert peso_a == pytest.approx(0.60, abs=1e-6)
    assert out["B1"] == pytest.approx(0.40, abs=1e-6)
    assert sum(out.values()) == pytest.approx(1.0)
    assert not avisos


def test_respeita_os_dois_tetos_simultaneamente():
    pesos = {"A1": 0.5, "A2": 0.2, "B1": 0.2, "C1": 0.1}
    setores = {"A1": "S1", "A2": "S1", "B1": "S2", "C1": "S3"}
    out, _ = project_sector_capped(pesos, setores, cap=0.30, group_cap=0.50)
    assert max(out.values()) <= 0.30 + 1e-6
    assert sum(out[t] for t in ("A1", "A2")) <= 0.50 + 1e-6
    assert sum(out.values()) == pytest.approx(1.0)


def test_carteira_toda_ciclica_recebe_aviso_explicito():
    """O caso real: 2 setores, ambos cíclicos, teto de 30% é impossível."""
    pesos = _pesos("WEGE3", "LEVE3", "BRAP3", "UNIP6")
    setores = {"WEGE3": "Bens Industriais", "LEVE3": "Consumo Cíclico",
               "BRAP3": "Materiais Básicos", "UNIP6": "Materiais Básicos"}
    out, avisos = project_sector_capped(pesos, setores, cap=0.35, group_cap=0.30)
    assert sum(out.values()) == pytest.approx(1.0)
    assert avisos, "inviabilidade precisa ser reportada, nunca silenciosa"
    assert any("comporta no máximo" in a for a in avisos)


def test_nunca_viola_em_silencio():
    """Se sobrar violação, ela aparece na lista de avisos."""
    pesos = _pesos("A1", "A2")
    setores = {"A1": "S1", "A2": "S1"}          # setor único
    out, avisos = project_sector_capped(pesos, setores, cap=0.60, group_cap=0.50)
    peso_setor = out["A1"] + out["A2"]
    if peso_setor > 0.50 + 1e-6:
        assert any("ficou com" in a or "comporta no máximo" in a for a in avisos)


def test_ativo_sem_setor_nao_cria_grupo_concentrado_artificial():
    pesos = _pesos("A1", "X1", "X2")
    setores = {"A1": "S1"}                       # X1 e X2 sem setor
    out, avisos = project_sector_capped(pesos, setores, cap=0.50, group_cap=0.40)
    assert sum(out.values()) == pytest.approx(1.0)
    # cada sem-setor vira grupo próprio: não são somados num "setor None"
    assert not any("None" in a for a in avisos)


def test_pesos_ja_conformes_ficam_praticamente_intactos():
    pesos = {"A1": 0.30, "B1": 0.30, "C1": 0.40}
    setores = {"A1": "S1", "B1": "S2", "C1": "S3"}
    out, avisos = project_sector_capped(pesos, setores, cap=0.40, group_cap=0.40)
    for ticker, peso in pesos.items():
        assert out[ticker] == pytest.approx(peso, abs=1e-6)
    assert not avisos


def test_entrada_vazia_nao_quebra():
    out, avisos = project_sector_capped({}, {}, cap=0.3, group_cap=0.3)
    assert out == {} and avisos == []
