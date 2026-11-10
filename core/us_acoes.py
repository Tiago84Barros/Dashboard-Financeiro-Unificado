"""Contagem de ações vigente para o valor de mercado derivado (EUA).

Valor de mercado derivado = preço x ações do último balanço. Dois defeitos
confirmados na auditoria de 10/11/2026, ambos por pegar o balanço "mais recente
com ações" sem olhar a idade nem o que aconteceu com a ação desde então:

* WMT: a única contagem de ações era de 31/01/2012, anterior ao desdobramento
  3:1 de 26/02/2024. O preço de hoje (pós-split) vezes as ações de 2012
  (pré-split) dava ~1/3 do valor real, e o P/L saía 16 contra 45.
* Qualquer empresa sem balanço recente (deslistada, parser sem a tag) ficava
  com um valor de mercado "vivo" montado sobre um número de anos atrás.

Regras (determinísticas, uma só função para o app e para o ETL):

1. Idade máxima de ``MAX_IDADE_ACOES_DIAS`` (18 meses) entre a data de
   referência do balanço e a data do preço. Acima disso: sem valor (``None``),
   nunca um número velho com cara de atual.
2. Os preços do armazém são retroajustados por desdobramento. As ações do
   balanço são da época. Para pôr as duas na mesma base, multiplica-se pelo
   fator de todo desdobramento POSTERIOR à entrega do balanço
   (``market_us.splits``: numerador/denominador, 3:1 = 3).
3. Desdobramento entre a data de referência e a entrega do balanço é ambíguo
   (a capa do 10-K traz a contagem de uma data nesse intervalo): sem como saber
   se a contagem já está pós-split, o balanço é descartado, não adivinhado.

Unidade: ações (unidades). Ausência permanece ausência.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Iterable, Optional, Sequence

#: 18 meses em dias (30,4 dias por mês, arredondado para cima).
MAX_IDADE_ACOES_DIAS = 548


def _como_data(valor) -> Optional[date]:
    if valor is None or valor == "":
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


def _positivo(valor) -> Optional[float]:
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return None
    return v if v == v and v > 0 else None


def _lista_splits(splits: Optional[Iterable]) -> list[tuple[date, float]]:
    """``[(data, fator)]`` com fator = numerador / denominador, só os válidos."""
    saida: list[tuple[date, float]] = []
    for s in splits or ():
        if isinstance(s, dict):
            dia, num, den = s.get("split_date"), s.get("numerator"), s.get("denominator")
        else:
            dia, num, den = s[0], s[1], s[2]
        dia, num, den = _como_data(dia), _positivo(num), _positivo(den)
        if dia is not None and num is not None and den is not None:
            saida.append((dia, num / den))
    return saida


def acoes_na_base_do_preco(acoes, reference_date, available_at, data_preco,
                           splits: Optional[Sequence] = None) -> Optional[float]:
    """Ações do balanço, na base do preço de ``data_preco``; ``None`` se não vale.

    ``splits`` aceita tuplas ``(split_date, numerator, denominator)`` ou dicts
    com essas chaves, só os do símbolo em questão.
    """
    n = _positivo(acoes)
    ref, preco_em = _como_data(reference_date), _como_data(data_preco)
    if n is None or ref is None or preco_em is None:
        return None
    if (preco_em - ref).days > MAX_IDADE_ACOES_DIAS:
        return None
    entrega = _como_data(available_at) or ref
    fator = 1.0
    for dia, f in _lista_splits(splits):
        if ref < dia <= entrega:
            return None          # ambíguo: ver regra 3 do módulo
        if dia > entrega:
            fator *= f
    return n * fator


def ultimas_acoes(balance_rows, data_preco, splits: Optional[Sequence] = None
                  ) -> Optional[float]:
    """Ações vigentes: o balanço mais recente (por ``fiscal_year``) que vale.

    ``balance_rows`` em ordem crescente de exercício, com ``shares_outstanding``,
    ``reference_date`` e ``available_at``. Sem ``data_preco`` não há como medir
    a idade: devolve ``None`` (falha fechada).
    """
    if _como_data(data_preco) is None:
        return None
    for r in reversed(list(balance_rows or [])):
        if r.get("shares_outstanding") is None:
            continue
        valor = acoes_na_base_do_preco(
            r.get("shares_outstanding"), r.get("reference_date"),
            r.get("available_at"), data_preco, splits)
        if valor is not None:
            return valor
        if _positivo(r.get("shares_outstanding")) is not None and \
                _como_data(r.get("reference_date")) is not None and \
                (_como_data(data_preco) - _como_data(r.get("reference_date"))
                 ).days > MAX_IDADE_ACOES_DIAS:
            return None          # o mais recente já é velho; os demais também
    return None
