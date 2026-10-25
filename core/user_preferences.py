"""Preferências pessoais: lista permitida e escrita pontual da chave.

O tema mora numa chave do ``extra_settings`` (jsonb) que a conta compartilha
com o histórico dos chats e o das análises. Ler e gravar o documento inteiro
para mexer numa palavra fazia o custo do tema crescer com o que NÃO é tema:
medido em 25/10/2026 contra o Supabase, a conta do administrador -- 480 KB de
``extra_settings``, dos quais 380 KB são conversa -- pagava 225 ms e 480 KB de
rede por leitura (contra 47 ms e 23 bytes de uma conta pequena) e 561 ms por
gravação, com picos de 1,95 s. A gravação era pior do que lenta: um
read-modify-write que segura a linha com ``FOR UPDATE`` enquanto arrasta o
documento para o cliente e o devolve inteiro, disputando a mesma linha com o
gravador de chat, que faz exatamente o mesmo. Na única conexão do pool do
Cloud, isso é tempo suficiente para a escolha do tema não chegar ao banco antes
de a pessoa desistir -- e o claro "não entrava" só na conta grande.

A leitura pede a chave (``->>``) e a gravação usa ``jsonb_set``: o documento não
sai do servidor em nenhuma das duas, e a escrita deixou de poder sobrescrever o
que outra aba gravou -- não há mais um documento em memória para devolver.
Depois da mudança: 49 ms e 4 bytes na leitura, 242 ms na gravação -- o mesmo
que uma conta sem histórico nenhum.
"""
from sqlalchemy import text

from core.user_accounts import _engine
from core.user_context import require_user

THEMES = ("dark", "light")
FIELD = "app4_theme"


def load_theme() -> str:
    uid = require_user()
    with _engine().connect() as conn:
        theme = conn.execute(text(
            "SELECT extra_settings ->> CAST(:field AS text) FROM user_settings "
            "WHERE user_id = :uid"
        ), {"uid": uid, "field": FIELD}).scalar()
    return theme if theme in THEMES else "dark"


def save_theme(theme: str) -> None:
    uid = require_user()
    if theme not in THEMES:
        raise ValueError("Tema inválido.")
    with _engine().begin() as conn:
        # A linha pode não existir ainda: o tema é a primeira preferência que
        # muita conta grava.
        conn.execute(text(
            "INSERT INTO user_settings (user_id) VALUES (:uid) "
            "ON CONFLICT (user_id) DO NOTHING"
        ), {"uid": uid})
        conn.execute(text(
            "UPDATE user_settings SET extra_settings = jsonb_set("
            "COALESCE(extra_settings, '{}'::jsonb), ARRAY[CAST(:field AS text)], "
            "to_jsonb(CAST(:theme AS text)), true), updated_at = NOW() "
            "WHERE user_id = :uid"
        ), {"uid": uid, "field": FIELD, "theme": theme})
