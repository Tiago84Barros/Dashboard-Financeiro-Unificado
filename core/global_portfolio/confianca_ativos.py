"""Confianca dos dados por ativo, comparavel entre B3, EUA e FII (A-150).

``signals.sinais_qualidade`` sabe ponderar cada sinal de qualidade pela
confianca do dado daquele ativo desde que foi escrito, e documenta isso em
cinco linhas. O unico chamador nunca passou o argumento: todo sinal entrava com
peso pleno, e um ativo com dado ruim carregava a mesma conviccao que um com
dado impecavel. Quarta ocorrencia do mesmo defeito estrutural nesta base --
motor medindo certo, sem ninguem consultando.

O motivo de nao ter sido ligado antes fica claro assim que se mede as fontes:

    fonte                              escala   n      p50 (com dado)
    core.data_confidence.score_ticker  0-100    447    97.2
    market_us.company_snapshots        0-100   2823    70.6
    market.fii_score_snapshots         0-1      428    69.7   (0.697)

Passar isso cru multiplicaria o sinal de FII por ~0.40 e o de B3 por ~0.90.
A diferenca nao mede qualidade de dado: mede que as tres definicoes de
confianca nao sao a mesma regua, e que a coluna de FII vive em 0-1. Alimentar
sem normalizar puniria a classe cuja metrica e mais severa -- o mesmo padrao de
"medicao que pune a evidencia" que ja apareceu na conciliacao bancaria.

A normalizacao e por ANCORA declarada, nao por percentil recalculado a cada
render: ancora movel faz o peso de um ativo mudar porque OUTRO ativo entrou na
carteira, o que e indefensavel num numero que o usuario ve. As constantes abaixo
sao o p75 de cada classe entre os ativos que tem dado, medido em 27/08/2026.
Ativo no p75 ou acima da propria classe entra com conviccao plena; abaixo, e
atenuado na proporcao de quanto esta pior que a norma daquele pipeline.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# p75 por classe entre ativos com confianca > 0, medido em 27/08/2026 contra a
# producao. Revisar quando o pipeline de uma classe mudar de patamar -- a
# constante existe justamente para que essa revisao seja uma decisao visivel, e
# nao um alvo que se desloca sozinho.
ANCORA_POR_CLASSE: dict[str, float] = {"b3": 100.0, "us": 81.1, "fii": 76.6}


def _ancorar(bruto: float | None, classe: str) -> float | None:
    """Confianca 0-100 comparavel entre classes. ``None`` = nao medido."""
    if bruto is None:
        return None
    ancora = ANCORA_POR_CLASSE.get(classe)
    if not ancora:
        return None
    return max(0.0, min(100.0, float(bruto) / ancora * 100.0))


def _simbolos(df_posicoes, classe: str) -> dict[str, str]:
    """{ticker consultavel -> symbol como aparece na carteira}."""
    fora = {}
    for linha in df_posicoes.to_dict(orient="records"):
        if str(linha.get("asset_class") or "").strip().lower() != classe:
            continue
        symbol = str(linha["symbol"])
        fora[symbol.upper().replace(".SA", "")] = symbol
    return fora


def _b3(engine, alvo: dict[str, str]) -> dict[str, float]:
    from core.data_confidence import compute_confidence
    linhas = compute_confidence(engine, tickers=list(alvo))
    return {alvo[t]: v for d in linhas
            if (t := str(d["ticker"]).upper()) in alvo
            and (v := _ancorar(d.get("score"), "b3")) is not None}


def _us(engine, alvo: dict[str, str]) -> dict[str, float]:
    from sqlalchemy import text
    with engine.connect() as conn:
        linhas = conn.execute(text("""
            SELECT symbol, score_confidence FROM market_us.company_snapshots
            WHERE score_confidence IS NOT NULL AND symbol = ANY(:s)
        """), {"s": list(alvo)}).fetchall()
    return {alvo[s.upper()]: v for s, bruto in linhas
            if s.upper() in alvo and (v := _ancorar(bruto, "us")) is not None}


def _fii(engine, alvo: dict[str, str]) -> dict[str, float]:
    # `confidence` vive em 0-1 nesta tabela; x100 antes de ancorar. O snapshot
    # mais recente por ticker, nao o mais antigo: DISTINCT ON com ORDER BY desc.
    from sqlalchemy import text
    with engine.connect() as conn:
        linhas = conn.execute(text("""
            SELECT DISTINCT ON (ticker) ticker, confidence
            FROM market.fii_score_snapshots
            WHERE confidence IS NOT NULL AND ticker = ANY(:s)
            ORDER BY ticker, reference_date DESC
        """), {"s": list(alvo)}).fetchall()
    return {alvo[t.upper()]: v for t, bruto in linhas
            if t.upper() in alvo
            and (v := _ancorar(float(bruto) * 100.0, "fii")) is not None}


_FONTES = {"b3": _b3, "us": _us, "fii": _fii}


def confianca_por_ativo(df_posicoes, engine=None) -> dict[str, float]:
    """{symbol -> confianca 0-100 comparavel} para as posicoes da carteira.

    Devolve ``{}`` -- conviccao plena para todos, que e o comportamento
    anterior -- se QUALQUER classe presente na carteira falhar em produzir. E
    deliberado e e o ponto mais delicado deste modulo: medir so uma parte das
    classes nao e meia melhora, e uma distorcao nova. Se o B3 for ponderado e o
    EUA nao, o motor passa a preferir ativos americanos sistematicamente --
    nao por serem melhores, mas por nao terem sido medidos. Cobertura parcial
    entre classes e pior que cobertura nenhuma.

    Dentro de uma classe, ativo ausente da vitrine simplesmente nao recebe
    entrada e entra com peso pleno: ai a ausencia e por ativo, nao sistematica,
    e vale a regra ja documentada em ``sinais_qualidade``.
    """
    if df_posicoes is None or getattr(df_posicoes, "empty", True):
        return {}
    if engine is None:
        from core.database import get_engine
        engine = get_engine()
    if engine is None:
        return {}

    fora: dict[str, float] = {}
    for classe, fonte in _FONTES.items():
        alvo = _simbolos(df_posicoes, classe)
        if not alvo:
            continue
        try:
            fora.update(fonte(engine, alvo))
        except Exception as exc:  # noqa: BLE001
            logger.warning("confianca_por_ativo: classe %s indisponivel (%s); "
                           "sinal de qualidade segue com peso pleno em TODAS "
                           "as classes", classe, type(exc).__name__)
            return {}
    return fora
