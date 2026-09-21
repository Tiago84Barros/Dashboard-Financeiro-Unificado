"""
views/documentação.py
Documentação visual do App 4.

Cria fluxogramas interativos para explicar as partes mais complexas do app:
análise avançada, simulador/criação de portfólio B3, análise de portfólio e
dicionário de indicadores/demonstrações financeiras.
"""
from __future__ import annotations

import html
from dataclasses import dataclass

import streamlit as st

from design.componentes import container_pagina, cor_token

_CSS = """
<style>
.doc-intro {
    background: linear-gradient(135deg, color-mix(in srgb, var(--app-primary) 10%, transparent), color-mix(in srgb, var(--app-info) 8%, transparent));
    border: 1px solid color-mix(in srgb, var(--app-primary) 22%, transparent);
    border-radius: 10px;
    padding: 18px 20px;
    margin-bottom: 18px;
}
.doc-intro-title {
    color: var(--app-text);
    font-weight: 850;
    font-size: 1.05rem;
    margin-bottom: 6px;
}
.doc-intro-text {
    color: var(--app-muted);
    font-size: .86rem;
    line-height: 1.55;
}
.doc-flow-shell {
    border: 1px solid var(--app-border);
    background: var(--app-surface);
    border-radius: 10px;
    padding: 14px 14px 4px;
}
.doc-row-label {
    color: var(--app-subtle);
    font-size: .68rem;
    letter-spacing: .12em;
    text-transform: uppercase;
    font-weight: 800;
    margin: 2px 0 7px;
}
.doc-arrow {
    color: var(--app-subtle);
    text-align: center;
    font-weight: 800;
    font-size: 1.15rem;
    margin: 1px 0 5px;
}
.doc-detail {
    background: var(--app-surface);
    border: 1px solid var(--app-border);
    border-radius: 10px;
    padding: 18px 20px;
}
.doc-detail-kicker {
    color: var(--app-primary);
    font-size: .68rem;
    letter-spacing: .12em;
    text-transform: uppercase;
    font-weight: 850;
}
.doc-detail-title {
    color: var(--app-text);
    font-size: 1.25rem;
    font-weight: 900;
    margin: 4px 0 8px;
}
.doc-detail-body {
    color: var(--app-muted);
    line-height: 1.58;
    font-size: .88rem;
}
.doc-chip {
    display: inline-block;
    border: 1px solid var(--app-border);
    background: var(--app-surface-raised);
    color: var(--app-muted);
    border-radius: 999px;
    padding: 3px 9px;
    margin: 4px 5px 0 0;
    font-size: .70rem;
    font-weight: 750;
}
.doc-note {
    color: var(--app-subtle);
    font-size: .75rem;
    margin-top: 10px;
}
.doc-card-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 12px;
    margin-top: 12px;
}
.doc-card {
    border: 1px solid var(--app-border);
    background: var(--app-surface);
    border-radius: 10px;
    padding: 14px 16px;
}
.doc-card-title {
    color: var(--app-text);
    font-weight: 850;
    font-size: .92rem;
    margin-bottom: 5px;
}
.doc-card-text {
    color: var(--app-muted);
    line-height: 1.5;
    font-size: .80rem;
}
.doc-indicator-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(315px, 1fr));
    gap: 14px;
    margin-top: 14px;
}
.doc-indicator-card {
    position: relative;
    overflow: hidden;
    border: 1px solid var(--app-border);
    background: var(--app-surface);
    border-radius: 10px;
    padding: 16px 17px 15px;
    min-height: 270px;
}
.doc-indicator-card::before {
    content: "";
    position: absolute;
    left: 0;
    top: 0;
    bottom: 0;
    width: 4px;
    background: var(--accent, var(--app-primary));
}
.doc-indicator-top {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 10px;
    margin-bottom: 10px;
}
.doc-indicator-name {
    color: var(--app-text);
    font-size: 1.08rem;
    font-weight: 900;
    line-height: 1.15;
}
.doc-indicator-group {
    color: var(--accent, var(--app-primary));
    border: 1px solid color-mix(in srgb, var(--accent, var(--app-primary)) 42%, transparent);
    background: color-mix(in srgb, var(--accent, var(--app-primary)) 13%, transparent);
    border-radius: 999px;
    padding: 3px 9px;
    font-size: .64rem;
    font-weight: 850;
    text-transform: uppercase;
    letter-spacing: .08em;
    white-space: nowrap;
}
.doc-field {
    border-top: 1px solid var(--app-border);
    padding-top: 9px;
    margin-top: 9px;
}
.doc-field-label {
    color: var(--app-subtle);
    font-size: .63rem;
    font-weight: 850;
    text-transform: uppercase;
    letter-spacing: .10em;
    margin-bottom: 3px;
}
.doc-field-text {
    color: var(--app-muted);
    font-size: .80rem;
    line-height: 1.48;
}
.doc-author-note {
    color: var(--app-muted);
    font-size: .78rem;
    line-height: 1.5;
    padding: 10px 11px;
    border-radius: 8px;
    background: color-mix(in srgb, var(--app-warning) 7%, transparent);
    border: 1px solid color-mix(in srgb, var(--app-warning) 18%, transparent);
    margin-top: 10px;
}
.doc-statement-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(270px, 1fr));
    gap: 12px;
    margin-top: 12px;
}
.doc-statement-card {
    border: 1px solid var(--app-border);
    background: var(--app-surface);
    border-radius: 10px;
    padding: 15px 16px;
}
.doc-statement-title {
    color: var(--app-text);
    font-weight: 900;
    font-size: .96rem;
    margin-bottom: 9px;
}
.doc-av-shell {
    border: 1px solid var(--app-border);
    background: var(--app-surface);
    border-radius: 10px;
    padding: 16px;
}
.doc-av-flow-title {
    color: var(--app-text);
    font-size: .82rem;
    font-weight: 850;
    text-transform: uppercase;
    letter-spacing: .10em;
    margin-bottom: 12px;
}
.doc-av-arrow {
    color: var(--app-subtle);
    text-align: center;
    font-size: 1.35rem;
    font-weight: 900;
    margin: -2px 0 2px;
}
.doc-av-detail {
    border: 1px solid var(--app-border);
    background:
        linear-gradient(180deg, color-mix(in srgb, var(--app-primary) 7%, transparent), color-mix(in srgb, var(--app-info) 3%, transparent)),
        var(--app-surface);
    border-radius: 10px;
    padding: 18px 20px;
}
.doc-av-detail.score-final {
    border-color: color-mix(in srgb, var(--app-primary) 55%, transparent);
    box-shadow: 0 0 0 1px color-mix(in srgb, var(--app-primary) 13%, transparent), 0 0 34px color-mix(in srgb, var(--app-primary) 8%, transparent);
}
.doc-av-kicker {
    color: var(--app-primary);
    font-size: .68rem;
    font-weight: 900;
    letter-spacing: .12em;
    text-transform: uppercase;
    margin-bottom: 3px;
}
.doc-av-title {
    color: var(--app-text);
    font-size: 1.38rem;
    font-weight: 950;
    margin-bottom: 8px;
}
.doc-av-section {
    border-top: 1px solid var(--app-border);
    padding-top: 10px;
    margin-top: 10px;
}
.doc-av-label {
    color: var(--app-subtle);
    font-size: .65rem;
    font-weight: 900;
    letter-spacing: .10em;
    text-transform: uppercase;
    margin-bottom: 4px;
}
.doc-av-text {
    color: var(--app-muted);
    font-size: .86rem;
    line-height: 1.56;
}
.doc-av-impact {
    border: 1px solid color-mix(in srgb, var(--app-info) 22%, transparent);
    background: color-mix(in srgb, var(--app-info) 7%, transparent);
    border-radius: 9px;
    padding: 11px 12px;
    margin-top: 11px;
}
.doc-mini-title {
    color: var(--app-text);
    font-weight: 850;
    font-size: .95rem;
    margin: 18px 0 8px;
}
.stButton > button {
    border-radius: 8px;
    border: 1px solid var(--app-border);
    background: var(--app-surface);
    color: var(--app-text);
    min-height: 54px;
    font-weight: 800;
    white-space: normal;
    line-height: 1.15;
}
.stButton > button:hover {
    border-color: color-mix(in srgb, var(--app-primary) 55%, transparent);
    background: color-mix(in srgb, var(--app-primary) 10%, transparent);
    color: var(--app-text);
}
</style>
"""


@dataclass(frozen=True)
class FlowNode:
    id: str
    title: str
    layer: str
    summary: str
    contains: tuple[str, ...]
    why: str


@dataclass(frozen=True)
class FlowSpec:
    key: str
    title: str
    subtitle: str
    rows: tuple[tuple[str, tuple[str, ...]], ...]
    nodes: dict[str, FlowNode]
    default: str
    notes: tuple[str, ...] = ()


def _node(
    id_: str,
    title: str,
    layer: str,
    summary: str,
    contains: tuple[str, ...],
    why: str,
) -> FlowNode:
    return FlowNode(id_, title, layer, summary, contains, why)


FLOW_ANALISE_AVANCADA = FlowSpec(
    key="analise_avancada",
    title="Análise avançada de empresas B3",
    subtitle=(
        "Mostra como o app transforma dados brutos de empresas em score comparável, "
        "ranking, simulação histórica e uma leitura de entrada."
    ),
    rows=(
        ("Universo", ("setores", "multiplos", "dre_macro")),
        ("Tratamento", ("limpeza", "slopes", "pesos_setoriais")),
        ("Score", ("percentis", "ajustes", "score_final")),
        ("Validação", ("backtest", "calibracao", "score_entrada")),
        ("Saída", ("ranking", "explicacao")),
    ),
    default="setores",
    nodes={
        "setores": _node(
            "setores", "Setores e segmentos", "Entrada",
            "Carrega o cadastro de empresas e agrupa cada ticker por setor, subsetor e segmento.",
            (
                "Tabela de setores do Supabase",
                "Ticker, empresa, setor, subsetor e segmento",
                "Base para comparar empresas com pares semelhantes",
            ),
            "Sem agrupamento setorial, bancos, varejo, energia e tecnologia seriam comparados como se tivessem a mesma estrutura econômica.",
        ),
        "multiplos": _node(
            "multiplos", "Múltiplos históricos", "Entrada",
            "Busca indicadores fundamentalistas anuais, com fallback web quando o banco tem lacunas ou outliers.",
            (
                "ROE, ROIC, margens, DY, P/L, P/VP, EV/EBIT",
                "Histórico por ano",
                "Auditoria de campos substituidos por Fundamentus",
            ),
            "Os múltiplos são a primeira camada quantitativa: condensam preço, lucro, patrimônio, dividendos e rentabilidade do capital.",
        ),
        "dre_macro": _node(
            "dre_macro", "DRE e macro", "Entrada",
            "Combina demonstrações financeiras e contexto macroeconômico usado nos ajustes de qualidade e risco.",
            (
                "Receita, EBITDA, EBIT, lucro, dívida e caixa",
                "Selic, IPCA, câmbio e PIB",
                "Histórico com publication lag para evitar olhar o futuro",
            ),
            "A empresa não existe no vácuo: crescimento, margem e endividamento precisam ser lidos junto com juros, inflação e ciclo econômico.",
        ),
        "limpeza": _node(
            "limpeza", "Limpeza e saneamento", "Preparação",
            "Remove valores impossíveis, padroniza escalas percentuais e reduz distorções de dados contaminados.",
            (
                "Faixas aceitáveis por indicador",
                "DY contaminado ou fora de escala",
                "Imputação por mediana do grupo quando há lacunas",
            ),
            "Evita que uma empresa ganhe ou perca score por erro de dado, e não por qualidade econômica real.",
        ),
        "slopes": _node(
            "slopes", "Tendências históricas", "Preparação",
            "Calcula slopes log-lineares para medir a direção de ROE, ROIC e margens ao longo do tempo.",
            (
                "ROE_slope_log",
                "ROIC_slope_log",
                "Margem_Líquida_slope_log",
                "Margem_Operacional_slope_log",
            ),
            "Uma foto atual pode enganar; a tendência mostra se a qualidade esta melhorando, piorando ou apenas parecendo boa.",
        ),
        "pesos_setoriais": _node(
            "pesos_setoriais", "Pesos por setor", "Preparação",
            "Escolhe pesos diferentes por tipo de negócio: financeiro, energia, consumo, saúde, utilidade publica e outros.",
            (
                "ROE mais relevante em bancos",
                "DY e endividamento mais fortes em utilities",
                "ROIC e margens mais importantes em negócios industriais",
                "Peso de barganha opcional: P/L, P/VP, EV/EBIT (menor é melhor)",
            ),
            "O mesmo indicador não tem o mesmo significado em todos os setores; a ponderação respeita a economia de cada negócio, e o peso de barganha permite misturar qualidade com preço (comprar boa empresa barata).",
        ),
        "percentis": _node(
            "percentis", "Percentis entre pares", "Score",
            "Converte cada indicador em posição relativa dentro do grupo comparável.",
            (
                "Rank percentual",
                "Indicadores em que maior é melhor",
                "Indicadores em que menor é melhor, como P/L, P/VP, EV/EBIT e endividamento",
            ),
            "A pergunta principal vira: esta empresa é melhor ou pior que seus pares no indicador certo?",
        ),
        "ajustes": _node(
            "ajustes", "Ajustes de risco", "Score",
            "Aplica penalidades e ajustes para reduzir concentração, dados frágeis, crowding e sensibilidade macro.",
            (
                "Winsorização",
                "Penalidade por valores extremos ou dados insuficientes",
                "Ajuste macro e crowding em múltiplos",
            ),
            "A camada protege o ranking contra histórias bonitas demais que dependem de uma única variável ou de um dado instável.",
        ),
        "score_final": _node(
            "score_final", "Score final", "Score",
            "Agrega os indicadores ponderados em uma nota de 0 a 100 para ordenar as empresas.",
            (
                "Score bruto",
                "Score ajustado",
                "Versão do score para auditoria",
            ),
            "O score não substitui análise, mas cria uma triagem objetiva e repetível para encontrar candidatos.",
        ),
        "backtest": _node(
            "backtest", "Backtest mensal", "Validação",
            "Simula aportes mensais usando os scores disponíveis no período correto, sem usar dados futuros.",
            (
                "Publication lag = 1 (point-in-time)",
                "Aportes mensais com custos",
                "Pesos Iguais = referência de habilidade (macro-neutra)",
                "Selic = diagnóstico de timing (não critério)",
                "Rank-IC: score preve o retorno do ano seguinte",
            ),
            "Bater os Pesos Iguais mostra habilidade de seleção mesmo em ciclo ruim (se o setor caiu, os pares cairam junto); a Selic é apenas referência de timing, não de qualidade.",
        ),
        "calibracao": _node(
            "calibracao", "Calibração", "Validação",
            "Testa parâmetros de peso, limite máximo e suavização para evitar carteiras concentradas ou superajustadas.",
            (
                "Gamma",
                "Cap por ativo",
                "Soft cap",
                "Walk-forward e shrinkage para defaults",
            ),
            "A calibração tenta equilibrar retorno, volatilidade, drawdown e custos de transação.",
        ),
        "score_entrada": _node(
            "score_entrada", "Score de entrada", "Validação",
            "Combina qualidade, valor, risco e contexto macro para classificar o momento de compra.",
            (
                "Composição avançada",
                "Status de entrada",
                "Explicação textual da nota",
            ),
            "Uma boa empresa pode estar cara, alavancada ou em momento ruim; o score de entrada separa qualidade de oportunidade.",
        ),
        "ranking": _node(
            "ranking", "Ranking e líderes", "Saída",
            "Exibe as empresas mais fortes por segmento e permite auditoria dos motivos.",
            (
                "Tabela comparativa",
                "Líderes por score",
                "Indicadores que mais puxaram a nota",
            ),
            "O usuário sai da caixa-preta e consegue ver por que uma empresa apareceu acima de outra.",
        ),
        "explicacao": _node(
            "explicacao", "Explicação visual", "Saída",
            "Mostra tabelas, gráficos, status e alertas para transformar cálculo em entendimento.",
            (
                "Gráficos Plotly",
                "Cards de status",
                "Auditorias de dados e parâmetros",
            ),
            "A tela existe para que o usuário consiga discordar do modelo com informação, não apenas aceitar um número.",
        ),
    },
)


ETAPAS_ANALISE_AVANCADA = {
    "entrada_dados": {
        "titulo": "Entrada dos dados",
        "objetivo": "Reunir as bases que alimentam a análise avançada antes de qualquer cálculo de score.",
        "dados": "Cadastro de empresas B3, tickers, setores, subsetores, segmentos, múltiplos, DRE, preços históricos e variáveis macroeconômicas.",
        "formula": "Base de análise = empresas elegíveis + indicadores financeiros + preços + macro",
        "exemplo": "Tickers carregados: 420\nTickers com setor definido: 410\nTickers com histórico mínimo: 280\n\nUniverso inicial analisável = 280 empresas",
        "interpretacao": "A etapa define o universo disponível para comparação. Empresas sem dados mínimos podem ficar fora da análise quantitativa.",
        "impacto": "Quanto melhor a cobertura dos dados, mais confiável tende a ser a comparação entre empresas.",
        "limitacao": "Dados ausentes, atrasados ou inconsistentes reduzem a cobertura e podem deixar empresas relevantes fora do cálculo.",
    },
    "setores_segmentos": {
        "titulo": "Setores e segmentos",
        "objetivo": "Agrupar empresas em conjuntos comparáveis antes de normalizar indicadores.",
        "dados": "SETOR, SUBSETOR, SEGMENTO, ticker e nome da empresa, vindos da base setorial do App4.",
        "formula": "Grupo comparável = setor -> subsetor -> segmento\n\nComparação preferencial: segmento\nFallback: subsetor ou setor quando o grupo é pequeno",
        "exemplo": "Empresa A: Utilidade Pública > Energia Elétrica > Distribuição\n\nEla deve ser comparada com distribuidoras de energia, não com bancos ou varejistas.",
        "interpretacao": "Empresas de modelos econômicos parecidos são avaliadas lado a lado, preservando diferenças estruturais entre setores.",
        "impacto": "Define quais pares entram nos percentis, nas medianas e nas ponderações do score.",
        "limitacao": "Classificações setoriais muito amplas ou incorretas podem distorcer a comparação.",
    },
    "multiplos_historicos": {
        "titulo": "Múltiplos históricos",
        "objetivo": "Trazer o histórico anual de indicadores fundamentalistas usados na análise quantitativa.",
        "dados": "ROE, ROIC, ROA, margens, DY, P/L, P/VP, EV/EBIT, P/FCO, endividamento, liquidez e payout.",
        "formula": "Snapshot anual do indicador = último valor disponível até o ano de referência permitido pelo lag de publicação",
        "exemplo": "Ano de compra: 2024\nLag de publicação: 1 ano\n\nIndicadores usados no score de 2024: dados disponíveis até 2023",
        "interpretacao": "O modelo tenta simular uma decisão realista, usando somente informações que estariam disponíveis no momento da análise.",
        "impacto": "Fornece a matéria-prima para normalização, percentis, pesos e cálculo do score final.",
        "limitacao": "Múltiplos podem sofrer distorções por lucro não recorrente, mudança contábil, eventos extraordinários ou erro de escala.",
    },
    "dre_macro": {
        "titulo": "DRE e dados macroeconômicos",
        "objetivo": "Complementar múltiplos com fundamentos operacionais e contexto econômico.",
        "dados": "Receita líquida, EBITDA, EBIT, lucro líquido, dívida, caixa, Selic, IPCA, câmbio e PIB.",
        "formula": "Leitura fundamental = desempenho operacional + estrutura financeira + ambiente macro",
        "exemplo": "Receita cresce 8%\nLucro cresce 2%\nSelic sobe de 10% para 13%\n\nInterpretação: crescimento existe, mas margem e custo financeiro precisam ser observados.",
        "interpretacao": "A DRE mostra a qualidade da operação; a macro ajuda a entender juros, inflação, câmbio e ciclo econômico.",
        "impacto": "Afeta leituras de crescimento, rentabilidade, risco financeiro e sensibilidade macro do score.",
        "limitacao": "Macro ajuda a contextualizar, mas não deve substituir a análise específica da empresa.",
    },
    "limpeza_saneamento": {
        "titulo": "Limpeza e saneamento",
        "objetivo": "Proteger o modelo contra dados nulos, inconsistentes, contaminados ou extremos.",
        "dados": "Todos os indicadores numéricos usados no score, com validação de faixas aceitáveis por indicador.",
        "formula": """Se indicador = nulo ou inconsistente:
    excluir do cálculo daquele período
ou
    substituir pela mediana setorial, quando aplicável

Se indicador estiver fora da faixa aceitável:
    tratar como ausente ou limitar pela regra de saneamento""",
        "exemplo": """Empresa X possui DY = 180%
Faixa aceitável para DY = até 50%

Resultado:
DY é tratado como inconsistente e não entra no cálculo daquele período.""",
        "interpretacao": "A etapa protege o modelo contra distorções provocadas por dados incompletos ou fora do padrão.",
        "impacto": "Reduz pontuações artificiais e evita que erros de base virem vantagem ou punição indevida.",
        "limitacao": "Substituir pela mediana setorial preserva cobertura, mas pode suavizar diferenças reais entre empresas.",
    },
    "tendencias_historicas": {
        "titulo": "Tendências históricas",
        "objetivo": "Medir se indicadores de qualidade estão melhorando ou piorando ao longo do tempo.",
        "dados": "Séries históricas de ROE, ROIC, margem líquida e margem operacional.",
        "formula": "Variação percentual = ((Valor atual - Valor anterior) / Valor anterior) × 100",
        "exemplo": """ROE 2023 = 12%
ROE 2024 = 15%

Variação = ((15 - 12) / 12) × 100
Variação = 25%""",
        "interpretacao": "A empresa apresentou melhora histórica no indicador analisado.",
        "impacto": "Tendências positivas podem reforçar a qualidade do score; tendências negativas reduzem confiança na nota atual.",
        "limitacao": "Uma melhora curta pode ser cíclica ou não recorrente. A tendência deve ser lida junto com DRE e setor.",
    },
    "pesos_setor": {
        "titulo": "Pesos por setor",
        "objetivo": "Aplicar pesos diferentes para indicadores conforme a natureza econômica de cada setor.",
        "dados": "Indicadores normalizados e matriz de pesos setoriais do App4.",
        "formula": "Score parcial = Indicador normalizado × Peso do indicador",
        "exemplo": """Margem líquida normalizada = 80
Peso da margem líquida = 20%

Contribuição no score = 80 × 0,20 = 16 pontos""",
        "interpretacao": "O indicador contribuiu com 16 pontos para o score final da empresa.",
        "impacto": "Setores diferentes dão importância diferente a rentabilidade, dividendos, endividamento, margens e valuation.",
        "limitacao": "Pesos são uma escolha de modelo. Eles organizam a análise, mas não capturam todas as particularidades de uma empresa.",
    },
    "percentis_pares": {
        "titulo": "Percentis entre pares",
        "objetivo": "Converter indicadores em posição relativa dentro de um grupo comparável.",
        "dados": "Indicadores normalizados das empresas do mesmo setor, subsetor ou segmento.",
        "formula": "Percentil = posição relativa da empresa dentro do grupo comparável",
        "exemplo": """Empresa analisada está melhor que 92 empresas
dentro de um grupo de 100 empresas do mesmo setor.

Percentil = 92""",
        "interpretacao": "A empresa está melhor que aproximadamente 92% dos pares comparáveis naquele indicador.",
        "impacto": "Transforma indicadores com escalas diferentes em uma régua comum de 0 a 100.",
        "limitacao": "A comparação deve ocorrer dentro do setor, subsetor ou segmento, e não contra todas as empresas da bolsa.",
    },
    "ajustes_risco": {
        "titulo": "Ajustes de risco",
        "objetivo": "Reduzir o score quando há sinais de risco financeiro, instabilidade ou dado frágil.",
        "dados": "Endividamento, liquidez, volatilidade histórica dos indicadores, qualidade dos dados e sensibilidade macro.",
        "formula": "Score ajustado = Score bruto - Penalidade de risco",
        "exemplo": """Score bruto = 82
Penalidade por alto endividamento = 7

Score ajustado = 82 - 7 = 75""",
        "interpretacao": "Mesmo com bons indicadores operacionais, a empresa perde pontuação por apresentar risco financeiro maior.",
        "impacto": "Evita que empresas aparentemente baratas ou rentáveis recebam nota alta sem considerar fragilidade.",
        "limitacao": "Penalidades simplificam riscos complexos. Governança, litígios e riscos qualitativos podem não aparecer totalmente.",
    },
    "score_final": {
        "titulo": "Score final",
        "objetivo": "Consolidar indicadores tratados em uma nota comparável.",
        "dados": "Rentabilidade, crescimento, endividamento, eficiência, valuation, pesos setoriais e penalidades de risco.",
        "formula": "Score final = ∑(Indicador normalizado × Peso) - Penalidades",
        "exemplo": """Rentabilidade: 85 × 30% = 25,5
Crescimento: 70 × 20% = 14,0
Endividamento: 60 × 20% = 12,0
Eficiência: 75 × 15% = 11,25
Valuation: 65 × 15% = 9,75

Score bruto = 72,5
Penalidade de risco = 5

Score final = 72,5 - 5
Score final = 67,5""",
        "interpretacao": "A empresa recebeu score final de 67,5 em uma escala comparativa. Isso indica posição intermediária/positiva dentro do universo analisado, mas não deve ser lido isoladamente como recomendação de compra.",
        "impacto": "Define a posição relativa da empresa dentro do modelo e orienta rankings, backtests e leituras de entrada.",
        "limitacao": "O score não substitui análise fundamentalista, leitura qualitativa, avaliação de preço, liquidez, governança e contexto macroeconômico.",
    },
    "backtest_mensal": {
        "titulo": "Backtest mensal",
        "objetivo": "Verificar como empresas selecionadas pelo score teriam se comportado historicamente.",
        "dados": "Preços mensais, dividendos quando disponíveis, score histórico com publication lag e benchmarks como Selic/equal-weight.",
        "formula": "Retorno mensal = ((Preço final - Preço inicial) / Preço inicial) × 100",
        "exemplo": """Preço inicial = R$ 20,00
Preço final = R$ 22,00

Retorno mensal = ((22 - 20) / 20) × 100
Retorno mensal = 10%""",
        "interpretacao": "O backtest verifica se empresas com scores mais altos apresentaram desempenho superior ao longo do tempo analisado.",
        "impacto": "Ajuda a avaliar se o score tem utilidade prática ou apenas organiza dados retrospectivos.",
        "limitacao": "Backtest não garante resultado futuro e pode sofrer com survivorship bias, custos, liquidez e mudanças estruturais.",
    },
    "calibracao": {
        "titulo": "Calibração do modelo",
        "objetivo": "Ajustar parâmetros para equilibrar retorno, risco, concentração e robustez.",
        "dados": "Resultados de backtest, volatilidade, drawdown, custos estimados, limites de peso e parâmetros gamma/cap/soft.",
        "formula": "Objetivo simplificado = CAGR - penalidade de volatilidade - penalidade de drawdown - custos",
        "exemplo": """CAGR = 18%
Penalidade de volatilidade = 5%
Penalidade de drawdown = 4%
Custos estimados = 1%

Objetivo = 18 - 5 - 4 - 1 = 8""",
        "interpretacao": "O melhor parâmetro não é necessariamente o que mais rendeu, mas o que melhor equilibrou retorno e risco.",
        "impacto": "Define pesos finais, limite de concentração e intensidade com que scores maiores recebem mais alocação.",
        "limitacao": "Calibrar demais pode gerar overfitting. O modelo usa shrinkage e walk-forward para reduzir esse risco.",
    },
    "score_entrada": {
        "titulo": "Score de entrada",
        "objetivo": "Transformar o score e os ajustes em uma régua interpretativa para priorizar análise.",
        "dados": "Score final, qualidade, valuation, risco, cenário macro e composição avançada.",
        "formula": """Score final >= 80: entrada forte
Score final entre 65 e 79: entrada moderada
Score final entre 50 e 64: observação
Score final < 50: evitar ou aguardar melhora""",
        "exemplo": """Score final = 67,5

Classificação:
67,5 está entre 65 e 79
Entrada moderada""",
        "interpretacao": "Essa classificação organiza prioridades de análise, mas não representa recomendação automática de compra.",
        "impacto": "Ajuda o usuário a separar oportunidades mais fortes, casos de observação e empresas que exigem cautela.",
        "limitacao": "A régua depende de dados quantitativos e deve ser combinada com liquidez, governança, preço atual e tese qualitativa.",
    },
    "leitura_final": {
        "titulo": "Leitura final / sugestões",
        "objetivo": "Converter o resultado técnico em uma explicação prática para o usuário.",
        "dados": "Score final, score de entrada, ranking, alertas, backtest, dados financeiros e contexto macro.",
        "formula": "Sugestão de leitura = score + risco + contexto + validação histórica + julgamento qualitativo",
        "exemplo": """Score final = 67,5
Entrada = moderada
Backtest = positivo
Risco = endividamento acima da média

Leitura: boa candidata para estudo, mas exige atenção ao balanço.""",
        "interpretacao": "A etapa final não compra nem vende automaticamente; ela organiza evidências para uma análise mais consciente.",
        "impacto": "Melhora a transparência do modelo e ajuda o usuário a entender por que uma empresa aparece como prioridade.",
        "limitacao": "Sugestões são apoio analítico. Decisão final exige análise própria, perfil de risco e objetivos do investidor.",
    },
}


ORDEM_ANALISE_AVANCADA = (
    ("entrada_dados", "Entrada dos dados"),
    ("setores_segmentos", "Setores e segmentos"),
    ("multiplos_historicos", "Múltiplos históricos"),
    ("dre_macro", "DRE e dados macroeconômicos"),
    ("limpeza_saneamento", "Limpeza e saneamento"),
    ("tendencias_historicas", "Tendências históricas"),
    ("pesos_setor", "Pesos por setor"),
    ("percentis_pares", "Percentis entre pares"),
    ("ajustes_risco", "Ajustes de risco"),
    ("score_final", "Score final"),
    ("backtest_mensal", "Backtest mensal"),
    ("calibracao", "Calibração do modelo"),
    ("score_entrada", "Score de entrada"),
    ("leitura_final", "Leitura final / sugestões"),
)


FLOW_CRIACAO_PORTFOLIO = FlowSpec(
    key="criacao_portfolio",
    title="Criação de portfólio B3",
    subtitle=(
        "Fluxo inspirado nos seus rascunhos: setores, subsetores e segmentos entram no motor; "
        "o app encontra líderes, testa desempenho e salva uma carteira modelo."
    ),
    rows=(
        ("Dados", ("setores_cp", "historico_cp", "macro_cp")),
        ("Segmentação", ("setor_cp", "subsetor_cp", "segmento_cp")),
        ("Motor", ("variacao_cp", "score_cp", "lideres_cp")),
        ("Simulação", ("backtest_cp", "comparacao_cp", "aprovacao_cp")),
        ("Portfólio", ("pesos_cp", "salvar_cp")),
    ),
    default="setores_cp",
    nodes={
        "setores_cp": _node(
            "setores_cp", "Escolha do universo", "Dados",
            "Carrega todas as empresas B3 cobertas e organiza por setor, subsetor e segmento.",
            ("load_setores()", "Tickers elegíveis", "Nome da empresa e classificação setorial"),
            "E o ponto de partida para que cada empresa seja julgada dentro de um grupo econômico justo.",
        ),
        "historico_cp": _node(
            "historico_cp", "Histórico de indicadores", "Dados",
            "Busca múltiplos e DRE históricos para cada ticker, exigindo um mínimo de anos validos.",
            ("load_múltiplos_todos()", "load_múltiplos_histórico_batch()", "Histórico DRE mínimo"),
            "Sem histórico suficiente, o modelo evita aprovar segmentos que parecem bons por uma única observação.",
        ),
        "macro_cp": _node(
            "macro_cp", "Cenário macro", "Dados",
            "Carrega Selic e demais variáveis macro para simular benchmark e ajustar o score.",
            ("load_selic_macro()", "load_macro_history()", "Taxa Selic média de fallback"),
            "A comparação contra Selic e essencial porque o investidor brasileiro sempre tem uma alternativa de renda fixa.",
        ),
        "setor_cp": _node(
            "setor_cp", "Setor", "Segmentação",
            "Primeiro nível de agrupamento: bancos, energia, consumo, materiais, saúde e outros.",
            ("Pesos setoriais", "Comparação ampla", "Contexto de negócio"),
            "Define quais indicadores recebem mais peso.",
        ),
        "subsetor_cp": _node(
            "subsetor_cp", "Subsetor", "Segmentação",
            "Nível intermediário que refina empresas com dinâmicas econômicas parecidas.",
            ("Grupo operacional", "Filtro de comparabilidade", "Fallback quando segmento e pequeno"),
            "Ajuda a evitar comparações grosseiras dentro de setores grandes.",
        ),
        "segmento_cp": _node(
            "segmento_cp", "Segmento", "Segmentação",
            "Menor unidade do motor: cada segmento passa por score, líderes e backtest.",
            ("Tickers do segmento", "Score anual", "Histórico de liderança"),
            "E a camada mais próxima do desenho manual: segmento gera variáveis, score, empresas e líder.",
        ),
        "variacao_cp": _node(
            "variacao_cp", "Variáveis do segmento", "Motor",
            "Seleciona indicadores relevantes e calcula score ano a ano com lag de "
            "publicação, respeitando a data em que cada dado ficou disponível (point-in-time).",
            ("Pesos do setor", "Snapshot até N-1", "AvailableAt (vintages) <= abril do ano", "Indicadores saneados"),
            "Garante que o modelo de compra em um ano só use dados que já existiam "
            "naquela data — sem look-ahead bias.",
        ),
        "score_cp": _node(
            "score_cp", "Score e pesos", "Motor",
            "Ordena empresas, aplica penalidade de liderança recorrente e calcula pesos proporcionais ao score.",
            ("Decay penalty", "Heurística top-N", "Gamma tilt", "Cap e soft cap"),
            "O objetivo e escolher líderes sem deixar a carteira virar uma aposta concentrada em uma única empresa.",
        ),
        "lideres_cp": _node(
            "lideres_cp", "Líderes", "Motor",
            "Identifica a melhor empresa, e opcionalmente a maior participação histórica quando ainda faz sentido.",
            ("Líder por score", "Maior participação", "Recência de liderança", "Rank atual"),
            "Une desempenho quantitativo com continuidade histórica do segmento.",
        ),
        "backtest_cp": _node(
            "backtest_cp", "Simulação mensal", "Simulação",
            "Reconstrui aportes mensais nos líderes de cada ano e reinveste dividendos quando há dados.",
            ("Preços mensais yfinance", "Dividendos mensais", "Aporte mensal", "Rebalanceamento anual dos novos aportes"),
            "Transforma a ideia em uma trilha de patrimônio acumulado.",
        ),
        "comparacao_cp": _node(
            "comparacao_cp", "Comparação", "Simulação",
            "Compara o patrimônio da estratégia com Tesouro Selic e equal-weight do próprio "
            "segmento — tanto no histórico cheio quanto no holdout final de ~24 meses, que é a base da aprovação.",
            ("Valor estratégia", "Valor Selic", "Valor equal-weight", "Margens no histórico", "Margens no holdout OOS ~24m"),
            "Uma empresa líder precisa provar valor contra alternativas simples — e, sobretudo, "
            "fora da janela usada para desenvolver a estratégia.",
        ),
        "aprovacao_cp": _node(
            "aprovacao_cp", "Aprovação do segmento", "Simulação",
            "Aprova por HABILIDADE DE SELEÇÃO: bater o Equal-Weight do próprio "
            "segmento com significância estatística no holdout OOS de ~24 meses. "
            "Neutro ao macro — se o cenário derrubou o segmento todo, o EW caiu junto.",
            ("Significância vs Equal-Weight (p-value OOS + FDR q <= 10%)",
             "Rank-IC >= 2 anos positivo (qualidade preve retorno)",
             "Margem vs EW (piso de magnitude opcional)",
             "Margem vs Selic = DIAGNÓSTICO (não reprova)", "Recência de liderança"),
            "Só entram segmentos cujos líderes superaram os pares (habilidade), com "
            "evidência preditiva (Rank-IC) e significância fora da amostra. Bater a "
            "Selic e decisão de timing do investidor, não critério de qualidade.",
        ),
        "pesos_cp": _node(
            "pesos_cp", "Montagem do portfólio", "Portfólio",
            "Remove duplicatas, consolida motivos e distribui empresas selecionadas por peso e setor.",
            ("Lista de empresas líderes", "Score médio", "Alpha médio", "Distribuição setorial"),
            "E a transição do motor por segmento para uma carteira única e acionável.",
        ),
        "salvar_cp": _node(
            "salvar_cp", "Salvar modelo", "Portfólio",
            "Persiste a carteira sugerida como portfólio B3 ativo do usuário.",
            ("b3_portfolio_models", "b3_portfolio_model_items", "Parâmetros e métricas JSON"),
            "Esse registro vira a base da análise qualitativa e aparece no Dashboard Geral.",
        ),
    },
)


FLOW_SIMULADOR = FlowSpec(
    key="simulador_portfolio",
    title="Modelo de simulação de portfólio",
    subtitle=(
        "Mostra como o app transforma líderes por segmento em trajetórias de patrimônio, "
        "com aportes, dividendos, benchmarks e regras de aprovação."
    ),
    rows=(
        ("Preparação", ("precos_sp", "dividendos_sp", "aportes_sp")),
        ("Carteiras paralelas", ("estrategia_sp", "selic_sp", "equal_weight_sp")),
        ("Tempo", ("rebalance_sp", "cotas_sp", "custos_sp")),
        ("Resultado", ("montante_sp", "margem_sp", "stress_sp")),
    ),
    default="precos_sp",
    nodes={
        "precos_sp": _node(
            "precos_sp", "Preços mensais", "Preparação",
            "Le fechamentos mensais AJUSTADOS (retorno total) do banco market.* "
            "(market.historical_prices); cai no yfinance só se o market.* não estiver ativo.",
            ("_batch_yf_preços_mensais()", "Colunas por ticker", "adjusted_close (retorno total)"),
            "Preço ajustado é a ponte entre score teórico e retorno realmente simulado.",
        ),
        "dividendos_sp": _node(
            "dividendos_sp", "Dividendos", "Preparação",
            "Não há passo separado de dividendos: o preço ajustado (adjusted_close) já "
            "embute proventos e splits reinvestidos, evitando dupla contagem.",
            ("adjusted_close", "Proventos já embutidos", "Sem reinvestimento duplicado"),
            "Reinvestir dividendos por cima do preço ajustado contaria os proventos duas vezes.",
        ),
        "aportes_sp": _node(
            "aportes_sp", "Aporte mensal", "Preparação",
            "Todo mês o simulador injeta novo capital na estratégia, Selic e equal-weight.",
            ("Aporte configurável", "Cotas compradas", "Mês a mês"),
            "A simulação representa acumulação recorrente, não apenas uma compra única.",
        ),
        "estrategia_sp": _node(
            "estrategia_sp", "Estratégia", "Carteiras paralelas",
            "Compra os líderes definidos pelo score do segmento, com pesos ajustados por score e limites.",
            ("Líderes por ano", "Pesos por score", "Cap por ativo", "Soft cap"),
            "Mostra o resultado da tese principal do modelo.",
        ),
        "selic_sp": _node(
            "selic_sp", "Tesouro Selic", "Carteiras paralelas",
            "Acumula o mesmo aporte pela taxa Selic mensalizada de cada ano.",
            ("Selic anual", "Taxa mensal equivalente", "Benchmark de baixo risco"),
            "E a barra mínima para justificar risco de ações no contexto brasileiro.",
        ),
        "equal_weight_sp": _node(
            "equal_weight_sp", "Equal-weight", "Carteiras paralelas",
            "Distribui aportes igualmente entre todos os ativos disponíveis do segmento.",
            ("Todos os tickers do segmento", "Mesmo peso", "Benchmark simples"),
            "Se o score não vence uma regra simples, talvez ele esteja apenas complicando o óbvio.",
        ),
        "rebalance_sp": _node(
            "rebalance_sp", "Virada de ano", "Tempo",
            "No ano novo, o motor recalcula os líderes com dados disponíveis até o ano anterior.",
            ("Publication lag", "Troca de líderes", "Novos pesos para novos aportes"),
            "Evita usar demonstrações financeiras que ainda não tinham sido publicadas.",
        ),
        "cotas_sp": _node(
            "cotas_sp", "Cotas acumuladas", "Tempo",
            "O simulador acumula quantidade de ações por ticker e marca a mercado no fim da série.",
            ("Cotas da estratégia", "Cotas equal-weight", "Valor final por ticker"),
            "Permite ver quais empresas explicaram o patrimônio final.",
        ),
        "custos_sp": _node(
            "custos_sp", "Custos e limites", "Tempo",
            "A análise avançada também possui suporte para overhead de transação, limites e Markowitz.",
            ("Corretagem/spread/IR estimados", "Cap de concentração", "Min-variance híbrido"),
            "Custos e concentração impedem que o backtest fique bonito demais e pouco executável.",
        ),
        "montante_sp": _node(
            "montante_sp", "Montante final", "Resultado",
            "Calcula o valor acumulado de cada carteira paralela no fim da simulação.",
            ("Valor estratégia", "Valor Selic", "Valor equal-weight", "Contribuição por ativo"),
            "E o número que aparece no desenho como montante antes da comparação.",
        ),
        "margem_sp": _node(
            "margem_sp", "Margens", "Resultado",
            "Transforma montantes em alpha percentual para aprovar ou reprovar segmentos.",
            ("Alpha vs Selic", "Alpha vs equal-weight", "Tabela de auditoria"),
            "Ajuda o usuário a entender não só quem ganhou, mas por quanto ganhou.",
        ),
        "stress_sp": _node(
            "stress_sp", "Stress tests", "Resultado",
            "Na aba Análise de Investimentos, a carteira atual também pode passar por choques históricos.",
            ("Cenários adversos", "Perda estimada", "Tempo de recuperação"),
            "E a ponte entre retorno esperado e risco suportável.",
        ),
    },
)


FLOW_ANALISE_PORTFOLIO = FlowSpec(
    key="analise_portfolio",
    title="Análise qualitativa de portfólio B3",
    subtitle=(
        "Explica como a carteira salva e enriquecida com dados, documentos e LLM para gerar relatório, "
        "redistribuição de pesos e conversa com o portfólio."
    ),
    rows=(
        ("Base", ("modelo_ap", "items_ap", "macro_ap")),
        ("Enriquecimento", ("multiplos_ap", "dre_ap", "rag_ap")),
        ("LLM", ("empresa_ap", "portfolio_ap", "json_ap")),
        ("Decisão", ("pesos_ap", "relatorio_ap", "chat_ap")),
    ),
    default="modelo_ap",
    nodes={
        "modelo_ap": _node(
            "modelo_ap", "Portfólio salvo", "Base",
            "Carrega o portfólio B3 ativo salvo na criação de portfólio.",
            ("load_active_b3_portfólio_model()", "Parâmetros", "Métricas", "Ano-base"),
            "Sem uma carteira modelo salva, a análise qualitativa não tem composição para avaliar.",
        ),
        "items_ap": _node(
            "items_ap", "Empresas e pesos", "Base",
            "Organiza cada ativo com ticker, nome, setor, peso, score e alpha histórico.",
            ("Itens do modelo", "Pesos originais", "Score quantitativo", "Alpha vs Selic"),
            "Essa é a fotografia quantitativa antes de chamar a camada qualitativa.",
        ),
        "macro_ap": _node(
            "macro_ap", "Macro atual", "Base",
            "Exibe e injeta no prompt Selic, IPCA, câmbio, PIB e variações recentes.",
            ("load_macro_history()", "Cards macro", "Contexto para sensibilidade setorial"),
            "A mesma carteira pode ser excelente ou perigosa dependendo do regime de juros, inflação e câmbio.",
        ),
        "multiplos_ap": _node(
            "multiplos_ap", "Múltiplos recentes", "Enriquecimento",
            "Carrega histórico de múltiplos de cada empresa para o prompt e para auditoria.",
            ("load_múltiplos_histórico_batch()", "Últimos 3 anos", "ROE, ROIC, margens, DY, valuation"),
            "Da ao LLM a base numérica de rentabilidade, preço e balanço.",
        ),
        "dre_ap": _node(
            "dre_ap", "DRE", "Enriquecimento",
            "Busca demonstrações financeiras por empresa para mostrar crescimento, lucro, EBITDA e dívida.",
            ("load_financials_batch()", "Receita", "EBITDA", "Lucro", "Dívida"),
            "Ajuda a diferenciar empresa barata de empresa deteriorando.",
        ),
        "rag_ap": _node(
            "rag_ap", "Documentos CVM/IPE", "Enriquecimento",
            "Recupera trechos relevantes de documentos corporativos para enriquecer a análise.",
            ("retrieve_chunks()", "format_rag_context()", "Cobertura documental"),
            "Acrescenta fatos textuais que não aparecem nos múltiplos, como eventos, riscos e comunicados.",
        ),
        "empresa_ap": _node(
            "empresa_ap", "Análise por empresa", "LLM",
            "Chama o modelo para cada ativo e pede perspectiva, riscos, catalisadores, confiança e alocação sugerida.",
            ("analisar_empresa()", "JSON estruturado", "Perspectiva forte/moderada/fraca", "Ação sugerida"),
            "Transforma dados quantitativos em uma tese legível e comparável por ativo.",
        ),
        "portfolio_ap": _node(
            "portfolio_ap", "Análise consolidada", "LLM",
            "Depois das empresas, o LLM avalia o portfólio como conjunto.",
            ("analisar_portfólio()", "Qualidade da carteira", "Perspectiva 12m", "Pontos fortes e fracos"),
            "Uma boa lista de empresas não garante uma boa carteira; o conjunto precisa ser coerente.",
        ),
        "json_ap": _node(
            "json_ap", "Fallback e validação", "LLM",
            "A resposta e parseada como JSON; se falhar, o app usa fallback estruturado para não quebrar a tela.",
            ("_parse_json()", "Fallback empresa", "Fallback portfólio"),
            "Mantém a experiência estável mesmo quando a IA responde fora do formato esperado.",
        ),
        "pesos_ap": _node(
            "pesos_ap", "Redistribuição", "Decisão",
            "Combina score quantitativo, score qualitativo, confiança, alpha, perspectiva e a "
            "corroboração entre banco e web para sugerir novos pesos.",
            ("60% quanti + 40% quali", "Multiplicador por perspectiva",
             "Modelo único (sem escolha de modo)", "Corroboração banco x web",
             "Min 2% e max 25% por ativo"),
            "Ajuda a transformar análise em ação: manter, aumentar, reduzir ou revisar.",
        ),
        "relatorio_ap": _node(
            "relatorio_ap", "Relatório", "Decisão",
            "Mostra síntese executiva, papel dos ativos, riscos, catalisadores e conclusão estratégica.",
            ("Relatório consolidado", "Cards de alocação", "Tags de riscos e catalisadores"),
            "Entrega uma leitura de gestor, não apenas uma tabela.",
        ),
        "chat_ap": _node(
            "chat_ap", "Chat com portfólio", "Decisão",
            "Permite tirar dúvidas sobre a carteira usando o contexto já montado.",
            ("chat_com_portfólio()", "Histórico da conversa", "Contexto do portfólio"),
            "Fecha o ciclo educativo: o usuário pode perguntar por que algo foi sugerido.",
        ),
    },
)


FLOW_INVESTIMENTOS = FlowSpec(
    key="analise_investimentos",
    title="Análise da carteira atual de investimentos",
    subtitle=(
        "Mostra como a aba Investimentos le a carteira real, consolida posições e apresenta risco, "
        "distribuição, exposição macro e stress tests."
    ),
    rows=(
        ("Fontes", ("positions_ai", "quotes_ai", "dividends_ai")),
        ("Consolidação", ("snapshot_ai", "classes_ai", "setores_ai")),
        ("Análise", ("rentabilidade_ai", "risco_ai", "stress_ai")),
        ("Saída", ("dashboard_ai", "tabelas_ai", "alertas_ai")),
    ),
    default="positions_ai",
    nodes={
        "positions_ai": _node(
            "positions_ai", "Posições", "Fontes",
            "Le portfólio_positions ou snapshots importados da corretora para montar a carteira atual.",
            ("Quantidade", "Preço médio", "Total investido", "Moeda"),
            "E a base patrimonial: sem posição correta, toda análise fica torta.",
        ),
        "quotes_ai": _node(
            "quotes_ai", "Cotações", "Fontes",
            "Busca a cotação mais recente de cada ativo e converte USD quando necessário.",
            ("asset_quotes", "Preço atual", "FX USD/BRL", "Fallbacks"),
            "Marca a carteira a mercado e permite comparar custo com valor atual.",
        ),
        "dividends_ai": _node(
            "dividends_ai", "Proventos", "Fontes",
            "Carrega dividendos e JCP para mostrar renda, yield on cost e histórico.",
            ("dividends", "Eventos", "YoC", "Proventos por ativo"),
            "Renda recebida e parte relevante do retorno total.",
        ),
        "snapshot_ai": _node(
            "snapshot_ai", "Snapshot consolidado", "Consolidação",
            "Agrupa tickers fracionários, reconcilia custo e posição e classifica ativos.",
            ("BBAS3 + BBAS3F", "Venda parcial", "Histórico incompleto", "Tesouro por prefixo"),
            "Resolve detalhes operacionais antes de mostrar números finais.",
        ),
        "classes_ai": _node(
            "classes_ai", "Classes", "Consolidação",
            "Agrupa por Ações BR, FII, ETF, Tesouro, Renda Fixa, Exterior e outros.",
            ("Valor por classe", "Percentual da carteira", "Rentabilidade por classe"),
            "Ajuda a enxergar a alocação antes de olhar ativo por ativo.",
        ),
        "setores_ai": _node(
            "setores_ai", "Setores", "Consolidação",
            "Agrupa ações e FIIs por setor para medir concentração econômica.",
            ("Setor", "Valor de mercado", "Percentual da carteira"),
            "Duas empresas diferentes podem ter o mesmo risco setorial escondido.",
        ),
        "rentabilidade_ai": _node(
            "rentabilidade_ai", "Rentabilidade", "Análise",
            "Calcula retorno sobre custo, evolução patrimonial e comparações internas.",
            ("Rentabilidade total", "TWRR/evolução", "Top 10 contribuidores"),
            "Mostra se a carteira está ganhando dinheiro e onde.",
        ),
        "risco_ai": _node(
            "risco_ai", "Risco e concentração", "Análise",
            "Mede concentração por ativo, classe e setor, além de indicadores de dependência macro.",
            ("Top 1", "Top 5", "HHI", "Dependências macro"),
            "Ajuda a ver riscos que uma rentabilidade positiva pode esconder.",
        ),
        "stress_ai": _node(
            "stress_ai", "Stress tests", "Análise",
            "Aplica choques históricos simplificados para estimar perda e recuperação.",
            ("Crises históricas", "Perda percentual", "Perda em R$", "Tempo de recuperação"),
            "Responde a pergunta que importa no susto: quanto isso pode cair?",
        ),
        "dashboard_ai": _node(
            "dashboard_ai", "Dashboard", "Saída",
            "Resume patrimônio, retorno, proventos, distribuição e alertas visuais.",
            ("KPIs", "Gráficos", "Badges de fonte", "Atualização"),
            "Da uma visão rápida para quem quer decidir o próximo passo.",
        ),
        "tabelas_ai": _node(
            "tabelas_ai", "Tabelas", "Saída",
            "Permite auditar cada posição com quantidade, preço médio, mercado, lucro e participação.",
            ("Carteira detalhada", "Filtros", "Ordenação", "Download visual via dataframe"),
            "A transparência fica no nível do ativo.",
        ),
        "alertas_ai": _node(
            "alertas_ai", "Alertas", "Saída",
            "Aponta concentração, falta de cotação, queda, dependência e outras situações relevantes.",
            ("Severidade", "Mensagem", "Módulo de origem"),
            "Transforma análise em lista de pontos que merecem atenção.",
        ),
    },
)


FLOW_CONTROLE_FINANCEIRO = FlowSpec(
    key="controle_financeiro",
    title="Controle financeiro",
    subtitle=(
        "Mostra como o app separa o dinheiro que já entrou/saiu no mês (fluxo de "
        "caixa) da fatura futura do cartão (fluxo a vencer) — duas naturezas que "
        "nunca se misturam."
    ),
    rows=(
        ("Entrada", ("lanc_manual", "extrato", "fatura_cc")),
        ("Classificação", ("conta", "categoria")),
        ("Natureza do fluxo", ("fluxo_caixa", "fluxo_futuro")),
        ("Visualização", ("abas", "graficos")),
    ),
    default="lanc_manual",
    nodes={
        "lanc_manual": _node(
            "lanc_manual", "Lançamento manual", "Entrada",
            "Barra lateral: você registra entradas, saídas e investimentos do mês corrente.",
            (
                "Tipo: entrada / saída / investimento",
                "Valor, data, categoria e descrição",
                "Grava source='manual'",
            ),
            "E dinheiro que entra e sai no ato do lançamento — fluxo de caixa do presente.",
        ),
        "extrato": _node(
            "extrato", "Importação de extrato", "Entrada",
            "Configurações > Atualização de dados > Controle Financeiro > Extrato bancário: "
            "sobe o PDF do banco, confere direção, valor e categoria de cada movimento e grava.",
            (
                "Extrato do banco em PDF",
                "Prévia revisável antes de gravar",
                "Classificação automática por regras",
                "Grava source='import'",
            ),
            "Dinheiro que JÁ saiu da conta também é fluxo de caixa, ainda que não digitado a mão.",
        ),
        "fatura_cc": _node(
            "fatura_cc", "Upload da fatura (cartão)", "Entrada",
            "Configurações > Atualização de dados > Controle Financeiro > Fatura do cartão: "
            "sobe o CSV, revisa vencimento, conta e lançamentos, e só então publica a fatura. "
            "A aba Cartão de Crédito passou a ser só leitura do que foi publicado.",
            (
                "Compras da fatura, parcelas, estornos",
                "Conta do tipo credit_card",
                "Revisão de vencimento e conta antes de publicar",
                "Grava source='csv'",
            ),
            "E dinheiro que ainda NÃO saiu (fatura a vencer) — fluxo futuro, não do mês corrente.",
        ),
        "conta": _node(
            "conta", "Resolução de conta", "Classificação",
            "O lançamento manual e gravado na Conta Corrente (checking), nunca em conta de investimento.",
            (
                "Prioriza type='checking'",
                "Exclui contas de cartão de crédito",
                "Fallback seguro se não houver checking",
            ),
            "Fluxo de caixa e movimentação da conta corrente — não aporte em investimento como B3/XP.",
        ),
        "categoria": _node(
            "categoria", "Categorias", "Classificação",
            "Classifica cada lançamento; 'Pagamento de Cartão' representa a quitação da fatura pela conta.",
            (
                "Entrada / saída / investimento",
                "'Pagamento de Cartão' permitido no manual",
                "Bloqueia consumo de cartão digitado a mão",
            ),
            "Separa a QUITAÇÃO da fatura (fluxo de caixa) do CONSUMO do cartão (que vem só do CSV).",
        ),
        "fluxo_caixa": _node(
            "fluxo_caixa", "Fluxo de caixa (presente)", "Natureza do fluxo",
            "Reúne lançamentos manuais + extrato: tudo que já entrou ou saiu da conta no mês.",
            (
                "source 'manual' + 'import'",
                "Dinheiro já movimentado",
                "Base do Dashboard e das Tabelas",
            ),
            "E a foto do dinheiro real do mês corrente — o que você de fato tem.",
        ),
        "fluxo_futuro": _node(
            "fluxo_futuro", "Cartão de crédito (futuro)", "Natureza do fluxo",
            "A fatura CSV vive isolada: dinheiro que ainda vai sair, com projeção e parcelas.",
            (
                "source 'csv' + conta credit_card",
                "Fluxo a vencer (não saiu ainda)",
                "Exclusivo da aba Cartão de Crédito",
            ),
            "Natureza diferente do fluxo de caixa — parecem iguais, mas nunca se misturam.",
        ),
        "abas": _node(
            "abas", "Abas e filtros", "Visualização",
            "Navegação: Dashboard, Análises, Tabelas e Cartão de Crédito, com filtros e edição.",
            (
                "KPIs do mês (renda, despesa, saldo)",
                "Filtros: categoria / ano / mês / dia",
                "Tabela editável dos lançamentos",
            ),
            "Consultar, filtrar e corrigir os lançamentos sem sair da tela.",
        ),
        "graficos": _node(
            "graficos", "Gráficos e análises", "Visualização",
            "Gastos por categoria, histórico de 6 meses e pagamento de cartão mensal (só fluxo de caixa).",
            (
                "Gastos por categoria (mês)",
                "Histórico Receitas x Despesas x Investimentos",
                "Pagamento de cartão mensal (exclui fatura CSV)",
            ),
            "Analisar tendências sem misturar fluxo de caixa com a fatura futura do cartão.",
        ),
    },
    notes=(
        "Regra de ouro: fluxo de caixa (manual + extrato) e cartão de crédito "
        "(fatura CSV) são independentes e NUNCA se misturam.",
    ),
)


FLOW_SELECAO_FIIS = FlowSpec(
    key="selecao_fiis",
    title="Seleção de FIIs — da vitrine publicada a carteira-modelo",
    subtitle=(
        "Como o app pontua fundos imobiliarios, mede a própria confiança e decide "
        "se o que sai na tela é uma Carteira-Modelo ou apenas uma Lista de Diligência."
    ),
    rows=(
        ("Universo", ("vitrine_fii", "tipos_fii", "integridade_fii")),
        ("Métricas", ("renda_fii", "valuation_fii", "risco_fii", "governanca_fii")),
        ("Score", ("percentil_fii", "cobertura_fii", "confianca_fii")),
        ("Validação", ("pit_fii", "gate_fii")),
        ("Carteira", ("preferencias_fii", "otimizacao_fii", "monitor_fii")),
    ),
    default="vitrine_fii",
    nodes={
        "vitrine_fii": _node(
            "vitrine_fii", "Vitrine publicada", "Universo",
            "A tela não calcula nada ao vivo: ela le uma vitrine já publicada, gerada no "
            "armazém local e enviada para o banco de produção com data e versão de metodologia.",
            (
                "Snapshot com versão de metodologia e data de geração",
                "Selo de frescor: cadência alvo x limite de validade",
                "Publicação manual ou pela rotina noturna",
            ),
            "Você sabe de QUANDO e o número que está lendo. Vitrine antiga aparece "
            "como selo vencido, em vez de passar por dado atual.",
        ),
        "tipos_fii": _node(
            "tipos_fii", "Tipo do fundo", "Universo",
            "Cada fundo é classificado em tijolo, papel, FoF ou híbrido — e o tipo decide "
            "QUAIS métricas fazem sentido e com que peso entram no score.",
            (
                "Tijolo: vacância, WAULT, concentração de inquilinos, cap rate",
                "Papel: indexadores, inadimplência, LTV da carteira",
                "FoF e híbrido: composição e dupla camada de taxa",
            ),
            "Cobrar vacância de um fundo de papel não mede nada. O tipo evita comparar "
            "fundos que vivem de coisas diferentes com a mesma régua.",
        ),
        "integridade_fii": _node(
            "integridade_fii", "Integridade da leitura", "Universo",
            "Antes de pontuar, o app confere se o quadro lido tem as colunas esperadas. "
            "Quadro sem coluna e falha de leitura, não fundo inelegível.",
            (
                "Checagem de colunas obrigatórias",
                "Erro de leitura levanta erro, não vira lista vazia",
                "Aba Revisão de dados expoe as lacunas por fundo",
            ),
            "Já aconteceu de uma falha de leitura virar 'todos os fundos são inelegíveis'. "
            "Erro tem que parecer erro.",
        ),
        "renda_fii": _node(
            "renda_fii", "Renda e recorrência", "Métricas",
            "Não basta o dividend yield do mês: o motor olha o yield recorrente, o crescimento "
            "da renda por cota em 3 anos e quanto dessa renda se repete.",
            (
                "DY recorrente (descontando eventos não repetíveis)",
                "Crescimento da renda por cota em 3 anos",
                "Recorrência: fração da renda que se repete",
            ),
            "Um fundo que vendeu um imóvel e distribuiu o ganho mostra yield alto uma vez só. "
            "Separar recorrente de extraordinário evita comprar um evento passado.",
        ),
        "valuation_fii": _node(
            "valuation_fii", "Preço e valor patrimonial", "Métricas",
            "P/VP entra como ALVO, não como 'quanto menor melhor': desconto grande demais "
            "costuma ser o mercado precificando um problema que o balanço ainda não mostra.",
            (
                "P/VP com faixa-alvo, não monotônico",
                "VPA vindo da fonte regulatória",
                "Preço de mercado com prazo de validade próprio",
            ),
            "Ordenar por 'P/VP mais baixo' é uma armadilha clássica: a ponta barata concentra "
            "fundos com vacância alta ou crédito problematico.",
        ),
        "risco_fii": _node(
            "risco_fii", "Liquidez e risco", "Métricas",
            "Liquidez diária, drawdown máximo e tendência de retorno total, sempre com a "
            "janela ancorada no calendário do mercado — nunca no último dia do próprio fundo.",
            (
                "Liquidez diária média em janela recente",
                "Drawdown máximo e tendência de retorno total",
                "Janela ancorada na última data do mercado",
            ),
            "Ancorar a janela no próprio ativo faz um fundo parado há anos exibir liquidez "
            "com cara de fresca. A âncora precisa vir de fora.",
        ),
        "governanca_fii": _node(
            "governanca_fii", "Governança e estrutura", "Métricas",
            "Peso pequeno, mas presente: taxas, alavancagem, histórico do gestor e "
            "concentração por administradora entram no score e no teto da carteira.",
            (
                "Taxa de administração e de performance",
                "Alavancagem e obrigações a pagar",
                "Teto por gestora e por administradora na carteira",
            ),
            "Concentrar a carteira inteira numa única administradora é um risco que "
            "nenhuma métrica de renda mostra.",
        ),
        "percentil_fii": _node(
            "percentil_fii", "Nota por percentil", "Score",
            "Cada métrica vira percentil DENTRO do tipo do fundo e as notas são combinadas "
            "por média ponderada. Valor ausente nunca vira zero.",
            (
                "Percentil calculado dentro do tipo (tijolo x papel x FoF)",
                "Média ponderada renormalizada sobre o que foi medido",
                "Ausente = fora da média, nunca zero",
            ),
            "Converter ausência em zero pune quem não publicou o dado como se tivesse "
            "publicado o pior número possível.",
        ),
        "cobertura_fii": _node(
            "cobertura_fii", "Cobertura da nota", "Score",
            "Junto da nota vem quanto do peso total foi efetivamente medido naquele fundo. "
            "Nota de 80 com 40% de cobertura não é a mesma coisa que 80 com 90%.",
            (
                "Fração do peso total efetivamente medida",
                "Exibida ao lado da nota, não escondida",
                "Piso de cobertura para entrar na carteira",
            ),
            "Sem cobertura ao lado, a nota mais alta tende a ser a do fundo com MENOS "
            "dado — porque sobra só o que ele tem de bom.",
        ),
        "confianca_fii": _node(
            "confianca_fii", "Confiança por fundo", "Score",
            "Além da cobertura, cada fundo carrega uma confiança própria: idade do dado, "
            "procedência da fonte e concordância entre fontes quando há mais de uma.",
            (
                "Idade do dado por métrica, com validade própria",
                "Procedência: qual fonte respondeu por aquele número",
                "Conciliação entre fontes quando existe duplicidade",
            ),
            "Confiança é por ativo. Uma média geral alta pode conviver com fundos "
            "individualmente mal cobertos dentro da mesma tela.",
        ),
        "pit_fii": _node(
            "pit_fii", "Validação point-in-time", "Validação",
            "A aba Retrospectiva refaz a seleção safra a safra usando SÓ o que era "
            "conhecido naquela data e mede o que a regra teria escolhido.",
            (
                "Safras reconstruidas com dado da época",
                "Mínimo metodologico de períodos para valer",
                "Snapshots verificados e contados na tela",
            ),
            "Sem point-in-time, o backtest lê o futuro e aprova qualquer regra. Mudar a "
            "versão de metodologia sem reconstruir a safra desliga o backtest em silêncio.",
        ),
        "gate_fii": _node(
            "gate_fii", "Portão de publicação", "Validação",
            "O app só chama o resultado de Carteira-Modelo se passar no portão: cobertura "
            "mínima, confiança mediana e validação PIT em dia. Senão, sai como Lista de Diligência.",
            (
                "Cobertura mínima do universo",
                "Confiança mediana acima do piso",
                "Validação PIT válida para a versão corrente",
            ),
            "É a diferença entre 'isto foi validado' e 'isto é um ponto de partida para "
            "você investigar'. O rótulo muda porque a evidência mudou.",
        ),
        "preferencias_fii": _node(
            "preferencias_fii", "Suas preferências", "Carteira",
            "Você define número de ativos, tetos por tipo, por gestora e por administradora, "
            "e o piso de liquidez. A regra sai da sua mão, não de um padrão escondido.",
            (
                "Quantidade de ativos e pesos mínimo/máximo",
                "Tetos por tipo, gestora e administradora",
                "Piso de liquidez diária",
            ),
            "Teto que você não viu é premissa de quem escreveu o código — e envelhece "
            "sem parecer errado.",
        ),
        "otimizacao_fii": _node(
            "otimizacao_fii", "Montagem da carteira", "Carteira",
            "A otimização busca a melhor combinação dentro dos SEUS limites. Quando os "
            "limites se contradizem, o app avisa qual deles esvaziou a carteira.",
            (
                "Otimização com restrições de peso e de grupo",
                "Diagnóstico quando a restrição é inalcançável",
                "Cessão controlada da forma, nunca do risco",
            ),
            "Carteira vazia quase nunca é falta de ativo bom: é teto inalcançável. "
            "O app precisa dizer QUAL limite travou, não devolver uma lista em branco.",
        ),
        "monitor_fii": _node(
            "monitor_fii", "Acompanhamento", "Carteira",
            "Depois de montada, a carteira é reavaliada a cada nova safra: o que caiu de "
            "nota, o que perdeu liquidez e o que saiu do universo.",
            (
                "Comparação entre safras",
                "Entradas e saídas do universo elegível",
                "Alerta de fundo que deixou de cumprir o piso",
            ),
            "Painel que só ganha ativos e nunca perde é assinatura de amostra sobrevivente. "
            "Contar as saídas é o teste barato.",
        ),
    },
    notes=(
        "Carteira-Modelo e Lista de Diligência não são sinônimos: a primeira passou no "
        "portão de publicação, a segunda não — e a tela diz qual das duas você está vendo.",
    ),
)


FLOW_EMPRESAS_EUA = FlowSpec(
    key="empresas_eua",
    title="Empresas Americanas — do arquivo da SEC ao ranking",
    subtitle=(
        "O módulo dos EUA repete a casca da B3, mas com outra fonte, outro universo "
        "e outra forma de medir crescimento."
    ),
    rows=(
        ("Universo", ("sec_universo", "so_acoes", "vitrine_us")),
        ("Métricas", ("gaap_us", "trilhas_us", "crescimento_us")),
        ("Score", ("percentil_industria_us", "cobertura_us", "piso_us")),
        ("Validação", ("pit_us", "saidas_us")),
        ("Aplicação", ("avancada_us", "carteira_us", "avaliacao_us")),
    ),
    default="sec_universo",
    nodes={
        "sec_universo": _node(
            "sec_universo", "Arquivos da SEC", "Universo",
            "A base vem dos próprios arquivos entregues a SEC (10-K, 10-Q, 8-K), não de "
            "um provedor que já mastigou o número.",
            (
                "10-K e 10-Q: demonstrações auditadas",
                "8-K: eventos, inclusive saída de bolsa",
                "Preço de mercado complementado por fonte de cotação",
            ),
            "Número mastigado por terceiro traz o critério do terceiro junto. Ir na fonte "
            "custa mais trabalho e devolve a procedência.",
        ),
        "so_acoes": _node(
            "so_acoes", "Só ações operacionais", "Universo",
            "REIT, fundo, SPAC e classe preferencial ficam de fora. A regra é única e "
            "centralizada, para não divergir entre telas.",
            (
                "Exclui REIT, fundo, SPAC e preferencial",
                "Regra única em um só lugar do código",
                "Imobiliário operacional continua dentro",
            ),
            "Um REIT não se compara a uma indústria pelos mesmos múltiplos. E o código do "
            "setor não separa REIT de incorporadora — quem separa é a eleição fiscal no 10-K.",
        ),
        "vitrine_us": _node(
            "vitrine_us", "Vitrine publicada", "Universo",
            "Como nos FIIs, a tela le uma vitrine publicada com versão de metodologia. "
            "Trocar a versão sem republicar deixa a tela lendo safra que não existe mais.",
            (
                "Versão de metodologia gravada na vitrine",
                "Selo de frescor na cabeça da tela",
                "Republicação a cada mudança de versão",
            ),
            "Já custou caro: vitrine sem as colunas novas zerou o ranking inteiro sem "
            "levantar um único erro.",
        ),
        "gaap_us": _node(
            "gaap_us", "Contabilidade americana", "Métricas",
            "As linhas vem em US GAAP, que não casa linha a linha com o padrão brasileiro. "
            "O app trabalha com os conceitos da fonte, sem forcar equivalência.",
            (
                "Linhas em US GAAP, na moeda de reporte",
                "Sem tradução forcada para o plano de contas da B3",
                "Faixa de validação registra o valor recusado",
            ),
            "Forcar equivalência entre padrões contábeis cria comparação que parece válida "
            "e não é. Melhor duas réguas honestas que uma régua falsa.",
        ),
        "trilhas_us": _node(
            "trilhas_us", "Trilhas de fatores", "Métricas",
            "As métricas se agrupam em trilhas: qualidade, crescimento, solidez, eficiência "
            "de capital, valuation e retorno ao acionista.",
            (
                "Qualidade, crescimento e solidez",
                "Eficiência de capital e valuation",
                "Retorno ao acionista (dividendo e recompra)",
            ),
            "Agrupar evita que dez métricas correlacionadas da mesma familia dominem o "
            "score só por serem muitas.",
        ),
        "crescimento_us": _node(
            "crescimento_us", "Crescimento por inclinação", "Métricas",
            "Crescimento é medido pela inclinação da regressão da série, não por CAGR de "
            "ponta a ponta — e a qualidade do ajuste é publicada junto.",
            (
                "Inclinação da regressão sobre a série inteira",
                "Qualidade do ajuste publicada ao lado",
                "Pesos ajustados para o setor financeiro",
            ),
            "CAGR usa dois pontos e ignora o caminho. Duas empresas com o mesmo CAGR podem "
            "ter uma série estável e outra totalmente errática.",
        ),
        "percentil_industria_us": _node(
            "percentil_industria_us", "Percentil na indústria", "Score",
            "A nota de cada métrica é o percentil dentro da indústria, não no universo "
            "inteiro. Qualidade é relativa ao segmento.",
            (
                "Percentil dentro da indústria",
                "Piso de quantidade de pares para o percentil valer",
                "Substituição dentro do mesmo segmento",
            ),
            "Comparar margem de software com margem de varejo alimentar produz um ranking "
            "que só mede em que setor a empresa esta.",
        ),
        "cobertura_us": _node(
            "cobertura_us", "Cobertura e confiança", "Score",
            "Mesma regra dos FIIs: a fração do peso efetivamente medida sai ao lado da nota, "
            "e ausência nunca vira zero.",
            (
                "Fração do peso medida por empresa",
                "Procedência por métrica",
                "Ausente fora da média, nunca zero",
            ),
            "Sem isso, a empresa com menos divulgação sobe no ranking por falta de "
            "informação contraria.",
        ),
        "piso_us": _node(
            "piso_us", "Piso de qualidade", "Score",
            "Empresas abaixo do piso de cobertura ou com sinal eliminatório saem do "
            "ranking — mas a tela diz quantas saíram e por que.",
            (
                "Piso de cobertura para entrar no ranking",
                "Sinais eliminatórios explicitos",
                "Contagem de excluidos visível",
            ),
            "Portão que só podia dar False nunca é revisto. No dia em que a fonte chega, "
            "ele promove a base inteira de uma vez.",
        ),
        "pit_us": _node(
            "pit_us", "Painel point-in-time", "Validação",
            "Safras reconstruidas com o dado conhecido na época, para medir se a ordenação "
            "do motor antecipou alguma coisa.",
            (
                "Safras com dado da época",
                "Poder de ordenação medido separado do excesso de retorno",
                "Republicado a cada mudança de versão",
            ),
            "Ordenar bem não é superar o mercado. São duas medidas diferentes, e dizer que "
            "o motor 'funciona' exige dizer qual das duas foi medida.",
        ),
        "saidas_us": _node(
            "saidas_us", "Saídas de bolsa", "Validação",
            "Empresas que saíram da bolsa entram no painel. Aquisição e falência são "
            "desfechos opostos e não podem receber o mesmo retorno.",
            (
                "Saídas lidas do item do 8-K",
                "Aquisição separada de falência",
                "Retorno publicado como banda, não como ponto",
            ),
            "Zero deslistagem em 16 anos não é limpeza: é assinatura de universo "
            "sobrevivente, e infla todo retorno histórico.",
        ),
        "avancada_us": _node(
            "avancada_us", "Análise avançada", "Aplicação",
            "O laboratório equivalente ao da B3: mexer em pesos, ver o efeito no ranking "
            "e comparar com o comportamento fora da amostra.",
            (
                "Pesos ajustáveis por trilha",
                "Efeito imediato no ranking",
                "Comparação dentro e fora da amostra",
            ),
            "Ver o ranking mudar quando você mexe no peso mostra de quanto julgamento "
            "aquele resultado depende.",
        ),
        "carteira_us": _node(
            "carteira_us", "Criação de portfólio", "Aplicação",
            "Segunda etapa: transformar o ranking em carteira, com limites por setor e "
            "por ativo definidos por você.",
            (
                "Limites por setor e por ativo",
                "Piso de liquidez",
                "Diagnóstico de restrição inalcançável",
            ),
            "O ranking ordena; a carteira precisa caber em limites. São decisões diferentes.",
        ),
        "avaliacao_us": _node(
            "avaliacao_us", "Avaliação de portfólio", "Aplicação",
            "Terceira etapa: submeter a carteira pronta as mesmas métricas de risco, "
            "concentração e fatores.",
            (
                "Risco e concentração da carteira montada",
                "Exposição por fator",
                "Comparação com a carteira atual",
            ),
            "Carteira montada por ranking pode concentrar fator sem ninguém perceber. "
            "A avaliação existe para mostrar isso ANTES do aporte.",
        ),
    },
    notes=(
        "O universo americano do app é de ações operacionais. REIT, fundo, SPAC e "
        "preferencial ficam de fora por regra, não por falta de dado.",
    ),
)


FLOW_PORTFOLIO_GLOBAL = FlowSpec(
    key="portfolio_global",
    title="Portfólio Global — tudo o que você tem, numa visão só",
    subtitle=(
        "Junta B3, FIIs, renda fixa e exterior num único retrato: concentração, fatores, "
        "risco, papel de cada ativo e o que fazer com o próximo aporte."
    ),
    rows=(
        ("Consolidação", ("snapshots_pg", "alvos_pg", "cambio_pg")),
        ("Estrutura", ("concentracao_pg", "correlacao_pg", "fatores_pg")),
        ("Risco", ("risco_pg", "papeis_pg", "qualidade_pg")),
        ("Ação", ("recomendacoes_pg", "aporte_pg", "chat_pg")),
    ),
    default="snapshots_pg",
    nodes={
        "snapshots_pg": _node(
            "snapshots_pg", "Consolidação das posições", "Consolidação",
            "Lê as posições de todas as classes e monta uma tabela única com classe, "
            "país, moeda, setor e símbolo.",
            (
                "Ações, FIIs, renda fixa e exterior na mesma tabela",
                "Classe, país, moeda, setor e símbolo por posição",
                "Aviso explicito para setor não mapeado",
            ),
            "Avaliar cada classe na sua própria tela esconde a concentração que só aparece "
            "quando tudo esta junto.",
        ),
        "alvos_pg": _node(
            "alvos_pg", "Alocação-alvo", "Consolidação",
            "Você define o alvo por classe num editor. O app compara alvo com o real e "
            "avisa quando existe alvo sem nenhuma posição correspondente.",
            (
                "Editor de alvo por classe",
                "Desvio entre alvo e posição atual",
                "Aviso de classe com alvo e sem posição",
            ),
            "Alvo sem posição costuma ser intenção esquecida. Vale aparecer como aviso, "
            "não sumir silenciosamente da conta.",
        ),
        "cambio_pg": _node(
            "cambio_pg", "Moeda", "Consolidação",
            "Posição em moeda estrangeira é convertida para comparar, mas a moeda de "
            "origem continua registrada como dimensão própria.",
            (
                "Conversão para moeda de referência",
                "Moeda de origem preservada como dimensão",
                "Exposição cambial medida separadamente",
            ),
            "Converter tudo e esquecer a moeda apaga um risco real: a carteira pode estar "
            "diversificada em ativo e concentrada em dólar.",
        ),
        "concentracao_pg": _node(
            "concentracao_pg", "Concentração", "Estrutura",
            "Índice de concentração e número efetivo de posições em cinco dimensões: "
            "classe, país, moeda, setor e ativo.",
            (
                "Concentração por classe, país, moeda, setor e ativo",
                "Número efetivo de posições",
                "Maiores posições por dimensão, em cards",
            ),
            "Ter 30 ativos não significa ter 30 apostas. O número efetivo mostra quantas "
            "posições realmente independentes existem.",
        ),
        "correlacao_pg": _node(
            "correlacao_pg", "Correlação", "Estrutura",
            "Correlação calculada em janela comum entre os ativos, para não comparar "
            "séries de tamanhos diferentes.",
            (
                "Janela comum entre os ativos comparados",
                "Série mensal de retorno",
                "Ativo sem série suficiente fica de fora, declarado",
            ),
            "Correlação em janelas heterogêneas mistura regimes diferentes e produz um "
            "número que não descreve período nenhum.",
        ),
        "fatores_pg": _node(
            "fatores_pg", "Exposição a fatores", "Estrutura",
            "A carteira é projetada contra fatores representados por fundos negociados em "
            "bolsa: dólar, inflação, juros, mercado local, ouro e small cap.",
            (
                "Fatores representados por ETFs reais, não índices teoricos",
                "Small cap medido como diferença contra o mercado",
                "Exposição por fator e por ativo",
            ),
            "Usar ETF negociável como representante mantém o fator investível: é uma "
            "exposição que você poderia de fato ter.",
        ),
        "risco_pg": _node(
            "risco_pg", "Perda esperada na cauda", "Risco",
            "Risco calculado pelo percentil empírico da série observada — sem supor "
            "distribuição normal — com contribuição marginal por ativo.",
            (
                "VaR e CVaR históricos, por percentil empírico",
                "Contribuição marginal de cada ativo",
                "Pesos renormalizados sobre quem tem série",
            ),
            "Supor normalidade subestima a cauda justo onde ela importa. A série observada "
            "já carrega as crises que aconteceram.",
        ),
        "papeis_pg": _node(
            "papeis_pg", "Papel de cada ativo", "Risco",
            "Cada posição recebe um papel — renda, crescimento, hedge cambial, proteção "
            "contra inflação, reserva de valor, baixa volatilidade ou diversificação.",
            (
                "Papel atribuido com evidência numérica",
                "Indeterminado carrega a causa nomeada",
                "Cobertura de papeis na carteira",
            ),
            "Papel sem evidência é rótulo. Quando não dá para determinar, dizer POR QUE "
            "vale mais que chutar uma categoria.",
        ),
        "qualidade_pg": _node(
            "qualidade_pg", "Qualidade do retrato", "Risco",
            "Quanto da carteira tem série de preço, setor mapeado e papel determinado. "
            "O retrato declara a própria cobertura.",
            (
                "Fração com série de preço disponível",
                "Posições sem setor ou sem papel",
                "Impacto da lacuna sobre cada painel",
            ),
            "Uma medida de risco que cobre 60% da carteira não é a medida de risco da "
            "carteira — e precisa dizer isso.",
        ),
        "recomendacoes_pg": _node(
            "recomendacoes_pg", "Recomendações", "Ação",
            "Os sinais viram inclinação no peso-alvo, passam pelos tetos por ativo e por "
            "classe, e só então viram sugestão de movimento com custo estimado.",
            (
                "Sinal inclina o peso-alvo, não decide sozinho",
                "Teto por ativo e por classe sobre o resultado acumulado",
                "Custo de transação entra na decisão",
            ),
            "Movimento menor que o custo de fazer vira 'manter'. E quando o custo não esta "
            "calibrado, a resposta também é 'manter'.",
        ),
        "aporte_pg": _node(
            "aporte_pg", "Aporte do mês", "Ação",
            "Responde a pergunta prática: com R$ X para aportar, onde o dinheiro vai? "
            "Converge para o alvo comprando, sem vender nada.",
            (
                "Só compra: nunca sugere venda para rebalancear",
                "Prioriza as classes mais abaixo do alvo",
                "Respeita lote e valor mínimo",
            ),
            "Rebalanceamento teórico vende para acertar o peso. Quem só aporta não faz "
            "isso — e a conta precisa ser a do aportador.",
        ),
        "chat_pg": _node(
            "chat_pg", "Conversa sobre a carteira", "Ação",
            "Por último, e de propósito: a conversa com o modelo recebe os painéis já "
            "calculados, em vez de recalcular por conta própria.",
            (
                "Recebe os números já apurados nos painéis",
                "Fica por último na tela, depois da evidência",
                "Não substitui os painéis",
            ),
            "O modelo comenta o que foi medido. Se ele estivesse antes dos painéis, a "
            "conversa viraria a evidência — e ela não é.",
        ),
    },
    notes=(
        "Nenhum painel do Portfólio Global executa ordem. Tudo aqui termina em sugestão "
        "com custo estimado, para você decidir.",
    ),
)


FLOW_QUALIDADE_DADOS = FlowSpec(
    key="qualidade_dados",
    title="Qualidade dos dados — de onde vem o número e quanto ele vale",
    subtitle=(
        "Como o app separa armazém local de vitrine publicada, mede a própria confiança "
        "por seção e recusa transformar o que não foi medido em nota cheia."
    ),
    rows=(
        ("Origem", ("armazem_local", "vitrine_publicada", "frescor")),
        ("Medição", ("confiabilidade", "abrangencia", "nao_medido")),
        ("Portões", ("gate_publicacao", "validacao_pit", "piso_qualidade")),
        ("Leitura", ("onde_ver", "limites")),
    ),
    default="armazem_local",
    nodes={
        "armazem_local": _node(
            "armazem_local", "Armazém local", "Origem",
            "O histórico pesado — séries de preço, observações de crédito, arquivos "
            "regulatórios — mora num banco local, fora da nuvem.",
            (
                "Séries diárias e mensais completas",
                "Arquivos brutos e observações de crédito",
                "Não é alcançável pelo app publicado",
            ),
            "O plano gratuito da nuvem tem limite de espaço. Guardar o histórico fora dela "
            "é o que permite manter série longa sem cortar ativo.",
        ),
        "vitrine_publicada": _node(
            "vitrine_publicada", "Vitrine publicada", "Origem",
            "Do armazém sai um resumo — a vitrine — que é publicado na nuvem. É ela que a "
            "tela le. O cálculo pesado já aconteceu antes.",
            (
                "Resumo gerado a partir do armazém",
                "Publicado com data e versão de metodologia",
                "A tela le a vitrine, não recalcula",
            ),
            "Separar geração de leitura deixa a tela rápida e torna auditável QUANDO cada "
            "número foi produzido.",
        ),
        "frescor": _node(
            "frescor", "Selo de frescor", "Origem",
            "Cada vitrine carrega uma cadência alvo e um limite de validade. O selo no "
            "topo da tela compara a data da publicação com esses dois prazos.",
            (
                "Cadência alvo: de quanto em quanto tempo deveria atualizar",
                "Limite de validade: a partir de quando esta velho",
                "Selo visível na cabeça da tela",
            ),
            "Dado velho não levanta erro — ele só fica parado. Sem selo, parado é "
            "indistinguível de atualizado.",
        ),
        "confiabilidade": _node(
            "confiabilidade", "Confiabilidade", "Medição",
            "Primeiro eixo, de peso alto: o quanto a fonte, a validação e a conciliação "
            "sustentam o número daquela seção.",
            (
                "Procedência da fonte por métrica",
                "Validação e conciliação entre fontes",
                "Idade do dado dentro da própria validade",
            ),
            "E a pergunta 'da para confiar neste número?', separada da pergunta 'quantos "
            "ativos ele cobre?'.",
        ),
        "abrangencia": _node(
            "abrangencia", "Abrangência", "Medição",
            "Segundo eixo, de peso baixo: quantos ativos da seção estão efetivamente "
            "cobertos pela medição.",
            (
                "Fração do universo coberta",
                "Peso deliberadamente menor que o da confiabilidade",
                "Lacuna de ingestão aparece aqui primeiro",
            ),
            "Cobrir muito com fonte fraca é pior que cobrir menos com fonte forte. Por isso "
            "os dois eixos não tem o mesmo peso.",
        ),
        "nao_medido": _node(
            "nao_medido", "O que não foi medido", "Medição",
            "Critério sem medição sai como NÃO MEDIDO e fica de FORA da média ponderada. "
            "Nunca entra como zero nem como cem.",
            (
                "Não medido sai da média, e não vira nota",
                "A tela declara quantos critérios ficaram de fora",
                "Média renormalizada sobre o que sobrou",
            ),
            "Zero pune quem não mediu como se tivesse medido o pior. Cem premia a "
            "ignorância. As duas saídas mentem — a terceira é declarar.",
        ),
        "gate_publicacao": _node(
            "gate_publicacao", "Portão de publicação", "Portões",
            "Antes de chamar um resultado de recomendação, o app checa cobertura, confiança "
            "mediana e validação. Reprovou, o rótulo muda.",
            (
                "Cobertura mínima do universo",
                "Confiança mediana acima do piso",
                "Rótulo de diligência quando reprova",
            ),
            "Não é censura: o resultado continua visível. O que muda é a afirmação que o "
            "app faz sobre ele.",
        ),
        "validacao_pit": _node(
            "validacao_pit", "Validação point-in-time", "Portões",
            "As retrospectivas usam SÓ o que era conhecido em cada data. Versão de "
            "metodologia nova exige safra nova.",
            (
                "Safras reconstruidas com dado da época",
                "Mínimo de períodos para a validação valer",
                "Versão sem safra correspondente desliga o painel",
            ),
            "Backtest que le o futuro aprova qualquer regra. E painel vazio precisa dizer "
            "o motivo, em vez de parecer 'sem dado'.",
        ),
        "piso_qualidade": _node(
            "piso_qualidade", "Pisos e faixas", "Portões",
            "Acima de 75 a seção é considerada de confiança alta; entre 55 e 75, média; "
            "abaixo disso, baixa — e a faixa aparece junto do número.",
            (
                "Faixa alta a partir de 75",
                "Faixa média a partir de 55",
                "Faixa exibida junto da nota",
            ),
            "Nota isolada não diz se é boa. A faixa dá a escala sem exigir que você decore "
            "o critério.",
        ),
        "onde_ver": _node(
            "onde_ver", "Onde ver isso", "Leitura",
            "A aba Grau de Confiança, dentro de Configurações, mostra a nota por seção e "
            "o detalhe critério a critério.",
            (
                "Configurações > Grau de Confiança",
                "Nota por seção do app",
                "Detalhe por critério, com o que não foi medido",
            ),
            "Medida de qualidade que não tem porta de entrada é decoração. Ela precisa "
            "estar a um clique da tela que você usa.",
        ),
        "limites": _node(
            "limites", "Limitações declaradas", "Leitura",
            "Cada painel carrega as próprias limitações, derivadas da medição — não de um "
            "texto fixo que envelhece sem avisar.",
            (
                "Limitação derivada do que foi medido",
                "Texto revisto quando a medição muda",
                "Documentação e Configurações não fazem afirmação de confiança",
            ),
            "Aviso escrito a mão envelhece invertido: vira falso e continua soando como "
            "rigor. Por isso a limitação sai da medição.",
        ),
    },
    notes=(
        "Regra que atravessa o app inteiro: o que não foi medido não vira 100 — e "
        "também não vira 0. Vira uma declaração.",
    ),
)


FLOW_CONFIGURACOES = FlowSpec(
    key="configuracoes",
    title="Configurações — o que você ajusta, o que você sobe e o que você audita",
    subtitle=(
        "A Central de Configurações reúne quatro tarefas diferentes sob a mesma barra: "
        "preferência da conta, entrada de arquivo, diagnóstico do ambiente e segurança. "
        "Saber qual aba faz o quê evita procurar o upload no lugar errado."
    ),
    rows=(
        ("Preferências", ("cfg_tema", "cfg_trocar_usuario", "cfg_memoria_llm")),
        ("Entrada de arquivo", ("cfg_fatura", "cfg_extrato", "cfg_carteira")),
        ("Diagnóstico", ("cfg_confianca", "cfg_mercado", "cfg_banco")),
        ("Segurança", ("cfg_sessao", "cfg_usuarios")),
    ),
    default="cfg_tema",
    nodes={
        "cfg_tema": _node(
            "cfg_tema", "Tema do aplicativo", "Preferências",
            "Escolha entre o tema escuro e o claro. A escolha é gravada na conta e "
            "vale nas próximas aberturas, em qualquer tela.",
            (
                "Aba ⚙️ Geral, primeiro bloco",
                "Escolha gravada por usuário",
                "O seletor morava na barra lateral até 21/09/2026",
            ),
            "O tema é preferência de quem usa, e preferência pertence à conta — não ao "
            "menu de navegação, onde disputava espaço com as rotas do app.",
        ),
        "cfg_trocar_usuario": _node(
            "cfg_trocar_usuario", "Trocar de usuário", "Preferências",
            "Encerra a sessão deste navegador e devolve a tela de entrada. Exige marcar "
            "uma confirmação explícita antes do botão ficar disponível.",
            (
                "Aba ⚙️ Geral, segundo bloco",
                "Confirmação obrigatória antes de sair",
                "Limpa a sessão inteira do navegador",
            ),
            "Sair apaga tudo o que a sessão guardava em memória, inclusive filtros e "
            "prévias ainda não gravadas. Por isso a saída pede confirmação em vez de "
            "acontecer num clique solto.",
        ),
        "cfg_memoria_llm": _node(
            "cfg_memoria_llm", "Memória da LLM", "Preferências",
            "Apaga o histórico de conversa com o assistente — de uma seção específica "
            "ou de todas. A contagem aparece antes do clique.",
            (
                "Aba ⚙️ Geral, terceiro bloco",
                "Contagem de conversas exibida antes de apagar",
                "Limpa o banco e também a sessão aberta",
            ),
            "Apagar só o banco deixaria a conversa viva na tela até o próximo recarregamento, "
            "e o usuário concluiria que a limpeza falhou. As duas cópias somem juntas.",
        ),
        "cfg_fatura": _node(
            "cfg_fatura", "Fatura do cartão", "Entrada de arquivo",
            "Sobe o CSV da fatura, confere vencimento, conta e lançamentos na prévia e só "
            "então grava. É a entrada do fluxo futuro do cartão.",
            (
                "Aba 🔁 Atualização de dados › 💳 Controle Financeiro",
                "Arquivo CSV, com prévia antes de gravar",
                "Alimenta a aba Cartão, não o fluxo do mês",
            ),
            "A fatura projeta o que ainda vai ser pago. Somá-la ao caixa do mês mistura duas "
            "linhas do tempo diferentes, e foi por isso que as duas entradas seguem separadas.",
        ),
        "cfg_extrato": _node(
            "cfg_extrato", "Extrato bancário", "Entrada de arquivo",
            "Sobe o PDF do extrato e revisa direção, valor e categoria de cada movimento "
            "antes da importação.",
            (
                "Aba 🔁 Atualização de dados › 💳 Controle Financeiro",
                "Arquivo PDF, com revisão linha a linha",
                "Alimenta os movimentos do mês",
            ),
            "Extrato importado sem revisão inverte sinal e erra categoria em silêncio: o total "
            "fecha e a leitura por categoria fica errada. A conferência é parte da importação.",
        ),
        "cfg_carteira": _node(
            "cfg_carteira", "Arquivos da carteira", "Entrada de arquivo",
            "Importa os arquivos de negociação da corretora — seleciona a instituição, "
            "valida a prévia e atualiza a carteira consolidada.",
            (
                "Aba 🔁 Atualização de dados › 📈 Investimentos",
                "Formatos CSV, XLSX e PDF conforme a origem",
                "Exige banco conectado",
            ),
            "O nome do arquivo às vezes carrega parte da chave — a data do lote, por exemplo. "
            "Por isso a importação lê os arquivos um a um, e não como um bloco único de bytes.",
        ),
        "cfg_confianca": _node(
            "cfg_confianca", "Grau de Confiança", "Diagnóstico",
            "Mostra quanto o app confia em cada seção e a evidência por trás de cada nota. "
            "A tela lê a última medição gravada e só remede quando você clica.",
            (
                "Aba 🎯 Grau de Confiança, a primeira da barra",
                "Nota por seção, com o detalhe por critério",
                "Apoio analítico — não é recomendação",
            ),
            "Medir a cada abertura custava uma varredura inteira do banco por clique de menu. "
            "Ler a última medição e remedir sob comando deixa o custo onde a decisão está.",
        ),
        "cfg_mercado": _node(
            "cfg_mercado", "Dados de mercado", "Diagnóstico",
            "Acompanha cotações, fundamentos e indicadores macroeconômicos: quantas fontes "
            "estão em dia, quantas pedem atenção e quando foi a última atualização.",
            (
                "Aba 🔄 Dados de mercado",
                "Cotações e indicadores diários; macro consolidado mensal",
                "Documentos corporativos, semanais",
            ),
            "Transações, operações e proventos ficam de fora de propósito: eles vêm do arquivo "
            "que você sobe ou do lançamento manual, e não de coleta automática.",
        ),
        "cfg_banco": _node(
            "cfg_banco", "Banco de dados", "Diagnóstico",
            "Conexão, capacidade e schema do banco publicado, além das rotas controladas de "
            "importação. Diagnóstico técnico, sem conteúdo financeiro.",
            (
                "Aba 🗄️ Banco de dados",
                "Estado da conexão e avisos de ambiente",
                "Uso do espaço e maiores tabelas",
            ),
            "O plano gratuito da nuvem tem teto de espaço, e estourar o teto derruba a leitura "
            "do app inteiro. Ver a margem antes de publicar carga nova é o que evita a parada.",
        ),
        "cfg_sessao": _node(
            "cfg_sessao", "Proteção e sessão", "Segurança",
            "Confirma se o acesso está protegido por senha, se a sessão deste navegador está "
            "autenticada e gera o hash da credencial do aplicativo.",
            (
                "Aba 🔒 Segurança, blocos 01 e 02",
                "Hash gerado localmente, em SHA-256",
                "Somente o hash vai para o arquivo de segredos",
            ),
            "Senha em texto puro dentro da configuração vaza junto com qualquer cópia do "
            "ambiente. O aplicativo guarda o resumo criptográfico, e nunca a senha.",
        ),
        "cfg_usuarios": _node(
            "cfg_usuarios", "Usuários cadastrados", "Segurança",
            "Lista quem tem acesso ao aplicativo, com data de cadastro e situação. Somente "
            "leitura, e visível apenas para quem administra.",
            (
                "Aba 🔒 Segurança, bloco 03",
                "Data de cadastro e situação por usuário",
                "Quem não administra não vê o bloco",
            ),
            "A lista fecha a página, e não abre: o administrador chega nela depois de conferir "
            "o estado da sessão e da credencial.",
        ),
    },
    notes=(
        "Quem não administra o app vê uma versão reduzida desta tela, com duas abas: "
        "importar os próprios dados e a própria conta.",
        "As duas entradas do Controle Financeiro são irmãs na mesma sub-aba, mas nunca se "
        "somam: a fatura é fluxo futuro e o extrato é o mês corrente.",
    ),
)


FLOWS = (
    FLOW_ANALISE_AVANCADA,
    FLOW_SIMULADOR,
    FLOW_CRIACAO_PORTFOLIO,
    FLOW_ANALISE_PORTFOLIO,
    FLOW_INVESTIMENTOS,
    FLOW_CONTROLE_FINANCEIRO,
    FLOW_SELECAO_FIIS,
    FLOW_EMPRESAS_EUA,
    FLOW_PORTFOLIO_GLOBAL,
    FLOW_QUALIDADE_DADOS,
    FLOW_CONFIGURACOES,
)


INDICADORES = [
    {
        "Grupo": "Rentabilidade",
        "Indicador": "ROE",
        "O que mede": "Lucro líquido dividido pelo patrimônio líquido.",
        "Importância": "Mostra quanto retorno a empresa gera sobre o capital dos acionistas.",
        "Leitura": "Maior costuma ser melhor, mas precisa ser sustentável e não vir apenas de alavancagem.",
        "Autores": "Graham e Buffett tratam retorno consistente sobre capital como sinal de qualidade; Lynch compara esse retorno com crescimento, dívida e preço.",
    },
    {
        "Grupo": "Rentabilidade",
        "Indicador": "ROIC",
        "O que mede": "Retorno sobre o capital investido na operação.",
        "Importância": "Ajuda a medir eficiência econômica do negócio independentemente da estrutura de financiamento.",
        "Leitura": "ROIC alto e recorrente sugere vantagem competitiva; ROIC em queda pode indicar perda de moat ou ciclo ruim.",
        "Autores": "Damodaran e Greenblatt dao grande peso ao retorno sobre capital para separar empresas excelentes de negócios medianos.",
    },
    {
        "Grupo": "Rentabilidade",
        "Indicador": "ROA",
        "O que mede": "Lucro líquido dividido pelos ativos totais.",
        "Importância": "Mostra eficiência no uso dos ativos, útil para empresas intensivas em capital.",
        "Leitura": "Deve ser comparado dentro do setor; bancos e indústrias tem bases de ativos muito diferentes.",
        "Autores": "Graham reforca comparação histórica e setorial para evitar conclusões por números isolados.",
    },
    {
        "Grupo": "Margens",
        "Indicador": "Margem Líquida",
        "O que mede": "Lucro líquido como percentual da receita.",
        "Importância": "Resume quanto da venda vira lucro depois de custos, despesas, juros e impostos.",
        "Leitura": "Margem alta e estável indica poder de precificação; margem volátil exige cautela.",
        "Autores": "Lynch procura entender a história operacional por trás das margens; Buffett valoriza negócios com poder de preço.",
    },
    {
        "Grupo": "Margens",
        "Indicador": "Margem Operacional",
        "O que mede": "Resultado operacional dividido pela receita.",
        "Importância": "Isola a qualidade da operação antes de efeitos financeiros e impostos.",
        "Leitura": "Boa para comparar eficiência entre pares do mesmo setor.",
        "Autores": "Damodaran usa margens e crescimento para estimar qualidade operacional e valor intrínseco.",
    },
    {
        "Grupo": "Dividendos",
        "Indicador": "DY",
        "O que mede": "Dividendos pagos nos últimos 12 meses divididos pelo preço.",
        "Importância": "Mostra a renda de dividendos em relação ao preço pago.",
        "Leitura": "DY alto pode ser oportunidade ou alerta de lucro não recorrente e preço deprimido.",
        "Autores": "Siegel destaca dividendos no retorno de longo prazo; Graham gostava de histórico consistente, não de yield isolado.",
    },
    {
        "Grupo": "Dividendos",
        "Indicador": "Payout",
        "O que mede": "Percentual do lucro distribuido como dividendos/JCP.",
        "Importância": "Mostra quanto lucro e retido para reinvestimento versus distribuido.",
        "Leitura": "Payout muito alto pode limitar crescimento ou ser insustentável; em utilities pode ser normal.",
        "Autores": "Lynch sugere olhar a capacidade de reinvestimento; Damodaran separa empresas maduras de empresas de crescimento.",
    },
    {
        "Grupo": "Valuation",
        "Indicador": "P/L",
        "O que mede": "Preço da ação dividido pelo lucro por ação.",
        "Importância": "Indica quantos anos de lucro o investidor esta pagando, em termos simplificados.",
        "Leitura": "Menor pode ser mais barato, mas também pode indicar risco, ciclo ou lucro temporário.",
        "Autores": "Graham usa múltiplos com margem de segurança; Lynch popularizou relacionar P/L com crescimento esperado.",
    },
    {
        "Grupo": "Valuation",
        "Indicador": "P/VP",
        "O que mede": "Valor de mercado dividido pelo patrimônio líquido.",
        "Importância": "Ajuda a avaliar preço versus base contábil, especialmente bancos e negócios patrimoniais.",
        "Leitura": "Baixo pode indicar desconto ou baixa rentabilidade; alto exige ROE superior e sustentável.",
        "Autores": "Graham usava valor patrimonial como âncora defensiva; Buffett aceita pagar mais por negócios superiores.",
    },
    {
        "Grupo": "Valuation",
        "Indicador": "EV/EBIT",
        "O que mede": "Valor da firma dividido pelo lucro operacional.",
        "Importância": "Compara preço do negócio inteiro, incluindo dívida, com resultado operacional.",
        "Leitura": "Útil para comparar empresas com estruturas de capital diferentes.",
        "Autores": "Greenblatt usa rendimento operacional sobre valor da firma como uma de suas ideias centrais.",
    },
    {
        "Grupo": "Valuation",
        "Indicador": "P/FCO",
        "O que mede": "Preço dividido pelo fluxo de caixa operacional.",
        "Importância": "Avalia preço contra caixa gerado pela operação, reduzindo distorções contábeis do lucro.",
        "Leitura": "Pode ser mais robusto que P/L em empresas com lucro contabel volátil.",
        "Autores": "Buffett e Munger enfatizam caixa e economia real do negócio acima de lucro meramente contábil.",
    },
    {
        "Grupo": "Solvência",
        "Indicador": "Endividamento Total",
        "O que mede": "Dívida em relação a capital, patrimônio ou métrica equivalente usada no banco.",
        "Importância": "Mostra fragilidade financeira e sensibilidade a juros.",
        "Leitura": "Menor tende a ser melhor, mas concessões, utilities e bancos exigem leitura setorial.",
        "Autores": "Graham valorizava balanços fortes; Marks reforca que risco aparece quando dívida encontra ciclo adverso.",
    },
    {
        "Grupo": "Solvência",
        "Indicador": "Liquidez Corrente",
        "O que mede": "Ativos circulantes divididos por passivos circulantes.",
        "Importância": "Indica folga de curto prazo para cumprir obrigações.",
        "Leitura": "Muito baixa pode sinalizar aperto; muito alta pode indicar capital parado.",
        "Autores": "Graham via liquidez como camada de proteção para o investidor defensivo.",
    },
]


DEMONSTRACOES = [
    {
        "Demonstração": "DRE",
        "Componentes": "Receita, custos, despesas, EBITDA, EBIT, lucro líquido.",
        "Importância": "Mostra a formação do lucro e a eficiência operacional.",
        "Cuidados": "Lucro pode ser afetado por não recorrentes, ciclo, câmbio e efeitos contábeis.",
    },
    {
        "Demonstração": "Balanço Patrimonial",
        "Componentes": "Ativos, passivos, patrimônio líquido, dívida, caixa e capital de giro.",
        "Importância": "Mostra estrutura financeira, solvência e base de capital.",
        "Cuidados": "Patrimônio contábil pode subestimar marcas fortes ou superestimar ativos ruins.",
    },
    {
        "Demonstração": "Fluxo de Caixa",
        "Componentes": "FCO, FCI, FCF, capex, variação de caixa.",
        "Importância": "Mostra se o lucro vira dinheiro e quanto sobra para crescer, pagar dívida ou distribuir.",
        "Cuidados": "Fluxo de um ano isolado pode ser distorcido por capital de giro ou eventos extraordinários.",
    },
    {
        "Demonstração": "Histórico de Dividendos",
        "Componentes": "Dividendos, JCP, frequência, yield on cost e payout.",
        "Importância": "Ajuda a medir disciplina de capital e retorno ao acionista.",
        "Cuidados": "Dividendos altos sem lucro e caixa recorrentes podem ser armadilha.",
    },
    {
        "Demonstração": "Contexto Macro",
        "Componentes": "Selic, IPCA, câmbio e PIB.",
        "Importância": "Ajusta a leitura de valuation, dívida, crescimento e atratividade relativa da renda fixa.",
        "Cuidados": "Macro não deve substituir a análise da empresa, mas pode mudar o preço justo e o risco.",
    },
]


AUTORES = [
    ("Benjamin Graham", "Margem de segurança, balanço forte, lucros consistentes e preço razoável antes de otimismo."),
    ("Warren Buffett e Charlie Munger", "Qualidade do negócio, retorno sobre capital, vantagem competitiva e caixa real no longo prazo."),
    ("Peter Lynch", "Entender a história da empresa, crescimento, P/L em relação ao crescimento, dívida e dividendos."),
    ("Aswath Damodaran", "Valor depende de fluxo de caixa, crescimento, risco e reinvestimento; múltiplos precisam de narrativa."),
    ("Joel Greenblatt", "Combinar qualidade do negócio com preço pago, usando retorno sobre capital e rendimento operacional."),
    ("Howard Marks", "Risco, ciclos, margem para erro e disciplina importam tanto quanto retorno projetado."),
    ("Jeremy Siegel", "Dividendos, reinvestimento e horizonte longo explicam parte importante do retorno das ações."),
]


_GROUP_ACCENTS = {
    "Rentabilidade": "#00C896",
    "Margens": "#4A9EFF",
    "Dividendos": "#F6C90E",
    # Roxo escurecido: os outros quatro sao tokens e acompanham o tema, este
    # nao tem token e ficava fixo. #B084F5 rendia 2,81:1 sobre a pagina clara
    # -- ilegivel -- contra 6,72:1 no escuro. #9B51E0 troca esse desequilibrio
    # por 4,52:1 no claro e 4,18:1 no escuro, legivel nos dois.
    "Valuation": "#9B51E0",
    "Solvência": "#FC5C7D",
}


def _set_selected(flow_key: str, node_id: str) -> None:
    st.session_state[f"doc_selected_{flow_key}"] = node_id


def _flow_sequence(flow: FlowSpec) -> tuple[tuple[str, str], ...]:
    return tuple(
        (node_id, flow.nodes[node_id].title)
        for _, node_ids in flow.rows
        for node_id in node_ids
    )


_FLOW_DETAIL_OVERRIDES = {
    "aportes_sp": {
        "formula": "Capital novo do mês = aporte mensal configurado\nCotas compradas = aporte mensal / preço do ativo",
        "exemplo": "Aporte mensal = R$ 1.000\nPreço do ativo = R$ 25\nCotas compradas = 1.000 / 25 = 40 cotas",
        "interpretacao": "O simulador reproduz acumulação recorrente, aproximando a experiência de quem investe todo mês.",
    },
    "selic_sp": {
        "formula": "Valor acumulado = valor anterior x (1 + taxa Selic mensal) + aporte do mês",
        "exemplo": "Valor anterior = R$ 10.000\nSelic mensal = 0,80%\nAporte = R$ 1.000\nValor = 10.000 x 1,008 + 1.000 = R$ 11.080",
        "interpretacao": "A estratégia de ações precisa superar uma alternativa simples de renda fixa para justificar o risco.",
    },
    "montante_sp": {
        "formula": "Montante final = soma(cotas do ativo x preço final do ativo) + caixa residual",
        "exemplo": "Ativo A: 100 cotas x R$ 30 = R$ 3.000\nAtivo B: 80 cotas x R$ 25 = R$ 2.000\nMontante final = R$ 5.000",
        "interpretacao": "O montante mostra o patrimônio acumulado da carteira ao fim da simulação.",
    },
    "margem_sp": {
        "formula": "Margem vs benchmark = ((montante da estratégia - montante benchmark) / montante benchmark) x 100",
        "exemplo": "Estratégia = R$ 120.000\nSelic = R$ 100.000\nMargem = ((120.000 - 100.000) / 100.000) x 100 = 20%",
        "interpretacao": "A margem indica quanto a estratégia adicionou ou perdeu em relação a uma alternativa comparável.",
    },
    "backtest_cp": {
        "formula": "Retorno acumulado = ((valor final - total aportado) / total aportado) x 100",
        "exemplo": "Total aportado = R$ 60.000\nValor final = R$ 78.000\nRetorno acumulado = ((78.000 - 60.000) / 60.000) x 100 = 30%",
        "interpretacao": "O backtest traduz a seleção dos líderes em uma trilha histórica de patrimônio.",
    },
    "comparacao_cp": {
        "formula": "Alpha = retorno da estratégia - retorno do benchmark",
        "exemplo": "Retorno da estratégia = 18%\nRetorno Selic = 11%\nAlpha = 18% - 11% = 7 p.p.",
        "interpretacao": "A comparação mostra se a carteira criada gerou retorno adicional depois de considerar alternativas simples.",
    },
    "aprovacao_cp": {
        "formula": "Segmento aprovado se margem mínima, recência e critérios de benchmark forem atendidos",
        "exemplo": "Margem mínima exigida = 5 p.p.\nMargem observada = 8 p.p.\nUltima liderança recente = sim\nResultado: segmento aprovado",
        "interpretacao": "A aprovação impede que um segmento entre na carteira apenas por um resultado isolado.",
    },
    "pesos_cp": {
        "formula": "Peso do ativo = score relativo do ativo / soma dos scores selecionados",
        "exemplo": "Empresa A score 80, Empresa B score 70\nPeso A = 80 / (80 + 70) = 53,3%",
        "interpretacao": "Empresas mais fortes recebem mais peso, mas a carteira ainda respeita limites de concentração.",
    },
    "items_ap": {
        "formula": "Participação do ativo = valor de mercado do ativo / valor total do portfólio",
        "exemplo": "Valor do ativo = R$ 12.000\nPortfólio total = R$ 100.000\nParticipação = 12.000 / 100.000 = 12%",
        "interpretacao": "A participação mostra o tamanho real de cada tese dentro da carteira salva.",
    },
    "pesos_ap": {
        "formula": "Score combinado = (score quantitativo x 60%) + (score qualitativo x 40%)",
        "exemplo": "Score quanti = 80\nScore quali = 70\nScore combinado = 80 x 0,60 + 70 x 0,40 = 76",
        "interpretacao": "A redistribuição combina dados históricos do banco, leitura qualitativa da LLM "
                         "e uma segunda fonte na web (Fundamentus/Status Invest) para sugerir novos pesos. "
                         "Indicador em que as duas fontes divergem reduz o peso da empresa em até 10%.",
    },
    "snapshot_ai": {
        "formula": "Valor de mercado = quantidade consolidada x cotação atual",
        "exemplo": "Quantidade = 300\nCotação atual = R$ 18\nValor de mercado = 300 x 18 = R$ 5.400",
        "interpretacao": "O snapshot transforma operações dispersas em uma posição única e auditável.",
    },
    "classes_ai": {
        "formula": "Peso da classe = valor da classe / valor total da carteira",
        "exemplo": "Ações BR = R$ 45.000\nCarteira total = R$ 150.000\nPeso = 45.000 / 150.000 = 30%",
        "interpretacao": "A leitura por classe revela a arquitetura da carteira antes da análise por ativo.",
    },
    "rentabilidade_ai": {
        "formula": "Rentabilidade = ((valor atual + proventos - custo total) / custo total) x 100",
        "exemplo": "Valor atual = R$ 11.000\nProventos = R$ 500\nCusto = R$ 10.000\nRentabilidade = ((11.000 + 500 - 10.000) / 10.000) x 100 = 15%",
        "interpretacao": "A rentabilidade considera ganho de capital e renda recebida quando os dados estão disponíveis.",
    },
    "risco_ai": {
        "formula": "Concentração Top 5 = soma dos pesos dos 5 maiores ativos",
        "exemplo": "Pesos dos 5 maiores = 18% + 14% + 10% + 8% + 6%\nConcentração Top 5 = 56%",
        "interpretacao": "Quanto maior a concentração, maior a dependência de poucas posições.",
    },
    "stress_ai": {
        "formula": "Perda estimada = valor atual da carteira x choque do cenário",
        "exemplo": "Carteira = R$ 200.000\nChoque = -18%\nPerda estimada = 200.000 x 18% = R$ 36.000",
        "interpretacao": "O stress test ajuda a medir se a carteira e compativel com o risco que o usuário suporta.",
    },
}


def _generic_flow_detail(flow: FlowSpec, node: FlowNode) -> dict[str, str]:
    dados = "\n".join(f"- {item}" for item in node.contains)
    formula = (
        "Saída da etapa = dados validados + regra da etapa + passagem para a próxima camada\n"
        f"Camada atual = {node.layer}"
    )
    exemplo = (
        f"Etapa: {node.title}\n"
        f"Entrada: informações da camada {node.layer}\n"
        f"Processamento: {node.summary}\n"
        "Saída: dado organizado para a próxima etapa do fluxo."
    )
    detail = {
        "titulo": node.title,
        "objetivo": node.summary,
        "dados": dados or "Dados consolidados da etapa anterior.",
        "formula": formula,
        "exemplo": exemplo,
        "interpretacao": node.why,
        "impacto": (
            "Define a qualidade da informação que avança no fluxo e influencia a confiabilidade "
            "das conclusões seguintes."
        ),
        "limitacao": (
            "Esta etapa deve ser lida dentro do contexto do fluxo completo. Dados incompletos, "
            "defasados ou muito concentrados podem distorcer a conclusão."
        ),
    }
    detail.update(_FLOW_DETAIL_OVERRIDES.get(node.id, {}))
    if flow.key == "simulador_portfolio":
        detail["impacto"] = "Afeta o patrimônio simulado, a comparação com benchmarks e a aprovação histórica da estratégia."
    elif flow.key == "criacao_portfolio":
        detail["impacto"] = "Afeta a seleção dos líderes, a distribuição de pesos e a carteira modelo que será salva."
    elif flow.key == "analise_portfolio":
        detail["impacto"] = "Afeta a leitura qualitativa, a redistribuição sugerida e o relatório final do portfólio."
    elif flow.key == "analise_investimentos":
        detail["impacto"] = "Afeta os KPIs, os gráficos, os alertas e a interpretação da carteira atual."
    elif flow.key == "selecao_fiis":
        detail["impacto"] = (
            "Afeta a nota do fundo, a cobertura declarada ao lado dela e se o resultado "
            "sai rotulado como Carteira-Modelo ou como Lista de Diligência."
        )
    elif flow.key == "empresas_eua":
        detail["impacto"] = (
            "Afeta o percentil da empresa dentro da indústria, a entrada dela no ranking "
            "e o que a retrospectiva point-in-time consegue medir."
        )
    elif flow.key == "portfolio_global":
        detail["impacto"] = (
            "Afeta o retrato consolidado da carteira, as medidas de concentração e risco "
            "e a sugestão de destino do próximo aporte."
        )
    elif flow.key == "qualidade_dados":
        detail["impacto"] = (
            "Afeta a nota de confiança da seção, o rótulo que o app usa para afirmar algo "
            "e as limitações que cada painel declara."
        )
    elif flow.key == "configuracoes":
        detail["impacto"] = (
            "Afeta o que entra no banco pela sua mão, a preferência gravada na conta e o "
            "diagnóstico que você consulta antes de confiar num painel."
        )
    return detail


def _select_fluxograma_documentacao(flow_key: str, node_id: str) -> None:
    st.session_state[f"doc_fluxograma_{flow_key}"] = node_id


def _render_flow(flow: FlowSpec) -> None:
    st.markdown(
        f"""
        <div class="doc-intro">
            <div class="doc-intro-title">{html.escape(flow.title)}</div>
            <div class="doc-intro-text">
                {html.escape(flow.subtitle)}
                Cada bloco é clicável e atualiza o painel explicativo com objetivo, dados,
                regra de cálculo, exemplo, interpretação e cuidados de leitura.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    sequence = _flow_sequence(flow)
    selected_key = f"doc_fluxograma_{flow.key}"
    if selected_key not in st.session_state:
        st.session_state[selected_key] = flow.default

    col_fluxo, col_detalhe = st.columns([1.05, 1.45], gap="large")
    with col_fluxo:
        st.markdown(
            '<div class="doc-av-shell"><div class="doc-av-flow-title">Sequência do fluxo</div>',
            unsafe_allow_html=True,
        )
        for idx, (node_id, label) in enumerate(sequence):
            node = flow.nodes[node_id]
            selected = st.session_state[selected_key] == node_id
            button_label = f"{node.layer}: {label}"
            st.button(
                button_label,
                key=f"doc_fluxo_{flow.key}_{node_id}",
                width="stretch",
                type="primary" if selected else "secondary",
                on_click=_select_fluxograma_documentacao,
                args=(flow.key, node_id),
            )
            if idx < len(sequence) - 1:
                st.markdown('<div class="doc-av-arrow">↓</div>', unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    node = flow.nodes.get(st.session_state[selected_key], flow.nodes[flow.default])
    etapa = _generic_flow_detail(flow, node)
    with col_detalhe:
        st.markdown(
            f"""
            <div class="doc-av-detail">
                <div class="doc-av-kicker">Etapa selecionada</div>
                <div class="doc-av-title">{html.escape(etapa["titulo"])}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        _render_av_field("Objetivo", etapa["objetivo"])
        _render_av_field("Dados utilizados", etapa["dados"])

        st.markdown('<div class="doc-av-section"><div class="doc-av-label">Fórmula matemática ou regra de cálculo</div></div>', unsafe_allow_html=True)
        st.code(etapa["formula"], language="text")

        st.markdown('<div class="doc-av-section"><div class="doc-av-label">Exemplo numérico simplificado</div></div>', unsafe_allow_html=True)
        st.code(etapa["exemplo"], language="text")

        _render_av_field("Interpretação", etapa["interpretacao"])
        st.markdown(
            f"""
            <div class="doc-av-impact">
                <div class="doc-av-label">Impacto no fluxo</div>
                <div class="doc-av-text">{html.escape(etapa["impacto"])}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.warning(etapa["limitacao"])
        if flow.notes:
            for note in flow.notes:
                st.caption(note)


def _render_node_detail(node: FlowNode) -> None:
    chips = "".join(f'<span class="doc-chip">{html.escape(item)}</span>' for item in node.contains)
    st.markdown(
        f"""
        <div class="doc-detail">
            <div class="doc-detail-kicker">{html.escape(node.layer)}</div>
            <div class="doc-detail-title">{html.escape(node.title)}</div>
            <div class="doc-detail-body">{html.escape(node.summary)}</div>
            <div style="margin-top:12px;">{chips}</div>
            <div class="doc-mini-title">Por que importa</div>
            <div class="doc-detail-body">{html.escape(node.why)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_av_field(label: str, text: str) -> None:
    st.markdown(
        f"""
        <div class="doc-av-section">
            <div class="doc-av-label">{html.escape(label)}</div>
            <div class="doc-av-text">{html.escape(text)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _select_etapa_analise_avancada(key: str) -> None:
    st.session_state["etapa_analise_avancada"] = key


def render_fluxograma_analise_avancada() -> None:
    st.markdown(
        """
        <div class="doc-intro">
            <div class="doc-intro-title">Fluxograma Interativo da Análise Avançada</div>
            <div class="doc-intro-text">
                Siga a sequência real do App4: entrada de dados, agrupamento por pares,
                saneamento, normalização, pesos, ajustes, score, backtest e leitura final.
                Cada bloco é clicável e atualiza o painel explicativo ao lado.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if "etapa_analise_avancada" not in st.session_state:
        st.session_state["etapa_analise_avancada"] = "score_final"

    col_fluxo, col_detalhe = st.columns([1.05, 1.45], gap="large")

    with col_fluxo:
        st.markdown(
            '<div class="doc-av-shell"><div class="doc-av-flow-title">Sequência do modelo</div>',
            unsafe_allow_html=True,
        )
        for i, (key, label) in enumerate(ORDEM_ANALISE_AVANCADA):
            selected = st.session_state["etapa_analise_avancada"] == key
            is_score = key == "score_final"
            button_label = f"★ {label}" if is_score else label
            st.button(
                button_label,
                key=f"btn_fluxo_av_{key}",
                width="stretch",
                type="primary" if selected or is_score else "secondary",
                on_click=_select_etapa_analise_avancada,
                args=(key,),
            )
            if i < len(ORDEM_ANALISE_AVANCADA) - 1:
                st.markdown('<div class="doc-av-arrow">↓</div>', unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    etapa_key = st.session_state["etapa_analise_avancada"]
    etapa = ETAPAS_ANALISE_AVANCADA[etapa_key]
    detail_class = "doc-av-detail score-final" if etapa_key == "score_final" else "doc-av-detail"

    with col_detalhe:
        st.markdown(
            f"""
            <div class="{detail_class}">
                <div class="doc-av-kicker">Etapa selecionada</div>
                <div class="doc-av-title">{html.escape(etapa["titulo"])}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        _render_av_field("Objetivo", etapa["objetivo"])
        _render_av_field("Dados utilizados", etapa["dados"])

        st.markdown('<div class="doc-av-section"><div class="doc-av-label">Fórmula matemática ou regra de cálculo</div></div>', unsafe_allow_html=True)
        st.code(etapa["formula"], language="text")

        st.markdown('<div class="doc-av-section"><div class="doc-av-label">Exemplo numérico simplificado</div></div>', unsafe_allow_html=True)
        st.code(etapa["exemplo"], language="text")

        _render_av_field("Interpretação", etapa["interpretacao"])
        st.markdown(
            f"""
            <div class="doc-av-impact">
                <div class="doc-av-label">Impacto no score</div>
                <div class="doc-av-text">{html.escape(etapa["impacto"])}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.warning(etapa["limitacao"])


def _render_indicadores() -> None:
    st.markdown(
        """
        <div class="doc-intro">
            <div class="doc-intro-title">Dicionário de indicadores e demonstrações</div>
            <div class="doc-intro-text">
                Esta aba traduz os indicadores usados no App 4 para uma linguagem prática:
                o que cada número mede, por que ele importa, como interpretar e que tipo de
                cuidado autores clássicos costumam recomendar.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="doc-mini-title">Indicadores usados no score e nas análises</div>', unsafe_allow_html=True)
    grupos = ["Todos"] + sorted({item["Grupo"] for item in INDICADORES})
    grupo = st.radio(
        "Grupo",
        grupos,
        index=0,
        horizontal=True,
        label_visibility="collapsed",
        key="doc_indicadores_grupo",
    )
    indicadores = [
        item for item in INDICADORES
        if grupo == "Todos" or item["Grupo"] == grupo
    ]

    indicador_cards = []
    for item in indicadores:
        # Roxo de Valuation nao tem token; os outros quatro sao a paleta
        # canonica e `cor_token` os troca pelo tom do tema em vigor.
        accent = cor_token(_GROUP_ACCENTS.get(item["Grupo"], "#00C896"))
        indicador_cards.append(
            f'<div class="doc-indicator-card" style="--accent:{accent};">'
            '<div class="doc-indicator-top">'
            f'<div class="doc-indicator-name">{html.escape(item["Indicador"])}</div>'
            f'<div class="doc-indicator-group">{html.escape(item["Grupo"])}</div>'
            '</div>'
            '<div class="doc-field">'
            '<div class="doc-field-label">O que mede</div>'
            f'<div class="doc-field-text">{html.escape(item["O que mede"])}</div>'
            '</div>'
            '<div class="doc-field">'
            '<div class="doc-field-label">Importância na análise</div>'
            f'<div class="doc-field-text">{html.escape(item["Importância"])}</div>'
            '</div>'
            '<div class="doc-field">'
            '<div class="doc-field-label">Como interpretar</div>'
            f'<div class="doc-field-text">{html.escape(item["Leitura"])}</div>'
            '</div>'
            f'<div class="doc-author-note">{html.escape(item["Autores"])}</div>'
            '</div>'
        )
    st.markdown(
        f'<div class="doc-indicator-grid">{"".join(indicador_cards)}</div>',
        unsafe_allow_html=True,
    )

    st.markdown('<div class="doc-mini-title">Demonstrações financeiras e bases auxiliares</div>', unsafe_allow_html=True)
    demonstracao_cards = []
    for item in DEMONSTRACOES:
        demonstracao_cards.append(
            '<div class="doc-statement-card">'
            f'<div class="doc-statement-title">{html.escape(item["Demonstração"])}</div>'
            '<div class="doc-field">'
            '<div class="doc-field-label">Componentes</div>'
            f'<div class="doc-field-text">{html.escape(item["Componentes"])}</div>'
            '</div>'
            '<div class="doc-field">'
            '<div class="doc-field-label">Importância</div>'
            f'<div class="doc-field-text">{html.escape(item["Importância"])}</div>'
            '</div>'
            '<div class="doc-field">'
            '<div class="doc-field-label">Cuidados</div>'
            f'<div class="doc-field-text">{html.escape(item["Cuidados"])}</div>'
            '</div>'
            '</div>'
        )
    st.markdown(
        f'<div class="doc-statement-grid">{"".join(demonstracao_cards)}</div>',
        unsafe_allow_html=True,
    )

    cards = []
    for autor, texto in AUTORES:
        cards.append(
            '<div class="doc-card">'
            f'<div class="doc-card-title">{html.escape(autor)}</div>'
            f'<div class="doc-card-text">{html.escape(texto)}</div>'
            '</div>'
        )
    st.markdown(
        '<div class="doc-mini-title">Como os autores entram na leitura</div>'
        f'<div class="doc-card-grid">{"".join(cards)}</div>'
        '<div class="doc-note">As notas acima são sínteses interpretativas, não citações literais.</div>',
        unsafe_allow_html=True,
    )


def render() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
    container_pagina(
        "Documentação",
        "Fluxogramas clicáveis e explicações para entender as partes complexas do App 4.",
        "📚",
    )

    tab_labels = [
        "Controle financeiro",
        "Carteira atual",
        "Análise avançada (B3)",
        "Simulador (B3)",
        "Criação de portfólio (B3)",
        "Análise de portfólio (B3)",
        "Seleção de FIIs",
        "Empresas Americanas",
        "Portfólio Global",
        "Qualidade dos dados",
        "Configurações",
        "Indicadores",
    ]
    tabs = st.tabs(tab_labels)

    with tabs[0]:
        _render_flow(FLOW_CONTROLE_FINANCEIRO)
    with tabs[1]:
        _render_flow(FLOW_INVESTIMENTOS)
    with tabs[2]:
        render_fluxograma_analise_avancada()
    with tabs[3]:
        _render_flow(FLOW_SIMULADOR)
    with tabs[4]:
        _render_flow(FLOW_CRIACAO_PORTFOLIO)
    with tabs[5]:
        _render_flow(FLOW_ANALISE_PORTFOLIO)
    with tabs[6]:
        _render_flow(FLOW_SELECAO_FIIS)
    with tabs[7]:
        _render_flow(FLOW_EMPRESAS_EUA)
    with tabs[8]:
        _render_flow(FLOW_PORTFOLIO_GLOBAL)
    with tabs[9]:
        _render_flow(FLOW_QUALIDADE_DADOS)
    with tabs[10]:
        _render_flow(FLOW_CONFIGURACOES)
    with tabs[11]:
        _render_indicadores()
