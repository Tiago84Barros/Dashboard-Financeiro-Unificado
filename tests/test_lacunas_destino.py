"""Destinos do log de lacunas: arquivo local e tabela `app_lacunas`.

A escolha NAO passa por `e_local(get_engine())`: na maquina local o
`get_engine()` tambem e o Supabase, e essa regra mandaria tudo para a nuvem.
"""
import json
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from core.lacunas import destino
from core.lacunas.destino import escolher_destino, gravar_banco, gravar_local
from core.lacunas.evento import construir_lacuna

_MIGRATION = (Path(__file__).resolve().parent.parent
              / "supabase_unificado" / "schema" / "077_app_lacunas.sql")


def _lacuna(**kw):
    base = dict(fonte="motor", codigo="fii.vpa", mensagem="sem VPA", modulo="core/x.py:f",
                entidade="HGLG11", contexto={"tabela": "fii_metrics"})
    base.update(kw)
    return construir_lacuna(**base)


# ── escolher_destino ─────────────────────────────────────────────────────────

def test_variavel_de_ambiente_vence_tudo():
    env = {"LACUNAS_DESTINO": "SUPABASE", "PYTEST_CURRENT_TEST": "x"}
    assert escolher_destino(env, Path("/home/eu/repo")) == "supabase"


def test_sob_pytest_o_padrao_e_desligado():
    assert escolher_destino({"PYTEST_CURRENT_TEST": "x"}, Path("/mount/src/app")) == "desligado"


def test_repositorio_em_mount_src_e_a_streamlit_cloud():
    assert escolher_destino({}, Path("/mount/src/dashboard")) == "supabase"


def test_qualquer_outro_caso_e_local():
    assert escolher_destino({}, Path(r"C:\Users\eu\repo")) == "local"


def test_valor_invalido_na_variavel_e_ignorado():
    assert escolher_destino({"LACUNAS_DESTINO": "nuvem"}, Path("/home/eu/repo")) == "local"


# ── gravar_local ─────────────────────────────────────────────────────────────

def test_gravar_local_cria_diretorio_e_acrescenta_linhas(tmp_path):
    arquivo = tmp_path / "sub" / "eventos.jsonl"
    gravar_local(_lacuna(), arquivo)
    gravar_local(_lacuna(mensagem="outra"), arquivo)
    linhas = arquivo.read_text(encoding="utf-8").splitlines()
    assert len(linhas) == 2
    evento = json.loads(linhas[0])
    assert evento["codigo"] == "fii.vpa"
    assert evento["contexto"] == {"tabela": "fii_metrics"}
    assert len(evento["impressao"]) == 40


def test_gravar_local_le_o_caminho_padrao_na_hora_da_chamada(tmp_path, monkeypatch):
    arquivo = tmp_path / "eventos.jsonl"
    monkeypatch.setattr(destino, "ARQUIVO_LOCAL", arquivo)
    gravar_local(_lacuna())
    assert arquivo.exists()


# ── gravar_banco (SQLite imitando app_lacunas) ───────────────────────────────

@pytest.fixture
def engine():
    eng = create_engine("sqlite+pysqlite:///:memory:", future=True)
    with eng.begin() as con:
        con.execute(text("""
            CREATE TABLE app_lacunas (
                impressao TEXT PRIMARY KEY, fonte TEXT, modulo TEXT, codigo TEXT,
                entidade TEXT, ultima_mensagem TEXT, contexto TEXT,
                primeira_vez TEXT, ultima_vez TEXT, ocorrencias INTEGER,
                status TEXT, reincidente BOOLEAN, pr_url TEXT, nota_triagem TEXT)
        """))
    return eng


def _linha(eng, impressao):
    with eng.connect() as con:
        return con.execute(text("SELECT * FROM app_lacunas WHERE impressao = :i"),
                           {"i": impressao}).mappings().one()


def test_primeira_gravacao_cria_linha_aberta(engine):
    lac = _lacuna()
    gravar_banco(engine, lac)
    row = _linha(engine, lac.impressao)
    assert row["ocorrencias"] == 1
    assert row["status"] == "aberta"
    assert not row["reincidente"]
    assert json.loads(row["contexto"]) == {"tabela": "fii_metrics"}


def test_segunda_gravacao_soma_e_atualiza_mensagem(engine):
    gravar_banco(engine, _lacuna(mensagem="faltam 12"))
    lac = _lacuna(mensagem="faltam 13")
    gravar_banco(engine, lac)
    row = _linha(engine, lac.impressao)
    assert row["ocorrencias"] == 2
    assert row["ultima_mensagem"] == "faltam 13"


def test_resolvida_que_reaparece_reabre_como_reincidente(engine):
    lac = _lacuna()
    gravar_banco(engine, lac)
    with engine.begin() as con:
        con.execute(text("UPDATE app_lacunas SET status = 'resolvida'"))
    gravar_banco(engine, lac)
    row = _linha(engine, lac.impressao)
    assert row["status"] == "aberta"
    assert row["reincidente"]


@pytest.mark.parametrize("status", ["legitima", "em_pr", "incerta"])
def test_status_triado_so_atualiza_contadores(engine, status):
    lac = _lacuna()
    gravar_banco(engine, lac)
    with engine.begin() as con:
        con.execute(text("UPDATE app_lacunas SET status = :s"), {"s": status})
    gravar_banco(engine, lac)
    row = _linha(engine, lac.impressao)
    assert row["status"] == status
    assert not row["reincidente"]
    assert row["ocorrencias"] == 2


def test_colunas_do_upsert_existem_na_migration():
    ddl = _MIGRATION.read_text(encoding="utf-8")
    colunas = re.search(r"INSERT INTO app_lacunas \(([^)]*)\)", destino._UPSERT).group(1)
    for coluna in (c.strip() for c in colunas.split(",")):
        assert re.search(rf"^\s+{coluna}\s", ddl, re.MULTILINE), coluna
    for status in ("aberta", "legitima", "em_pr", "resolvida", "incerta"):
        assert f"'{status}'" in ddl


# ── detalhe tecnico (05/10/2026) ─────────────────────────────────────────────

def test_detalhe_tecnico_nasce_legitimo_e_fora_da_fila(engine):
    lac = _lacuna(codigo="detalhe.fii.metodologia", fonte="tela")
    gravar_banco(engine, lac)
    assert _linha(engine, lac.impressao)["status"] == "legitima"


def test_detalhe_resolvido_que_reaparece_volta_a_legitimo(engine):
    lac = _lacuna(codigo="detalhe.fii.metodologia", fonte="tela")
    gravar_banco(engine, lac)
    with engine.begin() as con:
        con.execute(text("UPDATE app_lacunas SET status = 'resolvida'"))
    gravar_banco(engine, lac)
    row = _linha(engine, lac.impressao)
    assert row["status"] == "legitima"
    assert row["reincidente"]
