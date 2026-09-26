"""
core/llm_estrategia.py
Entrevista guiada que monta a política de investimentos.

Uma pergunta por vez. A cada resposta a LLM devolve, em JSON, o que a
resposta disse (campos da política, com o trecho que sustenta cada um) e a
próxima pergunta, escolhida à luz de tudo o que já foi respondido.

A LLM PROPÕE; este módulo decide. Tudo o que ela devolve passa por
``core.estrategia.politica``: campo desconhecido, valor fora das opções ou
sem trecho de evidência é descartado, e o encerramento só é aceito quando os
campos mínimos estão de fato preenchidos. Sem LLM, ou com resposta
ilegível, a entrevista segue por um roteiro determinístico — a próxima
pergunta vira a do primeiro campo que falta.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from core.estrategia import politica as pol
from core.llm_b3 import _chat_complete, llm_disponivel, provedores_disponiveis

logger = logging.getLogger(__name__)

MAX_TURNOS = 25
MAX_HISTORICO_PROMPT = 30
TEMPERATURA = 0.2

__all__ = ["Etapa", "MAX_TURNOS", "abertura", "llm_disponivel",
           "provedores_disponiveis", "proxima_etapa", "pergunta_do_roteiro"]


@dataclass(frozen=True)
class Etapa:
    politica: dict
    aplicados: tuple[str, ...] = ()
    rejeitados: dict = field(default_factory=dict)
    pergunta: str | None = None
    proposta: dict | None = None
    encerrar: bool = False
    origem: str = "llm"  # llm | roteiro
    aviso: str | None = None


# -- roteiro determinístico ----------------------------------------------------

def pergunta_do_roteiro(politica: dict) -> str | None:
    """Pergunta do primeiro campo que falta: mínimos antes, depois os demais."""
    prog = pol.progresso(politica)
    for chave in prog.faltantes + prog.complementares_faltantes:
        return pol.POR_CHAVE[chave].pergunta
    return None


def abertura(politica: dict) -> str:
    """Primeira mensagem da entrevista, sem chamar a LLM."""
    pergunta = pergunta_do_roteiro(politica) or (
        "Sua política já tem tudo o que é preciso. Quer ajustar algum ponto?")
    if pol.progresso(politica).obrigatorios_ok:
        return ("Vamos continuar de onde paramos. Em qualquer momento você "
                f"pode revisar as respostas no formulário.\n\n{pergunta}")
    return ("Vou fazer algumas perguntas, uma de cada vez, para entender o que "
            "você quer construir com seu patrimônio. Não existe resposta "
            f"certa, e tudo pode ser revisado depois.\n\n{pergunta}")


# -- prompt --------------------------------------------------------------------

def _descricao_campos() -> str:
    linhas = []
    for c in pol.CAMPOS:
        marca = "OBRIGATÓRIO" if c.obrigatorio else "complementar"
        if c.tipo in ("escolha", "multi"):
            tipo = (("uma de" if c.tipo == "escolha" else "lista de")
                    + " [" + ", ".join(k for k, _ in c.opcoes) + "]")
        elif c.tipo == "alocacao":
            tipo = ("objeto {renda_fixa, acoes_br, fiis, exterior} em %, "
                    "somando 100")
        elif c.tipo == "limites":
            tipo = "objeto {classe: % máximo}, só as classes citadas"
        elif c.tipo == "numero":
            tipo = f"número ({c.unidade})" if c.unidade else "número"
        elif c.tipo == "bool":
            tipo = "true/false"
        else:
            tipo = "texto curto"
        linhas.append(f"- {c.chave} ({marca}): {c.rotulo}; {tipo}")
    return "\n".join(linhas)


_SISTEMA = """Você conduz uma entrevista curta para montar a POLÍTICA DE \
INVESTIMENTOS de um investidor pessoa física brasileiro. A pergunta central é: \
o que esta pessoa pretende construir com o patrimônio?

REGRAS
1. Faça UMA pergunta por vez, curta, em português, em tom de conversa. Não \
liste várias perguntas.
2. Escolha a próxima pergunta pelo que já foi respondido: priorize os campos \
OBRIGATÓRIOS que faltam, depois os complementares que fazem sentido para esta \
pessoa (quem quer renda: valor mensal; quem aporta: valor do aporte; horizonte \
curto: liquidez). Não pergunte o que já está respondido nem o que não se aplica.
3. Em "updates" coloque SOMENTE o que o usuário disse ou confirmou \
explicitamente na última resposta. Nunca suponha, nunca complete lacunas, \
nunca preencha por coerência. Cada campo em "updates" precisa de um trecho da \
resposta em "evidence" com a mesma chave.
4. Traduza a resposta para as opções do campo. Se a resposta for ambígua, NÃO \
grave: faça uma pergunta de esclarecimento.
5. Você pode sugerir uma divisão por classe coerente com as respostas, em \
"proposal", explicando-a na pergunta. Ela só vai para "updates" \
(asset_class_targets) depois que o usuário confirmar.
6. NÃO recomende ativos, tickers, fundos, corretoras nem produtos específicos. \
Isso é definição de estratégia, não recomendação.
7. Use a carteira atual e as metas cadastradas só como contexto para \
perguntar melhor; elas NÃO são respostas do usuário.
8. "finished" = true só quando todos os OBRIGATÓRIOS estiverem preenchidos e \
os complementares relevantes cobertos, ou quando o usuário pedir para parar.

CAMPOS
{campos}

Responda APENAS com JSON:
{{"updates": {{"campo": valor}}, "evidence": {{"campo": "trecho dito"}}, \
"proposal": {{"renda_fixa": 0, "acoes_br": 0, "fiis": 0, "exterior": 0}} ou null, \
"next_question": "texto", "finished": false}}"""


def _mensagens(politica: dict, historico: list, resposta: str,
               contexto: str) -> list[dict]:
    prog = pol.progresso(politica)
    estado = [pol.texto_da_politica(politica),
              "\nObrigatórios que faltam: "
              + (", ".join(prog.faltantes) or "nenhum"),
              "Complementares que faltam: "
              + (", ".join(prog.complementares_faltantes) or "nenhum")]
    if contexto:
        estado.append("\n=== CONTEXTO (não são respostas do usuário) ===\n"
                      + contexto)
    msgs = [{"role": "system",
             "content": _SISTEMA.format(campos=_descricao_campos())},
            {"role": "system", "content": "\n".join(estado)}]
    for m in (historico or [])[-MAX_HISTORICO_PROMPT:]:
        if m.get("role") in ("user", "assistant") and m.get("content"):
            msgs.append({"role": m["role"], "content": str(m["content"])})
    msgs.append({"role": "user", "content": resposta})
    return msgs


def _json(bruto: str) -> dict | None:
    texto = (bruto or "").strip()
    if texto.startswith("```"):
        texto = texto.strip("`")
        texto = texto[texto.find("{"):]
    inicio, fim = texto.find("{"), texto.rfind("}")
    if inicio < 0 or fim <= inicio:
        return None
    try:
        dados = json.loads(texto[inicio:fim + 1])
    except ValueError:
        return None
    return dados if isinstance(dados, dict) else None


# -- etapa ---------------------------------------------------------------------

def _turnos(historico: list) -> int:
    return sum(1 for m in historico or [] if m.get("role") == "user")


def proxima_etapa(politica: dict, historico: list, resposta: str, *,
                  contexto: str = "", chat=None) -> Etapa:
    """Processa uma resposta do usuário e devolve a etapa seguinte.

    ``historico`` são as mensagens ANTERIORES a ``resposta``. ``chat`` é a
    função de completion (injeção para teste); por padrão, a cadeia de
    provedores do app.
    """
    chat = chat or _chat_complete
    resposta = (resposta or "").strip()
    if not resposta:
        return Etapa(politica=politica, pergunta=pergunta_do_roteiro(politica),
                     origem="roteiro")

    try:
        bruto = chat(_mensagens(politica, historico, resposta, contexto),
                     temperature=TEMPERATURA, json_mode=True)
        dados = _json(bruto)
    except Exception as exc:  # noqa: BLE001
        logger.warning("entrevista de estratégia sem LLM: %s", exc)
        dados = None

    if dados is None:
        return Etapa(
            politica=politica, pergunta=pergunta_do_roteiro(politica),
            origem="roteiro",
            aviso=("A IA não respondeu de forma legível; nada foi gravado "
                   "desta resposta. Você pode repetir ou usar o formulário."))

    updates = dados.get("updates") if isinstance(dados.get("updates"), dict) else {}
    evidencias = (dados.get("evidence")
                  if isinstance(dados.get("evidence"), dict) else {})
    # Sem trecho que sustente, o valor é suposição da LLM, não resposta.
    sem_prova = {k: "sem trecho da resposta que o sustente"
                 for k in updates if not str(evidencias.get(k) or "").strip()}
    aceitos = {k: v for k, v in updates.items() if k not in sem_prova}
    nova, rejeitados = pol.aplicar(politica, aceitos, fonte="entrevista",
                                   evidencias=evidencias)
    rejeitados = {**sem_prova, **rejeitados}
    aplicados = tuple(k for k in aceitos if k not in rejeitados)

    proposta = None
    if isinstance(dados.get("proposal"), dict):
        normal, erro = pol.normalizar("asset_class_targets", dados["proposal"])
        proposta = None if erro else normal

    prog = pol.progresso(nova)
    pergunta = str(dados.get("next_question") or "").strip() or None
    encerrar = bool(dados.get("finished")) and prog.minimos_completos
    if _turnos(historico) + 1 >= MAX_TURNOS and prog.minimos_completos:
        encerrar = True
    if encerrar:
        pergunta = None
    elif bool(dados.get("finished")) or pergunta is None:
        # A LLM quis parar com mínimo faltando, ou não perguntou nada: o
        # roteiro assume. Um "terminei" com lacuna não vira política vazia.
        pergunta = pergunta_do_roteiro(nova)
    if rejeitados:
        logger.info("entrevista: campos descartados %s", rejeitados)
    return Etapa(politica=nova, aplicados=aplicados, rejeitados=rejeitados,
                 pergunta=pergunta, proposta=proposta, encerrar=encerrar)
