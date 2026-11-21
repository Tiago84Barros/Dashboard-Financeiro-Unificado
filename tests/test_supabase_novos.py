"""Dois bancos: o principal (contas antigas) e o ``novos`` (contas abertas).

Roda contra um PostgreSQL descartável (``APP4_USERS_TEST_DATABASE_URL``, só
localhost), criando duas bases nele: uma faz o papel do Supabase principal,
outra o do Supabase das contas novas, preparada por
``scripts/preparar_supabase_novos.py`` com ``postgres_fdw`` de verdade.

O ``postgres_fdw`` conecta a partir do servidor, não deste processo: num
container com a porta mapeada, o principal é ``localhost:5432`` lá dentro.
``APP4_USERS_TEST_FDW_HOST``/``APP4_USERS_TEST_FDW_PORT`` mudam isso.
"""
import os
import time
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

import core.user_accounts as accounts
import core.user_context as context
from core.config import settings
from core.database import BANCO_NOVOS, BANCO_PRINCIPAL
from scripts import preparar_supabase_novos as prep

ADMIN = "11111111-1111-1111-1111-111111111111"
PASSWORD = "Test#8Ab"
_SCHEMA = Path(__file__).parents[1] / "supabase_unificado/schema"
_AUTH_UID = ("CREATE SCHEMA auth; CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql "
             "STABLE AS 'SELECT NULL::uuid'")


@pytest.fixture
def user_state(monkeypatch):
    state = {}
    monkeypatch.setattr(context.st, "session_state", state)
    monkeypatch.setattr(context, "get_script_run_ctx", lambda **kw: object())
    monkeypatch.setattr(settings, "ADMIN_USER_ID", ADMIN)
    return state


def _login(state, uid, banco=BANCO_PRINCIPAL):
    state["_app4_user"] = {"id": uid, "banco": banco, "expires_at": time.time() + 600}


@pytest.fixture
def dois_bancos(monkeypatch):
    url = os.environ.get("APP4_USERS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("PostgreSQL descartável não configurado")
    parsed = make_url(url)
    assert parsed.host in {"localhost", "127.0.0.1"}
    raiz = create_engine(url, isolation_level="AUTOCOMMIT")
    sufixo = uuid4().hex[:12]
    nomes = {BANCO_PRINCIPAL: f"principal_{sufixo}", BANCO_NOVOS: f"novos_{sufixo}"}
    with raiz.connect() as conn:
        for nome in nomes.values():
            conn.exec_driver_sql(f"CREATE DATABASE {nome}")
    engines = {b: create_engine(parsed.set(database=n)) for b, n in nomes.items()}
    with engines[BANCO_PRINCIPAL].begin() as conn:
        for arquivo in ("001_core_tables.sql", "002_financial_tables.sql",
                        "003_investment_tables.sql", "004_import_migration_tables.sql",
                        "007_views.sql"):
            with conn.connection.driver_connection.cursor() as cursor:
                cursor.execute((_SCHEMA / arquivo).read_text(encoding="utf-8"))
        # Como em produção: o log do lote não cascateia; a exclusão o apaga antes.
        conn.execute(text("ALTER TABLE import_logs DROP CONSTRAINT import_logs_batch_id_fkey, "
                          "ADD CONSTRAINT import_logs_batch_id_fkey FOREIGN KEY (batch_id) "
                          "REFERENCES import_batches(id)"))
        # Como no Supabase: ``auth.uid()`` e uma policy de dono numa tabela da pessoa.
        conn.execute(text(_AUTH_UID))
        conn.execute(text("CREATE POLICY accounts_owner_all ON accounts FOR ALL "
                          "USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid())"))
        conn.execute(text("CREATE SCHEMA market"))
        conn.execute(text("CREATE TABLE market.cotacao (ticker text PRIMARY KEY, preco numeric)"))
        conn.execute(text("INSERT INTO market.cotacao VALUES ('TEST3', 10.5)"))
        conn.execute(text("INSERT INTO categories (user_id, name, type) VALUES "
                          "(NULL, 'Sistema A', 'expense'), (NULL, 'Sistema B', 'income')"))
        conn.execute(text("INSERT INTO profiles (id, name, email, password_hash) "
                          "VALUES (:uid, 'Administrador teste', 'admin@example.test', :pw)"),
                     {"uid": ADMIN, "pw": accounts.hash_password(PASSWORD)})
    fdw = {"host": os.environ.get("APP4_USERS_TEST_FDW_HOST", "localhost"),
           "port": os.environ.get("APP4_USERS_TEST_FDW_PORT", "5432"),
           "dbname": nomes[BANCO_PRINCIPAL], "user": parsed.username,
           "password": parsed.password or "", "sslmode": "disable"}
    with engines[BANCO_NOVOS].begin() as conn:  # o Supabase novo já nasce com ``auth``
        conn.execute(text(_AUTH_UID))
    plano = prep.montar_plano(engines[BANCO_PRINCIPAL], fdw, papeis_da_api=False)
    prep.aplicar(engines[BANCO_NOVOS], plano)
    monkeypatch.setattr(accounts, "engine_do_banco", lambda banco: engines[banco])
    monkeypatch.setattr(accounts, "novos_configurado", lambda: True)
    yield engines, plano, fdw
    for engine in engines.values():
        engine.dispose()
    with raiz.connect() as conn:
        for nome in nomes.values():
            conn.exec_driver_sql(f"DROP DATABASE IF EXISTS {nome} WITH (FORCE)")
    raiz.dispose()


def _contar(engine, sql, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar()


def test_preparo_separa_o_que_e_da_pessoa_do_que_e_compartilhado(dois_bancos):
    engines, plano, fdw = dois_bancos
    novos = engines[BANCO_NOVOS]
    for tabela in ("profiles", "accounts", "transactions", "categories",
                   "import_batches", "user_settings"):
        assert tabela in plano.locais
    assert "assets" not in plano.locais  # compartilhada: lida do principal
    # Remoto: o que o principal tem, sem cópia.
    assert _contar(novos, "SELECT preco FROM market.cotacao WHERE ticker = 'TEST3'") == 10.5
    assert _contar(novos, "SELECT count(*) FROM information_schema.foreign_tables "
                          "WHERE foreign_table_name = 'assets'") == 1
    # Categorias do sistema copiadas; perfis do principal NÃO.
    assert _contar(novos, "SELECT count(*) FROM categories WHERE user_id IS NULL") == 2
    assert _contar(novos, "SELECT count(*) FROM profiles") == 0
    assert _contar(novos, "SELECT count(*) FROM pg_class WHERE relnamespace = "
                          "'public'::regnamespace AND relkind = 'r' AND NOT relrowsecurity") == 0
    # Reaplicar é seguro (roda de novo depois de cada migration do principal).
    prep.aplicar(novos, prep.montar_plano(engines[BANCO_PRINCIPAL], fdw, papeis_da_api=False))
    assert _contar(novos, "SELECT count(*) FROM categories WHERE user_id IS NULL") == 2


def test_policies_do_principal_valem_no_novos(dois_bancos):
    engines, _, fdw = dois_bancos
    novos = engines[BANCO_NOVOS]
    sql = ("SELECT pg_get_expr(polqual, polrelid) || ' | ' || pg_get_expr(polwithcheck, polrelid) "
           "FROM pg_policy WHERE polname = 'accounts_owner_all' AND polrelid = 'public.accounts'::regclass")
    esperado = _contar(engines[BANCO_PRINCIPAL], sql)
    assert esperado and _contar(novos, sql) == esperado
    # Reaplicar não duplica nem falha.
    prep.aplicar(novos, prep.montar_plano(engines[BANCO_PRINCIPAL], fdw, papeis_da_api=False))
    assert _contar(novos, "SELECT count(*) FROM pg_policy WHERE polname = 'accounts_owner_all'") == 1


def test_tabela_estrangeira_distingue_o_compartilhado(dois_bancos):
    from core.database import tabela_estrangeira

    engines, _, _ = dois_bancos
    with engines[BANCO_NOVOS].connect() as conn:
        assert tabela_estrangeira(conn, "assets")
        assert tabela_estrangeira(conn, "market.cotacao")
        assert not tabela_estrangeira(conn, "accounts")
        assert not tabela_estrangeira(conn, "nao_existe")
    with engines[BANCO_PRINCIPAL].connect() as conn:
        assert not tabela_estrangeira(conn, "assets")


def test_ativo_e_instituicao_criados_numa_conta_nova_vao_ao_principal(dois_bancos, monkeypatch):
    import core.database as database
    from data_pipeline.importers.investments import common

    engines, _, _ = dois_bancos
    monkeypatch.setattr(database, "engine_do_app", lambda: engines[BANCO_PRINCIPAL])
    principal = engines[BANCO_PRINCIPAL]
    with engines[BANCO_NOVOS].begin() as conn:
        um = common.get_or_create_asset(conn, "TSTA3", name="Teste A")
        lote = common.batch_get_or_create_assets(conn, [("TSTA3", "Teste A", "stock"),
                                                       ("TSTB4", "Teste B", "stock")])
        inst = common.get_or_create_institution(conn, "Corretora Teste", "broker")
        # A foto do fdw nesta transação não vê a criação; a releitura no principal vê.
        assert common.get_or_create_institution(conn, "Corretora Teste", "broker") == inst
    assert lote["TSTA3"] == um and set(lote) == {"TSTA3", "TSTB4"}
    assert _contar(principal, "SELECT count(*) FROM assets WHERE ticker IN ('TSTA3','TSTB4')") == 2
    assert _contar(principal, "SELECT currency FROM assets WHERE ticker = 'TSTB4'") == "BRL"
    assert _contar(principal, "SELECT id::text FROM financial_institutions "
                              "WHERE name = 'Corretora Teste'") == inst
    with engines[BANCO_PRINCIPAL].begin() as conn:  # no principal, a gravação fica na transação
        assert common.get_or_create_asset(conn, "TSTA3") == um


def test_plano_impresso_nao_mostra_a_senha(dois_bancos):
    _, plano, fdw = dois_bancos
    impresso = prep.imprimir(plano)
    assert "password '***'" in impresso
    if fdw["password"]:
        assert f"'{fdw['password']}'" not in impresso


def test_recusa_preparar_o_proprio_principal():
    a = "postgresql://postgres.ref1:x@aws-1.pooler.supabase.com:5432/postgres"
    b = "postgresql://postgres.ref2:x@aws-1.pooler.supabase.com:5432/postgres"
    assert prep._mesmo_banco(a, a)
    assert not prep._mesmo_banco(a, b)
    assert not prep._mesmo_banco("postgresql://u:x@localhost/principal",
                                 "postgresql://u:x@localhost/novos")


def test_cadastro_aberto_grava_so_no_novos_e_login_acha_nos_dois(dois_bancos, user_state):
    engines, _, _ = dois_bancos
    user_state.clear()  # cadastro na tela de login: ninguém logado
    uid = accounts.cadastrar_usuario("Pessoa nova", "Nova@example.test", PASSWORD)
    assert _contar(engines[BANCO_NOVOS], "SELECT count(*) FROM profiles WHERE id = :u", u=uid) == 1
    assert _contar(engines[BANCO_PRINCIPAL], "SELECT count(*) FROM profiles WHERE id = :u", u=uid) == 0

    nova = accounts.authenticate("nova@example.test", PASSWORD)
    assert nova["id"] == uid and nova["banco"] == BANCO_NOVOS
    assert accounts.session_valid(nova)
    antiga = accounts.authenticate("administrador", PASSWORD)
    assert antiga["id"] == ADMIN and antiga["banco"] == BANCO_PRINCIPAL

    with pytest.raises(ValueError):  # e-mail do principal não se repete no novos
        accounts.cadastrar_usuario("Outra", "admin@example.test", PASSWORD)
    with pytest.raises(ValueError):
        accounts.cadastrar_usuario("Outra", "nova@example.test", PASSWORD)


def test_cadastro_fica_fechado_sem_o_novos(monkeypatch):
    monkeypatch.setattr(accounts, "novos_configurado", lambda: False)
    with pytest.raises(RuntimeError):
        accounts.cadastrar_usuario("Pessoa", "p@example.test", PASSWORD)



def test_cadastro_fechado_nao_manda_procurar_o_administrador(monkeypatch):
    # Criar conta não depende de aprovação: só do banco novos configurado.
    import core.auth as auth
    import core.database as database

    avisos = []
    monkeypatch.setattr(database, "novos_configurado", lambda: False)
    monkeypatch.setattr(auth.st, "info", avisos.append)
    monkeypatch.setattr(auth.st, "form", lambda *a, **k: pytest.fail("formulário sem banco"))
    auth._renderizar_cadastro()
    assert len(avisos) == 1 and "administrador" not in avisos[0].lower()


def test_conta_nova_nasce_so_com_as_secoes_essenciais(dois_bancos, user_state, monkeypatch):
    from core import secoes_app
    from core import user_preferences as preferences

    engines, _, _ = dois_bancos
    # Sem ``ScriptRunContext`` o app cai no principal; aqui o banco sai da
    # sessão, como no Streamlit de verdade.
    monkeypatch.setattr(accounts, "banco_da_sessao",
                        lambda: user_state.get("_app4_user", {}).get("banco", BANCO_PRINCIPAL))
    user_state.clear()
    uid = accounts.cadastrar_usuario("Pessoa nova", "menu@example.test", PASSWORD)
    _login(user_state, uid, BANCO_NOVOS)
    assert preferences.load_secoes_ocultas() == list(secoes_app.ocultas_conta_nova())
    assert secoes_app.ocultas_da_sessao(user_state) == frozenset(secoes_app.ocultas_conta_nova())
    # A escolha inicial não ocupa a linha: o tema grava ao lado dela.
    preferences.save_theme("light")
    assert preferences.load_theme() == "light"
    # Ligar tudo em Configurações devolve o menu inteiro.
    preferences.save_secoes_ocultas([])
    assert preferences.load_secoes_ocultas() == []

    # Conta que já existia não perde seção, e o principal não ganha linha.
    _login(user_state, ADMIN)
    assert preferences.load_secoes_ocultas() == []
    assert _contar(engines[BANCO_PRINCIPAL],
                   "SELECT count(*) FROM user_settings WHERE user_id = :u", u=uid) == 0


def test_conta_nova_grava_no_novos_e_le_o_compartilhado(dois_bancos, user_state):
    engines, _, _ = dois_bancos
    uid = accounts.cadastrar_usuario("Pessoa nova", "nova@example.test", PASSWORD)
    _login(user_state, uid, BANCO_NOVOS)
    with engines[BANCO_NOVOS].begin() as conn:
        conn.execute(text("INSERT INTO accounts (user_id, name, type) VALUES (:u, 'Conta', 'checking')"),
                     {"u": uid})
    assert _contar(engines[BANCO_PRINCIPAL], "SELECT count(*) FROM accounts") == 0
    assert _contar(engines[BANCO_NOVOS], "SELECT count(*) FROM v_account_balance") == 1


def _semear(engine, uid):
    with engine.begin() as conn:
        conta = conn.execute(text("INSERT INTO accounts (user_id, name, type) "
                                  "VALUES (:u, 'Conta', 'checking') RETURNING id"), {"u": uid}).scalar()
        conn.execute(text("INSERT INTO transactions (user_id, account_id, description, amount, "
                          "due_date, type) VALUES (:u, :c, 'Sintético', 10, '2026-11-01', 'income')"),
                     {"u": uid, "c": conta})
        lote = conn.execute(text("INSERT INTO import_batches (user_id, source) "
                                 "VALUES (:u, 'manual') RETURNING id"), {"u": uid}).scalar()
        conn.execute(text("INSERT INTO import_logs (batch_id, target_table, action) "
                          "VALUES (:b, 'accounts', 'inserted')"), {"b": lote})


def test_exclusao_apaga_tudo_da_pessoa_e_so_dela(dois_bancos, user_state):
    engines, _, _ = dois_bancos
    novos = engines[BANCO_NOVOS]
    alvo = accounts.cadastrar_usuario("Alvo", "alvo@example.test", PASSWORD)
    fica = accounts.cadastrar_usuario("Fica", "fica@example.test", PASSWORD)
    _semear(novos, alvo)
    _semear(novos, fica)

    _login(user_state, fica, BANCO_NOVOS)
    with pytest.raises(PermissionError):
        accounts.excluir_usuario(alvo, BANCO_NOVOS)

    _login(user_state, ADMIN)
    with pytest.raises(ValueError):
        accounts.excluir_usuario(ADMIN, BANCO_PRINCIPAL)
    with pytest.raises(ValueError):  # conta do novos não está no principal
        accounts.excluir_usuario(alvo, BANCO_PRINCIPAL)
    apagadas = accounts.excluir_usuario(alvo, BANCO_NOVOS)
    assert apagadas["profiles"] == 1 and apagadas["import_logs"] == 1
    assert apagadas["transactions"] == 1 and apagadas["accounts"] == 1

    for tabela in ("accounts", "transactions", "import_batches", "user_settings"):
        assert _contar(novos, f"SELECT count(*) FROM {tabela} WHERE user_id = :u", u=alvo) == 0
    assert _contar(novos, "SELECT count(*) FROM profiles WHERE id = :u", u=alvo) == 0
    assert _contar(novos, "SELECT count(*) FROM accounts WHERE user_id = :u", u=fica) == 1
    assert _contar(novos, "SELECT count(*) FROM import_logs") == 1
    # Categorias do sistema e o compartilhado (remoto) ficam.
    assert _contar(novos, "SELECT count(*) FROM categories WHERE user_id IS NULL") == 2
    assert _contar(novos, "SELECT count(*) FROM market.cotacao") == 1
    assert accounts.authenticate("alvo@example.test", PASSWORD) is None


def test_exclusao_no_principal_nao_toca_o_novos(dois_bancos, user_state):
    engines, _, _ = dois_bancos
    antigo = str(uuid4())
    with engines[BANCO_PRINCIPAL].begin() as conn:
        conn.execute(text("INSERT INTO profiles (id, name, email, password_hash) "
                          "VALUES (:u, 'Antigo', 'antigo@example.test', 'x')"), {"u": antigo})
    _semear(engines[BANCO_PRINCIPAL], antigo)
    novo = accounts.cadastrar_usuario("Novo", "novo@example.test", PASSWORD)
    _semear(engines[BANCO_NOVOS], novo)
    _login(user_state, ADMIN)
    accounts.excluir_usuario(antigo, BANCO_PRINCIPAL)
    assert _contar(engines[BANCO_PRINCIPAL], "SELECT count(*) FROM accounts") == 0
    assert _contar(engines[BANCO_PRINCIPAL], "SELECT count(*) FROM categories WHERE user_id IS NULL") == 2
    assert _contar(engines[BANCO_NOVOS], "SELECT count(*) FROM accounts") == 1


def test_lista_de_usuarios_junta_os_dois_bancos(dois_bancos, user_state):
    novo = accounts.cadastrar_usuario("Novo", "novo@example.test", PASSWORD)
    _login(user_state, ADMIN)
    perfis = {str(p["id"]): p["banco"] for p in accounts.list_users()}
    assert perfis == {ADMIN: BANCO_PRINCIPAL, novo: BANCO_NOVOS}


def test_lista_nomeia_o_novos_fora_do_ar(dois_bancos, user_state, monkeypatch):
    engines, _, _ = dois_bancos

    def engine(banco):
        if banco == BANCO_NOVOS:
            raise ConnectionError("pausado")
        return engines[banco]

    monkeypatch.setattr(accounts, "engine_do_banco", engine)
    _login(user_state, ADMIN)
    falhas = []
    perfis = accounts.list_users(falhas)
    assert [str(p["id"]) for p in perfis] == [ADMIN] and len(falhas) == 1
    # Login de quem mora no principal segue funcionando.
    assert accounts.authenticate("administrador", PASSWORD)["id"] == ADMIN
