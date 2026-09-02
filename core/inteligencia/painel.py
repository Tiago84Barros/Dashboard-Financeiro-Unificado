"""O objeto único que a tela renderiza e a LLM explica.

Regra de fronteira: **este módulo não calcula nada**. Score estrutural e
conjuntural saem de :mod:`core.memoria_mercado.scores`; o nível de crise sai de
:mod:`core.eventos_extremos.transicao`; a faixa de impacto sai de
:mod:`core.memoria_mercado.estimativa`; o índice sai de
:mod:`core.eventos_extremos.antifragilidade`; relevância e impacto de notícia
saem de :mod:`core.noticias`. Aqui os resultados são traduzidos para o
vocabulário de :mod:`core.inteligencia.qualificacao` e empacotados.

Por que traduzir em vez de a view ler os motores direto
-------------------------------------------------------
Porque a LLM e a tela precisam ver **o mesmo** conjunto de números. Se a view
formatasse a partir dos motores e o prompt fosse montado de outra fonte, os dois
divergiriam no dia em que um motor mudasse -- e a divergência apareceria como a
LLM "inventando" um número que na verdade era o número antigo. Com um objeto só,
:meth:`Painel.numeros` é literalmente o conjunto do que pode ser citado, e
:mod:`core.inteligencia.llm` ancora a resposta nele.

A seção que não pôde ser calculada continua aparecendo
------------------------------------------------------
Bloco sem dado não some da tela: ele sai com os valores em
:data:`~core.inteligencia.qualificacao.AUSENTE` e o motivo ao lado. Seção que
desaparece é indistinguível de seção que está tudo bem, e este repositório já
publicou um painel onde a ausência de saídas parecia saúde
(``memoria: zero-censura-e-assinatura``).
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

from core.inteligencia import qualificacao as qz
from core.inteligencia.qualificacao import (AUSENTE, Bloco, Frescor, Provedor,
                                            Valor, ausente, estimativa, fato,
                                            hipotese)

logger = logging.getLogger(__name__)

PAINEL_VERSAO = "1.0.0"

# ── Situação do ativo (o campo "situação" do requisito) ──────────────────────
SIT_NORMAL = "normal"
SIT_OBSERVACAO = "observacao"
SIT_SUSPENSAO = "suspensao"
SIT_REVISAO = "revisao"

SITUACOES: tuple[str, ...] = (SIT_NORMAL, SIT_OBSERVACAO, SIT_SUSPENSAO, SIT_REVISAO)

APARENCIA_SITUACAO: dict[str, dict[str, str]] = {
    SIT_NORMAL: {"rotulo": "Normal", "icone": "●", "cor": "#00C896"},
    SIT_OBSERVACAO: {"rotulo": "Em observação", "icone": "◐", "cor": "#4A9EFF"},
    SIT_REVISAO: {"rotulo": "Em revisão", "icone": "▲", "cor": "#E8B84B"},
    SIT_SUSPENSAO: {"rotulo": "Aporte suspenso", "icone": "■", "cor": "#FC5C7D"},
}

#: Ação do motor de scores -> situação exibida. A ordem importa: a primeira que
#: casar vence, e a mais restritiva vem primeiro.
_ACAO_PARA_SITUACAO: tuple[tuple[str, str], ...] = (
    ("suspender_aporte", SIT_SUSPENSAO),
    ("reavaliar_fundamentos", SIT_REVISAO),
    ("observar", SIT_OBSERVACAO),
)

VALIDADE_PADRAO_HORAS: dict[str, float] = {
    "noticias": 6.0,
    "mercado": 24.0,
    "carteira": 24.0,
    "memoria_mercado": 168.0,
    "crise": 6.0,
}


def _agora(valor: dt.datetime | None) -> dt.datetime:
    return valor or dt.datetime.now(dt.timezone.utc)


# ── Notícias ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ItemNoticia:
    """Uma notícia ou um evento agrupado, pronto para a lista.

    ``titulo`` e ``resumo`` são hipótese enquanto o evento não é confirmado; a
    relevância e a confiabilidade, ao contrário, são fato -- nós as medimos. Não
    colapsar as duas coisas é o que permite mostrar uma manchete não confirmada
    sem que a tela pareça endossá-la.
    """

    id: str
    titulo: str
    fonte: str
    publicado_em: dt.datetime | None
    qualidade_conteudo: str = qz.HIPOTESE
    resumo: str = ""
    url: str = ""
    coletado_em: dt.datetime | None = None
    estado_verificacao: str = "nao_verificada"
    n_fontes: int = 1
    tipo_evento: str = ""
    tickers: tuple[str, ...] = ()
    empresas: tuple[str, ...] = ()
    setores: tuple[str, ...] = ()
    paises: tuple[str, ...] = ()
    moedas: tuple[str, ...] = ()
    classes: tuple[str, ...] = ()
    relevancia: Valor | None = None
    confiabilidade: Valor | None = None
    direcao: Valor | None = None
    impacto: Valor | None = None
    horizonte: Valor | None = None
    n_materias: int = 1

    @property
    def confirmado(self) -> bool:
        return self.estado_verificacao in ("confirmada_fonte_primaria",
                                           "confirmada_independente")

    @property
    def carimbo(self) -> str:
        """Fonte, data e hora -- os três que toda notícia tem de mostrar."""
        if self.publicado_em is None:
            return f"{self.fonte} · data não informada"
        quando = qz._aware(self.publicado_em)
        return f"{self.fonte} · {quando.strftime('%d/%m/%Y %H:%M UTC')}"

    def valores(self) -> tuple[Valor, ...]:
        return tuple(v for v in (self.relevancia, self.confiabilidade,
                                 self.direcao, self.impacto, self.horizonte)
                     if v is not None)

    def numeros(self) -> tuple[float, ...]:
        saida: list[float] = [float(self.n_fontes), float(self.n_materias)]
        for v in self.valores():
            saida.extend(v.numeros())
        return tuple(saida)


def _direcao_valor(direcao: str | None, *, confirmado: bool) -> Valor:
    rotulos = {"alta": "alta provável", "baixa": "baixa provável",
               "neutra": "sem direção dominante"}
    if not direcao or direcao == "indefinida" or direcao not in rotulos:
        return ausente("Direção provável",
                       "o evento não sustenta direção: nem alta nem baixa "
                       "podem ser afirmadas")
    obs = ("evento confirmado" if confirmado
           else "evento ainda não confirmado: a direção segue o relato")
    return Valor(rotulo="Direção provável", valor=rotulos[direcao],
                 qualidade=qz.ESTIMATIVA, observacao=obs)


def item_de_avaliada(avaliada, *, classes: tuple[str, ...] = ()) -> ItemNoticia:
    """Traduz :class:`core.noticias.modelos.NoticiaAvaliada` para a lista."""
    n = avaliada.noticia
    ent = n.entidades
    rel = avaliada.relevancia
    imp = avaliada.impacto
    dominio = (n.fonte.dominio if getattr(n, "fonte", None) else "") or n.provedor or "?"

    faixa = getattr(imp, "faixa", None)
    if faixa is not None:
        v_impacto = estimativa(
            "Impacto estimado", faixa=(float(faixa.minimo), float(faixa.maximo)),
            unidade=faixa.unidade, confianca=imp.grau_confianca,
            observacao=(f"base: {imp.fonte_base}" if imp.fonte_base else
                        "sem base histórica declarada"))
    else:
        v_impacto = ausente("Impacto estimado",
                            "sem base histórica para este tipo de evento")

    horizonte = getattr(imp, "horizonte", "indeterminado")
    v_horizonte = (ausente("Horizonte", "não determinado pelo classificador")
                   if horizonte in ("", "indeterminado") else
                   fato("Horizonte", horizonte))

    return ItemNoticia(
        id=n.id_dedup or n.hash_conteudo or n.titulo[:40],
        titulo=n.titulo, resumo=n.resumo or "", url=n.url_canonica or n.url or "",
        fonte=dominio, publicado_em=n.publicado_em, coletado_em=n.coletado_em,
        qualidade_conteudo=(qz.FATO if avaliada.confirmado_por_primaria
                            else qz.HIPOTESE),
        estado_verificacao=avaliada.estado_verificacao,
        n_fontes=int(avaliada.n_fontes_independentes),
        tipo_evento=n.tipo_evento or "",
        tickers=tuple(ent.tickers), empresas=tuple(ent.empresas),
        setores=tuple(ent.setores), paises=tuple(ent.paises),
        moedas=tuple(ent.moedas), classes=classes,
        relevancia=fato("Relevância", round(float(rel.nota), 1), unidade="/100",
                        observacao=f"faixa: {rel.rotulo_faixa}"),
        confiabilidade=(fato("Confiabilidade da fonte",
                             round(float(n.fonte.confiabilidade), 2))
                        if getattr(n, "fonte", None) is not None
                        else ausente("Confiabilidade da fonte",
                                     "fonte não cadastrada")),
        direcao=_direcao_valor(getattr(imp, "direcao", None),
                               confirmado=avaliada.confirmado_por_primaria),
        impacto=v_impacto, horizonte=v_horizonte)


def item_de_evento(evento, *, relevancia: float | None = None,
                   classes: tuple[str, ...] = ()) -> ItemNoticia:
    """Traduz :class:`core.noticias.eventos.Evento` (matérias agrupadas)."""
    principal = evento.principal
    ent = evento.entidades
    dominio = (principal.fonte.dominio if principal.fonte else "") or "?"
    return ItemNoticia(
        id=evento.id, titulo=principal.titulo, resumo=principal.resumo or "",
        url=principal.url_canonica or principal.url or "", fonte=dominio,
        publicado_em=evento.primeiro_em, coletado_em=principal.coletado_em,
        qualidade_conteudo=(qz.FATO if evento.confirmado_por_primaria
                            else qz.HIPOTESE),
        estado_verificacao=evento.estado_verificacao,
        n_fontes=evento.n_fontes_independentes, tipo_evento=evento.tipo,
        n_materias=len(evento.clusters),
        tickers=tuple(ent.tickers), empresas=tuple(ent.empresas),
        setores=tuple(ent.setores), paises=tuple(ent.paises),
        moedas=tuple(ent.moedas), classes=classes,
        relevancia=(fato("Relevância", round(float(relevancia), 1), unidade="/100")
                    if relevancia is not None
                    else ausente("Relevância", "evento ainda não pontuado")),
        confiabilidade=fato("Fontes independentes", evento.n_fontes_independentes),
        direcao=_direcao_valor(None, confirmado=evento.confirmado_por_primaria),
        impacto=ausente("Impacto estimado", "evento agrupado ainda não estimado"),
        horizonte=ausente("Horizonte", "não determinado"))


def filtrar(itens, *, empresa: str | None = None, ticker: str | None = None,
            setor: str | None = None, pais: str | None = None,
            classe: str | None = None, tipo_evento: str | None = None,
            confirmadas: bool | None = None) -> tuple[ItemNoticia, ...]:
    """Os filtros que a área de Inteligência de Mercado precisa oferecer.

    Comparação sem distinção de caixa nem de acento é deliberada: o usuário
    digita ``petrobras`` e a entidade veio como ``Petrobrás``.
    """
    def casa(campo: tuple[str, ...], alvo: str | None) -> bool:
        if not alvo:
            return True
        alvo_n = _normal(alvo)
        return any(alvo_n in _normal(v) for v in campo)

    saida = []
    for it in itens:
        if not casa(it.tickers, ticker):
            continue
        if not casa(it.empresas, empresa):
            continue
        if not casa(it.setores, setor):
            continue
        if not casa(it.paises, pais):
            continue
        if not casa(it.classes, classe):
            continue
        if tipo_evento and _normal(tipo_evento) != _normal(it.tipo_evento):
            continue
        if confirmadas is not None and it.confirmado is not confirmadas:
            continue
        saida.append(it)
    return tuple(saida)


def _normal(texto: str) -> str:
    import unicodedata
    sem = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in sem if not unicodedata.combining(c)).strip().lower()


# ── Crise ────────────────────────────────────────────────────────────────────
def bloco_crise(veredito=None, *, exposicao=None, evento: ItemNoticia | None = None,
                frescor: Frescor | None = None) -> Bloco:
    """O painel de crise, em linguagem descritiva.

    Sem veredito o bloco não some: ele diz que o motor não rodou. Painel de
    crise ausente é lido como "não há crise", que é exatamente a afirmação que
    não se pode fazer sem ter medido.
    """
    if veredito is None:
        return Bloco(
            titulo="Situação de crise",
            valores=(ausente("Nível de crise", "o motor de eventos extremos não "
                             "foi executado nesta sessão"),),
            frescor=frescor,
            limitacoes=("Sem avaliação de nível: a ausência de alerta aqui não "
                        "significa ausência de risco.",),
            explicacao_simples="Ainda não avaliamos o nível de crise agora.")

    nivel = veredito.nivel
    valores: list[Valor] = [
        fato("Nível de crise", f"{nivel.codigo} — {nivel.rotulo}"),
        fato("Severidade medida", round(float(veredito.severidade), 2),
             unidade="/1", observacao="0 = normal, 1 = severidade máxima"),
        fato("Confiança da avaliação", round(float(veredito.confianca), 2),
             unidade="/1"),
    ]

    if veredito.nivel_bruto != nivel.codigo:
        valores.append(fato(
            "Nível antes dos tetos", veredito.nivel_bruto,
            observacao=f"barrado para {nivel.codigo} por regra de contenção"))

    valores.append(
        fato("Confirmação", _texto_confirmacao(evento)) if evento is not None
        else ausente("Confirmação", "nenhum evento associado a esta avaliação"))

    valores.append(
        fato("Abrangência", veredito.abrangencia) if veredito.abrangencia
        else ausente("Abrangência", "não classificada: local, setorial, "
                     "nacional ou global não pôde ser determinado"))

    sev_carteira = veredito.severidade_carteira
    valores.append(
        fato("Exposição estimada da carteira", round(float(sev_carteira), 2),
             unidade="/1", observacao="severidade derivada da carteira, não "
             "valor em reais")
        if sev_carteira is not None
        else ausente("Exposição estimada da carteira",
                     "a carteira não foi avaliada contra este evento"))

    valores.extend(_valores_de_exposicao(exposicao))

    limitacoes = list(veredito.limitacoes)
    for chave, cob in sorted(veredito.cobertura.items()):
        if cob < 1.0:
            limitacoes.append(
                f"evidência {chave}: {cob:.0%} dos indicadores medidos")

    return Bloco(
        titulo="Situação de crise",
        valores=tuple(valores),
        frescor=frescor,
        limitacoes=tuple(limitacoes),
        explicacao_simples=nivel.resumo,
        detalhe_tecnico=tuple(veredito.justificativa()))


def _texto_confirmacao(evento: ItemNoticia) -> str:
    if evento.estado_verificacao == "confirmada_fonte_primaria":
        return f"confirmado por fonte oficial ({evento.fonte})"
    if evento.estado_verificacao == "confirmada_independente":
        return f"confirmado por {evento.n_fontes} fontes independentes"
    if evento.estado_verificacao == "contestada":
        return "contestado: há fontes divergindo"
    return f"não confirmado ({evento.n_fontes} fonte(s), sem oficial)"


def _valores_de_exposicao(exposicao) -> tuple[Valor, ...]:
    """Traduz :class:`core.eventos_extremos.exposicao` sem exigir sua presença."""
    if exposicao is None:
        return (ausente("Ativos vulneráveis",
                        "exposição da carteira não calculada"),
                ausente("Liquidez disponível",
                        "exposição da carteira não calculada"))
    saida: list[Valor] = []
    direta = getattr(exposicao, "direta", None)
    indireta = getattr(exposicao, "indireta", None)
    saida.append(fato("Exposição direta", round(float(direta), 4), unidade="")
                 if direta is not None else
                 ausente("Exposição direta", "sem mapeamento de ativos afetados"))
    saida.append(fato("Exposição indireta", round(float(indireta), 4), unidade="")
                 if indireta is not None else
                 ausente("Exposição indireta", "sem grafo de contágio"))
    vulneraveis = tuple(getattr(exposicao, "vulneraveis", ()) or ())
    saida.append(fato("Ativos vulneráveis", ", ".join(vulneraveis))
                 if vulneraveis else
                 ausente("Ativos vulneráveis", "nenhum ativo mapeado ao evento"))
    return tuple(saida)


# ── Antifragilidade ──────────────────────────────────────────────────────────
def bloco_antifragilidade(indice=None, *, frescor: Frescor | None = None) -> Bloco:
    """Os doze componentes, cada um publicado, medido ou não.

    A nota geral vem primeiro na leitura mas não substitui os componentes: o
    motor foi escrito para não esconder risco dentro de uma letra, e a tela não
    pode desfazer isso mostrando só a letra.
    """
    if indice is None:
        return Bloco(titulo="Antifragilidade da carteira",
                     valores=(ausente("Índice de antifragilidade",
                                      "o índice não foi calculado nesta sessão"),),
                     frescor=frescor,
                     limitacoes=("Sem índice: nada aqui autoriza concluir que a "
                                 "carteira resiste a choques.",))

    valores: list[Valor] = []
    if indice.valor is None:
        valores.append(ausente(
            "Índice de antifragilidade",
            "componentes insuficientes: veja as limitações abaixo"))
    else:
        valores.append(fato(
            "Índice de antifragilidade", round(float(indice.valor), 2),
            unidade="/1",
            observacao=("teto aplicado por componente crítico"
                        if indice.teto_aplicado else "")))
        if indice.teto_aplicado and indice.bruto is not None:
            valores.append(fato(
                "Índice antes do teto", round(float(indice.bruto), 2), unidade="/1",
                observacao="um componente crítico limita o índice inteiro"))

    valores.append(fato("Cobertura dos componentes",
                        round(float(indice.cobertura) * 100, 1), unidade="%"))

    for parte in indice.partes:
        valores.append(
            fato(parte.rotulo, round(float(parte.nota), 2), unidade="/1",
                 observacao=parte.evidencia)
            if parte.medido else
            ausente(parte.rotulo, parte.evidencia or "sem fonte"))

    return Bloco(
        titulo="Antifragilidade da carteira", valores=tuple(valores),
        frescor=frescor,
        limitacoes=tuple(indice.limitacoes) + tuple(indice.alertas),
        explicacao_simples=(
            "Mede o quanto a carteira aguenta um choque — liquidez, "
            "concentração, crédito, câmbio e dependência do Brasil. "
            "Não é previsão de retorno."),
        detalhe_tecnico=tuple(indice.descrever()))


# ── Memória de mercado ───────────────────────────────────────────────────────
def bloco_memoria(est=None, *, evento_atual: str = "",
                  evento_mais_similar: str = "",
                  tempo_recuperacao: float | None = None,
                  frescor: Frescor | None = None) -> Bloco:
    """Evento atual, amostra histórica, faixa e o que a limita."""
    if est is None:
        return Bloco(
            titulo="Memória de mercado",
            valores=(ausente("Eventos históricos comparáveis",
                             "nenhum evento comparável foi encontrado"),),
            frescor=frescor,
            limitacoes=("Sem amostra histórica: o comportamento passado deste "
                        "tipo de evento não pôde ser medido.",),
            explicacao_simples="Ainda não temos casos parecidos para comparar.")

    horiz = (f"{est.horizonte[0]} a {est.horizonte[1]} pregões"
             if est.horizonte else None)
    valores: list[Valor] = [
        fato("Evento atual", evento_atual or est.tipo_evento),
        fato("Tamanho da amostra", int(est.n_amostra),
             observacao="eventos históricos comparáveis encontrados"),
    ]

    valores.append(fato("Evento mais semelhante", evento_mais_similar)
                   if evento_mais_similar else
                   ausente("Evento mais semelhante",
                           "a amostra não identificou um caso dominante"))

    valores.append(
        fato("Similaridade com o cenário atual", round(float(est.similaridade), 1),
             unidade="/100")
        if est.similaridade is not None else
        ausente("Similaridade com o cenário atual", "não pôde ser calculada"))

    valores.append(
        fato("Reação mediana histórica", round(float(est.mediana_historica) * 100, 2),
             unidade="%", observacao=f"retorno {est.base_retorno}")
        if est.mediana_historica is not None else
        ausente("Reação mediana histórica", "amostra sem retorno apurado"))

    valores.append(
        estimativa("Intervalo histórico",
                   faixa=(round(est.intervalo_historico[0] * 100, 2),
                          round(est.intervalo_historico[1] * 100, 2)),
                   unidade="%", observacao="dispersão observada, não previsão")
        if est.intervalo_historico is not None else
        ausente("Intervalo histórico", "amostra pequena demais para intervalo"))

    if est.publicavel and est.faixa is not None:
        valores.append(estimativa(
            "Impacto atual estimado",
            faixa=(round(est.faixa[0] * 100, 2), round(est.faixa[1] * 100, 2)),
            unidade="%", confianca=est.confianca, horizonte=horiz,
            observacao=("estimativa experimental" if est.experimental else "")))
    else:
        valores.append(ausente(
            "Impacto atual estimado",
            "a estimativa não atingiu o mínimo para ser publicada"))

    valores.append(fato("Horizonte", horiz) if horiz else
                   ausente("Horizonte", "não determinado"))
    valores.append(
        fato("Tempo histórico de recuperação", round(float(tempo_recuperacao), 1),
             unidade=" pregões")
        if tempo_recuperacao is not None else
        ausente("Tempo histórico de recuperação",
                "não medido: exige série pós-evento completa"))
    valores.append(fato("Confiança", est.confianca))

    limitacoes = list(est.limitacoes)
    if est.n_amostra < 5:
        limitacoes.append(
            f"amostra de {est.n_amostra} evento(s): não sustenta inferência; "
            "trate como referência qualitativa")
    if est.experimental:
        limitacoes.append("estimativa marcada como experimental pelo motor")
    if est.condicoes_invalidam:
        limitacoes.append("condições que invalidam a análise: "
                          + "; ".join(est.condicoes_invalidam))

    return Bloco(
        titulo="Memória de mercado", valores=tuple(valores), frescor=frescor,
        limitacoes=tuple(limitacoes),
        explicacao_simples=(
            "Como o mercado reagiu a eventos parecidos no passado. "
            "Comportamento passado não garante o futuro."),
        detalhe_tecnico=tuple(est.fatores_ampliam) + tuple(est.fatores_reduzem))


# ── Fundamentos + Cenário (por empresa) ──────────────────────────────────────
def situacao_de(decisao) -> str:
    acoes = set(decisao.acoes)
    for acao, situacao in _ACAO_PARA_SITUACAO:
        if acao in acoes:
            return situacao
    return SIT_NORMAL


@dataclass(frozen=True)
class BlocoEmpresa:
    """"Fundamentos + Cenário" de um ativo, pronto para render e para a LLM."""

    simbolo: str
    situacao: str
    bloco: Bloco
    mudou: bool
    o_que_mudou: tuple[str, ...] = ()
    evidencias: tuple[str, ...] = ()
    invalidariam: tuple[str, ...] = ()
    noticias: tuple[ItemNoticia, ...] = ()

    @property
    def aparencia(self) -> dict[str, str]:
        return APARENCIA_SITUACAO[self.situacao]

    def numeros(self) -> tuple[float, ...]:
        saida = list(self.bloco.numeros())
        for n in self.noticias:
            saida.extend(n.numeros())
        return tuple(saida)


def bloco_empresa(decisao, *, anterior=None, est=None,
                  noticias: tuple[ItemNoticia, ...] = (),
                  frescor: Frescor | None = None,
                  combinar_scores: bool = False) -> BlocoEmpresa:
    """Monta a seção por empresa.

    ``combinar_scores`` fica desligado por padrão de propósito. As duas escalas
    são diferentes (0..100 e −100..+100) justamente para que somá-las pareça
    errado à primeira vista; o requisito pede o score final "caso a arquitetura
    use score combinado", e esta não usa.
    """
    est_v = decisao.score_estrutural
    conj_v = decisao.score_conjuntural

    valores: list[Valor] = []
    valores.append(fato("Score estrutural", round(float(est_v), 1), unidade="/100",
                        observacao="o que forma a carteira")
                   if est_v is not None else
                   ausente("Score estrutural", "cobertura de fundamentos abaixo "
                           "do mínimo"))
    valores.append(fato("Score conjuntural", round(float(conj_v), 1),
                        unidade=" (−100 a +100)",
                        observacao="ajuste de momento; não forma carteira")
                   if conj_v is not None else
                   ausente("Score conjuntural", "sem evidência conjuntural "
                           "suficiente"))

    ajuste = None
    if anterior is not None and anterior.fator_prioridade is not None:
        ajuste = float(decisao.fator_prioridade) - float(anterior.fator_prioridade)
        valores.append(fato("Ajuste causado por eventos", round(ajuste, 3),
                            observacao="variação no fator de prioridade de aporte"))
    else:
        valores.append(ausente("Ajuste causado por eventos",
                               "primeira avaliação: não há base de comparação"))

    if combinar_scores and est_v is not None and conj_v is not None:
        valores.append(fato("Score final combinado",
                            round(0.7 * float(est_v) + 0.3 * float(conj_v), 1)))
    else:
        valores.append(ausente(
            "Score final combinado",
            "esta arquitetura não combina os dois scores: as escalas são "
            "diferentes e a conjuntura não forma carteira"))

    valores.append(
        fato("Prioridade anterior de aporte", round(float(anterior.fator_prioridade), 3))
        if anterior is not None else
        ausente("Prioridade anterior de aporte", "primeira avaliação deste ativo"))
    valores.append(fato("Prioridade atual de aporte",
                        round(float(decisao.fator_prioridade), 3),
                        observacao=("aporte bloqueado" if decisao.bloqueia_aporte
                                    else "aporte liberado")))
    valores.append(fato("Confiança da avaliação", decisao.confianca))

    if est is not None and est.publicavel and est.faixa is not None:
        valores.append(estimativa(
            "Impacto estimado do cenário",
            faixa=(round(est.faixa[0] * 100, 2), round(est.faixa[1] * 100, 2)),
            unidade="%", confianca=est.confianca,
            horizonte=(f"{est.horizonte[0]} a {est.horizonte[1]} pregões"
                       if est.horizonte else None)))
    else:
        valores.append(ausente("Impacto estimado do cenário",
                               "sem estimativa publicável para este ativo"))

    situacao = situacao_de(decisao)
    mudou, o_que_mudou = _diferenca(decisao, anterior, situacao)

    invalidariam = tuple(est.condicoes_invalidam) if est is not None else ()
    if not invalidariam:
        invalidariam = ("Confirmação oficial que contradiga o relato.",
                        "Mudança de fundamento estrutural do ativo.",
                        "Divergência entre a notícia e o preço observado.")

    evidencias = tuple(f"notícia: {n.titulo} ({n.carimbo})" for n in noticias[:5])
    if decisao.motivo:
        evidencias = (f"motor de scores: {decisao.motivo}",) + evidencias

    bloco = Bloco(
        titulo=f"Fundamentos + Cenário — {decisao.simbolo or '?'}",
        valores=tuple(valores), frescor=frescor,
        limitacoes=tuple(decisao.limitacoes),
        explicacao_simples=_explicacao_simples(situacao, decisao),
        detalhe_tecnico=tuple(f"ação: {a}" for a in decisao.acoes))

    return BlocoEmpresa(simbolo=decisao.simbolo or "?", situacao=situacao,
                        bloco=bloco, mudou=mudou, o_que_mudou=o_que_mudou,
                        evidencias=evidencias, invalidariam=invalidariam,
                        noticias=tuple(noticias))


def _diferenca(decisao, anterior, situacao: str) -> tuple[bool, tuple[str, ...]]:
    if anterior is None:
        return False, ("Primeira avaliação registrada deste ativo.",)
    mudancas: list[str] = []
    if situacao_de(anterior) != situacao:
        mudancas.append(
            f"Situação: {APARENCIA_SITUACAO[situacao_de(anterior)]['rotulo']} "
            f"→ {APARENCIA_SITUACAO[situacao]['rotulo']}.")
    if set(anterior.acoes) != set(decisao.acoes):
        mudancas.append(f"Ações: {', '.join(anterior.acoes)} → "
                        f"{', '.join(decisao.acoes)}.")
    delta = float(decisao.fator_prioridade) - float(anterior.fator_prioridade)
    if abs(delta) >= 0.01:
        mudancas.append(f"Prioridade de aporte: {anterior.fator_prioridade:.3f} "
                        f"→ {decisao.fator_prioridade:.3f} ({delta:+.3f}).")
    if anterior.bloqueia_aporte != decisao.bloqueia_aporte:
        mudancas.append("Aporte passou a ser bloqueado." if decisao.bloqueia_aporte
                        else "Aporte foi desbloqueado.")
    if not mudancas:
        return False, ("Nada mudou desde a última avaliação.",)
    return True, tuple(mudancas)


def _explicacao_simples(situacao: str, decisao) -> str:
    base = {
        SIT_NORMAL: "Nada no cenário atual muda o plano para este ativo.",
        SIT_OBSERVACAO: "Há algo acontecendo que merece acompanhamento, mas "
                        "nada que exija ação agora.",
        SIT_REVISAO: "Um evento pode ter mexido nos fundamentos: vale reler a "
                     "tese antes de aportar mais.",
        SIT_SUSPENSAO: "Novos aportes neste ativo estão suspensos até o cenário "
                       "ficar mais claro. Nenhuma venda foi sugerida.",
    }[situacao]
    return base + " Isto não é garantia de retorno nem recomendação de compra."


# ── O painel completo ────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Painel:
    """Tudo que a área de Inteligência de Mercado mostra, numa estrutura só."""

    gerado_em: dt.datetime
    noticias: tuple[ItemNoticia, ...] = ()
    crise: Bloco | None = None
    antifragilidade: Bloco | None = None
    memoria: Bloco | None = None
    empresas: tuple[BlocoEmpresa, ...] = ()
    frescor: tuple[Frescor, ...] = ()
    provedores: tuple[Provedor, ...] = ()
    limitacoes: tuple[str, ...] = ()
    versao: str = PAINEL_VERSAO

    @property
    def blocos(self) -> tuple[Bloco, ...]:
        return tuple(b for b in (self.crise, self.antifragilidade, self.memoria)
                     if b is not None)

    @property
    def desatualizados(self) -> tuple[Frescor, ...]:
        """O que a tela é obrigada a destacar."""
        return tuple(f for f in self.frescor if f.a_destacar(self.gerado_em))

    @property
    def provedores_fora(self) -> tuple[Provedor, ...]:
        return tuple(p for p in self.provedores if not p.disponivel)

    @property
    def ultima_atualizacao(self) -> dt.datetime | None:
        """A mais ANTIGA das atualizações disponíveis.

        Publicar a mais recente faria o painel parecer atual enquanto metade
        dele está velha. Se uma fonte é de ontem, o painel é de ontem.
        """
        carimbos = [qz._aware(f.atualizado_em) for f in self.frescor
                    if f.disponivel and f.atualizado_em is not None]
        return min(carimbos) if carimbos else None

    def numeros(self) -> tuple[float, ...]:
        """Todo número que a LLM está autorizada a citar."""
        saida: list[float] = []
        for b in self.blocos:
            saida.extend(b.numeros())
        for e in self.empresas:
            saida.extend(e.numeros())
        for n in self.noticias:
            saida.extend(n.numeros())
        return tuple(saida)

    def empresa(self, simbolo: str) -> BlocoEmpresa | None:
        alvo = str(simbolo).strip().upper()
        for e in self.empresas:
            if e.simbolo.upper() == alvo:
                return e
        return None


def montar(*, veredito=None, indice=None, est=None, decisoes=(), anteriores=None,
           noticias=(), exposicao=None, provedores=(), frescor=(),
           evento_atual: str = "", evento_mais_similar: str = "",
           tempo_recuperacao: float | None = None, agora=None,
           combinar_scores: bool = False) -> Painel:
    """Junta o que os motores produziram num objeto só.

    Todo parâmetro é opcional porque toda fonte pode falhar, e o painel tem de
    continuar renderizável -- dizendo o que faltou -- em vez de levantar exceção
    no meio da tela.
    """
    quando = _agora(agora)
    anteriores = dict(anteriores or {})
    frescores = tuple(frescor)
    por_chave = {f.rotulo: f for f in frescores}

    itens = tuple(noticias)
    blocos_empresa = tuple(
        bloco_empresa(d, anterior=anteriores.get(d.simbolo), est=est,
                      noticias=filtrar(itens, ticker=d.simbolo) if d.simbolo else (),
                      frescor=por_chave.get("Notícias"),
                      combinar_scores=combinar_scores)
        for d in decisoes)

    evento_destaque = itens[0] if itens else None

    limitacoes: list[str] = []
    fora = [p.nome for p in provedores if not p.disponivel]
    if fora:
        limitacoes.append(
            f"provedor(es) indisponível(is): {', '.join(sorted(fora))}. "
            "A ausência de notícias pode ser falha de coleta, não calmaria.")
    for f in frescores:
        if f.a_destacar(quando):
            limitacoes.append(f.descrever(quando))

    return Painel(
        gerado_em=quando, noticias=itens,
        crise=bloco_crise(veredito, exposicao=exposicao, evento=evento_destaque,
                          frescor=por_chave.get("Crise")),
        antifragilidade=bloco_antifragilidade(
            indice, frescor=por_chave.get("Carteira")),
        memoria=bloco_memoria(est, evento_atual=evento_atual,
                              evento_mais_similar=evento_mais_similar,
                              tempo_recuperacao=tempo_recuperacao,
                              frescor=por_chave.get("Memória de mercado")),
        empresas=blocos_empresa, frescor=frescores,
        provedores=tuple(provedores), limitacoes=tuple(limitacoes))
