"""Formulários de conta pessoal e cadastro de usuários pelo administrador."""
import streamlit as st

from core.user_accounts import change_password, create_account, create_user
from core.user_context import is_admin, principal, require_user


def render_user_accounts():
    require_user()
    st.subheader("Minha conta")
    st.caption(str(principal().get("email", "")))
    with st.form("account_password", clear_on_submit=True):
        old = st.text_input("Senha atual", type="password")
        new = st.text_input("Nova senha (8 caracteres)", type="password", max_chars=8)
        confirmation = st.text_input("Confirme a nova senha", type="password")
        if st.form_submit_button("Alterar minha senha"):
            if new != confirmation:
                st.error("As senhas não coincidem.")
            else:
                try:
                    change_password(old, new)
                except ValueError as exc:
                    st.error(str(exc))
                except Exception:
                    st.error("Não foi possível alterar a senha.")
                else:
                    from core.auth import encerrar_sessao
                    encerrar_sessao()

    st.subheader("Contas financeiras")
    with st.form("new_personal_account", clear_on_submit=True):
        name = st.text_input("Nome da conta")
        types = {"Conta corrente": "checking", "Poupança": "savings",
                 "Investimentos": "investment", "Cartão de crédito": "credit_card"}
        kind = st.selectbox("Tipo de conta", list(types))
        if st.form_submit_button("Criar minha conta financeira"):
            try:
                create_account(name, types[kind])
                from core.controle import _clear_controle_caches
                _clear_controle_caches()
            except ValueError as exc:
                st.error(str(exc))
            except Exception:
                st.error("Não foi possível criar a conta financeira.")
            else:
                st.success("Conta criada. Você pode registrar lançamentos e importar seus arquivos.")

    if not is_admin():
        return
    st.subheader("Cadastrar usuário")
    st.caption("Cada pessoa terá seus próprios dados financeiros e memória de conversas.")
    with st.form("new_app_user", clear_on_submit=True):
        name = st.text_input("Nome da pessoa")
        email = st.text_input("E-mail")
        password = st.text_input("Senha inicial (8 caracteres)", type="password", max_chars=8)
        confirm = st.text_input("Confirme a senha inicial", type="password")
        if st.form_submit_button("Cadastrar usuário"):
            if password != confirm:
                st.error("As senhas não coincidem.")
            else:
                try:
                    create_user(name, email, password)
                except ValueError as exc:
                    st.error(str(exc))
                except Exception:
                    st.error("Não foi possível cadastrar o usuário.")
                else:
                    st.success("Usuário cadastrado. Ele já pode entrar com e-mail e senha.")
