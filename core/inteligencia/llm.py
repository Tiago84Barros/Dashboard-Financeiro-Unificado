"""A LLM explica. Ela não calcula, não estima e não muda score.

Como a proibição é feita valer
------------------------------
Instruir no prompt não basta -- instrução no prompt é pedido, não garantia. O
que faz valer é a verificação depois:

1. :func:`contexto` monta o texto a partir **exclusivamente** do
   :class:`~core.inteligencia.painel.Painel`. A LLM não recebe nenhum número que
   o backend não tenha publicado.
2. :func:`validar` passa a resposta por
   :func:`core.llm_grounding.check_grounding` contra esse mesmo contexto. Número
   que não estiver lá -- nem for derivável dele -- reprova a resposta.
3. Score alterado é, por construção, um número que não está no contexto. A regra
   "a LLM não pode alterar scores" não precisa de detector próprio: ela cai no
   mesmo portão, e é por isso que o portão vale mais que a instrução.
4. :data:`PROIBICOES` recusa promessa de retorno mesmo quando a aritmética
   fecha. "Vai subir 12%" pode citar um número publicado e ainda assim ser a
   frase que o requisito proíbe.

Declaração obrigatória é derivada, não confiada
-----------------------------------------------
O requisito lista seis situações em que a LLM "deve declarar" a limitação.
Deixar isso a cargo do modelo é o mesmo erro do aviso que envelhece invertido:
no dia em que ele esquecer, a tela publica uma análise confiante sobre uma
amostra de três eventos. :func:`declaracoes_obrigatorias` deriva a lista do
estado do painel, injeta no prompt **e** anexa ao texto final o que a resposta
tiver omitido.

Sem LLM a tela não fica muda
----------------------------
:func:`explicacao_deterministica` monta a mesma explicação a partir do painel,
sem chamar provedor nenhum. É o que aparece quando não há LLM configurada,
quando o provedor cai, e quando a resposta reprova na ancoragem.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from core import llm_grounding
from core.inteligencia import qualificacao as qz
from core.inteligencia.painel import Painel
from core.memoria_mercado import amostra as _amostra

logger = logging.getLogger(__name__)

LLM_VERSAO = "1.0.0"

#: Ancoragem mínima para publicar a resposta do modelo.
RAZAO_MINIMA = 1.0

#: As nove perguntas que a explicação tem de responder.
PERGUNTAS: tuple[str, ...] = (
    "O que aconteceu?",
    "Por que isso é relevante?",
    "Quais ativos foram afetados?",
    "Quais fundamentos podem mudar?",
    "Como eventos semelhantes se comportaram?",
    "Como a conjuntura atual difere desses eventos?",
    "Como a carteira está exposta?",
    "Por que a prioridade de aporte mudou ou permaneceu?",
    "O que deve ser monitorado a partir de agora?",
)

# ── As seis declarações obrigatórias ─────────────────────────────────────────
D_SEM_DADOS = "sem_dados"
D_AMOSTRA_PEQUENA = "amostra_pequena"
D_FONTES_DIVERGEM = "fontes_divergem"
D_NAO_CONFIRMADO = "nao_confirmado"
D_SEM_IMPACTO = "sem_impacto"
D_DESATUALIZADO = "desatualizado"

TEXTO_DECLARACAO: dict[str, str] = {
    D_SEM_DADOS: "Não há dados suficientes para sustentar esta análise: parte "
                 "dos componentes não foi medida.",
    D_AMOSTRA_PEQUENA: "A amostra histórica é pequena e não sustenta "
                       "inferência estatística.",
    D_FONTES_DIVERGEM: "As fontes divergem entre si sobre este evento.",
    D_NAO_CONFIRMADO: "O evento ainda não foi confirmado por fonte oficial.",
    D_SEM_IMPACTO: "O impacto não pode ser estimado com os dados disponíveis.",
    D_DESATUALIZADO: "Os dados usados nesta análise estão desatualizados ou "
                     "indisponíveis.",
}

#: Cobertura de bloco abaixo da qual a análise se declara insuficiente.
COBERTURA_INSUFICIENTE = 0.50

#: Tamanho de amostra abaixo do qual a inferência não se sustenta.
#: Derivado do motor, nao redeclarado. A memoria de mercado recusa
#: publicar faixa abaixo de N_MINIMO_EXPERIMENTAL; um limiar proprio
#: aqui deixaria a LLM chamar de suficiente uma amostra que o motor
#: ja considerou pequena demais para virar numero.
AMOSTRA_MINIMA = _amostra.N_MINIMO_EXPERIMENTAL

#: Frases que prometem retorno. Reprovam mesmo com a aritmética correta.
PROIBICOES: tuple[tuple[str, str], ...] = (
    (r"garant\w*\s+(de\s+)?(retorno|lucro|ganho|rentabilidade|valoriza)",
     "promessa de retorno"),
    (r"\b(com\s+certeza|certamente|sem\s+d[úu]vida)\b.{0,40}"
     r"\b(subir|cair|valorizar|desvalorizar|render)\w*",
     "certeza sobre movimento de preço"),
    (r"\b(vai|ir[áa]|deve)\s+(subir|cair|valorizar|desvalorizar|render)\b",
     "previsão apresentada como fato"),
    # "venda" sozinha e substantivo -- aparece no proprio aviso "nao e
    # recomendacao de compra ou venda". So o imperativo e ordem de operacao.
    (r"\b(compre|venda|vender|comprar)\s+(agora|j[aá]|hoje|tudo|"
     r"a posi[cç][aã]o|as a[cç][oõ]es)\b", "ordem de operação"),
    (r"\b(lucro|ganho)\s+(garantido|certo|assegurado)\b", "promessa de retorno"),
    (r"\bretorno\s+garantido\b", "promessa de retorno"),
)


@dataclass(frozen=True)
class Validacao:
    """O veredito sobre uma resposta do modelo."""

    aprovada: bool
    razao_ancorada: float
    numeros_inventados: tuple[str, ...] = ()
    frases_proibidas: tuple[str, ...] = ()
    declaracoes_faltando: tuple[str, ...] = ()
    motivo: str = ""

    def descrever(self) -> tuple[str, ...]:
        saida = [f"ancoragem: {self.razao_ancorada:.0%}"]
        if self.numeros_inventados:
            saida.append("números sem lastro no painel: "
                         + ", ".join(self.numeros_inventados))
        if self.frases_proibidas:
            saida.append("linguagem recusada: " + ", ".join(self.frases_proibidas))
        if self.declaracoes_faltando:
            saida.append("declarações anexadas pelo backend: "
                         + ", ".join(self.declaracoes_faltando))
        return tuple(saida)


@dataclass(frozen=True)
class Explicacao:
    """O que a tela mostra na área de texto."""

    texto: str
    origem: str                      # "llm" ou "backend"
    validacao: Validacao | None = None
    declaracoes: tuple[str, ...] = ()
    contexto: str = ""
    perguntas: tuple[str, ...] = field(default_factory=lambda: PERGUNTAS)

    @property
    def gerada_por_llm(self) -> bool:
        return self.origem == "llm"


# ── O contexto: tudo que a LLM pode citar, e nada além ───────────────────────
def contexto(pn: Painel, *, simbolo: str | None = None) -> str:
    """Serializa o painel para o prompt.

    O mesmo texto vira o contexto da verificação de ancoragem. Se este texto e o
    prompt divergissem, uma citação correta seria reprovada -- e o resto do
    módulo perderia o sentido.
    """
    linhas: list[str] = [f"PAINEL gerado em "
                         f"{pn.gerado_em.strftime('%d/%m/%Y %H:%M UTC')}"]

    ultima = pn.ultima_atualizacao
    linhas.append(f"Última atualização das fontes: "
                  f"{ultima.strftime('%d/%m/%Y %H:%M UTC') if ultima else 'nunca'}")

    for f in pn.frescor:
        linhas.append(f"FRESCOR · {f.descrever(pn.gerado_em)}")
    for p in pn.provedores:
        linhas.append(f"PROVEDOR · {p.descrever()}")

    for bloco in pn.blocos:
        linhas.append(f"\n## {bloco.titulo} (cobertura {bloco.cobertura:.0%})")
        for v in bloco.valores:
            linhas.append(f"- {v.descrever()}")
        for lim in bloco.limitacoes:
            linhas.append(f"- LIMITAÇÃO: {lim}")

    alvo = (simbolo or "").strip().upper()
    for e in pn.empresas:
        if alvo and e.simbolo.upper() != alvo:
            continue
        linhas.append(f"\n## {e.bloco.titulo}")
        linhas.append(f"- Situação: {e.aparencia['rotulo']}")
        for v in e.bloco.valores:
            linhas.append(f"- {v.descrever()}")
        for m in e.o_que_mudou:
            linhas.append(f"- MUDANÇA: {m}")
        for ev_ in e.evidencias:
            linhas.append(f"- EVIDÊNCIA: {ev_}")
        for inv in e.invalidariam:
            linhas.append(f"- INVALIDARIA: {inv}")
        for lim in e.bloco.limitacoes:
            linhas.append(f"- LIMITAÇÃO: {lim}")

    if pn.noticias:
        linhas.append("\n## Notícias")
        for n in pn.noticias[:12]:
            marca = "confirmada" if n.confirmado else "NÃO confirmada"
            linhas.append(f"- [{marca}] {n.titulo} — {n.carimbo} "
                          f"({n.n_fontes} fonte(s) independentes)")
            for v in n.valores():
                linhas.append(f"  · {v.descrever()}")

    for lim in pn.limitacoes:
        linhas.append(f"- LIMITAÇÃO DO PAINEL: {lim}")

    return "\n".join(linhas)


# ── As declarações obrigatórias, derivadas do painel ─────────────────────────
def declaracoes_obrigatorias(pn: Painel, *,
                             simbolo: str | None = None) -> tuple[str, ...]:
    """Quais das seis declarações este painel torna obrigatórias."""
    exigidas: list[str] = []

    if pn.desatualizados or pn.provedores_fora:
        exigidas.append(D_DESATUALIZADO)

    blocos = list(pn.blocos)
    alvo = pn.empresa(simbolo) if simbolo else None
    if alvo is not None:
        blocos.append(alvo.bloco)
    if any(b.valores and b.cobertura < COBERTURA_INSUFICIENTE for b in blocos):
        exigidas.append(D_SEM_DADOS)

    memoria = pn.memoria
    if memoria is not None:
        amostra = memoria.valor_de("Tamanho da amostra")
        if amostra is None or not amostra.medido:
            exigidas.append(D_AMOSTRA_PEQUENA)
        elif float(amostra.valor) < AMOSTRA_MINIMA:
            exigidas.append(D_AMOSTRA_PEQUENA)
        impacto = memoria.valor_de("Impacto atual estimado")
        if impacto is not None and not impacto.medido:
            exigidas.append(D_SEM_IMPACTO)

    if pn.noticias:
        if any(n.estado_verificacao == "contestada" for n in pn.noticias):
            exigidas.append(D_FONTES_DIVERGEM)
        if not any(n.confirmado for n in pn.noticias):
            exigidas.append(D_NAO_CONFIRMADO)

    vistos: list[str] = []
    for d in exigidas:
        if d not in vistos:
            vistos.append(d)
    return tuple(vistos)


# ── Prompt ───────────────────────────────────────────────────────────────────
def montar_prompt(pn: Painel, *, simbolo: str | None = None,
                  pergunta: str | None = None) -> str:
    ctx = contexto(pn, simbolo=simbolo)
    exigidas = declaracoes_obrigatorias(pn, simbolo=simbolo)
    perguntas = "\n".join(f"{i}. {p}" for i, p in enumerate(PERGUNTAS, 1))
    declara = ("\n".join(f"- {TEXTO_DECLARACAO[d]}" for d in exigidas)
               or "- (nenhuma declaração obrigatória neste painel)")
    return f"""Você explica um painel financeiro para uma pessoa não especialista.

REGRAS INEGOCIÁVEIS
- Use SOMENTE os números que aparecem no PAINEL abaixo. Não calcule números
  novos, não arredonde de forma diferente e não estime nada.
- Não altere nenhum score. Os scores do painel são a fonte.
- Distinga sempre fato, hipótese e estimativa, do jeito que o painel já marca.
- Não prometa retorno, não diga que um ativo "vai subir" ou "vai cair", e não
  emita ordem de compra ou venda.
- Quando o painel não tiver o dado, diga que não tem. Não preencha lacuna.

DECLARAÇÕES QUE ESTA RESPOSTA É OBRIGADA A CONTER
{declara}

RESPONDA, EM PORTUGUÊS SIMPLES, ÀS NOVE PERGUNTAS
{perguntas}

PAINEL
{ctx}
"""


# ── Validação ────────────────────────────────────────────────────────────────
def _frases_proibidas(texto: str) -> tuple[str, ...]:
    baixo = texto.lower()
    achadas: list[str] = []
    for padrao, rotulo in PROIBICOES:
        if re.search(padrao, baixo) and rotulo not in achadas:
            achadas.append(rotulo)
    return tuple(achadas)


def _declaracoes_faltando(texto: str, exigidas: tuple[str, ...]) -> tuple[str, ...]:
    """Uma declaração conta como presente se sua ideia central aparece."""
    baixo = texto.lower()
    marcas = {
        D_SEM_DADOS: ("dados insuficientes", "não há dados suficientes",
                      "sem dados suficientes", "cobertura"),
        D_AMOSTRA_PEQUENA: ("amostra pequena", "amostra é pequena",
                            "poucos eventos", "amostra reduzida"),
        D_FONTES_DIVERGEM: ("fontes divergem", "divergência entre as fontes",
                            "fontes divergentes"),
        D_NAO_CONFIRMADO: ("não confirmado", "ainda não foi confirmado",
                           "não confirmada"),
        D_SEM_IMPACTO: ("impacto não pode ser estimado", "não é possível estimar",
                        "sem estimativa de impacto"),
        D_DESATUALIZADO: ("desatualizado", "desatualizados", "indisponível",
                          "indisponíveis"),
    }
    return tuple(d for d in exigidas
                 if not any(m in baixo for m in marcas.get(d, ())))


def validar(resposta: str, pn: Painel, *, simbolo: str | None = None,
            ctx: str | None = None) -> Validacao:
    """Reprova o que a LLM não podia ter escrito."""
    texto_ctx = ctx if ctx is not None else contexto(pn, simbolo=simbolo)
    rel = llm_grounding.check_grounding(resposta, texto_ctx)
    inventados = tuple(c.raw for c in rel.ungrounded)
    proibidas = _frases_proibidas(resposta)
    faltando = _declaracoes_faltando(
        resposta, declaracoes_obrigatorias(pn, simbolo=simbolo))

    motivos: list[str] = []
    if inventados:
        motivos.append("a resposta cita números que o backend não publicou")
    if proibidas:
        motivos.append("a resposta contém linguagem de garantia ou de ordem")

    return Validacao(
        aprovada=not inventados and not proibidas,
        razao_ancorada=rel.ratio, numeros_inventados=inventados,
        frases_proibidas=proibidas, declaracoes_faltando=faltando,
        motivo="; ".join(motivos))


# ── Explicação determinística: a tela nunca fica muda ───────────────────────
def explicacao_deterministica(pn: Painel, *,
                              simbolo: str | None = None) -> Explicacao:
    """Responde às nove perguntas usando só o painel, sem chamar provedor."""
    linhas: list[str] = []
    alvo = pn.empresa(simbolo) if simbolo else None

    def responder(pergunta: str, corpo: str) -> None:
        linhas.append(f"**{pergunta}**\n{corpo}\n")

    if pn.noticias:
        n = pn.noticias[0]
        marca = "Confirmado" if n.confirmado else "Relatado e ainda não confirmado"
        responder(PERGUNTAS[0], f"{marca}: {n.titulo} ({n.carimbo}).")
    else:
        responder(PERGUNTAS[0],
                  "Nenhum evento foi coletado para o período. Isso pode ser "
                  "ausência de notícia ou falha de coleta — veja o estado dos "
                  "provedores.")

    crise = pn.crise
    nivel = crise.valor_de("Nível de crise") if crise else None
    responder(PERGUNTAS[1],
              f"Nível de crise avaliado: {nivel.texto}." if nivel and nivel.medido
              else "O nível de crise não foi avaliado nesta sessão, então a "
                   "relevância não pôde ser dimensionada.")

    afetados = sorted({t for n in pn.noticias for t in n.tickers})
    responder(PERGUNTAS[2], ", ".join(afetados) if afetados else
              "Nenhum ativo da carteira foi mapeado a um evento.")

    responder(PERGUNTAS[3],
              " ".join(alvo.o_que_mudou) if alvo else
              "Nenhum fundamento específico foi apontado como afetado.")

    memoria = pn.memoria
    if memoria is not None and memoria.valor_de("Tamanho da amostra") is not None \
            and memoria.valor_de("Tamanho da amostra").medido:
        amostra = memoria.valor_de("Tamanho da amostra")
        mediana = memoria.valor_de("Reação mediana histórica")
        corpo = f"Foram encontrados {amostra.texto} eventos comparáveis."
        if mediana is not None and mediana.medido:
            corpo += f" A reação mediana observada foi de {mediana.texto}%."
        responder(PERGUNTAS[4], corpo)
    else:
        responder(PERGUNTAS[4],
                  "Não há amostra histórica comparável para este evento.")

    similar = memoria.valor_de("Similaridade com o cenário atual") if memoria else None
    responder(PERGUNTAS[5],
              f"Similaridade medida com o cenário atual: {similar.texto}."
              if similar is not None and similar.medido else
              "A semelhança com os eventos passados não pôde ser medida, então "
              "as diferenças de conjuntura não estão quantificadas.")

    anti = pn.antifragilidade
    indice = anti.valor_de("Índice de antifragilidade") if anti else None
    if indice is not None and indice.medido:
        corpo = f"Índice de antifragilidade em {indice.texto}."
        criticos = [v for v in anti.valores
                    if v.medido and isinstance(v.valor, (int, float))
                    and v.unidade == "/1" and float(v.valor) < 0.20]
        if criticos:
            corpo += (" Componentes em nível crítico: "
                      + ", ".join(v.rotulo for v in criticos) + ".")
        responder(PERGUNTAS[6], corpo)
    else:
        responder(PERGUNTAS[6],
                  "A resistência da carteira a choques não pôde ser calculada. "
                  "Isso não significa que ela seja resistente.")

    if alvo is not None:
        atual = alvo.bloco.valor_de("Prioridade atual de aporte")
        responder(PERGUNTAS[7],
                  f"Prioridade atual de aporte: {atual.texto}. "
                  + " ".join(alvo.o_que_mudou))
    else:
        responder(PERGUNTAS[7],
                  "Nenhum ativo foi reavaliado nesta sessão.")

    monitorar = list(alvo.invalidariam) if alvo else []
    monitorar += [f.rotulo for f in pn.desatualizados]
    responder(PERGUNTAS[8],
              " ".join(f"- {m}" for m in monitorar) if monitorar else
              "Nada específico ficou pendente de monitoramento.")

    exigidas = declaracoes_obrigatorias(pn, simbolo=simbolo)
    texto = "\n".join(linhas)
    if exigidas:
        texto += "\n**Limitações desta análise**\n" + "\n".join(
            f"- {TEXTO_DECLARACAO[d]}" for d in exigidas)

    return Explicacao(texto=texto, origem="backend", declaracoes=exigidas,
                      contexto=contexto(pn, simbolo=simbolo))


def _anexar_declaracoes(texto: str, faltando: tuple[str, ...]) -> str:
    if not faltando:
        return texto
    return texto + "\n\n**Limitações que esta análise precisa registrar**\n" + \
        "\n".join(f"- {TEXTO_DECLARACAO[d]}" for d in faltando)


def explicar(pn: Painel, *, simbolo: str | None = None,
             chamar=None) -> Explicacao:
    """Pede a explicação ao modelo e só publica o que passar na validação.

    Args:
        chamar: função ``(prompt) -> str``. Injetável para teste; em produção
            cai em :func:`core.llm_b3._call_llm`.

    Returns:
        A explicação do modelo quando ela passa; a determinística em qualquer
        outro caso -- sem LLM configurada, provedor fora do ar, resposta vazia,
        número inventado ou promessa de retorno.
    """
    ctx = contexto(pn, simbolo=simbolo)
    exigidas = declaracoes_obrigatorias(pn, simbolo=simbolo)

    if chamar is None:
        try:
            from core import llm_b3
            if not llm_b3.llm_disponivel():
                return explicacao_deterministica(pn, simbolo=simbolo)
            chamar = lambda p: llm_b3._chat_complete(  # noqa: E731
                [{"role": "user", "content": p}], json_mode=False)
        except Exception:
            logger.exception("LLM indisponível; usando explicação do backend")
            return explicacao_deterministica(pn, simbolo=simbolo)

    try:
        bruto = chamar(montar_prompt(pn, simbolo=simbolo))
    except Exception:
        logger.exception("chamada à LLM falhou; usando explicação do backend")
        return explicacao_deterministica(pn, simbolo=simbolo)

    if not (bruto or "").strip():
        return explicacao_deterministica(pn, simbolo=simbolo)

    veredito = validar(bruto, pn, simbolo=simbolo, ctx=ctx)
    if not veredito.aprovada:
        logger.warning("resposta da LLM reprovada (%s); usando o backend",
                       veredito.motivo)
        base = explicacao_deterministica(pn, simbolo=simbolo)
        return Explicacao(texto=base.texto, origem="backend",
                          validacao=veredito, declaracoes=exigidas, contexto=ctx)

    return Explicacao(
        texto=_anexar_declaracoes(bruto.strip(), veredito.declaracoes_faltando),
        origem="llm", validacao=veredito, declaracoes=exigidas, contexto=ctx)
