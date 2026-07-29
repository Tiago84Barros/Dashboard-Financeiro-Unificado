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


# ── teto por CLASSE (cíclicos) — recusa em vez de distorcer ─────────────────

def test_classe_recusa_quando_nao_ha_capacidade_fora_dela():
    """Caso real (28/07/2026): 5 cíclicos e 1 não-cíclico. Para ter 60% de teto
    cíclico, ITSA4 sozinha teria que carregar 40% — acima do teto de 35% por
    ativo. Trocar concentração de fator por concentração num nome é piorar."""
    from core.portfolio_constraints import project_class_capped

    pesos = {"WEGE3": .175, "PETR4": .175, "LEVE3": .175,
             "BRAP3": .15, "UNIP6": .15, "ITSA4": .175}
    ciclico = {"WEGE3": True, "PETR4": True, "LEVE3": True,
               "BRAP3": True, "UNIP6": True, "ITSA4": False}
    out, avisos = project_class_capped(pesos, ciclico, cap=0.35, class_cap=0.60)

    assert out == pytest.approx(pesos)          # pesos INTACTOS
    assert avisos and "não pôde ser aplicado" in avisos[0]
    assert "ao menos 2 ativos fora da classe" in avisos[0]
    assert "nenhuma distorção" in avisos[0]


def test_classe_aplica_quando_ha_capacidade():
    from core.portfolio_constraints import project_class_capped

    pesos = {"C1": .25, "C2": .25, "D1": .25, "D2": .25}
    ciclico = {"C1": True, "C2": True, "D1": False, "D2": False}
    out, avisos = project_class_capped(pesos, ciclico, cap=0.35, class_cap=0.40)

    assert out["C1"] + out["C2"] == pytest.approx(0.40, abs=1e-6)
    assert max(out.values()) <= 0.35 + 1e-6     # teto por ativo respeitado
    assert sum(out.values()) == pytest.approx(1.0)
    assert not avisos


def test_classe_ja_dentro_do_teto_nao_mexe_nos_pesos():
    from core.portfolio_constraints import project_class_capped

    pesos = {"C1": .30, "D1": .35, "D2": .35}
    out, avisos = project_class_capped(pesos, {"C1": True}, cap=0.40, class_cap=0.50)
    assert out == pytest.approx(pesos) and not avisos


def test_classe_sem_ativos_marcados_nao_faz_nada():
    from core.portfolio_constraints import project_class_capped

    pesos = {"A": .5, "B": .5}
    out, avisos = project_class_capped(pesos, {}, cap=0.6, class_cap=0.3)
    assert out == pytest.approx(pesos) and not avisos


# ── harness de auditoria: inconclusivo ≠ vazio ───────────────────────────────

def test_execucao_incompleta_e_inconclusiva_nao_determinismo_quebrado():
    """Aprendizado de 29/07/2026: um timeout devolveu carteira vazia e quase
    passou por defeito de determinismo. Ausência de resultado não é evidência."""
    from scripts.audit_portfolio_b3 import Resultado, verificar_determinismo

    completa = Resultado(config="base")
    completa.carteira = [{"tk": "A3", "peso": 0.5}, {"tk": "B3", "peso": 0.5}]
    vazia = Resultado(config="base")           # execução que não concluiu

    falhas = verificar_determinismo(completa, vazia)
    assert falhas, "divergência entre completa e vazia precisa ser reportada"


def test_determinismo_aceita_execucoes_identicas():
    from scripts.audit_portfolio_b3 import Resultado, verificar_determinismo

    def _r():
        r = Resultado(config="base")
        r.carteira = [{"tk": "B3", "peso": 0.5}, {"tk": "A3", "peso": 0.5}]
        return r

    assert verificar_determinismo(_r(), _r()) == []


# ── tetos aplicados EM CONJUNTO (defeito da varredura de 29/07/2026) ────────

def test_teto_de_classe_nao_desfaz_o_teto_setorial_em_silencio():
    """A varredura automatizada flagrou: com teto setorial 25% e de ciclo 50%,
    'Utilidade Pública' terminava com 25,2% — a redistribuição da classe
    cíclica empurrava peso de volta para um setor defensivo já no limite,
    desfazendo o primeiro teto sem avisar."""
    from core.portfolio_constraints import project_dual_capped

    pesos = {f"C{i}": 0.10 for i in range(5)}          # 50% cíclico
    pesos.update({"U1": 0.20, "U2": 0.20, "F1": 0.10})  # utilities + financeiro
    setores = {**{f"C{i}": "Materiais Básicos" for i in range(5)},
               "U1": "Utilidade Pública", "U2": "Utilidade Pública",
               "F1": "Financeiro"}
    ciclico = {**{f"C{i}": True for i in range(5)},
               "U1": False, "U2": False, "F1": False}

    out, avisos = project_dual_capped(pesos, setores, ciclico, cap=0.35,
                                      group_cap=0.25, class_cap=0.50)

    assert sum(out.values()) == pytest.approx(1.0)
    por_setor: dict[str, float] = {}
    for ticker, peso in out.items():
        por_setor[setores[ticker]] = por_setor.get(setores[ticker], 0.0) + peso
    violados = {s: p for s, p in por_setor.items() if p > 0.25 + 1e-4}
    # ou respeita o teto, ou declara o conflito — nunca viola em silêncio
    assert not violados or avisos, f"violação silenciosa: {violados}"


def test_tetos_compativeis_convergem_sem_aviso():
    from core.portfolio_constraints import project_dual_capped

    pesos = {"C1": .25, "C2": .25, "D1": .25, "D2": .25}
    setores = {"C1": "Materiais Básicos", "C2": "Bens Industriais",
               "D1": "Utilidade Pública", "D2": "Saúde"}
    ciclico = {"C1": True, "C2": True, "D1": False, "D2": False}
    out, avisos = project_dual_capped(pesos, setores, ciclico, cap=0.35,
                                      group_cap=0.40, class_cap=0.60)
    assert sum(out.values()) == pytest.approx(1.0)
    assert max(out.values()) <= 0.35 + 1e-6
    assert not avisos


def test_conflito_entre_tetos_e_declarado_com_explicacao():
    """Teto de classe muito baixo + teto setorial apertado = incompatíveis."""
    from core.portfolio_constraints import project_dual_capped

    pesos = {"C1": .34, "C2": .33, "D1": .33}
    setores = {"C1": "Materiais Básicos", "C2": "Bens Industriais",
               "D1": "Utilidade Pública"}
    ciclico = {"C1": True, "C2": True, "D1": False}
    out, avisos = project_dual_capped(pesos, setores, ciclico, cap=0.35,
                                      group_cap=0.25, class_cap=0.30)
    assert sum(out.values()) == pytest.approx(1.0)
    assert avisos, "conflito precisa ser declarado"
    assert any("Afrouxe um dos dois" in a or "não foi alcançado" in a
               for a in avisos)
