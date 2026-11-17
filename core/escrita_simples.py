"""Regra única de estilo: texto curto, simples, sem jargão (espírito do ASD-STE100).

Pedido do usuário de 17/11/2026: as respostas do app estavam longas, complexas
e cheias de siglas. O protocolo está no ``CLAUDE.md`` (seção "Como responder ao
usuário"). Este módulo leva o mesmo protocolo às LLMs do app.

Ponto único: :func:`com_regra_escrita`, chamada por
``core.llm_b3._chat_complete`` -- o único portão de saída para as LLMs. Todo
chat, dossiê, relatório, parecer de seleção, Portfolio Fit e leitura de
relatório passa por lá, com ou sem ``REGRA_CONTEXTO_MERCADO`` no prompt. Não se
cola ``REGRA_ESCRITA_SIMPLES`` em prompt nenhum: duas cópias divergem.

Trava: a regra é só de ESTILO. Ela nunca altera o que a resposta afirma, recusa
ou veta, nem a obrigação de citar fonte, número e data (``REGRA_CONTEXTO_MERCADO``)
nem a varredura do armazém. Simplificar a linguagem não tira precisão.

Não medida com LLM real (17/11/2026): vale pelo texto e pelos testes.
"""
from __future__ import annotations

REGRA_ESCRITA_SIMPLES = (
    "ESCRITA SIMPLES: escreva para quem não é especialista. Dê o resultado "
    "primeiro, em 1 ou 2 frases, e o detalhe depois. Frases curtas (até uns 20 "
    "palavras), uma ideia por frase, voz ativa, palavras do dia a dia. Prefira "
    "listas curtas a parágrafos longos. Sigla ou termo técnico só com uma "
    "explicação curta na primeira vez (ex.: \"Selic, a taxa básica de juros\"). "
    "Simplificar não tira precisão: mantenha todo número, fonte e data que as "
    "outras regras exigem, e toda ressalva, lacuna declarada e condição de veto, "
    "com o mesmo peso. Esta regra é só de estilo: não muda o que a resposta "
    "afirma, recusa ou veta, não corta item pedido e não muda o formato. Em "
    "resposta JSON, vale só para os campos de texto livre: chaves, formato e "
    "valores fixos (veredito, classe, ticker, nome de métrica) ficam como "
    "estão. Nome de métrica que o app mostra em outras telas não muda: explique-o "
    "logo depois, em frase curta."
)

# Marcador curto para saber se a regra já está no prompt (idempotência).
_MARCA = "ESCRITA SIMPLES:"


def com_regra_escrita(messages: list[dict]) -> list[dict]:
    """Devolve cópia de ``messages`` com ``REGRA_ESCRITA_SIMPLES`` no system prompt.

    Sem system prompt, cria um no início (prompts de usuário único, como os dos
    relatórios em JSON). Se a regra já estiver lá, não repete. Não altera a
    lista recebida nem o texto das demais mensagens.
    """
    copia = [dict(m) for m in messages]
    if copia and copia[0].get("role") == "system":
        atual = copia[0].get("content") or ""
        if isinstance(atual, str) and _MARCA in atual:
            return copia
        copia[0]["content"] = (atual + "\n\n" if atual else "") + REGRA_ESCRITA_SIMPLES
    else:
        copia.insert(0, {"role": "system", "content": REGRA_ESCRITA_SIMPLES})
    return copia
