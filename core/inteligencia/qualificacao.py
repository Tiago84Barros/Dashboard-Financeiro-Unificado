"""Fato, hipótese, estimativa -- e o que já venceu.

A exigência de "distinguir fato, hipótese e estimativa" não é rótulo de tela: é
uma propriedade do dado, e quem sabe qual é dos três é o backend que o produziu.
Se a distinção nascer na view, cada tela vai inventar a sua, e em pouco tempo a
mesma medição aparece como fato numa aba e como estimativa noutra.

As quatro qualidades
--------------------
:data:`FATO` -- observado, com fonte e carimbo de quando foi medido. Preço de
fechamento, HHI da carteira, número de fontes que publicaram.

:data:`HIPOTESE` -- afirmado por alguém, ainda não confirmado. A manchete que
diz que um banco quebrou é hipótese até a fonte oficial confirmar. Continua
valendo a pena mostrar -- é o que dispara o monitoramento -- mas não é fato.

:data:`ESTIMATIVA` -- saiu de modelo, amostra histórica ou simulação. Nunca é um
número só: :data:`Valor` recusa estimativa sem faixa, porque estimativa pontual
publicada sem intervalo é lida como previsão, e a especificação proibiu o app de
afirmar que prevê cisnes negros.

:data:`AUSENTE` -- não medido. Não é zero. Um componente que ninguém mediu tem
de sair da média e aparecer na cobertura, não entrar como 0,0 punindo quem
mediu menos.

Vencimento é derivado, nunca escrito
------------------------------------
:class:`Frescor` calcula o estado a partir do carimbo e da validade declarada.
Não existe campo "está atualizado" que alguém possa deixar para trás: este
projeto já publicou um aviso que dizia "nenhuma deslistagem foi ingerida" muito
depois de as deslistagens terem sido ingeridas, e ele seguia soando como rigor.
Texto de limitação que não é derivado da medição envelhece invertido.

Cor nunca é o único canal
-------------------------
Cada qualidade e cada estado de frescor carrega ``icone`` e ``texto`` além da
cor. Quem não distingue verde de vermelho -- e é gente demais para arredondar --
precisa ler a mesma informação.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

FATO = "fato"
HIPOTESE = "hipotese"
ESTIMATIVA = "estimativa"
AUSENTE = "ausente"

QUALIDADES: tuple[str, ...] = (FATO, HIPOTESE, ESTIMATIVA, AUSENTE)

#: Rótulo, ícone e cor de cada qualidade. O ícone e o rótulo bastam sozinhos.
APARENCIA: dict[str, dict[str, str]] = {
    FATO: {"rotulo": "Fato", "icone": "◆", "cor": "#4A9EFF",
           "ajuda": "Observado e com fonte identificada."},
    HIPOTESE: {"rotulo": "Hipótese", "icone": "◇", "cor": "#E8B84B",
               "ajuda": "Afirmado por uma fonte e ainda não confirmado."},
    ESTIMATIVA: {"rotulo": "Estimativa", "icone": "≈", "cor": "#A78BFA",
                 "ajuda": "Derivado de modelo ou de amostra histórica. "
                          "Publicado em faixa, nunca como número único."},
    AUSENTE: {"rotulo": "Não medido", "icone": "—", "cor": "#9CA3AF",
              "ajuda": "Sem fonte disponível. Não é zero."},
}

# ── Estados de frescor ────────────────────────────────────────────────────────
FRESCO = "fresco"
VENCIDO = "vencido"
INDISPONIVEL = "indisponivel"
NUNCA = "nunca_atualizado"

ESTADOS_FRESCOR: tuple[str, ...] = (FRESCO, VENCIDO, INDISPONIVEL, NUNCA)

APARENCIA_FRESCOR: dict[str, dict[str, str]] = {
    FRESCO: {"rotulo": "Atualizado", "icone": "●", "cor": "#00C896"},
    VENCIDO: {"rotulo": "Desatualizado", "icone": "▲", "cor": "#E8B84B"},
    INDISPONIVEL: {"rotulo": "Indisponível", "icone": "✕", "cor": "#FC5C7D"},
    NUNCA: {"rotulo": "Nunca atualizado", "icone": "○", "cor": "#9CA3AF"},
}

#: Estados em que o dado NÃO sustenta decisão e a tela tem de destacar.
ESTADOS_A_DESTACAR = frozenset({VENCIDO, INDISPONIVEL, NUNCA})


def _agora(valor: dt.datetime | None) -> dt.datetime:
    return valor or dt.datetime.now(dt.timezone.utc)


def _aware(quando: dt.datetime | None) -> dt.datetime | None:
    """Carimbo ingênuo é tratado como UTC.

    Comparar ingênuo com ciente levanta ``TypeError`` no meio da renderização, e
    o repositório mistura as duas coisas: o banco devolve ingênuo e
    ``core.noticias`` grava ciente.
    """
    if quando is None:
        return None
    return quando if quando.tzinfo else quando.replace(tzinfo=dt.timezone.utc)


@dataclass(frozen=True)
class Valor:
    """Um número publicável, com a qualidade que ele de fato tem.

    Raises:
        ValueError: estimativa sem faixa e sem motivo declarado. Estimativa
            pontual é lida como previsão, e o app não prevê.
    """

    rotulo: str
    valor: float | str | None = None
    qualidade: str = AUSENTE
    unidade: str = ""
    fonte: str | None = None
    medido_em: dt.datetime | None = None
    faixa: tuple[float, float] | None = None
    confianca: str | None = None
    horizonte: str | None = None
    observacao: str = ""

    def __post_init__(self) -> None:
        if self.qualidade not in QUALIDADES:
            raise ValueError(f"qualidade fora do vocabulário: {self.qualidade!r}")
        if self.valor is None and self.qualidade != AUSENTE:
            raise ValueError(
                f"{self.rotulo!r}: valor ausente não pode ser publicado como "
                f"{self.qualidade!r}. Ausência de dado não é ausência de risco.")
        if self.qualidade == ESTIMATIVA and self.faixa is None and not self.observacao:
            raise ValueError(
                f"{self.rotulo!r}: estimativa sem faixa precisa declarar por que "
                "a faixa não pôde ser calculada.")

    @property
    def medido(self) -> bool:
        return self.qualidade != AUSENTE

    @property
    def aparencia(self) -> dict[str, str]:
        return APARENCIA[self.qualidade]

    @property
    def texto(self) -> str:
        """O valor como se lê na tela, já com faixa e unidade."""
        if not self.medido:
            return "não medido"
        if self.faixa is not None:
            piso, teto = self.faixa
            return f"{_fmt(piso)}{self.unidade} a {_fmt(teto)}{self.unidade}"
        return f"{_fmt(self.valor)}{self.unidade}"

    def descrever(self) -> str:
        partes = [f"{self.rotulo}: {self.texto}",
                  f"[{self.aparencia['rotulo']}]"]
        if self.confianca:
            partes.append(f"confiança {self.confianca}")
        if self.horizonte:
            partes.append(f"horizonte {self.horizonte}")
        if self.fonte:
            partes.append(f"fonte: {self.fonte}")
        if self.observacao:
            partes.append(self.observacao)
        return " · ".join(partes)

    def numeros(self) -> tuple[float, ...]:
        """Os números que este valor autoriza a LLM a citar.

        A âncora da resposta sai daqui: o que não estiver nesta lista (nem for
        derivável dela) não veio do backend.
        """
        saida: list[float] = []
        if isinstance(self.valor, (int, float)) and not isinstance(self.valor, bool):
            saida.append(float(self.valor))
        if self.faixa is not None:
            saida.extend(float(v) for v in self.faixa)
        return tuple(saida)


def _fmt(valor) -> str:
    """Formata sem ALTERAR o valor.

    Arredondar aqui parecia inofensivo e não é: o texto é o que a LLM lê, e
    :meth:`Valor.numeros` é o conjunto contra o qual a resposta dela é
    ancorada. Um fator de 0,836 impresso como "0,84" faz a citação correta da
    tela ser reprovada como número inventado. Quem decide a precisão é quem
    produz o valor, arredondando ANTES de construir o :class:`Valor`.
    """
    if isinstance(valor, bool):
        return "sim" if valor else "não"
    if isinstance(valor, (int, float)):
        f = float(valor)
        inteiro, _, frac = f"{abs(f):.6f}".partition(".")
        frac = frac.rstrip("0")
        # Milhar com ponto e decimal com virgula: é o padrão pt-BR E o que
        # core.llm_grounding.parse_number sabe ler. Agrupar com espaco faria o
        # verificador quebrar "1 234 567" em três números inexistentes.
        texto = f"{int(inteiro):,}".replace(",", ".")
        if frac:
            texto += "," + frac
        sinal = "-" if f < 0 and (int(inteiro) or frac) else ""
        return sinal + texto
    return str(valor)


def ausente(rotulo: str, motivo: str) -> Valor:
    """Atalho para o caso mais importante: dizer que não se sabe."""
    return Valor(rotulo=rotulo, valor=None, qualidade=AUSENTE, observacao=motivo)


def fato(rotulo: str, valor, *, unidade: str = "", fonte: str | None = None,
         medido_em: dt.datetime | None = None, observacao: str = "") -> Valor:
    return Valor(rotulo=rotulo, valor=valor, qualidade=FATO, unidade=unidade,
                 fonte=fonte, medido_em=medido_em, observacao=observacao)


def hipotese(rotulo: str, valor, *, unidade: str = "", fonte: str | None = None,
             medido_em: dt.datetime | None = None, observacao: str = "") -> Valor:
    return Valor(rotulo=rotulo, valor=valor, qualidade=HIPOTESE, unidade=unidade,
                 fonte=fonte, medido_em=medido_em, observacao=observacao)


def estimativa(rotulo: str, *, faixa: tuple[float, float] | None = None,
               central: float | None = None, unidade: str = "",
               confianca: str | None = None, horizonte: str | None = None,
               fonte: str | None = None, observacao: str = "") -> Valor:
    """Estimativa publicada em faixa.

    ``central`` só existe para ordenar e comparar. A tela mostra a faixa: um
    número central sozinho é lido como previsão, e é assim que uma estimativa
    com 40 pontos de intervalo vira promessa de retorno.
    """
    return Valor(rotulo=rotulo, valor=central if central is not None else
                 (sum(faixa) / 2.0 if faixa else None),
                 qualidade=ESTIMATIVA if (faixa or central is not None) else AUSENTE,
                 unidade=unidade, faixa=faixa, confianca=confianca,
                 horizonte=horizonte, fonte=fonte, observacao=observacao)


@dataclass(frozen=True)
class Frescor:
    """Quando isto foi atualizado pela última vez, e se ainda vale.

    ``estado`` é derivado. Não há como um texto de "está atualizado" sobreviver
    ao fato de não estar mais.
    """

    rotulo: str
    atualizado_em: dt.datetime | None = None
    validade_horas: float = 24.0
    disponivel: bool = True
    erro: str = ""
    fonte: str | None = None

    def idade_horas(self, agora: dt.datetime | None = None) -> float | None:
        carimbo = _aware(self.atualizado_em)
        if carimbo is None:
            return None
        delta = _agora(agora) - carimbo
        return delta.total_seconds() / 3600.0

    def estado(self, agora: dt.datetime | None = None) -> str:
        if not self.disponivel:
            return INDISPONIVEL
        idade = self.idade_horas(agora)
        if idade is None:
            return NUNCA
        return VENCIDO if idade > self.validade_horas else FRESCO

    def a_destacar(self, agora: dt.datetime | None = None) -> bool:
        return self.estado(agora) in ESTADOS_A_DESTACAR

    def aparencia(self, agora: dt.datetime | None = None) -> dict[str, str]:
        return APARENCIA_FRESCOR[self.estado(agora)]

    def descrever(self, agora: dt.datetime | None = None) -> str:
        estado = self.estado(agora)
        rotulo = APARENCIA_FRESCOR[estado]["rotulo"]
        if estado == INDISPONIVEL:
            detalhe = self.erro or "fonte não respondeu"
            return f"{self.rotulo}: {rotulo} — {detalhe}"
        if estado == NUNCA:
            return f"{self.rotulo}: {rotulo}"
        idade = self.idade_horas(agora) or 0.0
        quando = _aware(self.atualizado_em)
        carimbo = quando.strftime("%d/%m/%Y %H:%M UTC") if quando else "?"
        if estado == VENCIDO:
            return (f"{self.rotulo}: {rotulo} — última atualização {carimbo} "
                    f"({idade:.1f}h atrás, validade {self.validade_horas:.0f}h)")
        return f"{self.rotulo}: {rotulo} — {carimbo} ({idade:.1f}h atrás)"


@dataclass(frozen=True)
class Provedor:
    """Estado de uma fonte externa, para a tela poder dizer o que caiu."""

    nome: str
    disponivel: bool
    detalhe: str = ""
    ultima_chamada: dt.datetime | None = None
    chamadas_restantes: int | None = None

    @property
    def aparencia(self) -> dict[str, str]:
        return APARENCIA_FRESCOR[FRESCO if self.disponivel else INDISPONIVEL]

    def descrever(self) -> str:
        estado = "disponível" if self.disponivel else "indisponível"
        texto = f"{self.nome}: {estado}"
        if self.detalhe:
            texto += f" — {self.detalhe}"
        if self.chamadas_restantes is not None:
            texto += f" (cota restante: {self.chamadas_restantes})"
        return texto


@dataclass(frozen=True)
class Bloco:
    """Um conjunto de valores com um frescor comum -- a unidade de tela."""

    titulo: str
    valores: tuple[Valor, ...] = ()
    frescor: Frescor | None = None
    limitacoes: tuple[str, ...] = ()
    explicacao_simples: str = ""
    detalhe_tecnico: tuple[str, ...] = field(default_factory=tuple)

    @property
    def medidos(self) -> tuple[Valor, ...]:
        return tuple(v for v in self.valores if v.medido)

    @property
    def nao_medidos(self) -> tuple[Valor, ...]:
        return tuple(v for v in self.valores if not v.medido)

    @property
    def cobertura(self) -> float:
        return len(self.medidos) / len(self.valores) if self.valores else 0.0

    def valor_de(self, rotulo: str) -> Valor | None:
        for v in self.valores:
            if v.rotulo == rotulo:
                return v
        return None

    def numeros(self) -> tuple[float, ...]:
        saida: list[float] = []
        for v in self.valores:
            saida.extend(v.numeros())
        return tuple(saida)
