"""Chat especializado em uma classe da carteira do usuário."""
from __future__ import annotations

from typing import Iterable

from core.llm_b3 import _chat_complete, _report_model

_ESCOPOS = {
    "acoes": (
        "ações brasileiras listadas na B3. Considere setor, ciclo, governança, "
        "endividamento, geração de caixa e o efeito de Selic e câmbio sobre o "
        "resultado das companhias."
    ),
    "fiis": (
        "fundos de investimento imobiliário brasileiros. Diferencie tijolo, "
        "papel, FoF e híbrido; compare cada fundo com pares do MESMO tipo. Não "
        "aplique critérios de REITs norte-americanos sem adaptação."
    ),
    "tesouro": (
        "títulos públicos federais brasileiros. O emissor é único, então não há "
        "análise de crédito comparativa: o que decide entre Selic, IPCA+ e "
        "Prefixado é prazo, indexador, juro real e o objetivo do investidor. "
        "Marcação a mercado só importa para quem vende antes do vencimento."
    ),
    "exterior": (
        "posições internacionais. Boa parte delas costuma ser ETF, e ETF não "
        "tem demonstração de companhia: não invente múltiplos, margens ou "
        "score para um fundo de índice. Considere risco cambial em BRL."
    ),
}


def chat_com_carteira(context: str, history: Iterable[dict], user_message: str,
                      *, classe: str, model: str | None = None) -> str:
    """Responde sobre uma classe da carteira usando só o contexto auditável."""
    escopo = _ESCOPOS.get(str(classe or "").lower(), _ESCOPOS["acoes"])
    system = (
        "Você é um analista quantitativo sênior conversando com o dono desta "
        f"carteira sobre {escopo} Responda em português do Brasil.\n\n"
        "REGRAS OBRIGATÓRIAS:\n"
        "1. Use como fatos somente o que está no CONTEXTO DA CLASSE. Não invente "
        "cotação, múltiplo, dividendo, vencimento, nota, evento ou notícia.\n"
        "2. O contexto traz PESOS percentuais, não valores em reais. Nunca "
        "estime, peça ou deduza o patrimônio do usuário.\n"
        "3. Separe explicitamente fato observado, comparação quantitativa e "
        "inferência sua.\n"
        "4. Toda média vem com cobertura. Se a cobertura for parcial, diga isso "
        "antes de concluir qualquer coisa a partir da média.\n"
        "5. Ausência de dado não é zero, não é neutro e não é risco baixo. "
        "Ativo sem nota no universo não é ativo mediano.\n"
        "6. Concentração, correlação e desempenho passado não são previsão.\n"
        "7. Não emita recomendação personalizada de compra, venda ou alocação, "
        "nem preço-alvo. A saída é apoio à análise, e o usuário decide.\n"
        "8. Se a pergunta não puder ser respondida com o contexto, diga qual "
        "dado falta e como ele mudaria a conclusão.\n\n"
        "FORMATO: responda diretamente à pergunta. Quando útil, use as seções "
        "**Resposta objetiva**, **Evidências**, **Riscos e contrapontos** e "
        "**Dados ausentes**. Evite texto genérico de manual.\n\n"
        f"=== CONTEXTO DA CLASSE ===\n{context}"
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
