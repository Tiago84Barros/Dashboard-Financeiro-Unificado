"""
core/transaction_costs.py — modelo de custos de transação por classe de ativo.

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

Fase 3b (2026-08-16) — custo por classe
────────────────────────────────────────
Os parâmetros acima foram calibrados só para pessoa física operando B3. A
carteira global soma 46 ativos B3, 11 FII e 12 EUA (ver
docs/superpowers/plans/2026-08-16-fase-3b-motor-de-movimentacao.md), e nem a
heurística de large cap (`_LARGE_CAP_PREFIXES`, uma lista de tickers do IBOV)
nem a isenção mensal de vendas (`ISENCAO_MES_VENDAS_DEF`, regra da Receita
Federal para PF no Brasil) valem para FII ou ativo estrangeiro.

Não inventamos alíquota, isenção nem spread para essas classes — um número
plausível e errado aqui é pior do que a ausência dele, porque alimenta
recomendação que parece precisa e não é. Em vez disso, `CostConfig` ganhou:

  • `classe`: a que classe de ativo a config se refere (`"b3"`, `"us"`,
    `"fii"`, ...). Default `"b3"` — preserva o comportamento de sempre.
  • `calibrado`: `False` quando os campos numéricos são sentinela
    (`math.nan`), porque ninguém calibrou custo para aquela classe ainda.
    Default `True` — todo caller existente continua vendo números reais.

`custo_por_classe(classe, mapa)` resolve a config pela classe; classe ausente
do mapa cai em `CostConfig.nao_calibrado(classe)`. O motor de recomendação
(Fase 3b Task 4) e o painel (Task 6) devem checar `calibrado` e exibir "não
calibrado" em vez do número quando for `False`.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass


# ──────────────────────────────────────────────────────────────────────────
# Parâmetros padrão — calibrados para mercado brasileiro PF mainstream
# ──────────────────────────────────────────────────────────────────────────

CORRETAGEM_FIXA_DEF      = 0.0      # R$ por ordem — corretoras zero hoje
SPREAD_BPS_LARGE_CAP_DEF = 10.0     # 0.10% — ações líquidas (IBOV top 50)
SPREAD_BPS_SMALL_CAP_DEF = 30.0     # 0.30% — small caps tendem a 20-50 bps
IR_RV_LONG_DEF           = 0.15     # 15% sobre lucros (RV swing/long)
ISENCAO_MES_VENDAS_DEF   = 20_000.0  # R$ — isenção PF até esse volume mensal

# Classes de ativo reconhecidas pela carteira global (Fase 3b).
CLASSE_B3  = "b3"
CLASSE_US  = "us"
CLASSE_FII = "fii"

# Lista de tickers considerados "large cap" para spread baixo
# (heurística simples — em produção usar CVM/B3 free float ranking)
# Vale só para classe == CLASSE_B3 — ver is_large_cap.
_LARGE_CAP_PREFIXES = (
    "PETR", "VALE", "ITUB", "BBDC", "BBAS", "B3SA", "MGLU", "WEGE",
    "ABEV", "ELET", "RENT", "RDOR", "RADL", "UGPA", "GGBR", "USIM",
    "EMBR", "CSAN", "JBSS", "BBSE", "SUZB", "BRFS", "BRKM", "CMIG",
    "EQTL", "TIMS", "VIVT", "TOTS", "LREN", "VBBR", "PRIO",
)


def is_large_cap(ticker: str, classe: str = CLASSE_B3) -> bool:
    """Heurística: ticker começa com prefixo de empresa do IBOV core.

    A lista `_LARGE_CAP_PREFIXES` foi levantada para a B3 e só vale para
    `classe == CLASSE_B3` — é por isso que o parâmetro tem esse default,
    que preserva toda chamada existente com um único argumento
    (`is_large_cap(ticker)`, ex.: `core/allocation_calibration.py`).

    Para qualquer outra classe não existe lista calibrada. Devolver `False`
    aqui por "o ticker não bateu nenhum prefixo" seria repetir o bug que
    motivou esta função virar class-aware: `ADBE`, `TJX`, `PGR` caindo em
    small cap por acidente de não casar um prefixo do IBOV. Por isso, fora
    de `b3`, o `False` é devolvido de forma deliberada e documentada — quem
    monta o `CostConfig` daquela classe precisa calibrar
    `spread_bps_large`/`spread_bps_small` com o MESMO valor explícito, para
    que a escolha entre os dois nunca importe. Enquanto isso não acontece,
    `CostConfig.nao_calibrado` garante que o custo resultante seja NaN, não
    um número que parece apurado.
    """
    if classe != CLASSE_B3:
        return False
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
      classe: classe de ativo a que esta config se refere (`CLASSE_B3`,
        `CLASSE_US`, `CLASSE_FII`, ...). Default `CLASSE_B3` — preserva o
        comportamento de sempre para quem não passa esse campo. É o que
        `custo_compra`/`custo_venda` repassam para `is_large_cap`.
      calibrado: `False` quando os campos numéricos acima são sentinela
        (`math.nan`) porque a classe ainda não foi calibrada — ver
        `nao_calibrado`. Default `True`: todo caller existente continua
        vendo números reais, sem mudança de comportamento.
    """
    corretagem_fixa:   float = CORRETAGEM_FIXA_DEF
    spread_bps_large:  float = SPREAD_BPS_LARGE_CAP_DEF
    spread_bps_small:  float = SPREAD_BPS_SMALL_CAP_DEF
    ir_rate:           float = IR_RV_LONG_DEF
    isencao_mes:       float = ISENCAO_MES_VENDAS_DEF
    ativo:             bool  = True
    classe:            str   = CLASSE_B3
    calibrado:         bool  = True

    @classmethod
    def desligado(cls) -> "CostConfig":
        """Retorna config 'sem custos' para backtest ideal (compatibilidade)."""
        return cls(ativo=False)

    @classmethod
    def brasil_pf_default(cls) -> "CostConfig":
        """Configuração default para pessoa física no Brasil (2026)."""
        return cls(ativo=True)

    @classmethod
    def nao_calibrado(cls, classe: str) -> "CostConfig":
        """Config para uma classe sem parâmetros calibrados ainda.

        Todos os campos numéricos viram `math.nan` de propósito: um custo
        calculado a partir daqui também sai NaN — erra de forma ruidosa
        (`NaN` em qualquer soma, comparação ou formatação) em vez de parecer
        um número apurado que na verdade foi inventado. `ativo=True` para
        que `custo_compra`/`custo_venda` de fato tentem o cálculo; se fosse
        `False`, o resultado seria `0.0`, que é exatamente a aparência de
        precisão (e a subestimação silenciosa) que este método existe para
        evitar.
        """
        nan = math.nan
        return cls(
            corretagem_fixa=nan,
            spread_bps_large=nan,
            spread_bps_small=nan,
            ir_rate=nan,
            isencao_mes=nan,
            ativo=True,
            classe=classe,
            calibrado=False,
        )


def custo_por_classe(classe: str, mapa: Mapping[str, CostConfig] | None) -> CostConfig:
    """Resolve a `CostConfig` de `classe` a partir de `mapa`.

    `mapa` é fornecido por quem chama (este módulo não faz I/O — nada de ler
    calibração de disco ou rede aqui). Classe ausente do mapa, ou mapa
    `None`/vazio, devolve `CostConfig.nao_calibrado(classe)`: preferimos
    declarar "não calibrado" a inventar um custo plausível para uma classe
    sem parâmetros definidos.
    """
    cfg = mapa.get(classe) if mapa else None
    if cfg is None:
        return CostConfig.nao_calibrado(classe)
    return cfg


# ──────────────────────────────────────────────────────────────────────────
# Cálculo de custos
# ──────────────────────────────────────────────────────────────────────────

def custo_compra(ticker: str, valor_bruto: float, cfg: CostConfig) -> float:
    """
    Retorna o custo de uma ordem de compra (corretagem + meia-spread).
    O valor pago efetivo é valor_bruto + custo_compra.

    Se `cfg.calibrado` for False, o retorno é `math.nan` — não há spread
    calibrado para essa classe, e um número aqui seria inventado.
    """
    if not cfg.ativo or valor_bruto <= 0:
        return 0.0
    if not cfg.calibrado:
        return math.nan
    spread_bps = cfg.spread_bps_large if is_large_cap(ticker, cfg.classe) else cfg.spread_bps_small
    return cfg.corretagem_fixa + valor_bruto * (spread_bps / 2.0 / 10_000.0)


def custo_venda(ticker: str, valor_bruto: float, lucro: float,
                vendas_mes_acumulado: float, cfg: CostConfig
                ) -> tuple[float, float]:
    """
    Retorna (custo_total_ordem, ir_devido).
    Aplica corretagem, meia-spread e IR 15% sobre lucro proporcional
    ao que excede a isenção mensal de R$ 20k.

    Se `cfg.calibrado` for False, ambos os componentes voltam `math.nan`.
    Isso é explícito de propósito: uma comparação ingênua contra
    `cfg.isencao_mes` (que também seria NaN) silenciosamente sempre dá
    False em Python, o que faria o IR "vazar" como 0.0 — um número que
    parece dizer "isento" quando na verdade é "desconhecido". O guard
    abaixo evita esse vazamento.
    """
    if not cfg.ativo or valor_bruto <= 0:
        return 0.0, 0.0
    if not cfg.calibrado:
        return math.nan, math.nan
    spread_bps = cfg.spread_bps_large if is_large_cap(ticker, cfg.classe) else cfg.spread_bps_small
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

    Se `cfg.calibrado` for False, o retorno é `math.nan` (mesma razão de
    `custo_compra`/`custo_venda`: sem parâmetro calibrado, sem número).
    """
    if not cfg.ativo:
        return 0.0
    if not cfg.calibrado:
        return math.nan
    # Assume mix médio 70% large + 30% small cap
    spread_mix = 0.70 * cfg.spread_bps_large + 0.30 * cfg.spread_bps_small
    round_trip_cost = 2.0 * rotation_pct_aa * spread_mix
    # IR estimado: assume 12% rentab realizada/ano e 80% acima isenção
    ir_est = 1200 * cfg.ir_rate * 0.80
    return round_trip_cost + ir_est
