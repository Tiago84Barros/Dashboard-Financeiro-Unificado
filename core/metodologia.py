"""
core/metodologia.py — Catálogo das técnicas e referências científicas.

Centraliza, de forma auditável, TODAS as análises aplicadas no App4 e vincula
cada técnica ao estudo/autor que a fundamenta — para reforçar a credibilidade
dos tickers selecionados e dar transparência metodológica.

Cada técnica é marcada com os ENGINES em que é efetivamente usada:
  • "AVANCADA"  → aba "Análise Avançada" (score + Markowitz/FF/DCC-GARCH/etc.)
  • "PORTFOLIO" → aba "Criação de Portfólio" (líderes por segmento, publication
                   lag=1, reconstrução histórica vs Selic/Equal-Weight)

Assim, cada aba renderiza apenas o que de fato executa — sem alegar técnicas
que não aplica (o que minaria a credibilidade).
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Engines
ENG_AV = "AVANCADA"
ENG_PF = "PORTFOLIO"
AMBOS  = frozenset({ENG_AV, ENG_PF})
SO_AV  = frozenset({ENG_AV})
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
    engines:    frozenset = AMBOS  # onde é efetivamente usada


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
        ETAPA_DADOS, "Ano-base anterior",
        "O score usa fundamentos do último ano COMPLETO para decidir o ano "
        "corrente — evita dado parcial e antecipação.",
        "boa prática contábil", "—", SO_AV,
    ),
    Metodo(
        ETAPA_DADOS, "Exclusão de empresas descontinuadas",
        "Empresas que saíram da B3 são removidas — só ativos investíveis hoje "
        "entram na seleção (controle de survivorship).",
        "Brown et al. (1992)", "brown1992", SO_AV,
    ),
    Metodo(
        ETAPA_DADOS, "Filtro de liquidez de negociação",
        "Exclui ações com baixo volume diário, onde o spread bid-ask encarece "
        "a operação.",
        "Amihud (2002)", "amihud2002", SO_AV,
    ),
    Metodo(
        ETAPA_DADOS, "Reconciliação multi-fonte",
        "Sanea vazios/outliers cruzando o banco com fontes web (Fundamentus) "
        "e ranges físicos plausíveis por indicador.",
        "qualidade de dados", "—", AMBOS,
    ),
    Metodo(
        ETAPA_DADOS, "Winsorização de outliers",
        "Limita valores extremos aos percentis 5–95 antes do ranking, evitando "
        "que um número absurdo distorça o score do grupo.",
        "Tukey (1962)", "tukey1962", AMBOS,
    ),
    Metodo(
        ETAPA_DADOS, "Imputação MICE",
        "Estima indicadores ausentes pela correlação entre os demais "
        "(IterativeImputer/BayesianRidge), em vez de mediana cega ou 0,5 fixo.",
        "van Buuren & Groothuis-Oudshoorn (2011)", "vanbuuren2011", AMBOS,
    ),

    # ── 2 — Score fundamentalista ──────────────────────────────────────────
    Metodo(
        ETAPA_SCORE, "Percentil intra-setor",
        "Compara cada empresa apenas com pares do mesmo setor — não mistura "
        "bancos com tecnologia nem pune um setor inteiro por ciclo.",
        "cross-sectional ranking", "—", AMBOS,
    ),
    Metodo(
        ETAPA_SCORE, "Pesos setoriais calibrados",
        "Cada setor pondera os indicadores que mais importam para ele (ex.: DY "
        "pesa mais em utilities; ROIC em indústria).",
        "Fama & French (1992)", "famafrench1992", AMBOS,
    ),
    Metodo(
        ETAPA_SCORE, "Pesos empíricos Fama-MacBeth (opcional)",
        "Regressão cross-sectional estima empiricamente o quanto cada indicador "
        "prevê qualidade futura, calibrando os pesos.",
        "Fama & MacBeth (1973)", "famamacbeth1973", SO_AV,
    ),
    Metodo(
        ETAPA_SCORE, "Score de entrada (4 motores)",
        "Combina Qualidade, Consistência, Qualidade de Caixa e um motor de Risco "
        "(logit) para aprovar/observar/excluir cada empresa antes de recomendá-la.",
        "Altman (1968); Ohlson (1980)", "altman1968", AMBOS,
    ),
    Metodo(
        ETAPA_SCORE, "Penalidade de instabilidade (CV)",
        "Desconta empresas com resultados voláteis no histórico (coeficiente de "
        "variação) — premia consistência, essencial para dividendos.",
        "qualidade do lucro (earnings quality)", "—", AMBOS,
    ),
    Metodo(
        ETAPA_SCORE, "Penalidade de crowding",
        "Reduz o score de empresas no bucket de valuation mais lotado do setor, "
        "evitando concentração no consenso.",
        "controle de aglomeração", "—", AMBOS,
    ),
    Metodo(
        ETAPA_SCORE, "Ajuste macroeconômico",
        "Modula o score conforme SELIC, juro real e IPCA — em juro alto, "
        "alavancagem pesa mais; em inflação, margens importam mais.",
        "contexto macro-financeiro", "—", AMBOS,
    ),

    # ── 3 — Resiliência e valuation ────────────────────────────────────────
    Metodo(
        ETAPA_RESILIENCIA, "Bônus de reversão à média",
        "Premia empresa de qualidade temporariamente abaixo da própria média "
        "histórica mas acima da mediana setorial — oportunidade, não fraqueza.",
        "De Bondt & Thaler (1985); Lakonishok, Shleifer & Vishny (1994)",
        "debondt1985", AMBOS,
    ),
    Metodo(
        ETAPA_RESILIENCIA, "Penalidade de saúde financeira",
        "Avalia risco de sobrevivência (alavancagem crítica + margens negativas) "
        "separado do score de rentabilidade.",
        "Altman (1968); Altman & Hotchkiss (2006)", "altman1968", AMBOS,
    ),
    Metodo(
        ETAPA_RESILIENCIA, "Desconto vs. histórico próprio",
        "Bonifica empresa cujo valuation (P/VP, EV/EBIT) está abaixo da própria "
        "média histórica sem deterioração de fundamentos — margem de segurança.",
        "Lakonishok, Shleifer & Vishny (1994)", "debondt1985", AMBOS,
    ),
    Metodo(
        ETAPA_RESILIENCIA, "Preço justo — Graham",
        "Margem de segurança via √(22,5·LPA·VPA): a ação está cara ou barata em "
        "termos absolutos, não só relativa aos pares.",
        "Graham (1949); Graham & Dodd (1934)", "graham1949", SO_AV,
    ),
    Metodo(
        ETAPA_RESILIENCIA, "Preço justo — Bazin",
        "Preço-teto pelo dividendo: preço justo = dividendo / yield-alvo. Foco no "
        "investidor de renda.",
        "Bazin (1991)", "bazin1991", SO_AV,
    ),
    Metodo(
        ETAPA_RESILIENCIA, "Sustentabilidade de dividendos",
        "Verifica se o dividendo é pagável de forma recorrente (payout + cobertura "
        "por Fluxo de Caixa Operacional), detectando dividend traps.",
        "Damodaran (2012)", "damodaran2012", AMBOS,
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
        "diversificação (Markowitz 1952)", "markowitz1952", AMBOS,
    ),
    Metodo(
        ETAPA_RISCO, "Reinvestimento de dividendos",
        "O backtest reinveste os dividendos mensais por ação, refletindo o "
        "retorno total (não só a valorização do preço).",
        "retorno total", "—", AMBOS,
    ),
    # AVANCADA-específicos
    Metodo(
        ETAPA_RISCO, "Otimização de Markowitz",
        "Calcula pesos que minimizam a variância da carteira sujeitos a teto por "
        "ativo — considera correlações, não só o score.",
        "Markowitz (1952)", "markowitz1952", SO_AV,
    ),
    Metodo(
        ETAPA_RISCO, "Covariância Ledoit-Wolf",
        "Shrinkage da matriz de covariância, estável em janelas curtas — evita "
        "pesos extremos do estimador amostral puro.",
        "Ledoit & Wolf (2004)", "ledoitwolf2004", SO_AV,
    ),
    Metodo(
        ETAPA_RISCO, "Modelo de risco Fama-French",
        "Covariância estruturada por fatores (mercado, tamanho, valor): "
        "Σ = B·Σ_F·B' + D. Captura risco comum entre pares.",
        "Fama & French (1993)", "famafrench1993", SO_AV,
    ),
    Metodo(
        ETAPA_RISCO, "Covariância dinâmica DCC-GARCH",
        "Correlações condicionais que variam no tempo e sobem em crises — captura "
        "o risco de cauda que a correlação estática ignora.",
        "Engle (2002); Bollerslev (1986)", "engle2002", SO_AV,
    ),
    Metodo(
        ETAPA_RISCO, "Views Bayesianas Black-Litterman",
        "Combina o equilíbrio de mercado com as visões do investidor de forma "
        "estatisticamente coerente (opcional).",
        "Black & Litterman (1992); Idzorek (2005)", "blacklitterman1992", SO_AV,
    ),
    Metodo(
        ETAPA_RISCO, "Custos de transação",
        "Backtest desconta corretagem, spread e IR sobre lucro acima da isenção "
        "mensal — retorno realista para PF no Brasil.",
        "modelo de custos PF-BR", "—", SO_AV,
    ),

    # ── 5 — Validação e explicabilidade ────────────────────────────────────
    Metodo(
        ETAPA_VALIDACAO, "Validação walk-forward (purged k-fold)",
        "Calibra parâmetros em janelas crescentes com purga/embargo, evitando "
        "vazamento de informação do futuro.",
        "López de Prado (2018)", "lopezdeprado2018", SO_AV,
    ),
    Metodo(
        ETAPA_VALIDACAO, "Teste de estresse histórico",
        "Reaplica choques de crises reais (Subprime, COVID, eleições) à carteira "
        "para estimar perda potencial.",
        "stress testing", "—", SO_AV,
    ),
    Metodo(
        ETAPA_VALIDACAO, "Atribuição Brinson",
        "Decompõe o retorno em efeito de alocação setorial vs. seleção de ativos.",
        "Brinson, Hood & Beebower (1986)", "brinson1986", SO_AV,
    ),
    Metodo(
        ETAPA_VALIDACAO, "Explicabilidade Shapley (XAI)",
        "Decompõe o score de cada empresa na contribuição exata de cada motor "
        "(qualidade, risco, consistência) — transparência total.",
        "Shapley (1953); Lundberg & Lee (2017)", "shapley1953", SO_AV,
    ),
    Metodo(
        ETAPA_VALIDACAO, "Bootstrap (intervalo de confiança)",
        "Reamostra o universo para estimar a incerteza do score — quão robusto é "
        "o ranking a pequenas variações nos dados.",
        "Efron (1979)", "efron1979", SO_AV,
    ),
]


# ──────────────────────────────────────────────────────────────────────────
# Bibliografia completa
# ──────────────────────────────────────────────────────────────────────────

BIBLIOGRAFIA: dict[str, str] = {
    "altman1968": "Altman, E. I. (1968). Financial Ratios, Discriminant Analysis "
                  "and the Prediction of Corporate Bankruptcy. The Journal of "
                  "Finance, 23(4), 589–609. (+ Ohlson, J. (1980), JAR 18(1).)",
    "amihud2002": "Amihud, Y. (2002). Illiquidity and stock returns: cross-section "
                  "and time-series effects. Journal of Financial Markets, 5(1), 31–56.",
    "bazin1991": "Bazin, D. (1991). Faça Fortuna com Ações, Antes que Seja Tarde. "
                 "Editora CLA.",
    "blacklitterman1992": "Black, F., & Litterman, R. (1992). Global Portfolio "
                          "Optimization. Financial Analysts Journal, 48(5), 28–43. "
                          "(+ Idzorek, T. (2005). A Step-by-Step Guide to the "
                          "Black-Litterman Model.)",
    "bollerslev1986": "Bollerslev, T. (1986). Generalized Autoregressive Conditional "
                      "Heteroskedasticity. Journal of Econometrics, 31(3), 307–327.",
    "brinson1986": "Brinson, G. P., Hood, L. R., & Beebower, G. L. (1986). "
                   "Determinants of Portfolio Performance. Financial Analysts "
                   "Journal, 42(4), 39–44.",
    "brown1992": "Brown, S. J., Goetzmann, W., Ibbotson, R. G., & Ross, S. A. "
                 "(1992). Survivorship Bias in Performance Studies. Review of "
                 "Financial Studies, 5(4), 553–580.",
    "damodaran2012": "Damodaran, A. (2012). Investment Valuation: Tools and "
                     "Techniques for Determining the Value of Any Asset (3rd ed.). "
                     "Wiley.",
    "debondt1985": "De Bondt, W. F. M., & Thaler, R. (1985). Does the Stock Market "
                   "Overreact? The Journal of Finance, 40(3), 793–805. "
                   "(+ Lakonishok, Shleifer & Vishny (1994), JF 49(5).)",
    "demiguel2009": "DeMiguel, V., Garlappi, L., & Uppal, R. (2009). Optimal Versus "
                    "Naive Diversification: How Inefficient is the 1/N Portfolio "
                    "Strategy? Review of Financial Studies, 22(5), 1915–1953.",
    "efron1979": "Efron, B. (1979). Bootstrap Methods: Another Look at the "
                 "Jackknife. The Annals of Statistics, 7(1), 1–26.",
    "engle2002": "Engle, R. (2002). Dynamic Conditional Correlation. Journal of "
                 "Business & Economic Statistics, 20(3), 339–350.",
    "famafrench1992": "Fama, E. F., & French, K. R. (1992). The Cross-Section of "
                      "Expected Stock Returns. The Journal of Finance, 47(2), 427–465.",
    "famafrench1993": "Fama, E. F., & French, K. R. (1993). Common Risk Factors in "
                      "the Returns on Stocks and Bonds. Journal of Financial "
                      "Economics, 33(1), 3–56.",
    "famamacbeth1973": "Fama, E. F., & MacBeth, J. D. (1973). Risk, Return, and "
                       "Equilibrium: Empirical Tests. Journal of Political Economy, "
                       "81(3), 607–636.",
    "graham1949": "Graham, B. (1949). The Intelligent Investor. Harper & Row. "
                  "(+ Graham, B., & Dodd, D. (1934). Security Analysis. McGraw-Hill.)",
    "jegadeesh1993": "Jegadeesh, N., & Titman, S. (1993). Returns to Buying Winners "
                     "and Selling Losers: Implications for Stock Market Efficiency. "
                     "The Journal of Finance, 48(1), 65–91.",
    "ledoitwolf2004": "Ledoit, O., & Wolf, M. (2004). Honey, I Shrunk the Sample "
                      "Covariance Matrix. The Journal of Portfolio Management, "
                      "30(4), 110–119.",
    "lopezdeprado2018": "López de Prado, M. (2018). Advances in Financial Machine "
                        "Learning. Wiley.",
    "lundberg2017": "Lundberg, S. M., & Lee, S.-I. (2017). A Unified Approach to "
                    "Interpreting Model Predictions. NeurIPS 30.",
    "markowitz1952": "Markowitz, H. (1952). Portfolio Selection. The Journal of "
                     "Finance, 7(1), 77–91.",
    "shapley1953": "Shapley, L. S. (1953). A Value for n-Person Games. Contributions "
                   "to the Theory of Games, 2(28), 307–317. "
                   "(+ Lundberg & Lee (2017), NeurIPS.)",
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
