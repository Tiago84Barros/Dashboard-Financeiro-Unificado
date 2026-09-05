"""A base histórica que alimenta o portão quantitativo, lida do armazém local.

Por que este módulo existe
--------------------------
``coleta.coletar`` aceita ``bases`` desde que o motor de impacto foi escrito, e
até 05/09/2026 nenhum chamador de produção o preenchia. A consequência medida
está na revisão de 02/09 (A-141): em **12 de 12** cenários o portão quantitativo
saía ``indeterminado``, e ``None`` não aprova — logo ``sugerir_revisao`` era
estruturalmente inalcançável, mesmo com os outros cinco portões abertos e nota
82,8.

Corrigir só o portão teria mudado o lugar do problema, não o problema: critério
sem entrada continua inalcançável. Por isso o que este módulo faz é abrir a
porta da entrada — traduzir o que a Memória de Mercado já sabe medir para o
formato que o motor de notícias já sabe consumir.

De onde vem, e de onde **não** vem
----------------------------------
Vem de ``memoria_mercado.eventos_medidos``, no armazém local. Nunca do Supabase:
a instrução do usuário é literal, e o Supabase estava em 477 MB de 500 em
05/09/2026. :func:`core.memoria_mercado.repositorio.carregar_eventos` chama
``exigir_local`` por conta própria, então um destino remoto aqui vira exceção,
não vira gravação.

O que ele não faz
-----------------
Não inventa base. Sem safra construída, :func:`carregar` devolve ``{}`` e uma
limitação declarada — e o portão quantitativo continua em "não medido", que é a
descrição correta do estado do mundo. Uma base vazia com ``n_observacoes=0``
atravessaria os portões carregando zeros, e é exatamente o modo de falha do
*fallback que só preenche lacuna e nunca contradiz*.
"""
from __future__ import annotations

import logging

from core.memoria_mercado.retornos import HORIZONTES as _HORIZONTES_MEDIDOS

logger = logging.getLogger(__name__)

#: Horizonte, em pregões, em que a base é lida. Trocar este número troca o
#: significado da probabilidade publicada, e por isso ele é constante nomeada e
#: não literal solto no meio da consulta.
#:
#: Ele **tem** de ser um dos horizontes que o pipeline mede. ``amostra.resumir``
#: só empilha evento que tenha aquele horizonte medido -- é assim que ele evita
#: contar foto truncada como reação nula -- então um horizonte fora de
#: :data:`core.memoria_mercado.retornos.HORIZONTES` não devolve erro: devolve
#: amostra vazia, base ``None`` e portão em "não medido" para sempre. Um "21"
#: escrito à mão aqui (~1 mês corrido) passaria em toda revisão de código e
#: nunca produziria uma única base. Por isso o valor é escolhido *dentro* da
#: tupla medida, e o ``assert`` de módulo quebra se ela mudar sem que este
#: módulo acompanhe.
HORIZONTE_PREGOES = 20


assert HORIZONTE_PREGOES in _HORIZONTES_MEDIDOS, (
    f"HORIZONTE_PREGOES={HORIZONTE_PREGOES} nao esta entre os horizontes "
    f"medidos {_HORIZONTES_MEDIDOS}: a base sairia vazia em silencio")


def _rehidratar(linha: dict):
    """Reconstrói ``EventoMedido`` a partir da linha crua do repositório.

    O repositório devolve dicionário de propósito — conferir cobertura da base
    não precisa reidratar objeto nenhum. Quem precisa do dataclass é
    ``amostra.resumir``, e a reidratação mora aqui, junto de quem a usa.
    """
    from core.memoria_mercado.retornos import EventoMedido, MetricasJanela

    janelas = {}
    for horizonte, dados in (linha.get("janelas") or {}).items():
        if not isinstance(dados, dict):
            continue
        campos = {c: dados.get(c) for c in MetricasJanela.__dataclass_fields__
                  if c in dados}
        campos["horizonte"] = int(horizonte)
        janelas[int(horizonte)] = MetricasJanela(**campos)

    return EventoMedido(
        chave=str(linha.get("chave") or ""),
        simbolo=str(linha.get("simbolo") or ""),
        tipo_evento=str(linha.get("tipo_evento") or ""),
        data_evento=linha.get("data_evento"),
        janelas=janelas,
        limitacoes=tuple(linha.get("limitacoes") or ()),
    )


def carregar(engine=None, *, horizonte: int = HORIZONTE_PREGOES
             ) -> tuple[dict, tuple[str, ...]]:
    """Bases por tipo de evento, e as limitações do que não pôde ser medido.

    Devolve ``({}, (motivo,))`` em qualquer caminho que não produza medição:
    sem armazém, sem safra, ou safra que não cobre o horizonte. Nenhum desses
    casos vira base vazia silenciosa.
    """
    if engine is None:
        try:
            from core.noticias.destino import engine_acervo
            engine = engine_acervo()
        except Exception as exc:  # noqa: BLE001 - coleta não cai por isto
            return {}, (f"base historica nao lida ({exc}): o portao "
                        f"quantitativo fica em 'nao medido'",)
    if engine is None:
        return {}, ("armazem local nao configurado: sem base historica, o "
                    "portao quantitativo fica em 'nao medido' e nenhuma "
                    "noticia vira sugestao de revisao",)

    try:
        from core.memoria_mercado import ponte_noticias as ponte
        from core.memoria_mercado import repositorio as repo
        from core.memoria_mercado.amostra import resumir

        linhas = repo.carregar_eventos(engine)
    except Exception as exc:  # noqa: BLE001 - falha declarada, não vazio
        causa = str(exc).splitlines()[0][:160]
        logger.warning("Base historica indisponivel: %s", causa)
        return {}, (f"base historica indisponivel ({causa}): o portao "
                    f"quantitativo fica em 'nao medido'",)

    if not linhas:
        return {}, ("memoria de mercado sem safra construida: o portao "
                    "quantitativo fica em 'nao medido' em toda noticia. "
                    "Construir com scripts/construir_memoria_mercado.py",)

    por_tipo: dict[str, list] = {}
    for linha in linhas:
        tipo = str(linha.get("tipo_evento") or "")
        if tipo:
            por_tipo.setdefault(tipo, []).append(_rehidratar(linha))

    bases: dict = {}
    sem_horizonte: list[str] = []
    for tipo, eventos in sorted(por_tipo.items()):
        amostra = resumir(eventos, tipo_evento=tipo, horizonte=horizonte)
        base = ponte.para_base_historica(amostra)
        if base is None:
            sem_horizonte.append(tipo)
            continue
        bases[tipo] = base

    limitacoes: list[str] = []
    if sem_horizonte:
        limitacoes.append(
            f"sem base utilizavel em {horizonte} pregoes para: "
            + ", ".join(sem_horizonte)
            + " -- o portao quantitativo fica em 'nao medido' nesses tipos")
    logger.info("Bases historicas: %s tipos com base, %s sem",
                len(bases), len(sem_horizonte))
    return bases, tuple(limitacoes)
