"""A tomada entre a Memória de Mercado e o Motor Conjuntural de notícias.

:mod:`core.noticias.impacto` foi escrito com um encaixe explícito -- *"``base`` é
a única porta por onde probabilidade e faixa entram"* -- e este módulo é o
plugue. Ele não recalcula nada: traduz uma :class:`AmostraHistorica` numa
:class:`~core.noticias.impacto.BaseHistorica` e deixa o motor de notícias
aplicar os próprios portões.

Duas conversões que precisam estar certas
-----------------------------------------
**Unidade.** Este pacote trabalha em fração (−0,064). ``BaseHistorica`` trabalha
em pontos percentuais, porque ``FaixaVariacao`` tem ``unidade="%"``. Passar
fração onde se espera porcentagem publica "−0,1%" no lugar de "−6,4%" sem erro
nenhum -- e o número errado sai com cara de número certo, que é o modo de falha
de ``memoria: defeito-silencioso-vs-erro``. A multiplicação por 100 acontece
aqui, num lugar só.

**Piso de amostra.** A Memória publica faixa a partir de 8 eventos; o Motor
Conjuntural exige 30 para publicar probabilidade. A ponte **não** força a
passagem: uma amostra de 12 vira uma ``BaseHistorica`` com
``n_observacoes=12``, e ``suficiente`` continua devolvendo ``False`` do outro
lado. Os dois módulos mantêm cada um o seu piso, e a diferença fica explícita em
:func:`descrever`.
"""
from __future__ import annotations

from core.calibracao import limiar as lim
from core.memoria_mercado.amostra import AmostraHistorica
from core.noticias import taxonomia
from core.noticias.impacto import BaseHistorica

#: Limiar de "movimento relevante" quando nada se sabe sobre o ativo, em fração.
#:
#: Era 0,03 fixo para tudo, e essa era a versão errada: 3% é quase quatro desvios
#: num FII (o motor ficava mudo) e é uma terça-feira comum numa small cap (o
#: motor virava alarme). Quem define o limiar agora é
#: :mod:`core.calibracao.limiar`, por classe de ativo e pela volatilidade medida
#: do próprio ativo antes do evento. Este valor sobrou como último recurso: é o
#: prior da classe desconhecida, e sai marcado como não estimado.
#:
#: O número continua viajando para a saída e continua sendo impresso junto da
#: probabilidade, para que "72%" nunca apareça sem o "de variação acima de X%".
LIMIAR_RELEVANTE_PADRAO = lim.PARAMETROS[lim.CLASSE_DESCONHECIDA].prior(1)

#: Pregões -> horizonte qualitativo da taxonomia de notícias. Um pregão é
#: intradia do ponto de vista da notícia; 60 pregões são ~3 meses, que a
#: taxonomia chama de médio.
HORIZONTE_POR_PREGOES: dict[int, str] = {
    1: taxonomia.HORIZONTE_INTRADIA,
    5: taxonomia.HORIZONTE_CURTO,
    20: taxonomia.HORIZONTE_CURTO,
    60: taxonomia.HORIZONTE_MEDIO,
}


def horizonte_qualitativo(pregoes: int) -> str:
    """Converte pregões no rótulo da taxonomia, por faixa e não por tabela fixa."""
    direto = HORIZONTE_POR_PREGOES.get(int(pregoes))
    if direto:
        return direto
    p = int(pregoes)
    if p <= 1:
        return taxonomia.HORIZONTE_INTRADIA
    if p <= 21:
        return taxonomia.HORIZONTE_CURTO
    if p <= 126:
        return taxonomia.HORIZONTE_MEDIO
    return taxonomia.HORIZONTE_LONGO


def para_base_historica(amostra: AmostraHistorica, *,
                        limiar_relevante: float | None = None,
                        limiar: lim.Limiar | None = None,
                        fonte: str | None = None) -> BaseHistorica | None:
    """Traduz a amostra. Devolve ``None`` quando não há o que traduzir.

    ``None`` -- e não uma base vazia -- porque ``estimar`` do motor de notícias
    trata ``base=None`` como "sem base", que é a leitura correta. Uma base com
    ``n_observacoes=0`` atravessaria os portões dele carregando zeros.

    ``limiar`` é o caminho preferido: um :class:`core.calibracao.limiar.Limiar`
    calculado com a volatilidade do próprio ativo na janela anterior ao evento.
    ``limiar_relevante`` continua aceito como número solto para quem já tem o
    valor pronto; sem nenhum dos dois, entra o prior da classe desconhecida --
    que é pior que os dois e por isso vira uma limitação declarada em
    :func:`descrever`.
    """
    if amostra is None or amostra.n_eventos <= 0:
        return None
    principal = amostra.principal
    if principal is None:
        return None

    if limiar is not None:
        limiar_relevante = limiar.valor
    elif limiar_relevante is None:
        limiar_relevante = LIMIAR_RELEVANTE_PADRAO

    prob = amostra.prob_movimento_relevante(limiar_relevante)
    procedencia = fonte or (
        "memoria_mercado:retorno_bruto" if amostra.usa_retorno_bruto
        else "memoria_mercado:retorno_anormal")

    periodo = amostra.periodo
    janela = (f"{periodo[0]} a {periodo[1]}" if periodo else None)

    return BaseHistorica(
        tipo_evento=amostra.tipo_evento,
        n_observacoes=amostra.n_eventos,
        limiar_relevante=limiar_relevante * 100.0,   # fração -> pontos percentuais
        horizonte=horizonte_qualitativo(amostra.horizonte),
        prob_movimento_relevante=prob,
        p10=principal.p10 * 100.0,
        p90=principal.p90 * 100.0,
        fonte=procedencia,
        janela=janela,
    )


def descrever(amostra: AmostraHistorica, base: BaseHistorica | None,
              limiar: lim.Limiar | None = None) -> tuple[str, ...]:
    """Limitações que só existem por causa da travessia entre os dois módulos.

    Elas não pertencem a nenhum dos dois lados: pertencem à junção. Ficam aqui
    para que a tela consiga dizer "a Memória publicou uma faixa, o Motor
    Conjuntural não publicou probabilidade, e o motivo é o piso diferente" --
    em vez de mostrar um campo vazio sem explicação.
    """
    itens: list[str] = []
    if base is None:
        itens.append(
            "nenhuma base historica repassada ao motor de noticias: sem eventos "
            "comparaveis medidos")
        return tuple(itens)
    if not base.suficiente:
        itens.append(
            f"base de {base.n_observacoes} eventos: suficiente para a faixa "
            "experimental da Memoria de Mercado, insuficiente para o motor de "
            "noticias publicar probabilidade")
    if amostra.usa_retorno_bruto:
        itens.append(
            "base construida sobre retorno bruto: a probabilidade repassada "
            "mede movimento do ativo, nao efeito isolado do evento")
    if amostra.experimental:
        itens.append(
            "base marcada como experimental pela Memoria de Mercado")
    if limiar is None:
        itens.append(
            "limiar de movimento relevante do prior da classe desconhecida: a "
            "volatilidade do ativo nao foi medida, e a probabilidade publicada "
            "vale para um ativo tipico, nao para este")
    else:
        if not limiar.estimado:
            itens.append(
                f"limiar de movimento relevante nao estimado: {limiar.descrever()}")
        itens.extend(limiar.limitacoes)
    return tuple(itens)
