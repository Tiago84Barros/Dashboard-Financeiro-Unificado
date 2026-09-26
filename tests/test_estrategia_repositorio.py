"""Repositório da política: ciclo de vida das versões (SQLite)."""
import datetime as dt

import pytest
from sqlalchemy import create_engine, text

from core.estrategia import politica as pol
from core.estrategia import repositorio as repo

DONO = "33333333-3333-3333-3333-333333333333"
OUTRO = "44444444-4444-4444-4444-444444444444"

MINIMOS = {
    "objective": "crescimento_patrimonial",
    "time_horizon": "muito_longo",
    "risk_profile": "arrojado",
    "liquidity_need": "baixa",
    "predominant_strategy": "crescimento",
    "asset_class_targets": {"renda_fixa": 10, "acoes_br": 50, "fiis": 10,
                            "exterior": 30},
}


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as conn:
        conn.execute(text("""
            CREATE TABLE investment_policies (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                version INTEGER NOT NULL, schema_version TEXT NOT NULL,
                status TEXT NOT NULL,
                policy_json TEXT NOT NULL DEFAULT '{}',
                interview_json TEXT NOT NULL DEFAULT '[]',
                completion_pct REAL NOT NULL DEFAULT 0,
                completed_at TEXT, created_at TEXT, updated_at TEXT,
                UNIQUE (user_id, version))
        """))
    return eng


def _kw(engine, dono=DONO):
    return {"engine": engine, "owner_id": dono}


def test_sem_linha_e_nao_iniciada(engine):
    estado = repo.carregar(**_kw(engine))
    assert estado.status == pol.NOT_STARTED
    assert estado.vigente is None and estado.rascunho is None


def test_tabela_ausente_degrada_sem_levantar():
    eng = create_engine("sqlite:///:memory:")
    assert repo.carregar(**_kw(eng)).tabela_ausente
    with pytest.raises(repo.TabelaAusente):
        repo.iniciar(**_kw(eng))


def test_iniciar_e_idempotente_e_rascunho_vazio_nao_conclui(engine):
    r1 = repo.iniciar(**_kw(engine))
    r2 = repo.iniciar(**_kw(engine))
    assert r1.id == r2.id and r1.version == 1
    assert repo.carregar(**_kw(engine)).status == pol.IN_PROGRESS
    ok, erros = repo.concluir(r1.id, **_kw(engine))
    assert not ok and len(erros) == len(pol.OBRIGATORIOS)
    assert repo.carregar(**_kw(engine)).vigente is None


def test_ciclo_completo_com_versao_e_arquivamento(engine):
    r1 = repo.iniciar(**_kw(engine))
    politica, _ = pol.aplicar({}, MINIMOS, fonte="entrevista")
    conversa = [{"role": "assistant", "content": "Qual o objetivo?"},
                {"role": "user", "content": "crescer"}]
    salvo = repo.salvar_rascunho(r1.id, politica, conversa, **_kw(engine))
    assert salvo.completion_pct == 100 and salvo.entrevista == conversa

    assert repo.concluir(r1.id, **_kw(engine)) == (True, [])
    estado = repo.carregar(**_kw(engine))
    assert estado.status == pol.COMPLETED and estado.rascunho is None
    assert estado.vigente.version == 1 and estado.vigente.completed_at
    assert repo.politica_para_analise(**_kw(engine)).id == r1.id

    # Editar: nova versão nasce como cópia; a vigente continua valendo.
    r2 = repo.iniciar(**_kw(engine))
    assert r2.version == 2 and r2.politica == estado.vigente.politica
    estado = repo.carregar(**_kw(engine))
    assert estado.vigente.version == 1 and estado.rascunho.version == 2
    assert repo.politica_para_analise(**_kw(engine)).version == 1

    nova, _ = pol.aplicar(r2.politica, {"risk_profile": "moderado"},
                          fonte="manual")
    repo.salvar_rascunho(r2.id, nova, [], **_kw(engine))
    assert repo.concluir(r2.id, **_kw(engine)) == (True, [])
    estado = repo.carregar(**_kw(engine))
    assert estado.vigente.version == 2
    assert pol.valor(estado.vigente.politica, "risk_profile") == "moderado"
    with engine.connect() as conn:
        status = dict(conn.execute(text(
            "SELECT version, status FROM investment_policies")).fetchall())
    assert status == {1: "ARCHIVED", 2: "COMPLETED"}


def test_descartar_edicao_preserva_a_vigente(engine):
    r1 = repo.iniciar(**_kw(engine))
    politica, _ = pol.aplicar({}, MINIMOS, fonte="manual")
    repo.salvar_rascunho(r1.id, politica, [], **_kw(engine))
    repo.concluir(r1.id, **_kw(engine))
    r2 = repo.iniciar(**_kw(engine))
    assert repo.descartar_rascunho(r2.id, **_kw(engine))
    assert not repo.descartar_rascunho(r1.id, **_kw(engine))  # concluída não
    estado = repo.carregar(**_kw(engine))
    assert estado.vigente.id == r1.id and estado.rascunho is None


def test_um_usuario_nao_toca_o_rascunho_do_outro(engine):
    r1 = repo.iniciar(**_kw(engine))
    with pytest.raises(LookupError):
        repo.salvar_rascunho(r1.id, {}, [], **_kw(engine, OUTRO))
    assert repo.concluir(r1.id, **_kw(engine, OUTRO))[0] is False
    assert not repo.descartar_rascunho(r1.id, **_kw(engine, OUTRO))
    assert repo.carregar(**_kw(engine, OUTRO)).status == pol.NOT_STARTED


def test_transcricao_e_limitada(engine):
    r1 = repo.iniciar(**_kw(engine))
    conversa = [{"role": "user", "content": str(i)} for i in range(100)]
    salvo = repo.salvar_rascunho(r1.id, {}, conversa, **_kw(engine))
    assert len(salvo.entrevista) == repo.MAX_MENSAGENS
    assert salvo.entrevista[-1]["content"] == "99"


def test_concluida_vencida_aparece_como_revisao(engine):
    r1 = repo.iniciar(**_kw(engine))
    politica, _ = pol.aplicar({}, MINIMOS, fonte="manual")
    repo.salvar_rascunho(r1.id, politica, [], **_kw(engine))
    repo.concluir(r1.id, **_kw(engine))
    velha = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=400)
    with engine.begin() as conn:
        conn.execute(text("UPDATE investment_policies SET completed_at = :d"),
                     {"d": velha.isoformat()})
    estado = repo.carregar(**_kw(engine))
    assert estado.status == pol.NEEDS_REVIEW
    assert repo.politica_para_analise(**_kw(engine)) is None
