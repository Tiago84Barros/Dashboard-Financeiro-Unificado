import json

import data_pipeline.quality.score as score
import data_pipeline.quality.report as report
import data_pipeline.quality.sanitizer as san
from core.data_healing import resolve_field


# ── score ─────────────────────────────────────────────────────────────────────

def test_score_perfect_when_three_sources_fresh_consistent():
    s = score.compute_field_score(n_sources_agree=3, age_days=0, hist_cv=0.0,
                                  n_validations=5, n_divergences=0)
    assert s == 100.0


def test_score_lower_with_single_source_and_old_data():
    s_single = score.compute_field_score(1, age_days=0, hist_cv=0.0)
    s_three = score.compute_field_score(3, age_days=0, hist_cv=0.0)
    assert s_single < s_three
    s_old = score.compute_field_score(3, age_days=365 * 3, hist_cv=0.0)
    assert s_old < s_three


def test_score_divergences_penalize():
    base = score.compute_field_score(3, age_days=0, hist_cv=0.0, n_divergences=0)
    pen = score.compute_field_score(3, age_days=0, hist_cv=0.0, n_divergences=4)
    assert pen < base


def test_score_bounded():
    assert 0.0 <= score.compute_field_score(0, age_days=99999, hist_cv=99, n_divergences=99) <= 100.0


# ── sanitizer (política) ───────────────────────────────────────────────────────

def test_sanitizer_maps_actions():
    # corrigido → corrigir (grava)
    r = resolve_field("Margem_Liquida", bd=1.904, fundamentus=0.021, status_invest=0.022)
    d = san.decide(r)
    assert d in (san.CORRIGIR, san.ATUALIZAR)
    assert san.is_write_decision(d)
    # banco corroborado → ignorar (não grava)
    r2 = resolve_field("ROE", bd=0.18, fundamentus=0.182, status_invest=None)
    assert san.decide(r2) == san.IGNORAR
    assert not san.is_write_decision(san.IGNORAR)
    # 1 fonte só → marcar revisão
    r3 = resolve_field("ROE", bd=0.18, fundamentus=None, status_invest=None)
    assert san.decide(r3) == san.MARCAR_REVISAO


# ── report ─────────────────────────────────────────────────────────────────────

def test_build_report_shape_and_sanitizes_error():
    rep = report.build_report({
        "empresas_verificadas": 50, "empresas_corrigidas": 3,
        "campos_atualizados": 7, "divergencias": 2,
        "fontes": ["Fundamentus", "StatusInvest"],
        "erro": "falha postgresql://user:pass@host/db timeout",
    }, run_ts="2026-06-19T00:00:00")
    assert rep["empresas_verificadas"] == 50
    assert rep["fontes"] == ["Fundamentus", "StatusInvest"]
    assert "postgresql://***" in rep["erro"]
    assert "user:pass" not in rep["erro"]
    # serializável
    json.dumps(rep, default=str)


def test_write_files_creates_json_csv(tmp_path):
    rep = report.build_report({"empresas_verificadas": 1, "fontes": ["X"]},
                              run_ts="2026-06-19T01:02:03")
    out = report.write_files(rep, out_dir=str(tmp_path))
    assert out["json"] and out["csv"]
    import os
    assert os.path.exists(out["json"]) and os.path.exists(out["csv"])
