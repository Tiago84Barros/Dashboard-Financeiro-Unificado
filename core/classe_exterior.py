"""
core/classe_exterior.py
Ação americana ou ETF? Classe de um ativo negociado em dólar.

O cadastro não responde sozinho. O importador de PDF da Nomad gravava todo
ativo como ``etf`` ("universo Nomad é majoritariamente ETF"), e
``get_or_create_asset`` nunca reescreve a classe de um ativo que já existe:
MELI, AAPL e qualquer ação comprada na Nomad ficaram ``etf`` no banco e
apareciam no Dashboard como "ETF Internacional".

A decisão, em ordem:

1. ticker no universo de ações americanas do arquivo publicado
   (``data/public/valuation_historico.json.gz``, empresas da SEC; ETF não
   entra lá) → ação;
2. ticker de ETF conhecido, ou a palavra ETF/ETN/Fund no nome → ETF;
3. cadastro dizendo ``stock`` → ação (antes da marca de gestora: "The
   Charles Schwab Corporation" e "WisdomTree, Inc." são empresas);
4. marca de gestora no nome (iShares, SPDR, Vanguard...) → ETF;
5. nome com sufixo de empresa (Inc, Corp, Ltd, PLC...) → ação;
6. nenhum sinal → ETF, o comportamento antigo. ``classe_por_evidencia``
   para antes desta regra e devolve ``None``.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

ACAO_EUA = "stock_us"
ETF_EXTERIOR = "etf_intl"

ETFS_CONHECIDOS = frozenset({
    "VOO", "IVV", "SPY", "QQQ", "VTI", "VEA", "IEFA", "BND", "AGG", "SGOV",
    "TFLO", "VT", "VXUS", "VWO", "IEMG", "EFA", "EEM", "SCHD", "VIG", "VYM",
    "DIA", "IWM", "GLD", "IAU", "SLV", "TLT", "IEF", "SHY", "BIL", "SHV",
    "LQD", "HYG", "VNQ", "XLK", "XLF", "XLE", "XLV", "ARKK", "SMH", "SOXX",
    "JEPI", "JEPQ", "QQQM", "SPLG", "VGT", "IBIT", "FBTC", "ETHA",
})

_RE_FUNDO = re.compile(r"\b(ETF|ETN|FUND|INDEX)\b", re.IGNORECASE)
_RE_GESTORA = re.compile(
    r"\b(ISHARES|SPDR|VANGUARD|INVESCO|PROSHARES|WISDOMTREE|DIREXION|"
    r"GLOBAL X|SCHWAB)\b",
    re.IGNORECASE,
)
_RE_EMPRESA = re.compile(
    r"\b(INC|INCORPORATED|CORP|CORPORATION|LTD|LIMITED|PLC|CO|COMPANY|"
    r"HOLDINGS?|GROUP|N\.?V|S\.?A|AG|SE|ADR|ADS)\b\.?",
    re.IGNORECASE,
)

_ARQUIVO = Path(__file__).resolve().parents[1] / "data" / "public" / \
    "valuation_historico.json.gz"


def universo_acoes_eua() -> frozenset[str]:
    """Tickers de ações americanas do arquivo publicado; vazio se ausente."""
    try:
        from core.inteligencia_ativos import arquivo_publicado
        art = arquivo_publicado.ler(str(_ARQUIVO), "valuation") or {}
        return frozenset(str(t).upper() for t in (art.get("eua") or {}))
    except Exception as exc:  # noqa: BLE001 - classificação não pode derrubar a carteira
        logger.warning("Universo de ações EUA indisponível: %s", exc)
        return frozenset()


def classe_por_evidencia(ticker: str | None, nome: str | None,
                         classe_cadastro: str | None,
                         universo: frozenset[str] | set[str] | None = None,
                         ) -> str | None:
    """``stock_us``, ``etf_intl`` ou ``None`` quando nada aponta para um lado.

    ``universo`` existe para teste; sem ele, lê o arquivo publicado.
    """
    t = (ticker or "").strip().upper()
    n = (nome or "").strip()
    if universo is None:
        universo = universo_acoes_eua()
    if t and t in universo:
        return ACAO_EUA
    if t in ETFS_CONHECIDOS or _RE_FUNDO.search(n):
        return ETF_EXTERIOR
    if (classe_cadastro or "").strip().lower() in {"stock", "stock_us"}:
        return ACAO_EUA
    if _RE_GESTORA.search(n):
        return ETF_EXTERIOR
    if n and n.upper() != t and _RE_EMPRESA.search(n):
        return ACAO_EUA
    return None


def classe_ativo_usd(ticker: str | None, nome: str | None,
                     classe_cadastro: str | None,
                     universo: frozenset[str] | set[str] | None = None) -> str:
    """``stock_us`` ou ``etf_intl`` para um ativo em dólar; sem sinal, ETF."""
    return (classe_por_evidencia(ticker, nome, classe_cadastro, universo)
            or ETF_EXTERIOR)
