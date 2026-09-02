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

from core.noticias import dedup, fontes, normalizacao, taxonomia
from core.noticias import entidades as ent_mod
from core.noticias import eventos as ev_mod
from core.noticias import impacto as imp_mod
from core.noticias import relevancia as rel_mod
from core.noticias import sentimento as sent_mod
from core.noticias.entidades import UNIVERSO_VAZIO, Universo
from core.noticias.frescor_noticias import RegistroColeta
from core.noticias.modelos import Noticia, NoticiaAvaliada
from core.noticias.portoes import PERFIL_VAZIO, Perfil
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
FALHA_INVALIDA = "resposta_invalida"
FALHA_REDE = "rede"

ROTULO_FALHA = {
    FALHA_INDISPONIVEL: "fonte indisponivel",
    FALHA_LIMITE: "limite de requisicoes atingido",
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
    resumo = normalizacao.limpar_html(item.resumo)
    publicado = normalizacao.para_utc(item.publicado_em)

    fonte = fontes.classificar(item.url, item.veiculo)
    idioma = item.idioma or normalizacao.detectar_idioma(f"{titulo} {resumo or ''}")

    entidades = ent_mod.resolver(
        titulo, resumo,
        tickers_declarados=item.tickers,
        empresas_declaradas=item.empresas,
        pais_declarado=item.pais or (fonte.pais if fonte else None),
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


def _exposicao(noticia: Noticia, perfil: Perfil) -> float | None:
    """Fração da carteira exposta ao que a notícia toca.

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
            exposicao_carteira=_exposicao(noticia, perfil),
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
    for evento in eventos:
        avaliadas.extend(avaliar_evento(evento, agora=referencia, perfil=perfil,
                                        pesos=pesos, bases=bases))

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
