"""Auditoria FII de 10/11/2026: a observacao mais VELHA passava por cima da
linha-base mais nova (D1), o VPA/PL reapresentado pela CVM nao chegava a linha
(D2), as cotas ficavam na ultima foto da brapi (D4). Dados sinteticos."""
from __future__ import annotations

import pandas as pd
import pytest

from core import market_read


def _obs(metric, valor, ref, know, source="brapi_fii_v2"):
    return {"metric_name": metric, "value_numeric": valor, "value_text": None,
            "value_json": None, "reference_date": pd.Timestamp(ref),
            "available_at": pd.Timestamp(know, tz="UTC"),
            "knowledge_at": pd.Timestamp(know, tz="UTC"),
            "availability_quality": "verified_publication", "vintage": "v",
            "source": source, "quality_status": "observed"}


def _base(**kw):
    row = {"ticker": "FUNDO11", "price": 100.0, "pvp": 1.0, "dy_12m": 0.10,
           "vpa": 100.0, "patrimonio_liquido": 1_000_000_000.0,
           "updated_at": pd.Timestamp("2026-11-07 12:00", tz="UTC"),
           "cvm_ref_date": pd.Timestamp("2026-09-01"), "metric_metadata": {}}
    row.update(kw)
    return row


def _aplicar(row, obs):
    market_read._aplicar_observacoes_fii(row, obs, {})
    return row


def test_observacao_velha_nao_passa_por_cima_da_linha_base_nova():
    row = _aplicar(_base(), [
        _obs("price", 120.0, "2026-07-16", "2026-07-16"),
        _obs("dy_12m", 0.20, "2026-07-16", "2026-07-16"),
        _obs("pvp", 1.5, "2026-07-16", "2026-07-16")])
    assert row["price"] == 100.0
    assert row["dy_12m"] == 0.10
    assert row["pvp"] == pytest.approx(1.0)


def test_observacao_mais_nova_que_a_base_vence():
    row = _aplicar(_base(), [_obs("price", 110.0, "2026-11-09", "2026-11-09")])
    assert row["price"] == 110.0


def test_observacao_preenche_quando_a_base_nao_tem_valor():
    row = _aplicar(_base(dy_12m=None), [_obs("dy_12m", 0.2, "2026-07-16", "2026-07-16")])
    assert row["dy_12m"] == 0.2


def test_pvp_e_preco_sobre_vpa_da_linha_final():
    row = _aplicar(_base(price=50.0, pvp=9.9, vpa=100.0), [
        _obs("pvp", 3.0, "2026-07-16", "2026-07-16")])
    assert row["pvp"] == pytest.approx(0.5)


def test_reapresentacao_cvm_da_mesma_competencia_vence_vpa_e_pl():
    row = _aplicar(_base(vpa=195.67, patrimonio_liquido=3.69e9), [
        _obs("nav_per_share", 100.3, "2026-09-01", "2026-11-05", "cvm_informe_mensal"),
        _obs("equity", 1.89e9, "2026-09-01", "2026-11-05", "cvm_informe_mensal")])
    assert row["vpa"] == 100.3
    assert row["patrimonio_liquido"] == 1.89e9
    assert row["pvp"] == pytest.approx(100.0 / 100.3)


def test_vpa_de_competencia_mais_velha_ou_fonte_nao_oficial_nao_vence():
    row = _aplicar(_base(), [
        _obs("nav_per_share", 90.0, "2026-07-01", "2026-11-08", "cvm_informe_mensal"),
        _obs("equity", 5e8, "2026-07-16", "2026-11-08", "brapi_fii_v2")])
    assert row["vpa"] == 100.0
    assert row["patrimonio_liquido"] == 1_000_000_000.0


def test_cotas_derivadas_da_cvm_substituem_a_foto_velha_da_brapi():
    row = _aplicar(_base(vpa=10.0, patrimonio_liquido=5.67e9), [
        _obs("shares_outstanding", 460e6, "2026-03-01", "2026-07-16")])
    assert row["shares_outstanding"] == pytest.approx(567e6)
    # cotas x VPA fecham com o PL da mesma competencia (<= 1%)
    assert abs(row["shares_outstanding"] * row["vpa"] / row["patrimonio_liquido"] - 1) <= 0.01
    meta = row["metric_metadata"]["shares_outstanding"]
    assert "PL" in meta["source"] and meta["reference_date"] == "2026-09-01"


def test_sem_vpa_ou_pl_as_cotas_ficam_como_estavam():
    row = _aplicar(_base(vpa=None, patrimonio_liquido=None), [
        _obs("shares_outstanding", 460e6, "2026-03-01", "2026-07-16")])
    assert row["shares_outstanding"] == 460e6


def test_pvp_do_detalhe_usa_o_close_bruto_nao_o_ajustado():
    from core import fii_detalhe_armazem as det
    precos = [{"date": "2026-10-01", "preco": 80.0, "preco_bruto": 100.0},
              {"date": "2026-11-06", "preco": 90.0, "preco_bruto": 120.0}]
    r = det.resumo_precos(precos)
    assert r["ultimo"] == 90.0          # retorno total segue ajustado
    assert r["ultimo_bruto"] == 120.0   # preco de tela e P/VP: close
    assert r["r12m"] is None or isinstance(r["r12m"], float)


def test_sem_close_bruto_nao_ha_pvp_inventado():
    from core import fii_detalhe_armazem as det
    r = det.resumo_precos([{"date": "2026-10-01", "preco": 80.0, "preco_bruto": None},
                           {"date": "2026-11-06", "preco": 90.0, "preco_bruto": None}])
    assert r["ultimo_bruto"] is None


def test_busca_usa_os_kpis_da_mesma_vitrine_do_ranking():
    from views import fiis
    d = pd.Series({"Ticker": "FUNDO11", "Preço": 154.2, "P/VP": 1.5, "DY_12m": 0.2,
                   "VPA": 90.0, "Patrimonio": 1.0, "Cotistas": 10.0,
                   "Liquidez_Diaria": 5.0, "updated_at": pd.Timestamp("2026-10-28")})
    vitrine = pd.DataFrame([{"Ticker": "FUNDO11", "Preço": 100.0, "P/VP": 1.0,
                             "DY_12m": 0.1, "VPA": 100.0, "Patrimonio": 2.0,
                             "Cotistas": None, "Liquidez_Diaria": 7.0,
                             "updated_at": pd.Timestamp("2026-11-07"),
                             "cvm_ref_date": pd.Timestamp("2026-09-01")}])
    out = fiis._kpis_da_vitrine(d, vitrine, "FUNDO11")
    assert out["Preço"] == 100.0 and out["P/VP"] == 1.0 and out["VPA"] == 100.0
    assert out["Cotistas"] == 10.0          # ausente na vitrine: nao vira zero nem some
    assert out["updated_at"] == pd.Timestamp("2026-11-07")
    assert d["Preço"] == 154.2              # nao muta a entrada


def test_busca_sem_linha_na_vitrine_mantem_o_que_tinha():
    from views import fiis
    d = pd.Series({"Ticker": "FUNDO11", "Preço": 154.2})
    out = fiis._kpis_da_vitrine(d, pd.DataFrame(), "FUNDO11")
    assert out["Preço"] == 154.2


def test_competencias_desalinhadas_nao_geram_cotas_nem_dependem_da_ordem():
    obs = [_obs("equity", 2e9, "2026-10-01", "2026-11-08", "cvm_informe_mensal"),
           _obs("nav_per_share", 100.0, "2026-09-01", "2026-11-08", "cvm_informe_mensal")]
    for ordem in (obs, obs[::-1]):
        row = _aplicar(_base(cvm_ref_date=pd.Timestamp("2026-08-01"),
                             vpa=90.0, patrimonio_liquido=1e9), list(ordem))
        assert row["vpa"] == 100.0 and row["patrimonio_liquido"] == 2e9
        assert row["metric_metadata"].get("shares_outstanding", {}).get(
            "source", "") != "derivado: PL / VPA (Informe Mensal CVM)"


def test_data_do_preco_acompanha_a_observacao_vencedora():
    row = _aplicar(_base(), [_obs("price", 110.0, "2026-11-09", "2026-11-09")])
    assert row["price_date"] == "2026-11-09"
    assert row["metric_metadata"]["pvp"]["price_date"] == "2026-11-09"


def test_faixa_12m_usa_close_bruto_ao_lado_da_cotacao():
    from core import fii_detalhe_armazem as det
    r = det.resumo_precos([{"date": "2026-10-01", "preco": 80.0, "preco_bruto": 100.0},
                           {"date": "2026-11-06", "preco": 90.0, "preco_bruto": 120.0}])
    assert (r["min52_bruto"], r["max52_bruto"]) == (100.0, 120.0)


def test_pvp_datado_pela_competencia_do_proprio_vpa():
    row = _aplicar(_base(cvm_ref_date=pd.Timestamp("2026-08-01")), [
        _obs("nav_per_share", 100.0, "2026-10-01", "2026-11-08", "cvm_informe_mensal")])
    assert row["metric_metadata"]["pvp"]["reference_date"] == "2026-10-01"
    assert "_vpa_comp" not in row


def test_enrich_da_cvm_nao_rejuvenesce_a_cotacao_base():
    row = _base(updated_at=pd.Timestamp("2026-11-10 12:00", tz="UTC"),
                price_base_at=pd.Timestamp("2026-11-07 12:00", tz="UTC"))
    _aplicar(row, [_obs("price", 110.0, "2026-11-09", "2026-11-09")])
    assert row["price"] == 110.0 and row["price_date"] == "2026-11-09"


def test_proveniencia_sobrevive_a_compactacao_do_snapshot():
    from core.inteligencia_ativos.fontes_fundamentos import dados_fii
    from scripts.publish_fii_selection_snapshot import _compact_payload
    row = _aplicar(_base(vpa=10.0, patrimonio_liquido=5.67e9), [
        _obs("shares_outstanding", 460e6, "2026-03-01", "2026-07-16")])
    publicado = market_read._expand_snapshot_payload(_compact_payload(row))
    d = dados_fii(publicado)
    assert "preço de 2026-11-07 ÷ VPA da CVM de 2026-09" in d["p_vpa" if False else "p_vp"].nota
    assert "derivado de PL" in d["emissoes"].nota


def test_dy_mantem_a_data_da_base_quando_so_o_preco_foi_aceito():
    row = _aplicar(_base(), [_obs("price", 110.0, "2026-11-09", "2026-11-09")])
    assert row["price_date"] == "2026-11-09"
    assert row["dy_date"] == "2026-11-07"
