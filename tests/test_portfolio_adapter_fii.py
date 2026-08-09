"""Adaptador FII: montagem do payload a partir dos itens da carteira."""
import datetime as dt

import pandas as pd

from core.portfolio.adapters.fii import build_snapshots

ITENS = [
    {"ticker": "HGLG11", "nome": "CSHG Logistica", "segmento": "Logistica",
     "score": 78.0, "peso": 0.6},
    {"tk": "KNCR11", "nome": "Kinea Rendimentos", "segmento": "Papel",
     "score": 71.0, "peso": 0.4},
]

FIIS = pd.DataFrame({
    "Ticker": ["HGLG11", "KNCR11"],
    "Nome": ["CSHG Logistica", "Kinea Rendimentos"],
    "Segmento": ["Logistica", "Papel"],
    "Tipo": ["Tijolo", "Papel"],
    "Preço": [160.0, 102.0],
    "P/VP": [0.95, 1.01],
    "DY_12m": [8.4, 12.1],
    "Liquidez_Diaria": [3_000_000.0, 5_000_000.0],
    "Patrimonio": [3.2e9, 6.1e9],
    "VPA": [168.0, 101.0],
    "Cotistas": [250_000, 410_000],
    "Gestao": ["Ativa", "Ativa"],
    "Pct_Imoveis": [96.0, 0.0],
    "Pct_Papel": [0.0, 94.0],
    "Pct_Caixa": [4.0, 6.0],
    "Pct_Fundos": [0.0, 0.0],
    "Score": [78.0, 71.0],
})


def _loaders():
    return {"fiis": lambda: FIIS}


def _build():
    return build_snapshots(ITENS, model_id="m01", params={"top_n": 2},
                           as_of=dt.date(2026, 8, 5), loaders=_loaders())


def test_gera_um_snapshot_por_item_aceitando_ticker_e_tk():
    assert [s.symbol for s in _build()] == ["HGLG11", "KNCR11"]


def test_classe_moeda_e_pais_vem_do_registro():
    snap = _build()[0]
    assert snap.asset_class == "fii"
    assert snap.payload["identity"]["currency"] == "BRL"
    assert snap.payload["identity"]["country"] == "BR"


def test_identity_usa_segmento_como_setor():
    ident = _build()[0].payload["identity"]
    assert ident["sector"] == "Logistica"
    assert ident["segment"] == "Tijolo"


def test_fundamentals_traz_pvp_dy_e_patrimonio():
    fund = _build()[0].payload["fundamentals"]
    assert fund["pvp"] == 0.95
    assert fund["dy_12m"] == 8.4
    assert fund["patrimonio_liquido"] == 3.2e9


def test_composicao_por_tipo_de_ativo_fica_em_classification():
    comp = _build()[1].payload["classification"]["composition"]
    assert comp["pct_papel"] == 94.0
    assert comp["pct_imoveis"] == 0.0


def test_metrics_preserva_score_e_peso():
    metrics = _build()[0].payload["metrics"]
    assert metrics["score"] == 78.0
    assert metrics["weight"] == 0.6


def test_fii_ausente_da_base_gera_snapshot_degradado():
    itens = ITENS + [{"ticker": "XXXX11", "nome": "Fora da base", "peso": 0.1}]
    out = build_snapshots(itens, model_id="m01", params={}, as_of=dt.date(2026, 8, 5),
                          loaders=_loaders())
    xxxx = [s for s in out if s.symbol == "XXXX11"][0]
    assert xxxx.payload["fundamentals"] == {}
    assert xxxx.payload["classification"]["composition"] == {}


def test_dy_e_pvp_do_item_vencem_o_valor_atual_da_base():
    """No backfill o item traz o valor da selecao; a base traz o de hoje.

    Preferir a base trocaria historico verdadeiro por valor atual, em silencio.
    No salvamento ao vivo os dois coincidem, entao a regra nao muda nada la.
    """
    itens = [{"ticker": "HGLG11", "nome": "CSHG Logistica", "segmento": "Logistica",
              "score": 78.0, "peso": 0.6, "dy_12m": 7.1, "pvp": 0.88}]
    fund = build_snapshots(itens, model_id="m01", params={},
                           as_of=dt.date(2026, 8, 5),
                           loaders=_loaders())[0].payload["fundamentals"]

    assert fund["dy_12m"] == 7.1     # do item (selecao), nao 8.4 da base
    assert fund["pvp"] == 0.88       # do item (selecao), nao 0.95 da base
    assert fund["patrimonio_liquido"] == 3.2e9   # este so existe na base


def test_sem_dy_e_pvp_no_item_usa_a_base():
    fund = _build()[0].payload["fundamentals"]
    assert fund["dy_12m"] == 8.4
    assert fund["pvp"] == 0.95


def test_provenance_registra_origem():
    assert _build()[0].payload["provenance"]["source"] == "selecao_fiis"
