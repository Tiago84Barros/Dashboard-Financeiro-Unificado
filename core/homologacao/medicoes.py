"""Quem mede os critérios de avanço de fase -- e quem admite não medir.

Os critérios existiam com limiar e sem medidor. Todos saíam ``None``, a tela
escrevia *"não medido nesta instalação"* para os dez, e nenhuma fase podia
avançar por **ausência de medição** -- não por reprovação. Um critério sem
medidor não protege nada: ele só adia a decisão para fora do sistema, onde
ninguém a audita.

Este módulo é o registro dos medidores. Cada entrada é uma função sem
argumentos que devolve :class:`Medicao`. ``valor=None`` continua sendo **não
medido**, e continua não avançando fase -- a lei do projeto vale aqui igual:
ausência de medição jamais vira aprovação silenciosa.

Amostra pequena é ausência, não aprovação
-----------------------------------------
Todo medidor daqui tem piso de amostra, e o motivo é o defeito mais fácil de
cometer neste arquivo. ``taxa_de_erro_da_coleta`` sobre zero ciclos dá zero
erros; ``itens_sem_fonte`` sobre zero notícias dá zero itens sem fonte. Os dois
são critérios "menor melhor", os dois passariam -- e passariam exatamente por
não terem sido testados. É o mesmo erro que já apareceu neste projeto com
"nenhuma deslistagem ingerida" lido como rigor (``memoria:
zero-censura-e-assinatura``). Abaixo do piso, o medidor devolve ``None`` com o
tamanho da amostra no motivo.

O que este módulo **não** faz: inventar medidor. Um critério como
``falsos_positivos_nivel_3_ou_4`` exige operação real observada, e não há
operação real observada -- a Fase 4 nunca rodou. Escrever um medidor que
devolvesse ``0.0`` por não ter encontrado nada seria o pior defeito possível
aqui. Esses continuam sem medidor, e a ausência aparece na tela com o motivo.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import text

logger = logging.getLogger(__name__)

#: Janela de observação dos medidores, em dias. Trinta dias é o compromisso
#: entre amostra e atualidade: mais curto não junta ciclos suficientes para uma
#: taxa de erro significar algo, e mais longo faria a nota de hoje carregar
#: meses de um comportamento que já foi corrigido.
JANELA_DIAS = 30

#: Pisos de amostra. Não são estatística fina -- são a fronteira entre "medi e
#: deu zero" e "não tinha o que medir".
MINIMO_ITENS = 30
MINIMO_CICLOS = 20
MINIMO_REGISTROS = 20


@dataclass(frozen=True)
class Medicao:
    """O valor medido, ou a razão de não haver valor.

    Um ``float | None`` solto obrigava a tela a adivinhar por que faltava o
    número: banco fora do ar, tabela ausente e amostra pequena chegavam iguais.
    São situações diferentes -- a primeira se resolve reconectando, a última só
    se resolve deixando o sistema rodar mais tempo -- e quem decide avançar de
    fase precisa distinguir as duas.
    """

    valor: float | None = None
    amostra: int = 0
    motivo: str = ""

    @property
    def medido(self) -> bool:
        return self.valor is not None


#: Por que cada critério ainda não tem medidor automático. Texto de tela: o
#: usuário precisa saber se falta rodar um teste ou se falta o mundo acontecer.
SEM_MEDIDOR: dict[str, str] = {
    "falsos_positivos_nivel_3_ou_4":
        "exige operação real observada; a Fase 4 nunca rodou, e contar zero "
        "num período sem operação seria aprovar o critério por não tê-lo "
        "testado",
    "tempo_ate_rebaixar_nivel_h":
        "exige um ciclo completo de subida e rebaixamento de nível em "
        "produção; medir em simulação diria o tempo do simulador",
    "alarmes_por_semana":
        "o alerta é montado a cada abertura de tela e não fica guardado, "
        "então não há série para contar por semana; e contar numa janela com "
        "o Modo Crise desligado daria zero por não ter havido o que alarmar",
    "erro_de_calibracao_probabilidade":
        "exige o desfecho realizado de cada probabilidade publicada; o acervo "
        "guarda a probabilidade estimada e não o que aconteceu depois, e "
        "calibração sem desfecho é a estimativa comparada consigo mesma",
}


def _engine():
    from core.database import get_engine

    return get_engine()


def _janela(conn, sql: str) -> dict | None:
    linha = conn.execute(text(sql), {"dias": JANELA_DIAS}).mappings().first()
    return dict(linha) if linha else None


# ── Fase 2 -- Painel informativo ─────────────────────────────────────────────

_SQL_ACERVO = """
    SELECT COUNT(*)::int AS total,
           COUNT(*) FILTER (WHERE publicado_em IS NOT NULL)::int AS com_data,
           COUNT(*) FILTER (
               WHERE COALESCE(NULLIF(TRIM(veiculo), ''),
                              NULLIF(TRIM(dominio), '')) IS NULL
           )::int AS sem_fonte
      FROM noticias_itens
     WHERE coletado_em >= NOW() - (:dias || ' days')::interval
"""


def _acervo() -> dict[str, Medicao]:
    """Frescor e fonte saem da mesma varredura do acervo.

    Uma consulta para dois critérios porque são a mesma linha lida duas vezes,
    e porque esta tela abre contra o Supabase: dois ``COUNT`` na mesma
    varredura custam uma ida, e duas consultas custariam duas.
    """
    motor = _engine()
    chaves = ("cobertura_de_frescor", "itens_sem_fonte")
    if motor is None:
        return dict.fromkeys(chaves, Medicao(motivo="sem banco configurado"))
    with motor.connect() as conn:
        dados = _janela(conn, _SQL_ACERVO) or {}
    total = int(dados.get("total") or 0)
    if total < MINIMO_ITENS:
        return dict.fromkeys(chaves, Medicao(
            amostra=total,
            motivo=(f"amostra insuficiente: {total} notícias nos últimos "
                    f"{JANELA_DIAS} dias, mínimo de {MINIMO_ITENS}")))
    return {
        "cobertura_de_frescor": Medicao(
            valor=int(dados.get("com_data") or 0) / total, amostra=total),
        # Contagem absoluta, e não taxa: o limiar do critério é zero, e uma
        # taxa arredondaria para zero um punhado de itens sem fonte num acervo
        # grande -- justamente os que a tela promete não exibir.
        "itens_sem_fonte": Medicao(
            valor=float(int(dados.get("sem_fonte") or 0)), amostra=total),
    }


_SQL_CICLOS = """
    SELECT COUNT(*)::int AS total,
           COUNT(*) FILTER (
               WHERE status IN ('indisponivel', 'degradado')
                  OR jsonb_array_length(COALESCE(erros, '[]'::jsonb)) > 0
           )::int AS falhos
      FROM noticias_coleta_ciclos
     WHERE iniciado_em >= NOW() - (:dias || ' days')::interval
"""


def _taxa_de_erro_da_coleta() -> Medicao:
    """Ciclos que falharam sobre ciclos executados.

    Falho é o ciclo com status degradado ou indisponível **ou** com erro
    registrado: um ciclo pode terminar com status utilizável e ainda ter
    perdido um provedor no caminho, e é essa perda que envelhece o painel sem
    que a tela mude de cara.
    """
    motor = _engine()
    if motor is None:
        return Medicao(motivo="sem banco configurado")
    with motor.connect() as conn:
        dados = _janela(conn, _SQL_CICLOS) or {}
    total = int(dados.get("total") or 0)
    if total < MINIMO_CICLOS:
        return Medicao(
            amostra=total,
            motivo=(f"amostra insuficiente: {total} ciclos de coleta nos "
                    f"últimos {JANELA_DIAS} dias, mínimo de {MINIMO_CICLOS}"))
    return Medicao(valor=int(dados.get("falhos") or 0) / total, amostra=total)


# ── Fase 3 -- Recomendações conjunturais ─────────────────────────────────────

_SQL_TRILHA = """
    SELECT COUNT(*)::int AS total,
           COUNT(*) FILTER (
               WHERE COALESCE(TRIM(motivo), '') <> ''
                 AND jsonb_array_length(COALESCE(evidencias, '[]'::jsonb)) > 0
                 AND COALESCE(TRIM(motor), '') <> ''
                 AND COALESCE(TRIM(versao_modelo), '') <> ''
                 AND COALESCE(TRIM(versao_dados), '') <> ''
                 AND momento IS NOT NULL
           )::int AS completos,
           COUNT(*) FILTER (WHERE llm_aprovada IS NOT NULL)::int AS julgadas,
           COUNT(*) FILTER (WHERE llm_aprovada IS FALSE)::int AS reprovadas
      FROM {tabela}
     WHERE momento >= NOW() - (:dias || ' days')::interval
"""


def _trilha() -> dict[str, Medicao]:
    """Cobertura da trilha e reprovação da LLM, da mesma tabela.

    **O que ``cobertura_da_trilha`` mede:** a fração dos registros que responde
    às três partes da pergunta do requisito -- o que foi recomendado, por quê,
    e o que estava vigente naquele momento. Registro sem motivo, sem evidência
    ou sem versão de modelo existe e não responde nada.

    **O que ela não mede:** recomendação que nunca chegou à trilha. Contá-la
    exigiria um denominador de fora da trilha, e medir dentro da própria tabela
    daria 100% por construção -- o defeito de contar só as entradas
    (``memoria: painel-so-com-entradas``). O que cobre esse flanco não é
    métrica e sim trava: ``trilha.registrar`` levanta ``AuditoriaIndisponivel``
    em vez de engolir a falha, e a trava ``auditoria_falhou`` bloqueia a
    mudança que não pôde ser registrada.
    """
    from core.auditoria import trilha

    motor = _engine()
    chaves = ("cobertura_da_trilha", "respostas_llm_reprovadas")
    if motor is None:
        return dict.fromkeys(chaves, Medicao(motivo="sem banco configurado"))
    with motor.connect() as conn:
        dados = _janela(conn, _SQL_TRILHA.format(tabela=trilha.TABELA)) or {}

    total = int(dados.get("total") or 0)
    saida: dict[str, Medicao] = {}
    if total < MINIMO_REGISTROS:
        saida["cobertura_da_trilha"] = Medicao(
            amostra=total,
            motivo=(f"amostra insuficiente: {total} recomendações registradas "
                    f"nos últimos {JANELA_DIAS} dias, mínimo de "
                    f"{MINIMO_REGISTROS}"))
    else:
        saida["cobertura_da_trilha"] = Medicao(
            valor=int(dados.get("completos") or 0) / total, amostra=total)

    # Denominador próprio: a taxa é sobre as respostas que passaram pelo
    # validador, não sobre todas as recomendações. Dividir pelo total faria a
    # taxa cair sozinha nos períodos em que a LLM esteve desligada -- o
    # critério melhoraria por não ter sido exercido.
    julgadas = int(dados.get("julgadas") or 0)
    if julgadas < MINIMO_REGISTROS:
        saida["respostas_llm_reprovadas"] = Medicao(
            amostra=julgadas,
            motivo=(f"amostra insuficiente: {julgadas} explicações passaram "
                    f"pelo validador nos últimos {JANELA_DIAS} dias, mínimo "
                    f"de {MINIMO_REGISTROS}"))
    else:
        saida["respostas_llm_reprovadas"] = Medicao(
            valor=int(dados.get("reprovadas") or 0) / julgadas,
            amostra=julgadas)
    return saida


# ── Fase 4 -- Modo Crise ─────────────────────────────────────────────────────

def _cenarios_historicos_reproduzidos() -> Medicao:
    """Cenários que reproduzem o retorno observado no índice de referência."""
    from core import stress_tests

    n = int(stress_tests.cenarios_reproduzidos())
    return Medicao(valor=float(n), amostra=n)


#: Nome do critério -> medidor de um valor só. Os que compartilham consulta
#: entram por ``_EM_LOTE``.
MEDIDORES: dict[str, Callable[[], Medicao]] = {
    "taxa_de_erro_da_coleta": _taxa_de_erro_da_coleta,
    "cenarios_historicos_reproduzidos": _cenarios_historicos_reproduzidos,
}

#: Medidores que devolvem mais de um critério por consulta, com os critérios
#: que cada um responde. A lista de chaves não é redundante: quando o lote
#: falha, é ela que diz **quais** critérios ficaram sem medida. Derivar isso de
#: "o que ainda não está no resultado" carimbaria o erro de um lote nos
#: critérios do outro, e o motivo na tela apontaria para a tabela errada.
_EM_LOTE: tuple[tuple[Callable[[], dict[str, Medicao]], tuple[str, ...]], ...] = (
    (_acervo, ("cobertura_de_frescor", "itens_sem_fonte")),
    (_trilha, ("cobertura_da_trilha", "respostas_llm_reprovadas")),
)

#: Todo critério que este módulo se propõe a medir. Existe para o teste poder
#: cobrar a lista sem depender do formato de cada medidor.
COBERTOS: frozenset[str] = frozenset(MEDIDORES).union(
    *(chaves for _, chaves in _EM_LOTE))


def _falha(exc: object) -> str:
    """Motivo curto de uma falha de medição, achatado e com corte declarado."""
    from core.auditoria import trilha

    return "a medição falhou: " + trilha.motivo_curto(exc, 160)


def medir_detalhado() -> dict[str, Medicao]:
    """Roda os medidores disponíveis, cada um isolado do outro.

    Um medidor que levante exceção vira ``None`` -- não medido -- em vez de
    derrubar a medição dos outros e a tela inteira junto. Medição que falha é
    medição ausente, e ausente já tem tratamento correto.
    """
    saida: dict[str, Medicao] = {}
    for nome, fn in MEDIDORES.items():
        try:
            saida[nome] = fn()
        except Exception as exc:  # noqa: BLE001 - medidor quebrado é ausência
            logger.exception("medidor de %s falhou", nome)
            saida[nome] = Medicao(motivo=_falha(exc))
    for lote, chaves in _EM_LOTE:
        try:
            saida.update(lote())
        except Exception as exc:  # noqa: BLE001
            logger.exception("medidor em lote %s falhou", lote.__name__)
            for nome in chaves:
                saida[nome] = Medicao(motivo=_falha(exc))
    return saida


def medir() -> dict[str, float | None]:
    """Só os valores. ``criterios.avaliar`` decide, e decide sobre ``float``.

    Chave ausente e valor ``None`` são a mesma coisa para quem avalia, e é ele
    quem deve decidir -- repetir a decisão aqui daria dois lugares para o mesmo
    julgamento e um dia eles divergiriam.
    """
    return {nome: m.valor for nome, m in medir_detalhado().items()}


def situacao(nome: str, medidas=None) -> str:
    """Frase de tela para um critério: o valor medido, ou por que não há.

    Aceita tanto o mapa detalhado quanto o de valores porque o segundo é o que
    ``criterios`` consome; recusar um deles obrigaria a tela a medir duas vezes
    para escrever a mesma linha.
    """
    medidas = medidas if medidas is not None else medir_detalhado()
    achado = medidas.get(nome)
    medicao = achado if isinstance(achado, Medicao) else Medicao(
        valor=achado if isinstance(achado, (int, float)) else None)
    if medicao.medido:
        corpo = f"medido: {medicao.valor:g}"
        return (f"{corpo} (amostra: {medicao.amostra})" if medicao.amostra
                else corpo)
    if medicao.motivo:
        return f"não medido — {medicao.motivo}"
    if nome in SEM_MEDIDOR:
        return f"não medido — {SEM_MEDIDOR[nome]}"
    if nome in COBERTOS:
        return "não medido — o medidor existe mas falhou nesta execução"
    return "não medido — este critério ainda não tem medidor automático"
