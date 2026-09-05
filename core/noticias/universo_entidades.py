"""Quem o resolvedor de entidades conhece — e por que ele não conhecia ninguém.

:mod:`core.noticias.entidades` sabe casar nome de empresa com ticker, expandir
para todas as classes (notícia da Petrobras vale para PETR3 e PETR4), herdar
setor e país do ativo. Nada disso funciona com ``Universo`` vazio: sem
``por_nome`` o casamento por nome não roda uma vez sequer, e sem
``setor_por_ticker``/``pais_por_ticker`` o setor só existe se o provedor o
declarar.

O universo nunca era montado. ``data_pipeline.jobs.update_noticias`` chamava
``coletar(consulta, provedores, registro=registro)`` sem ``universo=``, e o valor
por omissão é ``UNIVERSO_VAZIO``. O motor inteiro de resolução rodava no ramo
degradado: aceitava o ticker que o provedor declarasse e mais nada.

O efeito medido, na coleta de 04/09/2026: **43 notícias, 2 ativos resolvidos**
(AAPL e MSFT, ambos declarados por provedor americano), nenhum ticker brasileiro.
Com o piso de três itens por ativo, isso é uma vitrine com zero ativos medidos —
o componente de notícias fica no denominador sem nunca mover peso. Não havia erro
em lugar nenhum: cada peça funcionava, e a peça que as liga não era chamada
(``memoria: diagnostico-precisa-porta-de-entrada``).

Três fontes, três limitações separadas
---------------------------------------
B3, EUA e FIIs são consultados em blocos independentes, cada um com seu ``try``.
Uma fonte que falha entra em ``limitacoes`` com nome e motivo, e as outras duas
seguem. Um universo parcial declarado é útil; um universo parcial silencioso
apresentaria "não achamos ticker nesta notícia" onde o certo era "não sabíamos
procurar por ele".
"""
from __future__ import annotations

import logging
import re
import time

from sqlalchemy import text

from core.noticias.entidades import UNIVERSO_VAZIO, Universo
from core.noticias.normalizacao import normalizar_texto

logger = logging.getLogger(__name__)

#: Validade do universo em memória. O cadastro de empresas muda em escala de
#: semanas; reler a cada notícia custaria três consultas por item.
TTL_S = 6 * 3600

#: Nomes curtos demais casam dentro de outra palavra e o resolvedor já os
#: descarta (piso de 4 caracteres em ``resolver_tickers``). Estes são longos o
#: bastante para passar e genéricos o bastante para casar com qualquer notícia:
#: manter "Brasil" ou "Banco" no mapa de nomes atribuiria matéria macro a uma
#: empresa específica, que é o modo de falha que ``entidades`` foi escrito para
#: evitar.
NOMES_GENERICOS = frozenset({
    "brasil", "banco", "brasileira", "brasileiro",
    "energia", "energias", "cia", "companhia", "grupo", "holding",
    "participacoes", "industrias", "s a", "sa", "brb", "usa", "america",
    "corporation", "corp", "company", "inc", "group", "international",
    "technologies", "technology", "systems", "solutions", "capital",
    "financial", "national", "general", "global", "first", "united",
})

_SQL_B3 = text("""
    SELECT a.ticker, COALESCE(c.name, a.ticker) AS nome,
           COALESCE(NULLIF(c.sector, ''), '') AS setor
      FROM market.assets a
      JOIN market.companies c ON c.id = a.company_id
     WHERE a.is_active IS TRUE AND a.ticker IS NOT NULL
""")

_SQL_US = text("""
    SELECT symbol AS ticker, COALESCE(name, symbol) AS nome,
           COALESCE(sector, '') AS setor
      FROM market_us.company_snapshots
     WHERE is_active IS TRUE AND symbol IS NOT NULL
""")

_SQL_FII = text("""
    SELECT ticker, COALESCE(name, ticker) AS nome,
           COALESCE(NULLIF(segmento, ''), 'Fundo Imobiliário') AS setor
      FROM market.fiis
     WHERE ticker IS NOT NULL
""")

_SQL_ALIAS = text("""
    SELECT brapi_symbol, b3_ticker FROM market.ticker_alias
     WHERE brapi_symbol IS NOT NULL AND b3_ticker IS NOT NULL
""")

_cache: dict = {}


#: Sufixos societários e de classe, retirados do fim do nome. Manchete não diz
#: "Itau Unibanco Holding SA Pfd"; o cadastro, sim. Sem esta poda, o casamento
#: exigia que a forma jurídica aparecesse no texto -- e ela nunca aparece.
SUFIXOS = (
    "pfd", "ord", "adr", "ads", "cl a", "cl b", "class a", "class b",
    "s.a.", "s/a", "s.a", "sa", "s a", "on", "pn", "holding", "holdings",
    "inc.", "inc", "corp.", "corp", "corporation", "ltd.", "ltd", "plc",
    "co.", "n.v.", "nv", "ag", "the",
)


def _limpar_nome(nome: str) -> str:
    """Poda sufixos societários e de classe, do fim para o começo.

    Repete a poda enquanto houver o que podar: "Itau Unibanco Holding SA Pfd"
    perde ``Pfd``, depois ``SA``, depois ``Holding``, e sobra "Itau Unibanco" --
    que é como a imprensa escreve. A poda **não** inventa apelido: "Petroleo
    Brasileiro SA Pfd" vira "Petroleo Brasileiro", e não "Petrobras". Apelido de
    marca é outra coisa e mora em :data:`APELIDOS`, declarado à mão.
    """
    texto = _CORTES.sub("", " ".join(str(nome or "").split())).strip(" ,.-")
    mudou = True
    while mudou and texto:
        mudou = False
        baixo = texto.lower()
        for sufixo in SUFIXOS:
            if baixo.endswith(" " + sufixo):
                texto = texto[: -len(sufixo) - 1].strip(" ,.")
                mudou = True
                break
    return texto


#: Boilerplate de estrutura societária no fim do nome. O cadastro descreve o
#: papel ("Non-Cum Perp Pfd Registered Shs", "Units Cons of 1 Sh + 2 Pfd"); a
#: notícia fala da empresa. Cortado a partir do marcador, não podado token a
#: token, porque o rabo tem tamanho variável.
_CORTES = re.compile(
    r"\s+(?:non-cum|units?\s+cons|ctf\s+de\s+deposito|conv\s+pfd|"
    r"registered\s+shs|cons\s+of|sponsored|perp\s+pfd|shs).*$",
    re.IGNORECASE)

#: Apelidos de marca, **declarados à mão**. Nenhuma regra deriva "Petrobras" de
#: "Petroleo Brasileiro" ou "Cemig" de "Companhia Energetica de Minas Gerais":
#: é conhecimento de mundo, não morfologia. Por isso a lista é explícita e
#: pequena em vez de heurística e ampla -- uma heurística que acertasse estes
#: erraria em outros e o erro seria silencioso.
#:
#: Piso, não teto: o apelido **soma** ao nome do cadastro, nunca o substitui.
#: Ativo fora desta lista continua casando pelo nome oficial.
APELIDOS: dict[str, tuple[str, ...]] = {
    "PETR3": ("Petrobras",), "PETR4": ("Petrobras",),
    "CMIG3": ("Cemig",), "CMIG4": ("Cemig",),
    "SBSP3": ("Sabesp",),
    "USIM3": ("Usiminas",), "USIM5": ("Usiminas",), "USIM6": ("Usiminas",),
    "ELET3": ("Eletrobras",), "ELET6": ("Eletrobras",),
    "CSNA3": ("Siderurgica Nacional",),
    "ITSA3": ("Itausa",), "ITSA4": ("Itausa",),
    "RENT3": ("Localiza",),
    "BPAC11": ("BTG Pactual",),
    "NTCO3": ("Natura",),
    "VIVT3": ("Vivo",),
    "SANB3": ("Santander Brasil",), "SANB4": ("Santander Brasil",),
    "SANB11": ("Santander Brasil",),
    "YDUQ3": ("Estacio",),
    "CIEL3": ("Cielo",),
    "GOAU4": ("Metalurgica Gerdau",),
}


def _bloco(conn, sql, pais: str) -> tuple[dict, dict, dict]:
    pares: dict[str, str] = {}
    setores: dict[str, str] = {}
    paises: dict[str, str] = {}
    for linha in conn.execute(sql).mappings():
        ticker = str(linha["ticker"] or "").strip().upper().replace(".SA", "")
        if not ticker:
            continue
        pares[ticker] = _limpar_nome(linha["nome"])
        if linha["setor"]:
            setores[ticker] = str(linha["setor"])
        paises[ticker] = pais
    return pares, setores, paises


def carregar(*, engine=None, usar_cache: bool = True
             ) -> tuple[Universo, tuple[str, ...]]:
    """Universo conhecido e as limitações da montagem.

    Nunca levanta: coleta de notícias que aborta porque o cadastro de empresas
    engasgou troca uma degradação por um apagão. Fonte que falha vira limitação
    escrita, e ``Universo`` vazio com limitação é diferente de universo vazio
    silencioso — o primeiro diz por que não sabe.
    """
    agora = time.monotonic()
    if usar_cache and _cache.get("universo") is not None:
        if agora - _cache.get("em", 0.0) < TTL_S:
            return _cache["universo"], _cache["limitacoes"]

    if engine is None:
        try:
            from core.database import get_engine

            engine = get_engine()
        except Exception as exc:  # noqa: BLE001
            return UNIVERSO_VAZIO, (f"universo de entidades: {exc}",)
    if engine is None:
        return UNIVERSO_VAZIO, ("universo de entidades: sem banco configurado",)

    pares: dict[str, str] = {}
    setores: dict[str, str] = {}
    paises: dict[str, str] = {}
    limitacoes: list[str] = []
    contagem: dict[str, int] = {}

    for rotulo, sql, pais in (("B3", _SQL_B3, "BR"), ("EUA", _SQL_US, "US"),
                              ("FIIs", _SQL_FII, "BR")):
        try:
            with engine.connect() as conn:
                p, s, pa = _bloco(conn, sql, pais)
        except Exception as exc:  # noqa: BLE001 - fonte ausente é declarada
            causa = str(exc).splitlines()[0][:160]
            logger.warning("Universo de entidades sem %s: %s", rotulo, causa)
            limitacoes.append(
                f"universo de entidades sem {rotulo}: {causa} — notícia sobre "
                f"esses ativos não será atribuída a ticker")
            contagem[rotulo] = 0
            continue
        pares.update(p)
        setores.update(s)
        paises.update(pa)
        contagem[rotulo] = len(p)

    # ``market.ticker_alias`` é a tabela que já existe para o mesmo problema do
    # outro lado: a brapi grava sob símbolo divergente do ticker B3 (AXIA3 é
    # ELET3, MOTV3 é CCRO3). Sem aplicá-la, notícia da Eletrobras seria atribuída
    # a AXIA3 -- um símbolo que nenhuma tela do APP4 lê, e a vitrine ganharia uma
    # linha que ninguém consulta (``memoria: ingestao-brapi-tickers-divergentes``).
    try:
        with engine.connect() as conn:
            mapa = {str(r[0]).upper(): str(r[1]).upper()
                    for r in conn.execute(_SQL_ALIAS)}
    except Exception as exc:  # noqa: BLE001
        mapa = {}
        limitacoes.append(
            f"apelidos de ticker não aplicados ({str(exc).splitlines()[0][:120]}): "
            f"notícia pode ser atribuída ao símbolo do provedor em vez do da B3")
    if mapa:
        pares = {mapa.get(t, t): n for t, n in pares.items()}
        setores = {mapa.get(t, t): v for t, v in setores.items()}
        paises = {mapa.get(t, t): v for t, v in paises.items()}

    universo = Universo.de_pares(pares, setores, paises)
    universo = _sem_nomes_genericos(universo)
    universo = _com_apelidos(universo, pares)

    if universo.vazio:
        limitacoes.append(
            "universo de entidades vazio: só tickers declarados pelo provedor "
            "serão reconhecidos, e nenhum nome de empresa será casado")
    else:
        logger.info("Universo de entidades: %s ativos (%s)", len(pares),
                    ", ".join(f"{k}={v}" for k, v in contagem.items()))

    _cache["universo"] = universo
    _cache["limitacoes"] = tuple(limitacoes)
    _cache["em"] = agora
    return universo, tuple(limitacoes)


def _com_apelidos(universo: Universo, pares: dict[str, str]) -> Universo:
    """Soma :data:`APELIDOS` ao mapa de nomes, sem tocar no de tickers.

    O apelido é uma **chave a mais** para o mesmo conjunto de classes: depois
    disto "petrobras" e "petroleo brasileiro" levam ambos a PETR3 e PETR4. Os
    tickers do universo não mudam -- apelido é forma de escrever, não ativo
    novo, e inflar ``tickers`` faria ``conhece()`` aprovar símbolo inexistente.

    O apelido só entra se o ticker existir no cadastro lido. Uma entrada que
    envelheceu (empresa que saiu da bolsa) some sozinha, em vez de virar um
    nome que aponta para um ticker que ninguém mais tem.
    """
    por_nome = dict(universo.por_nome)
    for ticker, apelidos in APELIDOS.items():
        if ticker not in pares:
            continue
        # Todas as classes da empresa, não só a listada: o apelido segue a
        # mesma regra multi-classe do nome oficial.
        classes = universo.por_nome.get(normalizar_texto(pares[ticker]),
                                        (ticker,))
        for apelido in apelidos:
            chave = normalizar_texto(apelido)
            if not chave or chave in NOMES_GENERICOS:
                continue
            juntos = list(por_nome.get(chave, ()))
            for classe in classes:
                if classe not in juntos:
                    juntos.append(classe)
            por_nome[chave] = tuple(sorted(juntos))
    return Universo(tickers=universo.tickers, por_nome=por_nome,
                    setor_por_ticker=universo.setor_por_ticker,
                    pais_por_ticker=universo.pais_por_ticker)


def _sem_nomes_genericos(universo: Universo) -> Universo:
    """Remove do mapa de nomes o que casaria com metade do noticiário.

    O filtro age só sobre ``por_nome``: os tickers continuam todos no universo,
    porque reconhecer ``BBAS3`` declarado pelo provedor não tem nada de
    arriscado. O que sai é a chave "banco do brasil", que apareceria em toda
    matéria sobre política monetária.
    """
    por_nome = {nome: tickers for nome, tickers in universo.por_nome.items()
                if nome not in NOMES_GENERICOS}
    if len(por_nome) == len(universo.por_nome):
        return universo
    return Universo(tickers=universo.tickers, por_nome=por_nome,
                    setor_por_ticker=universo.setor_por_ticker,
                    pais_por_ticker=universo.pais_por_ticker)


def limpar_cache() -> None:
    """Esquece o universo em memória. Para os testes e para o publicador."""
    _cache.clear()
