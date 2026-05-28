"""
core/survivorship_prices.py — reconstrução de preços históricos pós-delisting.

Implementa a recomendação C3cc+ do parecer da banca examinadora:
durante backtests survivorship-free, tickers delisted precisam de série
de preços que cubra o período entre o ÚLTIMO PREÇO conhecido e o evento
de delisting — caso contrário o backtest "ignora" o ativo e perde
exatamente o sinal de stress (queda terminal típica de RJ/falência).

Estratégias suportadas:

  • LINEAR_DECAY: do ultimo_preco até `valor_residual` ao longo de
    `decay_months`. Default 12 meses até 30% do último preço — heurística
    Altman & Hotchkiss (2006) "Corporate Financial Distress".

  • STEP_DROP: mantém ultimo_preco até a data de delisting, então DROPA
    para `valor_residual` no D+1. Útil quando o evento é abrupto
    (Joesley Day → trading halt → OPA forçada).

  • RECOVERY_OPA: assume OPA com prêmio (default +20% sobre último preço)
    no D-1 do delisting, depois delistagem ao preço da OPA. Apropriado
    para fechamentos de capital amigáveis (Cielo, Elektro).

Output: pd.Series com índice DatetimeIndex e valores em R$, pronto pra
concat com o DataFrame `df_precos` usado em `_simular_backtest`.

Sem dependência externa além de pandas/numpy.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Literal

import numpy as np
import pandas as pd

from core.survivorship import DelistedTicker

# ──────────────────────────────────────────────────────────────────────────
# Defaults calibrados empiricamente
# ──────────────────────────────────────────────────────────────────────────

# Fração do último preço que sobra ao final do decay (proxy de valor de
# liquidação ou valor residual pós-RJ). Empírico Altman:
#   - Recovery rate médio Brasil ~25-35% para credor júnior/equity
#   - Em RJ homologada com plano: ~10-20%
#   - Em falência: ~0-5%
RECOVERY_BY_MOTIVO: dict[str, float] = {
    "falencia":            0.00,    # Inepar, OGXP3 → essencialmente zero
    "rj_homologada":       0.15,    # Oi → ~15-20% pré-RJ
    "recuperacao_judicial": 0.20,
    "opa":                 1.20,    # OPA paga prêmio sobre último preço
    "fechamento_capital":  1.10,    # OPA básica
    "fusao":               1.05,    # Conversão em ações da incorporadora
    "default":             0.30,    # caso desconhecido — conservador
}

DECAY_MONTHS_DEFAULT = 12  # janela típica de RJ → delisting


@dataclass(frozen=True)
class SurvivorshipPriceConfig:
    """Configuração do método de reconstrução de preços."""
    strategy:        Literal["linear_decay", "step_drop", "recovery_opa"] = "linear_decay"
    decay_months:    int = DECAY_MONTHS_DEFAULT
    valor_residual:  float | None = None   # None = usa RECOVERY_BY_MOTIVO
    opa_premium:     float = 0.20          # +20% sobre último preço (recovery_opa)


# ──────────────────────────────────────────────────────────────────────────
# Reconstrução
# ──────────────────────────────────────────────────────────────────────────

def _residual_for(delisted: DelistedTicker, cfg: SurvivorshipPriceConfig) -> float:
    """Retorna preço residual final dado a estratégia e o motivo."""
    if cfg.valor_residual is not None:
        return float(cfg.valor_residual)
    multiplicador = RECOVERY_BY_MOTIVO.get(delisted.motivo,
                                             RECOVERY_BY_MOTIVO["default"])
    return float(delisted.ultimo_preco) * multiplicador


def reconstruct_price_series(
    delisted:       DelistedTicker,
    date_index:     pd.DatetimeIndex,
    last_known_price: float | None = None,
    last_known_date:  date | None = None,
    cfg:            SurvivorshipPriceConfig | None = None,
) -> pd.Series:
    """Reconstrói série de preços para um ticker delisted.

    Args:
      delisted:        DelistedTicker com data_delisting + ultimo_preco
      date_index:      DatetimeIndex de saída (geralmente um trading calendar)
      last_known_price: último preço observado em yfinance/B3 (ou
                       delisted.ultimo_preco se None)
      last_known_date: data do último preço observado (ou None = início
                       do date_index)
      cfg:             config de estratégia (default linear_decay)

    Returns:
      pd.Series alinhada com date_index. NaN antes de last_known_date e
      após data_delisting + (margem de 30 dias para liquidação).
    """
    if cfg is None:
        cfg = SurvivorshipPriceConfig()
    if not isinstance(date_index, pd.DatetimeIndex):
        date_index = pd.DatetimeIndex(date_index)

    last_price = float(last_known_price if last_known_price is not None
                        else delisted.ultimo_preco)
    if last_known_date is None:
        last_known_date = date_index.min().date()
    delist_dt = pd.Timestamp(delisted.data_delisting)
    last_dt = pd.Timestamp(last_known_date)

    residual = _residual_for(delisted, cfg)

    serie = pd.Series(np.nan, index=date_index, dtype=float)
    # Período de preço "vivo" (último preço observado)
    serie.loc[(date_index >= last_dt) & (date_index < delist_dt)] = last_price

    if cfg.strategy == "step_drop":
        # Mantém ultimo_preco até delist_dt e dropa pra residual após
        serie.loc[date_index == delist_dt] = residual
        # NaN após o evento

    elif cfg.strategy == "recovery_opa":
        # Sobe pra (1 + opa_premium) × last_price no D-1, fixa nesse valor
        opa_price = last_price * (1.0 + cfg.opa_premium)
        # Substitui últimas N=5 obs antes do delisting pelo "anúncio gradual"
        pre_window = (date_index >= delist_dt - pd.Timedelta(days=10)) & \
                     (date_index < delist_dt)
        serie.loc[pre_window] = opa_price
        serie.loc[date_index == delist_dt] = opa_price

    else:  # linear_decay (default)
        # Do (delist_dt - decay_months) até delist_dt, decai de last_price
        # para residual linearmente; após delist_dt, NaN
        decay_start = delist_dt - pd.DateOffset(months=cfg.decay_months)
        if decay_start < last_dt:
            decay_start = last_dt
        decay_mask = (date_index >= decay_start) & (date_index <= delist_dt)
        n_decay = int(decay_mask.sum())
        if n_decay >= 2:
            # Interpolação linear entre last_price e residual
            ramp = np.linspace(last_price, residual, n_decay)
            serie.loc[decay_mask] = ramp
        else:
            serie.loc[date_index == delist_dt] = residual

    return serie


def augment_prices_with_delisted(
    df_precos:       pd.DataFrame,
    delisted_pool:   Iterable[DelistedTicker],
    cfg:             SurvivorshipPriceConfig | None = None,
) -> pd.DataFrame:
    """Adiciona colunas ao df_precos para cada ticker delisted relevante.

    Para cada delisted em delisted_pool cuja data_delisting cai DENTRO do
    range de df_precos.index, adiciona uma coluna com a série reconstruída.
    Se o ticker já existe em df_precos.columns, mescla preservando os
    valores existentes (fillna pelo reconstruído).

    Returns:
      Novo DataFrame (não modifica o input) com colunas adicionais.
    """
    if cfg is None:
        cfg = SurvivorshipPriceConfig()
    out = df_precos.copy()
    if out.empty:
        return out
    idx_min = out.index.min()
    idx_max = out.index.max()

    for delisted in delisted_pool:
        delist_dt = pd.Timestamp(delisted.data_delisting)
        # Só inclui se o delisting está dentro da janela ou imediatamente após
        if delist_dt < idx_min or delist_dt > idx_max + pd.Timedelta(days=30):
            continue
        existing = out.get(delisted.ticker)
        if existing is not None and existing.dropna().size > 5:
            # Já temos histórico real — usa último preço observado real
            last_real = existing.dropna()
            last_known_price = float(last_real.iloc[-1])
            last_known_date = last_real.index[-1].date()
        else:
            last_known_price = float(delisted.ultimo_preco)
            last_known_date = idx_min.date()
        reconstructed = reconstruct_price_series(
            delisted, out.index, last_known_price, last_known_date, cfg,
        )
        if existing is None:
            out[delisted.ticker] = reconstructed
        else:
            out[delisted.ticker] = existing.combine_first(reconstructed)
    return out


def summary_reconstruction(
    delisted_pool:  Iterable[DelistedTicker],
    cfg:            SurvivorshipPriceConfig | None = None,
) -> pd.DataFrame:
    """Tabela com preços reconstruídos finais (para auditoria)."""
    if cfg is None:
        cfg = SurvivorshipPriceConfig()
    rows = []
    for d in delisted_pool:
        residual = _residual_for(d, cfg)
        loss_pct = (residual - d.ultimo_preco) / d.ultimo_preco if d.ultimo_preco > 0 else 0.0
        rows.append({
            "ticker":        d.ticker,
            "data_delisting": d.data_delisting,
            "motivo":        d.motivo,
            "ultimo_preco":  d.ultimo_preco,
            "residual":      round(residual, 2),
            "variacao_pct":  round(loss_pct * 100, 1),
            "strategy":      cfg.strategy,
        })
    return pd.DataFrame(rows).sort_values("data_delisting")
