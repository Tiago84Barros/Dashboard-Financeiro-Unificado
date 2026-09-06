"""Orquestração da coleta: provedores, deduplicação, eventos e avaliação.

É o único módulo que junta todas as peças, e é onde as regras de degradação
ficam visíveis:

* **Falha parcial não derruba a coleta.** Um provedor fora do ar entra em
  ``falhas`` e os outros seguem. O que ele deixou de trazer vira limitação
  escrita, não um silêncio.
* **Falha não vira sucesso.** ``RegistroColeta.registrar_sucesso`` só é chamado
  quando houve resposta de rede válida. Cache vencido é resultado degradado e
  rotulado, e não atualiza o carimbo de última coleta bem-sucedida.
* **Zero itens não é o mesmo que fonte indisponível.** ``ResultadoColeta``
  distingue "consultei e não havia nada" de "não consegui consultar", porque a
  tela precisa dizer coisas diferentes nos dois casos.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.noticias import dedup, fontes, normalizacao, rate_limit, taxonomia
from core.noticias import entidades as ent_mod
from core.noticias import eventos as ev_mod
from core.noticias import impacto as imp_mod
from core.noticias import portoes as pt_mod
from core.noticias import relevancia as rel_mod
from core.noticias import sentimento as sent_mod
from core.noticias.entidades import UNIVERSO_VAZIO, Universo
from core.noticias.frescor_noticias import RegistroColeta
from core.noticias.modelos import Noticia, NoticiaAvaliada
from core.noticias.portoes import PERFIL_VAZIO, Perfil, Veredito
from core.noticias.provedores.base import (
    ORIGEM_CACHE_VENCIDO,
    Consulta,
    ItemBruto,
    ProvedorIndisponivel,
    RespostaInvalida,
)
from core.noticias.rate_limit import LimiteExcedido
from core.noticias.transporte import ErroTransporte

logger = logging.getLogger(__name__)

FALHA_INDISPONIVEL = "indisponivel"
FALHA_LIMITE = "limite"
#: Cota ainda tem saldo; o provedor foi pulado pelo piso de espaçamento
#: (``rate_limit.Limite.intervalo_minimo_s``) para o teto diário cobrir as 24h.
#: Separado de :data:`FALHA_LIMITE` de propósito: quem lê a limitação na tela
#: precisa distinguir "acabou" de "esta sendo racionado para durar" -- são a
#: mesma decisão para o coletor e leituras opostas para quem interpreta.
FALHA_ESPACAMENTO = "espacamento"
FALHA_INVALIDA = "resposta_invalida"
FALHA_REDE = "rede"

ROTULO_FALHA = {
    FALHA_INDISPONIVEL: "fonte indisponivel",
    FALHA_LIMITE: "limite de requisicoes atingido",
    FALHA_ESPACAMENTO: ("espacado para a cota diaria cobrir as 24h "
                        "(ainda ha saldo)"),
    FALHA_INVALIDA: "resposta invalida da fonte",
    FALHA_REDE: "falha de comunicacao com a fonte",
}


def _agora_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class FalhaProvedor:
    provedor: str
    tipo: str
    motivo: str
    usou_cache_vencido: bool = False

    @property
    def rotulo(self) -> str:
        return ROTULO_FALHA.get(self.tipo, self.tipo)

    def texto(self) -> str:
        base = f"{self.provedor}: {self.rotulo}"
        if self.usou_cache_vencido:
            base += " (exibindo ultima coleta guardada, fora do prazo)"
        return base


@dataclass(frozen=True)
class ResultadoColeta:
    """O que a coleta produziu, com a procedência de cada parte."""

    avaliadas: tuple[NoticiaAvaliada, ...] = ()
    #: Veredito dos seis portões por ``noticia.id_dedup``. Até 05/09/2026 o
    #: motor de portões não tinha chamador em produção (A-140): ele existia,
    #: passava nos testes, e nenhuma coleta o consultava. Motor de análise que
    #: não é consultado na decisão é decoração.
    vereditos: dict[str, Veredito] = field(default_factory=dict)
    eventos: tuple[ev_mod.Evento, ...] = ()
    provedores_consultados: tuple[str, ...] = ()
    provedores_ok: tuple[str, ...] = ()
    falhas: tuple[FalhaProvedor, ...] = ()
    origens: dict[str, str] = field(default_factory=dict)
    limitacoes: tuple[str, ...] = ()
    itens_brutos: int = 0
    coletado_em: datetime | None = None

    @property
    def degradado(self) -> bool:
        return bool(self.falhas)

    @property
    def sem_fonte(self) -> bool:
        """Nenhum provedor respondeu. Diferente de "nenhuma noticia"."""
        return bool(self.provedores_consultados) and not self.provedores_ok

    @property
    def duplicatas_removidas(self) -> int:
        return max(0, self.itens_brutos - len(self.avaliadas))

    def por_faixa(self, faixa: str) -> tuple[NoticiaAvaliada, ...]:
        return tuple(a for a in self.avaliadas if a.faixa == faixa)


def para_noticia(item: ItemBruto, provedor: str, *,
                 universo: Universo = UNIVERSO_VAZIO,
                 coletado_em: datetime | None = None) -> Noticia:
    """Converte o item cru do provedor no registro canônico do APP4.

    Tudo o que o provedor não informou permanece ausente. Nenhum campo é
    preenchido por conveniência: ausência de autor é ``None``, não string
    vazia, e ausência de data é ``None``, não a hora da coleta -- carimbar a
    coleta como publicação transformaria matéria antiga em matéria de agora.
    """
    canonica = normalizacao.url_canonica(item.url)
    titulo = normalizacao.limpar_html(item.titulo) or ""
    # O corte do rodape mora aqui, e nao no provedor de RSS onde nasceu:
    # este e o funil unico por onde todo provedor passa, e a assinatura de
    # plugin ("The post X appeared first on Y") chega por agregador tambem.
    # Sai antes de ``hash_conteudo`` e ``simhash`` de proposito -- rodape
    # identico em materias diferentes as aproximaria sem que o texto se
    # parecesse.
    resumo = normalizacao.sem_rodape_de_feed(
        normalizacao.limpar_html(item.resumo)) or None
    publicado = normalizacao.para_utc(item.publicado_em)

    fonte = fontes.classificar(item.url, item.veiculo)
    idioma = item.idioma or normalizacao.detectar_idioma(f"{titulo} {resumo or ''}")

    entidades = ent_mod.resolver(
        titulo, resumo,
        tickers_declarados=item.tickers,
        empresas_declaradas=item.empresas,
        # O país do VEÍCULO não entra: ele é procedência, não entidade do
        # fato. Enquanto entrava, uma matéria da Reuters sobre o Brasil saía
        # com ``paises=("GB",)`` e a mesma matéria no Valor com ``("BR",)`` --
        # e o agrupamento por evento, que usa país como chave de último
        # recurso, separava o mesmo fato pela nacionalidade de quem publicou
        # (A-145). O efeito não parava ali: ``exposicao`` lia o país do
        # veículo como exposição da carteira e ``relevancia`` premiava a
        # matéria por um vínculo macro que ela não tinha. A procedência
        # continua registrada em ``Noticia.pais``, que é o lugar dela.
        pais_declarado=item.pais,
        universo=universo,
    )

    return Noticia(
        id_dedup=dedup.hash_url(canonica or item.url or titulo),
        hash_conteudo=dedup.hash_conteudo(titulo, resumo),
        simhash=dedup.simhash(f"{titulo} {resumo or ''}"),
        titulo=titulo,
        resumo=resumo,
        url=item.url,
        url_canonica=canonica,
        fonte=fonte,
        autor=item.autor,
        publicado_em=publicado,
        coletado_em=coletado_em or _agora_utc(),
        provedor=provedor,
        idioma=idioma,
        pais=item.pais or (fonte.pais if fonte else None),
        entidades=entidades,
        tipo_evento=ev_mod.classificar(titulo, resumo, item.categorias),
        sentimento=sent_mod.avaliar(
            titulo, resumo, idioma=idioma,
            sentimento_api=item.sentimento_api,
            rotulo_api=item.rotulo_sentimento,
        ),
        bruto=dict(item.bruto or {}),
    )


def exposicao_de_carteira(noticia: Noticia, perfil: Perfil) -> float | None:
    """Fração da carteira exposta ao que a notícia toca.

    Pública -- e não ``_exposicao`` como nasceu -- porque a reavaliação do
    acervo precisa da mesma conta. Duas implementações da mesma fração seriam
    duas notas para a mesma notícia conforme quem a calculou.

    ``None`` sem carteira cadastrada. Devolver ``0.0`` faria toda notícia
    perder pontos por uma carteira que o usuário nunca informou.
    """
    if perfil.vazio or not perfil.exposicao_por_ativo:
        return None
    tickers = set(noticia.entidades.tickers)
    if not tickers:
        return 0.0  # medido: a carteira existe e não tem relação com a matéria
    return min(1.0, sum(peso for t, peso in perfil.exposicao_por_ativo.items()
                        if t in tickers))


#: Mínimo de ocorrências passadas para a base histórica valer como indicador.
#: Abaixo disso a probabilidade é ruído com três casas decimais, e o portão
#: continua em "não medido" -- que não aprova.
MIN_OBSERVACOES_QUANTITATIVO = 8

#: Acima deste valor a base histórica corrobora. É a fronteira do "mais provável
#: que não": em mais da metade das ocorrências passadas deste tipo de evento o
#: preço se moveu além do limiar de relevância.
PROB_CORROBORA = 0.5


def confirmacao_quantitativa(base) -> bool | None:
    """Traduz a base histórica na entrada do portão quantitativo.

    Devolve ``None`` -- e nunca ``False`` -- quando não há base, quando ela é
    pequena demais, ou quando a probabilidade não foi apurada. ``False`` é uma
    medição ("os indicadores disponíveis não corroboram"); ``None`` é a ausência
    dela. Confundir os dois é o modo de falha que o projeto já pagou caro: em
    média renormalizada ``None`` é neutro e ``0.0`` é punitivo.

    O portão continuava estruturalmente indeterminado (A-141) porque ninguém o
    preenchia. Preencher com otimismo seria pior que deixar vazio -- é o
    *fallback que só preenche lacuna e nunca contradiz*: regra certa, entrada
    errada, aprovação confiante. Por isso a entrada é uma medição de fora, com
    procedência declarada em ``base.fonte``, e não uma heurística local.
    """
    if base is None:
        return None
    n = getattr(base, "n_observacoes", 0) or 0
    if n < MIN_OBSERVACOES_QUANTITATIVO:
        return None
    prob = getattr(base, "prob_movimento_relevante", None)
    if prob is None:
        return None
    try:
        return float(prob) >= PROB_CORROBORA
    except (TypeError, ValueError):
        return None


def avaliar_evento(evento: ev_mod.Evento, *, agora: datetime | None = None,
                   perfil: Perfil = PERFIL_VAZIO,
                   pesos: rel_mod.Pesos = rel_mod.PESOS_PADRAO,
                   bases=None) -> list[NoticiaAvaliada]:
    """Avalia as matérias de um evento com o contexto do evento inteiro.

    A confirmação independente é do evento, não da matéria: é o evento que
    reúne veículos distintos. Avaliar matéria a matéria daria confirmação 1
    para cada uma e nenhuma passaria pelo portão -- ou, pior, contaria a
    replicação sindicalizada como confirmação.
    """
    referencia = agora or _agora_utc()
    bases = bases or {}
    base = bases.get(evento.tipo)
    saida: list[NoticiaAvaliada] = []

    for noticia in evento.noticias:
        relevancia = rel_mod.calcular(
            noticia,
            pesos=pesos,
            agora=referencia,
            n_fontes_independentes=evento.n_fontes_independentes,
            confirmado_por_primaria=evento.confirmado_por_primaria,
            primeiro_em=evento.primeiro_em,
            tickers_alvo=perfil.tickers,
            exposicao_carteira=exposicao_de_carteira(noticia, perfil),
        )
        impacto = imp_mod.estimar(
            tipo_evento=noticia.tipo_evento,
            sentimento=noticia.sentimento,
            confiabilidade_fonte=(noticia.fonte.confiabilidade
                                  if noticia.fonte else None),
            estado_verificacao=evento.estado_verificacao,
            cobertura_relevancia=relevancia.cobertura,
            base=base,
        )
        saida.append(NoticiaAvaliada(
            noticia=noticia,
            relevancia=relevancia,
            impacto=impacto,
            estado_verificacao=evento.estado_verificacao,
            n_fontes_independentes=evento.n_fontes_independentes,
            confirmado_por_primaria=evento.confirmado_por_primaria,
        ))
    return saida


def _classificar_erro(exc: Exception) -> str:
    if isinstance(exc, LimiteExcedido):
        if getattr(exc, "motivo", None) == rate_limit.MOTIVO_ESPACAMENTO:
            return FALHA_ESPACAMENTO
        return FALHA_LIMITE
    if isinstance(exc, ProvedorIndisponivel):
        return FALHA_INDISPONIVEL
    if isinstance(exc, RespostaInvalida):
        return FALHA_INVALIDA
    return FALHA_REDE


def coletar(
    consulta: Consulta,
    provedores,
    *,
    universo: Universo = UNIVERSO_VAZIO,
    perfil: Perfil = PERFIL_VAZIO,
    registro: RegistroColeta | None = None,
    pesos: rel_mod.Pesos = rel_mod.PESOS_PADRAO,
    bases=None,
    agora: datetime | None = None,
    janela_evento_h: float = ev_mod.JANELA_PADRAO_H,
    permitir_cache_vencido: bool = True,
) -> ResultadoColeta:
    """Consulta os provedores, deduplica, agrupa em eventos e avalia.

    Cada provedor é isolado: exceção dele não sai daqui. O motivo é o requisito
    de "falha parcial de provedores" -- com dois provedores configurados e um
    fora do ar, a coleta precisa entregar o que o outro trouxe **e** dizer o
    que faltou.
    """
    referencia = agora or _agora_utc()
    brutos: list[Noticia] = []
    ok: list[str] = []
    falhas: list[FalhaProvedor] = []
    origens: dict[str, str] = {}
    limitacoes: list[str] = []
    consultados: list[str] = []

    for provedor in provedores:
        nome = getattr(provedor, "nome", provedor.__class__.__name__)
        consultados.append(nome)

        try:
            resposta = provedor.buscar(consulta)
        except (ErroTransporte, ValueError) as exc:
            tipo = _classificar_erro(exc)
            # Mensagem do provedor, não a URL: a chave viaja na query string.
            logger.warning("Provedor %s falhou (%s)", nome, tipo)
            if registro is not None:
                registro.registrar_falha(nome, tipo, quando=referencia)

            resposta = None
            if permitir_cache_vencido:
                try:
                    resposta = provedor.do_cache_vencido(consulta)
                except Exception:  # pragma: no cover - cache nunca deve travar
                    resposta = None

            falhas.append(FalhaProvedor(
                provedor=nome, tipo=tipo, motivo=str(exc),
                usou_cache_vencido=resposta is not None,
            ))
            if resposta is None:
                continue
        else:
            if registro is not None:
                registro.registrar_sucesso(nome, itens=len(resposta.itens),
                                           quando=referencia)
            ok.append(nome)

        origens[nome] = resposta.origem
        limitacoes.extend(f"{nome}: {texto}" for texto in resposta.limitacoes)
        if resposta.origem == ORIGEM_CACHE_VENCIDO:
            limitacoes.append(
                f"{nome}: conteudo vindo de cache vencido, pode estar "
                "desatualizado")

        for item in resposta.itens:
            try:
                brutos.append(para_noticia(item, nome, universo=universo,
                                           coletado_em=referencia))
            except (TypeError, ValueError) as exc:
                logger.warning("Item descartado de %s: %s", nome, exc)

    clusters = dedup.agrupar_duplicatas(brutos)
    eventos = ev_mod.agrupar(clusters, janela_evento_h)

    avaliadas: list[NoticiaAvaliada] = []
    vereditos: dict[str, Veredito] = {}
    for evento in eventos:
        do_evento = avaliar_evento(evento, agora=referencia, perfil=perfil,
                                   pesos=pesos, bases=bases)
        # Os portões passam a rodar aqui, e não em lugar nenhum. A saída máxima
        # possível continua sendo ``sugerir_revisao``: nenhum veredito compra,
        # vende ou emite ordem. O que muda é que agora existe veredito.
        conf = confirmacao_quantitativa((bases or {}).get(evento.tipo))
        for avaliada in do_evento:
            vereditos[avaliada.noticia.id_dedup] = pt_mod.avaliar(
                avaliada, perfil=perfil, confirmacao_quantitativa=conf)
        avaliadas.extend(do_evento)

    # Ordem estável e útil: nota desce, e o desempate é pelo identificador, não
    # pela ordem de chegada dos provedores -- senão a mesma coleta produz telas
    # diferentes conforme qual API respondeu primeiro.
    avaliadas.sort(key=lambda a: (-a.nota, a.noticia.id_dedup))

    if perfil.vazio:
        limitacoes.append(
            "sem carteira cadastrada: exposicao da carteira nao entrou no "
            "indice de relevancia")
    if not ok and consultados:
        limitacoes.append(
            "nenhum provedor respondeu: a lista abaixo nao reflete o momento "
            "atual")

    return ResultadoColeta(
        avaliadas=tuple(avaliadas),
        vereditos=vereditos,
        eventos=tuple(eventos),
        provedores_consultados=tuple(consultados),
        provedores_ok=tuple(ok),
        falhas=tuple(falhas),
        origens=origens,
        limitacoes=tuple(dict.fromkeys(limitacoes)),
        itens_brutos=len(brutos),
        coletado_em=referencia,
    )


def destaques(resultado: ResultadoColeta,
              minimo: float = taxonomia.LIMITE_OBSERVACAO,
              limite: int = 20) -> tuple[NoticiaAvaliada, ...]:
    """As notícias que merecem aparecer primeiro, sem esconder o resto."""
    return tuple(a for a in resultado.avaliadas if a.nota >= minimo)[:limite]
