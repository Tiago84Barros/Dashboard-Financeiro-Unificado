"""Vacância do FII: escala explícita, fonte única, data-base e poda (D3, 10/11/2026)."""
from datetime import date, datetime, timedelta, timezone

import pytest

from core import fii_vacancia as fv

T0 = datetime(2026, 7, 16, 12, tzinfo=timezone.utc)


def _l(fonte, vac, area, quando=T0, nome="x"):
    return {"fonte": fonte, "vacancia": vac, "area_m2": area, "updated_at": quando,
            "nome_imovel": nome}


def test_mesmo_imovel_fracao_e_percentual_da_1_por_cento():
    assert fv.para_fracao(0.01, "fracao") == pytest.approx(0.01)
    assert fv.para_fracao(1.00, "percentual") == pytest.approx(0.01)
    assert fv.agregar_imoveis([{"vacancia": 1.0, "area_m2": 100}], "percentual") == pytest.approx(0.01)


def test_fracao_acima_de_1_so_pode_ser_percentual():
    assert fv.para_fracao(3.5, "fracao") == pytest.approx(0.035)
    assert fv.para_fracao(250, "fracao") is None
    assert fv.para_fracao(-1, "fracao") is None
    assert fv.para_fracao(None, "fracao") is None
    with pytest.raises(ValueError):
        fv.para_fracao(0.5, "ratio")


def test_lista_com_maioria_da_area_em_1_00_e_ambigua_e_nao_vira_66_por_cento():
    linhas = [{"vacancia": 1.0, "area_m2": 100.0}] * 6 + [{"vacancia": 0.0, "area_m2": 50.0}] * 2
    assert fv.lista_ambigua(linhas)
    assert fv.agregar_imoveis(linhas, "fracao") is None


def test_fundo_pequeno_totalmente_vago_nao_e_ambiguo():
    assert fv.agregar_imoveis([{"vacancia": 1.0, "area_m2": 10}], "fracao") == 1.0


def test_xpml11_statusinvest_e_brapi_nao_se_misturam():
    brapi = [_l("brapi_fii_v2", 1.0, 100.0, T0, f"b{i}") for i in range(6)]
    si = [_l("statusinvest", 0.01, 100.0, T0 + timedelta(days=3), f"s{i}") for i in range(4)]
    coleta = fv.coleta_mais_recente(brapi + si)
    assert {r["fonte"] for r in coleta} == {"statusinvest"}
    assert fv.agregar_imoveis(coleta) == pytest.approx(0.01)


def test_imovel_inalterado_com_carimbo_antigo_continua_na_coleta():
    # updated_at so muda quando o valor muda: o imovel de 900 m2 nao e fossil.
    novo = [_l("statusinvest", 0.50, 100.0, T0 + timedelta(days=40), "a")]
    inalterado = [_l("statusinvest", 0.0, 900.0, T0, "b")]
    coleta = fv.coleta_mais_recente(novo + inalterado)
    assert {r["nome_imovel"] for r in coleta} == {"a", "b"}
    assert fv.agregar_imoveis(coleta) == pytest.approx(0.05)


def test_fonte_mais_nova_vence_mesmo_com_nome_depois_no_alfabeto():
    brapi = [_l("brapi_fii_v2", 0.9, 100.0, T0, "b")]
    si = [_l("statusinvest", 0.02, 100.0, T0 + timedelta(hours=2), "s")]
    assert {r["fonte"] for r in fv.coleta_mais_recente(brapi + si)} == {"statusinvest"}


def test_cvm_recente_vence_e_data_e_a_data_base():
    imoveis = [_l("brapi_fii_v2", 1.0, 100.0, T0, f"b{i}") for i in range(6)]
    cvm = {"valor": 0.0593, "data_base": date(2026, 6, 30)}
    r = fv.decidir_vacancia_fundo(imoveis, cvm, date(2026, 11, 10))
    assert r["vacancia"] == pytest.approx(0.0593)
    assert r["vacancia_ref_date"] == date(2026, 6, 30)
    assert r["vacancia_ref_date"] != date(2026, 11, 10)


def test_cvm_velha_cai_para_imoveis_datados_pela_coleta():
    imoveis = [_l("statusinvest", 0.04, 100.0, T0, "a")]
    cvm = {"valor": 0.5, "data_base": date(2025, 3, 31)}
    r = fv.decidir_vacancia_fundo(imoveis, cvm, date(2026, 11, 10))
    assert r["vacancia"] == pytest.approx(0.04)
    assert r["vacancia_ref_date"] == T0.date()


def test_sem_fonte_confiavel_devolve_none_nunca_zero():
    ambiguo = [_l("brapi_fii_v2", 1.0, 100.0, T0, f"b{i}") for i in range(6)]
    assert fv.decidir_vacancia_fundo(ambiguo, None, date(2026, 11, 10)) is None
    assert fv.decidir_vacancia_fundo([], None, date(2026, 11, 10)) is None


def test_poda_respeita_piso_de_cardinalidade():
    assert fv.deve_podar(14, 14)
    assert fv.deve_podar(14, 7)
    assert not fv.deve_podar(14, 6)
    assert not fv.deve_podar(14, 0)


def _payload(vacs):
    return {"requestedAt": "2026-07-12T12:00:00+00:00", "fiis": [{
        "ticker": "XPML11", "referenceDate": "2026-03-31", "summary": {},
        "properties": [{"name": f"Shopping {i}", "area": 100, "vacancyRate": v}
                       for i, v in enumerate(vacs)]}]}


def test_ingestao_brapi_lista_toda_em_1_00_nao_grava_vacancia_de_100_por_cento():
    from data_pipeline.market import fii_v2
    rows = fii_v2.normalize_properties(_payload([1.0] * 6 + [0.0]))["properties"]
    assert len(rows) == 7 and all(r["vacancia"] is None for r in rows)


def test_ingestao_brapi_fracao_normal_e_percentual_explicito():
    from data_pipeline.market import fii_v2
    rows = fii_v2.normalize_properties(_payload([0.03, 5.0, 250, None]))["properties"]
    assert [r["vacancia"] for r in rows] == [0.03, 0.05, None, None]
