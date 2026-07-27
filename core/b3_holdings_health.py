"""Saúde das empresas EFETIVAMENTE selecionadas + concentração de ciclo.

Por que este módulo existe. A carteira B3 tinha gates de segmento (margem vs
Selic, resiliência de ROIC dos líderes históricos) e um gate de entrada por
score, mas **nenhuma verificação no nível da empresa escolhida** sobre
endividamento, sustentabilidade do dividendo ou retorno vs risco-livre. A rota
de valor (``core/b3_value_route.py``) sabia julgar isso, mas era apenas uma
seção de exibição: nunca conversava com a seleção.

O efeito prático apareceu numa carteira real (27/07/2026): uma empresa com
payout de 318%, endividamento de 3,2× e ROIC abaixo da Selic foi aprovada pela
rota de segmentos, enquanto a rota de valor — na mesma tela — a classificava
como armadilha potencial. As duas rotas discordavam e nada avisava o usuário.

Este módulo fecha essa lacuna em duas frentes:

1. **Por empresa** — cruza a selecionada com o gate de solvência da rota de
   valor e com a sustentabilidade do dividendo, devolvendo alertas explícitos.
2. **Por carteira** — mede a concentração em setores cíclicos. Setores
   diferentes podem ser o MESMO fator: bens de capital, mineração, autopeças e
   petroquímica caem juntos num aperto do ciclo industrial global.

Puro (sem banco, sem rede). Coberto por tests/test_b3_holdings_health.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from core.b3_value_route import ValuePolicy, rank_value_opportunities
from core.valuation import dividend_sustainability

VERSION = "b3-holdings-health-1.0.0"

OK = "ok"
ATENCAO = "atencao"
CRITICO = "critico"

# Dois patamares, calibrados contra casos reais. Distribuir um pouco acima do
# lucro acontece em ano de evento extraordinário e é ESTRUTURAL em holding, que
# repassa dividendo da controlada (BRAP3: 120%) — atenção, não veto. Já 1,5×+ o
# lucro não se sustenta por definição (UNIP6: 318%).
PAYOUT_ATENCAO = 1.00
PAYOUT_CRITICO = 1.50

# Taxonomia B3 → sensibilidade ao ciclo. Curada e explícita: a alternativa seria
# inferir de correlação de preços, que numa janela curta confunde regime com
# estrutura. Setores fora do mapa contam como "outros" (não penalizam).
SETORES_CICLICOS = frozenset({
    "materiais básicos", "materiais basicos",
    "bens industriais",
    "consumo cíclico", "consumo ciclico",
    "petróleo, gás e biocombustíveis", "petroleo, gas e biocombustiveis",
})
SETORES_DEFENSIVOS = frozenset({
    "utilidade pública", "utilidade publica",
    "saúde", "saude",
    "consumo não cíclico", "consumo nao ciclico",
})


@dataclass(frozen=True)
class HoldingHealth:
    """Diagnóstico de UMA empresa já selecionada para a carteira."""
    ticker: str
    nivel: str                      # ok | atencao | critico
    alertas: tuple[str, ...] = ()
    classificacao_valor: str = ""   # veredito da rota de valor, se disponível

    @property
    def bloqueante(self) -> bool:
        """Só nível crítico justifica veto automático."""
        return self.nivel == CRITICO

    @property
    def resumo(self) -> str:
        if not self.alertas:
            return "Sem alertas de solvência ou de dividendo."
        return "; ".join(self.alertas)


@dataclass(frozen=True)
class PortfolioHealth:
    """Diagnóstico da carteira como conjunto."""
    holdings: tuple[HoldingHealth, ...] = ()
    n_ativos: int = 0
    pct_ciclico: float = 0.0
    pct_defensivo: float = 0.0
    setores: tuple[str, ...] = ()
    alertas: tuple[str, ...] = field(default_factory=tuple)

    @property
    def criticos(self) -> tuple[HoldingHealth, ...]:
        return tuple(h for h in self.holdings if h.nivel == CRITICO)

    @property
    def atencao(self) -> tuple[HoldingHealth, ...]:
        return tuple(h for h in self.holdings if h.nivel == ATENCAO)


def _num(valor) -> float:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return float("nan")
    return numero


def check_holdings(df_mult: pd.DataFrame, tickers: list[str], *,
                   policy: ValuePolicy | None = None,
                   selic: float | None = None) -> list[HoldingHealth]:
    """Diagnostica as empresas selecionadas cruzando as duas rotas.

    Args:
        df_mult: cross-section de múltiplos (mesmo formato da rota de valor).
        tickers: empresas efetivamente escolhidas pela carteira.
        policy: limites de solvência (padrões da rota de valor).
        selic: risco-livre em fração, para o alerta de ROIC.
    """
    policy = policy or ValuePolicy()
    alvos = [str(t).upper().replace(".SA", "") for t in (tickers or [])]
    if df_mult is None or df_mult.empty or not alvos:
        return []

    avaliadas = rank_value_opportunities(df_mult, policy=policy, selic=selic)
    por_ticker = {str(r["Ticker"]).upper(): r for _, r in avaliadas.iterrows()}
    base = {str(r["Ticker"]).upper(): r
            for _, r in df_mult.iterrows() if "Ticker" in df_mult.columns}

    saida: list[HoldingHealth] = []
    for ticker in alvos:
        linha_valor = por_ticker.get(ticker)
        linha_base = base.get(ticker)
        alertas: list[str] = []
        nivel = OK
        classificacao = str(linha_valor["classificacao"]) if linha_valor is not None else ""

        # 1) Solvência — reaproveita exatamente o gate da rota de valor.
        falhas = list(linha_valor["falhas_solvencia"]) if linha_valor is not None else []
        if falhas:
            nivel = CRITICO
            alertas.append("Solvência: " + "; ".join(falhas))

        # 2) Sustentabilidade do dividendo — motor já existente, nunca usado aqui.
        if linha_base is not None:
            payout = _num(linha_base.get("Payout"))
            dy = _num(linha_base.get("DY"))
            p_fco = _num(linha_base.get("P_FCO"))
            saude = dividend_sustainability(payout, dy, p_fco)
            if payout == payout and payout >= PAYOUT_CRITICO:
                nivel = CRITICO
                alertas.append(
                    f"Dividendo: payout de {payout:.0%} — distribui muito acima "
                    "do lucro, insustentável")
            elif payout == payout and payout >= PAYOUT_ATENCAO:
                nivel = ATENCAO if nivel == OK else nivel
                alertas.append(
                    f"Dividendo: payout de {payout:.0%} acima do lucro — "
                    "verifique se é repasse de holding ou evento extraordinário")
            elif "🔴" in saude.label:
                nivel = CRITICO
                alertas.append(f"Dividendo: {saude.motivo}")
            elif "🟡" in saude.label:
                nivel = ATENCAO if nivel == OK else nivel
                alertas.append(f"Dividendo: {saude.motivo}")

            # 3) ROIC vs risco-livre — atenção, nunca veto: pode ser vale de ciclo.
            roic = _num(linha_base.get("ROIC"))
            if selic is not None and roic == roic and roic < float(selic):
                nivel = ATENCAO if nivel == OK else nivel
                alertas.append(
                    f"Retorno: ROIC de {roic:.1%} abaixo da Selic ({selic:.1%})")

        # 4) Divergência entre as rotas — o sinal que faltava na tela.
        if classificacao == "armadilha_potencial":
            alertas.append("A rota de valor classifica como ARMADILHA POTENCIAL")
        elif classificacao == "sem_evidencia":
            nivel = ATENCAO if nivel == OK else nivel
            faltando = list(linha_valor["criticos_ausentes"])
            alertas.append(
                "Rota de valor não pôde julgar solvência (sem "
                + ", ".join(faltando) + ")")

        saida.append(HoldingHealth(ticker, nivel, tuple(alertas), classificacao))
    return saida


def _classificar_setor(setor: object) -> str:
    texto = str(setor or "").strip().lower()
    if texto in SETORES_CICLICOS:
        return "ciclico"
    if texto in SETORES_DEFENSIVOS:
        return "defensivo"
    return "outros"


def check_portfolio(df_mult: pd.DataFrame, tickers: list[str],
                    setores: dict[str, str] | None = None, *,
                    policy: ValuePolicy | None = None,
                    selic: float | None = None,
                    min_ativos: int = 5,
                    max_pct_ciclico: float = 0.75) -> PortfolioHealth:
    """Diagnóstico da carteira: saúde individual + concentração de ciclo.

    Setores nominalmente distintos podem ser o mesmo fator de risco. Bens
    industriais, mineração, autopeças e petroquímica são quatro setores — e uma
    aposta só no ciclo industrial global.
    """
    holdings = tuple(check_holdings(df_mult, tickers, policy=policy, selic=selic))
    alvos = [h.ticker for h in holdings]
    mapa = {str(k).upper(): v for k, v in (setores or {}).items()}
    classes = [_classificar_setor(mapa.get(t)) for t in alvos]
    total = len(alvos) or 1
    pct_ciclico = classes.count("ciclico") / total
    pct_defensivo = classes.count("defensivo") / total

    alertas: list[str] = []
    if len(alvos) < min_ativos:
        alertas.append(
            f"Concentração: {len(alvos)} ativo(s) — abaixo do mínimo prudente "
            f"de {min_ativos} para diversificar risco específico.")
    if pct_ciclico >= max_pct_ciclico and len(alvos) >= 2:
        alertas.append(
            f"Fator único: {pct_ciclico:.0%} da carteira em setores cíclicos. "
            "Setores diferentes, mesmo fator — tendem a cair juntos num aperto "
            "do ciclo industrial/commodities.")
    if pct_defensivo == 0 and len(alvos) >= 2:
        alertas.append(
            "Sem contrapeso defensivo (utilidade pública, saúde ou consumo "
            "não cíclico) na carteira.")
    setores_unicos = tuple(sorted({str(mapa.get(t) or "—") for t in alvos}))
    if len(setores_unicos) < max(2, len(alvos) // 2) and len(alvos) >= 3:
        alertas.append(
            f"Diversificação setorial baixa: {len(setores_unicos)} setor(es) "
            f"para {len(alvos)} ativos.")

    return PortfolioHealth(holdings, len(alvos), pct_ciclico, pct_defensivo,
                           setores_unicos, tuple(alertas))
