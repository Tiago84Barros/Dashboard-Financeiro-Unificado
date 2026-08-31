"""Falha de leitura da vitrine de FIIs não pode virar reprovação dos fundos.

Em 31/08/2026 a tela publicada mostrava "0 de 394 FIIs passaram pelos filtros"
e mandava relaxar a elegibilidade. Nenhum filtro havia reprovado: a vitrine de
26/08 estourou o limite de 4 dias, o carregador devolveu as linhas cruas sem
expandir as métricas, e cada fundo foi excluído por ausência de liquidez, DY,
P/VP, histórico e drawdown.
"""
import pandas as pd
import pytest

import core.market_read as market_read
import views.fiis as fiis
from core.fii_integrated_model import (
    IntegratedEligibilityPolicy,
    apply_integrated_eligibility,
)


def test_falha_de_leitura_nao_devolve_linhas_cruas(monkeypatch):
    cru = pd.DataFrame([
        {"ticker": f"AAAA{i}11", "payload_json": {"dy_12m": .10},
         "as_of_date": "2026-08-26", "schema_version": "fii_selection_inputs.v2"}
        for i in range(5)
    ])
    cru.attrs.update({"load_error": "snapshot_stale", "snapshot_age_days": 5,
                      "snapshot_as_of": "2026-08-26"})
    monkeypatch.setattr(market_read, "_load_fii_selection_snapshot", lambda *a, **k: cru)
    market_read._load_fii_methodology_inputs_cached.clear()

    frame = market_read._load_fii_methodology_inputs_cached(prefer_snapshot=True)

    # Vazio: quem só checa `.empty` não pode consumir linhas sem métrica.
    assert frame.empty
    assert frame.attrs["load_error"] == "snapshot_stale"
    assert frame.attrs["snapshot_age_days"] == 5


def test_vitrine_envelhecida_ainda_entrega_as_metricas():
    """Idade vira aviso; as métricas continuam sendo lidas."""
    hard = market_read._FII_SNAPSHOT_HARD_MAX_AGE_DAYS
    soft = market_read._FII_SNAPSHOT_MAX_AGE_DAYS
    assert hard > soft, "sem banda entre alvo e limite, envelhecer = apagar o dado"


def test_ausencia_generalizada_nao_e_diagnosticada_como_filtro_apertado():
    sem_metrica = [{"ticker": f"AAAA{i}11"} for i in range(20)]
    _, relatorio = apply_integrated_eligibility(
        sem_metrica, IntegratedEligibilityPolicy())
    assert relatorio["eligible_count"] == 0
    mensagem = fiis._mensagem_de_universo_vazio(relatorio)
    assert "dado" in mensagem
    assert "Relaxe os filtros" not in mensagem


def test_reprovacao_real_continua_pedindo_para_relaxar_os_filtros():
    reprovados = [
        {"ticker": f"AAAA{i}11", "liquidez_diaria": 1_000.0, "dy_12m": .01,
         "pvp": .90, "history_months": 60, "max_drawdown": .10}
        for i in range(20)
    ]
    _, relatorio = apply_integrated_eligibility(
        reprovados, IntegratedEligibilityPolicy())
    assert relatorio["eligible_count"] == 0
    assert "Relaxe os filtros" in fiis._mensagem_de_universo_vazio(relatorio)


@pytest.mark.parametrize("erro,trecho", [
    ("snapshot_stale", "prazo de validade"),
    ("database_unavailable", "banco não respondeu"),
])
def test_tela_atribui_a_falha_a_leitura_e_nao_aos_criterios(monkeypatch, erro, trecho):
    capturado: list[str] = []
    monkeypatch.setattr(fiis.st, "markdown", lambda html, **k: capturado.append(str(html)))
    frame = pd.DataFrame()
    frame.attrs.update({"load_error": erro, "snapshot_as_of": "2026-08-26",
                        "snapshot_age_days": 5})

    assert fiis._falha_de_leitura_da_vitrine(frame) is True
    texto = " ".join(capturado)
    assert trecho in texto
    assert "não relaxe os critérios" in texto
    assert "nenhum filtro reprovou nada" in texto
