"""Chat especializado em UM ativo — a empresa/fundo aberto na tela de análise.

Difere de `core.llm_b3.chat_com_portfolio` e de `core.llm_fii.chat_com_fiis`:
lá o objeto é a carteira; aqui é o ativo isolado que o usuário está analisando.
"""
from __future__ import annotations

from typing import Iterable

from core.llm_b3 import _chat_complete, _report_model

_MERCADOS = {
    "b3": (
        "ações listadas na B3 (Brasil)",
        "Compare com pares do mesmo segmento/subsetor, não com o mercado inteiro. "
        "Considere que múltiplos e demonstrações vêm de vintages com defasagem.",
    ),
    "us": (
        "ações listadas nas bolsas dos Estados Unidos",
        "O universo cobre apenas ações ordinárias — não há REIT, fundo, SPAC nem "
        "preferencial. Compare com pares da mesma indústria. Não existe base "
        "documental indexada aqui: não cite trechos de 10-K que não estejam no contexto.",
    ),
    "fii": (
        "Fundos de Investimento Imobiliário brasileiros",
        "Trate tijolo, papel, FoF e híbrido com critérios distintos e não aplique "
        "métricas de REIT norte-americano sem adaptação.",
    ),
}


def chat_com_ativo(context: str, history: Iterable[dict], user_message: str,
                   *, mercado: str, ticker: str, model: str | None = None) -> str:
    """Responde sobre um único ativo usando somente o contexto auditável do app."""
    escopo, nota = _MERCADOS.get(str(mercado or "").lower(), _MERCADOS["b3"])
    system = (
        f"Você é um analista sênior de investimentos cobrindo {escopo}. Nesta conversa "
        f"o objeto de análise é UM ativo específico: {ticker}. Responda em português do "
        "Brasil.\n\n"
        f"ESCOPO DO MERCADO: {nota}\n\n"
        "REGRAS OBRIGATÓRIAS:\n"
        f"1. Use como fatos somente os dados do CONTEXTO DO ATIVO. Não invente números, "
        "eventos, guidance, notícias, ratings ou projeções da empresa.\n"
        "2. Separe explicitamente fato observado, comparação com pares e inferência sua.\n"
        "3. Quando um dado necessário estiver ausente, diga qual é e como ele mudaria a "
        "conclusão — ausência de dado não é sinal de segurança.\n"
        "4. Informe defasagem, cobertura e confiança sempre que o contexto trouxer.\n"
        f"5. Se a pergunta for sobre outro ativo que não {ticker}, responda o que o "
        "contexto permitir sobre os pares e avise que a tela está focada em "
        f"{ticker}.\n"
        "6. Desempenho passado, múltiplos e score não são previsão nem garantia.\n"
        "7. A saída é apoio à análise, não recomendação de compra ou venda.\n\n"
        "FORMATO: responda direto à pergunta. Quando ajudar, use as seções "
        "**Resposta objetiva**, **Evidências**, **Riscos e contrapontos** e "
        "**Dados ausentes**. Evite texto genérico e evite repetir o contexto inteiro.\n\n"
        f"=== CONTEXTO DO ATIVO {ticker} ===\n{context}"
    )
    messages = [{"role": "system", "content": system}]
    for message in list(history)[-10:]:
        role = str(message.get("role") or "")
        content = str(message.get("content") or "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})
    return _chat_complete(messages, temperature=.25, json_mode=False,
                          primary_model=model or _report_model())
