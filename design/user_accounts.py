"""Formulários de senha pessoal e cadastro de usuários pelo administrador."""
import streamlit as st

from core.user_accounts import change_password, create_user, list_users
from core.user_context import is_admin, principal, require_user


def render_user_accounts():
    """Bloco de conta da aba Segurança: trocar a própria senha e, para o
    administrador, cadastrar pessoas.

    O formulário "Contas financeiras" (nome + tipo -> ``create_account``) saiu
    daqui em 17/09/2026 a pedido do usuário: cadastro de conta bancária não é
    assunto de Segurança, e ele aparecia entre a troca de senha e o cadastro de
    usuários, dois fluxos de identidade.

    Fica o registro de que ``core.user_accounts.create_account`` ficou sem
    chamador de interface -- os importadores de investimento criam conta
    sozinhos (``data_pipeline/importers/investments/common.py``), mas conta
    corrente e cartão de crédito não têm outra porta de entrada na UI. A
    função foi mantida no ``core`` de propósito, para que reabrir esse cadastro
    em Controle Financeiro seja só montar o formulário de novo.
    """
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


def render_registered_users():
    """Lista de quem tem acesso ao app. Só o administrador vê, e só ele chama.

    Dupla checagem de propósito: ``is_admin`` aqui decide se o bloco aparece, e
    ``core.user_accounts.list_users`` chama ``require_admin`` antes de tocar no
    banco. Se um dia esta função for chamada de outro lugar sem o guarda da
    tela, a consulta continua recusando.
    """
    if not is_admin():
        return
    try:
        usuarios = list_users()
    except PermissionError:
        return
    except Exception:
        st.error("Não foi possível carregar os usuários cadastrados.")
        return
    if not usuarios:
        st.caption("Nenhum usuário cadastrado.")
        return

    admin_id = str(principal().get("id", ""))
    linhas = [
        {
            "Nome": pessoa["name"],
            "E-mail": pessoa["email"],
            "Cadastro": pessoa["created_at"].strftime("%d/%m/%Y")
            if pessoa.get("created_at") else "—",
            "Situação": "Ativo" if pessoa["active"] else "Inativo",
            "Perfil": "Administrador" if str(pessoa["id"]) == admin_id else "Usuário",
        }
        for pessoa in usuarios
    ]
    st.dataframe(linhas, hide_index=True, width="stretch")
    ativos = sum(1 for pessoa in usuarios if pessoa["active"])
    st.caption(
        f"{ativos} ativo(s) de {len(usuarios)} perfil(is). "
        "Desativar uma pessoa é feito no banco (`profiles.active = FALSE`)."
    )
