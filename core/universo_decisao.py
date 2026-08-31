# -*- coding: utf-8 -*-
"""Universo de decisao: o que o app considera ao recomendar, e o que ele descarta.

Um analista senior nao trava porque parte do cadastro esta suja. Ele descarta
o que nao da para decidir e opera com o resto, dizendo com que confianca opera.
Este modulo torna essa politica explicita e mensuravel, em vez de deixar cada
tela decidir por conta propria o que faz com dado ruim.

Tres populacoes, e a diferenca entre elas e o ponto do modulo:

``nominal``      linhas no cadastro. Inclui casca: ticker que existe no
                 registro e nunca negociou. Nao e universo de investimento.
``investivel``   tem preco. Existe como ativo que alguem pode comprar.
``apto``         tem o dado minimo para SUSTENTAR uma recomendacao.

Descartar casca (``nominal`` -> ``investivel``) nao custa nada e nao pode
baixar a confianca: nunca houve ativo ali. Descartar por dado faltando
(``investivel`` -> ``apto``) custa: era ativo de verdade e ficamos sem opiniao
sobre ele. So o segundo entra na conta de confianca.

A decisao de descartar depende do que SOBRA, nao do que sai. Universo de 1.111
acoes americanas e abundante mesmo representando 36% do cadastro; universo de
12 nomes nao sustenta carteira nenhuma por mais limpo que esteja. Por isso o
gate primario e ``MINIMO_ABSOLUTO``, e o percentual e reportado como o preco
que se pagou, nao como o criterio.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Piso para que o universo remanescente ainda sustente uma carteira
# diversificada. Abaixo disso, descartar deixaria de ser higiene e viraria
# amostragem: a carteira passaria a ser consequencia do que sobrou de dado.
MINIMO_ABSOLUTO = 40

# Fatia do universo INVESTIVEL (nao do cadastro) que precisa estar apta para
# que o descarte seja rotina e nao evento. Abaixo disso o descarte ainda
# acontece, mas a secao passa a declarar ressalva.
MARGEM_CONFORTAVEL = 0.60

MODO_DESCARTAR = "descartar"
MODO_RESSALVA = "ressalva"
MODO_INSUFICIENTE = "insuficiente"


@dataclass(frozen=True)
class Universo:
    """Fotografia do universo de um modulo e da politica aplicada a ele."""

    modulo: str
    nominal: int
    investivel: int
    apto: int
    exemplos_descartados: tuple[str, ...] = ()
    notas: tuple[str, ...] = field(default_factory=tuple)
    minimo_absoluto: int = MINIMO_ABSOLUTO

    @property
    def casca(self) -> int:
        """Linhas de cadastro que nunca foram ativo negociavel."""
        return max(0, self.nominal - self.investivel)

    @property
    def sem_dado(self) -> int:
        """Ativos reais sobre os quais o app nao consegue ter opiniao."""
        return max(0, self.investivel - self.apto)

    @property
    def share_apto(self) -> float:
        """Aptos sobre INVESTIVEIS. O denominador exclui casca de proposito:
        medir contra o cadastro faria o app parecer pior por ter um registro
        mais completo, o que inverte o incentivo."""
        return (self.apto / self.investivel) if self.investivel else 0.0

    @property
    def share_nominal(self) -> float:
        return (self.apto / self.nominal) if self.nominal else 0.0

    @property
    def modo(self) -> str:
        if self.apto >= self.minimo_absoluto:
            return (MODO_DESCARTAR if self.share_apto >= MARGEM_CONFORTAVEL
                    else MODO_RESSALVA)
        return MODO_INSUFICIENTE

    @property
    def descarta(self) -> bool:
        """``True`` quando o app pode simplesmente ignorar o que nao e apto."""
        return self.modo in (MODO_DESCARTAR, MODO_RESSALVA)

    def resumo(self) -> str:
        if self.modo == MODO_INSUFICIENTE:
            return (f"{self.apto} ativos aptos - abaixo do piso de "
                    f"{self.minimo_absoluto} para sustentar carteira")
        preco = (f", {self.sem_dado} descartados por dado insuficiente"
                 if self.sem_dado else "")
        casca = f" ({self.casca} cascas de cadastro ignoradas)" if self.casca else ""
        return (f"{self.apto} de {self.investivel} ativos negociaveis "
                f"({self.share_apto * 100:.0f}%){preco}{casca}")


def _scalar(conn, sql: str, params: dict | None = None) -> int:
    from sqlalchemy import text
    return int(conn.execute(text(sql), params or {}).scalar() or 0)


def universo_b3(engine=None) -> Universo:
    """B3: aptidao vem de ``core.data_confidence``, o indice que ja media
    cobertura/frescor/integridade por ticker e - ate A-125 - nao era lido por
    ninguem. Usa-lo como gate e o que finalmente lhe da um consumidor."""
    from core.data_confidence import _PRECO_VELHO_DIAS, LIMIAR_MEDIA, compute_confidence
    scored = compute_confidence(engine)
    if not scored:
        return Universo("Empresas B3", 0, 0, 0,
                        notas=("indice de confianca indisponivel",))
    # A-134: "investivel" era so "tem algum preco na serie" - LUXM3, com o
    # ultimo pregao em 2015, contava como ativo negociavel. Nao conta: sem
    # preco recente o papel esta fora do mercado, e mante-lo no DENOMINADOR
    # da abrangencia mede o modulo por uma cobertura que ninguem poderia
    # usar. Sai do numerador e do denominador, nao de um so.
    investivel = [s for s in scored
                  if s.get("dias_preco") is not None
                  and s["dias_preco"] <= _PRECO_VELHO_DIAS]
    aptos = [s for s in investivel
             if float(s.get("score") or 0) >= LIMIAR_MEDIA]
    nomes_aptos = {s["ticker"] for s in aptos}
    ruins = sorted(s["ticker"] for s in investivel
                   if s["ticker"] not in nomes_aptos)
    return Universo(
        modulo="Empresas B3",
        nominal=len(scored),
        investivel=len(investivel),
        apto=len(aptos),
        exemplos_descartados=tuple(ruins[:8]),
        notas=(f"gate: confianca de dados >= {LIMIAR_MEDIA:.0f}",),
    )


# A-134: o gate contava `market.fiis` cru. Depois do A-133 a liquidez pode vir
# da fita oficial da B3, e a tela de FIIs consome `market.fii_selection_inputs`
# -- a vitrine, nao o cadastro. Medir o cadastro subestimava: 306 de 432 (70,8%)
# contra 349 (80,8%) no dado que a decisao realmente le.
#
# A vitrine e a fonte certa tambem por um motivo que a fita nao atende: fundo
# que nao esta nela nao e decidivel, tenha fita ou nao. Contar pela fita daria
# 84,2% incluindo 36 investiveis que a selecao nunca ve -- numero melhor e
# falso. E, ao contrario da fita, a vitrine existe nos dois ambientes.

_QUARTETO_VITRINE = """
        SELECT 1 FROM market.fii_selection_inputs v
        WHERE v.ticker = f.ticker
          AND (v.payload_json::jsonb->>'liquidez_diaria') IS NOT NULL
          AND (v.payload_json::jsonb->>'dy_12m') IS NOT NULL
          AND (v.payload_json::jsonb->>'pvp')::numeric > 0"""


def _sql_apto_fii(com_vitrine: bool) -> str:
    """Contagem de FIIs aptos: quantos investiveis a decisao consegue ler."""
    if not com_vitrine:
        return """
            SELECT count(*) FROM market.fiis
            WHERE price > 0 AND dy_12m IS NOT NULL
              AND pvp IS NOT NULL AND pvp > 0
              AND liquidez_diaria IS NOT NULL"""
    return f"""
        SELECT count(*) FROM market.fiis f
        WHERE f.price > 0 AND EXISTS ({_QUARTETO_VITRINE})"""


def _sql_ruins_fii(com_vitrine: bool) -> str:
    """Exemplos de investivel que a decisao nao le, sob o criterio do gate."""
    if not com_vitrine:
        return """
            SELECT ticker FROM market.fiis
            WHERE price > 0 AND (dy_12m IS NULL OR pvp IS NULL OR pvp <= 0
                                 OR liquidez_diaria IS NULL)
            ORDER BY ticker LIMIT 8"""
    return f"""
        SELECT f.ticker FROM market.fiis f
        WHERE f.price > 0 AND NOT EXISTS ({_QUARTETO_VITRINE})
        ORDER BY f.ticker LIMIT 8"""


def _nota_gate_fii(com_vitrine: bool) -> str:
    if com_vitrine:
        return ("gate: preco, DY, P/VP e liquidez na vitrine que a tela le "
                "(liquidez ja arbitrada contra a fita oficial da B3)")
    return ("gate: preco, DY, P/VP e liquidez de cadastro "
            "(vitrine indisponivel neste ambiente)")


def _tem_vitrine_fii(conn) -> bool:
    """Vitrine ausente ou vazia derruba a tela se a query a referenciar."""
    from sqlalchemy import text
    existe = conn.execute(text("""
        SELECT 1 FROM information_schema.tables
        WHERE table_schema='market' AND table_name='fii_selection_inputs'
    """)).scalar()
    if not existe:
        return False
    return bool(conn.execute(
        text("SELECT count(*) FROM market.fii_selection_inputs")).scalar())


def universo_fii(engine=None) -> Universo:
    """FII: casca de cadastro domina. ``market.fiis`` guarda mais de mil linhas
    e a maioria nao tem preco - fundo encerrado, ticker de emissao, registro
    CVM sem negociacao. Aptidao exige o quarteto que a decisao consome:
    preco, DY, P/VP e liquidez."""
    from sqlalchemy import text

    from core.database import get_engine
    eng = engine or get_engine()
    with eng.connect() as conn:
        nominal = _scalar(conn, "SELECT count(*) FROM market.fiis")
        investivel = _scalar(conn,
                             "SELECT count(*) FROM market.fiis WHERE price > 0")
        com_vitrine = _tem_vitrine_fii(conn)
        apto = _scalar(conn, _sql_apto_fii(com_vitrine))
        # Os exemplos tem de obedecer ao mesmo criterio da contagem, senao a
        # tela lista como descartado quem o gate acabou de aprovar.
        ruins = tuple(r[0] for r in conn.execute(
            text(_sql_ruins_fii(com_vitrine))).fetchall())
    return Universo(
        modulo="Selecao de FIIs",
        nominal=nominal, investivel=investivel, apto=apto,
        exemplos_descartados=ruins,
        notas=(_nota_gate_fii(com_vitrine),),
    )


def universo_us(engine=None) -> Universo:
    """EUA: a vitrine ja classifica em faixas. ``decision_grade`` e a unica que
    sustenta recomendacao; ``screen_grade`` e ``research_grade`` tem dado, mas
    nao o bastante, e ``stale`` esta preso a uma versao antiga de score."""
    from sqlalchemy import text

    from core.database import get_engine
    eng = engine or get_engine()
    from core.us_methodology import US_FUNDAMENTAL_SCORE_VERSION as _ver
    # So a geracao corrente conta. Republicar a vitrine nao apaga as linhas das
    # versoes anteriores, e ate 25/08/2026 elas entravam no denominador: 221
    # simbolos de julho, marcados 'stale', faziam o universo parecer maior e a
    # abrangencia parecer pior do que e.
    with eng.connect() as conn:
        nominal = _scalar(conn, """SELECT count(*) FROM market_us.company_snapshots
                                   WHERE score_version = :v""", {"v": _ver})
        apto = _scalar(conn, """SELECT count(*) FROM market_us.company_snapshots
                                WHERE score_version = :v
                                  AND score_status = 'decision_grade'""",
                       {"v": _ver})
        ruins = tuple(r[0] for r in conn.execute(text("""
            SELECT symbol FROM market_us.company_snapshots
            WHERE score_version = :v AND score_status <> 'decision_grade'
            ORDER BY symbol LIMIT 8"""), {"v": _ver}).fetchall())
        # A-158: contar so a geracao corrente e certo, mas zero por CARIMBO e
        # indistinguivel de zero por AUSENCIA DE EMPRESA -- e so o primeiro tem
        # conserto. Em 31/08/2026 a versao subiu para 0.7.2 por uma correcao
        # que so tocou o painel PIT; a vitrine transversal, cujos numeros a
        # correcao nao muda, seguiu carimbada 0.7.1, e a abrangencia americana
        # exibiu 0 de 0 com as 2.626 empresas publicadas. O zero continua zero:
        # a nota nao inventa cobertura, so nomeia o que precisa ser republicado.
        outras = tuple(conn.execute(text("""
            SELECT score_version, count(*) FROM market_us.company_snapshots
            GROUP BY score_version ORDER BY count(*) DESC LIMIT 3""")).fetchall()
        ) if not nominal else ()
    notas = [f"gate: score_status = decision_grade na versao {_ver}"]
    if outras:
        publicado = ", ".join(f"{v} ({n})" for v, n in outras)
        notas.append(f"nenhuma linha publicada na versao {_ver}; a vitrine "
                     f"tem {publicado} -- republique o snapshot")
    return Universo(
        modulo="Empresas Americanas",
        nominal=nominal, investivel=nominal, apto=apto,
        exemplos_descartados=ruins,
        notas=tuple(notas),
    )


def todos(engine=None) -> list[Universo]:
    """Os tres universos de mercado. Uma fonte fora do ar vira universo vazio
    com nota, nunca excecao: o relatorio de confianca precisa poder dizer
    "nao consegui medir" em vez de nao existir."""
    saida: list[Universo] = []
    for nome, fn in (("Empresas B3", universo_b3),
                     ("Selecao de FIIs", universo_fii),
                     ("Empresas Americanas", universo_us)):
        try:
            saida.append(fn(engine))
        except Exception as exc:  # noqa: BLE001 - relatorio nao pode quebrar
            logger.warning("universo %s indisponivel: %s", nome, exc)
            saida.append(Universo(
                nome, 0, 0, 0,
                notas=(f"fonte indisponivel: {type(exc).__name__}",)))
    return saida


def tickers_aptos_b3(engine=None) -> tuple[frozenset[str], Universo]:
    """Tickers B3 que sustentam recomendacao, para uso COMO FILTRO nas telas.

    Devolve tambem o ``Universo`` para que a tela declare o que descartou:
    filtro silencioso e pior que filtro nenhum - o usuario deixa de ver o
    ativo e nao sabe que deixou.

    Conjunto vazio significa "nao aplique o filtro" (fonte indisponivel ou
    universo remanescente abaixo do piso), nunca "descarte tudo".
    """
    u = universo_b3(engine)
    if not u.descarta:
        return frozenset(), u
    from core.data_confidence import LIMIAR_MEDIA, compute_confidence
    scored = compute_confidence(engine)
    aptos = frozenset(
        str(s["ticker"]).upper() for s in scored
        if s.get("dias_preco") is not None
        and float(s.get("score") or 0) >= LIMIAR_MEDIA)
    return aptos, u
