# -*- coding: utf-8 -*-
"""Grau de confianca por secao do app, em percentual.

Responde a pergunta que o usuario faz antes de agir: "o quanto eu posso
confiar no que esta nesta tela?". Nao e nota de qualidade de codigo nem
cobertura de teste - e uma medida do DADO que sustenta o que a secao afirma.

Dois eixos, deliberadamente separados porque o usuario age diferente em cada:

``confiabilidade``  o que a tela mostra esta certo? (integridade, frescor,
                    metodologia validada). E isto que "grau de confianca"
                    quer dizer, e leva o peso.
``abrangencia``     quanto do mercado a tela alcanca. Uma secao pode ser
                    altamente confiavel sobre uma fatia pequena. Entra com
                    peso baixo de proposito: 1.111 acoes americanas sustentam
                    carteira mesmo sendo 36% do cadastro, e punir isso como
                    se fosse defeito empurraria para inflar universo com dado
                    ruim - exatamente o oposto do que se quer.

Regra que vale para todo componente: **o que nao foi medido nao vira 100**.
Componente sem medicao sai da media ponderada e declara que saiu. Assumir
perfeicao no que nao se olhou foi o defeito A-124, e ele nao se repete aqui.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone

from core.universo_decisao import Universo, universo_b3, universo_fii, universo_us

logger = logging.getLogger(__name__)

FAIXA_ALTA = 75.0
FAIXA_MEDIA = 55.0

# Secoes que nao fazem afirmacao sobre dado (navegacao, texto, ajuste).
# Declarar "nao se aplica" e diferente de declarar 0%.
SECOES_SEM_AFIRMACAO = ("Documentacao", "Configuracoes")


@dataclass(frozen=True)
class Componente:
    """Uma dimensao medida da confianca. ``pct=None`` significa NAO MEDIDO."""

    nome: str
    pct: float | None
    peso: float
    evidencia: str

    @property
    def medido(self) -> bool:
        return self.pct is not None


@dataclass(frozen=True)
class ConfiancaSecao:
    secao: str
    componentes: tuple[Componente, ...]
    universo: Universo | None = None
    notas: tuple[str, ...] = ()
    aplicavel: bool = True

    @property
    def medidos(self) -> tuple[Componente, ...]:
        return tuple(c for c in self.componentes if c.medido)

    @property
    def nao_medidos(self) -> tuple[Componente, ...]:
        return tuple(c for c in self.componentes if not c.medido)

    @property
    def pct(self) -> float | None:
        """Media ponderada apenas dos componentes MEDIDOS, com os pesos
        renormalizados. ``None`` quando nada pode ser medido - o que e uma
        resposta honesta e diferente de zero."""
        med = self.medidos
        if not med:
            return None
        peso = sum(c.peso for c in med)
        if peso <= 0:
            return None
        return sum((c.pct or 0.0) * c.peso for c in med) / peso

    @property
    def cobertura_da_medicao(self) -> float:
        """Fracao do peso total que foi efetivamente medida. Um 90% apoiado em
        40% do peso nao vale o mesmo que um 90% apoiado em 100%, e esconder
        isso seria a mesma omissao que o modulo tenta evitar."""
        total = sum(c.peso for c in self.componentes)
        if total <= 0:
            return 0.0
        return sum(c.peso for c in self.medidos) / total

    @property
    def faixa(self) -> str:
        p = self.pct
        if p is None:
            return "Nao medido"
        if p >= FAIXA_ALTA:
            return "Alta"
        if p >= FAIXA_MEDIA:
            return "Media"
        return "Baixa"


# ── auxiliares de medicao ────────────────────────────────────────────────────

def _dias_desde(valor) -> int | None:
    if valor is None:
        return None
    if isinstance(valor, datetime):
        ref = valor.date() if valor.tzinfo is None else valor.astimezone(
            timezone.utc).date()
    elif isinstance(valor, date):
        ref = valor
    else:
        return None
    return (date.today() - ref).days


def _frescor_pct(dias: int | None, fresco: int, velho: int) -> float | None:
    """Decaimento linear entre ``fresco`` (100%) e ``velho`` (0%)."""
    if dias is None:
        return None
    if dias <= fresco:
        return 100.0
    if dias >= velho:
        return 0.0
    return 100.0 * (1.0 - (dias - fresco) / (velho - fresco))


def _abrangencia(u: Universo | None) -> Componente:
    if u is None or not u.investivel:
        return Componente("Abrangencia", None, 0.15,
                          "universo nao pode ser medido")
    return Componente(
        "Abrangencia", u.share_apto * 100.0, 0.15,
        f"{u.apto} de {u.investivel} ativos negociaveis sustentam decisao",
    )


def _scalar(conn, sql: str, params: dict | None = None):
    from sqlalchemy import text
    return conn.execute(text(sql), params or {}).scalar()


# ── secoes de mercado ────────────────────────────────────────────────────────

def confianca_b3(engine=None) -> ConfiancaSecao:
    from core.data_confidence import compute_confidence
    comps: list[Componente] = []
    notas: list[str] = []
    u = None
    try:
        u = universo_b3(engine)
    except Exception as exc:  # noqa: BLE001
        notas.append(f"universo indisponivel: {type(exc).__name__}")

    try:
        scored = compute_confidence(engine)
        aptos = [s for s in scored if s.get("dias_preco") is not None]
        if aptos:
            # Integridade: media do pilar de integridade SOBRE OS APTOS. Medir
            # sobre o cadastro inteiro deixaria a nota refem de tickers que a
            # politica ja descartou - o usuario nunca os ve.
            from core.data_confidence import LIMIAR_MEDIA
            bons = [s for s in aptos
                    if float(s.get("score") or 0) >= LIMIAR_MEDIA]
            if bons:
                integ = sum(float(s.get("integridade") or 0)
                            for s in bons) / len(bons)
                comps.append(Componente(
                    "Integridade", integ, 0.35,
                    f"media do pilar de integridade em {len(bons)} tickers aptos"))
                fr = [s.get("dias_preco") for s in bons
                      if s.get("dias_preco") is not None]
                if fr:
                    mediana = sorted(fr)[len(fr) // 2]
                    # Regua 3->30 dias e a do proprio projeto
                    # (data_confidence.price_freshness_factor). Zero aqui nao e
                    # calibracao apertada: e o feed de cotacoes parado, e a
                    # acao correspondente e rodar a ingestao.
                    comps.append(Componente(
                        "Frescor", _frescor_pct(mediana, 3, 30), 0.25,
                        f"preco mediano com {mediana} dias"
                        + (" - ingestao de cotacoes parada" if mediana > 30 else "")))
    except Exception as exc:  # noqa: BLE001
        logger.warning("confianca B3: %s", exc)
        notas.append(f"indice de confianca indisponivel: {type(exc).__name__}")

    comps.append(_abrangencia(u))

    try:
        from core.b3_validation import build_data_manifest, validation_readiness
        from core.database import get_engine
        pronto = validation_readiness(build_data_manifest(engine or get_engine()))
        ok = bool(pronto.get("ready"))
        bloq = pronto.get("blockers") or []
        comps.append(Componente(
            "Metodologia validada", 100.0 if ok else 50.0, 0.25,
            "validacao estrita liberada" if ok
            else f"gate nao-estrito: {'; '.join(bloq)[:90]}"))
    except Exception as exc:  # noqa: BLE001
        comps.append(Componente("Metodologia validada", None, 0.25,
                                f"nao medido: {type(exc).__name__}"))

    return ConfiancaSecao("Empresas B3", tuple(comps), u, tuple(notas))


def confianca_fii(engine=None) -> ConfiancaSecao:
    from core.database import get_engine
    comps: list[Componente] = []
    notas: list[str] = []
    u = None
    try:
        u = universo_fii(engine)
    except Exception as exc:  # noqa: BLE001
        notas.append(f"universo indisponivel: {type(exc).__name__}")

    try:
        eng = engine or get_engine()
        with eng.connect() as conn:
            dias = _dias_desde(_scalar(conn, """
                SELECT max(updated_at) FROM market.fiis WHERE price > 0"""))
            comps.append(Componente(
                "Frescor", _frescor_pct(dias, 3, 30), 0.25,
                (f"cadastro atualizado ha {dias} dias"
                 + (" - ingestao parada" if dias > 30 else ""))
                if dias is not None else "sem carimbo de atualizacao"))
            # Integridade: proventos ja passam pelo filtro renda-vs-capital
            # (A-128) e pelo dedup de eco de classe (A-129). O que resta medir
            # e a fracao de aptos SEM evento de magnitude implausivel (A-132),
            # que segue sinalizado e nao corrigido.
            suspeitos = _scalar(conn, """
                SELECT count(DISTINCT d.ticker)
                FROM market.dividends d JOIN market.fiis f ON f.ticker = d.ticker
                WHERE f.price > 0 AND d.amount > 0.30 * f.price
                  AND upper(d.type) NOT IN ('AMORTIZACAO', 'REST CAP DIN')""")
            if u and u.investivel:
                limpos = max(0, u.investivel - int(suspeitos or 0))
                comps.append(Componente(
                    "Integridade", 100.0 * limpos / u.investivel, 0.35,
                    f"{suspeitos} fundos com provento de magnitude implausivel "
                    f"(A-132, sinalizado)"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("confianca FII: %s", exc)
        notas.append(f"medicao parcial: {type(exc).__name__}")

    comps.append(_abrangencia(u))
    # PIT 6.8 passou no armazem local e nao foi publicada; producao le
    # 'unvalidated'. Errar para o lado conservador e o comportamento correto,
    # mas a nota tem de refletir o que a PRODUCAO ve, nao o que o local sabe.
    comps.append(Componente(
        "Metodologia validada", 50.0, 0.25,
        "certificado 6.8.0 existe no armazem local, producao le 'unvalidated'"))
    return ConfiancaSecao("Selecao de FIIs", tuple(comps), u, tuple(notas))


def confianca_us(engine=None) -> ConfiancaSecao:
    from core.database import get_engine
    comps: list[Componente] = []
    notas: list[str] = []
    u = None
    try:
        u = universo_us(engine)
    except Exception as exc:  # noqa: BLE001
        notas.append(f"universo indisponivel: {type(exc).__name__}")

    try:
        eng = engine or get_engine()
        with eng.connect() as conn:
            # Integridade: EUA-Q mediu zero preco nao-positivo e nenhum score
            # fora de [0,100]; o filing mais antigo em decision_grade e FY2024.
            fora = _scalar(conn, """
                SELECT count(*) FROM market_us.company_snapshots
                WHERE score_status = 'decision_grade'
                  AND (score < 0 OR score > 100)""")
            antigos = _scalar(conn, """
                SELECT count(*) FROM market_us.company_snapshots
                WHERE score_status = 'decision_grade'
                  AND last_fiscal_year < :ano""",
                {"ano": date.today().year - 2})
            apto = (u.apto if u else 0) or 1
            ruins = int(fora or 0) + int(antigos or 0)
            comps.append(Componente(
                "Integridade", 100.0 * max(0, apto - ruins) / apto, 0.35,
                f"{fora} scores fora de faixa, {antigos} filings anteriores a "
                f"{date.today().year - 2}"))
            # Frescor da VITRINE, nao do armazem: o usuario le a vitrine.
            dias = _dias_desde(_scalar(conn, """
                SELECT max(month_end) FROM market_us.prices_monthly"""))
            comps.append(Componente(
                "Frescor", _frescor_pct(dias, 35, 120), 0.25,
                f"ultimo preco mensal ha {dias} dias" if dias is not None
                else "sem serie de preco publicada"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("confianca EUA: %s", exc)
        notas.append(f"medicao parcial: {type(exc).__name__}")

    comps.append(_abrangencia(u))
    comps.append(Componente(
        "Metodologia validada", 50.0, 0.25,
        "vitrine publicada em 03/08/2026, score_version 0.5.0 - "
        "republicacao pendente de autorizacao"))
    notas.append("unico ponto do app em que a tela publicada esta MENOS "
                 "conservadora que o codigo local")
    return ConfiancaSecao("Empresas Americanas", tuple(comps), u, tuple(notas))


def confianca_portfolio_global(engine=None) -> ConfiancaSecao:
    """Global nao tem universo proprio: ele consome os tres adaptadores. Sua
    confiabilidade e limitada pela PIOR das fontes, nao pela media - uma
    carteira consolidada com um lado podre nao fica meio boa."""
    comps: list[Componente] = []
    notas: list[str] = []
    fontes = []
    for fn in (confianca_b3, confianca_fii, confianca_us):
        try:
            fontes.append(fn(engine))
        except Exception as exc:  # noqa: BLE001
            logger.warning("global: fonte indisponivel: %s", exc)
    pcts = [f.pct for f in fontes if f.pct is not None]
    if pcts:
        comps.append(Componente(
            "Fontes consolidadas", min(pcts), 0.45,
            "limitado pela pior fonte: "
            + min(fontes, key=lambda f: f.pct if f.pct is not None else 1e9).secao))
    else:
        comps.append(Componente("Fontes consolidadas", None, 0.45,
                                "nenhuma fonte pode ser medida"))
    try:
        from core.database import get_engine
        eng = engine or get_engine()
        with eng.connect() as conn:
            posicoes = int(_scalar(conn,
                           "SELECT count(*) FROM public.portfolio_positions") or 0)
            dias = _dias_desde(_scalar(conn,
                               "SELECT max(updated_at) FROM public.portfolio_positions"))
        comps.append(Componente(
            "Posicoes registradas", 100.0 if posicoes else 0.0, 0.20,
            f"{posicoes} posicoes na carteira"))
        comps.append(Componente(
            "Frescor", _frescor_pct(dias, 7, 60), 0.20,
            f"carteira atualizada ha {dias} dias" if dias is not None
            else "sem carimbo"))
    except Exception as exc:  # noqa: BLE001
        notas.append(f"carteira nao medida: {type(exc).__name__}")
    # Cobertura de risco: a maquina de Cobertura declara o que fica de fora
    # (A-133), entao o painel nunca apresenta fatia como todo.
    comps.append(Componente(
        "Cobertura de risco declarada", 100.0, 0.15,
        "painel informa peso coberto e motivo por simbolo (A-133)"))
    return ConfiancaSecao("Portfolio Global", tuple(comps), None, tuple(notas))


# ── secoes de controle pessoal ───────────────────────────────────────────────

def _confianca_pessoal(engine, secao: str, tabela: str, col_data: str,
                       col_categoria: str | None) -> ConfiancaSecao:
    """Controle pessoal: o dado e digitado ou importado pelo usuario, entao
    confianca aqui e completude e frescor do REGISTRO, nao qualidade de fonte
    externa. Nao ha o que descartar - toda linha e um fato do usuario."""
    from core.database import get_engine
    comps: list[Componente] = []
    notas: list[str] = []
    try:
        eng = engine or get_engine()
        with eng.connect() as conn:
            n = int(_scalar(conn, f"SELECT count(*) FROM {tabela}") or 0)
            comps.append(Componente(
                "Registro presente", 100.0 if n else 0.0, 0.30,
                f"{n} lancamentos"))
            dias = _dias_desde(_scalar(conn, f"SELECT max({col_data}) FROM {tabela}"))
            comps.append(Componente(
                "Frescor", _frescor_pct(dias, 15, 90), 0.35,
                f"ultimo lancamento ha {dias} dias" if dias is not None
                else "sem lancamento"))
            if col_categoria and n:
                cat = int(_scalar(conn, f"""SELECT count(*) FROM {tabela}
                                            WHERE {col_categoria} IS NOT NULL""") or 0)
                comps.append(Componente(
                    "Classificacao", 100.0 * cat / n, 0.35,
                    f"{cat} de {n} lancamentos categorizados"))
            elif col_categoria:
                comps.append(Componente("Classificacao", None, 0.35,
                                        "sem lancamento para classificar"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("confianca %s: %s", secao, exc)
        notas.append(f"nao medido: {type(exc).__name__}")
    return ConfiancaSecao(secao, tuple(comps), None, tuple(notas))


def confianca_controle_financeiro(engine=None) -> ConfiancaSecao:
    """Duas fontes independentes por decisao de projeto: o lancamento manual
    (fluxo do mes) e o extrato bancario importado. Medir so a primeira daria
    100% com o extrato parado ha meses - a omissao exata que este modulo diz
    evitar. O extrato entra com peso menor porque atualiza-lo e acao do
    usuario, nao do app: extrato velho e informacao sobre o registro, nao
    defeito do sistema."""
    from core.database import get_engine
    base = _confianca_pessoal(engine, "Controle Financeiro",
                              "public.transactions", "due_date", "category_id")
    comps = list(base.componentes)
    notas = list(base.notas)
    try:
        eng = engine or get_engine()
        with eng.connect() as conn:
            n = int(_scalar(conn,
                    "SELECT count(*) FROM bank_statement_movements") or 0)
            if n:
                dias = _dias_desde(_scalar(conn, """
                    SELECT max(data_movimento) FROM bank_statement_movements"""))
                conf = int(_scalar(conn, """
                    SELECT count(*) FROM bank_statement_movements
                    WHERE status_classificacao = 'confirmada'""") or 0)
                comps.append(Componente(
                    "Extrato importado", _frescor_pct(dias, 30, 240), 0.20,
                    f"{n} movimentos, {conf} confirmados, ultimo ha {dias} dias"))
                if dias is not None and dias > 90:
                    notas.append("extrato bancario nao e reimportado ha "
                                 f"{dias} dias - o fluxo manual segue atual")
            else:
                comps.append(Componente(
                    "Extrato importado", None, 0.20,
                    "nenhum extrato importado (fluxo manual e suficiente)"))
    except Exception as exc:  # noqa: BLE001
        comps.append(Componente("Extrato importado", None, 0.20,
                                f"nao medido: {type(exc).__name__}"))
    return ConfiancaSecao("Controle Financeiro", tuple(comps),
                          notas=tuple(notas))


def confianca_investimentos(engine=None) -> ConfiancaSecao:
    return _confianca_pessoal(engine, "Investimentos",
                              "public.investment_transactions",
                              "transaction_date", None)


def confianca_dashboard_geral(engine=None) -> ConfiancaSecao:
    """Dashboard Geral so agrega. Herda o pior entre caixa e investimentos."""
    fontes = [confianca_controle_financeiro(engine),
              confianca_investimentos(engine)]
    pcts = [f.pct for f in fontes if f.pct is not None]
    comp = Componente(
        "Fontes agregadas", min(pcts) if pcts else None, 1.0,
        "limitado pela pior fonte: " + min(
            fontes, key=lambda f: f.pct if f.pct is not None else 1e9).secao
        if pcts else "nenhuma fonte medida")
    return ConfiancaSecao("Dashboard Geral", (comp,))


# ── relatorio ────────────────────────────────────────────────────────────────

_SECOES = (
    ("Dashboard Geral", confianca_dashboard_geral),
    ("Controle Financeiro", confianca_controle_financeiro),
    ("Investimentos", confianca_investimentos),
    ("Empresas B3", confianca_b3),
    ("Empresas Americanas", confianca_us),
    ("Selecao de FIIs", confianca_fii),
    ("Portfolio Global", confianca_portfolio_global),
)


def relatorio(engine=None) -> list[ConfiancaSecao]:
    """Confianca de todas as secoes analiticas, na ordem da barra lateral.
    Uma secao que estoure vira entrada 'Nao medido' com nota, nunca some do
    relatorio: secao ausente parece secao sem problema."""
    saida: list[ConfiancaSecao] = []
    for nome, fn in _SECOES:
        try:
            saida.append(fn(engine))
        except Exception as exc:  # noqa: BLE001
            logger.warning("relatorio: secao %s falhou: %s", nome, exc)
            saida.append(ConfiancaSecao(
                nome, (Componente("Medicao", None, 1.0,
                                  f"falhou: {type(exc).__name__}"),),
                notas=("secao nao pode ser medida nesta execucao",)))
    return saida


def confianca_global(secoes: list[ConfiancaSecao] | None = None,
                     engine=None) -> float | None:
    """Confianca do app inteiro: media das secoes medidas, ponderada pelo peso
    efetivamente medido de cada uma. Secao apoiada em pouca medicao pesa menos
    no numero de manchete."""
    secoes = secoes if secoes is not None else relatorio(engine)
    pares = [(s.pct, s.cobertura_da_medicao) for s in secoes if s.pct is not None]
    if not pares:
        return None
    peso = sum(c for _, c in pares)
    if peso <= 0:
        return None
    return sum(p * c for p, c in pares) / peso
