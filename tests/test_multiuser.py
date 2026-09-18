"""Isolamento de sessão/cache e integração com PostgreSQL descartável."""
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

ADMIN = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"
PASSWORD = "Test#8Ab"


@pytest.fixture
def user_state(monkeypatch):
    state = {}
    monkeypatch.setattr(context.st, "session_state", state)
    monkeypatch.setattr(context, "get_script_run_ctx", lambda **kw: object())
    monkeypatch.setattr(settings, "ADMIN_USER_ID", ADMIN)
    return state


def login_as(state, uid):
    state["_app4_user"] = {"id": uid, "expires_at": time.time() + 600}


def test_identidade_nao_cai_no_dono_global_sem_login(user_state):
    assert settings.OWNER_USER_ID == ""
    login_as(user_state, OTHER)
    assert settings.OWNER_USER_ID == OTHER
    user_state.clear()
    assert settings.OWNER_USER_ID == ""
    with pytest.raises(PermissionError):
        context.require_user()


def test_cache_real_do_controle_separa_usuarios(user_state, monkeypatch):
    import core.controle as control
    monkeypatch.setattr(settings, "MOCK_MODE", False)
    calls = []

    def read(year, month):
        calls.append(settings.OWNER_USER_ID)
        return {"owner": settings.OWNER_USER_ID}

    monkeypatch.setattr(control, "_controle_real", read)
    control.get_controle.clear()
    login_as(user_state, ADMIN)
    assert control.get_controle(2026, 9)["owner"] == ADMIN
    login_as(user_state, OTHER)
    assert control.get_controle(2026, 9)["owner"] == OTHER
    login_as(user_state, ADMIN)
    assert control.get_controle(2026, 9)["owner"] == ADMIN
    assert calls == [ADMIN, OTHER]
    control.get_controle.clear()


def test_senha_tem_salt_e_nao_aceita_hash_como_senha():
    first = accounts.hash_password(PASSWORD)
    assert first != accounts.hash_password(PASSWORD)
    assert accounts.verify_password(PASSWORD, first)
    assert not accounts.verify_password(first, first)
    assert not accounts.verify_password("errada", first)
    with pytest.raises(ValueError):
        accounts.hash_password("curta")
    with pytest.raises(ValueError):
        accounts.hash_password("Test#8AbX")


def test_importacao_identica_de_pessoas_diferentes_nao_colide(user_state):
    from data_pipeline.importers.investments.common import make_external_id
    login_as(user_state, ADMIN)
    original = make_external_id("b3neg", ["AAAA3", 10, 30])
    login_as(user_state, OTHER)
    second = make_external_id("b3neg", ["AAAA3", 10, 30])
    assert original != second
    assert second == make_external_id("b3neg", ["AAAA3", 10, 30])


@pytest.fixture
def postgres(monkeypatch):
    url = os.environ.get("APP4_USERS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("PostgreSQL descartável não configurado")
    parsed = make_url(url)
    assert parsed.host in {"localhost", "127.0.0.1"}
    # Banco novo em container dedicado: nunca usa credenciais de produção.
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    name = "users_test_" + uuid4().hex
    with admin.connect() as conn:
        conn.exec_driver_sql(f"CREATE DATABASE {name}")
    engine = create_engine(parsed.set(database=name))
    schema = Path(__file__).parents[1] / "supabase_unificado/schema"
    with engine.begin() as conn:
        for filename in ("001_core_tables.sql", "002_financial_tables.sql",
                         "003_investment_tables.sql", "004_import_migration_tables.sql"):
            with conn.connection.driver_connection.cursor() as cursor:
                cursor.execute((schema / filename).read_text(encoding="utf-8"))
        conn.execute(text("INSERT INTO profiles (id, name, email, password_hash) VALUES (:uid, 'Administrador teste', 'admin@example.test', :pw)"),
                     {"uid": ADMIN, "pw": accounts.hash_password(PASSWORD)})
    monkeypatch.setattr(accounts, "get_engine", lambda: engine)
    yield engine
    engine.dispose()
    admin.dispose()


def test_cadastro_login_bloqueio_e_revogacao(postgres, user_state):
    login_as(user_state, OTHER)
    with pytest.raises(PermissionError):
        accounts.create_user("Pessoa", "person@example.test", PASSWORD)
    login_as(user_state, ADMIN)
    uid = accounts.create_user("Pessoa", "Person@example.test", PASSWORD)
    login = accounts.authenticate("person@example.test", PASSWORD)
    assert login["id"] == uid and uid != ADMIN
    assert accounts.session_valid(login)
    for _ in range(5):
        assert accounts.authenticate("person@example.test", "errada") is None
    assert accounts.authenticate("person@example.test", PASSWORD) is None
    with postgres.begin() as conn:
        conn.execute(text("UPDATE profiles SET active = FALSE WHERE id = :uid"), {"uid": uid})
    assert not accounts.session_valid(login)


def test_admin_legado_preserva_uuid_e_nao_aceita_hash_literal(postgres, user_state, monkeypatch):
    import hashlib
    digest = hashlib.sha256(PASSWORD.encode()).hexdigest()
    monkeypatch.setattr(settings, "APP_PASSWORD", digest)
    with postgres.begin() as conn:
        conn.execute(text("UPDATE profiles SET password_hash = 'legacy' WHERE id = :uid"), {"uid": ADMIN})
    assert accounts.authenticate("administrador", digest) is None
    user = accounts.authenticate("administrador", PASSWORD)
    assert user["id"] == ADMIN
    user_state["_app4_user"] = user
    accounts.change_password(PASSWORD, "New#8Ab!")
    assert not accounts.session_valid(user)
    assert accounts.authenticate("administrador", PASSWORD) is None
    assert accounts.authenticate("administrador", "New#8Ab!")


def test_setup_local_nao_substitui_senha_individual(postgres, user_state, monkeypatch):
    from scripts import setup_app4_admin as setup

    monkeypatch.setattr(setup, "get_engine", lambda: postgres)
    with pytest.raises(ValueError):
        setup.configure(PASSWORD)
    with postgres.begin() as conn:
        conn.execute(text("UPDATE profiles SET password_hash = 'legacy' WHERE id = :uid"), {"uid": ADMIN})
    setup.configure(PASSWORD)
    assert accounts.authenticate("administrador", PASSWORD)
    with pytest.raises(ValueError):
        setup.configure("New#8Ab!")


def test_memoria_privada_restaurada_oculta_e_limpeza(postgres, user_state):
    from core.chat_memory import (
        clear_chat_history,
        load_chat_history,
        save_chat_history,
        visible_chat_history,
    )
    login_as(user_state, ADMIN)
    uid = accounts.create_user("Outra", "other@example.test", PASSWORD)
    original = [{"role": "user", "content": "Contexto sintético A"}]
    save_chat_history("chat", original, session_state=user_state)
    login_as(user_state, uid)
    assert load_chat_history("chat", session_state=user_state) == []
    second = [{"role": "user", "content": "Contexto sintético B"}]
    save_chat_history("chat", second, session_state=user_state)
    # Simula queda do servidor: nova sessão, mesma pessoa.
    user_state.clear()
    login_as(user_state, ADMIN)
    restored = load_chat_history("chat", session_state=user_state)
    assert restored == original
    assert visible_chat_history(restored, "chat", session_state=user_state) == []
    restored.append({"role": "user", "content": "Continuação"})
    save_chat_history("chat", restored, session_state=user_state)
    assert visible_chat_history(restored, "chat", session_state=user_state) == restored[-1:]
    clear_chat_history("chat", session_state=user_state)
    assert load_chat_history("chat", session_state=user_state) == []
    login_as(user_state, uid)
    assert load_chat_history("chat", session_state=user_state) == second


def test_contas_financeiras_sao_criadas_para_a_pessoa_autenticada(postgres, user_state):
    login_as(user_state, ADMIN)
    uid = accounts.create_user("Outra", "other@example.test", PASSWORD)
    login_as(user_state, uid)
    accounts.create_account("Conta sintética", "checking")
    with postgres.connect() as conn:
        owners = conn.execute(text("SELECT user_id FROM accounts")).scalars().all()
    assert [str(value) for value in owners] == [uid]


def test_sql_transacoes_rejeita_conta_de_outra_pessoa(postgres, user_state):
    from core.controle import _SQL_INSERT_TX, _SQL_UPDATE_TX

    login_as(user_state, ADMIN)
    uid = accounts.create_user("Outra", "other@example.test", PASSWORD)
    accounts.create_account("Conta A", "checking")
    login_as(user_state, uid)
    accounts.create_account("Conta B", "checking")
    with postgres.begin() as conn:
        rows = conn.execute(text("SELECT id, user_id FROM accounts")).all()
        ids = {str(row.user_id): str(row.id) for row in rows}
        params = dict(uid=uid, account_id=ids[ADMIN], category_id=None,
                      description="Sintético", amount=10, due_date="2026-09-01", type="income")
        assert conn.execute(text(_SQL_INSERT_TX), params).rowcount == 0
        params["account_id"] = ids[uid]
        assert conn.execute(text(_SQL_INSERT_TX), params).rowcount == 1
        tx = conn.execute(text("SELECT id FROM transactions")).scalar()
        params.update(tx_id=str(tx), uid=ADMIN, account_id=ids[ADMIN])
        assert conn.execute(text(_SQL_UPDATE_TX), params).rowcount == 0
        params["uid"] = uid
        assert conn.execute(text(_SQL_UPDATE_TX), params).rowcount == 0


def test_login_logout_na_interface_limpa_dados_da_pessoa_anterior(monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(accounts, "authenticate", lambda email, pw: {
        "id": OTHER, "name": "Pessoa sintética", "email": "other@example.test",
        "expires_at": time.time() + 600,
    } if email == "other@example.test" and pw == PASSWORD else None)
    monkeypatch.setattr(accounts, "session_valid", lambda user: bool(user))
    app = AppTest.from_string('''
import streamlit as st
from core.auth import verificar_autenticacao, encerrar_sessao
from core.config import settings
verificar_autenticacao()
st.write("Minha área privada")
st.session_state["private_data"] = "Conteúdo sintético"
st.caption(settings.OWNER_USER_ID)
if st.button("Sair"):
    encerrar_sessao()
''').run(timeout=20)
    assert not app.exception
    assert not any("Minha área privada" in item.value for item in app.markdown)
    app.text_input(key="_auth_email_input").set_value("other@example.test")
    app.text_input(key="_auth_senha_input").set_value(PASSWORD)
    app.button[0].click().run()
    assert not app.exception
    assert app.caption[0].value == OTHER
    assert app.session_state["private_data"] == "Conteúdo sintético"
    app.button[0].click().run()
    assert not app.exception
    assert "private_data" not in app.session_state
    assert "_app4_user" not in app.session_state
    assert app.text_input(key="_auth_email_input")


def test_chat_da_interface_lembra_sem_renderizar_passado(monkeypatch):
    from streamlit.testing.v1 import AppTest

    import core.chat_repository as repo
    import design.chat_ativo as view
    stored = [{"role": "user", "content": "Minha pergunta anterior sintética"},
              {"role": "assistant", "content": "Resposta anterior sintética"}]
    captured = []
    monkeypatch.setattr(repo, "load", lambda key: list(stored))
    monkeypatch.setattr(repo, "save", lambda key, messages: None)
    monkeypatch.setattr(view, "llm_disponivel", lambda: True)
    monkeypatch.setattr(view, "provedores_disponiveis", lambda: [])

    def llm(ctx, history, question, **kw):
        captured.append(history)
        return "Resposta nova sintética"

    monkeypatch.setattr(view, "chat_com_ativo", llm)
    app = AppTest.from_string('''
import time
import streamlit as st
from design.chat_ativo import render_chat_ativo
st.session_state['_app4_user'] = {'id': 'test-person', 'expires_at': time.time() + 100}
render_chat_ativo(mercado='b3', ticker='AAAA3', build_context=lambda q: 'Dados sintéticos')
''').run(timeout=20)
    assert not app.exception
    assert len(app.chat_message) == 0
    app.chat_input[0].set_value("Continue a explicação").run(timeout=20)
    assert not app.exception
    assert captured == [stored]
    assert len(app.chat_message) == 2
    app.run()
    assert len(app.chat_message) == 2


def test_criar_usuario_tem_formulario_so_para_admin(monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(settings, "ADMIN_USER_ID", ADMIN)
    app = AppTest.from_string('''
import streamlit as st
from design.user_accounts import render_user_accounts
render_user_accounts()
''')
    app.session_state['_app4_user'] = {'id': OTHER, 'expires_at': time.time() + 100}
    app.run(timeout=20)
    assert not app.exception
    assert not any(item.label == "Cadastrar usuário" for item in app.button)
    app.session_state['_app4_user'] = {'id': ADMIN, 'expires_at': time.time() + 100}
    app.run()
    assert not app.exception
    assert any(item.label == "Cadastrar usuário" for item in app.button)


def test_lista_de_usuarios_recusa_quem_nao_e_admin(postgres, user_state):
    """O guarda mora na consulta, não só no ``if`` da tela.

    Nome e e-mail das outras pessoas são dado pessoal: esconder o bloco na
    interface protege a interface. Quem chamar ``list_users`` de qualquer
    outro lugar tem que levar ``PermissionError``.
    """
    login_as(user_state, ADMIN)
    uid = accounts.create_user("Pessoa", "person@example.test", PASSWORD)
    perfis = accounts.list_users()
    assert {str(item["id"]) for item in perfis} == {ADMIN, uid}
    assert all("password_hash" not in item for item in perfis)

    login_as(user_state, OTHER)
    with pytest.raises(PermissionError):
        accounts.list_users()
    user_state.clear()
    with pytest.raises(PermissionError):
        accounts.list_users()


def test_usuarios_cadastrados_aparecem_so_para_o_admin(monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(settings, "ADMIN_USER_ID", ADMIN)
    app = AppTest.from_string('''
import streamlit as st
import design.user_accounts as ui
ui.list_users = lambda: [{"id": "11111111-1111-1111-1111-111111111111",
                          "name": "Administrador teste",
                          "email": "admin@example.test",
                          "created_at": None, "active": True}]
ui.render_registered_users()
''')
    app.session_state['_app4_user'] = {'id': OTHER, 'expires_at': time.time() + 100}
    app.run(timeout=20)
    assert not app.exception
    assert len(app.dataframe) == 0
    app.session_state['_app4_user'] = {'id': ADMIN, 'expires_at': time.time() + 100}
    app.run(timeout=20)
    assert not app.exception
    assert len(app.dataframe) == 1

