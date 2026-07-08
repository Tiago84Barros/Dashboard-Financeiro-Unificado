"""
views/documentacao.py
Documentacao visual do App 4.

Cria fluxogramas interativos para explicar as partes mais complexas do app:
analise avancada, simulador/criacao de portfolio B3, analise de portfolio e
dicionario de indicadores/demonstracoes financeiras.
"""
from __future__ import annotations

import html
from dataclasses import dataclass

import streamlit as st

from design.componentes import container_pagina


_CSS = """
<style>
.doc-intro {
    background: linear-gradient(135deg, rgba(0,200,150,.10), rgba(74,158,255,.08));
    border: 1px solid rgba(0,200,150,.22);
    border-radius: 10px;
    padding: 18px 20px;
    margin-bottom: 18px;
}
.doc-intro-title {
    color: #E2E8F0;
    font-weight: 850;
    font-size: 1.05rem;
    margin-bottom: 6px;
}
.doc-intro-text {
    color: #AEB8C8;
    font-size: .86rem;
    line-height: 1.55;
}
.doc-flow-shell {
    border: 1px solid #263247;
    background: #101622;
    border-radius: 10px;
    padding: 14px 14px 4px;
}
.doc-row-label {
    color: #64748B;
    font-size: .68rem;
    letter-spacing: .12em;
    text-transform: uppercase;
    font-weight: 800;
    margin: 2px 0 7px;
}
.doc-arrow {
    color: #526176;
    text-align: center;
    font-weight: 800;
    font-size: 1.15rem;
    margin: 1px 0 5px;
}
.doc-detail {
    background: #0C111B;
    border: 1px solid #263247;
    border-radius: 10px;
    padding: 18px 20px;
}
.doc-detail-kicker {
    color: #00C896;
    font-size: .68rem;
    letter-spacing: .12em;
    text-transform: uppercase;
    font-weight: 850;
}
.doc-detail-title {
    color: #F7FAFC;
    font-size: 1.25rem;
    font-weight: 900;
    margin: 4px 0 8px;
}
.doc-detail-body {
    color: #B8C2D2;
    line-height: 1.58;
    font-size: .88rem;
}
.doc-chip {
    display: inline-block;
    border: 1px solid rgba(255,255,255,.12);
    background: rgba(255,255,255,.045);
    color: #D6DCE6;
    border-radius: 999px;
    padding: 3px 9px;
    margin: 4px 5px 0 0;
    font-size: .70rem;
    font-weight: 750;
}
.doc-note {
    color: #718096;
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
    border: 1px solid #263247;
    background: #111827;
    border-radius: 10px;
    padding: 14px 16px;
}
.doc-card-title {
    color: #E2E8F0;
    font-weight: 850;
    font-size: .92rem;
    margin-bottom: 5px;
}
.doc-card-text {
    color: #AEB8C8;
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
    border: 1px solid #28354A;
    background:
        linear-gradient(180deg, rgba(255,255,255,.035), rgba(255,255,255,0)),
        #101722;
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
    background: var(--accent, #00C896);
}
.doc-indicator-top {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 10px;
    margin-bottom: 10px;
}
.doc-indicator-name {
    color: #F7FAFC;
    font-size: 1.08rem;
    font-weight: 900;
    line-height: 1.15;
}
.doc-indicator-group {
    color: var(--accent, #00C896);
    border: 1px solid color-mix(in srgb, var(--accent, #00C896) 42%, transparent);
    background: color-mix(in srgb, var(--accent, #00C896) 13%, transparent);
    border-radius: 999px;
    padding: 3px 9px;
    font-size: .64rem;
    font-weight: 850;
    text-transform: uppercase;
    letter-spacing: .08em;
    white-space: nowrap;
}
.doc-field {
    border-top: 1px solid rgba(148,163,184,.14);
    padding-top: 9px;
    margin-top: 9px;
}
.doc-field-label {
    color: #718096;
    font-size: .63rem;
    font-weight: 850;
    text-transform: uppercase;
    letter-spacing: .10em;
    margin-bottom: 3px;
}
.doc-field-text {
    color: #C6D0DF;
    font-size: .80rem;
    line-height: 1.48;
}
.doc-author-note {
    color: #D7DEE9;
    font-size: .78rem;
    line-height: 1.5;
    padding: 10px 11px;
    border-radius: 8px;
    background: rgba(246,201,14,.07);
    border: 1px solid rgba(246,201,14,.18);
    margin-top: 10px;
}
.doc-statement-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(270px, 1fr));
    gap: 12px;
    margin-top: 12px;
}
.doc-statement-card {
    border: 1px solid #263247;
    background: #0F1622;
    border-radius: 10px;
    padding: 15px 16px;
}
.doc-statement-title {
    color: #E2E8F0;
    font-weight: 900;
    font-size: .96rem;
    margin-bottom: 9px;
}
.doc-av-shell {
    border: 1px solid #263247;
    background: #0F1622;
    border-radius: 10px;
    padding: 16px;
}
.doc-av-flow-title {
    color: #E2E8F0;
    font-size: .82rem;
    font-weight: 850;
    text-transform: uppercase;
    letter-spacing: .10em;
    margin-bottom: 12px;
}
.doc-av-arrow {
    color: #6B7A90;
    text-align: center;
    font-size: 1.35rem;
    font-weight: 900;
    margin: -2px 0 2px;
}
.doc-av-detail {
    border: 1px solid #2B3A51;
    background:
        linear-gradient(180deg, rgba(0,200,150,.07), rgba(74,158,255,.03)),
        #0B1019;
    border-radius: 10px;
    padding: 18px 20px;
}
.doc-av-detail.score-final {
    border-color: rgba(0,200,150,.55);
    box-shadow: 0 0 0 1px rgba(0,200,150,.13), 0 0 34px rgba(0,200,150,.08);
}
.doc-av-kicker {
    color: #00C896;
    font-size: .68rem;
    font-weight: 900;
    letter-spacing: .12em;
    text-transform: uppercase;
    margin-bottom: 3px;
}
.doc-av-title {
    color: #F7FAFC;
    font-size: 1.38rem;
    font-weight: 950;
    margin-bottom: 8px;
}
.doc-av-section {
    border-top: 1px solid rgba(148,163,184,.14);
    padding-top: 10px;
    margin-top: 10px;
}
.doc-av-label {
    color: #718096;
    font-size: .65rem;
    font-weight: 900;
    letter-spacing: .10em;
    text-transform: uppercase;
    margin-bottom: 4px;
}
.doc-av-text {
    color: #C9D3E2;
    font-size: .86rem;
    line-height: 1.56;
}
.doc-av-impact {
    border: 1px solid rgba(74,158,255,.22);
    background: rgba(74,158,255,.07);
    border-radius: 9px;
    padding: 11px 12px;
    margin-top: 11px;
}
.doc-mini-title {
    color: #E2E8F0;
    font-weight: 850;
    font-size: .95rem;
    margin: 18px 0 8px;
}
.stButton > button {
    border-radius: 8px;
    border: 1px solid #2D3A50;
    background: #141B29;
    color: #DCE5F2;
    min-height: 54px;
    font-weight: 800;
    white-space: normal;
    line-height: 1.15;
}
.stButton > button:hover {
    border-color: rgba(0,200,150,.55);
    background: rgba(0,200,150,.10);
    color: #F7FAFC;
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
    title="Analise avancada de empresas B3",
    subtitle=(
        "Mostra como o app transforma dados brutos de empresas em score comparavel, "
        "ranking, simulacao historica e uma leitura de entrada."
    ),
    rows=(
        ("Universo", ("setores", "multiplos", "dre_macro")),
        ("Tratamento", ("limpeza", "slopes", "pesos_setoriais")),
        ("Score", ("percentis", "ajustes", "score_final")),
        ("Validacao", ("backtest", "calibracao", "score_entrada")),
        ("Saida", ("ranking", "explicacao")),
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
            "Sem agrupamento setorial, bancos, varejo, energia e tecnologia seriam comparados como se tivessem a mesma estrutura economica.",
        ),
        "multiplos": _node(
            "multiplos", "Multiplos historicos", "Entrada",
            "Busca indicadores fundamentalistas anuais, com fallback web quando o banco tem lacunas ou outliers.",
            (
                "ROE, ROIC, margens, DY, P/L, P/VP, EV/EBIT",
                "Historico por ano",
                "Auditoria de campos substituidos por Fundamentus",
            ),
            "Os multiplos sao a primeira camada quantitativa: condensam preco, lucro, patrimonio, dividendos e rentabilidade do capital.",
        ),
        "dre_macro": _node(
            "dre_macro", "DRE e macro", "Entrada",
            "Combina demonstracoes financeiras e contexto macroeconomico usado nos ajustes de qualidade e risco.",
            (
                "Receita, EBITDA, EBIT, lucro, divida e caixa",
                "Selic, IPCA, cambio e PIB",
                "Historico com publication lag para evitar olhar o futuro",
            ),
            "A empresa nao existe no vacuo: crescimento, margem e endividamento precisam ser lidos junto com juros, inflacao e ciclo economico.",
        ),
        "limpeza": _node(
            "limpeza", "Limpeza e saneamento", "Preparacao",
            "Remove valores impossiveis, padroniza escalas percentuais e reduz distorcoes de dados contaminados.",
            (
                "Faixas aceitaveis por indicador",
                "DY contaminado ou fora de escala",
                "Imputacao por mediana do grupo quando ha lacunas",
            ),
            "Evita que uma empresa ganhe ou perca score por erro de dado, e nao por qualidade economica real.",
        ),
        "slopes": _node(
            "slopes", "Tendencias historicas", "Preparacao",
            "Calcula slopes log-lineares para medir a direcao de ROE, ROIC e margens ao longo do tempo.",
            (
                "ROE_slope_log",
                "ROIC_slope_log",
                "Margem_Liquida_slope_log",
                "Margem_Operacional_slope_log",
            ),
            "Uma foto atual pode enganar; a tendencia mostra se a qualidade esta melhorando, piorando ou apenas parecendo boa.",
        ),
        "pesos_setoriais": _node(
            "pesos_setoriais", "Pesos por setor", "Preparacao",
            "Escolhe pesos diferentes por tipo de negocio: financeiro, energia, consumo, saude, utilidade publica e outros.",
            (
                "ROE mais relevante em bancos",
                "DY e endividamento mais fortes em utilities",
                "ROIC e margens mais importantes em negocios industriais",
            ),
            "O mesmo indicador nao tem o mesmo significado em todos os setores; a ponderacao tenta respeitar a economia de cada negocio.",
        ),
        "percentis": _node(
            "percentis", "Percentis entre pares", "Score",
            "Converte cada indicador em posicao relativa dentro do grupo comparavel.",
            (
                "Rank percentual",
                "Indicadores em que maior e melhor",
                "Indicadores em que menor e melhor, como P/L, P/VP, EV/EBIT e endividamento",
            ),
            "A pergunta principal vira: esta empresa e melhor ou pior que seus pares no indicador certo?",
        ),
        "ajustes": _node(
            "ajustes", "Ajustes de risco", "Score",
            "Aplica penalidades e ajustes para reduzir concentracao, dados frageis, crowding e sensibilidade macro.",
            (
                "Winsorizacao",
                "Penalidade por valores extremos ou dados insuficientes",
                "Ajuste macro e crowding em multiplos",
            ),
            "A camada protege o ranking contra historias bonitas demais que dependem de uma unica variavel ou de um dado instavel.",
        ),
        "score_final": _node(
            "score_final", "Score final", "Score",
            "Agrega os indicadores ponderados em uma nota de 0 a 100 para ordenar as empresas.",
            (
                "Score bruto",
                "Score ajustado",
                "Versao do score para auditoria",
            ),
            "O score nao substitui analise, mas cria uma triagem objetiva e repetivel para encontrar candidatos.",
        ),
        "backtest": _node(
            "backtest", "Backtest mensal", "Validacao",
            "Simula aportes mensais usando os scores disponiveis no periodo correto, sem usar dados futuros.",
            (
                "Publication lag = 1",
                "Aportes mensais",
                "Comparacao contra Selic e carteira equal-weight",
                "Rank-IC: score vs retorno do ano seguinte",
                "Reinvestimento de dividendos quando disponivel",
            ),
            "Um ranking so tem valor se ele sobreviver minimamente ao passado sem vazamento de informacao futura.",
        ),
        "calibracao": _node(
            "calibracao", "Calibracao", "Validacao",
            "Testa parametros de peso, limite maximo e suavizacao para evitar carteiras concentradas ou superajustadas.",
            (
                "Gamma",
                "Cap por ativo",
                "Soft cap",
                "Walk-forward e shrinkage para defaults",
            ),
            "A calibracao tenta equilibrar retorno, volatilidade, drawdown e custos de transacao.",
        ),
        "score_entrada": _node(
            "score_entrada", "Score de entrada", "Validacao",
            "Combina qualidade, valor, risco e contexto macro para classificar o momento de compra.",
            (
                "Composicao avancada",
                "Status de entrada",
                "Explicacao textual da nota",
            ),
            "Uma boa empresa pode estar cara, alavancada ou em momento ruim; o score de entrada separa qualidade de oportunidade.",
        ),
        "ranking": _node(
            "ranking", "Ranking e lideres", "Saida",
            "Exibe as empresas mais fortes por segmento e permite auditoria dos motivos.",
            (
                "Tabela comparativa",
                "Lideres por score",
                "Indicadores que mais puxaram a nota",
            ),
            "O usuario sai da caixa-preta e consegue ver por que uma empresa apareceu acima de outra.",
        ),
        "explicacao": _node(
            "explicacao", "Explicacao visual", "Saida",
            "Mostra tabelas, graficos, status e alertas para transformar calculo em entendimento.",
            (
                "Graficos Plotly",
                "Cards de status",
                "Auditorias de dados e parametros",
            ),
            "A tela existe para que o usuario consiga discordar do modelo com informacao, nao apenas aceitar um numero.",
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
    title="Criacao de portfolio B3",
    subtitle=(
        "Fluxo inspirado nos seus rascunhos: setores, subsetores e segmentos entram no motor; "
        "o app encontra lideres, testa desempenho e salva uma carteira modelo."
    ),
    rows=(
        ("Dados", ("setores_cp", "historico_cp", "macro_cp")),
        ("Segmentacao", ("setor_cp", "subsetor_cp", "segmento_cp")),
        ("Motor", ("variacao_cp", "score_cp", "lideres_cp")),
        ("Simulacao", ("backtest_cp", "comparacao_cp", "aprovacao_cp")),
        ("Portfolio", ("pesos_cp", "salvar_cp")),
    ),
    default="setores_cp",
    nodes={
        "setores_cp": _node(
            "setores_cp", "Escolha do universo", "Dados",
            "Carrega todas as empresas B3 cobertas e organiza por setor, subsetor e segmento.",
            ("load_setores()", "Tickers elegiveis", "Nome da empresa e classificacao setorial"),
            "E o ponto de partida para que cada empresa seja julgada dentro de um grupo economico justo.",
        ),
        "historico_cp": _node(
            "historico_cp", "Historico de indicadores", "Dados",
            "Busca multiplos e DRE historicos para cada ticker, exigindo um minimo de anos validos.",
            ("load_multiplos_todos()", "load_multiplos_historico_batch()", "Historico DRE minimo"),
            "Sem historico suficiente, o modelo evita aprovar segmentos que parecem bons por uma unica observacao.",
        ),
        "macro_cp": _node(
            "macro_cp", "Cenario macro", "Dados",
            "Carrega Selic e demais variaveis macro para simular benchmark e ajustar o score.",
            ("load_selic_macro()", "load_macro_history()", "Taxa Selic media de fallback"),
            "A comparacao contra Selic e essencial porque o investidor brasileiro sempre tem uma alternativa de renda fixa.",
        ),
        "setor_cp": _node(
            "setor_cp", "Setor", "Segmentacao",
            "Primeiro nivel de agrupamento: bancos, energia, consumo, materiais, saude e outros.",
            ("Pesos setoriais", "Comparacao ampla", "Contexto de negocio"),
            "Define quais indicadores recebem mais peso.",
        ),
        "subsetor_cp": _node(
            "subsetor_cp", "Subsetor", "Segmentacao",
            "Nivel intermediario que refina empresas com dinamicas economicas parecidas.",
            ("Grupo operacional", "Filtro de comparabilidade", "Fallback quando segmento e pequeno"),
            "Ajuda a evitar comparacoes grosseiras dentro de setores grandes.",
        ),
        "segmento_cp": _node(
            "segmento_cp", "Segmento", "Segmentacao",
            "Menor unidade do motor: cada segmento passa por score, lideres e backtest.",
            ("Tickers do segmento", "Score anual", "Historico de lideranca"),
            "E a camada mais proxima do desenho manual: segmento gera variaveis, score, empresas e lider.",
        ),
        "variacao_cp": _node(
            "variacao_cp", "Variaveis do segmento", "Motor",
            "Seleciona indicadores relevantes e calcula score ano a ano com lag de "
            "publicacao, respeitando a data em que cada dado ficou disponivel (point-in-time).",
            ("Pesos do setor", "Snapshot ate N-1", "AvailableAt (vintages) <= abril do ano", "Indicadores saneados"),
            "Garante que o modelo de compra em um ano so use dados que ja existiam "
            "naquela data — sem look-ahead bias.",
        ),
        "score_cp": _node(
            "score_cp", "Score e pesos", "Motor",
            "Ordena empresas, aplica penalidade de lideranca recorrente e calcula pesos proporcionais ao score.",
            ("Decay penalty", "Heuristica top-N", "Gamma tilt", "Cap e soft cap"),
            "O objetivo e escolher lideres sem deixar a carteira virar uma aposta concentrada em uma unica empresa.",
        ),
        "lideres_cp": _node(
            "lideres_cp", "Lideres", "Motor",
            "Identifica a melhor empresa, e opcionalmente a maior participacao historica quando ainda faz sentido.",
            ("Lider por score", "Maior participacao", "Recencia de lideranca", "Rank atual"),
            "Une desempenho quantitativo com continuidade historica do segmento.",
        ),
        "backtest_cp": _node(
            "backtest_cp", "Simulacao mensal", "Simulacao",
            "Reconstrui aportes mensais nos lideres de cada ano e reinveste dividendos quando ha dados.",
            ("Precos mensais yfinance", "Dividendos mensais", "Aporte mensal", "Rebalanceamento anual dos novos aportes"),
            "Transforma a ideia em uma trilha de patrimonio acumulado.",
        ),
        "comparacao_cp": _node(
            "comparacao_cp", "Comparacao", "Simulacao",
            "Compara o patrimonio da estrategia com Tesouro Selic e equal-weight do proprio "
            "segmento — tanto no historico cheio quanto no holdout final de ~24 meses, que e a base da aprovacao.",
            ("Valor estrategia", "Valor Selic", "Valor equal-weight", "Margens no historico", "Margens no holdout OOS ~24m"),
            "Uma empresa lider precisa provar valor contra alternativas simples — e, sobretudo, "
            "fora da janela usada para desenvolver a estrategia.",
        ),
        "aprovacao_cp": _node(
            "aprovacao_cp", "Aprovacao do segmento", "Simulacao",
            "Aprova por HABILIDADE DE SELECAO: bater o Equal-Weight do proprio "
            "segmento com significancia estatistica no holdout OOS de ~24 meses. "
            "Neutro ao macro — se o cenario derrubou o segmento todo, o EW caiu junto.",
            ("Significancia vs Equal-Weight (p-value OOS + FDR q <= 10%)",
             "Rank-IC >= 2 anos positivo (qualidade preve retorno)",
             "Margem vs EW (piso de magnitude opcional)",
             "Margem vs Selic = DIAGNOSTICO (nao reprova)", "Recencia de lideranca"),
            "So entram segmentos cujos lideres superaram os pares (habilidade), com "
            "evidencia preditiva (Rank-IC) e significancia fora da amostra. Bater a "
            "Selic e decisao de timing do investidor, nao criterio de qualidade.",
        ),
        "pesos_cp": _node(
            "pesos_cp", "Montagem do portfolio", "Portfolio",
            "Remove duplicatas, consolida motivos e distribui empresas selecionadas por peso e setor.",
            ("Lista de empresas lideres", "Score medio", "Alpha medio", "Distribuicao setorial"),
            "E a transicao do motor por segmento para uma carteira unica e acionavel.",
        ),
        "salvar_cp": _node(
            "salvar_cp", "Salvar modelo", "Portfolio",
            "Persiste a carteira sugerida como portfolio B3 ativo do usuario.",
            ("b3_portfolio_models", "b3_portfolio_model_items", "Parametros e metricas JSON"),
            "Esse registro vira a base da analise qualitativa e aparece no Dashboard Geral.",
        ),
    },
)


FLOW_SIMULADOR = FlowSpec(
    key="simulador_portfolio",
    title="Modelo de simulacao de portfolio",
    subtitle=(
        "Mostra como o app transforma lideres por segmento em trajetorias de patrimonio, "
        "com aportes, dividendos, benchmarks e regras de aprovacao."
    ),
    rows=(
        ("Preparacao", ("precos_sp", "dividendos_sp", "aportes_sp")),
        ("Carteiras paralelas", ("estrategia_sp", "selic_sp", "equal_weight_sp")),
        ("Tempo", ("rebalance_sp", "cotas_sp", "custos_sp")),
        ("Resultado", ("montante_sp", "margem_sp", "stress_sp")),
    ),
    default="precos_sp",
    nodes={
        "precos_sp": _node(
            "precos_sp", "Precos mensais", "Preparacao",
            "Le fechamentos mensais AJUSTADOS (retorno total) do banco market.* "
            "(market.historical_prices); cai no yfinance so se o market.* nao estiver ativo.",
            ("_batch_yf_precos_mensais()", "Colunas por ticker", "adjusted_close (retorno total)"),
            "Preco ajustado e a ponte entre score teorico e retorno realmente simulado.",
        ),
        "dividendos_sp": _node(
            "dividendos_sp", "Dividendos", "Preparacao",
            "Nao ha passo separado de dividendos: o preco ajustado (adjusted_close) ja "
            "embute proventos e splits reinvestidos, evitando dupla contagem.",
            ("adjusted_close", "Proventos ja embutidos", "Sem reinvestimento duplicado"),
            "Reinvestir dividendos por cima do preco ajustado contaria os proventos duas vezes.",
        ),
        "aportes_sp": _node(
            "aportes_sp", "Aporte mensal", "Preparacao",
            "Todo mes o simulador injeta novo capital na estrategia, Selic e equal-weight.",
            ("Aporte configuravel", "Cotas compradas", "Mes a mes"),
            "A simulacao representa acumulacao recorrente, nao apenas uma compra unica.",
        ),
        "estrategia_sp": _node(
            "estrategia_sp", "Estrategia", "Carteiras paralelas",
            "Compra os lideres definidos pelo score do segmento, com pesos ajustados por score e limites.",
            ("Lideres por ano", "Pesos por score", "Cap por ativo", "Soft cap"),
            "Mostra o resultado da tese principal do modelo.",
        ),
        "selic_sp": _node(
            "selic_sp", "Tesouro Selic", "Carteiras paralelas",
            "Acumula o mesmo aporte pela taxa Selic mensalizada de cada ano.",
            ("Selic anual", "Taxa mensal equivalente", "Benchmark de baixo risco"),
            "E a barra minima para justificar risco de acoes no contexto brasileiro.",
        ),
        "equal_weight_sp": _node(
            "equal_weight_sp", "Equal-weight", "Carteiras paralelas",
            "Distribui aportes igualmente entre todos os ativos disponiveis do segmento.",
            ("Todos os tickers do segmento", "Mesmo peso", "Benchmark simples"),
            "Se o score nao vence uma regra simples, talvez ele esteja apenas complicando o obvio.",
        ),
        "rebalance_sp": _node(
            "rebalance_sp", "Virada de ano", "Tempo",
            "No ano novo, o motor recalcula os lideres com dados disponiveis ate o ano anterior.",
            ("Publication lag", "Troca de lideres", "Novos pesos para novos aportes"),
            "Evita usar demonstracoes financeiras que ainda nao tinham sido publicadas.",
        ),
        "cotas_sp": _node(
            "cotas_sp", "Cotas acumuladas", "Tempo",
            "O simulador acumula quantidade de acoes por ticker e marca a mercado no fim da serie.",
            ("Cotas da estrategia", "Cotas equal-weight", "Valor final por ticker"),
            "Permite ver quais empresas explicaram o patrimonio final.",
        ),
        "custos_sp": _node(
            "custos_sp", "Custos e limites", "Tempo",
            "A analise avancada tambem possui suporte para overhead de transacao, limites e Markowitz.",
            ("Corretagem/spread/IR estimados", "Cap de concentracao", "Min-variance hibrido"),
            "Custos e concentracao impedem que o backtest fique bonito demais e pouco executavel.",
        ),
        "montante_sp": _node(
            "montante_sp", "Montante final", "Resultado",
            "Calcula o valor acumulado de cada carteira paralela no fim da simulacao.",
            ("Valor estrategia", "Valor Selic", "Valor equal-weight", "Contribuicao por ativo"),
            "E o numero que aparece no desenho como montante antes da comparacao.",
        ),
        "margem_sp": _node(
            "margem_sp", "Margens", "Resultado",
            "Transforma montantes em alpha percentual para aprovar ou reprovar segmentos.",
            ("Alpha vs Selic", "Alpha vs equal-weight", "Tabela de auditoria"),
            "Ajuda o usuario a entender nao so quem ganhou, mas por quanto ganhou.",
        ),
        "stress_sp": _node(
            "stress_sp", "Stress tests", "Resultado",
            "Na aba Analise de Investimentos, a carteira atual tambem pode passar por choques historicos.",
            ("Cenarios adversos", "Perda estimada", "Tempo de recuperacao"),
            "E a ponte entre retorno esperado e risco suportavel.",
        ),
    },
)


FLOW_ANALISE_PORTFOLIO = FlowSpec(
    key="analise_portfolio",
    title="Analise qualitativa de portfolio B3",
    subtitle=(
        "Explica como a carteira salva e enriquecida com dados, documentos e LLM para gerar relatorio, "
        "redistribuicao de pesos e conversa com o portfolio."
    ),
    rows=(
        ("Base", ("modelo_ap", "items_ap", "macro_ap")),
        ("Enriquecimento", ("multiplos_ap", "dre_ap", "rag_ap")),
        ("LLM", ("empresa_ap", "portfolio_ap", "json_ap")),
        ("Decisao", ("pesos_ap", "relatorio_ap", "chat_ap")),
    ),
    default="modelo_ap",
    nodes={
        "modelo_ap": _node(
            "modelo_ap", "Portfolio salvo", "Base",
            "Carrega o portfolio B3 ativo salvo na criacao de portfolio.",
            ("load_active_b3_portfolio_model()", "Parametros", "Metricas", "Ano-base"),
            "Sem uma carteira modelo salva, a analise qualitativa nao tem composicao para avaliar.",
        ),
        "items_ap": _node(
            "items_ap", "Empresas e pesos", "Base",
            "Organiza cada ativo com ticker, nome, setor, peso, score e alpha historico.",
            ("Itens do modelo", "Pesos originais", "Score quantitativo", "Alpha vs Selic"),
            "Essa e a fotografia quantitativa antes de chamar a camada qualitativa.",
        ),
        "macro_ap": _node(
            "macro_ap", "Macro atual", "Base",
            "Exibe e injeta no prompt Selic, IPCA, cambio, PIB e variacoes recentes.",
            ("load_macro_history()", "Cards macro", "Contexto para sensibilidade setorial"),
            "A mesma carteira pode ser excelente ou perigosa dependendo do regime de juros, inflacao e cambio.",
        ),
        "multiplos_ap": _node(
            "multiplos_ap", "Multiplos recentes", "Enriquecimento",
            "Carrega historico de multiplos de cada empresa para o prompt e para auditoria.",
            ("load_multiplos_historico_batch()", "Ultimos 3 anos", "ROE, ROIC, margens, DY, valuation"),
            "Da ao LLM a base numerica de rentabilidade, preco e balanco.",
        ),
        "dre_ap": _node(
            "dre_ap", "DRE", "Enriquecimento",
            "Busca demonstracoes financeiras por empresa para mostrar crescimento, lucro, EBITDA e divida.",
            ("load_financials_batch()", "Receita", "EBITDA", "Lucro", "Divida"),
            "Ajuda a diferenciar empresa barata de empresa deteriorando.",
        ),
        "rag_ap": _node(
            "rag_ap", "Documentos CVM/IPE", "Enriquecimento",
            "Recupera trechos relevantes de documentos corporativos para enriquecer a analise.",
            ("retrieve_chunks()", "format_rag_context()", "Cobertura documental"),
            "Acrescenta fatos textuais que nao aparecem nos multiplos, como eventos, riscos e comunicados.",
        ),
        "empresa_ap": _node(
            "empresa_ap", "Analise por empresa", "LLM",
            "Chama o modelo para cada ativo e pede perspectiva, riscos, catalisadores, confianca e alocacao sugerida.",
            ("analisar_empresa()", "JSON estruturado", "Perspectiva forte/moderada/fraca", "Acao sugerida"),
            "Transforma dados quantitativos em uma tese legivel e comparavel por ativo.",
        ),
        "portfolio_ap": _node(
            "portfolio_ap", "Analise consolidada", "LLM",
            "Depois das empresas, o LLM avalia o portfolio como conjunto.",
            ("analisar_portfolio()", "Qualidade da carteira", "Perspectiva 12m", "Pontos fortes e fracos"),
            "Uma boa lista de empresas nao garante uma boa carteira; o conjunto precisa ser coerente.",
        ),
        "json_ap": _node(
            "json_ap", "Fallback e validacao", "LLM",
            "A resposta e parseada como JSON; se falhar, o app usa fallback estruturado para nao quebrar a tela.",
            ("_parse_json()", "Fallback empresa", "Fallback portfolio"),
            "Mantem a experiencia estavel mesmo quando a IA responde fora do formato esperado.",
        ),
        "pesos_ap": _node(
            "pesos_ap", "Redistribuicao", "Decisao",
            "Combina score quantitativo, score qualitativo, confianca, alpha e perspectiva para sugerir novos pesos.",
            ("60% quanti + 40% quali", "Multiplicador por perspectiva", "Modo rigido/flexivel", "Min e max por ativo"),
            "Ajuda a transformar analise em acao: manter, aumentar, reduzir ou revisar.",
        ),
        "relatorio_ap": _node(
            "relatorio_ap", "Relatorio", "Decisao",
            "Mostra sintese executiva, papel dos ativos, riscos, catalisadores e conclusao estrategica.",
            ("Relatorio consolidado", "Cards de alocacao", "Tags de riscos e catalisadores"),
            "Entrega uma leitura de gestor, nao apenas uma tabela.",
        ),
        "chat_ap": _node(
            "chat_ap", "Chat com portfolio", "Decisao",
            "Permite tirar duvidas sobre a carteira usando o contexto ja montado.",
            ("chat_com_portfolio()", "Historico da conversa", "Contexto do portfolio"),
            "Fecha o ciclo educativo: o usuario pode perguntar por que algo foi sugerido.",
        ),
    },
)


FLOW_INVESTIMENTOS = FlowSpec(
    key="analise_investimentos",
    title="Analise da carteira atual de investimentos",
    subtitle=(
        "Mostra como a aba Investimentos le a carteira real, consolida posicoes e apresenta risco, "
        "distribuicao, exposicao macro e stress tests."
    ),
    rows=(
        ("Fontes", ("positions_ai", "quotes_ai", "dividends_ai")),
        ("Consolidacao", ("snapshot_ai", "classes_ai", "setores_ai")),
        ("Analise", ("rentabilidade_ai", "risco_ai", "stress_ai")),
        ("Saida", ("dashboard_ai", "tabelas_ai", "alertas_ai")),
    ),
    default="positions_ai",
    nodes={
        "positions_ai": _node(
            "positions_ai", "Posicoes", "Fontes",
            "Le portfolio_positions ou snapshots importados da corretora para montar a carteira atual.",
            ("Quantidade", "Preco medio", "Total investido", "Moeda"),
            "E a base patrimonial: sem posicao correta, toda analise fica torta.",
        ),
        "quotes_ai": _node(
            "quotes_ai", "Cotacoes", "Fontes",
            "Busca a cotacao mais recente de cada ativo e converte USD quando necessario.",
            ("asset_quotes", "Preco atual", "FX USD/BRL", "Fallbacks"),
            "Marca a carteira a mercado e permite comparar custo com valor atual.",
        ),
        "dividends_ai": _node(
            "dividends_ai", "Proventos", "Fontes",
            "Carrega dividendos e JCP para mostrar renda, yield on cost e historico.",
            ("dividends", "Eventos", "YoC", "Proventos por ativo"),
            "Renda recebida e parte relevante do retorno total.",
        ),
        "snapshot_ai": _node(
            "snapshot_ai", "Snapshot consolidado", "Consolidacao",
            "Agrupa tickers fracionarios, reconcilia custo e posicao e classifica ativos.",
            ("BBAS3 + BBAS3F", "Venda parcial", "Historico incompleto", "Tesouro por prefixo"),
            "Resolve detalhes operacionais antes de mostrar numeros finais.",
        ),
        "classes_ai": _node(
            "classes_ai", "Classes", "Consolidacao",
            "Agrupa por Acoes BR, FII, ETF, Tesouro, Renda Fixa, Exterior e outros.",
            ("Valor por classe", "Percentual da carteira", "Rentabilidade por classe"),
            "Ajuda a enxergar a alocacao antes de olhar ativo por ativo.",
        ),
        "setores_ai": _node(
            "setores_ai", "Setores", "Consolidacao",
            "Agrupa acoes e FIIs por setor para medir concentracao economica.",
            ("Setor", "Valor de mercado", "Percentual da carteira"),
            "Duas empresas diferentes podem ter o mesmo risco setorial escondido.",
        ),
        "rentabilidade_ai": _node(
            "rentabilidade_ai", "Rentabilidade", "Analise",
            "Calcula retorno sobre custo, evolucao patrimonial e comparacoes internas.",
            ("Rentabilidade total", "TWRR/evolucao", "Top 10 contribuidores"),
            "Mostra se a carteira esta ganhando dinheiro e onde.",
        ),
        "risco_ai": _node(
            "risco_ai", "Risco e concentracao", "Analise",
            "Mede concentracao por ativo, classe e setor, alem de indicadores de dependencia macro.",
            ("Top 1", "Top 5", "HHI", "Dependencias macro"),
            "Ajuda a ver riscos que uma rentabilidade positiva pode esconder.",
        ),
        "stress_ai": _node(
            "stress_ai", "Stress tests", "Analise",
            "Aplica choques historicos simplificados para estimar perda e recuperacao.",
            ("Crises historicas", "Perda percentual", "Perda em R$", "Tempo de recuperacao"),
            "Responde a pergunta que importa no susto: quanto isso pode cair?",
        ),
        "dashboard_ai": _node(
            "dashboard_ai", "Dashboard", "Saida",
            "Resume patrimonio, retorno, proventos, distribuicao e alertas visuais.",
            ("KPIs", "Graficos", "Badges de fonte", "Atualizacao"),
            "Da uma visao rapida para quem quer decidir o proximo passo.",
        ),
        "tabelas_ai": _node(
            "tabelas_ai", "Tabelas", "Saida",
            "Permite auditar cada posicao com quantidade, preco medio, mercado, lucro e participacao.",
            ("Carteira detalhada", "Filtros", "Ordenacao", "Download visual via dataframe"),
            "A transparencia fica no nivel do ativo.",
        ),
        "alertas_ai": _node(
            "alertas_ai", "Alertas", "Saida",
            "Aponta concentracao, falta de cotacao, queda, dependencia e outras situacoes relevantes.",
            ("Severidade", "Mensagem", "Modulo de origem"),
            "Transforma analise em lista de pontos que merecem atencao.",
        ),
    },
)


FLOWS = (
    FLOW_ANALISE_AVANCADA,
    FLOW_SIMULADOR,
    FLOW_CRIACAO_PORTFOLIO,
    FLOW_ANALISE_PORTFOLIO,
    FLOW_INVESTIMENTOS,
)


INDICADORES = [
    {
        "Grupo": "Rentabilidade",
        "Indicador": "ROE",
        "O que mede": "Lucro liquido dividido pelo patrimonio liquido.",
        "Importancia": "Mostra quanto retorno a empresa gera sobre o capital dos acionistas.",
        "Leitura": "Maior costuma ser melhor, mas precisa ser sustentavel e nao vir apenas de alavancagem.",
        "Autores": "Graham e Buffett tratam retorno consistente sobre capital como sinal de qualidade; Lynch compara esse retorno com crescimento, divida e preco.",
    },
    {
        "Grupo": "Rentabilidade",
        "Indicador": "ROIC",
        "O que mede": "Retorno sobre o capital investido na operacao.",
        "Importancia": "Ajuda a medir eficiencia economica do negocio independentemente da estrutura de financiamento.",
        "Leitura": "ROIC alto e recorrente sugere vantagem competitiva; ROIC em queda pode indicar perda de moat ou ciclo ruim.",
        "Autores": "Damodaran e Greenblatt dao grande peso ao retorno sobre capital para separar empresas excelentes de negocios medianos.",
    },
    {
        "Grupo": "Rentabilidade",
        "Indicador": "ROA",
        "O que mede": "Lucro liquido dividido pelos ativos totais.",
        "Importancia": "Mostra eficiencia no uso dos ativos, util para empresas intensivas em capital.",
        "Leitura": "Deve ser comparado dentro do setor; bancos e industrias tem bases de ativos muito diferentes.",
        "Autores": "Graham reforca comparacao historica e setorial para evitar conclusoes por numeros isolados.",
    },
    {
        "Grupo": "Margens",
        "Indicador": "Margem Liquida",
        "O que mede": "Lucro liquido como percentual da receita.",
        "Importancia": "Resume quanto da venda vira lucro depois de custos, despesas, juros e impostos.",
        "Leitura": "Margem alta e estavel indica poder de precificacao; margem volatil exige cautela.",
        "Autores": "Lynch procura entender a historia operacional por tras das margens; Buffett valoriza negocios com poder de preco.",
    },
    {
        "Grupo": "Margens",
        "Indicador": "Margem Operacional",
        "O que mede": "Resultado operacional dividido pela receita.",
        "Importancia": "Isola a qualidade da operacao antes de efeitos financeiros e impostos.",
        "Leitura": "Boa para comparar eficiencia entre pares do mesmo setor.",
        "Autores": "Damodaran usa margens e crescimento para estimar qualidade operacional e valor intrinseco.",
    },
    {
        "Grupo": "Dividendos",
        "Indicador": "DY",
        "O que mede": "Dividendos pagos nos ultimos 12 meses divididos pelo preco.",
        "Importancia": "Mostra a renda de dividendos em relacao ao preco pago.",
        "Leitura": "DY alto pode ser oportunidade ou alerta de lucro nao recorrente e preco deprimido.",
        "Autores": "Siegel destaca dividendos no retorno de longo prazo; Graham gostava de historico consistente, nao de yield isolado.",
    },
    {
        "Grupo": "Dividendos",
        "Indicador": "Payout",
        "O que mede": "Percentual do lucro distribuido como dividendos/JCP.",
        "Importancia": "Mostra quanto lucro e retido para reinvestimento versus distribuido.",
        "Leitura": "Payout muito alto pode limitar crescimento ou ser insustentavel; em utilities pode ser normal.",
        "Autores": "Lynch sugere olhar a capacidade de reinvestimento; Damodaran separa empresas maduras de empresas de crescimento.",
    },
    {
        "Grupo": "Valuation",
        "Indicador": "P/L",
        "O que mede": "Preco da acao dividido pelo lucro por acao.",
        "Importancia": "Indica quantos anos de lucro o investidor esta pagando, em termos simplificados.",
        "Leitura": "Menor pode ser mais barato, mas tambem pode indicar risco, ciclo ou lucro temporario.",
        "Autores": "Graham usa multiplos com margem de seguranca; Lynch popularizou relacionar P/L com crescimento esperado.",
    },
    {
        "Grupo": "Valuation",
        "Indicador": "P/VP",
        "O que mede": "Valor de mercado dividido pelo patrimonio liquido.",
        "Importancia": "Ajuda a avaliar preco versus base contabil, especialmente bancos e negocios patrimoniais.",
        "Leitura": "Baixo pode indicar desconto ou baixa rentabilidade; alto exige ROE superior e sustentavel.",
        "Autores": "Graham usava valor patrimonial como ancora defensiva; Buffett aceita pagar mais por negocios superiores.",
    },
    {
        "Grupo": "Valuation",
        "Indicador": "EV/EBIT",
        "O que mede": "Valor da firma dividido pelo lucro operacional.",
        "Importancia": "Compara preco do negocio inteiro, incluindo divida, com resultado operacional.",
        "Leitura": "Util para comparar empresas com estruturas de capital diferentes.",
        "Autores": "Greenblatt usa rendimento operacional sobre valor da firma como uma de suas ideias centrais.",
    },
    {
        "Grupo": "Valuation",
        "Indicador": "P/FCO",
        "O que mede": "Preco dividido pelo fluxo de caixa operacional.",
        "Importancia": "Avalia preco contra caixa gerado pela operacao, reduzindo distorcoes contabeis do lucro.",
        "Leitura": "Pode ser mais robusto que P/L em empresas com lucro contabel volátil.",
        "Autores": "Buffett e Munger enfatizam caixa e economia real do negocio acima de lucro meramente contabil.",
    },
    {
        "Grupo": "Solvencia",
        "Indicador": "Endividamento Total",
        "O que mede": "Divida em relacao a capital, patrimonio ou metrica equivalente usada no banco.",
        "Importancia": "Mostra fragilidade financeira e sensibilidade a juros.",
        "Leitura": "Menor tende a ser melhor, mas concessoes, utilities e bancos exigem leitura setorial.",
        "Autores": "Graham valorizava balancos fortes; Marks reforca que risco aparece quando divida encontra ciclo adverso.",
    },
    {
        "Grupo": "Solvencia",
        "Indicador": "Liquidez Corrente",
        "O que mede": "Ativos circulantes divididos por passivos circulantes.",
        "Importancia": "Indica folga de curto prazo para cumprir obrigacoes.",
        "Leitura": "Muito baixa pode sinalizar aperto; muito alta pode indicar capital parado.",
        "Autores": "Graham via liquidez como camada de protecao para o investidor defensivo.",
    },
]


DEMONSTRACOES = [
    {
        "Demonstracao": "DRE",
        "Componentes": "Receita, custos, despesas, EBITDA, EBIT, lucro liquido.",
        "Importancia": "Mostra a formacao do lucro e a eficiencia operacional.",
        "Cuidados": "Lucro pode ser afetado por nao recorrentes, ciclo, cambio e efeitos contabeis.",
    },
    {
        "Demonstracao": "Balanco Patrimonial",
        "Componentes": "Ativos, passivos, patrimonio liquido, divida, caixa e capital de giro.",
        "Importancia": "Mostra estrutura financeira, solvencia e base de capital.",
        "Cuidados": "Patrimonio contabil pode subestimar marcas fortes ou superestimar ativos ruins.",
    },
    {
        "Demonstracao": "Fluxo de Caixa",
        "Componentes": "FCO, FCI, FCF, capex, variacao de caixa.",
        "Importancia": "Mostra se o lucro vira dinheiro e quanto sobra para crescer, pagar divida ou distribuir.",
        "Cuidados": "Fluxo de um ano isolado pode ser distorcido por capital de giro ou eventos extraordinarios.",
    },
    {
        "Demonstracao": "Historico de Dividendos",
        "Componentes": "Dividendos, JCP, frequencia, yield on cost e payout.",
        "Importancia": "Ajuda a medir disciplina de capital e retorno ao acionista.",
        "Cuidados": "Dividendos altos sem lucro e caixa recorrentes podem ser armadilha.",
    },
    {
        "Demonstracao": "Contexto Macro",
        "Componentes": "Selic, IPCA, cambio e PIB.",
        "Importancia": "Ajusta a leitura de valuation, divida, crescimento e atratividade relativa da renda fixa.",
        "Cuidados": "Macro nao deve substituir a analise da empresa, mas pode mudar o preco justo e o risco.",
    },
]


AUTORES = [
    ("Benjamin Graham", "Margem de seguranca, balanco forte, lucros consistentes e preco razoavel antes de otimismo."),
    ("Warren Buffett e Charlie Munger", "Qualidade do negocio, retorno sobre capital, vantagem competitiva e caixa real no longo prazo."),
    ("Peter Lynch", "Entender a historia da empresa, crescimento, P/L em relacao ao crescimento, divida e dividendos."),
    ("Aswath Damodaran", "Valor depende de fluxo de caixa, crescimento, risco e reinvestimento; multiplos precisam de narrativa."),
    ("Joel Greenblatt", "Combinar qualidade do negocio com preco pago, usando retorno sobre capital e rendimento operacional."),
    ("Howard Marks", "Risco, ciclos, margem para erro e disciplina importam tanto quanto retorno projetado."),
    ("Jeremy Siegel", "Dividendos, reinvestimento e horizonte longo explicam parte importante do retorno das acoes."),
]


_GROUP_ACCENTS = {
    "Rentabilidade": "#00C896",
    "Margens": "#4A9EFF",
    "Dividendos": "#F6C90E",
    "Valuation": "#B084F5",
    "Solvencia": "#FC5C7D",
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
        "formula": "Capital novo do mes = aporte mensal configurado\nCotas compradas = aporte mensal / preco do ativo",
        "exemplo": "Aporte mensal = R$ 1.000\nPreco do ativo = R$ 25\nCotas compradas = 1.000 / 25 = 40 cotas",
        "interpretacao": "O simulador reproduz acumulacao recorrente, aproximando a experiencia de quem investe todo mes.",
    },
    "selic_sp": {
        "formula": "Valor acumulado = valor anterior x (1 + taxa Selic mensal) + aporte do mes",
        "exemplo": "Valor anterior = R$ 10.000\nSelic mensal = 0,80%\nAporte = R$ 1.000\nValor = 10.000 x 1,008 + 1.000 = R$ 11.080",
        "interpretacao": "A estrategia de acoes precisa superar uma alternativa simples de renda fixa para justificar o risco.",
    },
    "montante_sp": {
        "formula": "Montante final = soma(cotas do ativo x preco final do ativo) + caixa residual",
        "exemplo": "Ativo A: 100 cotas x R$ 30 = R$ 3.000\nAtivo B: 80 cotas x R$ 25 = R$ 2.000\nMontante final = R$ 5.000",
        "interpretacao": "O montante mostra o patrimonio acumulado da carteira ao fim da simulacao.",
    },
    "margem_sp": {
        "formula": "Margem vs benchmark = ((montante da estrategia - montante benchmark) / montante benchmark) x 100",
        "exemplo": "Estrategia = R$ 120.000\nSelic = R$ 100.000\nMargem = ((120.000 - 100.000) / 100.000) x 100 = 20%",
        "interpretacao": "A margem indica quanto a estrategia adicionou ou perdeu em relacao a uma alternativa comparavel.",
    },
    "backtest_cp": {
        "formula": "Retorno acumulado = ((valor final - total aportado) / total aportado) x 100",
        "exemplo": "Total aportado = R$ 60.000\nValor final = R$ 78.000\nRetorno acumulado = ((78.000 - 60.000) / 60.000) x 100 = 30%",
        "interpretacao": "O backtest traduz a selecao dos lideres em uma trilha historica de patrimonio.",
    },
    "comparacao_cp": {
        "formula": "Alpha = retorno da estrategia - retorno do benchmark",
        "exemplo": "Retorno da estrategia = 18%\nRetorno Selic = 11%\nAlpha = 18% - 11% = 7 p.p.",
        "interpretacao": "A comparacao mostra se a carteira criada gerou retorno adicional depois de considerar alternativas simples.",
    },
    "aprovacao_cp": {
        "formula": "Segmento aprovado se margem minima, recencia e criterios de benchmark forem atendidos",
        "exemplo": "Margem minima exigida = 5 p.p.\nMargem observada = 8 p.p.\nUltima lideranca recente = sim\nResultado: segmento aprovado",
        "interpretacao": "A aprovacao impede que um segmento entre na carteira apenas por um resultado isolado.",
    },
    "pesos_cp": {
        "formula": "Peso do ativo = score relativo do ativo / soma dos scores selecionados",
        "exemplo": "Empresa A score 80, Empresa B score 70\nPeso A = 80 / (80 + 70) = 53,3%",
        "interpretacao": "Empresas mais fortes recebem mais peso, mas a carteira ainda respeita limites de concentracao.",
    },
    "items_ap": {
        "formula": "Participacao do ativo = valor de mercado do ativo / valor total do portfolio",
        "exemplo": "Valor do ativo = R$ 12.000\nPortfolio total = R$ 100.000\nParticipacao = 12.000 / 100.000 = 12%",
        "interpretacao": "A participacao mostra o tamanho real de cada tese dentro da carteira salva.",
    },
    "pesos_ap": {
        "formula": "Score combinado = (score quantitativo x 60%) + (score qualitativo x 40%)",
        "exemplo": "Score quanti = 80\nScore quali = 70\nScore combinado = 80 x 0,60 + 70 x 0,40 = 76",
        "interpretacao": "A redistribuicao combina dados historicos com leitura qualitativa para sugerir novos pesos.",
    },
    "snapshot_ai": {
        "formula": "Valor de mercado = quantidade consolidada x cotacao atual",
        "exemplo": "Quantidade = 300\nCotacao atual = R$ 18\nValor de mercado = 300 x 18 = R$ 5.400",
        "interpretacao": "O snapshot transforma operacoes dispersas em uma posicao unica e auditavel.",
    },
    "classes_ai": {
        "formula": "Peso da classe = valor da classe / valor total da carteira",
        "exemplo": "Acoes BR = R$ 45.000\nCarteira total = R$ 150.000\nPeso = 45.000 / 150.000 = 30%",
        "interpretacao": "A leitura por classe revela a arquitetura da carteira antes da analise por ativo.",
    },
    "rentabilidade_ai": {
        "formula": "Rentabilidade = ((valor atual + proventos - custo total) / custo total) x 100",
        "exemplo": "Valor atual = R$ 11.000\nProventos = R$ 500\nCusto = R$ 10.000\nRentabilidade = ((11.000 + 500 - 10.000) / 10.000) x 100 = 15%",
        "interpretacao": "A rentabilidade considera ganho de capital e renda recebida quando os dados estao disponiveis.",
    },
    "risco_ai": {
        "formula": "Concentracao Top 5 = soma dos pesos dos 5 maiores ativos",
        "exemplo": "Pesos dos 5 maiores = 18% + 14% + 10% + 8% + 6%\nConcentracao Top 5 = 56%",
        "interpretacao": "Quanto maior a concentracao, maior a dependencia de poucas posicoes.",
    },
    "stress_ai": {
        "formula": "Perda estimada = valor atual da carteira x choque do cenario",
        "exemplo": "Carteira = R$ 200.000\nChoque = -18%\nPerda estimada = 200.000 x 18% = R$ 36.000",
        "interpretacao": "O stress test ajuda a medir se a carteira e compativel com o risco que o usuario suporta.",
    },
}


def _generic_flow_detail(flow: FlowSpec, node: FlowNode) -> dict[str, str]:
    dados = "\n".join(f"- {item}" for item in node.contains)
    formula = (
        "Saida da etapa = dados validados + regra da etapa + passagem para a proxima camada\n"
        f"Camada atual = {node.layer}"
    )
    exemplo = (
        f"Etapa: {node.title}\n"
        f"Entrada: informacoes da camada {node.layer}\n"
        f"Processamento: {node.summary}\n"
        "Saida: dado organizado para a proxima etapa do fluxo."
    )
    detail = {
        "titulo": node.title,
        "objetivo": node.summary,
        "dados": dados or "Dados consolidados da etapa anterior.",
        "formula": formula,
        "exemplo": exemplo,
        "interpretacao": node.why,
        "impacto": (
            "Define a qualidade da informacao que avanca no fluxo e influencia a confiabilidade "
            "das conclusoes seguintes."
        ),
        "limitacao": (
            "Esta etapa deve ser lida dentro do contexto do fluxo completo. Dados incompletos, "
            "defasados ou muito concentrados podem distorcer a conclusao."
        ),
    }
    detail.update(_FLOW_DETAIL_OVERRIDES.get(node.id, {}))
    if flow.key == "simulador_portfolio":
        detail["impacto"] = "Afeta o patrimonio simulado, a comparacao com benchmarks e a aprovacao historica da estrategia."
    elif flow.key == "criacao_portfolio":
        detail["impacto"] = "Afeta a selecao dos lideres, a distribuicao de pesos e a carteira modelo que sera salva."
    elif flow.key == "analise_portfolio":
        detail["impacto"] = "Afeta a leitura qualitativa, a redistribuicao sugerida e o relatorio final do portfolio."
    elif flow.key == "analise_investimentos":
        detail["impacto"] = "Afeta os KPIs, os graficos, os alertas e a interpretacao da carteira atual."
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
                Cada bloco e clicavel e atualiza o painel explicativo com objetivo, dados,
                regra de calculo, exemplo, interpretacao e cuidados de leitura.
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
            '<div class="doc-av-shell"><div class="doc-av-flow-title">Sequencia do fluxo</div>',
            unsafe_allow_html=True,
        )
        for idx, (node_id, label) in enumerate(sequence):
            node = flow.nodes[node_id]
            selected = st.session_state[selected_key] == node_id
            button_label = f"{node.layer}: {label}"
            st.button(
                button_label,
                key=f"doc_fluxo_{flow.key}_{node_id}",
                use_container_width=True,
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

        st.markdown('<div class="doc-av-section"><div class="doc-av-label">Formula matematica ou regra de calculo</div></div>', unsafe_allow_html=True)
        st.code(etapa["formula"], language="text")

        st.markdown('<div class="doc-av-section"><div class="doc-av-label">Exemplo numerico simplificado</div></div>', unsafe_allow_html=True)
        st.code(etapa["exemplo"], language="text")

        _render_av_field("Interpretacao", etapa["interpretacao"])
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
                use_container_width=True,
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
            <div class="doc-intro-title">Dicionario de indicadores e demonstracoes</div>
            <div class="doc-intro-text">
                Esta aba traduz os indicadores usados no App 4 para uma linguagem pratica:
                o que cada numero mede, por que ele importa, como interpretar e que tipo de
                cuidado autores classicos costumam recomendar.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="doc-mini-title">Indicadores usados no score e nas analises</div>', unsafe_allow_html=True)
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
        accent = _GROUP_ACCENTS.get(item["Grupo"], "#00C896")
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
            '<div class="doc-field-label">Importancia na analise</div>'
            f'<div class="doc-field-text">{html.escape(item["Importancia"])}</div>'
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

    st.markdown('<div class="doc-mini-title">Demonstracoes financeiras e bases auxiliares</div>', unsafe_allow_html=True)
    demonstracao_cards = []
    for item in DEMONSTRACOES:
        demonstracao_cards.append(
            '<div class="doc-statement-card">'
            f'<div class="doc-statement-title">{html.escape(item["Demonstracao"])}</div>'
            '<div class="doc-field">'
            '<div class="doc-field-label">Componentes</div>'
            f'<div class="doc-field-text">{html.escape(item["Componentes"])}</div>'
            '</div>'
            '<div class="doc-field">'
            '<div class="doc-field-label">Importancia</div>'
            f'<div class="doc-field-text">{html.escape(item["Importancia"])}</div>'
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
        '<div class="doc-note">As notas acima sao sinteses interpretativas, nao citacoes literais.</div>',
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
        "Análise avançada",
        "Simulador",
        "Criação de portfólio",
        "Análise de portfólio",
        "Carteira atual",
        "Indicadores",
    ]
    tabs = st.tabs(tab_labels)

    with tabs[0]:
        render_fluxograma_analise_avancada()
    with tabs[1]:
        _render_flow(FLOW_SIMULADOR)
    with tabs[2]:
        _render_flow(FLOW_CRIACAO_PORTFOLIO)
    with tabs[3]:
        _render_flow(FLOW_ANALISE_PORTFOLIO)
    with tabs[4]:
        _render_flow(FLOW_INVESTIMENTOS)
    with tabs[5]:
        _render_indicadores()
