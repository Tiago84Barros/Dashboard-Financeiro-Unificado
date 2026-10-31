"""Vitrines local-first (04/10/2026): série mensal dos FIIs e documentos do
dossiê lidos de artefatos em ``data/public``, com o Supabase só de reserva."""
from __future__ import annotations

import gzip
import json
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from core import dossie_b3, market_read
from core.inteligencia_ativos import fontes_informacoes
from scripts import publish_fii_metrics_monthly as pub

AGORA = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)


def _metrica(tk, mes, vpa=10.0, cot=100):
    return {"ticker": tk, "ref_month": mes, "vpa": vpa,
            "patrimonio_liquido": 1000.0, "num_cotistas": cot,
            "dy_patrimonial_mes": 0.01, "pct_imoveis": 0.5, "pct_papel": 0.3,
            "pct_caixa": 0.1, "pct_fundos": 0.1}


# -- publicador --------------------------------------------------------------

def test_payload_une_fechamento_do_mes_e_deixa_nulo_o_que_falta():
    met = [_metrica("AAAA11", date(2026, 8, 1)), _metrica("AAAA11", date(2026, 9, 1)),
           _metrica("BBBB11", date(2026, 9, 1), vpa=None)]
    fech = [{"ticker": "AAAA11", "mes": date(2026, 8, 1), "close": 9.5}]
    p = pub.montar_payload(met, fech, AGORA)
    assert p["base_ate"] == "2026-09-01"
    assert p["n_tickers"] == 2 and p["n_linhas"] == 3
    linhas = p["por_ticker"]["AAAA11"]
    assert linhas[0][0] == "2026-08-01" and linhas[0][-1] == 9.5
    assert linhas[1][-1] is None            # sem fita no mês: não inventa preço
    assert p["por_ticker"]["BBBB11"][0][1] is None
    assert p["colunas"][-1] == "fechamento"


def test_recusa_serie_velha_e_aceita_em_dia():
    em_dia = pub.montar_payload([_metrica("A11", date(2026, 9, 1))], [], AGORA)
    assert pub.serie_velha(em_dia, AGORA.date()) is None
    velha = pub.montar_payload([_metrica("A11", date(2026, 5, 1))], [], AGORA)
    assert "2026-05-01" in pub.serie_velha(velha, AGORA.date())
    assert pub.serie_velha(pub.montar_payload([], [], AGORA), AGORA.date())


def test_serializacao_e_deterministica(tmp_path):
    p = pub.montar_payload([_metrica("A11", date(2026, 9, 1))], [], AGORA)
    a, b = tmp_path / "a.json.gz", tmp_path / "b.json.gz"
    pub.serializar(p, a)
    pub.serializar(p, b)
    assert a.read_bytes() == b.read_bytes()
    assert json.loads(gzip.decompress(a.read_bytes()))["versao"] == pub.VERSAO


# -- leitor com fallback -----------------------------------------------------

@pytest.fixture
def artefato(tmp_path, monkeypatch):
    met = [_metrica("AAAA11", date(2026, 8, 1), vpa=10.0, cot=5),
           _metrica("AAAA11", date(2026, 9, 1), vpa=0.0)]
    fech = [{"ticker": "AAAA11", "mes": date(2026, 8, 1), "close": 9.0},
            {"ticker": "AAAA11", "mes": date(2026, 9, 1), "close": 9.0}]
    caminho = tmp_path / "fii.json.gz"
    pub.serializar(pub.montar_payload(met, fech, AGORA), caminho)
    monkeypatch.setattr(market_read, "ARQUIVO_FII_METRICS_MENSAL", caminho)
    return caminho


def _sem_banco(monkeypatch):
    def _proibido(*a, **k):
        raise AssertionError("não devia consultar o Supabase")
    monkeypatch.setattr(market_read, "_q", _proibido)


def test_leitor_usa_artefato_sem_tocar_no_banco(artefato, monkeypatch):
    _sem_banco(monkeypatch)
    df = market_read.load_fii_metrics_mensal.__wrapped__("AAAA11.SA")
    assert list(df["Data"].dt.strftime("%Y-%m")) == ["2026-08", "2026-09"]
    assert df.loc[0, "P/VP"] == pytest.approx(0.9)
    assert pd.isna(df.loc[1, "P/VP"])       # VPA 0: sem P/VP, como no SQL
    assert {"VPA", "Patrimonio", "Cotistas", "DY_Patrimonial", "Pct_Imoveis",
            "Pct_Papel", "Pct_Caixa", "Pct_Fundos"} <= set(df.columns)
    assert "fechamento" not in df.columns


def test_leitor_cai_no_banco_para_fundo_fora_do_artefato(artefato, monkeypatch):
    chamadas = []

    def _q(sql, params=None):
        chamadas.append(params)
        return pd.DataFrame()
    monkeypatch.setattr(market_read, "_q", _q)
    assert market_read.load_fii_metrics_mensal.__wrapped__("ZZZZ11").empty
    assert chamadas == [{"tk": "ZZZZ11"}]


def test_leitor_cai_no_banco_sem_arquivo(tmp_path, monkeypatch):
    monkeypatch.setattr(market_read, "ARQUIVO_FII_METRICS_MENSAL",
                        tmp_path / "nao_existe.json.gz")
    chamadas = []
    monkeypatch.setattr(market_read, "_q",
                        lambda sql, params=None: chamadas.append(1) or pd.DataFrame())
    assert market_read.load_fii_metrics_mensal.__wrapped__("AAAA11").empty
    assert chamadas


def test_adaptador_de_carteira_recebe_a_serie_do_artefato(artefato, monkeypatch):
    from core.portfolio.adapters import fii
    monkeypatch.setattr(market_read, "_q", lambda sql, params=None: pd.DataFrame())
    monkeypatch.setattr(market_read, "load_fii_metrics_mensal",
                        market_read.load_fii_metrics_mensal.__wrapped__)
    saida = fii._carregar_metricas_mensais(("AAAA11", "ZZZZ11"))
    assert set(saida) == {"AAAA11"}


# -- dossiê ------------------------------------------------------------------

def _art_docs(**extra):
    return {"relatorios": {"janela_dias": 180, "base_ate": {"b3": "2026-09-25"},
                           "por_ticker": {
        "PETR4": [{"reference_date": "2026-09-01", "tipo": "balanco",
                   "titulo": "ITR 2T26", "source": "CVM/IPE"}],
        "PETR3": [{"reference_date": "2026-09-10", "tipo": "fato_relevante",
                   "titulo": "Fato relevante", "source": "CVM/IPE"}],
        "PETR11": [{"reference_date": "2026-09-20", "tipo": "balanco",
                    "titulo": "Informe de FII", "source": "B3/FNET"}],
        "VALE3": [{"reference_date": "2026-09-02", "tipo": "balanco",
                   "titulo": "outra", "source": "CVM/IPE"}]}, **extra}}


def test_dossie_prefere_o_artefato_e_junta_classes(monkeypatch):
    monkeypatch.setattr(fontes_informacoes, "arquivo", _art_docs)
    monkeypatch.setattr(dossie_b3, "_rows", lambda *a, **k: pytest.fail("Supabase"))
    ev = dossie_b3._eventos_societarios("PETR4", n=12)
    assert [e["data"] for e in ev["eventos"]] == ["2026-09-10", "2026-09-01"]
    assert ev["n_docs"] == 2 and ev["docs_desde"] == "2026-09-01"
    assert ev["janela_dias"] == 180


def test_dossie_respeita_o_limite_n(monkeypatch):
    monkeypatch.setattr(fontes_informacoes, "arquivo", _art_docs)
    assert dossie_b3._eventos_societarios("PETR4", n=1)["n_docs"] == 1


def test_dossie_sem_doc_no_artefato_nao_vai_ao_supabase(monkeypatch):
    monkeypatch.setattr(fontes_informacoes, "arquivo", _art_docs)
    monkeypatch.setattr(dossie_b3, "_rows", lambda *a, **k: pytest.fail("Supabase"))
    ev = dossie_b3._eventos_societarios("ITUB4")
    assert ev["n_docs"] == 0 and ev["eventos"] == []


def test_dossie_sem_artefato_cai_no_supabase(monkeypatch):
    monkeypatch.setattr(fontes_informacoes, "arquivo", lambda: {})
    monkeypatch.setattr(dossie_b3, "_rows", lambda *a, **k: [
        {"dt": "2026-06-26", "cat": "ITR", "titulo": "x"}])
    ev = dossie_b3._eventos_societarios("PETR4")
    assert ev["n_docs"] == 1 and ev["eventos"][0]["categoria"] == "ITR"
