"""
core/estrategia/repositorio.py
Persistência da política de investimentos (tabela ``investment_policies``).

Único módulo do pacote com SQL. Compatível com PostgreSQL (produção) e SQLite
(testes), no mesmo padrão de ``core/portfolio/repository.py`` — de onde vêm o
engine, o dono e o cast de JSONB, para não haver uma segunda forma de
resolver conexão ou usuário.

Ciclo de vida de uma versão::

    (nenhuma linha)  --iniciar-->  IN_PROGRESS  --concluir-->  COMPLETED
    COMPLETED  --iniciar (editar)-->  nova versão IN_PROGRESS, cópia da vigente
    nova versão concluída  -->  a anterior vira ARCHIVED

A vigente continua valendo enquanto a edição não é concluída: a análise
nunca lê um rascunho.

Coberto por tests/test_estrategia_repositorio.py.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy import text

from core.estrategia import politica as pol
from core.portfolio.repository import (
    _decode,
    _json_placeholder,
    _resolve_engine,
    _resolve_owner,
)

logger = logging.getLogger(__name__)

TABELA = "investment_policies"
MAX_MENSAGENS = 60
MIGRATION = "supabase_unificado/schema/076_investment_policies.sql"

_COLUNAS = ("id, version, status, schema_version, policy_json, interview_json, "
            "completion_pct, completed_at, created_at, updated_at")


class TabelaAusente(RuntimeError):
    """A migration 076 ainda não foi rodada neste banco."""


@dataclass(frozen=True)
class Registro:
    id: str
    version: int
    status_gravado: str
    schema_version: str
    politica: dict
    entrevista: list
    completion_pct: float
    completed_at: dt.datetime | None
    created_at: dt.datetime | None
    updated_at: dt.datetime | None

    @property
    def status(self) -> str:
        return pol.status_efetivo(
            self.status_gravado, self.politica,
            schema_version=self.schema_version, completed_at=self.completed_at)

    @property
    def progresso(self) -> pol.Progresso:
        return pol.progresso(self.politica)


@dataclass(frozen=True)
class Estado:
    vigente: Registro | None = None
    rascunho: Registro | None = None
    tabela_ausente: bool = False
    erro: str | None = field(default=None)

    @property
    def status(self) -> str:
        if self.vigente is not None:
            return self.vigente.status
        if self.rascunho is not None:
            return pol.IN_PROGRESS
        return pol.NOT_STARTED

    @property
    def em_edicao(self) -> Registro | None:
        return self.rascunho


# -- apoio ---------------------------------------------------------------------

def _agora() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _data(valor) -> dt.datetime | None:
    if valor is None or isinstance(valor, dt.datetime):
        return valor
    try:
        return dt.datetime.fromisoformat(str(valor))
    except ValueError:
        return None


def _tabela_ausente(exc: Exception) -> bool:
    msg = str(exc).lower()
    return TABELA in msg and ("does not exist" in msg or "no such table" in msg)


def _registro(row) -> Registro:
    m = row._mapping
    return Registro(
        id=str(m["id"]),
        version=int(m["version"]),
        status_gravado=str(m["status"]),
        schema_version=str(m["schema_version"]),
        politica=_decode(m["policy_json"]) or {},
        entrevista=_decode(m["interview_json"]) or [],
        completion_pct=float(m["completion_pct"] or 0),
        completed_at=_data(m["completed_at"]),
        created_at=_data(m["created_at"]),
        updated_at=_data(m["updated_at"]),
    )


def _linhas(conn, owner: str) -> list[Registro]:
    rows = conn.execute(text(
        f"SELECT {_COLUNAS} FROM {TABELA} WHERE user_id = :uid "
        "AND status <> 'ARCHIVED' ORDER BY version DESC"), {"uid": owner})
    return [_registro(r) for r in rows]


def _estado(registros: list[Registro]) -> Estado:
    rascunho = next((r for r in registros
                     if r.status_gravado == pol.IN_PROGRESS), None)
    vigente = next((r for r in registros
                    if r.status_gravado in (pol.COMPLETED, pol.NEEDS_REVIEW)),
                   None)
    return Estado(vigente=vigente, rascunho=rascunho)


def _ler(conn, registro_id: str, owner: str) -> Registro | None:
    row = conn.execute(text(
        f"SELECT {_COLUNAS} FROM {TABELA} WHERE id = :id AND user_id = :uid"),
        {"id": registro_id, "uid": owner}).fetchone()
    return _registro(row) if row else None


# -- leitura -------------------------------------------------------------------

def carregar(*, engine=None, owner_id=None) -> Estado:
    """Vigente e rascunho do usuário. Nunca levanta por tabela ausente.

    Sem a migration 076 o estado volta com ``tabela_ausente=True``: a tela
    avisa qual SQL rodar, e o resto de Configurações segue funcionando.
    """
    eng = _resolve_engine(engine)
    owner = _resolve_owner(owner_id)
    try:
        with eng.connect() as conn:
            return _estado(_linhas(conn, owner))
    except Exception as exc:  # noqa: BLE001
        if _tabela_ausente(exc):
            return Estado(tabela_ausente=True)
        raise


def politica_para_analise(*, engine=None, owner_id=None) -> Registro | None:
    """A política que a análise pode usar: só a vigente, e só se concluída.

    Rascunho nunca; concluída que caiu para revisão também não — quem decide
    usar mesmo assim é quem chama, vendo o status, não este atalho.
    """
    estado = carregar(engine=engine, owner_id=owner_id)
    vigente = estado.vigente
    if vigente is None or vigente.status != pol.COMPLETED:
        return None
    return vigente


# -- escrita -------------------------------------------------------------------

def iniciar(*, engine=None, owner_id=None) -> Registro:
    """Rascunho para trabalhar: o que já existe, ou uma versão nova.

    Havendo política vigente, o rascunho nasce como CÓPIA dela (version + 1):
    editar é mudar o que já foi dito, não recomeçar do zero.
    """
    eng = _resolve_engine(engine)
    owner = _resolve_owner(owner_id)
    ph_pol = _json_placeholder(eng).replace(":payload", ":pol")
    ph_ent = _json_placeholder(eng).replace(":payload", ":ent")
    try:
        with eng.begin() as conn:
            estado = _estado(_linhas(conn, owner))
            if estado.rascunho is not None:
                return estado.rascunho
            maior = conn.execute(text(
                f"SELECT MAX(version) FROM {TABELA} WHERE user_id = :uid"),
                {"uid": owner}).scalar()
            base = estado.vigente.politica if estado.vigente else {}
            agora = _agora()
            novo_id = str(uuid.uuid4())
            conn.execute(text(
                f"INSERT INTO {TABELA} (id, user_id, version, schema_version, "
                "status, policy_json, interview_json, completion_pct, "
                "created_at, updated_at) VALUES (:id, :uid, :ver, :sv, "
                f"'IN_PROGRESS', {ph_pol}, {ph_ent}, :pct, :agora, :agora)"),
                {"id": novo_id, "uid": owner, "ver": int(maior or 0) + 1,
                 "sv": pol.SCHEMA_VERSION, "pol": json.dumps(base),
                 "ent": "[]", "pct": pol.progresso(base).pct, "agora": agora})
            return _ler(conn, novo_id, owner)
    except Exception as exc:  # noqa: BLE001
        if _tabela_ausente(exc):
            raise TabelaAusente(MIGRATION) from exc
        raise


def salvar_rascunho(registro_id: str, politica: dict, entrevista: list, *,
                    engine=None, owner_id=None) -> Registro:
    """Grava o rascunho. Só toca linha IN_PROGRESS do próprio usuário."""
    eng = _resolve_engine(engine)
    owner = _resolve_owner(owner_id)
    ph_pol = _json_placeholder(eng).replace(":payload", ":pol")
    ph_ent = _json_placeholder(eng).replace(":payload", ":ent")
    entrevista = list(entrevista or [])[-MAX_MENSAGENS:]
    with eng.begin() as conn:
        n = conn.execute(text(
            f"UPDATE {TABELA} SET policy_json = {ph_pol}, "
            f"interview_json = {ph_ent}, completion_pct = :pct, "
            "schema_version = :sv, updated_at = :agora "
            "WHERE id = :id AND user_id = :uid AND status = 'IN_PROGRESS'"),
            {"pol": json.dumps(politica or {}), "ent": json.dumps(entrevista),
             "pct": pol.progresso(politica).pct, "sv": pol.SCHEMA_VERSION,
             "agora": _agora(), "id": registro_id, "uid": owner}).rowcount
        if n != 1:
            raise LookupError("Rascunho não encontrado ou já concluído.")
        return _ler(conn, registro_id, owner)


def concluir(registro_id: str, *, engine=None, owner_id=None
             ) -> tuple[bool, list[str]]:
    """Conclui o rascunho se os mínimos passam; arquiva a versão anterior.

    A validação roda aqui, sobre o que está GRAVADO — não sobre o que a tela
    acha que tem. Um clique em "Concluir" com a política incompleta não
    produz um COMPLETED vazio.
    """
    eng = _resolve_engine(engine)
    owner = _resolve_owner(owner_id)
    with eng.begin() as conn:
        rascunho = _ler(conn, registro_id, owner)
        if rascunho is None or rascunho.status_gravado != pol.IN_PROGRESS:
            return False, ["Rascunho não encontrado ou já concluído."]
        erros = pol.erros_de_conclusao(rascunho.politica)
        if erros:
            return False, erros
        agora = _agora()
        conn.execute(text(
            f"UPDATE {TABELA} SET status = 'ARCHIVED', updated_at = :agora "
            "WHERE user_id = :uid AND id <> :id "
            "AND status IN ('COMPLETED', 'NEEDS_REVIEW')"),
            {"agora": agora, "uid": owner, "id": registro_id})
        conn.execute(text(
            f"UPDATE {TABELA} SET status = 'COMPLETED', completed_at = :agora, "
            "updated_at = :agora, completion_pct = :pct, schema_version = :sv "
            "WHERE id = :id AND user_id = :uid"),
            {"agora": agora, "pct": pol.progresso(rascunho.politica).pct,
             "sv": pol.SCHEMA_VERSION, "id": registro_id, "uid": owner})
    return True, []


def descartar_rascunho(registro_id: str, *, engine=None, owner_id=None) -> bool:
    """Apaga só o rascunho. A versão vigente não é tocada."""
    eng = _resolve_engine(engine)
    owner = _resolve_owner(owner_id)
    with eng.begin() as conn:
        n = conn.execute(text(
            f"DELETE FROM {TABELA} WHERE id = :id AND user_id = :uid "
            "AND status = 'IN_PROGRESS'"),
            {"id": registro_id, "uid": owner}).rowcount
    return n == 1
