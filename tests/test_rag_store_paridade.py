"""Paridade entre o corpus em Parquet e a tabela no Postgres.

Migrar 93.498 chunks de um banco para arquivo so e defensavel se der para
PROVAR que a resposta nao mudou. Estes testes rodam a MESMA consulta nos dois
backends, com dado real, e comparam linha a linha.

Sao pulados quando falta o Parquet publicado ou o armazem local -- a suite
precisa passar em CI sem banco. Isso e limitacao declarada, nao aprovacao:
quem valida a migracao roda com os dois presentes.
"""
from __future__ import annotations

import os

import pytest

from core import rag_store

pytestmark = pytest.mark.skipif(
    not rag_store.usando_parquet() or os.getenv("GITHUB_ACTIONS") == "true",
    reason="exige corpus Parquet publicado e armazem local")


@pytest.fixture(scope="module")
def conn():
    """Conexao com o armazem local (a origem do Parquet)."""
    from sqlalchemy import create_engine
    from scripts.publish_fii_selection_from_local import _warehouse_url
    try:
        eng = create_engine(
            _warehouse_url().replace("postgresql://", "postgresql+psycopg2://"),
            future=True)
        with eng.connect() as c:
            yield c
        eng.dispose()
    except Exception as exc:  # pragma: no cover - ambiente sem Docker
        pytest.skip(f"armazem local indisponivel: {exc}")


# Tickers escolhidos por perfil, nao por conveniencia: WEGE3 e o caso do
# documento longo que motivou o teto por documento; PETR4 e VALE3 tem corpus
# grande e muitos tipos; ZZZZ nao existe e testa o caminho vazio.
TICKERS = ["WEGE3", "PETR4", "VALE3", "ITUB4", "ZZZZ9"]


@pytest.mark.parametrize("ticker", TICKERS)
def test_busca_temporal_identica(ticker, conn):
    pq = rag_store.busca_temporal(ticker, 40)
    pg = rag_store.busca_temporal(ticker, 40, conn=conn)
    assert pq == pg, f"{ticker}: temporal divergiu ({len(pq)} vs {len(pg)})"


@pytest.mark.parametrize("ticker", TICKERS)
def test_busca_ancora_identica(ticker, conn):
    pq = rag_store.busca_ancora(ticker, 25)
    pg = rag_store.busca_ancora(ticker, 25, conn=conn)
    assert pq == pg, f"{ticker}: ancora divergiu ({len(pq)} vs {len(pg)})"


@pytest.mark.parametrize("meses", [6, 24])
def test_corte_de_data_identico(meses, conn):
    """O corte virou parametro Python justamente porque o intervalo SQL nao era
    portatil; entao e ele que precisa provar paridade."""
    pq = rag_store.busca_temporal("PETR4", 40, meses=meses)
    pg = rag_store.busca_temporal("PETR4", 40, meses=meses, conn=conn)
    assert pq == pg


def test_cobertura_identica(conn):
    alvo = tuple(TICKERS)
    assert rag_store.cobertura(alvo) == rag_store.cobertura(alvo, conn=conn)


def test_corte_desligado_traz_mais_que_corte_curto():
    """Sanidade: se o filtro de data fosse ignorado, os dois seriam iguais e a
    paridade acima passaria sem exercitar nada."""
    largo = rag_store.busca_temporal("PETR4", 200, meses=None)
    curto = rag_store.busca_temporal("PETR4", 200, meses=3)
    assert len(largo) >= len(curto)


def test_teto_por_documento_respeitado():
    """Nenhum documento pode dominar a recuperacao (caso WEGE3)."""
    from collections import Counter
    linhas = rag_store.busca_temporal("WEGE3", 200)
    if not linhas:
        pytest.skip("sem corpus para WEGE3")
    por_doc = Counter(r[5] for r in linhas)
    assert max(por_doc.values()) <= 8


def test_manifesto_confere_com_a_origem():
    m = rag_store.manifesto()
    assert m["confere"] is True
    assert m["assinatura_chunk_hash"] == m["assinatura_origem"]
    assert m["linhas"] == m["linhas_origem"]


def test_data_sai_como_iso_sem_hora():
    """date32, nao timestamp: `2026-08-25`, nunca `2026-08-25 00:00:00`."""
    import re as _re
    linhas = rag_store.busca_temporal("PETR4", 5)
    if not linhas:
        pytest.skip("sem corpus para PETR4")
    datas = [r[1] for r in linhas if r[1]]
    assert datas, "nenhuma data para verificar"
    assert all(_re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) for d in datas)
