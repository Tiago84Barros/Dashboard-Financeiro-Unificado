"""Contas individuais sobre profiles; SQL parametrizado e senhas scrypt.

Nenhuma migração ou criação de schema em runtime. O perfil já existente do
administrador conserva seu UUID e todos os seus registros financeiros.

Desde 14/11/2026 as contas moram em dois bancos (``core.database.BANCOS``):
quem já existia continua no Supabase principal; quem se cadastra pela tela de
login grava no Supabase ``novos``. O login procura o e-mail nos dois, e a
sessão guarda em ``banco`` onde a pessoa mora, para ``get_engine()`` rotear.
O e-mail é único entre os dois bancos.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from uuid import uuid4

from sqlalchemy import text

from core.config import settings
from core.database import (
    BANCO_NOVOS,
    BANCO_PRINCIPAL,
    banco_da_sessao,
    engine_do_banco,
    novos_configurado,
)
from core.user_context import require_admin, require_user

logger = logging.getLogger(__name__)

#: Teto de cadastros por hora no banco novo, entre todos os processos. O
#: cadastro é aberto a qualquer pessoa; sem teto, um robô enche os 500 MB do
#: plano Free em uma tarde.
_CADASTROS_POR_HORA = 20

_EMAIL_VALIDO = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")


def hash_password(password: str) -> str:
    if len(password) != 8:
        raise ValueError("Use uma senha de exatamente 8 caracteres.")
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return f"scrypt-v1${salt.hex()}${key.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    if not isinstance(password, str) or len(password) > 256:
        return False
    try:
        version, salt, expected = encoded.split("$")
        if version != "scrypt-v1" or len(salt) != 32 or len(expected) != 128:
            return False
        key = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
        return hmac.compare_digest(key.hex(), expected)
    except (ValueError, TypeError):
        return False


def _engine(banco: str | None = None):
    """Engine do banco nomeado; sem nome, o banco da pessoa logada."""
    engine = engine_do_banco(banco or banco_da_sessao())
    if engine is None:
        raise RuntimeError("Banco indisponível. Tente novamente mais tarde.")
    return engine


def _bancos_de_login() -> tuple[str, ...]:
    """Ordem de busca do login: o principal primeiro, onde mora o admin."""
    return (BANCO_PRINCIPAL, BANCO_NOVOS) if novos_configurado() else (BANCO_PRINCIPAL,)


def _extra(value) -> dict:
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


def locked_preferences(conn, user_id: str) -> dict:
    conn.execute(text("INSERT INTO user_settings (user_id) VALUES (:uid) ON CONFLICT (user_id) DO NOTHING"), {"uid": user_id})
    return _extra(conn.execute(text(
        "SELECT extra_settings FROM user_settings WHERE user_id = :uid FOR UPDATE"
    ), {"uid": user_id}).scalar())


def write_preferences(conn, user_id: str, extra: dict) -> None:
    conn.execute(text("UPDATE user_settings SET extra_settings = CAST(:extra AS jsonb), updated_at = NOW() WHERE user_id = :uid"),
                 {"uid": user_id, "extra": json.dumps(extra, ensure_ascii=False)})


def _credential_version(row) -> str:
    credential = str(row["password_hash"])
    if str(row["id"]) == settings.ADMIN_USER_ID and not credential.startswith("scrypt-v1$"):
        credential = settings.APP_PASSWORD
    return hashlib.sha256(credential.encode()).hexdigest()


def authenticate(email: str, password: str) -> dict | None:
    """Cinco falhas bloqueiam a conta por 15 min, inclusive entre processos.

    Procura o e-mail no principal e, se não achar, no banco novo. O banco novo
    fora do ar (projeto Free pausado, por exemplo) não impede quem mora no
    principal de entrar: a falha vai ao log e o login segue como "não achou".
    """
    email = email.strip().lower()
    if len(email) > 255 or len(password) > 256:
        return None
    for banco in _bancos_de_login():
        try:
            achado, user = _autenticar_no_banco(banco, email, password)
        except Exception:
            if banco == BANCO_PRINCIPAL:
                raise
            logger.exception("banco das contas novas inacessível no login")
            continue
        if achado:
            return user
    # Mesmo custo criptográfico para conta inexistente.
    hashlib.scrypt(password.encode(), salt=b"app4-login-dummy", n=16384, r=8, p=1)
    return None


def _autenticar_no_banco(banco: str, email: str, password: str) -> tuple[bool, dict | None]:
    """(achou a conta neste banco, sessão — ou None se a senha não confere)."""
    # O apelido "administrador" só vale no principal, onde o admin mora.
    admin = settings.ADMIN_USER_ID if banco == BANCO_PRINCIPAL else ""
    with _engine(banco).begin() as conn:
        row = conn.execute(text(
            "SELECT id, name, email, password_hash, active FROM profiles "
            "WHERE lower(email) = :email OR (:email = 'administrador' AND id = CAST(:admin AS uuid)) "
            "FOR UPDATE"
        ), {"email": email, "admin": admin or None}).mappings().first()
        if not row or not row["active"]:
            return False, None
        uid = str(row["id"])
        extra = locked_preferences(conn, uid)
        guard = extra.get("app4_login_guard", {})
        now = time.time()
        if guard.get("blocked_until", 0) > now:
            return True, None
        encoded = str(row["password_hash"])
        valid = verify_password(password, encoded)
        # Transição só para o dono original. Nunca aceita o hash como senha.
        if uid == settings.ADMIN_USER_ID and not encoded.startswith("scrypt-v1$"):
            conf = settings.APP_PASSWORD
            valid = bool(conf) and (
                hmac.compare_digest(hashlib.sha256(password.encode()).hexdigest(), conf.lower())
                if re.fullmatch(r"[a-fA-F0-9]{64}", conf)
                else hmac.compare_digest(password, conf)
            )
        if not valid:
            attempts = guard.get("attempts", 0) + 1 if now - guard.get("last", 0) < 900 else 1
            extra["app4_login_guard"] = {"attempts": attempts, "last": now,
                                           "blocked_until": now + 900 if attempts >= 5 else 0}
            write_preferences(conn, uid, extra)
            return True, None
        extra.pop("app4_login_guard", None)
        write_preferences(conn, uid, extra)
        return True, {"id": uid, "name": row["name"], "email": row["email"], "banco": banco,
                      "credential_version": _credential_version(row), "expires_at": now + 12 * 3600}


def session_valid(user: dict) -> bool:
    if not user or user.get("expires_at", 0) <= time.time():
        return False
    banco = BANCO_NOVOS if user.get("banco") == BANCO_NOVOS else BANCO_PRINCIPAL
    with _engine(banco).connect() as conn:
        row = conn.execute(text("SELECT id, password_hash, active FROM profiles WHERE id = :uid"),
                           {"uid": user["id"]}).mappings().first()
    return bool(row and row["active"] and hmac.compare_digest(
        _credential_version(row), user.get("credential_version", "")))


def _validar_cadastro(name: str, email: str) -> tuple[str, str]:
    name, email = name.strip(), email.strip().lower()
    if not name or len(name) > 150 or len(email) > 255 or not _EMAIL_VALIDO.fullmatch(email):
        raise ValueError("Informe nome e e-mail válidos.")
    return name, email


def _email_existe(conn, email: str) -> bool:
    return conn.execute(text("SELECT 1 FROM profiles WHERE lower(email) = :email"),
                        {"email": email}).first() is not None


def cadastrar_usuario(name: str, email: str, password: str) -> str:
    """Cadastro aberto da tela de login; grava SEMPRE no banco novo.

    Não pede administrador: qualquer pessoa cria a própria conta. O principal
    está perto do limite do plano Free, e quem já existe nele fica lá; contas
    novas nascem só no Supabase ``novos``. Sem ele configurado, o cadastro fica
    fechado -- nunca cai no principal.

    O e-mail é único entre os dois bancos: a mesma pessoa em dois bancos faria
    o login escolher um deles em silêncio.
    """
    if not novos_configurado():
        raise RuntimeError("O cadastro de novas contas ainda não está aberto.")
    name, email = _validar_cadastro(name, email)
    encoded = hash_password(password)
    with _engine(BANCO_PRINCIPAL).connect() as conn:
        if _email_existe(conn, email):
            raise ValueError("Este e-mail já está cadastrado.")
    uid = str(uuid4())
    with _engine(BANCO_NOVOS).begin() as conn:
        # Serializa e-mails normalizados sem alterar índices do schema existente.
        conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:email))"), {"email": email})
        if _email_existe(conn, email):
            raise ValueError("Este e-mail já está cadastrado.")
        conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('app4-cadastro-aberto'))"))
        recentes = conn.execute(text(
            "SELECT count(*) FROM profiles WHERE created_at > NOW() - INTERVAL '1 hour'"
        )).scalar() or 0
        if recentes >= _CADASTROS_POR_HORA:
            raise RuntimeError("Muitos cadastros na última hora. Tente novamente mais tarde.")
        conn.execute(text("INSERT INTO profiles (id, name, email, password_hash) VALUES (:uid, :name, :email, :password)"),
                     {"uid": uid, "name": name, "email": email, "password": encoded})
    return uid


def list_users(falhas: list | None = None) -> list[dict]:
    """Perfis cadastrados nos dois bancos, para o administrador conferir.

    ``require_admin`` antes da consulta, e não só no formulário da tela: a
    lista é dado pessoal de outras pessoas (nome e e-mail), e um gate que mora
    apenas na interface protege a interface, não a função.

    ``password_hash`` fica fora do SELECT de propósito -- quem lê a lista quer
    saber quem entra, nunca a credencial.

    Banco novo inacessível não derruba a lista do principal: a falha é
    anexada a ``falhas`` (quando passada) para a tela nomeá-la, em vez de a
    lista sair menor em silêncio.
    """
    require_admin()
    perfis: list[dict] = []
    for banco in _bancos_de_login():
        try:
            with _engine(banco).begin() as conn:
                rows = conn.execute(text(
                    "SELECT id, name, email, created_at, active FROM profiles "
                    "ORDER BY active DESC, lower(name)"
                )).mappings().all()
        except Exception:
            if banco == BANCO_PRINCIPAL:
                raise
            logger.exception("banco das contas novas inacessível na lista de usuários")
            if falhas is not None:
                falhas.append("Banco das contas novas inacessível; as contas dele não aparecem.")
            continue
        perfis.extend({**dict(row), "banco": banco} for row in rows)
    return perfis


def _tabelas_do_usuario(conn) -> list[str]:
    """Tabelas locais de ``public`` com ``user_id``, filhas antes das mães.

    Lê o catálogo em vez de uma lista fixa: tabela nova com ``user_id`` entra
    na exclusão sem ninguém lembrar de cadastrá-la aqui. ``BASE TABLE`` deixa
    de fora as tabelas estrangeiras que o banco novo enxerga do principal pelo
    ``postgres_fdw`` -- apagar por elas apagaria no outro banco.
    """
    tabelas = set(conn.execute(text(
        "SELECT c.table_name FROM information_schema.columns c "
        "JOIN information_schema.tables t "
        "  ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
        "WHERE c.table_schema = 'public' AND c.column_name = 'user_id' "
        "AND t.table_type = 'BASE TABLE'"
    )).scalars())
    tabelas.discard("profiles")
    arestas = conn.execute(text(
        "SELECT ch.relname AS filha, pa.relname AS mae FROM pg_constraint con "
        "JOIN pg_class ch ON ch.oid = con.conrelid JOIN pg_class pa ON pa.oid = con.confrelid "
        "WHERE con.contype = 'f' AND ch.relnamespace = 'public'::regnamespace "
        "AND pa.relnamespace = 'public'::regnamespace"
    )).all()
    # Kahn: uma tabela sai quando nenhuma filha ainda pendente a referencia.
    filhas: dict[str, set[str]] = {t: set() for t in tabelas}
    for filha, mae in arestas:
        if filha != mae and filha in tabelas and mae in tabelas:
            filhas[mae].add(filha)
    ordem: list[str] = []
    pendentes = set(tabelas)
    while pendentes:
        livres = sorted(t for t in pendentes if not (filhas[t] & pendentes))
        if not livres:
            raise RuntimeError("Ciclo de chaves estrangeiras entre tabelas de usuário: "
                               + ", ".join(sorted(pendentes)))
        ordem.extend(livres)
        pendentes -= set(livres)
    return ordem


def _filhas_sem_dono(conn, tabelas: set[str]) -> list[tuple[str, str, list[str], list[str]]]:
    """FKs sem CASCADE vindas de tabelas SEM ``user_id`` para tabelas do usuário.

    Exemplo: ``import_logs.batch_id`` -> ``import_batches``. Essas linhas são
    da pessoa pela mãe, e o DELETE da mãe falharia por causa delas.
    """
    rows = conn.execute(text(
        "SELECT ch.relname AS filha, pa.relname AS mae, "
        "  (SELECT array_agg(a.attname::text ORDER BY k.i) "
        "     FROM unnest(con.conkey) WITH ORDINALITY AS k(n, i) "
        "     JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.n) AS cols, "
        "  (SELECT array_agg(a.attname::text ORDER BY k.i) "
        "     FROM unnest(con.confkey) WITH ORDINALITY AS k(n, i) "
        "     JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.n) AS refs "
        "FROM pg_constraint con "
        "JOIN pg_class ch ON ch.oid = con.conrelid JOIN pg_class pa ON pa.oid = con.confrelid "
        "WHERE con.contype = 'f' AND con.confdeltype IN ('a', 'r') "
        "AND ch.relnamespace = 'public'::regnamespace AND pa.relnamespace = 'public'::regnamespace "
        "AND ch.relkind = 'r'"
    )).all()
    return [(f, m, list(c), list(r)) for f, m, c, r in rows
            if m in tabelas and f not in tabelas and f != "profiles"]


def _ident(nome: str) -> str:
    """Identificador vindo do catálogo, citado; nunca vem de quem usa a tela."""
    return '"' + nome.replace('"', '""') + '"'


def excluir_usuario(user_id: str, banco: str) -> dict[str, int]:
    """Apaga a conta e TODOS os dados dela no banco onde mora. Só administrador.

    Recusa o próprio administrador. Tudo numa transação: ou some a pessoa
    inteira, ou nada some. Devolve as linhas apagadas por tabela, para a tela
    mostrar o que saiu.
    """
    admin = require_admin()
    uid = str(user_id)
    if uid in {admin, settings.ADMIN_USER_ID}:
        raise ValueError("A conta do administrador não pode ser excluída.")
    if banco not in (BANCO_PRINCIPAL, BANCO_NOVOS):
        raise ValueError("Banco desconhecido.")
    apagadas: dict[str, int] = {}
    with _engine(banco).begin() as conn:
        perfil = conn.execute(text("SELECT id FROM profiles WHERE id = :uid FOR UPDATE"),
                              {"uid": uid}).first()
        if perfil is None:
            raise ValueError("Conta não encontrada neste banco.")
        ordem = _tabelas_do_usuario(conn)
        filhas = _filhas_sem_dono(conn, set(ordem))
        for tabela in ordem:
            for filha, mae, cols, refs in filhas:
                if mae != tabela:
                    continue
                alvo = ", ".join(_ident(c) for c in cols)
                origem = ", ".join(_ident(r) for r in refs)
                n = conn.execute(text(
                    f"DELETE FROM {_ident(filha)} WHERE ({alvo}) IN "
                    f"(SELECT {origem} FROM {_ident(mae)} WHERE user_id = :uid)"
                ), {"uid": uid}).rowcount
                if n:
                    apagadas[filha] = apagadas.get(filha, 0) + n
            n = conn.execute(text(f"DELETE FROM {_ident(tabela)} WHERE user_id = :uid"),
                             {"uid": uid}).rowcount
            if n:
                apagadas[tabela] = apagadas.get(tabela, 0) + n
        conn.execute(text("DELETE FROM profiles WHERE id = :uid"), {"uid": uid})
        apagadas["profiles"] = 1
    from core.presenca import esquecer_usuario
    esquecer_usuario(uid)
    return apagadas


def change_password(old_password: str, new_password: str) -> None:
    uid = require_user()
    encoded = hash_password(new_password)
    with _engine().begin() as conn:
        row = conn.execute(text("SELECT email FROM profiles WHERE id = :uid"), {"uid": uid}).first()
    verified = authenticate(row.email, old_password) if row else None
    if not verified or verified["id"] != uid:
        raise ValueError("Não foi possível validar a senha atual.")
    with _engine().begin() as conn:
        conn.execute(text("UPDATE profiles SET password_hash = :password WHERE id = :uid AND active = TRUE"),
                     {"uid": uid, "password": encoded})


def create_account(name: str, account_type: str) -> None:
    uid = require_user()
    name = name.strip()
    if not name or len(name) > 100 or account_type not in {"checking", "savings", "investment", "credit_card"}:
        raise ValueError("Informe nome e tipo de conta válidos.")
    with _engine().begin() as conn:
        conn.execute(text("INSERT INTO accounts (user_id, name, type) VALUES (:uid, :name, :type)"),
                     {"uid": uid, "name": name, "type": account_type})
