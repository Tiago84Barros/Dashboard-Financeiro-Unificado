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

A leitura pede a chave (``->>``) e a gravação troca só as chaves do tema: o documento não
sai do servidor em nenhuma das duas, e a escrita deixou de poder sobrescrever o
que outra aba gravou -- não há mais um documento em memória para devolver.
Depois da mudança: 49 ms e 4 bytes na leitura, 242 ms na gravação -- o mesmo
que uma conta sem histórico nenhum.

Desde 31/10/2026 a conta guarda, junto do tema, o INSTANTE da escolha
(``app4_theme_at``, milissegundos desde a época). O tema muda se e somente se a
pessoa escolher, e "a escolha mais recente vale" só se decide com relógio:
sem ele, uma escolha que não chegou ao banco perdia para a anterior que chegou
-- e isso, visto da tela, é o app trocando de tema sozinho.
"""
import json
import time

from sqlalchemy import text

from core.user_accounts import _engine
from core.user_context import require_user

THEMES = ("dark", "light")
FIELD = "app4_theme"
FIELD_AT = "app4_theme_at"

# ``->>`` devolve texto; o que não for um inteiro curto vira 0 -- escolha de
# antes do relógio, a mais antiga possível. Nunca levanta por valor estranho.
_INSTANTE_GRAVADO = (
    "(CASE WHEN extra_settings ->> CAST(:field_at AS text) ~ '^[0-9]{1,18}$' "
    "THEN (extra_settings ->> CAST(:field_at AS text))::bigint ELSE 0 END)"
)


def _instante(valor) -> int:
    try:
        instante = int(valor)
    except (TypeError, ValueError):
        return 0
    return instante if instante > 0 else 0


def load_theme_choice() -> tuple[str, int] | None:
    """``(tema, instante_ms)`` da última escolha gravada, ou ``None``.

    ``None`` é "esta conta nunca escolheu", não "escuro": o padrão é decisão de
    quem pinta (ver :mod:`design.theme_selector`), e nunca é gravado como se
    fosse escolha. Tema gravado antes de existir o instante volta com ``0``.

    Pede as duas chaves com ``->>``: o documento -- que carrega o histórico dos
    chats -- não sai do servidor.
    """
    uid = require_user()
    with _engine().connect() as conn:
        linha = conn.execute(text(
            "SELECT extra_settings ->> CAST(:field AS text), "
            "extra_settings ->> CAST(:field_at AS text) "
            "FROM user_settings WHERE user_id = :uid"
        ), {"uid": uid, "field": FIELD, "field_at": FIELD_AT}).first()
    if not linha or linha[0] not in THEMES:
        return None
    return linha[0], _instante(linha[1])


def load_theme() -> str | None:
    """Tema gravado na conta, ou ``None`` quando a conta nunca escolheu um.

    Devolver ``"dark"`` para a ausência misturava duas respostas que não são a
    mesma: "esta conta escolheu escuro" e "esta conta não escolheu nada".
    Ausência acontece de verdade: a linha de ``user_settings`` nasce sem a
    chave, e é criada por qualquer preferência -- cenário, histórico, chat --,
    não só pelo tema.
    """
    escolha = load_theme_choice()
    return escolha[0] if escolha else None


def save_theme(theme: str, at: int | None = None) -> bool:
    """Grava a escolha de tema SE ela for a mais recente. ``True`` se gravou.

    ``at`` é o instante da escolha (ms), não o da gravação: uma escolha que
    ficou pendente e só agora alcança o banco entra com a hora em que foi
    feita. Por isso a gravação é condicional -- ela não passa por cima de uma
    escolha mais nova feita em outro aparelho enquanto esta esperava. Sem
    isso, a escolha atrasada de um navegador desfazia a escolha recente de
    outro: o tema mudava sem ninguém ter pedido aquilo naquele momento.

    ``False`` não é falha: é "a conta já tem escolha mais nova", e a próxima
    leitura entrega essa.
    """
    uid = require_user()
    if theme not in THEMES:
        raise ValueError("Tema inválido.")
    instante = int(time.time() * 1000) if at is None else _instante(at)
    with _engine().begin() as conn:
        # A linha pode não existir ainda: o tema é a primeira preferência que
        # muita conta grava.
        conn.execute(text(
            "INSERT INTO user_settings (user_id) VALUES (:uid) "
            "ON CONFLICT (user_id) DO NOTHING"
        ), {"uid": uid})
        # ``||`` troca só as duas chaves: o documento não vem ao cliente nem
        # volta inteiro, e o que outra aba gravou nas outras chaves fica.
        resultado = conn.execute(text(
            "UPDATE user_settings SET extra_settings = "
            "COALESCE(extra_settings, '{}'::jsonb) || jsonb_build_object("
            "CAST(:field AS text), CAST(:theme AS text), "
            "CAST(:field_at AS text), CAST(:at AS bigint)), "
            "updated_at = NOW() "
            f"WHERE user_id = :uid AND {_INSTANTE_GRAVADO} <= CAST(:at AS bigint)"
        ), {"uid": uid, "field": FIELD, "field_at": FIELD_AT,
            "theme": theme, "at": instante})
    return bool(resultado.rowcount)


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
