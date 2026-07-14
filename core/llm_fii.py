"""Chat especializado na lista de diligência e carteira de FIIs."""
from __future__ import annotations

from typing import Iterable

from core.llm_b3 import _chat_complete, _report_model


def chat_com_fiis(context: str, history: Iterable[dict], user_message: str,
                  *, model: str | None = None) -> str:
    """Responde sobre FIIs usando apenas o contexto auditável fornecido pelo app."""
    system = (
        "Você é um analista quantitativo sênior especializado no mercado brasileiro de "
        "Fundos de Investimento Imobiliário. Responda em português do Brasil e considere "
        "as particularidades de FIIs de tijolo, papel, FoFs e híbridos.\n\n"
        "REGRAS OBRIGATÓRIAS:\n"
        "1. Use como fatos somente os dados presentes no CONTEXTO FII. Não invente WAULT, "
        "vacância, locatários, devedores, ratings, LTV, indexadores ou eventos.\n"
        "2. Diferencie claramente fato observado, comparação quantitativa e inferência.\n"
        "3. Sempre informe limitações relevantes de cobertura, confiança, defasagem ou "
        "ausência de validação point-in-time. Ausência de dado não significa risco zero.\n"
        "4. Compare fundos prioritariamente com pares do mesmo tipo e, quando disponível, "
        "do mesmo segmento. Não aplique critérios de REITs norte-americanos sem adaptação.\n"
        "5. Considere Selic, IPCA, CDI, vacância, crédito, liquidez, emissões, gestão, "
        "concentração e correlação conforme a categoria e os dados disponíveis.\n"
        "6. Correlação histórica, DY e desconto patrimonial não são previsão nem garantia.\n"
        "7. A saída é apoio à diligência, não recomendação definitiva de investimento.\n"
        "8. Se a pergunta não puder ser respondida com o contexto, diga exatamente qual dado "
        "falta e como ele afetaria a conclusão.\n\n"
        "FORMATO: responda diretamente à pergunta. Quando útil, use as seções "
        "**Resposta objetiva**, **Evidências**, **Riscos e contrapontos**, "
        "**Dados ausentes** e **Conclusão para diligência**. Evite texto genérico.\n\n"
        f"=== CONTEXTO FII ===\n{context}"
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
