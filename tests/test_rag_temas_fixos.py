"""Lacuna c74be6f0: o chat de ISAE3 não tinha custo médio e prazo médio da
dívida do 2T26, embora o release de resultados com esses números esteja no
corpus publicado. A busca temporal pega os documentos mais novos (rating,
agente fiduciário, comunicados) e o trecho de dívida do release fica abaixo do
corte. Dívida e geração de caixa são temas fixos da análise: entram por busca
de termos, como as âncoras entram por tipo de documento."""
import json
from datetime import date, timedelta

import pandas as pd
import pytest

import core.rag_b3 as rag_b3
import core.rag_store as rag_store

TRECHO_DIVIDA = ("A dívida bruta encerrou o 2T26 em R$ 31,3 bilhões, com custo "
                 "médio de CDI + 0,9% a.a. e prazo médio de 7,2 anos; o "
                 "cronograma de amortização concentra 12% em 2027.")
TRECHO_CAIXA = ("O fluxo de caixa operacional somou R$ 1,8 bilhão no trimestre, "
                "e a geração de caixa cobriu 1,4x os juros pagos.")


def _linha(i, doc, data, tipo, titulo, texto, ancora=False):
    return {"ticker": "ISAE3", "root": "ISAE", "doc_id": doc, "chunk_index": i,
            "chunk_text": texto, "chunk_hash": f"{doc}-{i}", "data_doc": data,
            "tipo_doc": tipo, "titulo": titulo, "eh_ancora": ancora,
            "eh_stub": False}


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    hoje = date.today()
    linhas = []
    # 60 comunicados mais novos que o release, sem nada de dívida/caixa.
    for d in range(60):
        dia = hoje - timedelta(days=d + 1)
        linhas.append(_linha(0, 1000 + d, dia, "Comunicado ao Mercado",
                             f"Comunicado {d}",
                             f"A companhia informa a eleição do conselheiro {d} "
                             f"em reunião realizada em {dia:%d/%m/%Y} com 9 membros."))
    # Como no corpus real: o release tem dezenas de trechos e a dívida vem
    # depois dos 8 primeiros, que são o que a âncora alcança.
    rel = hoje - timedelta(days=60)
    for i in range(13):
        linhas.append(_linha(i, 1, rel, "Press-release", "Release de resultados 2T26",
                             f"Receita líquida da linha {i} no 2T26 de R$ 1,{i} "
                             f"bilhão, alta de {i}% sobre o 2T25.", ancora=True))
    # Menção de passagem antes do trecho com os números (como o 38 do real).
    linhas.append(_linha(13, 1, rel, "Press-release", "Release de resultados 2T26",
                         "A emissão contribuiu para alongar o prazo médio das "
                         "dívidas pré-pagas em 2,7 anos e reduzir o spread.",
                         ancora=True))
    linhas.append(_linha(14, 1, rel, "Press-release", "Release de resultados 2T26",
                         TRECHO_DIVIDA, ancora=True))
    linhas.append(_linha(15, 1, rel, "Press-release", "Release de resultados 2T26",
                         TRECHO_CAIXA, ancora=True))
    df = pd.DataFrame(linhas)
    df["doc_id"] = df["doc_id"].astype("int64")
    df.to_parquet(tmp_path / "chunks_i_2026.parquet", index=False)
    (tmp_path / "manifesto.json").write_text(json.dumps({"confere": True}),
                                             encoding="utf-8")
    monkeypatch.setattr(rag_store, "DIR_PARQUET", tmp_path)
    rag_store.manifesto.cache_clear()
    monkeypatch.setattr(rag_b3, "_has_embeddings", lambda tk: False)
    yield
    rag_store.manifesto.cache_clear()


def test_trecho_de_divida_do_release_chega_ao_contexto(corpus):
    chunks, stats = rag_b3.retrieve_chunks("ISAE3", top_k_total=30,
                                           per_topic_k=3, months_back=48)
    textos = [c["chunk_text"] for c in chunks]
    assert any("prazo médio de 7,2 anos" in t for t in textos)
    assert any("fluxo de caixa operacional" in t for t in textos)
    assert stats["mode"] == "temporal"
    assert stats.get("tematicos", 0) >= 2


def test_trecho_tematico_sobrevive_a_formatacao_do_chat(corpus):
    from core.llm_context_b3 import get_chunks_context
    texto = get_chunks_context("ISAE3 vale a pena?", ["ISAE3"], None)
    assert "prazo médio de 7,2 anos" in texto


def test_busca_por_termos_nao_le_outro_root(corpus):
    linhas = rag_store.busca_termos("SOND", ("%prazo médio%",), 5, 48)
    assert linhas == []


def test_no_mesmo_documento_o_trecho_mais_denso_vem_primeiro(corpus):
    hits = [h for h in rag_b3._search_tematico(None, "ISAE3", 48)
            if h["topic"] == "divida"]
    assert hits[0]["chunk_text"].startswith("A dívida bruta encerrou o 2T26")
