"""Rota de valor para a carteira B3 — distorção de preço com disciplina de solvência.

Por que este módulo existe (auditoria 2026-07, §16): a construção de carteira só
tinha a rota de "habilidade de seleção por segmento", que pergunta *o meu
processo de escolha tem skill comprovado?*. Essa pergunta depende de amplitude
cross-seccional — e a B3 tem **mediana de 3 empresas por segmento**, onde
nenhum teste de ordenação tem poder. O resultado prático era um caminho único e
frequentemente mudo em crises, justamente quando aparecem as distorções.

Esta rota responde a OUTRA pergunta, que não depende de amplitude nenhuma:
*esta empresa está barata frente ao seu valor intrínseco E sobrevive para
realizar esse valor?*

A disciplina está na segunda metade da pergunta. Comprar o que caiu, sem filtro,
é comprar o navio afundando: ~20% das ações brasileiras perderam mais de 90% em
15 anos (Oi, Americanas, Gol). Por isso o módulo separa explicitamente:

* ``oportunidade``        — desconto real + solvência preservada;
* ``armadilha_potencial`` — desconto real MAS solvência comprometida;
* ``sem_margem``          — sólida, porém sem desconto;
* ``sem_evidencia``       — falta dado crítico para decidir.

Ausência de dado NUNCA vira aprovação: sem os insumos críticos, a empresa fica
em ``sem_evidencia`` e não entra na carteira. Módulo puro (sem banco, sem rede),
espelhando ``core/b3_company_score.py``. Coberto por tests/test_b3_value_route.py.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from core.valuation import bazin_margin, graham_margin

ROUTE_VERSION = "b3-value-route-1.0.0"

# Rótulos de classificação (também usados pela interface).
OPORTUNIDADE = "oportunidade"
ARMADILHA = "armadilha_potencial"
SEM_MARGEM = "sem_margem"
SEM_EVIDENCIA = "sem_evidencia"


@dataclass(frozen=True)
class ValuePolicy:
    """Parâmetros da rota. Os padrões são conservadores por decisão de projeto."""

    # Desconto mínimo vs valor intrínseco para a tese existir.
    margem_minima: float = 0.20
    # Yield-alvo de Bazin (fração). 0,06 = exige DY de 6% para preço-teto.
    bazin_yield_alvo: float = 0.06
    # Solvência — qualquer violação joga a empresa em ``armadilha_potencial``.
    max_endividamento: float = 2.5      # dívida/PL
    min_liquidez_corrente: float = 1.0  # ativo circulante / passivo circulante
    # ROIC abaixo do risco-livre NÃO reprova: pode ser vale de ciclo, que é
    # exatamente a tese desta rota. Vira apenas ressalva exibida.
    exigir_roic_positivo: bool = True
    # Nº mínimo de fontes de valuation (Graham, Bazin) para aceitar a margem.
    min_fontes_valuation: int = 1


# Insumos sem os quais não se decide solvência: ausência → ``sem_evidencia``.
_CRITICOS = ("P_FCO", "Margem_Operacional", "Endividamento_Total", "Liquidez_Corrente")


def _num(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)
    values = pd.to_numeric(df[column], errors="coerce")
    return values.replace([np.inf, -np.inf], np.nan)


def _margens_de_valor(df: pd.DataFrame, policy: ValuePolicy) -> pd.DataFrame:
    """Margem de segurança por fonte + consolidação.

    Graham mede desconto sobre lucro/patrimônio; Bazin mede desconto sobre a
    renda distribuída. São teses distintas, então a consolidação é a MÉDIA das
    disponíveis (não o máximo, que seria escolher a fonte mais generosa).
    """
    pl, pvp, dy = _num(df, "P/L"), _num(df, "P/VP"), _num(df, "DY")
    graham = pd.Series(
        [graham_margin(a, b) for a, b in zip(pl, pvp)], index=df.index, dtype=float)
    bazin = pd.Series(
        [bazin_margin(value, policy.bazin_yield_alvo) for value in dy],
        index=df.index, dtype=float)
    fontes = pd.concat([graham, bazin], axis=1)
    return pd.DataFrame({
        "margem_graham": graham,
        "margem_bazin": bazin,
        "margem_valor": fontes.mean(axis=1),
        "fontes_valuation": fontes.notna().sum(axis=1).astype(int),
    })


def _avaliar_solvencia(df: pd.DataFrame, policy: ValuePolicy,
                       selic: float | None) -> pd.DataFrame:
    """Gate de sobrevivência — o que separa distorção de armadilha de valor."""
    p_fco = _num(df, "P_FCO")
    margem_op = _num(df, "Margem_Operacional")
    endividamento = _num(df, "Endividamento_Total")
    liquidez = _num(df, "Liquidez_Corrente")
    roic = _num(df, "ROIC")

    # Cada regra devolve True quando a empresa FALHA nela. NaN não é falha —
    # a ausência é tratada à parte, como falta de evidência.
    falhas = {
        # FCO negativo: a operação queima caixa. Nenhum desconto compensa.
        "FCO negativo": p_fco < 0,
        "margem operacional negativa": margem_op < 0,
        f"endividamento > {policy.max_endividamento:g}x": endividamento > policy.max_endividamento,
        f"liquidez corrente < {policy.min_liquidez_corrente:g}": liquidez < policy.min_liquidez_corrente,
    }
    if policy.exigir_roic_positivo:
        falhas["ROIC negativo (destrói capital)"] = roic < 0

    motivos = pd.Series([[] for _ in range(len(df))], index=df.index, dtype=object)
    for motivo, serie in falhas.items():
        marcados = serie.fillna(False)
        motivos = pd.Series(
            [lista + [motivo] if flag else lista
             for lista, flag in zip(motivos, marcados)],
            index=df.index, dtype=object)

    ausentes = pd.Series(
        [[coluna for coluna in _CRITICOS if pd.isna(_num(df, coluna).iat[i])]
         for i in range(len(df))],
        index=df.index, dtype=object)

    # Ressalva (não reprova): retorno abaixo do risco-livre pode ser vale de
    # ciclo — a hipótese que esta rota existe para capturar.
    ressalvas = pd.Series([[] for _ in range(len(df))], index=df.index, dtype=object)
    if selic is not None and np.isfinite(selic):
        abaixo = (roic < float(selic)).fillna(False)
        ressalvas = pd.Series(
            [lista + [f"ROIC abaixo da Selic ({selic:.1%})"] if flag else lista
             for lista, flag in zip(ressalvas, abaixo)],
            index=df.index, dtype=object)

    return pd.DataFrame({
        "falhas_solvencia": motivos,
        "criticos_ausentes": ausentes,
        "ressalvas": ressalvas,
    })


def _forca_solvencia(df: pd.DataFrame, policy: ValuePolicy) -> pd.Series:
    """0–100 de folga de solvência, para ordenar entre as aprovadas."""
    componentes = []
    liquidez = _num(df, "Liquidez_Corrente")
    componentes.append((liquidez / max(policy.min_liquidez_corrente, .01)).clip(0, 2) / 2)
    endividamento = _num(df, "Endividamento_Total")
    componentes.append(
        (1 - endividamento / max(policy.max_endividamento, .01)).clip(0, 1))
    componentes.append((_num(df, "Margem_Operacional") * 5).clip(0, 1))
    componentes.append((_num(df, "ROIC") * 5).clip(0, 1))
    bloco = pd.concat(componentes, axis=1)
    # Média das dimensões observadas; ausência não conta como zero nem como
    # folga — apenas reduz a base do cálculo (já sinalizada em coverage).
    return (bloco.mean(axis=1) * 100).round(1)


def rank_value_opportunities(df: pd.DataFrame, *,
                             policy: ValuePolicy | None = None,
                             selic: float | None = None) -> pd.DataFrame:
    """Classifica um cross-section pela tese de valor com gate de solvência.

    Args:
        df: cross-section com ``Ticker`` e as colunas canônicas de múltiplos
            (P/L, P/VP, DY, ROIC, Margem_Operacional, Endividamento_Total,
            Liquidez_Corrente, P_FCO). Colunas ausentes viram NaN.
        policy: limites da rota (padrões conservadores).
        selic: taxa livre de risco em fração, só para a ressalva de ROIC.

    Returns:
        Cópia de ``df`` com margens por fonte, ``classificacao``, ``valor_score``
        (0–100, só para ``oportunidade``), motivos e ressalvas — ordenada por
        ``valor_score`` decrescente.
    """
    policy = policy or ValuePolicy()
    colunas = ["Ticker", "margem_graham", "margem_bazin", "margem_valor",
               "fontes_valuation", "classificacao", "valor_score",
               "forca_solvencia", "falhas_solvencia", "criticos_ausentes",
               "ressalvas", "explicacao"]
    if df is None or df.empty or "Ticker" not in df.columns:
        return pd.DataFrame(columns=colunas)

    base = df.copy().reset_index(drop=True)
    margens = _margens_de_valor(base, policy)
    solvencia = _avaliar_solvencia(base, policy, selic)
    resultado = pd.concat([base, margens, solvencia], axis=1)
    resultado["forca_solvencia"] = _forca_solvencia(base, policy)

    classificacoes: list[str] = []
    explicacoes: list[str] = []
    scores: list[float] = []
    for i in range(len(resultado)):
        margem = resultado.at[i, "margem_valor"]
        fontes = int(resultado.at[i, "fontes_valuation"])
        falhas = list(resultado.at[i, "falhas_solvencia"])
        ausentes = list(resultado.at[i, "criticos_ausentes"])
        tem_margem = (fontes >= policy.min_fontes_valuation
                      and pd.notna(margem) and margem >= policy.margem_minima)

        if ausentes:
            classificacoes.append(SEM_EVIDENCIA)
            explicacoes.append(
                "Falta dado crítico para julgar solvência: " + ", ".join(ausentes))
            scores.append(float("nan"))
            continue
        if falhas:
            classificacoes.append(ARMADILHA if tem_margem else SEM_MARGEM)
            desconto = (f"Desconto de {margem:.0%}, mas s" if tem_margem else "S")
            explicacoes.append(f"{desconto}olvência comprometida: " + "; ".join(falhas))
            scores.append(float("nan"))
            continue
        if not tem_margem:
            classificacoes.append(SEM_MARGEM)
            explicacoes.append(
                "Solvência preservada, sem desconto suficiente"
                + (f" (margem {margem:.0%} < {policy.margem_minima:.0%})"
                   if pd.notna(margem) else " (sem fonte de valuation aplicável)"))
            scores.append(float("nan"))
            continue

        classificacoes.append(OPORTUNIDADE)
        # 60% desconto + 40% folga de solvência: a tese é comprar barato o que
        # sobrevive, não o mais barato nem o mais sólido isoladamente.
        desconto_norm = float(np.clip(margem / max(policy.margem_minima * 3, .01), 0, 1))
        folga = float(resultado.at[i, "forca_solvencia"]) / 100.0
        folga = folga if np.isfinite(folga) else 0.5
        scores.append(round(100.0 * (.60 * desconto_norm + .40 * folga), 1))
        ressalva = list(resultado.at[i, "ressalvas"])
        explicacoes.append(
            f"Desconto de {margem:.0%} com solvência preservada"
            + (" — ressalva: " + "; ".join(ressalva) if ressalva else ""))

    resultado["classificacao"] = classificacoes
    resultado["valor_score"] = scores
    resultado["explicacao"] = explicacoes
    resultado["route_version"] = ROUTE_VERSION
    return resultado.sort_values(
        ["valor_score", "margem_valor"], ascending=False, na_position="last"
    ).reset_index(drop=True)


def blocked_by_missing_data(ranked: pd.DataFrame, *,
                            policy: ValuePolicy | None = None) -> pd.DataFrame:
    """Empresas com desconto relevante barradas só por falta de dado crítico.

    Não são oportunidades — nada aqui foi verificado. São a lista do que a
    cobertura de fundamentos está custando: cada linha é uma tese que não pôde
    ser julgada. Serve para priorizar ingestão, não para investir.
    """
    policy = policy or ValuePolicy()
    if ranked is None or ranked.empty or "classificacao" not in ranked.columns:
        return pd.DataFrame(columns=["Ticker", "margem_valor", "criticos_ausentes"])
    sem_evidencia = ranked[ranked["classificacao"] == SEM_EVIDENCIA]
    com_desconto = sem_evidencia[
        sem_evidencia["margem_valor"] >= policy.margem_minima]
    return (com_desconto[["Ticker", "margem_valor", "criticos_ausentes"]]
            .sort_values("margem_valor", ascending=False).reset_index(drop=True))


def route_summary(ranked: pd.DataFrame) -> dict[str, int]:
    """Contagem por classificação — para a interface declarar o funil."""
    if ranked is None or ranked.empty or "classificacao" not in ranked.columns:
        return {OPORTUNIDADE: 0, ARMADILHA: 0, SEM_MARGEM: 0, SEM_EVIDENCIA: 0}
    contagem = ranked["classificacao"].value_counts().to_dict()
    return {chave: int(contagem.get(chave, 0))
            for chave in (OPORTUNIDADE, ARMADILHA, SEM_MARGEM, SEM_EVIDENCIA)}
