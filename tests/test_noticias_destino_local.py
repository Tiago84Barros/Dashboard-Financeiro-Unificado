"""O acervo de notícias não pode ir para o Supabase, e ler não pode criar tabela.

A restrição é de tamanho, não de gosto: ~11 mil itens por janela de 30 dias a
~2 KB dão ~22 MB por janela, acumulando, contra 71 MB de folga no Supabase.
Um ``engine=`` distraído não pode ser suficiente para encher o banco de que a
produção depende -- e por isso a recusa é testada, não só documentada.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from core.destino_local import DestinoRemotoRecusado, e_local, exigir_local


def _eng(url: str):
    """Engine que nunca conecta: a guarda olha a URL, não o servidor."""
    return create_engine(url)


# --------------------------------------------------------------- a guarda ---
@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "0.0.0.0",
                                  "host.docker.internal", "dfu_warehouse"])
def test_hosts_do_armazem_sao_locais(host):
    assert e_local(_eng(f"postgresql://u:p@{host}:5433/postgres"))


@pytest.mark.parametrize("url", [
    "postgresql://u:p@db.abcdefgh.supabase.co:5432/postgres",
    "postgresql://u:p@aws-0-sa-east-1.pooler.supabase.com:6543/postgres",
    "postgresql://u:p@ep-x.neon.tech/db",
    "postgresql://u:p@algo.amazonaws.com:5432/db",
])
def test_destinos_de_nuvem_sao_recusados(url):
    assert not e_local(_eng(url))
    with pytest.raises(DestinoRemotoRecusado):
        exigir_local(_eng(url), o_que="o acervo de notícias")


def test_engine_ausente_e_recusa_e_nao_passe_livre():
    with pytest.raises(DestinoRemotoRecusado):
        exigir_local(None, o_que="o acervo de notícias")


def test_sqlite_em_memoria_e_local_por_construcao():
    assert e_local(create_engine("sqlite://"))


def test_a_recusa_diz_o_que_se_tentava_gravar():
    with pytest.raises(DestinoRemotoRecusado) as exc:
        exigir_local(_eng("postgresql://u:p@db.x.supabase.co/postgres"),
                     o_que="o acervo de notícias")
    assert "acervo de notícias" in str(exc.value)


def test_a_recusa_nao_vaza_a_senha():
    segredo = "SenhaSuperSecreta123"
    with pytest.raises(DestinoRemotoRecusado) as exc:
        exigir_local(_eng(f"postgresql://u:{segredo}@db.x.supabase.co/postgres"),
                     o_que="o acervo de notícias")
    assert segredo not in str(exc.value)


# ------------------------------------------------------ gravar e recusar ----
def test_gravar_recusa_destino_remoto():
    from core.noticias.armazenamento import gravar
    from core.noticias.coleta import ResultadoColeta

    remoto = _eng("postgresql://u:p@db.abcdefgh.supabase.co:5432/postgres")
    with pytest.raises(DestinoRemotoRecusado):
        gravar(ResultadoColeta(),
               engine=remoto)


def test_sem_acervo_configurado_nao_grava_e_diz_o_motivo(monkeypatch):
    from core.noticias import armazenamento as A
    from core.noticias.coleta import ResultadoColeta

    monkeypatch.setattr(A, "engine_acervo", lambda: None)
    resumo = A.gravar(ResultadoColeta())
    assert resumo["gravado"] is False
    assert resumo["motivo"] == "sem banco configurado"


# ------------------------------------------- ler não cria, e falha aparece ---
def test_ler_falha_levanta_em_vez_de_devolver_vazio():
    from core.noticias.armazenamento import AcervoIlegivel, ler_recentes

    # Banco que não existe: a leitura tem de falhar como falha.
    morto = _eng("postgresql://u:p@localhost:5433/banco_que_nao_existe_dfu")
    with pytest.raises(AcervoIlegivel):
        ler_recentes(engine=morto)


def test_ler_nao_cria_tabela():
    """Ler é leitura. Criar no caminho de leitura gastava espaço numa consulta
    e fazia "a tabela não existe" ficar igual a "não há notícias"."""
    from core.noticias.armazenamento import AcervoIlegivel, ler_recentes

    eng = create_engine("sqlite://")
    with pytest.raises(AcervoIlegivel):
        ler_recentes(engine=eng)
    with eng.connect() as c:
        tabelas = [r[0] for r in c.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table'"))]
    assert tabelas == [], f"a leitura criou tabelas: {tabelas}"
