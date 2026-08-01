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

# Falhas OPERACIONAIS: a operação não se sustenta. Bastam por si.
# As demais são ESTRUTURAIS (balanço) — uma geradora de caixa forte pode
# carregá-las sem risco de insolvência (PETR4: liquidez corrente 0,74 com
# margem operacional de 29% e FCO barato). Quem consome deve exigir
# confirmação antes de tratar falha estrutural como crítica.
FALHAS_OPERACIONAIS = ("FCO negativo", "margem operacional negativa",
                       "ROIC negativo")

# Falhas ESTRUTURAIS GRAVES: não são operacionais (não falam da operação), mas
# também não pedem confirmação. Patrimônio líquido negativo é insolvência
# contábil — o passivo excede o ativo; não há segundo sinal a esperar. O mesmo
# vale para dívida/PL fora da faixa coerente (> 20×). Distinguem-se da estrutura
# apenas APERTADA (liquidez < 1, endividamento acima do limite de política), que
# uma geradora de caixa forte carrega sem risco — daí a exigência de confirmação.
FALHAS_ESTRUTURAIS_GRAVES = ("patrimônio líquido negativo",
                             "endividamento fora de faixa")


def is_operational_failure(motivo: str) -> bool:
    """True quando a falha indica operação que não se paga."""
    texto = str(motivo or "")
    return any(texto.startswith(chave) for chave in FALHAS_OPERACIONAIS)


def is_conclusive_failure(motivo: str) -> bool:
    """True quando a falha basta por si — sem precisar de segundo sinal.

    É o predicado que quem decide deve usar. ``is_operational_failure`` cobre
    só metade do conjunto e continua exportado porque distinguir operação de
    balanço ainda importa na hora de explicar ao usuário o que houve.
    """
    texto = str(motivo or "")
    return is_operational_failure(motivo) or any(
        texto.startswith(chave) for chave in FALHAS_ESTRUTURAIS_GRAVES)


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
    # Confirmação de prejuízo para as falhas operacionais que a métrica sozinha
    # não decide (ver "margem operacional negativa" abaixo). ROE é o proxy
    # disponível no cross-section; ausente, não confirma nem absolve.
    roe = _num(df, "ROE")

    # Sinais de balanço/caixa rompido: valores que a faixa coerente rejeita e
    # que por isso NUNCA chegam como número. Sem eles a regra abaixo era letra
    # morta — medido em 30/07/2026, das 3.066 linhas de P_FCO no banco, ZERO
    # eram negativas (mínimo 0,0135), porque a faixa é (0,01, 200). A regra
    # "p_fco < 0" nunca disparou para nenhuma empresa desde que existe, e as
    # 71 que queimam caixa caíam em "sem evidência" por falta justamente de
    # P_FCO. Ver core.data_quality.SIGNAL_RANGES.
    fco_negativo = _num(df, "FCO_Negativo") == 1
    pl_negativo = _num(df, "Patrimonio_Negativo") == 1
    endiv_fora = _num(df, "Endividamento_Fora_De_Faixa") == 1

    # Cada regra devolve True quando a empresa FALHA nela. NaN não é falha —
    # a ausência é tratada à parte, como falta de evidência.
    falhas = {
        # FCO negativo: a operação queima caixa. Nenhum desconto compensa.
        "FCO negativo": (p_fco < 0) | fco_negativo,
        "patrimônio líquido negativo": pl_negativo,
        f"endividamento fora de faixa (> {policy.max_endividamento:g}x)": endiv_fora,
        # Margem operacional negativa exige PREJUÍZO confirmando, pelo mesmo
        # motivo do FCO negativo. EBIT/receita não é conceito válido para
        # instituição financeira — banco não tem "operação" no sentido
        # industrial, e a brapi devolve o quociente mesmo assim. Medido em
        # 01/08/2026: das 81 empresas com margem operacional negativa, 28 são do
        # setor Financeiro (o maior grupo) e 33 têm ROE POSITIVO. Sem a
        # confirmação, Banco do Brasil e Bradesco saíam CRÍTICOS por um número
        # que não se aplica a eles.
        #
        # ROE ausente não confirma nem absolve: sem o segundo sinal a falha não
        # é conclusiva, e a empresa cai na faixa de atenção como qualquer
        # estrutura apertada.
        "margem operacional negativa": (margem_op < 0) & (roe < 0),
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

        # Uma falha CONCLUSIVA vence a ausência de dado. Antes, `ausentes` era
        # avaliado primeiro e engolia o veredito: empresa com patrimônio
        # negativo não tem Endividamento_Total (a razão fica negativa e a faixa
        # rejeita), e empresa que queima caixa não tem P_FCO — os dois são
        # CRÍTICOS, então as piores empresas do universo saíam classificadas
        # como "sem evidência", o estado reservado a quem não tem dado. A
        # ausência aqui é CONSEQUÊNCIA da falha, não desconhecimento dela.
        conclusivas = [f for f in falhas if is_conclusive_failure(f)]
        if ausentes and not conclusivas:
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
