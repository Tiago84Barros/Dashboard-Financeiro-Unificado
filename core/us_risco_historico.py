"""Risco da empresa americana medido no HISTÓRICO, não numa foto.

Por que existe. Até 15/09/2026 toda red flag de `core/us_dossie.red_flags` lia
o dicionário de métricas do ÚLTIMO exercício: `net_debt_ebitda > 4`,
`interest_coverage < 2`, `_fcf < 0`. A linha emitida era a mesma — palavra por
palavra — para quem teve um ano ruim em cinco e para quem teve cinco em cinco.

Medido no armazém local sobre as 3.737 empresas elegíveis, entre as 1.578 com
"Fluxo de caixa livre negativo" no último exercício:

    só o último ano ............. 133
    2 a 4 dos últimos 5 ......... 764
    todos os 5 .................. 681

Ou seja: nos EUA o problema quase nunca é o período isolado (ao contrário da
B3, onde 94 de 426 empresas ficavam em vermelho por UM trimestre). O problema é
que a bandeira não dizia qual dos dois casos ela descrevia — e 2.419 das 3.737
empresas (65%) acendiam alguma, todas sob o mesmo cabeçalho.

Este módulo mede. Quem transforma a medição em texto e severidade é
`core/us_dossie.py`; o vocabulário de severidade é `core/severidade_flags.py`.

Puro (sem banco, sem rede). Coberto por tests/test_us_severidade_historica.py.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from core.us_metrics import _f

# Cinco exercícios: é a janela que as séries do EDGAR sustentam para a maioria
# do universo e a mesma de `revenue_trend_5y`.
JANELA_PADRAO = 5

# Abaixo de três exercícios não dá para separar episódio de padrão — e essa
# ignorância é uma LIMITAÇÃO DE COBERTURA, não um risco confirmado nem um
# atestado de saúde. A linha continua aparecendo, dizendo o que é.
MIN_ANOS_PARA_JULGAR = 3


@dataclass(frozen=True)
class Persistencia:
    """Em quantos exercícios avaliáveis da janela a condição foi observada."""

    nome: str
    acesos: int
    anos: int
    acende_no_ultimo: bool

    @property
    def maioria(self) -> bool:
        """Maioria ESTRITA da janela avaliável.

        Metade exata não é padrão: em 2 de 4 exercícios a evidência está
        empatada, e empate cai do lado de "contexto", que continua visível.
        """
        return self.anos > 0 and self.acesos * 2 > self.anos

    @property
    def julgavel(self) -> bool:
        return self.anos >= MIN_ANOS_PARA_JULGAR


def _por_ano(serie: Sequence[dict]) -> dict[int, dict]:
    """Série anual para {fiscal_year: linha}. Ano ilegível é descartado."""
    saida: dict[int, dict] = {}
    for linha in serie or []:
        try:
            ano = int(linha.get("fiscal_year"))
        except (TypeError, ValueError):
            continue
        saida[ano] = linha
    return saida


def _v(linha: dict | None, campo: str):
    return None if linha is None else _f(linha.get(campo))


def _ebitda(inc: dict | None, cfw: dict | None):
    """EBITDA do ano, com a MESMA derivação de `compute_company_metrics`."""
    valor = _v(inc, "ebitda")
    if valor is not None:
        return valor
    op = _v(inc, "operating_income")
    dep = _v(cfw, "depreciation_and_amortization")
    return None if op is None or dep is None else op + abs(dep)


def _fcf(cfw: dict | None):
    valor = _v(cfw, "free_cash_flow")
    if valor is not None:
        return valor
    ocf, capex = _v(cfw, "operating_cash_flow"), _v(cfw, "capex")
    return None if ocf is None or capex is None else ocf + capex


def _net_debt(bal: dict | None):
    valor = _v(bal, "net_debt")
    if valor is not None:
        return valor
    debt, cash = _v(bal, "total_debt"), _v(bal, "cash_and_equivalents")
    return None if debt is None or cash is None else debt - cash


def _cond_alavancagem(inc, bal, cfw) -> bool | None:
    nd, ebitda = _net_debt(bal), _ebitda(inc, cfw)
    # Denominador não positivo não produz razão ordenável — o ano sai da conta
    # em vez de virar evidência (ver div_if_den_positive, A-101).
    if nd is None or ebitda is None or ebitda <= 0:
        return None
    return nd / ebitda > 4


def _cond_cobertura_juros(inc, bal, cfw) -> bool | None:
    ebit = _v(inc, "ebit")
    if ebit is None:
        ebit = _v(inc, "operating_income")
    juros = _v(inc, "interest_expense")
    if ebit is None or not juros:
        return None
    return ebit / abs(juros) < 2


def _cond_fcf_negativo(inc, bal, cfw) -> bool | None:
    fcf = _fcf(cfw)
    return None if fcf is None else fcf < 0


def _cond_conversao_caixa(inc, bal, cfw) -> bool | None:
    fcf, lucro = _fcf(cfw), _v(inc, "net_income")
    # Só faz sentido perguntar quanto do lucro virou caixa em ano de lucro.
    if fcf is None or lucro is None or lucro <= 0:
        return None
    return fcf / lucro < 0.5


def _cond_divida_patrimonio(inc, bal, cfw) -> bool | None:
    debt, eq = _v(bal, "total_debt"), _v(bal, "total_equity")
    if debt is None or eq is None or eq <= 0:
        return None
    return debt / eq > 2


def _cond_patrimonio_negativo(inc, bal, cfw) -> bool | None:
    eq = _v(bal, "total_equity")
    return None if eq is None else eq < 0


# nome -> (condição, texto do fato). O texto NÃO traz o número do último ano de
# propósito: a grandeza que este módulo apura é a frequência no histórico, e
# misturar as duas foi o defeito que ele corrige.
CONDICOES: tuple[tuple[str, object, str], ...] = (
    ("alavancagem", _cond_alavancagem,
     "Alavancagem alta (dívida líquida/EBITDA acima de 4x)"),
    ("cobertura_juros", _cond_cobertura_juros,
     "Cobertura de juros baixa (EBIT abaixo de 2x a despesa financeira)"),
    ("fcf_negativo", _cond_fcf_negativo,
     "Fluxo de caixa livre negativo"),
    ("conversao_caixa", _cond_conversao_caixa,
     "Baixa conversão de lucro em caixa (menos de 50% do lucro)"),
    ("divida_patrimonio", _cond_divida_patrimonio,
     "Dívida/patrimônio elevada (acima de 2x)"),
    ("patrimonio_negativo", _cond_patrimonio_negativo,
     "Patrimônio líquido negativo"),
)

TEXTO_CONDICAO: dict[str, str] = {nome: texto for nome, _c, texto in CONDICOES}


def avalia(income: Sequence[dict], balance: Sequence[dict],
           cashflow: Sequence[dict], *,
           janela: int = JANELA_PADRAO) -> dict[str, Persistencia]:
    """Persistência de cada condição de risco nos últimos ``janela`` exercícios.

    A janela é definida pelos anos PRESENTES nas séries, não por um calendário:
    empresa com lacuna no meio da série é julgada pelos anos que entregou.

    Condição não avaliável num ano (denominador nulo, campo ausente) não conta
    nem a favor nem contra — ela sai do denominador daquela condição. Contar
    ausência como ano limpo inflaria a saúde de quem não reportou.
    """
    inc, bal, cfw = _por_ano(income), _por_ano(balance), _por_ano(cashflow)
    anos = sorted(set(inc) | set(bal) | set(cfw))
    if not anos:
        return {}
    recorte = anos[-max(1, int(janela)):]
    ultimo = recorte[-1]

    saida: dict[str, Persistencia] = {}
    for nome, condicao, _texto in CONDICOES:
        observados = [
            (ano, condicao(inc.get(ano), bal.get(ano), cfw.get(ano)))
            for ano in recorte
        ]
        avaliaveis = [(ano, v) for ano, v in observados if v is not None]
        if not avaliaveis:
            continue
        saida[nome] = Persistencia(
            nome=nome,
            acesos=sum(1 for _a, v in avaliaveis if v),
            anos=len(avaliaveis),
            acende_no_ultimo=any(a == ultimo and v for a, v in avaliaveis),
        )
    return saida
