"""
core/transaction_costs.py — modelo de custos de transação brasileiro.

Implementa a recomendação C2 do parecer da banca examinadora (2026-05-23):
backtest tinha custo zero, subestimando fricção em 50-150 bps/ano para
portfólios pequenos com alta rotação.

Componentes modelados:
  • Corretagem fixa (default 0 — corretoras zero hoje, mas configurável)
  • Spread bid-ask aproximado (10 bps default para large caps, 30 bps small)
  • IR 15% sobre lucros em vendas (isenção PF até R$ 20k de vendas/mês)
  • Tributação ignorada para day-trade (não aplicável a este simulador)

Referências:
  - Frazzini, Israel & Moskowitz (2018) "Trading Costs"
  - Receita Federal IN nº 1.585/2015 (tributação RV)
  - CMN 4.553/2017 (mercado de capitais)
"""
from __future__ import annotations

from dataclasses import dataclass


# ──────────────────────────────────────────────────────────────────────────
# Parâmetros padrão — calibrados para mercado brasileiro PF mainstream
# ──────────────────────────────────────────────────────────────────────────

CORRETAGEM_FIXA_DEF      = 0.0      # R$ por ordem — corretoras zero hoje
SPREAD_BPS_LARGE_CAP_DEF = 10.0     # 0.10% — ações líquidas (IBOV top 50)
SPREAD_BPS_SMALL_CAP_DEF = 30.0     # 0.30% — small caps tendem a 20-50 bps
IR_RV_LONG_DEF           = 0.15     # 15% sobre lucros (RV swing/long)
ISENCAO_MES_VENDAS_DEF   = 20_000.0  # R$ — isenção PF até esse volume mensal

# Lista de tickers considerados "large cap" para spread baixo
# (heurística simples — em produção usar CVM/B3 free float ranking)
_LARGE_CAP_PREFIXES = (
    "PETR", "VALE", "ITUB", "BBDC", "BBAS", "B3SA", "MGLU", "WEGE",
    "ABEV", "ELET", "RENT", "RDOR", "RADL", "UGPA", "GGBR", "USIM",
    "EMBR", "CSAN", "JBSS", "BBSE", "SUZB", "BRFS", "BRKM", "CMIG",
    "EQTL", "TIMS", "VIVT", "TOTS", "LREN", "VBBR", "PRIO",
)


def is_large_cap(ticker: str) -> bool:
    """Heurística: ticker começa com prefixo de empresa do IBOV core."""
    t = (ticker or "").upper().strip()
    return any(t.startswith(p) for p in _LARGE_CAP_PREFIXES)


# ──────────────────────────────────────────────────────────────────────────
# Estrutura de configuração
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class CostConfig:
    """Configuração de custos para backtest/calibração.

    Atributos:
      corretagem_fixa: R$ por ordem (compra ou venda). Default 0.
      spread_bps_large: spread bid-ask em bps para large caps. Default 10.
      spread_bps_small: spread bid-ask em bps para small caps. Default 30.
      ir_rate: alíquota de IR sobre lucros de venda. Default 0.15.
      isencao_mes: isenção mensal PF de vendas. Default R$ 20.000.
      ativo: True para aplicar custos; False para backtest "ideal" (legado).
    """
    corretagem_fixa:   float = CORRETAGEM_FIXA_DEF
    spread_bps_large:  float = SPREAD_BPS_LARGE_CAP_DEF
    spread_bps_small:  float = SPREAD_BPS_SMALL_CAP_DEF
    ir_rate:           float = IR_RV_LONG_DEF
    isencao_mes:       float = ISENCAO_MES_VENDAS_DEF
    ativo:             bool  = True

    @classmethod
    def desligado(cls) -> "CostConfig":
        """Retorna config 'sem custos' para backtest ideal (compatibilidade)."""
        return cls(ativo=False)

    @classmethod
    def brasil_pf_default(cls) -> "CostConfig":
        """Configuração default para pessoa física no Brasil (2026)."""
        return cls(ativo=True)


# ──────────────────────────────────────────────────────────────────────────
# Cálculo de custos
# ──────────────────────────────────────────────────────────────────────────

def custo_compra(ticker: str, valor_bruto: float, cfg: CostConfig) -> float:
    """
    Retorna o custo de uma ordem de compra (corretagem + meia-spread).
    O valor pago efetivo é valor_bruto + custo_compra.
    """
    if not cfg.ativo or valor_bruto <= 0:
        return 0.0
    spread_bps = cfg.spread_bps_large if is_large_cap(ticker) else cfg.spread_bps_small
    return cfg.corretagem_fixa + valor_bruto * (spread_bps / 2.0 / 10_000.0)


def custo_venda(ticker: str, valor_bruto: float, lucro: float,
                vendas_mes_acumulado: float, cfg: CostConfig
                ) -> tuple[float, float]:
    """
    Retorna (custo_total_ordem, ir_devido).
    Aplica corretagem, meia-spread e IR 15% sobre lucro proporcional
    ao que excede a isenção mensal de R$ 20k.
    """
    if not cfg.ativo or valor_bruto <= 0:
        return 0.0, 0.0
    spread_bps = cfg.spread_bps_large if is_large_cap(ticker) else cfg.spread_bps_small
    fee = cfg.corretagem_fixa + valor_bruto * (spread_bps / 2.0 / 10_000.0)

    # IR só incide se vendas do mês excedem isenção E houve lucro positivo
    ir = 0.0
    if lucro > 0:
        novas_vendas = vendas_mes_acumulado + valor_bruto
        if novas_vendas > cfg.isencao_mes:
            # Fração tributável proporcional ao excedente
            excesso = min(valor_bruto, novas_vendas - cfg.isencao_mes)
            frac_tributavel = excesso / valor_bruto
            ir = lucro * frac_tributavel * cfg.ir_rate

    return fee, ir


def overhead_anual_estimado(
    cfg: CostConfig,
    rotation_pct_aa: float = 0.40,
    holding_typical_months: int = 12,
) -> float:
    """
    Estima overhead anual em bps para portfolio com rotação dada.
    Útil para ajustar CAGR no backtest sem rastrear cada operação.

    Modelo simplificado:
      overhead = 2 × rotation × (spread/2)   [round-trip por ano]
              + IR × rentabilidade_realizada
    """
    if not cfg.ativo:
        return 0.0
    # Assume mix médio 70% large + 30% small cap
    spread_mix = 0.70 * cfg.spread_bps_large + 0.30 * cfg.spread_bps_small
    round_trip_cost = 2.0 * rotation_pct_aa * spread_mix
    # IR estimado: assume 12% rentab realizada/ano e 80% acima isenção
    ir_est = 1200 * cfg.ir_rate * 0.80
    return round_trip_cost + ir_est
