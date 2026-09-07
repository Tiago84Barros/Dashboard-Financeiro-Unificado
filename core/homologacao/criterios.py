"""Critérios objetivos de avanço de fase, e o rollback.

    "Defina critérios objetivos para avançar de fase."

Objetivo quer dizer com número medido, não com impressão. "Parece estar
funcionando" não avança fase -- é a mesma exigência do Prompt 3 quando proíbe
tratar peso sugerido como verdade, e do Prompt 1 quando manda apresentar
evidência em vez de declarar sucesso.

O formato de cada critério
--------------------------
Um :class:`Criterio` tem um nome, um limiar e uma *leitura*. A leitura devolve
``None`` quando ninguém mediu -- e ``None`` **não** avança fase, mas também não
reprova: aparece como *não medido*, que é a lei do projeto (``ok=None`` nunca é
``False``).

A distinção importa aqui mais do que em qualquer outro lugar do sistema: um
critério que devolvesse ``False`` por falta de medição faria o avanço parecer
"reprovado por desempenho" quando na verdade ninguém rodou o teste. E um que
devolvesse ``True`` liberaria decisão real com base em nada.

Por que o rollback é uma fase, e não um botão
----------------------------------------------
Voltar de fase é a operação de segurança do Prompt 5 -- e ela precisa ser mais
barata que qualquer conserto. :func:`rollback` só mexe na fase: as flags ficam
como estavam, e o teto por fase (``core.homologacao.flags.Estado.ativo``) já
desliga tudo que a fase menor não alcança. Um rollback que também desligasse as
flags obrigaria alguém a reconfigurar nove chaves no pior momento possível.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.homologacao import flags

MAIOR_MELHOR = "maior_melhor"
MENOR_MELHOR = "menor_melhor"


@dataclass(frozen=True)
class Criterio:
    """Um portão de avanço, com limiar declarado."""

    nome: str
    limiar: float
    sentido: str
    unidade: str = ""
    justificativa: str = ""

    def avalia(self, medido: float | None) -> bool | None:
        """``None`` é 'não medido' -- não avança e não reprova."""
        if medido is None:
            return None
        return (medido >= self.limiar if self.sentido == MAIOR_MELHOR
                else medido <= self.limiar)

    def descrever(self, medido: float | None) -> str:
        alvo = ("≥" if self.sentido == MAIOR_MELHOR else "≤")
        if medido is None:
            return f"{self.nome}: não medido (exigido {alvo} {self.limiar}{self.unidade})"
        estado = "atende" if self.avalia(medido) else "NÃO atende"
        return (f"{self.nome}: {medido}{self.unidade} — {estado} "
                f"(exigido {alvo} {self.limiar}{self.unidade})")


#: O que cada fase exige para ser liberada. A Fase 1 não tem critério porque
#: ela não afirma nada -- exigir prova para começar a observar seria exigir a
#: medição antes de existir com o que medir.
EXIGIDO: dict[int, tuple[Criterio, ...]] = {
    flags.PAINEL: (
        Criterio("cobertura_de_frescor", 0.95, MAIOR_MELHOR,
                 justificativa="dado sem carimbo não pode ser exibido como "
                               "atual; a tela promete data e hora"),
        Criterio("itens_sem_fonte", 0.0, MENOR_MELHOR,
                 justificativa="toda notícia mostra fonte, data e hora"),
        Criterio("taxa_de_erro_da_coleta", 0.05, MENOR_MELHOR,
                 justificativa="coleta que falha muito publica painel velho "
                               "com cara de novo"),
    ),
    flags.RECOMENDACAO: (
        Criterio("erro_de_calibracao_probabilidade", 0.10, MENOR_MELHOR,
                 justificativa="probabilidade mal calibrada é o critério "
                               "explícito de não-produção do Prompt 3"),
        Criterio("alarmes_por_semana", 7.0, MENOR_MELHOR,
                 justificativa="alarme excessivo treina o usuário a ignorar, "
                               "e aí o alerta certo também é ignorado"),
        Criterio("cobertura_da_trilha", 1.0, MAIOR_MELHOR,
                 justificativa="recomendação sem registro não responde 'por "
                               "que naquele momento'"),
        Criterio("respostas_llm_reprovadas", 0.10, MENOR_MELHOR,
                 justificativa="reprovação alta é sinal de que o modelo está "
                               "inventando, não de que o filtro é bom"),
    ),
    flags.CRISE: (
        Criterio("falsos_positivos_nivel_3_ou_4", 0.0, MENOR_MELHOR,
                 justificativa="crise declarada por engano é o dano mais caro "
                               "que este sistema pode causar"),
        Criterio("cenarios_historicos_reproduzidos", 11.0, MAIOR_MELHOR,
                 justificativa="os 11 cenários históricos exigidos no "
                               "requisito original"),
        Criterio("tempo_ate_rebaixar_nivel_h", 24.0, MENOR_MELHOR,
                 unidade="h",
                 justificativa="sem rebaixamento e encerramento explícito, o "
                               "Modo Crise vira estado permanente"),
    ),
}


@dataclass(frozen=True)
class Avaliacao:
    """O resultado de perguntar 'posso avançar?'."""

    fase_atual: int
    fase_alvo: int
    linhas: tuple[str, ...]
    atendidos: tuple[str, ...]
    reprovados: tuple[str, ...]
    nao_medidos: tuple[str, ...]

    @property
    def pode_avancar(self) -> bool:
        """Exige **todos** atendidos. Não medido não conta como atendido."""
        return not self.reprovados and not self.nao_medidos

    def texto(self) -> str:
        cabeca = (f"{flags.NOME_FASE[self.fase_atual]} → "
                  f"{flags.NOME_FASE[self.fase_alvo]}: "
                  + ("liberado" if self.pode_avancar else "não liberado"))
        corpo = list(self.linhas)
        if self.nao_medidos:
            corpo.append(
                "Critérios não medidos não liberam a fase e não reprovam o "
                "sistema: eles dizem que o teste não foi feito.")
        return "\n".join([cabeca, *corpo])

    def resumo_auditoria(self) -> dict:
        return {
            "fase_atual": self.fase_atual,
            "fase_alvo": self.fase_alvo,
            "pode_avancar": self.pode_avancar,
            "atendidos": list(self.atendidos),
            "reprovados": list(self.reprovados),
            "nao_medidos": list(self.nao_medidos),
        }


def avaliar(fase_atual: int, medidas: dict[str, float | None]) -> Avaliacao:
    """Avalia o avanço de ``fase_atual`` para a seguinte.

    ``medidas`` mapeia nome de critério para o valor medido. Chave ausente é
    ``None`` -- não medido, e não zero. Tratar ausência como zero faria um
    critério do tipo "menor melhor" passar exatamente por não ter sido medido,
    que é o modo mais fácil de liberar decisão real sem prova.
    """
    alvo = fase_atual + 1
    if alvo not in EXIGIDO:
        return Avaliacao(fase_atual, min(alvo, flags.CRISE), (), (), (), ())
    linhas, ok, reprovados, ausentes = [], [], [], []
    for c in EXIGIDO[alvo]:
        medido = medidas.get(c.nome)
        linhas.append(c.descrever(medido))
        veredito = c.avalia(medido)
        (ok if veredito else ausentes if veredito is None else reprovados
         ).append(c.nome)
    return Avaliacao(fase_atual, alvo, tuple(linhas), tuple(ok),
                     tuple(reprovados), tuple(ausentes))


def avancar(estado: flags.Estado, medidas: dict[str, float | None]
            ) -> tuple[flags.Estado, Avaliacao]:
    """Devolve o estado após a tentativa de avanço, e o porquê.

    Devolve o par sempre -- inclusive quando não avança. Uma função que
    devolvesse só o estado obrigaria quem chama a re-avaliar para descobrir o
    motivo, e o motivo é a metade que interessa.
    """
    av = avaliar(estado.fase, medidas)
    if not av.pode_avancar or av.fase_alvo == estado.fase:
        return estado, av
    return flags.Estado(fase=av.fase_alvo, valores=dict(estado.valores)), av


def rollback(estado: flags.Estado, *, para: int | None = None
             ) -> flags.Estado:
    """Volta uma fase (ou até ``para``), preservando as flags.

    As flags ficam como estavam de propósito: o teto por fase já desliga o que
    a fase menor não alcança, e reconfigurar nove chaves no pior momento
    possível é como um rollback vira um segundo incidente.
    """
    destino = flags.OBSERVACAO if para is None else int(para)
    if para is None:
        destino = max(flags.OBSERVACAO, estado.fase - 1)
    destino = max(flags.OBSERVACAO, min(flags.CRISE, destino))
    return flags.Estado(fase=destino, valores=dict(estado.valores))
