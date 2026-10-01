"""O corpus RAG em Parquet entra na rotina semanal.

Até 29/09/2026 nada o republicava: o Parquet parou em 08/09 enquanto a coleta
CVM passou a gravar todo dia no armazém. Cada republicação vira histórico no
git, então o que importa aqui é só mudar o arquivo que tem dado novo.
"""
import json
from datetime import date, datetime, timezone

import pandas as pd

from core.publicacao_agenda import ALVOS, POR_CHAVE
from scripts.publish_rag_corpus_parquet import gravar_particoes, origem_inalterada


def _corpus(*linhas):
    return pd.DataFrame(
        [{"ticker": t, "root": t[:4], "doc_id": i, "chunk_index": 0,
          "chunk_text": f"texto {i}", "chunk_hash": f"h{i}", "data_doc": d,
          "tipo_doc": "", "titulo": "", "eh_ancora": False, "eh_stub": False}
         for i, (t, d) in enumerate(linhas)])


def test_alvo_semanal_depois_da_coleta_e_declara_o_diretorio():
    alvo = POR_CHAVE["rag_corpus"]
    ordem = [a.chave for a in ALVOS]
    assert alvo.cadencia_dias == 7
    assert ordem.index("cvm_ipe") < ordem.index("rag_corpus")
    assert alvo.passos == (("scripts/publish_rag_corpus_parquet.py",),)
    # Diretório, não arquivos: partição nova ou removida também vai ao commit.
    assert alvo.artefatos == ("data/public/rag",)


def test_particao_por_letra_e_ano(tmp_path):
    df = _corpus(("PETR4", date(2025, 3, 1)), ("PETR4", date(2026, 3, 1)),
                 ("VALE3", date(2026, 5, 1)), ("VALE3", None))
    partes = gravar_particoes(df, tmp_path)
    assert [p["arquivo"] for p in partes] == [
        "chunks_P_2025.parquet", "chunks_P_2026.parquet",
        "chunks_V_2026.parquet", "chunks_V_sem-data.parquet"]
    assert sum(p["linhas"] for p in partes) == 4


def test_particao_sem_dado_novo_sai_byte_a_byte_igual(tmp_path):
    """Senão o git vê as 24+ partições mudadas toda semana."""
    velho = _corpus(("PETR4", date(2025, 3, 1)), ("VALE3", date(2026, 5, 1)))
    gravar_particoes(velho, tmp_path)
    antes = (tmp_path / "chunks_P_2025.parquet").read_bytes()

    novo = _corpus(("PETR4", date(2025, 3, 1)), ("VALE3", date(2026, 5, 1)),
                   ("VALE3", date(2026, 9, 1)))
    gravar_particoes(novo, tmp_path)
    assert (tmp_path / "chunks_P_2025.parquet").read_bytes() == antes


def test_particao_que_deixou_de_existir_e_apagada(tmp_path):
    gravar_particoes(_corpus(("PETR4", date(2025, 3, 1))), tmp_path)
    gravar_particoes(_corpus(("VALE3", date(2026, 5, 1))), tmp_path)
    assert sorted(p.name for p in tmp_path.glob("*.parquet")) == ["chunks_V_2026.parquet"]


def test_origem_igual_nao_republica(tmp_path):
    assinatura = {"assinatura": "abc", "n": 10}
    anterior = {"confere": True, "assinatura_origem": "abc", "linhas_origem": 10}
    assert not origem_inalterada(anterior, assinatura, tmp_path)  # sem partição
    (tmp_path / "chunks_P_2026.parquet").write_bytes(b"x")
    assert origem_inalterada(anterior, assinatura, tmp_path)
    assert not origem_inalterada(anterior, {"assinatura": "def", "n": 10}, tmp_path)
    assert not origem_inalterada({**anterior, "confere": False}, assinatura, tmp_path)
    assert not origem_inalterada(None, assinatura, tmp_path)


def test_carimbo_le_o_manifesto_json_sem_gzip(tmp_path, monkeypatch):
    from scripts import atualizar_vitrines as av

    (tmp_path / "manifesto.json").write_text(
        json.dumps({"gerado_em": "2026-09-29T23:00:00+00:00"}), encoding="utf-8")
    monkeypatch.setattr(av, "ROOT", tmp_path)
    assert av._carimbo_do_arquivo("manifesto.json") == datetime(
        2026, 9, 29, 23, tzinfo=timezone.utc)
