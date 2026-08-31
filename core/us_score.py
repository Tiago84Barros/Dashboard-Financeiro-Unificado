"""
core/us_score.py
Score fundamentalista americano — RELATIVO por indústria (não copia pesos do B3).

Metodologia (alinhada ao rigor do B3, adaptada aos EUA):
  1. Winsorização intra-grupo (apara caudas antes de ranquear).
  2. Percentil intra-INDÚSTRIA por métrica (grupo estruturalmente comparável);
     grupos pequenos caem para setor e, por fim, universo (flag de fallback).
  3. Ausência preserva neutralidade estatística, mas reduz explicitamente a
     confiança e puxa a trilha incompleta para 50 (não mascara cobertura baixa).
  4. Seis trilhas de fatores → média das métricas da trilha → soma ponderada → 0–100.
  5. Pesos por trilha ajustáveis por setor (economicamente justificado).

Puro (pandas em memória). Coberto por tests/test_us_score.py.
"""
from __future__ import annotations

import re

import pandas as pd

from core.us_metrics import LOWER_IS_BETTER

# ── Trilhas de fatores ────────────────────────────────────────────────────────
FACTOR_TRACKS: dict[str, list[str]] = {
    # sbc_to_revenue e fcf_ex_sbc_margin entram como QUALIDADE DOS LUCROS: a
    # remuneração em ações é despesa econômica que o FCF GAAP devolve somada,
    # inflando a margem de caixa de quem paga o time em participação.
    "quality": ["gross_margin", "operating_margin", "net_margin", "fcf_margin",
                "cash_conversion", "roe", "roa", "sbc_to_revenue",
                "fcf_ex_sbc_margin"],
    # `*_growth_3y` sao taxas SIMETRICAS, nao CAGR (core/us_metrics.py). O CAGR
    # nao e definido com base ou ponta <= 0, e devolvia None para a maioria das
    # empresas -- prejuizo persistente entrava aqui como falta de dado, o que
    # derruba a cobertura da trilha em vez de ranquear a empresa por baixo.
    "growth": ["revenue_cagr_3y", "revenue_cagr_5y", "op_income_growth_3y",
               "eps_growth_3y", "fcf_growth_3y"],
    "solidity": ["net_debt_ebitda", "interest_coverage", "current_ratio",
                 "debt_to_equity"],
    "capital_efficiency": ["roic"],
    # p_fcf saiu: é exatamente 1/fcf_yield, que já está na trilha — mantê-lo
    # dobrava o peso do fluxo de caixa dentro de valuation. Mesmo motivo pelo
    # qual `pe` nunca entrou aqui, só earnings_yield.
    "valuation": ["earnings_yield", "ev_ebit", "ev_ebitda", "fcf_yield", "p_s"],
    # Recompra só cria valor se reduzir a base acionária: share_count_cagr_3y
    # é o contraponto ao shareholder_yield (buyback anulado por emissão SBC).
    "shareholder": ["shareholder_yield", "share_count_cagr_3y"],
}

DEFAULT_TRACK_WEIGHTS: dict[str, float] = {
    "quality": 0.22, "growth": 0.18, "solidity": 0.15,
    "capital_efficiency": 0.15, "valuation": 0.18, "shareholder": 0.12,
}

# Ajustes por setor (economicamente justificados). Banco: a alavancagem
# contábil é o negócio, não um risco a punir — dívida/EBITDA e cobertura de
# juros não significam para ele o que significam para uma indústria.
#
# A-140: o bloco "Real Estate" saiu daqui. Ele existia porque lucro e EBIT de
# REIT são distorcidos pela depreciação de imóvel; a resposta a isso deixou de
# ser reponderar e passou a ser excluir -- o módulo americano analisa ações, e
# REIT não é ação (core/us_instrumento.py). Manter os pesos seria guardar um
# ramo que nenhum dado alcança.
SECTOR_TRACK_OVERRIDES: dict[str, dict[str, float]] = {
    "Financial Services": {"quality": 0.24, "valuation": 0.20, "solidity": 0.10,
                           "capital_efficiency": 0.16, "growth": 0.18,
                           "shareholder": 0.12},
}

NEUTRAL = 0.5
_ALL_METRICS = [m for ms in FACTOR_TRACKS.values() for m in ms]

# Múltiplos ranqueados pelo YIELD recíproco (achado A-101). EV/EBIT = -9 não é
# mais barato que 5: o múltiplo deixa de ser monótono quando o denominador vira
# negativo, e LOWER_IS_BETTER punha a empresa deficitária no topo da trilha de
# valuation. EBIT/EV é monótono através do zero — rendimento negativo ranqueia
# abaixo de qualquer rendimento positivo, que é a leitura econômica correta.
# p_s fica de fora: receita não fica negativa, então P/S não tem o problema.
_RANK_AS_RECIPROCAL = frozenset({"ev_ebit", "ev_ebitda", "p_fcf", "pe"})

# Uma nota pode ser calculada com informação parcial, mas só é considerada
# decision-grade quando as trilhas essenciais possuem cobertura mínima.
CRITICAL_TRACK_MIN_COVERAGE = {
    "quality": 0.40,
    "growth": 0.40,
    "solidity": 0.25,
    "valuation": 0.50,
}

# A-160: quantas das perguntas da trilha ainda PODEM ser feitas.
#
# `coverage` responde "das perguntas respondíveis, quantas foram respondidas" —
# a razão indefinida sai do numerador e do denominador desde 0.7.1, e isso está
# certo. Mas sozinha ela deixa um buraco: a trilha em que sobrou UMA pergunta
# respondível, e ela foi respondida, marca cobertura de 100%. É o mesmo defeito
# de "quem pergunta menos tira nota maior", uma camada abaixo — no nível da
# métrica em vez do motor.
#
# Respondibilidade é a fração das métricas DEFINIDAS pela metodologia que a
# empresa consegue ter. O piso é a maioria estrita: uma trilha avaliada pela
# minoria das próprias perguntas não foi avaliada. Com as 4 métricas de
# Solidez, 2 respondíveis reprovam e 3 passam.
#
# O piso morde de verdade — 83 das 2.618 empresas da vitrine de 31/08/2026 têm
# alguma trilha crítica em 50%, e 68 delas passariam por todos os outros
# critérios. Um piso que não reprova ninguém seria carimbo, não portão.
PISO_RESPONDIBILIDADE_CRITICA = 0.5


def _nm_mask(df: pd.DataFrame, metrics: list[str]) -> pd.DataFrame:
    """Quais métricas da linha são INDEFINIDAS, e não ausentes.

    `nm_metrics` chega de core.us_metrics: são as razões anuladas porque o
    denominador foi MEDIDO e veio <= 0 (prejuízo, patrimônio negativo, capital
    investido negativo). Não há dado faltando ali — a razão simplesmente não
    existe, e cobrar cobertura por ela é cobrar a empresa por uma pergunta que
    não tem resposta possível.

    Tolera a coluna ausente: a vitrine publicada pode estar num snapshot
    anterior a ela (já houve drift de schema aqui), e sem a coluna o
    comportamento volta a ser exatamente o de antes.
    """
    vazio = pd.DataFrame(False, index=df.index, columns=metrics)
    if "nm_metrics" not in df.columns or not metrics:
        return vazio
    for i in df.index:
        valor = df.at[i, "nm_metrics"]
        if valor is None or isinstance(valor, float):
            continue
        for nome in valor:
            if nome in vazio.columns:
                vazio.at[i, nome] = True
    return vazio


def _answerability(df: pd.DataFrame, metrics: list[str]) -> pd.Series:
    """Fração das métricas da trilha que a empresa consegue ter, 0–1.

    Diferente de `coverage`: o denominador aqui é o que a METODOLOGIA define,
    não o que a empresa conseguiu definir. Métrica que a metodologia pede e a
    vitrine nem publica conta como não respondível — a pergunta continua sem
    resposta, e omiti-la do denominador repetiria o erro que este piso corrige.
    """
    if not metrics:
        return pd.Series(0.0, index=df.index)
    presentes = [m for m in metrics if m in df.columns]
    if not presentes:
        return pd.Series(0.0, index=df.index)
    nm = _nm_mask(df, presentes)
    return (~nm).sum(axis=1).div(len(metrics))


# A-139: `sector` na vitrine EUA guarda a DESCRIÇÃO SIC do formulário da SEC
# ("State Commercial Banks", "National Commercial Banks"), não o rótulo GICS que
# `SECTOR_TRACK_OVERRIDES` assume. Medido no armazém: "Financial Services" batia
# em ZERO das 2.831 linhas — os pesos por setor e a penalidade de confiança
# abaixo eram código economicamente justificado, documentado, e morto.
#
# Os padrões são ancorados por termo, não por substring solta: um regex ingênuo
# com "dealer" arrasta "Retail-Auto Dealers & Gasoline Stations" para dentro do
# setor financeiro.
_SIC_FINANCEIRO = re.compile(
    r"(?:^|[^a-z])(?:bank(?:s|ing)?|savings institutions?|insurance|"
    r"security (?:&|and) commodity brokers|security brokers|"
    r"commodity contracts brokers|investment advice|finance services|"
    r"loan brokers|mortgage bankers|credit institutions?|credit agencies|"
    r"investors, nec|consumer credit reporting|business credit)",
    re.IGNORECASE,
)

def sector_group(sector: object) -> str | None:
    """Traduz a descrição SIC para o rótulo que a metodologia por setor usa.

    Devolve "Financial Services" ou o próprio setor quando nenhum grupo com
    tratamento específico se aplica. REIT não aparece aqui: ele é excluído do
    universo antes do score (core/us_instrumento.py), não reponderado.
    """
    texto = str(sector or "").strip()
    if not texto:
        return None
    if _SIC_FINANCEIRO.search(texto):
        return "Financial Services"
    return texto


def _sector_confidence_penalty(sector: object) -> float:
    """Penaliza categorias ainda atendidas por proxies contábeis genéricas."""
    return 0.85 if sector_group(sector) == "Financial Services" else 1.0


def _weights_for(sector: str | None) -> dict[str, float]:
    w = dict(DEFAULT_TRACK_WEIGHTS)
    grupo = sector_group(sector)
    if grupo and grupo in SECTOR_TRACK_OVERRIDES:
        w.update(SECTOR_TRACK_OVERRIDES[grupo])
    total = sum(w.values())
    return {k: v / total for k, v in w.items()}  # renormaliza


def _winsorized_percentile(s: pd.Series, lower: float = 0.05,
                           upper: float = 0.95) -> pd.Series:
    """Winsoriza e devolve o percentil (0–1) na ordem 'maior é melhor'."""
    valid = s.dropna()
    if valid.empty:
        return pd.Series(NEUTRAL, index=s.index)
    lo, hi = valid.quantile(lower), valid.quantile(upper)
    clipped = s.clip(lower=lo, upper=hi)
    pct = clipped.rank(pct=True, method="average")
    return pct.fillna(NEUTRAL)


def _rank_within(df: pd.DataFrame, group_col: str, min_group: int) -> pd.DataFrame:
    """Percentil por métrica dentro do grupo; grupos pequenos usam o universo."""
    out = pd.DataFrame(index=df.index)
    counts = df.groupby(group_col)[group_col].transform("size") if group_col in df else None
    for metric in _ALL_METRICS:
        if metric not in df.columns:
            out[metric] = NEUTRAL
            continue
        col = pd.to_numeric(df[metric], errors="coerce")
        reciproco = metric in _RANK_AS_RECIPROCAL
        if reciproco:
            # 1/0 seria infinito; o múltiplo zerado não diz nada sobre preço.
            col = (1.0 / col.where(col != 0)).replace([float("inf"),
                                                       float("-inf")], None)
        base = df.assign(**{metric: col})
        if group_col in df.columns:
            ranked = base.groupby(group_col)[metric].transform(_winsorized_percentile)
            # grupos pequenos: rank no universo inteiro
            small = counts < min_group
            if small.any():
                universe = _winsorized_percentile(col)
                ranked = ranked.where(~small, universe)
        else:
            ranked = _winsorized_percentile(col)
        # O recíproco já inverteu o sentido: EBIT/EV maior é melhor.
        if metric in LOWER_IS_BETTER and not reciproco:
            ranked = 1.0 - ranked
        out[metric] = ranked.fillna(NEUTRAL)
    return out


def score_cross_section(df: pd.DataFrame, *, group_col: str = "industry",
                        min_group: int = 4) -> pd.DataFrame:
    """Calcula o score 0–100 e as trilhas para cada empresa do cross-section.

    df precisa conter 'symbol', 'sector'/'industry' e as colunas de métricas
    (saída de core.us_metrics.compute_company_metrics). Retorna cópia com colunas
    score_<trilha> (0–100), coverage_<trilha> e 'score' (0–100).
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["symbol", "score"])
    df = df.copy().reset_index(drop=True)
    pct = _rank_within(df, group_col, min_group)

    result = df.copy()
    track_scores: dict[str, pd.Series] = {}
    for track, metrics in FACTOR_TRACKS.items():
        present = [m for m in metrics if m in df.columns]
        if present:
            track_scores[track] = pct[present].mean(axis=1)
            # cobertura real = fração de métricas não-ausentes na trilha,
            # sobre as que PODIAM existir. Uma razão indefinida por medida
            # (ver _nm_mask) sai do numerador E do denominador: não é lacuna.
            nm = _nm_mask(df, present)
            denom = (~nm).sum(axis=1)
            cov = ((df[present].notna() & ~nm).sum(axis=1)
                   .div(denom.where(denom > 0)).fillna(0.0))
        else:
            track_scores[track] = pd.Series(NEUTRAL, index=df.index)
            cov = pd.Series(0.0, index=df.index)
        # Uma trilha esparsa não pode produzir convicção extrema. A nota é
        # encolhida para o neutro conforme a raiz da cobertura observada.
        reliability = cov.pow(0.5)
        track_scores[track] = NEUTRAL + (track_scores[track] - NEUTRAL) * reliability
        result[f"score_{track}"] = (track_scores[track] * 100).round(1)
        result[f"coverage_{track}"] = (cov * 100).round(0)
        result[f"answerability_{track}"] = (
            _answerability(df, metrics) * 100).round(0)

    # score final: soma ponderada por setor (pesos por linha, pois variam)
    def _row_score(i: int) -> float:
        sector = df.at[i, "sector"] if "sector" in df.columns else None
        w = _weights_for(sector)
        return round(sum(w[t] * track_scores[t].iat[i] for t in FACTOR_TRACKS) * 100, 1)

    result["score"] = [_row_score(i) for i in range(len(df))]
    # cobertura global (quantas métricas a empresa tinha, de todas)
    metric_cols = [m for m in _ALL_METRICS if m in df.columns]
    if metric_cols:
        nm_all = _nm_mask(df, metric_cols)
        denom_all = (~nm_all).sum(axis=1)
        cov_all = ((df[metric_cols].notna() & ~nm_all).sum(axis=1)
                   .div(denom_all.where(denom_all > 0)).fillna(0.0))
        result["coverage"] = (cov_all * 100).round(0)
    else:
        result["coverage"] = 0.0
    critical_missing: list[list[str]] = []
    unanswerable: list[list[str]] = []
    confidence: list[float] = []
    statuses: list[str] = []
    for i in range(len(result)):
        missing = [
            track for track, minimum in CRITICAL_TRACK_MIN_COVERAGE.items()
            if float(result.at[i, f"coverage_{track}"] or 0) / 100.0 < minimum
        ]
        critical_missing.append(missing)
        mudas = [
            track for track in CRITICAL_TRACK_MIN_COVERAGE
            if float(result.at[i, f"answerability_{track}"] or 0) / 100.0
            <= PISO_RESPONDIBILIDADE_CRITICA
        ]
        unanswerable.append(mudas)
        overall = float(result.at[i, "coverage"] or 0) / 100.0
        critical_ratio = 1.0 - len(missing) / len(CRITICAL_TRACK_MIN_COVERAGE)
        sector = df.at[i, "sector"] if "sector" in df.columns else None
        conf = 100.0 * (0.70 * overall + 0.30 * critical_ratio)
        conf *= _sector_confidence_penalty(sector)
        conf = round(max(0.0, min(100.0, conf)), 1)
        confidence.append(conf)
        # A-160: a marca de balanço estruturalmente quebrado deixa de ser
        # eliminatória por si só.
        #
        # A trava nasceu em A-101 contra um risco real: patrimônio negativo,
        # EBITDA não positivo ou capital investido negativo anulam várias razões
        # de uma vez, e naquela versão a empresa em pior situação chegava aqui
        # apenas como "cobertura um pouco menor". Duas correções depois esse
        # caminho não existe mais — 0.7.1 tirou a razão indefinida do numerador
        # E do denominador da cobertura, e `mudas` abaixo barra a trilha que
        # ficou sem a maioria das próprias perguntas.
        #
        # O que a trava passou a fazer não era o que ela foi escrita para
        # fazer. Ela bloqueava 1.023 das 2.618 empresas da vitrine de
        # 31/08/2026 — entre elas Lowe's, Altria, Cardinal Health e Bath & Body
        # Works, todas com cobertura 100% e confiança 100%. Patrimônio líquido
        # negativo nessas empresas é estrutura de capital escolhida (recompra
        # acumulada acima do lucro retido), não avaria de dado nem sinal de
        # insolvência. Recusar opinião sobre a Lowe's porque ela recomprou
        # ações é erro de categoria: o portão responde "consigo sustentar uma
        # recomendação?" e a resposta ali é sim.
        #
        # As 710 empresas de EBITDA não positivo também não são indecidíveis:
        # os múltiplos de valor são ranqueados pelo YIELD recíproco, que é
        # monótono através do zero, e as margens negativas ranqueiam no fundo.
        # A empresa deficitária tem de sair com nota BAIXA, não sem nota —
        # excluí-la do universo decidível devolvia ao usuário um cross-section
        # só de lucrativas, enquanto a evidência de que o score ordena (Rank-IC
        # em 16 safras) foi medida no cross-section inteiro, deficitárias
        # incluídas. O painel media um universo e a tela agia sobre outro.
        #
        # A marca continua gravada e continua sendo exibida: é informação
        # material sobre a empresa. Ela deixou de ser motivo para o app não ter
        # opinião; nunca deixou de ser motivo para o usuário olhar.
        if conf >= 75.0 and not missing and not mudas:
            statuses.append("decision_grade")
        elif conf >= 60.0:
            statuses.append("research_grade")
        else:
            statuses.append("screen_grade")
    result["score_confidence"] = confidence
    result["score_status"] = statuses
    result["critical_missing"] = critical_missing
    result["unanswerable_tracks"] = unanswerable
    return result.sort_values("score", ascending=False).reset_index(drop=True)


def industry_comparison(scored: pd.DataFrame, industry: str) -> pd.DataFrame:
    """Recorta e ordena os pares de uma indústria (aba Comparação por Indústria)."""
    if scored is None or scored.empty or "industry" not in scored.columns:
        return pd.DataFrame()
    peers = scored[scored["industry"] == industry].copy()
    return peers.sort_values("score", ascending=False).reset_index(drop=True)
