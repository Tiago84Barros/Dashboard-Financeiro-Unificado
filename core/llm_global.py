"""Chat especializado no Portfólio Global — as três carteiras como um patrimônio.

Reaproveita a cadeia de provedores de `core.llm_b3` (OpenRouter -> OpenAI ->
Gemini); aqui só muda o prompt de sistema.

Diferença deliberada em relação a `core.llm_b3.chat_com_portfolio`: aquele
prompt manda o modelo terminar com um bloco ```charts, porque a tela da B3
tem um renderizador para essa diretiva. O Portfólio Global não tem — emitir
a diretiva aqui imprimiria JSON cru na conversa. Por isso este módulo existe
em vez de reusar aquela função.

Coberto por tests/test_llm_global.py.
"""
from __future__ import annotations

from typing import Iterable

from core.llm_b3 import _chat_complete, _report_model

_HISTORICO_MAX = 10


def chat_com_portfolio_global(
    context: str,
    history: Iterable[dict],
    user_message: str,
    *,
    model: str | None = None,
) -> str:
    """Responde uma pergunta sobre o Portfólio Global a partir do contexto.

    `history` é a lista de {"role": "user"|"assistant", "content": str}; só as
    últimas `_HISTORICO_MAX` mensagens vão ao modelo.
    """
    system = (
        "Você é uma ANALISTA DE ALOCAÇÃO responsável por um único patrimônio "
        "que reúne três carteiras-modelo: ações da B3, fundos imobiliários e "
        "ações americanas. O CONTEXTO abaixo traz os números REAIS lidos dos "
        "snapshots persistidos: composição, pesos, alvo x real por classe, "
        "concentração, múltiplos agregados, qualidade por classe, cobertura da "
        "série mensal, risco, correlação, papel estratégico de cada ativo e as "
        "recomendações do motor de movimentação.\n\n"
        "REGRAS:\n"
        "1. Use SEMPRE os números do contexto. NUNCA invente peso, múltiplo, "
        "correlação, risco ou preço que não esteja lá.\n"
        "2. 'ausente' significa que o dado não existe — não trate como zero e "
        "não preencha por estimativa. Diga que falta e o que isso impede de "
        "concluir.\n"
        "3. Qualidade (score) de classes diferentes vem de metodologias e "
        "escalas diferentes: compare scores DENTRO da mesma classe, nunca "
        "entre classes.\n"
        "4. Estatísticas de risco e correlação valem só para os ativos com "
        "série de preço na janela comum. Se ativos ficaram de fora, considere "
        "isso ao responder.\n"
        "5. 'indeterminado' no motor é ausência de sinal, e é diferente de "
        "'manter', que é decisão com evidência. Não confunda os dois.\n"
        "6. Custo não calibrado significa que o motor se recusou a mexer por "
        "não conhecer o custo real — não é recomendação de comprar nem vender.\n"
        "7. Separe DADO OBJETIVO de OPINIÃO analítica, e aponte as limitações "
        "dos dados em vez de escondê-las.\n"
        "8. Não emita blocos de código com diretivas de gráfico: esta tela não "
        "os desenha.\n\n"
        "FORMATO (use só as seções aplicáveis, em markdown):\n"
        "**Resumo** · **Dados utilizados** · **Leitura por classe** · "
        "**Concentração e risco** · **Pontos de atenção** · "
        "**O que os dados não permitem afirmar** · **Conclusão prática**.\n\n"
        f"=== CONTEXTO DO PORTFÓLIO GLOBAL ===\n{context}"
    )

    messages: list[dict] = [{"role": "system", "content": system}]
    for message in list(history)[-_HISTORICO_MAX:]:
        role = str((message or {}).get("role") or "")
        content = str((message or {}).get("content") or "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})

    return _chat_complete(messages, temperature=0.25, json_mode=False,
                          primary_model=model or _report_model())
