"""INV-A1/A2/A3 da auditoria de 04/10/2026.

* A1: renda fixa/fundo com custo == valor da foto não tem retorno de 0,0%: tem
  "sem marcação". FIP não é renda fixa.
* A2: o "ganho total" vira três parcelas, com cobertura; amortização é
  devolução de capital, não rendimento.
* A3: o stress test escolhe o choque pelo indexador e pela duration; CDB não
  leva marcação a mercado.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from core import stress_tests as S
from core.investimentos import (
    _agregar_por_classe,
    _class_key_from_snapshot,
    _montar_carteira_snapshot,
    _montar_evolucao_snapshot,
    decompor_ganho,
    eh_fip,
    resumo_sem_marcacao,
    sem_marcacao_renda_fixa,
)

HOJE = date(2026, 10, 4)


def _row(ticker, *, qty, vm, asset_type, name=None, invested=None):
    return SimpleNamespace(
        ticker=ticker, quantity=qty, market_value=vm, invested_value=invested,
        asset_type=asset_type, asset_name=name or ticker, sector="other",
        currency="BRL", country="BR", report_date=date(2026, 9, 21),
        live_price=None, live_timestamp=None, usd_brl_rate=None,
        pp_quantity=0, pp_total_invested=0, pp_average_price=0,
    )


# ── A1: sem marcação e FIP ──────────────────────────────────────────────────

def test_cdb_com_custo_igual_ao_valor_fica_sem_marcacao_e_nao_zero():
    c = _montar_carteira_snapshot([
        _row("CDB-BANCOX-2028", qty=1, vm=10_000.0, asset_type="fixed_income",
             invested=10_000.0),
        _row("CDB-BANCOY-2029", qty=1, vm=11_000.0, asset_type="fixed_income",
             invested=10_000.0),
    ])
    por_t = {p["ticker"]: p for p in c["posicoes"]}
    assert por_t["CDB-BANCOX-2028"]["sem_marcacao"] is True
    assert por_t["CDB-BANCOX-2028"]["rentab_pct"] is None
    # custo != valor: o retorno é real e continua aparecendo
    assert por_t["CDB-BANCOY-2029"]["sem_marcacao"] is False
    assert por_t["CDB-BANCOY-2029"]["rentab_pct"] == 10.0
    assert c["sem_marcacao"]["n"] == 1
    assert c["sem_marcacao"]["pct"] == pytest.approx(10_000 / 21_000 * 100, abs=0.01)


def test_custo_ausente_imputado_pelo_mercado_tambem_e_sem_marcacao():
    assert sem_marcacao_renda_fixa("fundo_rf", "mercado_fallback", "snapshot", 5.0, 5.0)


def test_acao_e_papel_com_cotacao_viva_nunca_ficam_sem_marcacao():
    assert not sem_marcacao_renda_fixa("stock", "snapshot", "snapshot", 100.0, 100.0)
    assert not sem_marcacao_renda_fixa("renda_fixa", "snapshot", "live", 100.0, 100.0)


def test_retorno_da_classe_ignora_quem_esta_sem_marcacao():
    posicoes = [
        {"classe": "Renda Fixa", "cor": "#fff", "valor_mercado": 110.0,
         "total_investido": 100.0, "sem_marcacao": False},
        {"classe": "Renda Fixa", "cor": "#fff", "valor_mercado": 900.0,
         "total_investido": 900.0, "sem_marcacao": True},
    ]
    (cls,) = _agregar_por_classe(posicoes)
    assert cls["rentab_pct"] == 10.0          # sem diluir pelos R$ 900 sem marcação
    assert cls["sem_marcacao_n"] == 1
    assert resumo_sem_marcacao(posicoes)["valor"] == 900.0


@pytest.mark.parametrize("ticker", ["4606422UNA", "5082123CA1"])
def test_fips_da_auditoria_deixam_de_ser_renda_fixa(ticker):
    assert eh_fip(ticker)
    assert _class_key_from_snapshot("fixed_income", ticker, "BR") == "fip"
    c = _montar_carteira_snapshot([
        _row(ticker, qty=1, vm=3_000.0, asset_type="fixed_income", invested=3_000.0)])
    assert c["posicoes"][0]["classe"] == "FIP"
    assert c["posicoes"][0]["sem_marcacao"] is True


def test_fip_identificado_pelo_nome_e_cdb_comum_continua_renda_fixa():
    assert _class_key_from_snapshot(
        "fixed_income", "XPTO11", "BR", "XPTO Fundo de Investimento em Participações") == "fip"
    assert _class_key_from_snapshot("fixed_income", "CDB123", "BR", "CDB Banco X") == "renda_fixa"


# ── A2: três parcelas com cobertura ─────────────────────────────────────────

def _carteira_mista():
    return {"posicoes": [
        {"classe": "Ações BR", "valor_mercado": 600.0, "custo_fonte": "b3_negociacao",
         "custo_estimado": False},
        {"classe": "FII", "valor_mercado": 200.0, "custo_fonte": "preco_medio_estimado",
         "custo_estimado": True},
        {"classe": "Renda Fixa", "valor_mercado": 200.0, "custo_fonte": "snapshot",
         "custo_estimado": False, "sem_marcacao": True},
    ]}


def test_tres_parcelas_separadas_e_sem_total():
    g = decompor_ganho(
        {"total_mercado": 1000.0, "total_investido": 900.0, "total_dividendos": 40.0,
         "total_amortizacao": 6.0},
        {"ganho": 70.0, "valor_vendido": 500.0, "valor_sem_custo": 500.0},
        _carteira_mista())
    assert set(g) == {"nao_realizado", "realizado", "proventos"}
    assert g["nao_realizado"]["valor"] == 100.0
    assert g["realizado"]["valor"] == 70.0
    assert g["proventos"]["valor"] == 40.0
    # amortização não é rendimento
    assert g["proventos"]["devolucao_capital"] == 6.0


def test_cobertura_de_cada_parcela():
    g = decompor_ganho(
        {"total_mercado": 1000.0, "total_investido": 900.0, "total_dividendos": 40.0},
        {"ganho": 70.0, "valor_vendido": 500.0, "valor_sem_custo": 500.0},
        _carteira_mista())
    # só as ações (600 de 1000) têm custo confiável: FII estimado, RF sem marcação
    assert g["nao_realizado"]["cobertura_pct"] == 60.0
    # metade do valor vendido não tinha custo no extrato
    assert g["realizado"]["cobertura_pct"] == 50.0
    # ações + FII distribuem provento; a renda fixa rende dentro do valor
    assert g["proventos"]["cobertura_pct"] == 80.0


def test_realizado_indisponivel_nao_vira_zero():
    g = decompor_ganho({"total_mercado": 10.0, "total_investido": 5.0},
                       {"ganho": None, "motivo": "sem extrato"})
    assert g["realizado"]["valor"] is None
    assert g["realizado"]["cobertura_pct"] is None
    assert g["realizado"]["nota"] == "sem extrato"
    assert g["nao_realizado"]["cobertura_pct"] is None   # sem carteira, sem chute


def test_sem_custo_nao_decompoe():
    assert decompor_ganho({"total_mercado": 10.0, "total_investido": 0.0}) is None


def test_evolucao_separa_amortizacao_do_provento():
    # O SQL já devolve renda e amortização em colunas distintas.
    snaps = [SimpleNamespace(mes=date(2026, 9, 1), valor_mercado=1000.0,
                             valor_investido_snapshot=900.0)]
    divs = [SimpleNamespace(mes=date(2026, 8, 1), delta_dividendos=30.0,
                            delta_amortizacao=None),
            SimpleNamespace(mes=date(2026, 9, 1), delta_dividendos=None,
                            delta_amortizacao=6.0)]
    d = _montar_evolucao_snapshot(snaps, divs, None, [])
    assert d["total_dividendos"] == 30.0
    assert d["total_amortizacao"] == 6.0


def test_sql_de_proventos_exclui_amortizacao_da_renda():
    from core.investimentos import _SQL_EVOLUCAO_DIV

    assert "delta_amortizacao" in _SQL_EVOLUCAO_DIV
    assert "<> 'amortization'" in _SQL_EVOLUCAO_DIV


# ── A3: stress por indexador e duration ─────────────────────────────────────

CEN = S.SCENARIOS[0]   # Subprime: renda_fixa -5%, tesouro 0%


def _stress(pos):
    return S.aplicar_stress([pos], CEN, hoje=HOJE)


def test_tesouro_selic_leva_o_choque_de_pos_fixado():
    r = _stress({"classe": "Tesouro Direto", "ticker": "TSELIC2029",
                 "nome": "Tesouro Selic 2029", "valor_mercado": 1000.0})
    assert r["perda_pct"] == pytest.approx(CEN.shock_tesouro)
    assert list(r["por_indexador"]) == [S.ROTULO_SELIC]


def test_tesouro_ipca_nao_e_tratado_como_selic_e_escala_pela_duration():
    r = _stress({"classe": "Tesouro Direto", "ticker": "TIPCA2035",
                 "nome": "Tesouro IPCA+ 2035", "valor_mercado": 1000.0})
    dur = (date(2035, 7, 1) - HOJE).days / 365.25
    esperado = CEN.shock_renda_fixa * dur / S.DURATION_REFERENCIA_ANOS
    assert r["perda_pct"] == pytest.approx(esperado)
    assert r["perda_pct"] < CEN.shock_tesouro        # pior que o Selic
    assert r["por_indexador"][S.ROTULO_IPCA]["duration_anos"] == pytest.approx(dur)


def test_prefixado_longo_perde_mais_que_o_curto():
    longo = _stress({"classe": "Tesouro Direto", "ticker": "TPRE2035",
                     "nome": "Tesouro Prefixado 2035", "valor_mercado": 1000.0})
    curto = _stress({"classe": "Tesouro Direto", "ticker": "TPRE2027",
                     "nome": "Tesouro Prefixado 2027", "valor_mercado": 1000.0})
    assert S.ROTULO_PRE in longo["por_indexador"]
    assert longo["perda_pct"] < curto["perda_pct"] < 0


def test_cdb_nao_leva_marcacao_a_mercado():
    for nome in ("CDB Banco X pós 110% CDI", "CDB Banco X pré 13%", "CDB Banco X IPCA+6%"):
        r = _stress({"classe": "Renda Fixa", "ticker": "CDB123", "nome": nome,
                     "valor_mercado": 1000.0})
        assert r["perda_pct"] == 0.0
        assert S.ROTULO_CURVA in r["por_indexador"]


def test_titulo_sem_indexador_legivel_nao_vira_selic_e_e_declarado():
    r = _stress({"classe": "Tesouro Direto", "ticker": "TESOURO", "nome": "Título X",
                 "valor_mercado": 1000.0})
    assert r["perda_pct"] == pytest.approx(CEN.shock_renda_fixa)   # conservador, sem escala
    assert S.ROTULO_NAO_IDENT in r["por_indexador"]
    assert r["renda_fixa_sem_duration"] == ["TESOURO"]


def test_duration_informada_prevalece_e_vencimento_com_mes():
    d, fonte = S.duration_anos({"duration_anos": 3.0, "nome": "x 2040"}, HOJE)
    assert (d, fonte) == (3.0, "informada")
    d, _ = S.duration_anos({"nome": "NTN-B mar/2031"}, HOJE)
    assert d == pytest.approx((date(2031, 3, 15) - HOJE).days / 365.25)
    assert S.duration_anos({"nome": "sem data"}, HOJE)[0] is None


def test_choque_escalado_tem_piso():
    r = S.aplicar_stress(
        [{"classe": "Tesouro Direto", "ticker": "TIPCA2070", "nome": "Tesouro IPCA+ 2070",
          "valor_mercado": 100.0}], S.SCENARIOS[1 + 5], hoje=HOJE)   # Moratória 1998: -18%
    assert r["perda_pct"] >= S.PISO_CHOQUE_TITULO


def test_fip_leva_o_choque_de_acoes_e_acao_segue_igual():
    fip = _stress({"classe": "FIP", "ticker": "4606422UNA", "valor_mercado": 100.0})
    assert fip["perda_pct"] == pytest.approx(CEN.shock_stock_br)
    acao = _stress({"classe": "Ações BR", "ticker": "PETR4", "valor_mercado": 100.0})
    assert acao["perda_pct"] == pytest.approx(CEN.shock_stock_br)
    assert acao["por_indexador"] == {}
