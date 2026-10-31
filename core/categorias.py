"""
core/categorias.py — as categorias do Controle Financeiro, num lugar só.

Duas responsabilidades, e elas são distintas de propósito:

1. **O que a pessoa escolhe ao lançar.** ``listar``, ``criar`` e ``arquivar``
   leem e escrevem a tabela ``categories``; ``retirar`` e ``devolver`` mexem só
   no seletor de quem pediu (ver "retirar do seletor", abaixo). Antes disto, a
   lista do formulário era um literal em ``views/controle_financeiro.py``: três nomes desse literal
   (``Dividendos``, ``Restaurante``, ``Reserva de Despesa``) não existiam no
   banco, e escolher um deles gravava o lançamento **sem categoria nenhuma**,
   em silêncio — o ``next(...)`` que resolve o id devolvia ``None`` e o insert
   seguia feliz.

2. **O que o agregador considera aporte.** ``NOMES_DE_INVESTIMENTO`` e as duas
   formas derivadas dela (``SQL_INVESTIMENTO`` e ``CHAVES_DE_INVESTIMENTO``).
   Isto é deliberadamente uma CONSTANTE, e não uma consulta: se a lista viesse
   do banco, criar uma categoria nova reclassificaria histórico já fechado sem
   ninguém pedir. Uma categoria nova criada pela tela entra no agregado pelo
   ``transactions.type``, que o formulário já grava como ``investment``.

Havia três definições de "é investimento" no repositório — duas cópias
byte-a-byte do literal SQL (``core/controle.py`` e ``core/investimentos.py``) e
um ``frozenset`` normalizado que não batia com elas: o conjunto conhecia
``acao``, ``aporte investimento`` e ``fundo imobiliario``, que o SQL não citava.
Guarda duplicada não fica igual (``memoria: guarda-duplicada-diverge``); aqui
as três saem da MESMA lista, e um teste prova que não existe segunda cópia.
"""
from __future__ import annotations

import logging
import unicodedata
from collections.abc import Iterable

from sqlalchemy import text

logger = logging.getLogger(__name__)

# ── o que conta como aporte ──────────────────────────────────────────────────

#: Nomes de categoria que, mesmo em lançamento tipado como ``transfer``,
#: representam dinheiro saindo do caixa para investimento. As variantes com e
#: sem acento estão todas aqui porque a comparação em SQL é exata: o banco
#: guarda ``Renda Variável``, mas importações antigas gravaram ``Renda Variavel``.
#:
#: ``Resgate de Investimento`` NÃO entra: é o caminho inverso, e classificá-lo
#: como aporte somaria saída e entrada no mesmo número.
NOMES_DE_INVESTIMENTO: tuple[str, ...] = (
    "Investimento", "Investimentos",
    "Aporte em Investimento", "Aporte Investimento",
    "Renda Fixa", "Renda Variavel", "Renda Variável",
    "Exterior", "Reserva de Despesa", "Tesouro Direto",
    "Ações", "Acoes", "Ação", "Acao",
    "FIIs", "FII",
    "Fundos Imobiliários", "Fundos Imobiliarios",
    "Fundo Imobiliário", "Fundo Imobiliario",
    "Cripto", "Criptoativos", "Criptomoedas",
)

#: Forma interpolável em ``IN (...)``. Sem parâmetro ligado porque as consultas
#: que a usam são f-strings de módulo, montadas na importação; a lista é
#: literal e fechada, então não há entrada de usuário passando por aqui.
SQL_INVESTIMENTO: str = ",".join(f"'{nome}'" for nome in NOMES_DE_INVESTIMENTO)


def normalizar(value: object) -> str:
    """Texto comparável: sem acento, sem caixa, sem separador."""
    if value is None:
        return ""
    texto = unicodedata.normalize("NFKD", str(value))
    texto = texto.encode("ascii", "ignore").decode("ascii")
    texto = texto.replace("_", " ").replace("-", " ").strip().casefold()
    return " ".join(texto.split())


#: Mesma lista, na forma que a comparação em Python usa.
CHAVES_DE_INVESTIMENTO: frozenset[str] = frozenset(
    normalizar(nome) for nome in NOMES_DE_INVESTIMENTO
)


# ── as categorias do formulário ──────────────────────────────────────────────

#: Tipo escolhido na tela → tipo gravado em ``categories.type``.
TIPOS: dict[str, str] = {
    "entrada": "income",
    "saida": "expense",
    "investimento": "investment",
}

#: O que a migration 072 garante existir. Usado também como CHÃO da listagem:
#: enquanto a migration não tiver rodado no Supabase, ``listar`` completa a
#: lista com estes nomes em vez de devolver um seletor vazio — mas marca-os com
#: ``id=None``, e a tela diz que o banco ainda não tem a categoria.
SEED: dict[str, tuple[str, ...]] = {
    "entrada": ("Salário", "Renda Extra", "Dividendos", "Reembolso", "Outros"),
    "saida": (
        "Mercado", "Compras", "Condomínio", "Luz", "Internet", "Transporte",
        "Combustível", "Saúde", "Despesas Domésticas", "Lazer", "Assinaturas",
        "Educação", "Restaurante", "Financiamento", "Pagamento de Cartão",
        "Outros",
    ),
    "investimento": (
        "Renda Fixa", "Renda Variável", "Exterior", "Reserva de Despesa",
        "Aporte em Investimento", "Outros",
    ),
}

#: Chave de ``user_settings.extra_settings`` com os ids das categorias que a
#: pessoa tirou do PRÓPRIO seletor. Preferência da conta, e não coluna de
#: ``categories``, porque a maioria das categorias é de sistema
#: (``user_id IS NULL``): marcar a linha tiraria a opção de todo mundo. E mora
#: no jsonb que o tema já usa, sem migration: ``categories.active`` dependia da
#: 072 rodada à mão no Supabase, e uma coluna nova repetiria a espera.
CAMPO_RETIRADAS = "cf_categorias_retiradas"

# A lista de retiradas vem na MESMA consulta: o seletor do lançamento é
# desenhado a cada rerun do Controle Financeiro, e uma segunda ida ao banco só
# para filtrá-lo dobraria o custo de uma tela que se usa todo dia. Subconsulta
# sem correlação, e não JOIN: o Postgres a avalia uma vez (InitPlan), enquanto
# o JOIN extrairia a chave de novo em cada linha de categoria -- e o
# ``extra_settings`` da conta grande passa de 400 KB de histórico de chat
# (``core/user_preferences.py``). ``@>`` sobre algo que não é lista, ou sem
# linha em ``user_settings``, dá falso ou nulo: na dúvida a categoria aparece,
# que é o lado seguro.
_COLUNA_RETIRADA = """,
           COALESCE(
               (SELECT s.extra_settings -> CAST(:campo AS text)
                  FROM user_settings s
                 WHERE s.user_id = CAST(:uid AS uuid))
               @> jsonb_build_array(c.id::text),
               FALSE) AS retirada"""

_SQL_LISTAR = """
    SELECT c.id::text AS id, c.name AS nome, (c.user_id IS NOT NULL) AS minha
           {coluna_retirada}
    FROM   categories c
    WHERE  c.type = :tipo
      AND  (c.user_id IS NULL OR c.user_id = CAST(:uid AS uuid))
      {filtro_ativo}
    ORDER  BY c.name
"""

#: A coluna ``active`` chega na migration 072, que roda À MÃO no Supabase. Até
#: lá a consulta com o filtro estoura ("column active does not exist") — e cair
#: no ``except`` devolveria uma lista de nomes SEM id, gravando lançamento sem
#: categoria justamente no caminho que esta mudança veio consertar. Por isso a
#: falta da coluna tem tratamento próprio: refaz a consulta sem o filtro.
_SQL_LISTAR_COM_ATIVO = _SQL_LISTAR.format(
    coluna_retirada=_COLUNA_RETIRADA, filtro_ativo="AND COALESCE(c.active, TRUE)")
_SQL_LISTAR_SEM_ATIVO = _SQL_LISTAR.format(
    coluna_retirada=_COLUNA_RETIRADA, filtro_ativo="")
#: Último recurso: a consulta de antes das retiradas. Se a leitura da
#: preferência quebrar, o seletor mostra categoria a mais -- e não cai para o
#: SEED sem id, que grava lançamento sem categoria.
_SQL_LISTAR_SEM_RETIRADAS = _SQL_LISTAR.format(coluna_retirada="", filtro_ativo="")

_SQL_INSERIR = """
    INSERT INTO categories (id, user_id, name, type)
    VALUES (gen_random_uuid(), CAST(:uid AS uuid), :nome, :tipo)
    RETURNING id::text
"""

_SQL_ARQUIVAR = """
    UPDATE categories SET active = FALSE
    WHERE  id = CAST(:cid AS uuid) AND user_id = CAST(:uid AS uuid)
"""

# Retirar e devolver mexem SÓ na chave, e no servidor (``jsonb_set``): o
# documento ``extra_settings`` carrega o histórico dos chats, e ir buscá-lo
# inteiro para mudar uma lista de ids é o custo que ``core/user_preferences.py``
# já mediu e eliminou para o tema.
_SQL_GARANTIR_PREFERENCIAS = """
    INSERT INTO user_settings (user_id) VALUES (CAST(:uid AS uuid))
    ON CONFLICT (user_id) DO NOTHING
"""

_LISTA_RETIRADAS = """CASE WHEN jsonb_typeof(extra_settings -> CAST(:campo AS text)) = 'array'
                         THEN extra_settings -> CAST(:campo AS text)
                         ELSE '[]'::jsonb END"""

# Tirar antes de pôr: retirar duas vezes não duplica o id na lista.
_SQL_RETIRAR = f"""
    UPDATE user_settings
       SET extra_settings = jsonb_set(
               COALESCE(extra_settings, '{{}}'::jsonb),
               ARRAY[CAST(:campo AS text)],
               ({_LISTA_RETIRADAS} - CAST(:cid AS text))
                   || jsonb_build_array(CAST(:cid AS text)),
               true),
           updated_at = NOW()
     WHERE user_id = CAST(:uid AS uuid)
"""

_SQL_DEVOLVER = f"""
    UPDATE user_settings
       SET extra_settings = jsonb_set(
               COALESCE(extra_settings, '{{}}'::jsonb),
               ARRAY[CAST(:campo AS text)],
               {_LISTA_RETIRADAS} - CAST(:cid AS text),
               true),
           updated_at = NOW()
     WHERE user_id = CAST(:uid AS uuid)
"""


def _engine():
    from core.database import get_engine

    return get_engine()


def _uid() -> str:
    from core.user_context import require_user

    return require_user()


def _consultar(sql: str, tipo_db: str) -> list[dict] | None:
    """Executa numa conexão própria. ``None`` quando a consulta falhou."""
    try:
        with _engine().connect() as conn:
            return [dict(row._mapping) for row in
                    conn.execute(text(sql), {"tipo": tipo_db, "uid": _uid(),
                                             "campo": CAMPO_RETIRADAS})]
    except Exception:  # noqa: BLE001 - a tela não pode ficar sem seletor
        logger.warning("consulta de categorias falhou", exc_info=True)
        return None


def listar(tipo: str, *, com_retiradas: bool = False) -> list[dict]:
    """Categorias disponíveis para lançar naquele tipo.

    Devolve ``[{"id": str | None, "nome": str, "minha": bool, "retirada": bool}]``.
    ``minha`` separa a categoria criada pelo usuário da de sistema.
    ``retirada`` marca a que o usuário tirou do próprio seletor; ela só vem
    na lista com ``com_retiradas=True`` (Configurações, que oferece devolvê-la).
    ``id`` só é ``None`` para nome do ``SEED`` que ainda não existe no banco —
    e é justamente esse caso que gravava lançamento sem categoria antes desta
    mudança, agora visível.
    """
    tipo_db = TIPOS.get(tipo)
    if tipo_db is None:
        raise ValueError(f"tipo de categoria desconhecido: {tipo!r}")
    linhas = _consultar(_SQL_LISTAR_COM_ATIVO, tipo_db)
    if linhas is None:
        # Transação envenenada não aceita o plano B na mesma conexão
        # (``memoria: fallback-morre-com-a-transacao-abortada``): ``_consultar``
        # abre a sua.
        linhas = _consultar(_SQL_LISTAR_SEM_ATIVO, tipo_db)
    if linhas is None:
        linhas = _consultar(_SQL_LISTAR_SEM_RETIRADAS, tipo_db)
    if linhas is None:
        logger.error("nenhuma leitura de categorias de %s funcionou", tipo)
        linhas = []
    linhas = [{**linha, "retirada": bool(linha.get("retirada"))} for linha in linhas]
    # ``vistos`` sai ANTES de filtrar as retiradas: senão uma categoria de
    # sistema retirada voltaria ao seletor pela porta do SEED, sem id — e
    # gravando lançamento sem categoria.
    vistos = {normalizar(linha["nome"]) for linha in linhas}
    faltando = [{"id": None, "nome": nome, "minha": False, "retirada": False}
                for nome in SEED.get(tipo, ())
                if normalizar(nome) not in vistos]
    if not com_retiradas:
        linhas = [linha for linha in linhas if not linha["retirada"]]
    return linhas + sorted(faltando, key=lambda item: item["nome"])


def criar(nome: str, tipo: str) -> tuple[bool, str]:
    """Cria a categoria do usuário. Devolve ``(ok, mensagem)``.

    A duplicata é conferida por nome NORMALIZADO: ``Renda Variavel`` e
    ``Renda Variável`` são a mesma categoria para quem lança, e duas linhas
    partiriam o histórico em duas fatias que nenhuma tela soma.
    """
    limpo = " ".join(str(nome or "").split())
    if not limpo:
        return False, "Dê um nome à categoria."
    if len(limpo) > 60:
        return False, "Nome longo demais (máximo de 60 caracteres)."
    if tipo not in TIPOS:
        return False, f"Tipo desconhecido: {tipo}."
    # Com as retiradas: criar de novo o nome que a pessoa tirou do seletor
    # faria uma segunda linha e partiria o histórico; o caminho é devolvê-la.
    existente = next((c for c in listar(tipo, com_retiradas=True)
                      if normalizar(c["nome"]) == normalizar(limpo)), None)
    if existente is not None and existente["retirada"]:
        return False, (f"“{existente['nome']}” já existe e está fora do seu "
                       "seletor. Devolva-a em vez de criar outra.")
    if existente is not None:
        return False, f"Já existe uma categoria “{limpo}” em {tipo}."
    try:
        with _engine().begin() as conn:
            conn.execute(text(_SQL_INSERIR),
                         {"uid": _uid(), "nome": limpo, "tipo": TIPOS[tipo]})
    except Exception as exc:  # noqa: BLE001 - a frase volta para a tela
        # O texto da excecao (driver, SQL) fica no log e no log de lacunas;
        # a tela recebe so a frase (05/10/2026, Configuracoes -> Restricoes).
        logger.exception("falha ao criar categoria %r", limpo)
        _registrar(exc)
        return False, "Não foi possível criar a categoria. O detalhe técnico foi registrado para o administrador."
    return True, f"Categoria “{limpo}” criada."


def _registrar(exc: BaseException) -> None:
    try:
        from core.lacunas import registrar_excecao

        registrar_excecao(exc)
    except Exception:  # noqa: BLE001 - lacuna e extra, nunca requisito
        logger.warning("falha ao registrar a excecao como lacuna", exc_info=True)


def arquivar(categoria_id: str) -> tuple[bool, str]:
    """Some com a categoria do seletor SEM apagar a linha.

    Apagar levaria junto a categoria dos lançamentos que a usam — o histórico
    ficaria sem classificação e não haveria como voltar atrás. Categoria de
    sistema (``user_id IS NULL``) não é arquivável por aqui: ela não é de
    ninguém, e some para todo mundo.

    A tela de Configurações deixou de chamar esta função em 31/10/2026: lá o
    botão é ``retirar``, que vale também para a de sistema e tem volta.
    """
    try:
        with _engine().begin() as conn:
            linhas = conn.execute(
                text(_SQL_ARQUIVAR), {"cid": categoria_id, "uid": _uid()}).rowcount
    except Exception as exc:  # noqa: BLE001
        logger.exception("falha ao arquivar categoria %s", categoria_id)
        _registrar(exc)
        return False, "Não foi possível arquivar a categoria. O detalhe técnico foi registrado para o administrador."
    if not linhas:
        return False, "Só dá para arquivar categoria criada por você."
    return True, "Categoria arquivada. Os lançamentos antigos continuam nela."


# ── retirar do seletor ───────────────────────────────────────────────────────

def _gravar_retiradas(sql: str, ids: list[str]) -> None:
    uid = _uid()
    with _engine().begin() as conn:
        # A linha de ``user_settings`` pode não existir: conta nova que nunca
        # escolheu tema nem conversou com a LLM ainda não tem preferência.
        conn.execute(text(_SQL_GARANTIR_PREFERENCIAS), {"uid": uid})
        for cid in ids:
            conn.execute(text(sql),
                         {"uid": uid, "campo": CAMPO_RETIRADAS, "cid": cid})


def retirar(categoria_ids: Iterable[str], tipo: str) -> tuple[bool, str]:
    """Tira categorias do seletor de lançamento DE QUEM PEDIU. Nada mais.

    Vale para a de sistema e para a criada pela pessoa. A linha de
    ``categories`` não muda: os lançamentos antigos continuam classificados
    nela, os totais por categoria não se mexem, as grades de edição continuam
    oferecendo o nome (um lançamento antigo precisa poder mantê-lo) e
    ``devolver`` desfaz. Apagar a de sistema não seria opção: ela é de todos.

    Recusa deixar o tipo sem nenhuma categoria no seletor: o formulário
    gravaria o lançamento sem categoria, que é o defeito que este módulo
    nasceu para impedir.
    """
    if tipo not in TIPOS:
        return False, f"Tipo desconhecido: {tipo}."
    pedidas = list(dict.fromkeys(str(c) for c in categoria_ids if c))
    if not pedidas:
        return False, "Escolha ao menos uma categoria para retirar."
    todas = listar(tipo, com_retiradas=True)
    por_id = {c["id"]: c for c in todas if c["id"]}
    estranhas = [cid for cid in pedidas if cid not in por_id]
    if estranhas:
        return False, "Categoria não encontrada neste tipo. Recarregue a tela."
    novas = [cid for cid in pedidas if not por_id[cid]["retirada"]]
    if not novas:
        return True, "Essas categorias já estavam fora do seu seletor."
    sobram = [c for c in todas
              if c["id"] and not c["retirada"] and c["id"] not in novas]
    if not sobram:
        return False, ("Deixe ao menos uma categoria neste tipo: sem nenhuma, o "
                       "lançamento seria gravado sem categoria.")
    try:
        _gravar_retiradas(_SQL_RETIRAR, novas)
    except Exception as exc:  # noqa: BLE001
        logger.exception("falha ao retirar categorias %s", novas)
        _registrar(exc)
        return False, "Não foi possível retirar a categoria. O detalhe técnico foi registrado para o administrador."
    nomes = ", ".join(f"“{por_id[cid]['nome']}”" for cid in novas)
    verbo = "saiu" if len(novas) == 1 else "saíram"
    return True, (f"{nomes} {verbo} do seu seletor. Os lançamentos antigos "
                  "continuam classificados como estavam.")


def devolver(categoria_ids: Iterable[str]) -> tuple[bool, str]:
    """Põe de volta no seletor o que ``retirar`` tirou."""
    pedidas = list(dict.fromkeys(str(c) for c in categoria_ids if c))
    if not pedidas:
        return False, "Escolha ao menos uma categoria para devolver."
    try:
        _gravar_retiradas(_SQL_DEVOLVER, pedidas)
    except Exception as exc:  # noqa: BLE001
        logger.exception("falha ao devolver categorias %s", pedidas)
        _registrar(exc)
        return False, "Não foi possível devolver a categoria. O detalhe técnico foi registrado para o administrador."
    return True, "De volta ao seletor de lançamento."
