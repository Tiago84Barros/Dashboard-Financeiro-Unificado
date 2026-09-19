"""Indicadores mensais de consumo, separados da alocação em investimentos.

Entradas: totais em BRL do mesmo período; receitas/despesas não negativos,
aportes líquidos positivos (aplicação) ou negativos (resgate). Não agregue
transações aqui: a origem já classifica investimentos fora das despesas.
Saldo/poupança medem renda não consumida, não saldo bancário disponível.
"""
from decimal import Decimal, InvalidOperation


def indicadores_caixa(receitas: float, despesas: float, aportes: float = 0) -> dict:
    try:
        r, d, a = (Decimal(str(value)) for value in (receitas, despesas, aportes))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Totais financeiros ausentes ou inválidos.") from exc
    if not all(value.is_finite() for value in (r, d, a)) or r < 0 or d < 0:
        raise ValueError("Receitas e despesas devem ser totais finitos não negativos.")
    saldo = r - d
    status = ("deficit" if saldo < 0 else
              "aportes_excedem_sobra" if d + a > r else
              "superavit" if saldo > 0 else "equilibrio")
    return {
        "saldo": float(saldo),
        "comprometido_pct": float(d / r * 100) if r > 0 else None,
        "poupanca_pct": float(saldo / r * 100) if r > 0 else None,
        "status": status,
    }
