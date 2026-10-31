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
import json

from sqlalchemy import text

from core.user_accounts import _engine
from core.user_context import require_user

THEMES = ("dark", "light")
FIELD = "app4_theme"


def load_theme() -> str | None:
    """Tema gravado na conta, ou ``None`` quando a conta nunca escolheu um.

    Devolver ``"dark"`` para a ausência misturava duas respostas que não são a
    mesma: "esta conta escolheu escuro" e "esta conta não escolheu nada". Quem
    chama trata as duas igual -- e a segunda era adotada como preferência de
    verdade, gravada no cache da sessão, no lembrete do processo e no cookie do
    navegador. Uma leitura que não achou a chave apagava a memória de quem
    estava no claro, em silêncio e sem erro nenhum para mostrar.

    Ausência acontece de verdade: a linha de ``user_settings`` nasce sem a
    chave, e é criada por qualquer preferência -- cenário, histórico, chat --,
    não só pelo tema. Conta nova que nunca abriu Configurações → Geral tem
    linha, tem documento e não tem ``app4_theme``.

    ``None`` não é um tema: quem pinta decide o que fazer com a ausência (ver
    :func:`design.theme_selector.current_theme`), e o padrão escuro continua
    existindo -- só deixou de ser confundido com uma escolha.
    """
    uid = require_user()
    with _engine().connect() as conn:
        theme = conn.execute(text(
            "SELECT extra_settings ->> CAST(:field AS text) FROM user_settings "
            "WHERE user_id = :uid"
        ), {"uid": uid, "field": FIELD}).scalar()
    return theme if theme in THEMES else None


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


# -- seções do menu lateral ---------------------------------------------------

#: Chave do ``extra_settings`` com os módulos (``views/<modulo>.py``) que a
#: conta tirou do menu lateral. Guarda o que SAIU, e não o que fica: uma tela
#: nova que entrar no app aparece para todo mundo sem ninguém precisar editar a
#: preferência gravada de cada conta. O catálogo e a validação moram em
#: ``core/secoes_app.py``; aqui só a ida e a volta ao banco.
SECOES_OCULTAS_FIELD = "app4_secoes_ocultas"


def load_secoes_ocultas() -> list[str]:
    """Módulos que a conta tirou do menu; lista vazia quando nunca escolheu.

    Pede só a chave (``->``), pelo mesmo motivo do tema: o documento inteiro
    carrega o histórico dos chats. Valor que não é lista -- chave ausente,
    linha ausente, gravação estranha -- vira "nada oculto", que é o lado
    seguro: na dúvida a seção aparece.
    """
    uid = require_user()
    with _engine().connect() as conn:
        valor = conn.execute(text(
            "SELECT extra_settings -> CAST(:field AS text) FROM user_settings "
            "WHERE user_id = :uid"
        ), {"uid": uid, "field": SECOES_OCULTAS_FIELD}).scalar()
    if isinstance(valor, str):
        # Driver que não decodifica jsonb devolve o texto cru.
        try:
            valor = json.loads(valor)
        except ValueError:
            return []
    if not isinstance(valor, list):
        return []
    return [str(v) for v in valor if isinstance(v, str)]


def save_secoes_ocultas(modulos: list[str]) -> None:
    """Grava a lista inteira de uma vez -- ela é curta e quem a monta é a tela.

    ``jsonb_set`` no servidor, como o tema: só a chave muda, o resto do
    documento (chats, análises, tema, categorias) não sai do banco nem pode ser
    sobrescrito por uma cópia velha.
    """
    uid = require_user()
    valor = json.dumps([str(m) for m in modulos])
    with _engine().begin() as conn:
        conn.execute(text(
            "INSERT INTO user_settings (user_id) VALUES (:uid) "
            "ON CONFLICT (user_id) DO NOTHING"
        ), {"uid": uid})
        conn.execute(text(
            "UPDATE user_settings SET extra_settings = jsonb_set("
            "COALESCE(extra_settings, '{}'::jsonb), ARRAY[CAST(:field AS text)], "
            "CAST(:valor AS jsonb), true), updated_at = NOW() "
            "WHERE user_id = :uid"
        ), {"uid": uid, "field": SECOES_OCULTAS_FIELD, "valor": valor})
