"""Semântica única do saldo mensal: investimento não é despesa.

Por que este módulo existe
--------------------------
A mesma conta estava escrita em quatro lugares (duas abas de
``views/controle_financeiro``, dois blocos de ``views/dashboard_geral``) e as
quatro subtraíam o aporte do saldo do mês. O efeito medido na tela do usuário:
receitas 27.636,02, despesas 25.577,70 e aporte 10.000,00 produziam saldo
−7.941,68 em vermelho e "renda comprometida" 128,70%. Nenhum dos dois números
descreve o que aconteceu — o mês fechou positivo em 2.058,32 de caixa, e o
aporte é patrimônio que mudou de lugar, não dinheiro que saiu da vida do dono.

As três regras, e o que cada uma responde
-----------------------------------------
* ``saldo_caixa = receitas − despesas`` é a única linha que pode ser negativa,
  e ela só fica negativa quando a despesa ultrapassa a entrada. É a pergunta
  "eu gastei mais do que ganhei?".
* ``total_retido = saldo_caixa + investimentos`` é a pergunta "quanto do mês
  ficou comigo?" — sobra em conta mais o que virou posição. Quando ele supera a
  própria receita do mês, o excedente veio de aporte acima do que sobrou, e é
  isso que a cor azul sinaliza. Azul não é elogio nem alerta: é "o número
  ultrapassou a receita por causa do aporte, não por causa do fluxo".
* ``renda_comprometida = despesas / receitas`` ignora aporte de propósito.
  Comprometer renda é assumir saída obrigatória; investir é o contrário disso.

Cor é consequência das regras acima, nunca uma quarta decisão:

=========  ==================================================================
vermelho   ``saldo_caixa < 0`` — despesa acima da receita
azul       ``total_retido > receitas`` — só alcançável com aporte > despesas
verde      o resto
=========  ==================================================================

Ausência de receita não vira zero silencioso: com ``receitas <= 0`` a razão de
comprometimento devolve ``None`` e quem exibe decide como dizer "não medido".
"""
from __future__ import annotations

import math
from dataclasses import dataclass

#: Verde, vermelho e azul da paleta do app. Ficam aqui para que as quatro telas
#: não divirjam sobre qual verde é o verde do saldo.
COR_POSITIVO = "#00C896"
COR_NEGATIVO = "#FC5C7D"
COR_APORTE = "#4A9EFF"


def _num(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


@dataclass(frozen=True)
class FluxoMes:
    """Leitura fechada de um período. Todos os campos em reais, exceto ``*_pct``."""

    receitas: float
    despesas: float
    investimentos: float

    @property
    def saldo_caixa(self) -> float:
        """Receitas menos despesas. O aporte não entra."""
        return round(self.receitas - self.despesas, 2)

    @property
    def total_retido(self) -> float:
        """Sobra de caixa mais o que virou posição no período."""
        return round(self.saldo_caixa + self.investimentos, 2)

    @property
    def deficit(self) -> bool:
        return self.saldo_caixa < 0

    @property
    def acima_da_receita(self) -> bool:
        """Retido ultrapassou a receita — só ocorre com aporte acima da despesa."""
        return not self.deficit and self.total_retido > self.receitas

    @property
    def cor_saldo(self) -> str:
        if self.deficit:
            return COR_NEGATIVO
        if self.acima_da_receita:
            return COR_APORTE
        return COR_POSITIVO

    @property
    def renda_comprometida_pct(self) -> float | None:
        """Só despesa sobre receita. ``None`` quando não há receita para dividir."""
        if self.receitas <= 0:
            return None
        return round(self.despesas / self.receitas * 100, 1)

    @property
    def taxa_poupanca_pct(self) -> float | None:
        """Sobra de caixa sobre receita — o quanto do mês não foi consumido.

        O aporte fica de fora: ele já é destino da sobra, e contá-lo dos dois
        lados faria quem investe tudo o que sobrou aparecer com poupança zero.
        """
        if self.receitas <= 0:
            return None
        return round(self.saldo_caixa / self.receitas * 100, 1)

    def descricao_saldo(self, fmt_moeda) -> str:
        """Frase do card, já explicando a cor quando ela não é a óbvia."""
        if self.deficit:
            base = "As despesas superaram a renda do mês."
        elif self.acima_da_receita:
            base = ("Sobra de caixa mais aporte: o total retido supera a renda do "
                    "mês porque o aporte foi maior que a despesa.")
        else:
            base = "Sobrou dinheiro este mês depois das despesas."
        if self.investimentos > 0:
            base += (f" Investido no período: {fmt_moeda(self.investimentos)} "
                     f"(patrimônio, não despesa).")
        return base


def fluxo_do_mes(receitas, despesas, investimentos=0.0) -> FluxoMes:
    """Construtor tolerante a ``None``/``NaN`` vindos do banco."""
    return FluxoMes(round(_num(receitas), 2), round(_num(despesas), 2),
                    round(max(_num(investimentos), 0.0), 2))


def cor_comprometimento(pct: float | None) -> str:
    """Faixas de comprometimento preservadas do comportamento anterior."""
    if pct is None:
        return COR_POSITIVO
    if pct < 60:
        return COR_POSITIVO
    if pct < 80:
        return "#F6C90E"
    return COR_NEGATIVO
