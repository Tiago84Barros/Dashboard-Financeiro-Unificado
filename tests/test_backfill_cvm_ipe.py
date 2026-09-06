"""Seleção balanceada (round-robin entre categorias) do backfill CVM/IPE."""
from collections import Counter
from datetime import date

import pytest

from core.destino_local import DestinoRemotoRecusado
from scripts.backfill_cvm_ipe import (BUCKETS_ANALITICOS, _bucket, _engine,
                                     filtrar_buckets, select_balanced)


def _doc(ticker, categoria, dia):
    return {"ticker": ticker, "categoria": categoria, "data_entrega": date(2025, 1, dia)}


def test_bucket_classifica_categorias():
    assert _bucket("Fato Relevante") == "fato"
    assert _bucket("Dados Econômico-Financeiros") == "resultado"
    assert _bucket("Comunicado ao Mercado") == "comunicado"
    assert _bucket("Aviso aos Acionistas") == "provento"
    assert _bucket("Relatório Proventos") == "provento"
    assert _bucket("Assembleia") == "assembleia"
    assert _bucket("Escrituras e aditamentos de debêntures") == "capital"
    assert _bucket("OPA - Edital de Oferta Pública de Ações") == "capital"
    assert _bucket("Informações de Companhias em Recuperação Judicial") == "critico"
    assert _bucket("Qualquer outra coisa") == "outro"


def test_round_robin_nao_deixa_categoria_dominar():
    # 10 assembleias + 2 fatos + 2 resultados; teto 6 → mix equilibrado (2/2/2),
    # não 6 assembleias.
    docs = ([_doc("PETR4", "Assembleia", d) for d in range(1, 11)]
            + [_doc("PETR4", "Fato Relevante", d) for d in range(1, 3)]
            + [_doc("PETR4", "Dados Econômico-Financeiros", d) for d in range(1, 3)])
    sel = select_balanced(docs, per_ticker=6)
    cats = Counter(_bucket(d["categoria"]) for d in sel)
    assert len(sel) == 6
    assert cats["assembleia"] == 2 and cats["fato"] == 2 and cats["resultado"] == 2


def test_preenche_com_remanescente_quando_outras_esgotam():
    # teto 10: após esgotar fato(2) e resultado(2), assembleia preenche o resto.
    docs = ([_doc("VALE3", "Assembleia", d) for d in range(1, 11)]
            + [_doc("VALE3", "Fato Relevante", d) for d in range(1, 3)]
            + [_doc("VALE3", "Dados Econômico-Financeiros", d) for d in range(1, 3)])
    sel = select_balanced(docs, per_ticker=10)
    cats = Counter(_bucket(d["categoria"]) for d in sel)
    assert len(sel) == 10
    assert cats["fato"] == 2 and cats["resultado"] == 2 and cats["assembleia"] == 6


def test_respeita_recencia_dentro_da_categoria():
    docs = [_doc("WEGE3", "Fato Relevante", d) for d in (5, 1, 9, 3)]
    sel = select_balanced(docs, per_ticker=2)
    # mais recentes primeiro: dia 9 depois dia 5
    assert [d["data_entrega"].day for d in sel] == [9, 5]


def test_separa_por_ticker():
    docs = [_doc("PETR4", "Assembleia", 1), _doc("VALE3", "Assembleia", 1),
            _doc("VALE3", "Fato Relevante", 1)]
    sel = select_balanced(docs, per_ticker=30)
    by_tk = Counter(d["ticker"] for d in sel)
    assert by_tk["PETR4"] == 1 and by_tk["VALE3"] == 2


def test_recorte_analitico_tira_o_administrativo():
    """O recorte existe porque Assembleia e Comunicado ao Mercado, juntos, têm
    mais documentos disponíveis do que todas as categorias analíticas somadas —
    e o round-robin distribuiria a cota entre eles."""
    docs = ([_doc("PETR4", "Assembleia", d) for d in range(1, 6)]
            + [_doc("PETR4", "Comunicado ao Mercado", d) for d in range(1, 6)]
            + [_doc("PETR4", "Fato Relevante", 7)]
            + [_doc("PETR4", "Dados Econômico-Financeiros", 8)]
            + [_doc("PETR4", "Relatório Proventos", 9)])
    mantidos = filtrar_buckets(docs, BUCKETS_ANALITICOS)
    assert {_bucket(d["categoria"]) for d in mantidos} == {"fato", "resultado", "provento"}
    assert len(mantidos) == 3


def test_recorte_vazio_nao_filtra():
    docs = [_doc("PETR4", "Assembleia", 1)]
    assert filtrar_buckets(docs, ()) == docs


def test_recorte_antes_do_round_robin_muda_o_que_sobra():
    """Filtrar depois da seleção devolveria menos do que o teto pedido: a cota
    já teria sido gasta com o que seria descartado."""
    docs = ([_doc("PETR4", "Assembleia", d) for d in range(1, 11)]
            + [_doc("PETR4", "Fato Relevante", d) for d in range(1, 5)])
    antes = select_balanced(filtrar_buckets(docs, BUCKETS_ANALITICOS), 4)
    depois = filtrar_buckets(select_balanced(docs, 4), BUCKETS_ANALITICOS)
    assert len(antes) == 4
    assert len(depois) < 4


def test_destino_remoto_e_recusado_pelo_guarda(monkeypatch):
    """`--destino remoto` só é aceito se a URL não for de nuvem. O corpus não
    cabe no Supabase; o guarda tem que barrar, não o help do argparse."""
    monkeypatch.setenv("SUPABASE_DB_URL_B3",
                       "postgresql://u:p@db.abcdefgh.supabase.co:5432/postgres")
    eng = _engine("remoto")
    assert eng is not None  # destino remoto não passa por exigir_local...
    with pytest.raises(DestinoRemotoRecusado):
        from core.destino_local import exigir_local
        exigir_local(eng, o_que="teste")  # ...mas o guarda o recusaria
