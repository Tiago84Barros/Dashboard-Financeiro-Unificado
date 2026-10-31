"""Interface: o resultado quantitativo declara a ORIGEM da disponibilidade.

Achado A-002 (2026-08): o backtest da aba Empresas B3 roda hoje com 100 % das
vintages em `migration_baseline` — a data de disponibilidade é MODELADA pelo
prazo legal da CVM, não medida. Enquanto for assim, o resultado não pode ser
apresentado como "backtest point-in-time validado".

Estes testes fixam o contrato de interface: cobertura point-in-time exposta em
`attrs`, rótulo coerente com a cobertura, risco residual de restatement nomeado
e informação renderizada em card CSS (padrão do app), nunca solta.

Os produtores daqui (backtest e Rank-IC) saíram com a Análise Avançada em
30/10/2026. O produtor que restou, o motor da Criação de Portfólio, é coberto
por tests/test_portfolio_b3_pit_coverage.py.
"""
from __future__ import annotations

import views.empresas_b3 as b3
from views.empresas_b3 import (
    PITCoverage,
    _pit_card_html,
    _pit_rotulo_resultado,
)

# ── Rótulo (função pura) ─────────────────────────────────────────────────────

def test_cobertura_medida_zero_nao_pode_ser_chamada_de_point_in_time_validado():
    info = _pit_rotulo_resultado(PITCoverage(snapshots_modelados=12, decisoes=4))
    assert info["nivel"] == "modelada"
    assert info["cobertura_pct"] == "0%"
    assert "SIMULAÇÃO" in info["titulo"]
    assert "MODELADA" in info["titulo"]
    assert "não é backtest point-in-time validado" in info["titulo"]
    assert "prazo" in info["detalhe"].lower()


def test_rotulo_de_cobertura_total_e_de_cobertura_parcial():
    total = _pit_rotulo_resultado(PITCoverage(snapshots_medidos=8, decisoes=2))
    assert total["nivel"] == "medida"
    assert total["cobertura_pct"] == "100%"
    assert "point-in-time validado" in total["titulo"]
    assert "SIMULAÇÃO" not in total["titulo"]

    parcial = _pit_rotulo_resultado(
        PITCoverage(snapshots_medidos=1, snapshots_modelados=3, decisoes=2))
    assert parcial["nivel"] == "mista"
    assert parcial["cobertura_pct"] == "25%"
    assert "PARCIALMENTE" in parcial["titulo"]


def test_risco_de_restatement_e_nomeado_em_qualquer_nivel():
    for cov in (PITCoverage(snapshots_medidos=3),
                PITCoverage(snapshots_modelados=3),
                PITCoverage()):
        risco = _pit_rotulo_resultado(cov)["risco"]
        assert "metric_value" in risco
        assert "restatement" in risco.lower()
        assert "reapresent" in risco.lower()


def test_contexto_entra_no_rotulo():
    assert "Rank-IC" in _pit_rotulo_resultado(
        PITCoverage(snapshots_modelados=2), "Rank-IC")["titulo"]


# ── Card CSS (padrão do app: nada de informação solta) ───────────────────────

def test_card_usa_css_do_app_e_expoe_cobertura_e_risco():
    html_card = _pit_card_html(
        PITCoverage(snapshots_medidos=1, snapshots_modelados=3,
                    linhas_barradas_vintage=2, linhas_barradas_prazo=5,
                    decisoes=2),
        "Backtest",
    )
    assert 'class="b3-pit-card"' in html_card
    assert 'class="b3-ind-label"' in html_card
    assert "25% medida" in html_card
    assert "1/4" in html_card                      # snapshots medidos/usados
    assert "metric_value" in html_card
    assert "restatement" in html_card.lower()


def test_css_do_card_esta_declarado_no_modulo():
    assert ".b3-pit-card" in b3._CSS
    assert ".b3-pit-title" in b3._CSS
