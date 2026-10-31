"""Corrige o sinal de aportes gravados como entrada na conta corrente.

Por que existe
--------------
O saldo da conta (`v_account_balance`) é ``initial_balance + SUM(amount)`` das
transações liquidadas. Uma transferência PARA investimento (aporte) tem de
entrar negativa na conta de origem. Duas portas gravaram aportes positivos:

* a migração do App 3 (`migration/04_transform_to_canonical.py`), que trouxe
  linhas como "Tesouro Direto [Renda Fixa]" e "Transferido para a Rico
  [Renda Variável]" com ``type='transfer'`` e valor positivo;
* o importador de extrato (`core/bank_statement_import.py::_direction_for`),
  que lia linha sem sinal e sem termo de saída conhecido como "entrada".
  O importador foi corrigido no mesmo PR deste script.

O fluxo de caixa e o dashboard NÃO mudam com a correção: `v_monthly_cashflow`,
as views de gasto/orçamento e o SQL de `core/investimentos.py` excluem
transferências da despesa ou leem aporte por ``ABS(amount)``. Só o saldo da
conta corrente cai -- para o valor certo.

Alvo (todas as condições)
-------------------------
* ``amount > 0`` e ``type = 'transfer'``;
* conta de origem é de caixa (``checking``, ``savings``, ``digital_wallet``) --
  linhas do cartão de crédito ("Pagamento de Cartão", estornos) ficam de fora;
* categoria de investimento (``categories.type = 'investment'`` ou nome em
  ``core.categorias.NOMES_DE_INVESTIMENTO``);
* descrição sem marca de volta do dinheiro (resgate, "recebido", rendimento,
  dividendo...) -- a mesma régua do importador.

Valores por moeda: conta em dólar (Nomad, por exemplo) tem saldo e
transações em dólar, e somar com real daria um "saldo" que não existe. O
script mostra soma e saldo previsto separados por ``accounts.currency``, sem
converter -- a previsão é exata em cada moeda e não depende de câmbio.

Idempotente: depois de invertida, a linha deixa de ser ``amount > 0`` e não é
mais alvo; o UPDATE também repete a guarda ``amount > 0``.

Uso (raiz do projeto)::

    python scripts/corrige_sinal_aportes_importados.py           # só mostra
    python scripts/corrige_sinal_aportes_importados.py --apply   # grava
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.categorias import SQL_INVESTIMENTO  # noqa: E402

CONTAS_DE_CAIXA = ("checking", "savings", "digital_wallet")

SQL_CANDIDATAS = f"""
SELECT t.id::text        AS id,
       t.user_id::text   AS user_id,
       a.name            AS conta,
       t.due_date        AS data,
       t.description     AS descricao,
       c.name            AS categoria,
       t.amount          AS valor,
       upper(a.currency) AS moeda,
       t.status          AS status,
       t.source          AS origem
FROM transactions t
JOIN accounts a        ON a.id = t.account_id
LEFT JOIN categories c ON c.id = t.category_id
WHERE t.amount > 0
  AND t.type = 'transfer'
  AND a.type IN ({",".join(f"'{tipo}'" for tipo in CONTAS_DE_CAIXA)})
  AND (c.type = 'investment' OR c.name IN ({SQL_INVESTIMENTO}))
ORDER BY t.due_date, t.description
"""

SQL_SALDO_CAIXA = f"""
SELECT upper(currency) AS moeda, COALESCE(SUM(current_balance), 0) AS saldo
FROM v_account_balance
WHERE account_type IN ({",".join(f"'{tipo}'" for tipo in CONTAS_DE_CAIXA)})
GROUP BY upper(currency)
ORDER BY 1
"""

SQL_INVERTE = """
UPDATE transactions
SET amount = -amount
WHERE id = ANY(CAST(:ids AS uuid[]))
  AND amount > 0
"""


def _volta_do_dinheiro() -> re.Pattern[str]:
    from core.bank_statement_import import _RE_TERMOS_RECEBIMENTO

    return _RE_TERMOS_RECEBIMENTO


def filtrar_alvos(linhas: list[dict]) -> list[dict]:
    """Descarta resgates e recebimentos -- dinheiro que VOLTOU para a conta."""
    from core.bank_statement_import import _norm

    recebimento = _volta_do_dinheiro()
    alvos = []
    for linha in linhas:
        norm = _norm(linha.get("descricao") or "")
        if re.search(r"\bresgate\b", norm) or recebimento.search(norm):
            continue
        alvos.append(linha)
    return alvos


def somar_por_moeda(linhas: list[dict], so_liquidadas: bool = False
                    ) -> dict[str, float]:
    """Soma de ``valor`` por ``moeda``; real e dólar nunca se misturam."""
    somas: dict[str, float] = {}
    for linha in linhas:
        if so_liquidadas and linha["status"] != "settled":
            continue
        moeda = (linha.get("moeda") or "BRL").strip().upper()
        somas[moeda] = somas.get(moeda, 0.0) + float(linha["valor"])
    return somas


def _saldos(conn) -> dict[str, float]:
    from sqlalchemy import text

    return {(r.moeda or "BRL").strip(): float(r.saldo or 0)
            for r in conn.execute(text(SQL_SALDO_CAIXA))}


def _fmt(moeda: str, valor: float) -> str:
    return f"{'R$' if moeda == 'BRL' else moeda} {valor:,.2f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true",
                        help="grava a inversão de sinal (sem isso, só mostra)")
    args = parser.parse_args(argv)
    try:  # console do Windows em cp1252 estraga os acentos das descrições
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    from sqlalchemy import text

    from core.database import get_engine

    engine = get_engine()
    with engine.connect() as conn:
        linhas = [dict(r._mapping) for r in conn.execute(text(SQL_CANDIDATAS))]
        saldos_antes = _saldos(conn)

    alvos = filtrar_alvos(linhas)
    totais = somar_por_moeda(alvos)
    liquidadas = somar_por_moeda(alvos, so_liquidadas=True)

    for a in alvos:
        print(f"  {a['data']}  {a['conta']:<18} {a['moeda'] or 'BRL':>3} "
              f"{float(a['valor']):>12,.2f}  "
              f"[{a['status']}/{a['origem']}] {a['descricao']} ({a['categoria']})")
    descartadas = len(linhas) - len(alvos)
    print(f"\nLinhas a inverter: {len(alvos)}  (descartadas como resgate/recebimento: {descartadas})")
    for moeda in sorted(set(saldos_antes) | set(totais)):
        liq = liquidadas.get(moeda, 0.0)
        antes = saldos_antes.get(moeda, 0.0)
        if moeda in totais:
            print(f"[{moeda}] Soma dos valores: {_fmt(moeda, totais[moeda])}  "
                  f"(liquidadas: {_fmt(moeda, liq)})")
        print(f"[{moeda}] Saldo das contas de caixa: {_fmt(moeda, antes)} -> "
              f"{_fmt(moeda, antes - 2 * liq)} (previsto)")

    if not alvos:
        print("Nada a corrigir.")
        return 0
    if not args.apply:
        print("\nSimulação. Rode com --apply para gravar.")
        return 0

    with engine.begin() as conn:
        n = conn.execute(text(SQL_INVERTE), {"ids": [a["id"] for a in alvos]}).rowcount
        saldos_depois = _saldos(conn)
    print(f"\nInvertidas: {n}.")
    for moeda, saldo in saldos_depois.items():
        print(f"[{moeda}] Saldo das contas de caixa agora: {_fmt(moeda, saldo)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
