"""Registro canonico de uma noticia dentro do APP4.

Duas camadas propositalmente separadas:

``Noticia``  -- o que foi observado. Titulo, fonte, data, entidades, sentimento
              declarado pelo provedor. Nada aqui e opiniao do APP4.
``NoticiaAvaliada`` -- ``Noticia`` mais o que o APP4 concluiu: relevancia,
              impacto provavel, estado de verificacao. Reconstruivel a qualquer
              momento a partir da primeira camada.

A separacao existe porque as duas envelhecem de forma diferente. O fato
observado nao muda; a avaliacao muda quando a metodologia muda, e ai precisa ser
recalculada sem re-coletar. Guardar tudo num objeto so ja custou caro em outras
partes deste projeto (ver as safras PIT dos EUA): sem separar fato de conclusao,
subir a versao da metodologia obriga a jogar fora a evidencia.

Todo instante e ``datetime`` timezone-aware em UTC. A conversao para o fuso do
usuario acontece na view, nunca aqui.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from core.noticias import taxonomia
from core.noticias.fontes import Fonte

if TYPE_CHECKING:  # pragma: no cover - somente para tipagem
    from core.noticias.impacto import Impacto
    from core.noticias.relevancia import Relevancia


@dataclass(frozen=True)
class Sentimento:
    """Sentimento declarado pelo provedor e o recalculado pelo APP4.

    Os dois convivem de proposito. O provedor ve o texto em ingles com um modelo
    que nao conhecemos; o APP4 ve o mesmo texto com um lexico auditavel. Quando
    discordam, isso e informacao -- e nao um erro a ser silenciado escolhendo um
    dos dois.

    ``concordam`` e ``None`` quando qualquer um dos lados esta ausente. Nao e
    ``False``: "o provedor nao opinou" nao e "o provedor discordou".
    """

    valor_api: float | None = None      # -1..+1, como veio do provedor
    valor_app4: float | None = None     # -1..+1, recalculado localmente
    rotulo_api: str | None = None
    metodo_app4: str | None = None      # identificador do lexico/versao

    @property
    def concordam(self) -> bool | None:
        if self.valor_api is None or self.valor_app4 is None:
            return None
        # Concordancia e de sinal, nao de magnitude: escalas diferentes nao sao
        # comparaveis em modulo, mas a direcao e.
        if abs(self.valor_api) < 0.05 and abs(self.valor_app4) < 0.05:
            return True
        return (self.valor_api > 0) == (self.valor_app4 > 0)

    @property
    def valor(self) -> float | None:
        """O numero a exibir: o do APP4 quando existe, senao o do provedor."""
        return self.valor_app4 if self.valor_app4 is not None else self.valor_api


@dataclass(frozen=True)
class Entidades:
    """O que a noticia toca. Tudo em tuplas ordenadas, para dedup estavel."""

    tickers: tuple[str, ...] = ()
    empresas: tuple[str, ...] = ()
    setores: tuple[str, ...] = ()
    paises: tuple[str, ...] = ()
    moedas: tuple[str, ...] = ()
    ativos: tuple[str, ...] = ()        # commodities, indices, cripto

    @property
    def vazio(self) -> bool:
        return not (self.tickers or self.empresas or self.setores
                    or self.paises or self.moedas or self.ativos)


@dataclass(frozen=True)
class Noticia:
    """Um item de noticia normalizado. Fato observado, sem juizo do APP4."""

    # --- identidade / deduplicacao -------------------------------------
    id_dedup: str                       # sha256 da URL canonica
    hash_conteudo: str                  # sha256 de titulo+resumo normalizados
    simhash: int | None = None          # 64 bits, para quase-duplicata

    # --- conteudo -------------------------------------------------------
    titulo: str = ""
    resumo: str | None = None
    url: str = ""
    url_canonica: str = ""

    # --- proveniencia ---------------------------------------------------
    fonte: Fonte | None = None
    autor: str | None = None
    publicado_em: datetime | None = None   # UTC aware
    coletado_em: datetime | None = None    # UTC aware
    provedor: str = ""                     # qual adaptador trouxe
    idioma: str | None = None
    pais: str | None = None

    # --- o que a noticia toca -------------------------------------------
    entidades: Entidades = field(default_factory=Entidades)

    # --- evento ----------------------------------------------------------
    tipo_evento: str = taxonomia.TIPO_INDEFINIDO.chave
    evento_id: str | None = None        # preenchido pelo agrupador

    # --- sentimento -------------------------------------------------------
    sentimento: Sentimento = field(default_factory=Sentimento)

    # --- rastro do provedor ------------------------------------------------
    bruto: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def classe_fonte(self) -> str:
        return self.fonte.classe if self.fonte else "desconhecida"

    @property
    def confiabilidade(self) -> float:
        return self.fonte.confiabilidade if self.fonte else 0.20

    @property
    def tipo(self) -> taxonomia.TipoEvento:
        return taxonomia.tipo(self.tipo_evento)

    def idade_em_minutos(self, agora: datetime | None = None) -> float | None:
        """Idade em minutos, ou ``None`` quando a data de publicacao falta.

        Devolve ``None`` -- nunca ``0`` -- para data ausente, pela mesma razao
        que `core/frescor.py`: "nao sei quando saiu" nao pode ser lido como
        "acabou de sair". Uma noticia sem data que se apresentasse como recem
        publicada e exatamente o modo de falha que o usuario proibiu.
        """
        if self.publicado_em is None:
            return None
        ref = agora or datetime.now(timezone.utc)
        return (ref - self.publicado_em).total_seconds() / 60.0


@dataclass(frozen=True)
class NoticiaAvaliada:
    """``Noticia`` com o que o APP4 concluiu sobre ela.

    Os quatro numeros que o usuario exigiu manter separados moram em objetos
    distintos de proposito: `relevancia` (quanto merece atencao), `sentimento`
    (tom), e dentro de `impacto` a direcao, a magnitude, a probabilidade e a
    confianca. Nenhum deles e colapsado num "impacto de 72%".
    """

    noticia: Noticia
    relevancia: Relevancia
    impacto: Impacto
    estado_verificacao: str = taxonomia.VERIF_NAO_VERIFICADA
    n_fontes_independentes: int = 1
    confirmado_por_primaria: bool = False

    @property
    def faixa(self) -> str:
        return self.relevancia.faixa

    @property
    def nota(self) -> float:
        return self.relevancia.nota
