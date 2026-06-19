import data_pipeline.jobs.audit_and_heal as job
from core.data_healing import resolve_field


def _resolutions():
    # UGPA3: margem 190% corrigida; ROE corroborado (mantido); P/L divergência não resolvida
    ugpa = [
        resolve_field("Margem_Liquida", bd=1.904, fundamentus=0.021, status_invest=0.022),
        resolve_field("ROE", bd=0.18, fundamentus=0.182, status_invest=0.181),
        resolve_field("P/L", bd=6.0, fundamentus=12.0, status_invest=None),
    ]
    # PETR3: DY=0 preenchido
    petr = [
        resolve_field("DY", bd=0.0, fundamentus=0.069, status_invest=0.068),
    ]
    return {"UGPA3": ugpa, "PETR3": petr}


def test_summarize_counts_corrections_and_divergences():
    m = job.summarize(_resolutions(), gravados=0, ciclo_reiniciado=True)
    assert m["empresas_verificadas"] == 2
    # Margem (corrigir) + DY (atualizar) = 2 campos graváveis em 2 empresas
    assert m["campos_atualizados"] == 2
    assert m["empresas_corrigidas"] == 2
    # P/L da UGPA3 é divergência não resolvida
    assert m["divergencias"] == 1
    assert m["ciclo_reiniciado"] is True
    assert "Fundamentus" in m["fontes"]


def test_summarize_uses_gravados_when_applied():
    m = job.summarize(_resolutions(), gravados=5)
    assert m["campos_atualizados"] == 5  # quando aplicado, usa o real gravado


def test_build_score_rows_per_field():
    rows = job.build_score_rows(_resolutions())
    keys = {(r["ticker"], r["indicador"]) for r in rows}
    assert ("UGPA3", "Margem_Liquida") in keys
    assert ("PETR3", "DY") in keys
    for r in rows:
        assert 0.0 <= r["score"] <= 100.0


def test_apply_enabled_env(monkeypatch):
    monkeypatch.delenv("AUDIT_HEAL_APPLY", raising=False)
    assert job._apply_enabled(None) is False          # default dry-run
    assert job._apply_enabled(True) is True
    monkeypatch.setenv("AUDIT_HEAL_APPLY", "true")
    assert job._apply_enabled(None) is True
