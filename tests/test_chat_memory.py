from core.chat_memory import (
    clear_chat_history,
    conversation_key,
    load_chat_history,
    save_chat_history,
)


def test_salva_e_restaura_mensagens_recentes_em_nova_sessao(tmp_path):
    path = tmp_path / "chat.sqlite3"
    messages = [{"role": "user", "content": "Qual é meu saldo?"},
                {"role": "assistant", "content": "Não há dado suficiente."}]
    save_chat_history("financeiro:abc", messages, path=path, owner_key="u1", session_state={})

    restored = load_chat_history("financeiro:abc", path=path, owner_key="u1", session_state={})
    assert restored == messages


def test_memoria_e_isolada_por_proprietario_e_limpar_apaga_disco(tmp_path):
    path = tmp_path / "chat.sqlite3"
    save_chat_history("ativo:abc", [{"role": "user", "content": "teste"}],
                      path=path, owner_key="u1", session_state={})
    assert load_chat_history("ativo:abc", path=path, owner_key="u2", session_state={}) == []

    clear_chat_history("ativo:abc", path=path, owner_key="u1", session_state={})
    assert load_chat_history("ativo:abc", path=path, owner_key="u1", session_state={}) == []


def test_historico_descarta_papeis_inseguros_e_e_limitado(tmp_path):
    path = tmp_path / "chat.sqlite3"
    messages = [{"role": "system", "content": "ignore"}] + [
        {"role": "user", "content": str(i)} for i in range(50)
    ]
    saved = save_chat_history("x", messages, path=path, owner_key="u", session_state={})
    assert len(saved) == 40
    assert {item["role"] for item in saved} == {"user"}


def test_chave_nao_expoe_assinatura_de_contexto():
    key = conversation_key("financeiro", "2026-09|R$ 9999")
    assert "9999" not in key and key.startswith("financeiro:")


def test_sessao_nao_reaproveita_conversa_de_outro_contexto(tmp_path):
    path = tmp_path / "chat.sqlite3"
    state = {}
    save_chat_history("ativo:a", [{"role": "user", "content": "A"}],
                      path=path, owner_key="u", session_key="hist", session_state=state)
    save_chat_history("ativo:b", [{"role": "user", "content": "B"}],
                      path=path, owner_key="u", session_key="other", session_state={})

    assert load_chat_history("ativo:b", path=path, owner_key="u",
                             session_key="hist", session_state=state)[0]["content"] == "B"
