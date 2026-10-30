"""
core/metodologia.py — Catálogo das técnicas e referências científicas.

Centraliza, de forma auditável, TODAS as análises aplicadas no App4 e vincula
cada técnica ao estudo/autor que a fundamenta — para reforçar a credibilidade
dos tickers selecionados e dar transparência metodológica.

Cada técnica é marcada com os ENGINES em que é efetivamente usada. Hoje só
há um: "PORTFOLIO" → aba "Criação de Portfólio" de Empresas B3 (líderes por
segmento, publication lag=1, reconstrução histórica vs Selic/Equal-Weight).
O engine "AVANCADA" saiu com a aba Análise Avançada em 30/10/2026, junto com
as técnicas que só ela aplicava.

Assim, cada aba renderiza apenas o que de fato executa — sem alegar técnicas
que não aplica (o que minaria a credibilidade).
"""
from __future__ import annotations

from dataclasses import dataclass

# Engines
ENG_PF = "PORTFOLIO"
SO_PF  = frozenset({ENG_PF})

# Etapas do pipeline (ordem de exibição)
ETAPA_DADOS       = "1 · Tratamento de dados"
ETAPA_SCORE       = "2 · Score fundamentalista"
ETAPA_RESILIENCIA = "3 · Resiliência e valuation"
ETAPA_RISCO       = "4 · Risco e construção do portfólio"
ETAPA_VALIDACAO   = "5 · Validação e explicabilidade"

_ORDEM_ETAPAS = [
    ETAPA_DADOS, ETAPA_SCORE, ETAPA_RESILIENCIA, ETAPA_RISCO, ETAPA_VALIDACAO,
]


@dataclass(frozen=True)
class Metodo:
    """Uma técnica aplicada, com o que faz, a referência e os engines."""
    etapa:      str
    nome:       str
    o_que_faz:  str
    referencia: str               # citação curta (autor, ano)
    ref_key:    str               # chave para a bibliografia completa
    engines:    frozenset = SO_PF  # onde é efetivamente usada


# ──────────────────────────────────────────────────────────────────────────
# Catálogo de métodos
# ──────────────────────────────────────────────────────────────────────────

CATALOGO: list[Metodo] = [
    # ── 1 — Dados ──────────────────────────────────────────────────────────
    Metodo(
        ETAPA_DADOS, "Publication lag = 1",
        "Para decidir cada ano, usa apenas fundamentos já publicados no ano "
        "anterior — elimina o viés de antecipação (look-ahead) na reconstrução.",
        "controle de look-ahead (López de Prado 2018)", "lopezdeprado2018", SO_PF,
    ),
    Metodo(
        ETAPA_DADOS, "Reconciliação multi-fonte",
        "Sanea vazios/outliers cruzando o banco com fontes web (Fundamentus) "
        "e ranges físicos plausíveis por indicador.",
        "qualidade de dados", "—", SO_PF,
    ),
    Metodo(
        ETAPA_DADOS, "Winsorização de outliers",
        "Limita valores extremos aos percentis 5–95 antes do ranking, evitando "
        "que um número absurdo distorça o score do grupo.",
        "Tukey (1962)", "tukey1962", SO_PF,
    ),
    Metodo(
        ETAPA_DADOS, "Imputação MICE",
        "Estima indicadores ausentes pela correlação entre os demais "
        "(IterativeImputer/BayesianRidge), em vez de mediana cega ou 0,5 fixo.",
        "van Buuren & Groothuis-Oudshoorn (2011)", "vanbuuren2011", SO_PF,
    ),

    # ── 2 — Score fundamentalista ──────────────────────────────────────────
    Metodo(
        ETAPA_SCORE, "Percentil intra-setor",
        "Compara cada empresa apenas com pares do mesmo setor — não mistura "
        "bancos com tecnologia nem pune um setor inteiro por ciclo.",
        "cross-sectional ranking", "—", SO_PF,
    ),
    Metodo(
        ETAPA_SCORE, "Pesos setoriais calibrados",
        "Cada setor pondera os indicadores que mais importam para ele (ex.: DY "
        "pesa mais em utilities; ROIC em indústria).",
        "Fama & French (1992)", "famafrench1992", SO_PF,
    ),
    Metodo(
        ETAPA_SCORE, "Score de entrada (4 motores)",
        "Combina Qualidade, Consistência, Qualidade de Caixa e um motor de Risco "
        "(logit) para aprovar/observar/excluir cada empresa antes de recomendá-la.",
        "Altman (1968); Ohlson (1980)", "altman1968", SO_PF,
    ),
    Metodo(
        ETAPA_SCORE, "Penalidade de instabilidade (CV)",
        "Desconta empresas com resultados voláteis no histórico (coeficiente de "
        "variação) — premia consistência, essencial para dividendos.",
        "qualidade do lucro (earnings quality)", "—", SO_PF,
    ),
    Metodo(
        ETAPA_SCORE, "Penalidade de crowding",
        "Reduz o score de empresas no bucket de valuation mais lotado do setor, "
        "evitando concentração no consenso.",
        "controle de aglomeração", "—", SO_PF,
    ),
    Metodo(
        ETAPA_SCORE, "Ajuste macroeconômico",
        "Modula o score conforme SELIC, juro real e IPCA — em juro alto, "
        "alavancagem pesa mais; em inflação, margens importam mais.",
        "contexto macro-financeiro", "—", SO_PF,
    ),

    # ── 3 — Resiliência e valuation ────────────────────────────────────────
    Metodo(
        ETAPA_RESILIENCIA, "Bônus de reversão à média",
        "Premia empresa de qualidade temporariamente abaixo da própria média "
        "histórica mas acima da mediana setorial — oportunidade, não fraqueza.",
        "De Bondt & Thaler (1985); Lakonishok, Shleifer & Vishny (1994)",
        "debondt1985", SO_PF,
    ),
    Metodo(
        ETAPA_RESILIENCIA, "Penalidade de saúde financeira",
        "Avalia risco de sobrevivência (alavancagem crítica + margens negativas) "
        "separado do score de rentabilidade.",
        "Altman (1968); Altman & Hotchkiss (2006)", "altman1968", SO_PF,
    ),
    Metodo(
        ETAPA_RESILIENCIA, "Desconto vs. histórico próprio",
        "Bonifica empresa cujo valuation (P/VP, EV/EBIT) está abaixo da própria "
        "média histórica sem deterioração de fundamentos — margem de segurança.",
        "Lakonishok, Shleifer & Vishny (1994)", "debondt1985", SO_PF,
    ),
    Metodo(
        ETAPA_RESILIENCIA, "Sustentabilidade de dividendos",
        "Verifica se o dividendo é pagável de forma recorrente (payout + cobertura "
        "por Fluxo de Caixa Operacional), detectando dividend traps.",
        "Damodaran (2012)", "damodaran2012", SO_PF,
    ),

    # ── 4 — Risco e construção do portfólio ────────────────────────────────
    # PORTFOLIO-específicos
    Metodo(
        ETAPA_RISCO, "Seleção de líderes por segmento",
        "A cada ano, elege os líderes do segmento pelo score; nº de líderes "
        "definido por heurística do gap de score entre os melhores.",
        "Jegadeesh & Titman (1993)", "jegadeesh1993", SO_PF,
    ),
    Metodo(
        ETAPA_RISCO, "Penalidade de decaimento de liderança",
        "Desconta o score de quem lidera repetidamente, evitando perpetuar um "
        "único nome e forçando renovação/diversificação.",
        "controle de persistência", "—", SO_PF,
    ),
    Metodo(
        ETAPA_RISCO, "Benchmark Equal-Weight (1/N)",
        "Compara a estratégia ao portfólio ingênuo 1/N do próprio segmento — "
        "benchmark notoriamente difícil de superar.",
        "DeMiguel, Garlappi & Uppal (2009)", "demiguel2009", SO_PF,
    ),
    Metodo(
        ETAPA_RISCO, "Reconstrução histórica vs. Tesouro Selic",
        "Reconstrói o desempenho da carteira ano a ano e exige bater a renda "
        "fixa (Selic) por uma margem mínima para aprovar o segmento.",
        "custo de oportunidade (risk-free)", "—", SO_PF,
    ),
    Metodo(
        ETAPA_RISCO, "Diversificação por segmento",
        "A carteira final combina líderes de múltiplos segmentos, evitando "
        "concentração em um único setor.",
        "Markowitz (1952)", "markowitz1952", SO_PF,
    ),
    Metodo(
        ETAPA_RISCO, "Pesos por score com teto (cap-soft)",
        "Distribui o peso proporcional ao score (gamma-tilt) com teto suave por "
        "ativo, evitando concentração excessiva no topo do ranking.",
        "diversificação (Markowitz 1952)", "markowitz1952", SO_PF,
    ),
    Metodo(
        ETAPA_RISCO, "Reinvestimento de dividendos",
        "O backtest reinveste os dividendos mensais por ação, refletindo o "
        "retorno total (não só a valorização do preço).",
        "retorno total", "—", SO_PF,
    ),

    # ── 5 — Validação e explicabilidade ────────────────────────────────────
]


# ──────────────────────────────────────────────────────────────────────────
# Bibliografia completa
# ──────────────────────────────────────────────────────────────────────────

BIBLIOGRAFIA: dict[str, str] = {
    "altman1968": "Altman, E. I. (1968). Financial Ratios, Discriminant Analysis "
                  "and the Prediction of Corporate Bankruptcy. The Journal of "
                  "Finance, 23(4), 589–609. (+ Ohlson, J. (1980), JAR 18(1).)",
    "damodaran2012": "Damodaran, A. (2012). Investment Valuation: Tools and "
                     "Techniques for Determining the Value of Any Asset (3rd ed.). "
                     "Wiley.",
    "debondt1985": "De Bondt, W. F. M., & Thaler, R. (1985). Does the Stock Market "
                   "Overreact? The Journal of Finance, 40(3), 793–805. "
                   "(+ Lakonishok, Shleifer & Vishny (1994), JF 49(5).)",
    "demiguel2009": "DeMiguel, V., Garlappi, L., & Uppal, R. (2009). Optimal Versus "
                    "Naive Diversification: How Inefficient is the 1/N Portfolio "
                    "Strategy? Review of Financial Studies, 22(5), 1915–1953.",
    "famafrench1992": "Fama, E. F., & French, K. R. (1992). The Cross-Section of "
                      "Expected Stock Returns. The Journal of Finance, 47(2), 427–465.",
    "jegadeesh1993": "Jegadeesh, N., & Titman, S. (1993). Returns to Buying Winners "
                     "and Selling Losers: Implications for Stock Market Efficiency. "
                     "The Journal of Finance, 48(1), 65–91.",
    "lopezdeprado2018": "López de Prado, M. (2018). Advances in Financial Machine "
                        "Learning. Wiley.",
    "markowitz1952": "Markowitz, H. (1952). Portfolio Selection. The Journal of "
                     "Finance, 7(1), 77–91.",
    "tukey1962": "Tukey, J. W. (1962). The Future of Data Analysis. The Annals of "
                 "Mathematical Statistics, 33(1), 1–67.",
    "vanbuuren2011": "van Buuren, S., & Groothuis-Oudshoorn, K. (2011). mice: "
                     "Multivariate Imputation by Chained Equations in R. Journal of "
                     "Statistical Software, 45(3), 1–67.",
}


# ──────────────────────────────────────────────────────────────────────────
# Helpers para a UI
# ──────────────────────────────────────────────────────────────────────────

def _metodos(engine: str | None) -> list[Metodo]:
    if engine is None:
        return list(CATALOGO)
    return [m for m in CATALOGO if engine in m.engines]


def catalogo_por_etapa(engine: str | None = None) -> dict[str, list[Metodo]]:
    """Catálogo agrupado por etapa (na ordem de exibição), filtrado por engine."""
    agrupado: dict[str, list[Metodo]] = {e: [] for e in _ORDEM_ETAPAS}
    for m in _metodos(engine):
        agrupado.setdefault(m.etapa, []).append(m)
    return {e: agrupado[e] for e in _ORDEM_ETAPAS if agrupado.get(e)}


def referencias_ordenadas(engine: str | None = None) -> list[str]:
    """Referências bibliográficas (alfabéticas) das técnicas do engine dado."""
    keys = {m.ref_key for m in _metodos(engine) if m.ref_key != "—"}
    return sorted(BIBLIOGRAFIA[k] for k in keys if k in BIBLIOGRAFIA)


def total_metodos(engine: str | None = None) -> int:
    """Número de técnicas catalogadas para o engine dado."""
    return len(_metodos(engine))
