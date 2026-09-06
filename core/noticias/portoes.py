"""Os seis portões que uma notícia precisa atravessar para tocar em aporte.

Nota alta **não** é passe livre. O requisito é textual: "uma notícia com nota
superior a 80 não poderá, sozinha, alterar definitivamente a carteira". Aqui
isso vira código em três camadas:

1. A nota só abre a conversa. Abaixo de 80 nem se avaliam os portões.
2. Os seis critérios são conjuntivos. Um reprovado derruba tudo.
3. Mesmo com os seis aprovados, a saída é ``ACAO_SUGERIR_REVISAO`` -- uma
   sugestão que exige confirmação humana explícita. Nenhum caminho deste
   módulo produz ordem, peso ou rebalanceamento.

``satisfeito`` é ternário de propósito: ``True``, ``False`` e ``None``. ``None``
é "não consegui verificar", e **não satisfaz o portão** -- mas aparece com essa
palavra na evidência, para que ninguém confunda "o critério falhou" com "o dado
para checar o critério não existe". Este projeto já teve um portão que só podia
dar ``False``, e ninguém percebeu enquanto a causa não mudou.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.noticias import relevancia as rel
from core.noticias import taxonomia
from core.noticias.modelos import NoticiaAvaliada

ACAO_INFORMAR = "informar"
ACAO_OBSERVAR = "observar"
ACAO_SUGERIR_REVISAO = "sugerir_revisao"

PORTAO_CONFIRMACAO = "confirmacao"
PORTAO_RELACAO = "relacao"
PORTAO_PERSISTENCIA = "persistencia"
PORTAO_FUNDAMENTO = "fundamento"
PORTAO_QUANTITATIVO = "quantitativo"
PORTAO_CARTEIRA = "carteira"

ROTULO_PORTAO = {
    PORTAO_CONFIRMACAO: "Fonte primaria ou confirmacao independente",
    PORTAO_RELACAO: "Relacao direta com ativo, setor ou macro",
    PORTAO_PERSISTENCIA: "Possibilidade de efeito persistente",
    PORTAO_FUNDAMENTO: "Altera ou ameaca algum fundamento",
    PORTAO_QUANTITATIVO: "Confirmado pelos indicadores quantitativos",
    PORTAO_CARTEIRA: "Respeita perfil, horizonte e limites da carteira",
}

#: Piso de persistência para o efeito ser considerado possivelmente duradouro.
PISO_PERSISTENCIA = 0.50

#: Piso de relação direta (0,35 é o patamar de "macro relacionado").
PISO_RELACAO = 0.35

# Tipos de evento que mexem com fundamento por definição. Um resultado
# trimestral muda números do balanço; uma oscilação de câmbio, por si só, não.
TIPOS_DE_FUNDAMENTO = frozenset({
    "resultado_trimestral", "resultado_anual", "guidance", "dividendo",
    "divida_rating",
    "fusao_aquisicao", "emissao_capital", "recuperacao_judicial",
    "fraude_governanca", "litigio_regulatorio", "regulacao_setorial",
    "vacancia_locacao", "deslistagem", "mudanca_gestao", "concorrencia",
    "operacional",
})

#: Horizonte do evento em meses, para confrontar com o horizonte do investidor.
MESES_POR_HORIZONTE = {
    taxonomia.HORIZONTE_INTRADIA: 0,
    taxonomia.HORIZONTE_CURTO: 1,
    taxonomia.HORIZONTE_MEDIO: 6,
    taxonomia.HORIZONTE_LONGO: 24,
}


@dataclass(frozen=True)
class Perfil:
    """O que a carteira do usuário impõe. Vazio é um estado legítimo."""

    horizonte_meses: int | None = None
    limite_por_ativo: float | None = None
    exposicao_por_ativo: dict[str, float] = field(default_factory=dict)
    tickers: tuple[str, ...] = ()

    @property
    def vazio(self) -> bool:
        return (not self.tickers and not self.exposicao_por_ativo
                and self.horizonte_meses is None)


PERFIL_VAZIO = Perfil()


@dataclass(frozen=True)
class Portao:
    chave: str
    satisfeito: bool | None
    evidencia: str

    @property
    def rotulo(self) -> str:
        return ROTULO_PORTAO.get(self.chave, self.chave)

    @property
    def aprovado(self) -> bool:
        """Só ``True`` aprova. ``None`` não é aprovação nem por omissão."""
        return self.satisfeito is True


@dataclass(frozen=True)
class Veredito:
    """Resultado da avaliação. Nunca uma ordem, sempre uma proposta."""

    acao: str
    nota: float
    faixa: str
    portoes: tuple[Portao, ...] = ()
    limitacoes: tuple[str, ...] = ()

    #: Invariante do módulo, não configuração. Nenhum caminho a altera.
    exige_confirmacao_humana: bool = True

    @property
    def aprovados(self) -> tuple[Portao, ...]:
        return tuple(p for p in self.portoes if p.satisfeito is True)

    @property
    def reprovados(self) -> tuple[Portao, ...]:
        return tuple(p for p in self.portoes if p.satisfeito is False)

    @property
    def indeterminados(self) -> tuple[Portao, ...]:
        return tuple(p for p in self.portoes if p.satisfeito is None)

    @property
    def libera_revisao(self) -> bool:
        return self.acao == ACAO_SUGERIR_REVISAO

    @property
    def altera_carteira_automaticamente(self) -> bool:
        """Sempre ``False``.

        Existe para ser lida e testada, não para variar. Uma notícia isolada
        não pode gerar ordem nem alterar carteira, com nota nenhuma.
        """
        return False

    def motivo(self) -> str:
        if self.acao == ACAO_SUGERIR_REVISAO:
            return ("os seis criterios foram satisfeitos: sugerir revisao "
                    "estrategica ao usuario, que decide")
        pendentes = self.reprovados + self.indeterminados
        if pendentes:
            faltas = "; ".join(f"{p.rotulo}: {p.evidencia}" for p in pendentes)
            return f"nao atravessou {len(pendentes)} criterio(s) -- {faltas}"
        return "nota abaixo da faixa de revisao estrategica"


def _portao_confirmacao(avaliada: NoticiaAvaliada) -> Portao:
    if avaliada.confirmado_por_primaria:
        return Portao(PORTAO_CONFIRMACAO, True,
                      "publicada por fonte primaria ou reguladora")
    if avaliada.n_fontes_independentes >= 2:
        return Portao(
            PORTAO_CONFIRMACAO, True,
            f"{avaliada.n_fontes_independentes} veiculos independentes "
            "com apuracao propria")
    return Portao(PORTAO_CONFIRMACAO, False,
                  "fonte unica, sem confirmacao independente")


def _portao_relacao(avaliada: NoticiaAvaliada) -> Portao:
    valor = avaliada.relevancia.componentes.get(rel.RELACAO_ATIVO)
    if valor is None:
        return Portao(PORTAO_RELACAO, None,
                      "nenhuma entidade identificada: relacao nao verificavel")
    if valor >= PISO_RELACAO:
        return Portao(PORTAO_RELACAO, True,
                      f"relacao medida em {valor:.2f} (piso {PISO_RELACAO:.2f})")
    return Portao(PORTAO_RELACAO, False,
                  f"relacao medida em {valor:.2f}, abaixo do piso "
                  f"{PISO_RELACAO:.2f}")


def _portao_persistencia(avaliada: NoticiaAvaliada) -> Portao:
    tipo = avaliada.noticia.tipo
    if tipo.chave == taxonomia.TIPO_INDEFINIDO.chave:
        return Portao(PORTAO_PERSISTENCIA, None,
                      "tipo de evento indefinido: persistencia nao estimavel")
    if tipo.persistencia >= PISO_PERSISTENCIA:
        return Portao(PORTAO_PERSISTENCIA, True,
                      f"{tipo.rotulo}: persistencia tipica "
                      f"{tipo.persistencia:.2f}")
    return Portao(PORTAO_PERSISTENCIA, False,
                  f"{tipo.rotulo}: persistencia tipica "
                  f"{tipo.persistencia:.2f}, efeito tende a se dissipar")


def _portao_fundamento(avaliada: NoticiaAvaliada,
                       fundamentos_afetados: tuple[str, ...]) -> Portao:
    if fundamentos_afetados:
        return Portao(PORTAO_FUNDAMENTO, True,
                      "fundamentos apontados: " + ", ".join(fundamentos_afetados))
    chave = avaliada.noticia.tipo_evento
    if chave in TIPOS_DE_FUNDAMENTO:
        return Portao(PORTAO_FUNDAMENTO, True,
                      f"{avaliada.noticia.tipo.rotulo} incide sobre fundamento")
    if chave == taxonomia.TIPO_INDEFINIDO.chave:
        return Portao(PORTAO_FUNDAMENTO, None,
                      "tipo de evento indefinido: efeito sobre fundamento "
                      "nao verificavel")
    return Portao(PORTAO_FUNDAMENTO, False,
                  f"{avaliada.noticia.tipo.rotulo} nao altera fundamento "
                  "do ativo")


def _portao_quantitativo(confirmacao: bool | None) -> Portao:
    if confirmacao is True:
        return Portao(PORTAO_QUANTITATIVO, True,
                      "indicadores quantitativos disponiveis corroboram")
    if confirmacao is False:
        return Portao(PORTAO_QUANTITATIVO, False,
                      "indicadores quantitativos disponiveis nao corroboram")
    return Portao(PORTAO_QUANTITATIVO, None,
                  "nenhum indicador quantitativo disponivel para conferir")


def _portao_carteira(avaliada: NoticiaAvaliada, perfil: Perfil) -> Portao:
    if perfil.vazio:
        return Portao(PORTAO_CARTEIRA, None,
                      "sem carteira cadastrada: perfil, horizonte e limites "
                      "nao verificaveis")

    horizonte = avaliada.noticia.tipo.horizonte
    meses_evento = MESES_POR_HORIZONTE.get(horizonte)
    if (perfil.horizonte_meses is not None and meses_evento is not None
            and perfil.horizonte_meses >= 12 and meses_evento <= 0):
        return Portao(PORTAO_CARTEIRA, False,
                      f"evento de horizonte {horizonte} contra investidor de "
                      f"{perfil.horizonte_meses} meses")

    if perfil.limite_por_ativo is not None:
        estourados = [
            t for t in avaliada.noticia.entidades.tickers
            if perfil.exposicao_por_ativo.get(t, 0.0) >= perfil.limite_por_ativo
        ]
        if estourados:
            return Portao(PORTAO_CARTEIRA, False,
                          "ja no limite de exposicao: " + ", ".join(estourados))

    tickers = avaliada.noticia.entidades.tickers
    if perfil.tickers and tickers and not set(tickers) & set(perfil.tickers):
        return Portao(PORTAO_CARTEIRA, False,
                      "nenhum ativo da noticia esta na carteira")

    return Portao(PORTAO_CARTEIRA, True,
                  "dentro do perfil, do horizonte e dos limites declarados")


def avaliar(
    avaliada: NoticiaAvaliada,
    *,
    perfil: Perfil = PERFIL_VAZIO,
    fundamentos_afetados: tuple[str, ...] = (),
    confirmacao_quantitativa: bool | None = None,
) -> Veredito:
    """Passa uma notícia avaliada pelos seis portões.

    ``confirmacao_quantitativa`` vem de fora -- dos motores de score que já
    existem no APP4. ``None`` significa que nenhum indicador estava disponível
    para conferir, e nesse caso o portão não é dado por satisfeito. Preencher a
    ausência com um valor otimista seria o modo de falha do *fallback que só
    preenche lacuna e nunca contradiz*: regra certa, entrada errada, aprovação
    confiante.
    """
    portoes = (
        _portao_confirmacao(avaliada),
        _portao_relacao(avaliada),
        _portao_persistencia(avaliada),
        _portao_fundamento(avaliada, tuple(fundamentos_afetados or ())),
        _portao_quantitativo(confirmacao_quantitativa),
        _portao_carteira(avaliada, perfil),
    )

    faixa = avaliada.faixa
    limitacoes = list(avaliada.relevancia.limitacoes)

    if faixa != taxonomia.FAIXA_REVISAO:
        acao = (ACAO_OBSERVAR if faixa == taxonomia.FAIXA_OBSERVACAO
                else ACAO_INFORMAR)
    elif all(p.aprovado for p in portoes):
        acao = ACAO_SUGERIR_REVISAO
    else:
        # Nota >= 80 sem os seis critérios não vira sugestão de aporte; vira
        # observação. É exatamente a trava pedida no requisito.
        acao = ACAO_OBSERVAR
        limitacoes.append(
            "nota na faixa de revisao, mas os criterios de aporte nao foram "
            "todos satisfeitos: mantida em observacao")

    return Veredito(
        acao=acao,
        nota=avaliada.nota,
        faixa=faixa,
        portoes=portoes,
        limitacoes=tuple(limitacoes),
    )
