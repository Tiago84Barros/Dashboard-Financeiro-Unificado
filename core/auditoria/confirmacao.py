"""Confirmação explícita das mudanças grandes -- os nove pontos, e o tom.

O requisito lista nove coisas que precisam estar na tela antes de o usuário
confirmar uma mudança relevante: ação proposta, percentual ou valor, motivo,
riscos, custos, impostos, efeito na concentração, efeito na liquidez e
possibilidade de reversão. E acrescenta uma restrição sobre a **forma**:

    "Não use botões ou textos que induzam decisões impulsivas."

As duas metades se sustentam. Mostrar os nove pontos ao lado de um botão
escrito "Aproveitar agora" não informa -- decora a pressa com dados.

Ponto que faltou aparece; não some
-----------------------------------
Se o imposto não foi calculado, a tela escreve que não foi calculado. Ela não
esconde a linha nem preenche com zero. É a lei do projeto (``ok=None`` é "não
medido", nunca ``False``), e aqui ela tem consequência direta: imposto exibido
como "R$ 0,00" quando ninguém o calculou é uma afirmação falsa sobre o custo da
operação, e o usuário decide com base nela.

Faltar ponto não bloqueia a confirmação -- quem decide é o usuário, e negar a
ele a decisão porque o sistema não conseguiu calcular um campo seria trocar
transparência por paternalismo. O que o sistema faz é publicar a lacuna em
:attr:`Confirmacao.lacunas` e mostrá-la com o mesmo destaque dos pontos que
foram calculados.

Puro: sem rede, sem banco, sem Streamlit. A view desenha; aqui só se decide o
que precisa estar desenhado.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: Os nove pontos, na ordem em que a tela deve mostrá-los. Ação e tamanho
#: primeiro (o que muda), depois motivo, depois o preço de fazer, e reversão
#: por último -- é a informação que reduz o peso da decisão, e ela vem depois
#: dos custos justamente para não amortecer a leitura deles.
PONTOS: tuple[str, ...] = (
    "acao", "tamanho", "motivo", "riscos", "custos", "impostos",
    "concentracao", "liquidez", "reversao",
)

ROTULO: dict[str, str] = {
    "acao": "Ação proposta",
    "tamanho": "Percentual / valor",
    "motivo": "Por que agora",
    "riscos": "Riscos",
    "custos": "Custos",
    "impostos": "Impostos",
    "concentracao": "Efeito na concentração",
    "liquidez": "Efeito na liquidez",
    "reversao": "Possibilidade de reversão",
}

NAO_CALCULADO = "não calculado"

#: Palavras que empurram. A lista não é exaustiva -- enumerar formulação hostil
#: é um jogo perdido, como já se mediu na injeção de prompt. Ela serve para o
#: teste pegar a reincidência óbvia, e o que de fato segura o tom é o conjunto
#: fixo de rótulos em :data:`BOTOES`.
_INDUTORES = re.compile(
    r"(?i)\b(agora\s+ou\s+nunca|últim[ao]s?\s+chance|não\s+perc[ao]|"
    r"aproveite|garanta|imperd[íi]vel|urgente|corra|r[áa]pido|"
    r"antes\s+que|s[óo]\s+hoje|oportunidade\s+[úu]nica|lucro\s+garantido|"
    r"retorno\s+garantido|sem\s+risco)\b")

#: Rótulos permitidos. Descrevem o ato, não o resultado esperado dele.
BOTOES: dict[str, str] = {
    "confirmar": "Confirmar esta mudança",
    "recusar": "Não fazer",
    "adiar": "Decidir depois",
}


def texto_induz(texto: str) -> tuple[str, ...]:
    """Trechos que induzem pressa. Vazio quando o texto está limpo."""
    return tuple(dict.fromkeys(m.group(0).lower()
                               for m in _INDUTORES.finditer(texto or "")))


@dataclass(frozen=True)
class Ponto:
    chave: str
    texto: str | None

    @property
    def calculado(self) -> bool:
        return bool(self.texto and self.texto.strip())

    @property
    def rotulo(self) -> str:
        return ROTULO[self.chave]

    def descrever(self) -> str:
        return (f"{self.rotulo}: {self.texto}" if self.calculado
                else f"{self.rotulo}: {NAO_CALCULADO}")

    def aparencia(self) -> dict[str, str]:
        """Sem depender de verde e vermelho (requisito de acessibilidade)."""
        return ({"marca": "•", "estado": "calculado"} if self.calculado
                else {"marca": "?", "estado": "não calculado"})


@dataclass(frozen=True)
class Confirmacao:
    """O que a tela precisa mostrar antes de qualquer clique."""

    pontos: tuple[Ponto, ...]
    registro_id: str = ""

    @property
    def lacunas(self) -> tuple[str, ...]:
        return tuple(p.rotulo for p in self.pontos if not p.calculado)

    @property
    def completa(self) -> bool:
        return not self.lacunas

    def de(self, chave: str) -> Ponto:
        return next(p for p in self.pontos if p.chave == chave)

    def texto(self) -> str:
        linhas = [p.descrever() for p in self.pontos]
        if self.lacunas:
            linhas.append(
                "Pontos não calculados nesta análise: "
                + ", ".join(self.lacunas)
                + ". Eles não foram estimados no lugar.")
        linhas.append(
            "Nenhuma operação é executada pelo APP4. A confirmação registra a "
            "sua decisão; a ordem, se houver, é feita por você na corretora.")
        return "\n".join(linhas)

    def problemas_de_tom(self) -> tuple[str, ...]:
        """Trechos indutores em qualquer ponto. Deve ser sempre vazio."""
        achados: list[str] = []
        for p in self.pontos:
            achados.extend(texto_induz(p.texto or ""))
        return tuple(dict.fromkeys(achados))

    def resumo_auditoria(self) -> dict:
        return {
            "registro_id": self.registro_id,
            "pontos_calculados": [p.chave for p in self.pontos if p.calculado],
            "lacunas": list(self.lacunas),
            "tom": list(self.problemas_de_tom()),
        }


def montar(*, acao: str, tamanho: str | None = None, motivo: str | None = None,
           riscos: str | None = None, custos: str | None = None,
           impostos: str | None = None, concentracao: str | None = None,
           liquidez: str | None = None, reversao: str | None = None,
           registro_id: str = "") -> Confirmacao:
    """Monta a confirmação. Só ``acao`` é obrigatória.

    Os outros oito aceitam ``None`` porque ``None`` é a resposta honesta quando
    o cálculo não foi feito -- e é justamente esse ``None`` que
    :attr:`Confirmacao.lacunas` publica. Exigir os nove aqui empurraria quem
    chama a inventar string para preencher, que é o defeito oposto e mais caro.
    """
    if not (acao or "").strip():
        raise ValueError("confirmação sem ação proposta não é confirmação.")
    valores = {
        "acao": acao, "tamanho": tamanho, "motivo": motivo, "riscos": riscos,
        "custos": custos, "impostos": impostos, "concentracao": concentracao,
        "liquidez": liquidez, "reversao": reversao,
    }
    return Confirmacao(
        pontos=tuple(Ponto(c, valores[c]) for c in PONTOS),
        registro_id=registro_id)


def do_registro(reg, **extra) -> Confirmacao:
    """Deriva a confirmação de um :class:`core.auditoria.trilha.Registro`.

    A tela e a trilha passam a ler a mesma origem. Montar as duas separadamente
    deixaria o registro dizer uma coisa e o usuário ver outra -- e a auditoria
    responderia pela versão que ninguém leu.
    """
    tamanho = None
    if reg.percentual is not None:
        tamanho = f"{reg.percentual:.2f}% da carteira"
    elif reg.valor is not None:
        tamanho = f"R$ {reg.valor:,.2f}"
    campos = {
        "acao": reg.acao + (f" em {reg.ativo}" if reg.ativo else ""),
        "tamanho": tamanho,
        "motivo": reg.motivo or None,
        "registro_id": reg.id,
    }
    campos.update(extra)
    return montar(**campos)
