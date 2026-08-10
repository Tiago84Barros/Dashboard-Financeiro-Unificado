"""Adaptador de snapshot dos FIIs.

A composicao por tipo de ativo (imoveis, papel, caixa, fundos) e guardada em
classification.composition porque e o insumo do look-through da Fase 2.
Coberto por tests/test_portfolio_adapter_fii.py.
"""
from __future__ import annotations

import datetime as dt

from core.portfolio.adapters._frames import indexar, registros
from core.portfolio.models import AssetSnapshot
from core.portfolio.registry import get_spec

SPEC = get_spec("fii")

# Coluna da base (market_read.load_fiis) -> chave no bloco fundamentals.
_FUNDAMENTOS = {
    "Preço": "preco",
    "P/VP": "pvp",
    "DY_12m": "dy_12m",
    "Liquidez_Diaria": "liquidez_diaria",
    "Patrimonio": "patrimonio_liquido",
    "VPA": "vpa",
    "Cotistas": "num_cotistas",
    "Gestao": "tipo_gestao",
}

_COMPOSICAO = {
    "Pct_Imoveis": "pct_imoveis",
    "Pct_Papel": "pct_papel",
    "Pct_Caixa": "pct_caixa",
    "Pct_Fundos": "pct_fundos",
}


# Fundamentos que a propria selecao gravou junto do item. Quando o item os traz,
# eles vencem o valor lido da base agora: no salvamento os dois sao identicos, e
# no backfill o do item e o valor point-in-time correto, enquanto o da base e o
# de hoje. Preferir a base ali trocaria historico verdadeiro por valor atual.
_FUNDAMENTOS_DO_ITEM = ("dy_12m", "pvp")


def _carregar_metricas_mensais(tickers: tuple[str, ...]) -> dict:
    """Serie mensal por FII (market.fii_metrics_monthly).

    load_fii_metrics_mensal e por ticker e nao tem versao em lote (nota do
    plano: nao criar uma so para isto). Sao N consultas, cada uma cacheada
    por uma hora via @st.cache_data — custo aceito, o gancho de captura ja
    roda fora da transacao da carteira.
    """
    from core import market_read
    saida: dict = {}
    for tk in tickers:
        serie = market_read.load_fii_metrics_mensal(tk)
        if serie is not None and not serie.empty:
            saida[tk] = serie
    return saida


def _carregar_proventos(tickers: tuple[str, ...]) -> dict:
    """Proventos anuais por FII: mesma agregacao que load_demonstracoes_batch usa."""
    if not tickers:
        return {}
    from core.market_read import _q
    tks = list(tickers)
    divs = _q(
        "SELECT ticker, EXTRACT(YEAR FROM event_date)::int AS y, SUM(amount) AS d "
        "FROM market.dividends WHERE ticker = ANY(:tks) AND event_date IS NOT NULL "
        "GROUP BY 1, 2",
        {"tks": tks},
    )
    if divs.empty:
        return {}
    saida: dict = {}
    for r in divs.itertuples():
        tk = str(r.ticker).strip().upper()
        saida.setdefault(tk, {})[int(r.y)] = float(r.d)
    return saida


def _default_loaders() -> dict:
    from core import market_read
    return {
        "fiis": lambda: market_read.load_fiis(),
        "metricas_mensais": _carregar_metricas_mensais,
        "proventos": _carregar_proventos,
    }


def _historico_fii(tk: str, metricas: dict, proventos: dict) -> dict:
    """Monta o bloco history a partir dos loaders de serie mensal e proventos.

    FII sem serie ou sem proventos ainda produz um snapshot valido: listas
    vazias, nunca chave ausente.
    """
    serie = metricas.get(tk)
    mensal = registros(serie) if serie is not None else []
    anuais = proventos.get(tk) or {}
    proventos_anuais = [{"ano": ano, "total": total}
                        for ano, total in sorted(anuais.items())]
    return {"metricas_mensais": mensal, "proventos_anuais": proventos_anuais}


def _ticker(item: dict) -> str:
    # Precedência: tk > ticker (alinhado com b3._ticker)
    return str(item.get("tk") or item.get("ticker") or "").strip().upper()


def build_snapshots(items: list[dict], *, model_id: str, params: dict,
                    as_of: dt.date, loaders: dict | None = None) -> list[AssetSnapshot]:
    """Monta um AssetSnapshot por item valido da carteira de FIIs."""
    if loaders is None:
        loaders = _default_loaders()
    validos = [(item, _ticker(item)) for item in items]
    validos = [(item, tk) for item, tk in validos if tk]
    if not validos:
        return []

    base = indexar(loaders["fiis"](), "Ticker")

    tickers = tuple(sorted({tk for _, tk in validos}))
    # Chaves opcionais: fixtures dos testes ja existentes nao as fornecem, e o
    # snapshot degradado (sem serie) e o comportamento correto, nao um erro.
    metricas = loaders.get("metricas_mensais", lambda tks: {})(tickers) or {}
    proventos = loaders.get("proventos", lambda tks: {})(tickers) or {}

    saida: list[AssetSnapshot] = []
    for item, tk in validos:
        linha = base.get(tk) or {}
        fundamentals = {destino: linha[origem]
                        for origem, destino in _FUNDAMENTOS.items() if origem in linha}
        for campo in _FUNDAMENTOS_DO_ITEM:
            if item.get(campo) is not None:
                fundamentals[campo] = item[campo]
        composition = {destino: linha[origem]
                       for origem, destino in _COMPOSICAO.items() if origem in linha}

        saida.append(AssetSnapshot.from_blocks(
            asset_class=SPEC.key,
            model_id=model_id,
            symbol=tk,
            as_of_date=as_of,
            blocks={
                "identity": {
                    "symbol": tk,
                    "name": item.get("nome") or linha.get("Nome") or tk,
                    "asset_class": SPEC.key,
                    "currency": SPEC.currency,
                    "country": SPEC.country,
                    "sector": item.get("segmento") or linha.get("Segmento"),
                    "subsector": None,
                    "segment": linha.get("Tipo"),
                },
                "fundamentals": fundamentals,
                "metrics": {
                    "score": item.get("score") if item.get("score") is not None
                             else linha.get("Score"),
                    "weight": item.get("peso") if item.get("peso") is not None
                              else item.get("weight"),
                },
                "classification": {"composition": composition},
                "history": _historico_fii(tk, metricas, proventos),
                "assumptions": {"params": dict(params or {})},
                "evidence": {},
                "notes": "",
                "provenance": {
                    "source": "selecao_fiis",
                    "as_of_date": as_of.isoformat(),
                    "backfilled": False,
                },
            },
        ))
    return saida
