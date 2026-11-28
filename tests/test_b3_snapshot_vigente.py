from datetime import date
from types import SimpleNamespace

from core import market_read
from core.publicacao_agenda import POR_CHAVE
from data_pipeline.market import metrics
from data_pipeline.market.ingest import _perderia_base_cvm


def test_metricas_registram_trimestre_e_apenas_multiplos_de_preco_carregam_pregao():
    snapshot = {"P/L": (9.12, "market_cap/net_income"), "ROE": (0.0946, "net_income/equity")}
    rows = metrics.to_metric_rows("EXMP3", snapshot, year=2026, quarter=3,
                                  price_date=date(2026, 11, 26))
    assert [(r["year"], r["quarter"]) for r in rows] == [(2026, 3)] * 2
    assert rows[0]["calculation_method"].endswith("|preco=2026-11-26")
    assert rows[1]["calculation_method"] == "net_income/equity"


def test_agenda_refaz_ttm_depois_de_balanco_e_de_preco():
    for key, comando in (("b3_pregao", "acoes-cotahist"),
                          ("b3_brapi_anual", "annual"),
                          ("b3_brapi", "daily")):
        passos = POR_CHAVE[key].passos
        i = next(i for i, argumentos in enumerate(passos) if comando in argumentos)
        assert any("reprocess" in p and "--somente-ttm" in p for p in passos[i + 1:])


def test_leitura_de_hoje_exige_competencia_e_pregao(monkeypatch):
    def capturar(sql, params):
        assert params["tk"] == "EXMP3"
        assert "cm.year > 0" in sql
        assert "cm.quarter" in sql and "ultima_competencia" in sql
        assert "preco=" in sql and "data_preco" in sql
        return "consultou"

    monkeypatch.setattr(market_read, "_q_b3", capturar)
    assert market_read._multiplos_long("EXMP3.SA") == "consultou"


def test_coleta_parcial_preserva_a_ultima_revisao_oficial():
    anterior = ("2026-09-30", "2026-11-12")
    competencia = date(2026, 9, 30)
    oficial = SimpleNamespace(period_end=competencia, filing_date=date(2026, 11, 10))
    assert _perderia_base_cvm(anterior, None, competencia)
    assert _perderia_base_cvm(anterior, oficial, competencia)
    assert not _perderia_base_cvm(anterior, oficial, date(2026, 12, 31))
    oficial.filing_date = date(2026, 11, 13)
    assert not _perderia_base_cvm(anterior, oficial, competencia)

