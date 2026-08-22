"""
core/llm_b3.py
Módulo LLM para análise de portfólio B3.

Provider: OpenAI. Dois tiers de modelo:
  - _MODEL_DEFAULT (gpt-4o-mini): interações rápidas/baratas (chat).
  - _REPORT_MODEL_DEFAULT (gpt-4o): Relatórios por Empresa/Portfólio — síntese
    de 10 anos de fundamentos + peers + documentos CVM exige um modelo forte;
    o mini produzia teses genéricas. Override por env/secrets LLM_REPORT_MODEL.
Usa dados quantitativos do Supabase + contexto macro + conhecimento treinado
sobre setores/segmentos do mercado brasileiro para gerar análise institucional.
"""
from __future__ import annotations

import json
import logging
import os
import re

import pandas as pd
import streamlit as st

logger = logging.getLogger(__name__)

_MODEL_DEFAULT = "gpt-4o-mini"
_REPORT_MODEL_DEFAULT = "gpt-4o"


def _report_model() -> str:
    """Modelo dos relatórios (empresa/portfólio). Override: LLM_REPORT_MODEL."""
    try:
        from core.config import settings
        v = getattr(settings, "LLM_REPORT_MODEL", None)
        if v:
            return str(v)
    except Exception:
        pass
    return os.getenv("LLM_REPORT_MODEL", "").strip() or _REPORT_MODEL_DEFAULT
_GEMINI_MODEL_DEFAULT = "gemini-3.6-flash"
_GEMINI_BASE_URL_DEFAULT = "https://generativelanguage.googleapis.com/v1beta/openai/"
_TEMPERATURE   = 0.2
_TIMEOUT       = 90


# ─────────────────────────────────────────────────────────────────────────────
# Provedores LLM (resource-cached) — OpenAI primário, Gemini como fallback.
#
# Gemini é acessado pelo endpoint compatível com a API da OpenAI, então o MESMO
# SDK (openai) atende os dois — basta trocar api_key/base_url/modelo. Quando a
# OpenAI falha (ex.: cota 429), a cadeia tenta o Gemini automaticamente.
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_resource
def _get_openai_client():
    try:
        from openai import OpenAI

        from core.config import settings
        key = getattr(settings, "OPENAI_API_KEY", None) or os.environ.get("OPENAI_API_KEY", "")
        if not key:
            return None
        return OpenAI(api_key=key, timeout=_TIMEOUT)
    except Exception as exc:
        logger.warning("OpenAI client init falhou: %s", exc)
        return None


@st.cache_resource
def _get_gemini_client():
    try:
        from openai import OpenAI

        from core.config import settings
        key = (getattr(settings, "GEMINI_API_KEY", None)
               or os.environ.get("GEMINI_API_KEY", "")
               or os.environ.get("GOOGLE_API_KEY", ""))
        if not key:
            return None
        base = getattr(settings, "GEMINI_BASE_URL", None) or _GEMINI_BASE_URL_DEFAULT
        return OpenAI(api_key=key, base_url=base, timeout=_TIMEOUT)
    except Exception as exc:
        logger.warning("Gemini client init falhou: %s", exc)
        return None


def _gemini_model() -> str:
    try:
        from core.config import settings
        return getattr(settings, "GEMINI_MODEL", None) or _GEMINI_MODEL_DEFAULT
    except Exception:
        return _GEMINI_MODEL_DEFAULT


def _provider_chain(primary_model: str | None = None) -> list[tuple]:
    """
    Lista de (nome, client, modelo) na ordem de preferência: OpenAI → Gemini.
    Inclui apenas os provedores com chave configurada.
    """
    chain: list[tuple] = []
    oc = _get_openai_client()
    if oc is not None:
        chain.append(("openai", oc, primary_model or _MODEL_DEFAULT))
    gc = _get_gemini_client()
    if gc is not None:
        chain.append(("gemini", gc, _gemini_model()))
    return chain


def llm_disponivel() -> bool:
    return bool(_provider_chain())


def provedores_disponiveis() -> list[str]:
    """Nomes dos provedores LLM ativos (para exibição na UI)."""
    return [nome for nome, _c, _m in _provider_chain()]


def _chat_complete(
    messages: list[dict],
    temperature: float = _TEMPERATURE,
    json_mode: bool = False,
    primary_model: str | None = None,
) -> str:
    """
    Executa um chat completion com fallback entre provedores. Tenta OpenAI e,
    se falhar (cota/erro), tenta o Gemini. `json_mode` pede resposta JSON estrita
    (degrada para chamada simples se o provedor não suportar response_format).
    Levanta RuntimeError só se TODOS os provedores falharem.
    """
    chain = _provider_chain(primary_model)
    if not chain:
        raise RuntimeError(
            "Nenhum provedor LLM configurado — defina OPENAI_API_KEY e/ou "
            "GEMINI_API_KEY no .env / Streamlit Secrets."
        )
    erros: list[str] = []
    for nome, client, modelo in chain:
        try:
            if json_mode:
                try:
                    resp = client.chat.completions.create(
                        model=modelo, messages=messages, temperature=temperature,
                        response_format={"type": "json_object"},
                    )
                    return resp.choices[0].message.content
                except Exception as exc_json:
                    logger.warning("JSON mode falhou em %s (%s) — tentando sem response_format.",
                                   nome, exc_json)
            resp = client.chat.completions.create(
                model=modelo, messages=messages, temperature=temperature,
            )
            if nome != "openai":
                logger.info("LLM respondido pelo provedor de fallback: %s (%s)", nome, modelo)
            return resp.choices[0].message.content
        except Exception as exc:
            erros.append(f"{nome}({modelo}): {exc}")
            logger.warning("Provedor LLM %s falhou: %s", nome, exc)
            continue
    raise RuntimeError("Todos os provedores LLM falharam — " + " | ".join(erros))


def _call_llm(prompt: str, model: str = _MODEL_DEFAULT) -> str:
    # Os prompts de análise pedem JSON estrito; json_mode garante resposta
    # parseável e evita o fallback silencioso de parsing.
    return _chat_complete(
        [{"role": "user", "content": prompt}],
        temperature=_TEMPERATURE, json_mode=True, primary_model=model,
    )


def _parse_json(raw: str, fallback: dict) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip().rstrip("`").strip()
    try:
        return json.loads(raw)
    except Exception:
        # Tenta extrair o JSON do texto se houver ruído ao redor
        try:
            start = raw.index("{")
            end   = raw.rindex("}") + 1
            return json.loads(raw[start:end])
        except Exception:
            logger.warning("JSON parse falhou — usando fallback. Raw: %s", raw[:300])
            return fallback


# ─────────────────────────────────────────────────────────────────────────────
# Formatadores de contexto quantitativo
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_multiplos(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "  Sem múltiplos disponíveis."
    cols = [c for c in [
        "ROE", "ROIC", "ROA", "Margem_Liquida", "Margem_EBITDA",
        "DY", "P/VP", "P/L", "Endividamento_Total", "Liquidez_Corrente",
    ] if c in df.columns]
    if not cols:
        return "  Múltiplos indisponíveis."
    d = df.copy()
    # 10 anos (não 3): tendência de ciclo completo é o que diferencia uma tese
    # ancorada de um parágrafo genérico — o banco tem mediana de 16 anos.
    if "Data" in d.columns:
        d["Data"] = pd.to_datetime(d["Data"], errors="coerce")
        d = d.dropna(subset=["Data"]).sort_values("Data").tail(10)
    else:
        d = d.tail(10)
    lines = []
    for _, row in d.iterrows():
        ano = ""
        if "Data" in row:
            try:
                ano = f"({int(pd.Timestamp(row['Data']).year)})"
            except Exception:
                pass
        vals = [f"{c}={float(row[c]):.2f}" for c in cols if pd.notna(row.get(c))]
        if vals:
            lines.append(f"  {ano} {', '.join(vals)}")
    return "\n".join(lines) if lines else "  Insuficiente."


def _fmt_dre(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "  Sem DRE disponível."
    cols = [c for c in [
        "Receita_Liquida", "Lucro_Liquido", "EBITDA", "Divida_Liquida",
        "FCO", "Dividendos",
    ] if c in df.columns]
    if not cols:
        return "  DRE indisponível."
    d = df.copy()
    if "Data" in d.columns:
        d["Data"] = pd.to_datetime(d["Data"], errors="coerce")
        d = d.dropna(subset=["Data"]).sort_values("Data").tail(10)
    else:
        d = d.tail(10)
    lines = []
    for _, row in d.iterrows():
        ano = ""
        if "Data" in row:
            try:
                ano = f"({int(pd.Timestamp(row['Data']).year)})"
            except Exception:
                pass
        vals = []
        for c in cols:
            v = row.get(c)
            if pd.notna(v):
                v = float(v)
                if abs(v) >= 1_000_000_000:
                    vals.append(f"{c}={v / 1e9:.1f}B")
                elif abs(v) >= 1_000_000:
                    vals.append(f"{c}={v / 1e6:.0f}M")
                else:
                    vals.append(f"{c}={v:.2f}")
        if vals:
            lines.append(f"  {ano} {', '.join(vals)}")
    return "\n".join(lines) if lines else "  Insuficiente."


def _fmt_macro(macro_hist: dict) -> str:
    if not macro_hist:
        return "  Cenário macro não disponível."
    anos = sorted(macro_hist.keys(), reverse=True)[:2]
    lines = []
    for ano in sorted(anos):
        d = macro_hist[ano]
        parts = []
        if "selic" in d:
            parts.append(f"Selic={d['selic'] * 100:.2f}%a.a.")
        if "ipca" in d:
            parts.append(f"IPCA={d['ipca'] * 100:.2f}%")
        if "cambio" in d:
            parts.append(f"USD/BRL={d['cambio']:.2f}")
        if "pib" in d:
            parts.append(f"PIB={d['pib'] * 100:.1f}%")
        if parts:
            lines.append(f"  {ano}: {', '.join(parts)}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────────────────────────────────────

_PROMPT_EMPRESA = """\
Você é um analista de investimentos institucional especializado no mercado brasileiro de capitais (B3), \
com profundo conhecimento setorial e acesso ao repertório analítico de casas como XP, Bradesco BBI, \
Itaú BBA, BTG Pactual, Safra e JPMorgan Brasil. Integre dados quantitativos fornecidos com a perspectiva \
qualitativa de mercado sobre esta empresa, seu setor e o cenário macroeconômico atual.

EMPRESA: {ticker} — {nome}
SETOR: {setor} | SEGMENTO: {segmento}
PESO QUANTITATIVO NO PORTFÓLIO: {peso_pct:.1f}%
SCORE QUANTITATIVO (0-100): {score:.1f} | ALPHA vs SELIC (histórico): {alpha_selic:+.1f}%

=== MÚLTIPLOS FINANCEIROS (até 10 anos — leia a TENDÊNCIA, não só o último ano) ===
{multiplos}

=== DEMONSTRATIVO DE RESULTADOS (até 10 anos, com FCO e dividendos) ===
{dre}

=== PARES DO MESMO SEGMENTO (compare: cara ou barata vs quem?) ===
{peers_ctx}

=== CENÁRIO MACROECONÔMICO ===
{macro}

=== DOCUMENTOS CORPORATIVOS CVM/IPE (trechos relevantes, em ordem cronológica) ===
{rag_context}

=== CONTEXTO DO PORTFÓLIO ===
{portfolio_ctx}

Os trechos CVM/IPE vêm com a data no cabeçalho [AAAA-MM-DD | tipo | título] e estão em ordem \
cronológica. Quando houver documentos: (1) leia os fatos na sequência temporal, encadeando o que \
mudou ao longo do tempo (guidance, CAPEX, dividendos, emissões de dívida, aquisições, decisões \
judiciais); (2) identifique a TENDÊNCIA que essa evolução sugere; (3) ancore 'resumo', 'catalisadores', \
'riscos' e 'tese_final' em fatos datados reais (cite a data/assunto), nunca em suposições genéricas.

REGRAS DE ESPECIFICIDADE (obrigatórias): cite NÚMEROS das séries acima (ex.: "ROE caiu de 18% \
em 2021 para 8% em 2024", "P/L de 5,1x vs 9,3x do par X"); posicione a empresa VS OS PARES \
listados; se as séries de 10 anos mostrarem deterioração ou melhora, isso deve aparecer na tese. \
Frases que valem para qualquer empresa do setor ("desempenho sólido", "cenário favorável") são \
PROIBIDAS sem o número que as sustente.

Com base nos dados acima, nos documentos CVM quando disponíveis, e no seu conhecimento sobre esta \
empresa, setor e macroeconomia brasileira, gere uma análise estruturada em JSON com EXATAMENTE \
este schema (sem campos extras, sem texto fora do JSON):

{{
  "perspectiva": "forte" | "moderada" | "fraca",
  "acao_sugerida": "manter" | "aumentar" | "reduzir" | "revisar",
  "confianca": <inteiro 0-100>,
  "score_qualitativo": <inteiro 0-100>,
  "resumo": "<síntese 3-5 linhas: tese de investimento atual>",
  "alerta_principal": "<maior risco ou preocupação em 1 linha>",
  "proxima_acao": "<o que monitorar no próximo ciclo, 1 linha>",
  "riscos": ["<risco 1>", "<risco 2>", "<risco 3>"],
  "catalisadores": ["<catalisador 1>", "<catalisador 2>"],
  "sensibilidade_macro": ["<fator macro relevante 1>", "<fator macro relevante 2>"],
  "alocacao_sugerida_pct": <float 0.0–25.0, percentual sugerido no portfólio>,
  "justificativa_alocacao": "<1-2 linhas justificando o peso sugerido>",
  "tese_final": "<conclusão integrada 2-3 linhas>"
}}
"""

_PROMPT_PORTFOLIO = """\
Você é um gestor de portfólio institucional com visão macro-setorial do mercado brasileiro. \
Analise o portfólio abaixo e produza um relatório consolidado que integre a dimensão quantitativa, \
qualitativa e macroeconômica. Considere a perspectiva de casas de análise especializadas (XP, BTG, Itaú BBA) \
para embasar sua avaliação da carteira como um todo.

=== COMPOSIÇÃO DO PORTFÓLIO ===
{composicao}

=== SÍNTESE DAS ANÁLISES INDIVIDUAIS ===
{resumos}

=== CENÁRIO MACROECONÔMICO ===
{macro}

Gere o relatório em JSON com EXATAMENTE este schema:

{{
  "qualidade_carteira": "alta" | "media" | "baixa",
  "perspectiva_12m": "construtiva" | "equilibrada" | "cautelosa",
  "confianca_media": <inteiro 0-100>,
  "score_medio": <inteiro 0-100>,
  "cobertura": "alta" | "media" | "baixa",
  "resumo_executivo": "<síntese 4-6 linhas do portfólio como um todo>",
  "relatorio_estrategico": "<análise estratégica detalhada 6-10 linhas: posicionamento setorial, riscos sistêmicos, oportunidades>",
  "papel_dos_ativos": "<como os ativos se complementam em termos de risco/retorno e setores, 3-5 linhas>",
  "pontos_fortes": ["<força 1>", "<força 2>", "<força 3>"],
  "pontos_fracos": ["<fraqueza 1>", "<fraqueza 2>"],
  "sintese_alocacao": "<como a redistribuição ponderada melhora o portfólio, 2-3 linhas>",
  "conclusao_estrategica": "<conclusão 4-6 linhas com gatilhos de monitoramento e horizonte de revisão>"
}}
"""


# ─────────────────────────────────────────────────────────────────────────────
# API pública
# ─────────────────────────────────────────────────────────────────────────────

def analisar_empresa(
    ticker: str,
    nome: str,
    setor: str,
    segmento: str,
    peso_pct: float,
    score: float,
    alpha_selic: float,
    df_mult: pd.DataFrame,
    df_fin: pd.DataFrame,
    macro_hist: dict,
    portfolio_ctx: str,
    rag_context: str = "",
    peers_ctx: str = "",
    model: str | None = None,
) -> dict:
    """Chama LLM (modelo de relatório, default gpt-4o) e retorna a análise."""
    prompt = _PROMPT_EMPRESA.format(
        ticker=ticker, nome=nome, setor=setor or "N/D", segmento=segmento or "N/D",
        peso_pct=peso_pct, score=score, alpha_selic=alpha_selic,
        multiplos=_fmt_multiplos(df_mult),
        dre=_fmt_dre(df_fin),
        peers_ctx=peers_ctx or "  Sem pares mapeados para este segmento.",
        macro=_fmt_macro(macro_hist),
        rag_context=rag_context or "  Nenhum documento CVM disponível para este ativo.",
        portfolio_ctx=portfolio_ctx,
    )
    raw = _call_llm(prompt, model=model or _report_model())
    return _parse_json(raw, _fallback_empresa(ticker, peso_pct))


def analisar_portfolio(
    items_analisados: list[dict],
    macro_hist: dict,
    model: str | None = None,
) -> dict:
    """Chama LLM e retorna dict com análise consolidada do portfólio."""
    comp_lines, res_lines = [], []
    for it in items_analisados:
        tk  = it.get("ticker", "")
        w   = it.get("peso_pct", 0.0)
        sc  = it.get("score", 0.0)
        an  = it.get("analise", {})
        persp = an.get("perspectiva", "N/D")
        resumo = an.get("resumo", "")[:180]
        comp_lines.append(f"  {tk}: peso={w:.1f}% score={sc:.1f} perspectiva={persp}")
        if resumo:
            res_lines.append(f"  {tk}: {resumo}")

    prompt = _PROMPT_PORTFOLIO.format(
        composicao="\n".join(comp_lines) or "  (vazio)",
        resumos="\n".join(res_lines) or "  (sem resumos)",
        macro=_fmt_macro(macro_hist),
    )
    raw = _call_llm(prompt, model=model or _report_model())
    return _parse_json(raw, _fallback_portfolio())


# ─────────────────────────────────────────────────────────────────────────────
# Redistribuição de pesos quanti + quali
# ─────────────────────────────────────────────────────────────────────────────

# Redistribuição: piso e teto por ativo do modelo único.
PESO_MIN = 0.02
PESO_MAX = 0.25
# Teto relativo ao peso igualitário, usado quando PESO_MAX é inatingível.
_CAP_SOBRE_EQUAL_WEIGHT = 1.5
# Convicção mínima atribuída a quem não teve relatório gerado. Sem este piso a
# empresa com confiança 0 (fallback) saía com peso ~0 — uma exclusão silenciosa
# causada por falha de LLM, não por análise.
_CONVICCAO_MIN = 0.35


def redistribuir_pesos(
    items_analisados: list[dict],
    sinal_web: dict[str, dict] | None = None,
) -> dict[str, float]:
    """Modelo único de redistribuição quanti + quali + evidência web.

    Substitui a antiga escolha entre "Rígida" (excluía perspectiva fraca) e
    "Flexível" (mantinha tudo). O par era uma decisão sem critério: nenhum dos
    dois modos media *quanto* a leitura qualitativa era confiável, então a
    diferença entre eles era apenas quem o usuário decidia punir antes de ver a
    análise.

    O modelo único não exclui ninguém — quem julga entrada é a Criação de
    Portfólio — e faz o peso responder ao que de fato varia entre as empresas:

    * ``score`` quantitativo e ``score_qualitativo`` da LLM (60/40);
    * alpha vs Selic, com efeito deliberadamente pequeno (±15%);
    * perspectiva, como multiplicador (forte 1,20 · moderada 1,00 · fraca 0,65);
    * convicção declarada pela LLM, com piso para não converter falha técnica
      em exclusão;
    * corroboração entre banco e web (``sinal_web``): indicador que a segunda
      fonte contradiz reduz o peso em até 10%.

    O piso de 2% e o teto de 25% valem para todos.
    """
    sinal_web = sinal_web or {}
    scores: dict[str, float] = {}
    for it in items_analisados:
        tk   = str(it.get("ticker", "") or "")
        if not tk:
            continue
        an   = it.get("analise", {}) or {}
        persp = an.get("perspectiva", "moderada")

        score_q   = float(it.get("score", 50) or 0) / 100.0
        score_llm = float(an.get("score_qualitativo", 50) or 0) / 100.0
        conviccao = max(float(an.get("confianca", 50) or 0) / 100.0, _CONVICCAO_MIN)
        alpha_n   = min(max(float(it.get("alpha_selic", 0) or 0) / 100.0, -0.5), 1.0)

        mult = {"forte": 1.20, "moderada": 1.00, "fraca": 0.65}.get(persp, 1.00)
        fator_web = float((sinal_web.get(tk) or {}).get("fator", 1.0))
        combined = (
            (score_q * 0.60 + score_llm * 0.40)
            * (1 + alpha_n * 0.15)
            * mult
            * conviccao
            * fator_web
        )

        scores[tk] = max(combined, 1e-6)

    if not scores:
        return {}

    total = sum(scores.values())
    raw = {tk: v / total for tk, v in scores.items()}

    # Piso e teto EFETIVOS. Com poucos ativos o teto fixo é inatingível — em
    # três empresas o peso médio já é 33%, acima dos 25%, e o laço de clip +
    # renormalização convergia para pesos iguais: a análise inteira era
    # descartada em silêncio, e forte, moderada e fraca saíam idênticas.
    # Quando o teto não cabe, ele passa a ser relativo ao peso igualitário.
    # Para carteiras de seis ou mais nomes (o caso real) nada muda.
    n = len(raw)
    teto = max(PESO_MAX, _CAP_SOBRE_EQUAL_WEIGHT / n)
    piso = min(PESO_MIN, 1.0 / n)
    for _ in range(15):
        clipped = {tk: min(max(v, piso), teto) for tk, v in raw.items()}
        t = sum(clipped.values())
        raw = {tk: v / t for tk, v in clipped.items()}

    return raw


# ─────────────────────────────────────────────────────────────────────────────
# Fallbacks
# ─────────────────────────────────────────────────────────────────────────────

def _fallback_empresa(ticker: str, peso_pct: float) -> dict:
    return {
        "perspectiva": "moderada",
        "acao_sugerida": "revisar",
        "confianca": 50,
        "score_qualitativo": 50,
        "resumo": f"Análise de {ticker} indisponível (erro de parsing).",
        "alerta_principal": "Verificar dados manualmente.",
        "proxima_acao": "Acompanhar resultados trimestrais.",
        "riscos": ["Dados insuficientes para análise completa"],
        "catalisadores": [],
        "sensibilidade_macro": ["juros", "câmbio"],
        "alocacao_sugerida_pct": round(peso_pct, 1),
        "justificativa_alocacao": "Mantém peso quantitativo original por falta de dados qualitativos.",
        "tese_final": "Análise pendente — manter posição até revisão.",
    }


def chat_com_portfolio(
    context: str,
    history: list[dict],
    user_message: str,
    model: str = _MODEL_DEFAULT,
) -> str:
    """
    Responde perguntas sobre o portfólio usando o contexto pré-compilado da análise.
    history: lista de {"role": "user"|"assistant", "content": str}
    """
    system = (
        "Você é uma ANALISTA FUNDAMENTALISTA E ESTRATÉGICA DE PORTFÓLIO especializada no "
        "mercado brasileiro (B3). O CONTEXTO abaixo contém dados REAIS extraídos do banco: "
        "indicadores consolidados da carteira, fundamentos por empresa (DY, P/L, P/VP, ROE, "
        "ROIC, margens, endividamento, payout, crescimento, liquidez), pesos, segmentos, "
        "scores, justificativas de aprovação, comparações setoriais, universo B3, empresas "
        "selecionadas vs rejeitadas na Criação de Portfólio, macro e trechos de documentos CVM/IPE.\n\n"
        "REGRAS:\n"
        "1. Use SEMPRE os números reais do contexto — calcule médias, somas e comparações a "
        "partir deles. NUNCA invente indicadores ou valores não presentes.\n"
        "2. Compare empresas DENTRO e FORA da carteira quando a pergunta permitir, usando os "
        "blocos de universo/setor/fundamentos fornecidos.\n"
        "3. Explique a lógica da seleção quando perguntado (use motivos de aprovação e a "
        "Criação de Portfólio).\n"
        "4. Aponte limitações dos dados; só afirme que algo não existe se REALMENTE não "
        "constar no contexto. Cite a fonte interna ('carteira', 'banco', 'setor', 'documentos CVM').\n"
        "5. Sugira substituições/inclusões SOMENTE com evidência quantitativa explícita.\n"
        "6. Separe claramente DADO OBJETIVO de OPINIÃO analítica.\n"
        "7. DOCUMENTOS CVM/IPE: os trechos vêm com data no cabeçalho [AAAA-MM-DD | tipo | título] "
        "e em ordem cronológica. Ao analisar uma empresa (esteja ela DENTRO ou FORA da carteira), "
        "encadeie os fatos na linha do tempo, mostre como guidance/CAPEX/dividendos/dívida/aquisições "
        "evoluíram e conclua com a TENDÊNCIA e o que esperar — sempre citando as datas dos fatos. "
        "Se não houver documentos para o ticker citado, diga isso e use fundamentos + setor.\n\n"
        "FORMATO DA RESPOSTA (use só as seções aplicáveis, em markdown):\n"
        "**Resumo** · **Dados utilizados** · **Comparação dentro da carteira** · "
        "**Comparação com empresas fora** · **Pontos fortes** · **Pontos de atenção** · "
        "**Possíveis substituições/inclusões** · **Conclusão prática**.\n\n"
        "GRÁFICOS: quando um gráfico ajudar (comparação, ranking, composição, dividendos, "
        "correlação, desempenho de preço, receita×lucro), TERMINE a resposta com UM bloco de "
        "código ```charts contendo um array JSON. Cada item: {\"tipo\", \"metrica\", \"tickers\", "
        "\"titulo\"}. tipos válidos: comparison, ranking, dividend, sector_allocation, "
        "company_vs_peers, correlation, performance, financials. "
        "IMPORTANTE: 'performance' = desempenho histórico do PREÇO (base 100) e 'financials' = "
        "RECEITA LÍQUIDA × LUCRO LÍQUIDO anual (DRE). As séries de PREÇO e a DRE histórica ESTÃO "
        "disponíveis no banco para qualquer ticker — se o usuário pedir gráfico de preço/"
        "desempenho ou de receita/lucro, EMITA a diretiva correspondente em vez de dizer que não "
        "há dados. metrica ex.: ROE, P/L, P/VP, EV/EBIT, DY, Margem_Liquida, Endividamento_Total. "
        "tickers = lista de tickers reais. Não escreva código fora desse bloco; o app desenha o "
        "gráfico com dados reais. Omita o bloco se nenhum gráfico for útil.\n"
        "OBRIGATÓRIO: se o usuário PEDIR um gráfico, você DEVE terminar com o bloco ```charts "
        "correspondente — NUNCA responda apenas 'vou criar o gráfico' sem emitir o bloco. Para "
        "concorrentes/pares, use os tickers do bloco 'CONCORRENTES DE ...' do contexto (tipo "
        "'comparison' com o ativo + os concorrentes); nunca diga que não conhece os concorrentes "
        "se esse bloco estiver presente.\n\n"
        f"=== CONTEXTO DO PORTFÓLIO ===\n{context}"
    )

    messages = [{"role": "system", "content": system}]
    # Inclui histórico (máx 10 turnos para não estourar contexto)
    for msg in history[-10:]:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_message})

    # Chat livre (markdown), sem JSON mode. Usa a cadeia OpenAI → Gemini.
    return _chat_complete(messages, temperature=0.3, json_mode=False, primary_model=model)


# ─────────────────────────────────────────────────────────────────────────────
# Diretivas de gráfico emitidas pela LLM (parsing seguro — nunca executa código)
# ─────────────────────────────────────────────────────────────────────────────

_CHARTS_BLOCK_RE = re.compile(r"```charts\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def parse_chart_directives(resposta: str) -> tuple[str, list[dict]]:
    """
    Extrai um bloco ```charts <json>``` da resposta da LLM, retornando
    (texto_limpo_sem_o_bloco, lista_de_diretivas). Apenas faz json.loads — nunca
    executa código. Se não houver bloco válido, retorna (resposta, []).
    """
    if not resposta:
        return "", []
    m = _CHARTS_BLOCK_RE.search(resposta)
    if not m:
        return resposta, []

    raw = m.group(1).strip()
    if raw.lower().startswith("json"):
        raw = raw[4:].strip()
    directives: list[dict] = []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            parsed = [parsed]
        if isinstance(parsed, list):
            directives = [d for d in parsed if isinstance(d, dict)]
    except Exception as exc:
        logger.warning("parse_chart_directives: JSON inválido — ignorado: %s", exc)
        directives = []

    texto_limpo = (resposta[:m.start()] + resposta[m.end():]).strip()
    return texto_limpo, directives


def _fallback_portfolio() -> dict:
    return {
        "qualidade_carteira": "media",
        "perspectiva_12m": "equilibrada",
        "confianca_media": 50,
        "score_medio": 50,
        "cobertura": "media",
        "resumo_executivo": "Relatório indisponível.",
        "relatorio_estrategico": "",
        "papel_dos_ativos": "",
        "pontos_fortes": [],
        "pontos_fracos": [],
        "sintese_alocacao": "",
        "conclusao_estrategica": "",
    }
