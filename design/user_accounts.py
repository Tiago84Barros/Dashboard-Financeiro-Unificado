"""Senha pessoal e, para o administrador, contas, logados e exclusão.

Desde 14/11/2026 o cadastro de pessoas saiu daqui e foi para a tela de login
(``core.auth._renderizar_cadastro``): qualquer pessoa cria a própria conta. O
papel de Configurações passou a ser o do administrador -- ver quem tem acesso,
quem está logado agora e excluir uma conta com todos os dados dela.
"""
from datetime import datetime

import streamlit as st

from core.user_accounts import change_password, excluir_usuario, list_users
from core.user_context import is_admin, principal, require_user
from design.componentes import card_metrica
from design.lacunas import falha_de_acao

_NOME_DO_BANCO = {"principal": "Supabase principal", "novos": "Supabase das contas novas"}


def render_user_accounts():
    """Bloco de conta da aba Segurança: trocar a própria senha.

    O formulário "Cadastrar usuário" do administrador saiu daqui em 14/11/2026:
    o cadastro passou a ser aberto, na tela de login.

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
                except Exception as exc:
                    falha_de_acao("Não foi possível alterar a senha.", exc)
                else:
                    from core.auth import encerrar_sessao
                    encerrar_sessao()


def render_registered_users():
    """Lista de quem tem acesso ao app. Só o administrador vê, e só ele chama.

    Dupla checagem de propósito: ``is_admin`` aqui decide se a lista aparece, e
    ``core.user_accounts.list_users`` chama ``require_admin`` antes de tocar no
    banco. Se um dia esta função for chamada de outro lugar sem o guarda da
    tela, a consulta continua recusando.

    Desde 12/10/2026 quem não administra chega até aqui — o container da aba
    Segurança existe para todo mundo. Em vez de sair sem desenhar nada, a
    função diz por que a lista não veio: ela é de outras pessoas, e a guarda
    que a segura é de dado, não de tela.
    """
    if not is_admin():
        st.caption("A lista de quem tem acesso ao aplicativo é visível apenas "
                   "para quem o administra.")
        return
    falhas: list[str] = []
    try:
        usuarios = list_users(falhas)
    except PermissionError:
        return
    except Exception as exc:
        falha_de_acao("Não foi possível carregar os usuários cadastrados.", exc)
        return
    for falha in falhas:
        st.warning(falha)
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
            "Banco": _NOME_DO_BANCO.get(pessoa.get("banco"), "—"),
        }
        for pessoa in usuarios
    ]
    st.dataframe(linhas, hide_index=True, width="stretch")
    ativos = sum(1 for pessoa in usuarios if pessoa["active"])
    st.caption(
        f"{ativos} ativo(s) de {len(usuarios)} perfil(is). "
        "Desativar uma pessoa é feito no banco (`profiles.active = FALSE`)."
    )


def render_logged_users():
    """Quantas e quais pessoas estão logadas agora. Só o administrador vê.

    Lê o registro em memória de ``core.presenca``: conta uma sessão enquanto a
    aba do navegador está conectada e a identidade não expirou.
    """
    if not is_admin():
        st.caption("Quem está logado agora é visível apenas para quem administra "
                   "o aplicativo.")
        return
    from core.presenca import usuarios_logados
    try:
        logados = usuarios_logados()
    except PermissionError:
        return
    except Exception as exc:
        falha_de_acao("Não foi possível ler quem está logado.", exc)
        return
    abas = sum(p["abas"] for p in logados)
    # Cards KPI do tema (``card_metrica``), não ``st.metric``: o st.metric
    # desenhava retângulos brancos crus, fora do visual do resto do app.
    ultima = max((p["visto"] for p in logados), default=None)
    col1, col2, col3 = st.columns(3)
    with col1:
        card_metrica("Pessoas logadas", len(logados),
                     delta="com o app aberto agora", accent="var(--app-primary)",
                     ajuda="Pessoas distintas com sessão conectada e identidade válida.")
    with col2:
        media = f"média de {abas / len(logados):.1f} por pessoa".replace(".", ",") \
            if logados else "nenhuma aba conectada"
        card_metrica("Abas abertas", abas, delta=media, accent="var(--app-info)",
                     ajuda="Cada aba do navegador conectada conta uma vez.")
    with col3:
        card_metrica(
            "Última atividade",
            datetime.fromtimestamp(ultima).strftime("%H:%M") if ultima else "—",
            delta=datetime.fromtimestamp(ultima).strftime("%d/%m/%Y") if ultima else "sem sessões",
            accent="var(--app-warning)",
            ajuda="Momento mais recente em que alguma sessão deu sinal (horário do servidor).",
        )
    if not logados:
        st.caption("Ninguém logado agora.")
        return
    st.dataframe(
        [
            {
                "Nome": p["name"],
                "Entrou às": datetime.fromtimestamp(p["desde"]).strftime("%d/%m %H:%M"),
                "Última atividade": datetime.fromtimestamp(p["visto"]).strftime("%d/%m %H:%M"),
                "Abas": p["abas"],
                "Banco": _NOME_DO_BANCO.get(p["banco"], "—"),
            }
            for p in logados
        ],
        hide_index=True, width="stretch",
    )
    st.caption("Horário do servidor. Uma pessoa com o app aberto em duas abas "
               "conta uma vez em Pessoas e duas em Abas.")


def render_delete_user():
    """Exclui uma conta e TODOS os dados dela. Só o administrador.

    A confirmação pede o e-mail da conta digitado por extenso: a ação não tem
    volta, e um clique no item errado da lista não pode bastar.
    """
    if not is_admin():
        st.caption("Excluir uma conta é ação de quem administra o aplicativo.")
        return
    try:
        usuarios = list_users()
    except PermissionError:
        return
    except Exception as exc:
        falha_de_acao("Não foi possível carregar os usuários cadastrados.", exc)
        return
    admin_id = str(principal().get("id", ""))
    candidatos = [p for p in usuarios if str(p["id"]) != admin_id]
    if not candidatos:
        st.caption("Não há contas além da do administrador.")
        return
    rotulos = {
        f"{p['name']} — {p['email']} ({_NOME_DO_BANCO.get(p.get('banco'), '—')})": p
        for p in candidatos
    }
    escolha = st.selectbox("Conta a excluir", list(rotulos), index=None,
                           placeholder="Escolha a conta", key="_cfg_excluir_conta")
    if not escolha:
        return
    pessoa = rotulos[escolha]
    st.warning(
        f"Isto apaga a conta de **{pessoa['name']}** e todos os dados dela: "
        "lançamentos, contas, cartões, carteiras, importações, preferências e "
        "memória das conversas. Não tem volta."
    )
    with st.form("_cfg_excluir_conta_form", clear_on_submit=True):
        digitado = st.text_input("Digite o e-mail da conta para confirmar")
        confirmar = st.form_submit_button("Excluir conta e todos os dados", type="primary")
    if not confirmar:
        return
    if digitado.strip().lower() != str(pessoa["email"]).strip().lower():
        st.error("O e-mail digitado não confere com a conta escolhida.")
        return
    try:
        apagadas = excluir_usuario(str(pessoa["id"]), str(pessoa.get("banco") or "principal"))
    except ValueError as exc:
        st.error(str(exc))
        return
    except Exception as exc:
        falha_de_acao("Não foi possível excluir a conta.", exc)
        return
    total = sum(apagadas.values())
    st.success(f"Conta de {pessoa['name']} excluída: {total} linha(s) em "
               f"{len(apagadas)} tabela(s).")
