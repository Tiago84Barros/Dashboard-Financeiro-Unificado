"""
core/categorias.py — as categorias do Controle Financeiro, num lugar só.

Duas responsabilidades, e elas são distintas de propósito:

1. **O que a pessoa escolhe ao lançar.** ``listar``, ``criar`` e ``arquivar``
   leem e escrevem a tabela ``categories``. Antes disto, a lista do formulário
   era um literal em ``views/controle_financeiro.py``: três nomes desse literal
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

_SQL_LISTAR = """
    SELECT id::text AS id, name AS nome, (user_id IS NOT NULL) AS minha
    FROM   categories
    WHERE  type = :tipo
      AND  (user_id IS NULL OR user_id = CAST(:uid AS uuid))
      {filtro_ativo}
    ORDER  BY name
"""

#: A coluna ``active`` chega na migration 072, que roda À MÃO no Supabase. Até
#: lá a consulta com o filtro estoura ("column active does not exist") — e cair
#: no ``except`` devolveria uma lista de nomes SEM id, gravando lançamento sem
#: categoria justamente no caminho que esta mudança veio consertar. Por isso a
#: falta da coluna tem tratamento próprio: refaz a consulta sem o filtro.
_SQL_LISTAR_COM_ATIVO = _SQL_LISTAR.format(filtro_ativo="AND COALESCE(active, TRUE)")
_SQL_LISTAR_SEM_ATIVO = _SQL_LISTAR.format(filtro_ativo="")

_SQL_INSERIR = """
    INSERT INTO categories (id, user_id, name, type)
    VALUES (gen_random_uuid(), CAST(:uid AS uuid), :nome, :tipo)
    RETURNING id::text
"""

_SQL_ARQUIVAR = """
    UPDATE categories SET active = FALSE
    WHERE  id = CAST(:cid AS uuid) AND user_id = CAST(:uid AS uuid)
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
                    conn.execute(text(sql), {"tipo": tipo_db, "uid": _uid()})]
    except Exception:  # noqa: BLE001 - a tela não pode ficar sem seletor
        logger.warning("consulta de categorias falhou", exc_info=True)
        return None


def listar(tipo: str) -> list[dict]:
    """Categorias disponíveis para lançar naquele tipo.

    Devolve ``[{"id": str | None, "nome": str, "minha": bool}]``.
    ``minha`` separa a categoria criada pelo usuário da de sistema: só a
    primeira pode ser arquivada. ``id`` só é ``None`` para
    nome do ``SEED`` que ainda não existe no banco — e é justamente esse caso
    que gravava lançamento sem categoria antes desta mudança, agora visível.
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
        logger.error("nenhuma leitura de categorias de %s funcionou", tipo)
        linhas = []
    vistos = {normalizar(linha["nome"]) for linha in linhas}
    faltando = [{"id": None, "nome": nome, "minha": False}
            for nome in SEED.get(tipo, ())
                if normalizar(nome) not in vistos]
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
    if normalizar(limpo) in {normalizar(c["nome"]) for c in listar(tipo)}:
        return False, f"Já existe uma categoria “{limpo}” em {tipo}."
    try:
        with _engine().begin() as conn:
            conn.execute(text(_SQL_INSERIR),
                         {"uid": _uid(), "nome": limpo, "tipo": TIPOS[tipo]})
    except Exception as exc:  # noqa: BLE001 - a mensagem volta para a tela
        logger.exception("falha ao criar categoria %r", limpo)
        return False, f"Não foi possível criar: {exc}"
    return True, f"Categoria “{limpo}” criada."


def arquivar(categoria_id: str) -> tuple[bool, str]:
    """Some com a categoria do seletor SEM apagar a linha.

    Apagar levaria junto a categoria dos lançamentos que a usam — o histórico
    ficaria sem classificação e não haveria como voltar atrás. Categoria de
    sistema (``user_id IS NULL``) não é arquivável por aqui: ela não é de
    ninguém, e some para todo mundo.
    """
    try:
        with _engine().begin() as conn:
            linhas = conn.execute(
                text(_SQL_ARQUIVAR), {"cid": categoria_id, "uid": _uid()}).rowcount
    except Exception as exc:  # noqa: BLE001
        logger.exception("falha ao arquivar categoria %s", categoria_id)
        return False, f"Não foi possível arquivar: {exc}"
    if not linhas:
        return False, "Só dá para arquivar categoria criada por você."
    return True, "Categoria arquivada. Os lançamentos antigos continuam nela."
