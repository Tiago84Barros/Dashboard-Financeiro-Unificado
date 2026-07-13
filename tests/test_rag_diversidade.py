"""RAG: diversidade por documento e rebaixamento de dumps tabulares.

Caso real (WEGE3): um ITR em inglês de 46 chunks ranqueava como sinal ALTO
("itr"/"demonstra" na lista HIGH) e monopolizava os 12k chars do contexto,
expulsando Release de Resultados / Fato Relevante / Transcrição — causa direta
dos relatórios LLM genéricos.
"""
from core.rag_b3 import _signal_rank, format_rag_context


def _chunk(doc_id, tipo, titulo, texto, data="2026-05-01"):
    return {"doc_id": doc_id, "tipo_doc": tipo, "titulo": titulo,
            "chunk_text": texto, "data_doc": data, "dist": None}


def test_signal_rank_rebaixa_dump_tabular():
    assert _signal_rank("Demonstrações Financeiras Adicionais", "Versão em Inglês") == 0
    assert _signal_rank("Press-release", "Release de Resultados 1T26") == 3
    assert _signal_rank("Fato Relevante", "Investimentos de R$ 1,1 bilhão") == 3
    assert _signal_rank("Apresentações a analistas", "Transcrição da Teleconferência") == 2
    assert _signal_rank("AGO", "Assembleia") == 1
    # dump fica ABAIXO até de assembleia
    assert _signal_rank("Demonstrações Financeiras Adicionais", "") < _signal_rank("AGO", "")


def test_format_cap_por_documento():
    texto_util = "Receita do trimestre atingiu R$ 9,4 bilhões, queda de 6,1%. " * 4
    dump = [_chunk("ITR1", "Demonstrações Financeiras Adicionais", "Versão em Inglês",
                   f"1.01.0{i} Total assets 23.434.413 balanço linha {i} " * 6)
            for i in range(30)]
    release = [_chunk("REL1", "Press-release", "Release de Resultados 1T26", texto_util)]
    fato = [_chunk("FR1", "Fato Relevante", "Capex R$ 1,1 bi", texto_util)]
    out = format_rag_context(dump + release + fato, max_chars=12000, max_por_doc=8)
    # release e fato SEMPRE entram (sinal alto > dump)
    assert "Release de Resultados 1T26" in out
    assert "Capex R$ 1,1 bi" in out
    # o dump não passa do teto por documento
    assert out.count("Versão em Inglês") <= 8


def test_format_sem_doc_id_usa_chave_composta():
    a = [dict(_chunk(None, "AGO", "Assembleia", f"pauta item {i} deliberação " * 8),
              data_doc="2026-03-01") for i in range(12)]
    b = [dict(_chunk(None, "Fato Relevante", "Dividendos", "JCP de R$ 0,10 por ação " * 8),
              data_doc="2026-06-01")]
    out = format_rag_context(a + b, max_chars=8000, max_por_doc=4)
    assert "Dividendos" in out
    assert out.count("Assembleia") <= 4
