"""Achados paralelos da auditoria (04/10/2026): macro anual e versão do FII."""
from __future__ import annotations

import inspect
from datetime import date, timedelta

from core import contexto_mercado as cm


def _fato(provider, dias, nome="PIB"):
    periodo = date.today() - timedelta(days=dias)
    return {"indicator": nome, "provider": provider, "provider_code": "X",
            "reference_period": periodo.isoformat(), "value": 1.0, "unit": "%",
            "retrieved_at": "2026-10-01T00:00:00+00:00", "limitations": ()}


def test_world_bank_anual_com_dois_anos_de_atraso_entra_com_a_idade():
    linhas = cm._linhas_macro_local([_fato("world_bank", 2 * 365)], "Armazém")
    texto = "\n".join(linhas)
    assert "PIB [world_bank]" in texto
    assert "período há 730 dias" in texto
    assert "omitidas" not in texto


def test_serie_nao_anual_velha_e_nomeada_como_omitida():
    linhas = cm._linhas_macro_local([_fato("fred", 500, "FEDFUNDS")], "Armazém")
    texto = "\n".join(linhas)
    assert "omitidas por defasagem" in texto and "FEDFUNDS [fred]" in texto


def test_world_bank_alem_da_tolerancia_tambem_e_nomeado():
    texto = "\n".join(cm._linhas_macro_local([_fato("world_bank", 5 * 365)], "A"))
    assert "PIB [world_bank]" in texto and "omitidas" in texto


def test_monitoramento_e_enriquecimento_usam_a_versao_vigente():
    from core.fii_methodology import METHODOLOGY_VERSION
    from data_pipeline.market import fii_enrichment, fii_monitoring

    for mod in (fii_monitoring, fii_enrichment):
        fonte = inspect.getsource(mod)
        assert "6.0.0" not in fonte
        assert mod.METHODOLOGY_VERSION == METHODOLOGY_VERSION


def _carregar_migration(nome):
    import importlib.util
    from pathlib import Path

    caminho = Path(__file__).resolve().parents[1] / "migration" / f"{nome}.py"
    spec = importlib.util.spec_from_file_location(f"mig_{nome}", caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migration_04_normaliza_datas_de_texto_e_datetime():
    from datetime import datetime

    mod = _carregar_migration("04_transform_to_canonical")
    f = mod._normalize_date
    assert f("2026-01-05") == "2026-01-05"
    assert f("05/01/2026") == "2026-01-05"
    assert f("2026-01-05T10:30:00") == "2026-01-05"
    assert f("2026-01-05T10:30:00Z") == "2026-01-05"
    assert f(datetime(2026, 1, 5, 10, 30)) == "2026-01-05"
    assert f(date(2026, 1, 5)) == "2026-01-05"
    assert f(None) is None and f("  ") is None


def test_migration_08_legado_recusa_rodar_e_aponta_o_caminho_canonico(capsys, monkeypatch):
    mod = _carregar_migration("08_compute_portfolio_positions")
    monkeypatch.setattr("sys.argv", ["08", "--apply"])
    assert mod.main() == 2
    saida = capsys.readouterr().out
    assert "recompute_for_user" in saida and "OBSOLETO" in saida
