"""Índice de Relevância da Notícia, de 0 a 100.

Sete componentes, pesos configuráveis, e uma regra que decide o comportamento
do módulo inteiro: **componente não medido é ``None``, nunca ``0.0``**.

Isso obriga a renormalizar pelos pesos efetivamente medidos, e a renormalização
tem um efeito perverso conhecido -- quem foi medido em menos dimensões tira
nota mais alta, porque a média se calcula sobre menos exigências. Este projeto
já viveu exatamente isso: um dos motores marcava 100% de conformidade por fazer
uma pergunta a menos que os outros.

A defesa é dupla e está inteira aqui:

1. ``cobertura`` sai junto com a nota, sempre, e é a fração do peso total que
   foi efetivamente medida.
2. A faixa de revisão estratégica (>= 80) exige ``cobertura`` mínima. Uma
   notícia que tirou 92 medindo só três dos sete componentes não é candidata a
   revisão -- é uma notícia mal medida, e fica em observação com o motivo
   escrito.

Zero medido continua valendo zero: uma fonte de confiabilidade baixíssima
recebe nota baixa e isso é medição, não ausência.

**Teto de evidência (A-146, 05/09/2026).** Média ponderada tem um segundo modo
de falha, e ele é o oposto do primeiro: componentes altos *compensam* um déficit
eliminatório em outro. Medido antes da correção, com o motor real:

    fabricada  nota=77,8  (dominio desconhecido, fonte unica)
    pandemia   nota=78,3  (Reuters, tres fontes)
    guerra     nota=73,1  (Reuters, tres fontes)

*"URGENTE: Petrobras vai a falencia amanha, dizem fontes"*, publicada num
domínio que nunca vimos, empatava com a pandemia e ganhava da guerra. E não por
acaso: dos sete componentes, **cinco são declarados pela própria notícia**.
Quem a escreve escolhe o tipo de evento (materialidade 0,25 e persistência
0,10), cita um ticker da carteira (relação 0,20), publica agora (novidade 0,10)
e diz respeito a quanto do patrimônio quiser (exposição 0,10) -- 0,75 do peso
total sob controle de quem quer ser lido. Sobram 0,25 para confiabilidade e
confirmação, os dois únicos componentes que o autor **não** controla, e 0,25 de
peso não segura 0,75.

Este projeto já viveu a mesma forma em outro motor: um ativo marcava "Alta"
usando preço de 2015, porque os demais critérios compensavam a falta de preço
vivo (``memoria: media-ponderada-compensa-defeito-eliminatorio``). A lição foi
que evidência ausente não é um componente a mais na média -- é um **limite** ao
que a média pode afirmar.

Então a evidência externa vira teto, e não parcela: a nota é calculada como
sempre e depois **limitada** por ``TETO_BASE + (100 - TETO_BASE) * evidência``.
A nota bruta não some -- fica em ``nota_bruta`` e o rebaixamento aparece escrito
em ``limitacoes``, porque convenção não pode apagar o observado.

A propriedade que isso compra, e que é o ponto todo: **a faixa de revisão
estratégica passa a ser inalcançável sem evidência externa**. Chegar a 80 exige
evidência de 0,67, que nenhum domínio desconhecido sozinho alcança, escreva ele
o que escrever.

Não há detector de sensacionalismo aqui, e a ausência é deliberada. Classificar
vocabulário é uma corrida contra quem escolhe as palavras -- e o teto não
depende das palavras dele.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.noticias import taxonomia
from core.noticias.modelos import Noticia

#: Abaixo desta cobertura a notícia não pode ser promovida à faixa de revisão.
COBERTURA_MINIMA_REVISAO = 0.70

#: Piso do teto de evidência. Notícia sem nenhuma corroboração externa não é
#: zerada -- ela pode ser verdadeira, e o índice não julga veracidade. Ela fica
#: presa abaixo de ``LIMITE_OBSERVACAO`` (60), que é o que "informativa"
#: significa: registre, não decida por isso.
TETO_BASE = 40.0

MATERIALIDADE = "materialidade"
RELACAO_ATIVO = "relacao_ativo"
CONFIABILIDADE = "confiabilidade"
NOVIDADE = "novidade"
CONFIRMACAO = "confirmacao"
PERSISTENCIA = "persistencia"
EXPOSICAO = "exposicao"

#: Os únicos componentes que o autor da notícia não escolhe. Materialidade,
#: persistência e relação saem do texto que ele escreveu; novidade, do instante
#: em que ele publicou; exposição, do ticker que ele resolveu citar. Confiar em
#: quem publica e ter sido publicado por mais de um veículo, não.
COMPONENTES_DE_EVIDENCIA = (CONFIABILIDADE, CONFIRMACAO)

ROTULO_COMPONENTE = {
    MATERIALIDADE: "Materialidade financeira",
    RELACAO_ATIVO: "Relação direta com o ativo",
    CONFIABILIDADE: "Confiabilidade da fonte",
    NOVIDADE: "Novidade da informação",
    CONFIRMACAO: "Confirmação independente",
    PERSISTENCIA: "Persistência provável",
    EXPOSICAO: "Exposição da carteira",
}


@dataclass(frozen=True)
class Pesos:
    """Pesos dos componentes. Configuráveis, como o requisito pede."""

    materialidade: float = 0.25
    relacao_ativo: float = 0.20
    confiabilidade: float = 0.15
    novidade: float = 0.10
    confirmacao: float = 0.10
    persistencia: float = 0.10
    exposicao: float = 0.10

    def como_dicionario(self) -> dict[str, float]:
        return {
            MATERIALIDADE: self.materialidade,
            RELACAO_ATIVO: self.relacao_ativo,
            CONFIABILIDADE: self.confiabilidade,
            NOVIDADE: self.novidade,
            CONFIRMACAO: self.confirmacao,
            PERSISTENCIA: self.persistencia,
            EXPOSICAO: self.exposicao,
        }

    @property
    def total(self) -> float:
        return sum(self.como_dicionario().values())

    def validar(self) -> list[str]:
        """Avisos sobre a configuração. Não levanta: quem configura decide."""
        avisos = []
        if abs(self.total - 1.0) > 1e-6:
            avisos.append(f"pesos somam {self.total:.3f}, nao 1,000")
        for nome, peso in self.como_dicionario().items():
            if peso < 0:
                avisos.append(f"peso negativo em {nome}")
        return avisos


PESOS_PADRAO = Pesos()


@dataclass(frozen=True)
class Relevancia:
    """Nota, faixa e a conta que levou até elas."""

    nota: float
    faixa: str
    componentes: dict[str, float | None] = field(default_factory=dict)
    pesos: dict[str, float] = field(default_factory=dict)
    cobertura: float = 0.0
    limitacoes: tuple[str, ...] = ()
    #: A nota antes do teto de evidência. Igual a ``nota`` quando não houve
    #: rebaixamento. Guardada porque o teto é convenção deste motor e o número
    #: que ele limitou é observação -- apagar o segundo para exibir o primeiro
    #: seria perder a evidência de que o rebaixamento aconteceu.
    nota_bruta: float = 0.0
    #: O teto aplicado, em 0..100. ``None`` quando nenhum componente de
    #: evidência pôde ser medido, e aí não há teto -- inventar um seria punir
    #: ausência de medição, que é o defeito que o módulo inteiro evita.
    teto_evidencia: float | None = None

    @property
    def medidos(self) -> tuple[str, ...]:
        return tuple(k for k, v in self.componentes.items() if v is not None)

    @property
    def nao_medidos(self) -> tuple[str, ...]:
        return tuple(k for k, v in self.componentes.items() if v is None)

    @property
    def rotulo_faixa(self) -> str:
        return taxonomia.ROTULO_FAIXA.get(self.faixa, self.faixa)

    def texto_cobertura(self) -> str:
        faltando = ", ".join(ROTULO_COMPONENTE.get(k, k)
                             for k in self.nao_medidos)
        base = f"cobertura de {self.cobertura * 100:.0f}% dos criterios"
        return f"{base}; sem medicao de: {faltando}" if faltando else base


def _novidade(noticia: Noticia, agora: datetime,
              primeiro_em: datetime | None = None) -> float | None:
    """Quão nova é a informação. ``None`` sem data de publicação.

    Decai com a idade absoluta e leva desconto quando o evento já vinha sendo
    noticiado antes desta matéria: a quinta reportagem sobre o mesmo fato não
    traz informação nova, mesmo tendo saído há dez minutos.
    """
    idade_min = noticia.idade_em_minutos(agora)
    if idade_min is None:
        return None
    horas = max(0.0, idade_min / 60.0)
    if horas <= 6:
        base = 1.0
    elif horas <= 24:
        base = 0.85
    elif horas <= 72:
        base = 0.55
    elif horas <= 168:
        base = 0.25
    else:
        base = 0.05

    if primeiro_em is not None and noticia.publicado_em is not None:
        atraso_h = (noticia.publicado_em - primeiro_em).total_seconds() / 3600.0
        if atraso_h > 6:
            base *= 0.6
        elif atraso_h > 1:
            base *= 0.85
    return max(0.0, min(1.0, base))


def _confirmacao(n_fontes: int, primaria: bool) -> float:
    """Confirmação independente. Fonte primária vale confirmação completa."""
    if primaria:
        return 1.0
    if n_fontes >= 3:
        return 0.9
    if n_fontes == 2:
        return 0.65
    return 0.30


def _relacao(noticia: Noticia, tickers_alvo: frozenset[str]) -> float | None:
    """Quão direta é a relação com um ativo.

    Sem alvo definido, mede o escopo da própria notícia: matéria sobre uma
    empresa é mais direta do que matéria sobre um setor, que é mais direta do
    que macro. Com alvo, mede o encaixe entre a notícia e a carteira.
    """
    ent = noticia.entidades
    if tickers_alvo:
        if set(ent.tickers) & tickers_alvo:
            return 1.0
        if ent.setores:
            return 0.55
        if ent.paises or ent.moedas or ent.ativos:
            return 0.35
        return 0.10
    if ent.tickers or ent.empresas:
        return 1.0
    if ent.setores:
        return 0.60
    if ent.paises or ent.moedas or ent.ativos:
        return 0.35
    if ent.vazio:
        # Nada identificado: não é "pouco relacionado", é não medido.
        return None
    return 0.20


def _evidencia_externa(componentes: dict[str, float | None],
                       mapa_pesos: dict[str, float]) -> float | None:
    """Média dos componentes de evidência, renormalizada pelos medidos.

    ``None`` quando nenhum deles foi medido. Não é zero: "não sei se a fonte é
    confiável" não é "a fonte não é confiável", e tratar os dois como o mesmo
    número faria o teto punir a ausência de medição.
    """
    peso = sum(mapa_pesos.get(k, 0.0) for k in COMPONENTES_DE_EVIDENCIA
               if componentes.get(k) is not None)
    if peso <= 0:
        return None
    soma = sum(mapa_pesos.get(k, 0.0) * componentes[k]
               for k in COMPONENTES_DE_EVIDENCIA
               if componentes.get(k) is not None)
    return max(0.0, min(1.0, soma / peso))


def teto_de_evidencia(evidencia: float | None) -> float | None:
    """Quanto uma notícia pode afirmar, dada a evidência externa que ela tem.

    Linear de propósito, entre ``TETO_BASE`` e 100. Curva com joelho pareceria
    mais sofisticada e seria mais um parâmetro sem proveniência medida; a reta
    tem duas âncoras que dá para defender uma a uma:

    * evidência 1,00 (regulador, ou fonte primária) -> teto 100: não limita.
    * evidência 0,67 -> teto 80: é exatamente o mínimo para a faixa de revisão
      estratégica. Abaixo disso, nenhuma notícia é candidata a mexer em
      carteira -- e ``0,67`` não é alcançável por veículo desconhecido nenhum,
      porque o piso de confiabilidade da classe desconhecida é 0,20.
    """
    if evidencia is None:
        return None
    return TETO_BASE + (100.0 - TETO_BASE) * float(evidencia)


def _faixa(nota: float) -> str:
    if nota >= taxonomia.LIMITE_REVISAO:
        return taxonomia.FAIXA_REVISAO
    if nota >= taxonomia.LIMITE_OBSERVACAO:
        return taxonomia.FAIXA_OBSERVACAO
    return taxonomia.FAIXA_INFORMATIVA


def calcular(
    noticia: Noticia,
    *,
    pesos: Pesos = PESOS_PADRAO,
    agora: datetime | None = None,
    n_fontes_independentes: int = 1,
    confirmado_por_primaria: bool = False,
    primeiro_em: datetime | None = None,
    tickers_alvo=(),
    exposicao_carteira: float | None = None,
    cobertura_minima: float = COBERTURA_MINIMA_REVISAO,
) -> Relevancia:
    """Calcula o índice de relevância de uma notícia.

    ``exposicao_carteira`` é a fração do patrimônio exposta ao que a notícia
    toca, em 0..1. Passar ``None`` -- que é o caso de quem ainda não cadastrou
    carteira -- deixa o componente fora da conta e reduz a cobertura, em vez de
    marcar exposição zero. Marcar zero puniria quem não tem carteira com uma
    nota menor, que é o oposto do que a ausência de dado significa.
    """
    referencia = agora or datetime.now(timezone.utc)
    tipo = noticia.tipo
    alvo = frozenset(t.upper() for t in (tickers_alvo or ()))

    componentes: dict[str, float | None] = {
        MATERIALIDADE: tipo.materialidade,
        RELACAO_ATIVO: _relacao(noticia, alvo),
        CONFIABILIDADE: (noticia.fonte.confiabilidade
                         if noticia.fonte is not None else None),
        NOVIDADE: _novidade(noticia, referencia, primeiro_em),
        CONFIRMACAO: _confirmacao(n_fontes_independentes,
                                  confirmado_por_primaria),
        PERSISTENCIA: tipo.persistencia,
        EXPOSICAO: (None if exposicao_carteira is None
                    else max(0.0, min(1.0, float(exposicao_carteira)))),
    }

    mapa_pesos = pesos.como_dicionario()
    peso_medido = sum(mapa_pesos[k] for k, v in componentes.items()
                      if v is not None)
    total = pesos.total

    if peso_medido <= 0 or total <= 0:
        return Relevancia(
            nota=0.0,
            faixa=taxonomia.FAIXA_INFORMATIVA,
            componentes=componentes,
            pesos=mapa_pesos,
            cobertura=0.0,
            limitacoes=("nenhum componente pode ser medido",),
            nota_bruta=0.0,
        )

    soma = sum(mapa_pesos[k] * v for k, v in componentes.items()
               if v is not None)
    nota_bruta = max(0.0, min(100.0, (soma / peso_medido) * 100.0))
    cobertura = peso_medido / total

    limitacoes: list[str] = []

    # O teto entra ANTES da faixa: rebaixar a nota e classificar pela nota
    # antiga devolveria o defeito inteiro por outro caminho -- a faixa é o que
    # os portões e a ordenação leem.
    evidencia = _evidencia_externa(componentes, mapa_pesos)
    teto = teto_de_evidencia(evidencia)
    nota = nota_bruta
    if teto is not None and nota_bruta > teto:
        nota = teto
        limitacoes.append(
            f"nota {nota_bruta:.0f} limitada a {teto:.0f} pela evidencia "
            f"externa ({evidencia * 100:.0f}%): materialidade, relacao e "
            "novidade sao declaradas pela propria noticia, e sozinhas nao "
            "sustentam a nota")

    faixa = _faixa(nota)
    if componentes[EXPOSICAO] is None:
        limitacoes.append("sem carteira cadastrada: exposicao nao entrou na nota")
    if componentes[NOVIDADE] is None:
        limitacoes.append("sem data de publicacao: novidade nao entrou na nota")

    if faixa == taxonomia.FAIXA_REVISAO and cobertura < cobertura_minima:
        # Rebaixamento explícito, com motivo. Sem isso, a nota alta obtida com
        # poucos critérios medidos abriria o portão de revisão estratégica.
        faixa = taxonomia.FAIXA_OBSERVACAO
        limitacoes.append(
            f"nota {nota:.0f} obtida com cobertura de {cobertura * 100:.0f}%, "
            f"abaixo do minimo de {cobertura_minima * 100:.0f}% exigido para "
            "revisao estrategica")

    return Relevancia(
        nota=round(nota, 1),
        faixa=faixa,
        componentes=componentes,
        pesos=mapa_pesos,
        cobertura=round(cobertura, 4),
        limitacoes=tuple(limitacoes),
        nota_bruta=round(nota_bruta, 1),
        teto_evidencia=None if teto is None else round(teto, 1),
    )
