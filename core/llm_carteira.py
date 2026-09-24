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
    "geral": (
        "a carteira inteira, somando ações, FIIs, Tesouro e exterior. O foco é "
        "a alocação entre classes, a concentração por ativo e por setor, a "
        "renda recebida e o retorno mercado/custo. O detalhe fundamentalista "
        "de cada classe está nas outras sub-abas e NÃO está neste contexto: "
        "não invente múltiplo, nota ou indicador de ativo."
    ),
}


def escopo_da_classe(classe: str) -> str:
    """O parágrafo de escopo da classe — fonte única para chat e dossiê."""
    return _ESCOPOS.get(str(classe or "").lower(), _ESCOPOS["acoes"])


def regras_da_analise(*, geral: bool = False) -> str:
    """As regras que valem em TODA a aba Análise — chat e dossiê.

    A regra 7 já foi o oposto do que é hoje: proibia recomendação personalizada
    de compra, venda, alocação e preço-alvo. O dono da carteira pediu a
    recomendação de volta, e ela voltou presa: tem que citar o que no contexto
    a sustenta e o que falta de dado. Recomendação sem lastro declarado é pior
    que recomendação nenhuma, porque parece igual.

    Uma função só, lida pelos dois chamadores, porque guarda duplicada diverge
    — e divergir aqui significa o chat permitir o que o dossiê proíbe na mesma
    aba, sem que nada quebre.

    ``geral`` troca só o recorte da regra 2: na Visão Geral o contexto é a
    carteira inteira, e "cobre apenas esta classe" seria falso.
    """
    recorte = (
        "ele cobre a carteira como carregada nesta tela, nada além dela."
        if geral else
        "ele cobre apenas esta classe — nunca fale do patrimônio total."
    )
    contexto = "CONTEXTO DA CARTEIRA" if geral else "CONTEXTO DA CLASSE"
    return (
        "REGRAS OBRIGATÓRIAS:\n"
        f"1. Use como fatos somente o que está no {contexto}. Não invente "
        "cotação, múltiplo, dividendo, vencimento, nota, evento ou notícia.\n"
        "2. Trabalhe com os pesos percentuais. Valores em reais só existem se o "
        "contexto os trouxer; quando não trouxer, não estime, não peça e não "
        f"deduza o patrimônio do usuário. Mesmo quando trouxer, {recorte}\n"
        "3. Separe explicitamente fato observado, comparação quantitativa e "
        "inferência sua.\n"
        "4. Toda média vem com cobertura. Se a cobertura for parcial, diga isso "
        "antes de concluir qualquer coisa a partir da média.\n"
        "5. Ausência de dado não é zero, não é neutro e não é risco baixo. "
        "Ativo sem nota no universo não é ativo mediano.\n"
        "6. Concentração, correlação e desempenho passado não são previsão.\n"
        "7. Recomendação de compra, venda, peso ou substituição é permitida e "
        "esperada, sob três condições, todas obrigatórias: cite o que no "
        "contexto a sustenta; diga o que falta de dado e como isso mudaria a "
        "conclusão; e deixe claro que a decisão é do usuário. Recomendação sem "
        "o lastro citado é proibida. Preço-alvo só se o contexto trouxer a base "
        "para calculá-lo — caso contrário, diga que não há base.\n"
        "8. Se a pergunta não puder ser respondida com o contexto, diga qual "
        "dado falta e como ele mudaria a conclusão."
    )


def chat_com_carteira(context: str, history: Iterable[dict], user_message: str,
                      *, classe: str, model: str | None = None) -> str:
    """Responde sobre uma classe da carteira usando só o contexto auditável."""
    geral = str(classe or "").lower() == "geral"
    regras = regras_da_analise(geral=True) if geral else regras_da_analise()
    titulo = "CONTEXTO DA CARTEIRA" if geral else "CONTEXTO DA CLASSE"
    system = (
        "Você é um analista quantitativo sênior conversando com o dono desta "
        f"carteira sobre {escopo_da_classe(classe)} Responda em português do "
        "Brasil.\n\n"
        f"{regras}\n\n"
        "FORMATO: responda diretamente à pergunta. Quando útil, use as seções "
        "**Resposta objetiva**, **Evidências**, **Riscos e contrapontos** e "
        "**Dados ausentes**. Evite texto genérico de manual.\n\n"
        f"=== {titulo} ===\n{context}"
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
