"""Relatório institucional da Avaliação de Portfólio das Empresas Americanas.

Espelha ``core/portfolio_report_b3.py``: mesmo contrato de saída, mesmas
dimensões de score, mesmos cenários — a maquinaria comum vive em
``core/portfolio_report_common.py``. O que muda aqui é tudo que é intrínseco
ao mercado americano:

* moeda e escala em dólares, não reais;
* demonstrações anuais SEC/US GAAP (10-K) por ``fiscal_year``, não DRE societária;
* pares por **indústria SEC/SIC**, não por segmento B3;
* macro do Fed (juros, CPI, PIB real, desemprego, curva 10a-2a, spread de
  crédito), não Selic/IPCA/câmbio;
* referência de custo de oportunidade é o Treasury de 3 meses e o benchmark é
  o S&P 500, não Selic e Ibovespa;
* **não há RAG de documentos**: a CVM/IPE não tem equivalente indexado aqui. A
  camada de evidência é o dossiê determinístico mais o laboratório avançado
  (Piotroski, Altman, Sloan, ROIC incremental), tudo calculado em código.

Offline-first, como o resto do módulo americano: nenhuma chamada de rede sai
daqui. Sem dado local, o relatório degrada com a lacuna declarada em vez de
inventar.
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from core.llm_b3 import _call_llm, _parse_json, _report_model
from core.portfolio_report_common import (
    QUALITATIVE_WEIGHTS,
    company_summary_for_portfolio,
    fallback_company,
    fallback_portfolio,
    format_number,
    prioritize_peer_tickers,
    safe_float,
    sanitize_company_report,
    sanitize_portfolio_report,
    weights_contract,
)
from core.us_dossie import build_dossie, dossie_to_text

logger = logging.getLogger(__name__)

# Métricas do cross-section usadas na comparação por pares. Nomes vêm de
# core.us_metrics.compute_company_metrics.
_PEER_METRICS: tuple[tuple[str, str, str], ...] = (
    ("pe", "P/L", "x"),
    ("ev_ebit", "EV/EBIT", "x"),
    ("ev_ebitda", "EV/EBITDA", "x"),
    ("p_fcf", "P/FCL", "x"),
    ("fcf_yield", "Retorno do FCL", "%"),
    ("roe", "ROE", "%"),
    ("roic", "ROIC", "%"),
    ("net_margin", "Margem líquida", "%"),
    ("revenue_cagr_3y", "Cresc. receita 3a", "%"),
    ("net_debt_ebitda", "Dív.líq/EBITDA", "x"),
    ("shareholder_yield", "Retorno ao acionista", "%"),
)


def _fmt_metric(value: Any, unit: str) -> str:
    number = safe_float(value)
    if number is None:
        return "N/D"
    if unit == "%":
        return f"{number * 100:.1f}%" if abs(number) <= 2.0 else f"{number:.1f}%"
    return f"{number:.2f}x"


# ─────────────────────────────────────────────────────────────────────────────
# Pares por indústria SEC
# ─────────────────────────────────────────────────────────────────────────────

def compute_industry_peers(
    scored: pd.DataFrame | None,
    ticker: str,
    max_peers: int = 12,
) -> tuple[list[str], str]:
    """Pares da MESMA indústria; cai para o setor quando a indústria é rala.

    Equivale a ``compute_segment_peers`` da B3. A hierarquia americana é
    indústria → setor, não segmento → subsetor → setor, porque a classificação
    que existe no warehouse é a da SEC consolidada em setores.
    """
    tk = str(ticker).strip().upper()
    if scored is None or scored.empty or "symbol" not in scored.columns:
        return [], ""
    frame = scored.copy()
    frame["symbol"] = frame["symbol"].astype(str).str.upper()
    linha = frame[frame["symbol"] == tk]
    if linha.empty:
        return [], ""
    alvo = linha.iloc[0]

    for coluna, nivel in (("industry", "indústria SEC"), ("sector", "setor")):
        if coluna not in frame.columns:
            continue
        valor = alvo.get(coluna)
        if not valor or pd.isna(valor):
            continue
        mesmos = frame[frame[coluna] == valor]
        mesmos = mesmos[mesmos["symbol"] != tk]
        # Mesmo limiar da hierarquia B3: com menos de 3 pares a mediana da
        # indústria é ruído, e comparar contra ruído é pior do que subir um
        # nível. Indústria SEC é granular — nicho com dois nomes é comum.
        if len(mesmos) >= 3 or nivel == "setor":
            if "score" in mesmos.columns:
                mesmos = mesmos.sort_values("score", ascending=False)
            return mesmos["symbol"].head(max_peers).tolist(), nivel
    return [], ""


def build_us_fundamentals_context(
    scored: pd.DataFrame | None,
    tickers: list[str],
    max_n: int = 12,
) -> str:
    """Múltiplos e retornos das empresas consultadas, direto do cross-section."""
    tks = list(dict.fromkeys(str(t).strip().upper() for t in (tickers or []) if t))[:max_n]
    if not tks or scored is None or scored.empty or "symbol" not in scored.columns:
        return "FUNDAMENTOS DOS PARES: indisponíveis no warehouse local."
    frame = scored.copy()
    frame["symbol"] = frame["symbol"].astype(str).str.upper()
    sub = frame[frame["symbol"].isin(tks)]
    if sub.empty:
        return f"FUNDAMENTOS: sem dados locais para {', '.join(tks)}."
    linhas = ["FUNDAMENTOS (warehouse local, últimas demonstrações anuais SEC):"]
    for _, row in sub.iterrows():
        setor = row.get("industry") or row.get("sector") or ""
        inds = " | ".join(
            f"{label}={_fmt_metric(row.get(key), unit)}"
            for key, label, unit in _PEER_METRICS
            if safe_float(row.get(key)) is not None
        )
        score = safe_float(row.get("score"))
        prefixo = f"  {row['symbol']} [{setor}]"
        if score is not None:
            prefixo += f" score={score:.1f}"
        linhas.append(f"{prefixo}: {inds or 'sem métricas utilizáveis'}")
    return "\n".join(linhas)


def build_industry_medians_context(
    scored: pd.DataFrame | None,
    industries: list[str] | None,
) -> str:
    """Medianas por indústria — a régua contra a qual o desconto é julgado."""
    if scored is None or scored.empty or "industry" not in scored.columns:
        return "MEDIANAS POR INDÚSTRIA: indisponíveis."
    alvos = [i for i in (industries or []) if i]
    frame = scored if not alvos else scored[scored["industry"].isin(alvos)]
    if frame.empty:
        return "MEDIANAS POR INDÚSTRIA: sem empresas nas indústrias consultadas."
    linhas = ["MEDIANAS POR INDÚSTRIA (universo americano elegível):"]
    for industria, grupo in frame.groupby("industry", dropna=True):
        if len(grupo) < 3:
            continue
        partes = []
        for key, label, unit in _PEER_METRICS:
            if key not in grupo.columns:
                continue
            mediana = pd.to_numeric(grupo[key], errors="coerce").median()
            if pd.notna(mediana):
                partes.append(f"{label}={_fmt_metric(mediana, unit)}")
        if partes:
            linhas.append(f"  {industria} (n={len(grupo)}): " + " | ".join(partes))
    return "\n".join(linhas) if len(linhas) > 1 else "MEDIANAS POR INDÚSTRIA: amostra insuficiente."


def build_peer_context(
    ticker: str,
    portfolio_tickers: list[str] | tuple[str, ...],
    *,
    scored: pd.DataFrame | None = None,
    industry: str | None = None,
    max_peers: int = 8,
) -> str:
    """Hierarquia de pares: carteira primeiro, universo americano depois."""
    tk = str(ticker).strip().upper()
    candidatos, nivel = compute_industry_peers(scored, tk, max_peers=max(12, max_peers))
    na_carteira, do_universo = prioritize_peer_tickers(
        candidatos, portfolio_tickers, tk, max_peers=max_peers,
    )
    selecionados = na_carteira + do_universo
    return "\n".join([
        f"HIERARQUIA DE PARES DE {tk}: comparação primária por {nivel or 'indústria SEC'}.",
        "  PARES DA MESMA INDÚSTRIA JÁ NA CARTEIRA (prioridade): "
        + (", ".join(na_carteira) if na_carteira else "nenhum"),
        "  PARES COMPLEMENTARES DO UNIVERSO AMERICANO: "
        + (", ".join(do_universo) if do_universo else "nenhum com dados suficientes"),
        "  REGRA: comparar múltiplos entre indústrias diferentes não conclui caro/barato. "
        "Um software com P/L 30 e uma siderúrgica com P/L 6 não são comparáveis.",
        build_us_fundamentals_context(scored, [tk, *selecionados], max_n=max_peers + 1),
        build_industry_medians_context(scored, [industry] if industry else None),
    ])


# ─────────────────────────────────────────────────────────────────────────────
# Séries anuais SEC
# ─────────────────────────────────────────────────────────────────────────────

def build_financial_history_context(df_fin: pd.DataFrame | None) -> str:
    """Serializa até 10 exercícios SEC e calcula qualidade do lucro em Python.

    Diferença estrutural para a B3: aqui existe ``free_cash_flow`` publicado e
    ``capex`` separado, então FCL não precisa ser inferido do FCO — e o prompt
    pode cobrar a distinção sem margem para o modelo confundir os dois.
    """
    if df_fin is None or df_fin.empty:
        return ("HISTÓRICO FINANCEIRO (SEC): indisponível. Não infira tendência, "
                "FCL ou conversão de caixa sem dados.")
    frame = df_fin.copy()
    if "fiscal_year" in frame.columns:
        frame = frame.sort_values("fiscal_year").tail(10)
    else:
        frame = frame.tail(10)

    colunas = [
        c for c in (
            "revenue", "ebitda", "ebit", "operating_income", "net_income",
            "total_equity", "net_debt", "operating_cash_flow", "capex",
            "free_cash_flow", "dividends_paid",
        ) if c in frame.columns
    ]
    linhas = ["HISTÓRICO FINANCEIRO SEC/US GAAP (até 10 exercícios anuais, em USD):"]
    for idx, row in frame.iterrows():
        ano = str(row.get("fiscal_year") or idx)
        partes = [f"{col}={format_number(row.get(col))}" for col in colunas]
        receita = safe_float(row.get("revenue"))
        ebitda = safe_float(row.get("ebitda"))
        lucro = safe_float(row.get("net_income"))
        fco = safe_float(row.get("operating_cash_flow"))
        fcl = safe_float(row.get("free_cash_flow"))
        if receita:
            if ebitda is not None:
                partes.append(f"Margem_EBITDA={ebitda / receita * 100:.1f}%")
            if lucro is not None:
                partes.append(f"Margem_liquida={lucro / receita * 100:.1f}%")
            if fcl is not None:
                partes.append(f"Margem_FCL={fcl / receita * 100:.1f}%")
        if lucro and fco is not None:
            partes.append(f"FCO_Lucro={fco / lucro:.2f}x")
        linhas.append(f"  FY{ano}: " + " | ".join(partes))

    def _razao_recente(numerador: str, denominador: str) -> str:
        if numerador not in frame.columns or denominador not in frame.columns:
            return "N/D"
        for _, row in frame.iloc[::-1].iterrows():
            n, d = safe_float(row.get(numerador)), safe_float(row.get(denominador))
            if n is not None and d not in (None, 0.0):
                return f"{n / d:.2f}x"
        return "N/D"

    def _anos_positivos(coluna: str) -> str | None:
        if coluna not in frame.columns:
            return None
        valores = pd.to_numeric(frame[coluna], errors="coerce").dropna()
        return f"{int((valores > 0).sum())}/{len(valores)} anos" if len(valores) else None

    linhas.extend([
        "QUALIDADE DO RESULTADO — cálculos determinísticos:",
        f"  Conversão FCO/lucro mais recente: {_razao_recente('operating_cash_flow', 'net_income')}",
        f"  Conversão FCL/lucro mais recente: {_razao_recente('free_cash_flow', 'net_income')}",
        f"  FCO positivo: {_anos_positivos('operating_cash_flow') or 'N/D'} | "
        f"FCL positivo: {_anos_positivos('free_cash_flow') or 'N/D'}",
        "  FCO não é FCL: a diferença é capex. Ambos estão publicados acima; "
        "se um estiver N/D, declare a limitação em vez de usar o outro no lugar.",
    ])
    return "\n".join(linhas)


def build_advanced_context(advanced: dict | None) -> str:
    """Piotroski, Altman, Sloan e ROIC incremental — evidência calculada.

    É o que ocupa, no mercado americano, o lugar que o RAG de documentos CVM
    ocupa na B3: sinal verificável em código sobre a qualidade contábil, em
    vez de trecho de documento recuperado.
    """
    if not advanced:
        return ("LABORATÓRIO AVANÇADO: indisponível para esta empresa. Não conclua "
                "solidez contábil sem estes indicadores.")
    # Chaves conforme core.us_advanced.advanced_snapshot.
    rotulos = (
        ("f_score", "Piotroski F-Score (0–9)", "{:.0f}"),
        ("z_score", "Altman Z-Score", "{:.2f}"),
        ("sloan_accruals", "Accruals de Sloan", "{:.3f}"),
        ("incremental_roic", "ROIC incremental", "{:.1%}"),
    )
    partes = []
    for chave, rotulo, formato in rotulos:
        valor = safe_float(advanced.get(chave))
        if valor is not None:
            partes.append(f"  {rotulo}: {formato.format(valor)}")
    zona = advanced.get("z_zone")
    if zona:
        partes.append(f"  Zona de Altman: {zona}")
    # O F-Score só é comparável quando os nove sinais foram avaliáveis; declarar
    # a parcialidade evita que "4/9" seja lido como empresa fraca quando na
    # verdade cinco sinais não tinham dado.
    avaliaveis = safe_float(advanced.get("f_evaluable"))
    if avaliaveis is not None and avaliaveis < 9:
        partes.append(
            f"  Atenção: apenas {avaliaveis:.0f} dos 9 sinais de Piotroski eram "
            "avaliáveis — o F-Score está parcial e não compara com empresa completa."
        )
    if not partes:
        return "LABORATÓRIO AVANÇADO: sem indicadores utilizáveis."
    return "\n".join([
        "LABORATÓRIO AVANÇADO (calculado em código sobre as demonstrações SEC):",
        *partes,
        "  Leitura: F-Score alto indica melhora contábil ampla; Z-Score baixo, risco "
        "de insolvência; accruals altos, lucro pouco sustentado por caixa. São "
        "sinais, não veredictos — explique o mecanismo antes de concluir.",
    ])


def format_us_macro(macro: dict | None) -> str:
    """Regime macro americano — saída de ``core.us_macro.evaluate_macro``.

    A procedência abre o bloco de propósito. Sem ela, a LLM lê "Fed funds:
    4.25%" e escreve "com o Fed em 4,25%" no relatório institucional — uma
    afirmação sobre o mundo a partir de um literal de código. Com o rótulo de
    premissa, a mesma leitura vira "sob a premissa de Fed a 4,25%", que é o que
    o dado sustenta.
    """
    if not macro:
        return "CENÁRIO MACRO EUA: indisponível."
    entradas = macro.get("inputs") or {}
    observado = bool(macro.get("observado"))
    as_of = macro.get("as_of")

    if observado:
        procedencia = (
            "  PROCEDÊNCIA: séries oficiais (FRED) ingeridas no warehouse"
            + (f", data-base {as_of}." if as_of else ".")
            + " Pode afirmar estes valores como observados."
        )
    else:
        procedencia = (
            "  PROCEDÊNCIA: **PREMISSA DE SIMULAÇÃO**, não observação de mercado. "
            "Os valores abaixo são parâmetros de cenário definidos na interface, "
            "não leitura das séries oficiais. É PROIBIDO afirmá-los como fato "
            "('o Fed está em X%'); escreva sempre de forma condicional "
            "('sob a premissa de Fed a X%', 'num cenário de CPI a Y%')."
        )

    linhas = [
        "CENÁRIO MACRO ESTADOS UNIDOS:",
        procedencia,
        f"  Regime: {macro.get('regime', 'N/D')} "
        f"(pontuação {macro.get('score', 'N/D')}/100, tom {macro.get('tone', 'N/D')})",
    ]
    for chave, rotulo, sufixo in (
        ("fed_funds", "Fed funds", "%"),
        ("cpi_yoy", "CPI a/a", "%"),
        ("real_gdp_yoy", "PIB real a/a", "%"),
        ("unemployment", "Desemprego", "%"),
        ("yield_curve_10y_2y", "Curva 10a-2a", " p.p."),
        ("high_yield_spread", "Spread high yield", " p.p."),
    ):
        valor = safe_float(entradas.get(chave))
        if valor is not None:
            linhas.append(f"  {rotulo}: {valor:.2f}{sufixo}")
    impactos = macro.get("sector_impacts") or {}
    if impactos:
        ordenados = sorted(impactos.items(), key=lambda kv: -kv[1])
        linhas.append(
            "  Impulso por setor (-10 a +10): "
            + " | ".join(f"{setor}={valor:+.1f}" for setor, valor in ordenados)
        )
    linhas.append(
        "  Custo de oportunidade de referência é o Treasury; o benchmark de "
        "comparação é o S&P 500. Não use Selic nem Ibovespa."
    )
    return "\n".join(linhas)


# ─────────────────────────────────────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────────────────────────────────────

_PROMPT_COMPANY_PORTFOLIO = """\
Você é um analista sênior de ações americanas preparando uma nota de diligência para um gestor.
Esta chamada pertence EXCLUSIVAMENTE à aba Avaliação de Portfólio das Empresas Americanas. Produza
interpretação causal, não uma enumeração de indicadores. Todo fato ou número deve vir do contexto;
inferências devem ser marcadas como inferência. Se a evidência não sustentar uma causa, diga
"causa não confirmada nos dados". Responda em português do Brasil, com os valores em dólares.

EMPRESA: {ticker} — {name}
SETOR: {sector} | INDÚSTRIA: {industry}

=== DOSSIÊ DETERMINÍSTICO ===
{dossier}

=== HISTÓRICO, TENDÊNCIAS E QUALIDADE DO RESULTADO (SEC/US GAAP) ===
{financial_history}

=== EVIDÊNCIA CONTÁBIL CALCULADA ===
{advanced_context}

=== PEER ANALYSIS — ORDEM OBRIGATÓRIA ===
{peer_context}

=== MACRO ESTADOS UNIDOS ===
{macro}

=== PROCEDÊNCIA E GRAU DE CONFIANÇA DESTA EMPRESA ===
{provenance}

=== CONTEXTO SUPLEMENTAR DA CARTEIRA ===
{portfolio_context}

REGRAS ANALÍTICAS OBRIGATÓRIAS:
1. Compare valuation prioritariamente com pares da MESMA indústria. Pares da carteira têm
   prioridade quando são comparáveis; complete com o universo americano. Outras indústrias são
   apenas contexto de diversificação — múltiplo de software não julga múltiplo de banco.
2. Explique por que os múltiplos podem estar baixos/altos: conecte expectativa, tendência
   operacional, balanço, qualidade do lucro, governança, regulação e eventos. Não atribua opinião
   ao "mercado" sem evidência; nesse caso use "os múltiplos sugerem".
3. Em Qualidade dos Resultados, trate lucro, FCO, FCL, conversão caixa/lucro, recompras,
   remuneração em ações (SBC) e itens extraordinários. FCO não é FCL — a diferença é capex, e os
   dois estão publicados. Ausência de dado reduz confiança e nota, não autoriza invenção.
4. Classifique cada tendência relevante como acelerando, desacelerando, estável ou deteriorando e
   explique o mecanismo. Não conclua tendência com apenas um exercício.
5. Catalisadores e riscos devem ser específicos, ligados a uma métrica, evento, janela de evidência
   ou transmissão econômica. É proibido usar listas genéricas sem explicar o efeito.
6. Não escreva "vale comprar", "compre", "venda", "substitua por" ou "pode entrar na carteira".
   Descreva perfil de investidor, horizonte, tolerância a volatilidade e condições de adequação.
7. Os cenários Otimista/Base/Pessimista devem somar 100%, explicar probabilidade e mecanismo de
   impacto. Não invente preço-alvo. Use impacto qualitativo quando não houver modelo de preço.
8. Só cite fato corporativo (fusão, litígio, revisão contábil, mudança regulatória) se ele estiver
   no dossiê ou na evidência calculada. NÃO há base documental indexada nesta aba: se a informação
   não está no contexto, declare a lacuna em vez de recorrer à memória.
9. Sensibilidade macro deve usar os fatores americanos do contexto — Fed funds, CPI, PIB real,
   desemprego, curva de juros, spread de crédito e dólar. Não use Selic, IPCA nem Ibovespa.
   Respeite a PROCEDÊNCIA declarada no bloco macro: se ele estiver marcado como premissa,
   escreva de forma condicional e nunca afirme o valor como observação de mercado.
9b. O grau de confiança do score e a data-base estão declarados. Se a empresa estiver em grau
   de triagem, ou se faltarem trilhas críticas, NÃO conclua sobre valuation ou qualidade:
   descreva o que falta, rebaixe a nota de "confianca" e diga em qualidade_dados o que
   impediria a conclusão. Cobertura baixa não é sinônimo de empresa ruim.
10. A conclusão deve responder: cara/justa/barata; desconto justificável; pessimismo/otimismo
    implícito; risco-retorno; principal positivo; principal risco. Termine com resumo executivo de
    até cinco linhas.
11. Score qualitativo: notas 0–10, justificativa causal e evidência/lacuna para cada dimensão.
    Pesos: {weights_contract}. O código recalcula a média ponderada; não manipule a nota para
    recomendar ação.

Responda somente JSON válido, sem markdown, com exatamente esta estrutura principal:
{{
  "perspectiva": "forte|moderada|fraca",
  "confianca": <inteiro de 0 a 100 — NUNCA fração; 85 significa 85%, 0.85 é inválido>,
  "resumo": "síntese analítica de até cinco linhas",
  "relatorio": {{
    "empresa_hoje": "modelo econômico e fonte de valor",
    "analise_pares": "comparação por indústria e leitura dos descontos/prêmios",
    "valuation_interpretado": "múltiplos, causas prováveis, expectativas e justificativa",
    "tendencias": "receita, EBITDA, lucro, margens, ROE, ROIC, dívida e caixa",
    "qualidade_resultados": "lucro, FCO, FCL, conversão, recompras, SBC e extraordinários",
    "governanca_controlador": "governança e alocação de capital com evidência disponível",
    "eventos_relevantes": "fatos documentados no contexto e seu efeito potencial",
    "qualidade_dados": "lacunas que limitam a leitura"
  }},
  "riscos": [{{"risco": "", "mecanismo": "", "indicador_monitorado": ""}}],
  "catalisadores": [{{"catalisador": "", "mecanismo": "", "janela_ou_gatilho": ""}}],
  "sensibilidade_macro": ["fator americano -> mecanismo de transmissão"],
  "cenarios": [
    {{"cenario": "Otimista", "probabilidade_pct": 0, "impacto_esperado": "", "fundamentacao": ""}},
    {{"cenario": "Base", "probabilidade_pct": 0, "impacto_esperado": "", "fundamentacao": ""}},
    {{"cenario": "Pessimista", "probabilidade_pct": 0, "impacto_esperado": "", "fundamentacao": ""}}
  ],
  "score_qualitativo_detalhado": {{
    "modelo_negocio": {{"nota": 0, "justificativa": "", "evidencia_ou_lacuna": ""}},
    "vantagem_competitiva": {{"nota": 0, "justificativa": "", "evidencia_ou_lacuna": ""}},
    "governanca": {{"nota": 0, "justificativa": "", "evidencia_ou_lacuna": ""}},
    "eficiencia_operacional": {{"nota": 0, "justificativa": "", "evidencia_ou_lacuna": ""}},
    "saude_financeira": {{"nota": 0, "justificativa": "", "evidencia_ou_lacuna": ""}},
    "crescimento": {{"nota": 0, "justificativa": "", "evidencia_ou_lacuna": ""}},
    "geracao_caixa": {{"nota": 0, "justificativa": "", "evidencia_ou_lacuna": ""}},
    "rentabilidade": {{"nota": 0, "justificativa": "", "evidencia_ou_lacuna": ""}},
    "qualidade_resultados": {{"nota": 0, "justificativa": "", "evidencia_ou_lacuna": ""}},
    "valuation": {{"nota": 0, "justificativa": "", "evidencia_ou_lacuna": ""}}
  }},
  "adequacao_investidor": {{"perfil": "", "horizonte": "", "tolerancia_volatilidade": "", "condicoes": ""}},
  "conclusao": {{
    "faixa_valor": "cara|justa|barata|indeterminada",
    "desconto_justificavel": "",
    "percepcao_mercado": "pessimista|neutra|otimista|indeterminada",
    "risco_retorno": "",
    "principal_positivo": "",
    "principal_risco": "",
    "resumo_executivo": "até cinco linhas"
  }}
}}
"""


_PROMPT_PORTFOLIO = """\
Você é um gestor de ações americanas revisando uma carteira como conjunto. Use somente as análises
individuais e o macro abaixo. Explique causa e efeito, concentração, complementaridade, transmissão
de riscos e condições de adequação. Não dê ordens de compra, venda ou substituição. A comparação de
valuation de cada empresa já foi feita contra pares da mesma indústria; não compare múltiplos entre
indústrias diferentes. Responda em português do Brasil, com valores em dólares.

=== COMPOSIÇÃO E LEITURAS INDIVIDUAIS ===
{items_context}

=== MACRO ESTADOS UNIDOS ===
{macro}

=== CONCENTRAÇÃO POR SETOR E INDÚSTRIA ===
{concentration}

=== AVALIAÇÃO QUANTITATIVA DETERMINÍSTICA ===
{quant_context}

=== PROCEDÊNCIA DOS DADOS ===
{provenance}

=== GRAU DE CONFIANÇA POR EMPRESA ===
{confidence}

=== EXPOSIÇÃO CAMBIAL ===
{fx_context}

Considere explicitamente o que é próprio deste mercado: exposição cambial de quem investe em reais,
concentração em megacaps de tecnologia, sensibilidade à política do Fed e à curva de juros, e
diferença entre recompra e dividendo como forma de retorno ao acionista.

REGRAS DE HONESTIDADE (obrigatórias):
- Respeite a procedência do bloco macro. Premissa entra como cenário condicional, nunca como fato.
- Declare a data-base dos dados no resumo executivo. Uma leitura sobre demonstrações defasadas é
  válida, mas o leitor precisa saber que é isso que está lendo.
- Peso relevante em grau de triagem limita a conclusão do conjunto: diga qual fatia da carteira
  não sustenta leitura fundamentalista e ajuste a cobertura declarada para baixo.
- Trate a exposição cambial na adequação: ela é proteção e risco ao mesmo tempo.

Responda somente JSON válido com este schema. Preserve os campos legados porque a interface os consome:
{{
  "qualidade_carteira": "alta|media|baixa",
  "perspectiva_12m": "construtiva|equilibrada|cautelosa",
  "confianca_media": 0,
  "score_medio": 0,
  "cobertura": "alta|media|baixa",
  "resumo_executivo": "até cinco linhas, decisão central e principal risco",
  "relatorio_estrategico": "leitura causal do conjunto, sem recomendação simplista",
  "papel_dos_ativos": "como exposições se complementam ou concentram",
  "pontos_fortes": ["força específica e mecanismo"],
  "pontos_fracos": ["fragilidade específica e mecanismo"],
  "sintese_alocacao": "como o método quanti+quali altera exposições; não dê ordem de negociação",
  "diagnostico_causal": "choques -> transmissão -> impacto na carteira",
  "riscos_transmissao": [{{"risco": "", "ativos_expostos": [""], "mecanismo": "", "monitoramento": ""}}],
  "catalisadores_portfolio": [{{"catalisador": "", "ativos_expostos": [""], "mecanismo": ""}}],
  "adequacao_carteira": {{"perfil": "", "horizonte": "", "volatilidade": "", "condicoes": ""}},
  "conclusao_estrategica": "conclusão em até cinco linhas com risco-retorno e gatilhos de revisão"
}}
"""


# ── A-149: "sem receita" é um fato da empresa, não uma lacuna do dado ────────
#
# Uma biotech em fase clínica não tem receita porque não vende nada ainda; o
# dado não está faltando, o valor é zero e a SEC o publica. Medido em
# 27/08/2026: 209 empresas elegíveis nunca registraram receita em nenhum
# exercício, e a tela dizia a todas "cobertura insuficiente" -- que o leitor lê
# como "não sabemos", quando o correto é "sabemos, e não há receita". A
# distinção muda a conduta: lacuna se resolve buscando dado, ausência de
# receita se analisa por caixa, runway e pipeline.
#
# O piso de três exercícios existe porque uma empresa recém-listada com um
# único ano pode simplesmente não ter tido o filing lido ainda -- aí é lacuna
# mesmo. Depende do A-148: enquanto o parser aceitava a tag de rollup vazia,
# `revenue = 0` também aparecia em empresa com receita, e esta regra chamaria
# a Eaton de pré-receita se todos os anos dela fossem zero (não são).
_MIN_ANOS_PRE_RECEITA = 3


def e_pre_receita(df_fin: pd.DataFrame | None) -> bool:
    """True quando a empresa nunca registrou receita em nenhum exercício lido."""
    if df_fin is None or getattr(df_fin, "empty", True):
        return False
    if "revenue" not in df_fin.columns or "fiscal_year" not in df_fin.columns:
        return False
    anos = pd.to_numeric(df_fin["fiscal_year"], errors="coerce")
    receita = pd.to_numeric(df_fin["revenue"], errors="coerce")
    validos = anos.notna()
    if int(validos.sum()) < _MIN_ANOS_PRE_RECEITA:
        return False
    return bool((receita[validos].fillna(0.0) == 0).all())


def build_company_provenance(
    df_fin: pd.DataFrame | None,
    score_row: Any = None,
    status: dict | None = None,
) -> str:
    """Data-base e grau de confiança DESTA empresa, para o prompt individual."""
    linhas = ["PROCEDÊNCIA E CONFIANÇA:"]
    if df_fin is not None and not getattr(df_fin, "empty", True) \
            and "fiscal_year" in df_fin.columns:
        anos = pd.to_numeric(df_fin["fiscal_year"], errors="coerce").dropna()
        if not anos.empty:
            linhas.append(
                f"  Exercícios disponíveis: FY{int(anos.min())}–FY{int(anos.max())} "
                f"({len(anos)} anos). O mais recente é a base da leitura."
            )
    if status and status.get("last_update") is not None:
        linhas.append(f"  Última ingestão do warehouse: {status['last_update']}.")

    grau, rotulo, confianca = grau_de_confianca(score_row)
    parte = f"  Grau do score: {rotulo}"
    if confianca is not None:
        parte += f" (confiança {confianca:.0%})"
    linhas.append(parte + ".")
    pre_receita = e_pre_receita(df_fin)
    if pre_receita:
        linhas.append(
            "  EMPRESA PRÉ-RECEITA: não registrou receita em nenhum exercício "
            "lido. Isso é um fato apurado, não um dado ausente -- margem, "
            "múltiplo de receita e crescimento não existem aqui, e a nota baixa "
            "de cobertura reflete isso. Analise por caixa, queima, runway e "
            "estágio do pipeline; não conclua valuation por múltiplo."
        )
    elif grau != "decision_grade":
        motivo = motivo_do_grau(score_row)
        if motivo["tipo"] in ("balanco", "ambos"):
            # A instrução antiga dizia ao analista "cobertura baixa não é
            # empresa ruim" -- e a dizia justamente para quem tem patrimônio
            # negativo. Mandar rebaixar a convicção onde o dado é completo e
            # ruim é pedir que a reprovação saia como dúvida.
            legiveis = ", ".join(MARCA_LABEL.get(m, m) for m in motivo["marcas"])
            linhas.append(
                f"  ATENÇÃO: balanço estruturalmente quebrado ({legiveis}). "
                "O dado está completo -- isto é veredito sobre a empresa, e não "
                "lacuna. NÃO trate como incerteza: diga o que o balanço mostra, "
                "e trate múltiplo sobre base negativa como não significativo."
            )
        else:
            linhas.append(
                "  ATENÇÃO: cobertura insuficiente para conclusão fundamentalista "
                "firme. Descreva a lacuna, rebaixe a confiança e evite veredicto de "
                "valuation. Cobertura baixa não é empresa ruim."
            )
    faltando = None
    if score_row is not None:
        getter = score_row.get if hasattr(score_row, "get") else (
            lambda k, d=None: getattr(score_row, k, d))
        faltando = getter("critical_missing", None)
    if faltando is not None and len(faltando) > 0:
        rotulo_falta = ("Trilhas indefinidas por ausência de receita"
                        if pre_receita else "Trilhas sem cobertura mínima")
        linhas.append(f"  {rotulo_falta}: {', '.join(map(str, faltando))}.")
    return "\n".join(linhas)


def build_company_prompt(
    ticker: str,
    dossier: dict,
    df_fin: pd.DataFrame | None,
    advanced: dict | None,
    macro: dict | None,
    peer_context: str,
    portfolio_context: str,
    provenance: str = "",
) -> str:
    try:
        dossier_text = dossie_to_text(dossier)
    except (KeyError, TypeError):
        dossier_text = str(dossier)
    return _PROMPT_COMPANY_PORTFOLIO.format(
        ticker=ticker,
        name=dossier.get("name") or ticker,
        sector=dossier.get("sector") or "N/D",
        industry=dossier.get("industry") or "N/D",
        dossier=dossier_text,
        financial_history=build_financial_history_context(df_fin),
        advanced_context=build_advanced_context(advanced),
        peer_context=peer_context or "PARES: indisponíveis; não conclua prêmio/desconto setorial.",
        macro=format_us_macro(macro),
        provenance=provenance or build_company_provenance(df_fin),
        portfolio_context=portfolio_context or "Sem contexto suplementar da carteira.",
        weights_contract=weights_contract(),
    )


def generate_company_us_report(
    ticker: str,
    *,
    df_fin: pd.DataFrame | None = None,
    advanced: dict | None = None,
    macro: dict | None = None,
    scored: pd.DataFrame | None = None,
    portfolio_tickers: list[str] | tuple[str, ...] = (),
    portfolio_context: str = "",
    model: str | None = None,
    status: dict | None = None,
) -> tuple[dict, dict]:
    """Nota institucional de uma empresa americana. Devolve (relatório, dossiê)."""
    tk = str(ticker).strip().upper()
    dossier = build_dossie(tk)
    if dossier.get("erro"):
        return fallback_company(tk, f"dossiê indisponível: {dossier['erro']}"), dossier
    try:
        peer_context = build_peer_context(
            tk, portfolio_tickers, scored=scored, industry=dossier.get("industry"),
        )
    except Exception as exc:  # noqa: BLE001 - pares nunca derrubam o relatório
        logger.warning("Pares institucionais de %s indisponíveis: %s", tk, exc)
        peer_context = "PARES: indisponíveis; não conclua prêmio/desconto setorial."

    score_row = None
    if scored is not None and not scored.empty and "symbol" in scored.columns:
        linha = scored[scored["symbol"].astype(str).str.upper() == tk]
        if not linha.empty:
            score_row = linha.iloc[0]

    prompt = build_company_prompt(
        tk, dossier, df_fin, advanced, macro, peer_context, portfolio_context,
        provenance=build_company_provenance(df_fin, score_row, status),
    )
    try:
        raw = _call_llm(prompt, model=model or _report_model())
        parsed = _parse_json(raw, fallback_company(tk, "JSON inválido"))
        return sanitize_company_report(parsed, tk), dossier
    except Exception as exc:  # noqa: BLE001
        logger.warning("Relatório institucional de %s falhou: %s", tk, exc)
        return fallback_company(tk, str(exc)[:200]), dossier


# Grau de confiança do score, produzido por core.us_score. O relatório precisa
# dele: um valuation concluído sobre `screen_grade` é opinião com aparência de
# análise.
GRAU_LABEL = {
    "decision_grade": "decisão (cobertura alta)",
    "research_grade": "pesquisa (cobertura parcial)",
    "screen_grade": "triagem (cobertura baixa)",
}


def grau_de_confianca(row: Any) -> tuple[str, str, float | None]:
    """(status, rótulo legível, confiança 0–1) de uma linha do cross-section."""
    if row is None:
        return "screen_grade", GRAU_LABEL["screen_grade"], None
    getter = row.get if hasattr(row, "get") else (lambda k, d=None: getattr(row, k, d))
    status = str(getter("score_status", "") or "") or "screen_grade"
    return status, GRAU_LABEL.get(status, status), safe_float(getter("score_confidence", None))


#: Como cada marca de balanço quebrado se lê em português de gente.
MARCA_LABEL = {
    "patrimonio_liquido_negativo": "patrimônio líquido negativo",
    "ebitda_nao_positivo": "EBITDA não positivo",
    "capital_investido_negativo": "capital investido negativo",
}


def motivo_do_grau(row: Any) -> dict:
    """Por que o selo de decisão faltou: lacuna de dado ou veredito de balanço.

    As duas coisas chegavam à tela sob a mesma frase — "a leitura abaixo é
    limitada pelo que falta" — e para a maioria isso era falso. Medido em
    31/08/2026 sobre as 2.626 empresas ativas, 731 estavam em `research_grade`
    com TODAS as trilhas críticas cobertas e confiança >= 75: não faltava nada.
    O que havia era patrimônio líquido negativo, EBITDA não positivo ou capital
    investido negativo — um veredito SOBRE a empresa, e o oposto de uma lacuna.

    Dizer "não sei" onde a análise diz "sei, e é ruim" é o erro mais caro que
    esta tela pode cometer: ele transforma reprovação em dúvida, e dúvida o
    investidor resolve sozinho, para o lado que ele já queria.
    """
    if row is None:
        return {"tipo": "lacuna", "faltando": [], "marcas": [], "texto": ""}
    getter = row.get if hasattr(row, "get") else (
        lambda k, d=None: getattr(row, k, d))
    faltando = [str(x) for x in (getter("critical_missing", None) or [])]
    marcas = [str(x) for x in (getter("impairment_flags", None) or [])]
    legiveis = [MARCA_LABEL.get(m, m) for m in marcas]
    if marcas and faltando:
        tipo = "ambos"
        texto = (f"O balanço está quebrado ({', '.join(legiveis)}) E há trilhas "
                 f"sem cobertura mínima ({', '.join(faltando)}). O selo de "
                 "decisão cai pelas duas razões, e a primeira já basta.")
    elif marcas:
        tipo = "balanco"
        texto = (f"Os dados estão completos. O que trava o selo de decisão é o "
                 f"balanço: {', '.join(legiveis)}. Isto é veredito sobre a "
                 "empresa, não falta de informação.")
    elif faltando:
        tipo = "lacuna"
        texto = (f"Trilhas sem cobertura mínima: {', '.join(faltando)}. A "
                 "leitura abaixo é limitada pelo que falta — não é veredicto "
                 "sobre a empresa, é o que os dados disponíveis sustentam.")
    else:
        tipo = "suficiente"
        texto = ("Nenhuma trilha crítica ficou descoberta e o balanço não tem "
                 "marca estrutural; a confiança fica abaixo do selo de decisão "
                 "pela cobertura geral das métricas.")
    return {"tipo": tipo, "faltando": faltando, "marcas": marcas, "texto": texto}


def build_data_provenance_context(
    status: dict | None,
    df_por_ticker: dict[str, pd.DataFrame] | None = None,
) -> str:
    """Data-base e origem dos dados — o cabeçalho de qualquer nota profissional.

    Uma nota que não diz "demonstrações até FY2024, ingeridas em 03/08/2026"
    não permite ao leitor julgar se está lendo análise ou arqueologia.
    """
    status = status or {}
    modo = {
        "warehouse": "warehouse local completo",
        "snapshot": "vitrine publicada (snapshot do warehouse)",
        "none": "sem base disponível",
    }.get(str(status.get("mode") or "none"), str(status.get("mode")))
    linhas = ["PROCEDÊNCIA DOS DADOS:", f"  Origem: {modo}."]
    ultima = status.get("last_update")
    if ultima is not None:
        linhas.append(f"  Última ingestão: {ultima}.")
    else:
        linhas.append("  Última ingestão: não informada — trate a base como de "
                      "frescor desconhecido e diga isso na leitura.")
    if status.get("companies"):
        linhas.append(f"  Universo disponível: {status['companies']} empresas.")

    anos: list[int] = []
    for frame in (df_por_ticker or {}).values():
        if frame is None or getattr(frame, "empty", True):
            continue
        if "fiscal_year" in frame.columns:
            serie = pd.to_numeric(frame["fiscal_year"], errors="coerce").dropna()
            if not serie.empty:
                anos.append(int(serie.max()))
    if anos:
        linhas.append(
            f"  Último exercício fiscal disponível na carteira: FY{max(anos)}"
            + (f" (mais antigo entre as empresas: FY{min(anos)})" if min(anos) != max(anos) else "")
            + ". Demonstração anual publicada tem defasagem natural; não a "
              "confunda com posição de hoje."
        )
    return "\n".join(linhas)


def build_confidence_context(items: list[dict]) -> str:
    """Grau de confiança do score por empresa e o que falta em cada uma."""
    if not items:
        return "GRAU DE CONFIANÇA: carteira vazia."
    linhas = [
        "GRAU DE CONFIANÇA DO SCORE POR EMPRESA (calculado pela cobertura real "
        "das trilhas, não estimado):",
    ]
    peso_frag = 0.0
    for item in items:
        status = str(item.get("score_status") or "screen_grade")
        rotulo = GRAU_LABEL.get(status, status)
        confianca = safe_float(item.get("score_confidence"))
        faltando = item.get("critical_missing") or []
        peso = float(item.get("peso_pct") or 0.0)
        if status != "decision_grade":
            peso_frag += peso
        parte = f"  {item.get('ticker')}: {rotulo}"
        if confianca is not None:
            parte += f" (confiança {confianca:.0%})"
        if faltando:
            parte += f" — trilhas sem cobertura mínima: {', '.join(map(str, faltando))}"
        linhas.append(parte)
    linhas.append(
        f"  {peso_frag:.1f}% do peso da carteira está abaixo de grau de decisão."
    )
    linhas.append(
        "  REGRA: empresa em grau de triagem NÃO sustenta conclusão de valuation "
        "nem de qualidade. Para essas, escreva o que falta e rebaixe a confiança "
        "do parecer em vez de concluir com o que existe."
    )
    return "\n".join(linhas)


def build_fx_context(usd_brl: float | None = None) -> str:
    """Exposição cambial de quem investe em reais numa carteira em dólares.

    A carteira é cotada em USD, mas o patrimônio do usuário é medido em reais.
    Sem este bloco, o relatório discute retorno em dólar e o leitor lê como
    retorno dele — são coisas diferentes, e a diferença já foi de dois dígitos
    em anos recentes.
    """
    linhas = [
        "EXPOSIÇÃO CAMBIAL (investidor com patrimônio em reais):",
        "  Toda posição desta carteira é denominada em dólares. O retorno em "
        "reais é aproximadamente (1 + retorno em USD) × (1 + variação do "
        "USD/BRL) − 1 — o câmbio é um segundo ativo embutido, não um detalhe "
        "de conversão.",
    ]
    taxa = safe_float(usd_brl)
    if taxa is not None:
        linhas.append(f"  USD/BRL de referência na base: R$ {taxa:.2f}.")
    else:
        linhas.append("  USD/BRL não disponível na base — não estime a taxa.")
    linhas.append(
        "  Trate a exposição cambial na adequação da carteira: ela protege "
        "contra risco Brasil e, ao mesmo tempo, adiciona volatilidade em reais. "
        "Não a apresente só como proteção nem só como risco."
    )
    return "\n".join(linhas)


def build_quant_context(avaliacao: dict | None) -> str:
    """Avaliação quantitativa determinística — saída de ``evaluate_portfolio``.

    Ocupa aqui o papel que o dossiê ocupa na nota individual: números fechados
    em código, que a LLM interpreta mas não recalcula. Sem este bloco, ela
    estimaria concentração "de olho" a partir da lista de pesos.
    """
    if not avaliacao or not avaliacao.get("ok"):
        return "AVALIAÇÃO QUANTITATIVA: indisponível."
    linhas = [
        "AVALIAÇÃO QUANTITATIVA (calculada em código, não estime novamente):",
        f"  Pontuação consolidada: {avaliacao.get('adjusted_score')}/100 "
        f"(base {avaliacao.get('score')} + ajuste macro {avaliacao.get('macro_adjustment'):+})",
        f"  Classificação: {avaliacao.get('classification')}",
        f"  Diversificação: {avaliacao.get('diversification_score')}/100 | "
        f"ativos efetivos: {avaliacao.get('effective_assets')} | HHI: {avaliacao.get('hhi')}",
        f"  Maior peso setorial: {avaliacao.get('max_sector_weight')}% | "
        f"cobertura pontuada: {avaliacao.get('coverage_weight')}% do peso",
    ]
    trilhas = avaliacao.get("track_scores") or {}
    if trilhas:
        linhas.append("  Trilhas: " + " | ".join(
            f"{nome}={valor:.0f}" for nome, valor in trilhas.items()
        ))
    alertas = avaliacao.get("alerts") or []
    if alertas:
        linhas.append("  Alertas determinísticos: " + "; ".join(alertas))
    ausentes = avaliacao.get("missing") or []
    if ausentes:
        linhas.append(
            f"  Sem pontuação no universo: {', '.join(map(str, ausentes[:10]))} — "
            "trate como lacuna de cobertura, não como qualidade ruim."
        )
    return "\n".join(linhas)


def build_concentration_context(items: list[dict]) -> str:
    """Peso por setor e por indústria — insumo do diagnóstico de concentração."""
    if not items:
        return "CONCENTRAÇÃO: carteira vazia."

    def _agrega(chave: str) -> dict[str, float]:
        acumulado: dict[str, float] = {}
        for item in items:
            rotulo = str(item.get(chave) or "Não classificado")
            acumulado[rotulo] = acumulado.get(rotulo, 0.0) + float(item.get("peso_pct") or 0.0)
        return acumulado

    linhas = []
    for chave, rotulo in (("setor", "SETOR"), ("industria", "INDÚSTRIA")):
        agregado = _agrega(chave)
        if not agregado:
            continue
        ordenado = sorted(agregado.items(), key=lambda kv: -kv[1])
        linhas.append(
            f"  Por {rotulo.lower()}: "
            + " | ".join(f"{nome}={peso:.1f}%" for nome, peso in ordenado[:10])
        )
        maior = ordenado[0]
        linhas.append(f"    Maior exposição em {rotulo.lower()}: {maior[0]} com {maior[1]:.1f}%.")
    return "CONCENTRAÇÃO DA CARTEIRA:\n" + "\n".join(linhas) if linhas else "CONCENTRAÇÃO: indisponível."


def analyze_us_portfolio_report(
    items_analyzed: list[dict],
    macro: dict | None,
    *,
    model: str | None = None,
    avaliacao_quant: dict | None = None,
    status: dict | None = None,
    financials: dict[str, pd.DataFrame] | None = None,
    usd_brl: float | None = None,
) -> dict:
    """Síntese consolidada da carteira americana, no schema que a UI consome."""
    prompt = _PROMPT_PORTFOLIO.format(
        items_context="\n".join(
            company_summary_for_portfolio(item) for item in items_analyzed
        ) or "Carteira vazia.",
        macro=format_us_macro(macro),
        concentration=build_concentration_context(items_analyzed),
        quant_context=build_quant_context(avaliacao_quant),
        provenance=build_data_provenance_context(status, financials),
        confidence=build_confidence_context(items_analyzed),
        fx_context=build_fx_context(usd_brl),
    )
    try:
        raw = _call_llm(prompt, model=model or _report_model())
        parsed = _parse_json(raw, fallback_portfolio("JSON inválido"))
        return sanitize_portfolio_report(parsed, items_analyzed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Relatório consolidado americano falhou: %s", exc)
        return fallback_portfolio(str(exc)[:200])


__all__ = [
    "GRAU_LABEL",
    "QUALITATIVE_WEIGHTS",
    "analyze_us_portfolio_report",
    "build_advanced_context",
    "build_company_prompt",
    "build_company_provenance",
    "e_pre_receita",
    "build_concentration_context",
    "build_confidence_context",
    "build_data_provenance_context",
    "build_financial_history_context",
    "build_fx_context",
    "build_quant_context",
    "grau_de_confianca",
    "build_industry_medians_context",
    "build_peer_context",
    "build_us_fundamentals_context",
    "compute_industry_peers",
    "format_us_macro",
    "generate_company_us_report",
]
