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
SELECT COALESCE(SUM(current_balance), 0)
FROM v_account_balance
WHERE account_type IN ({",".join(f"'{tipo}'" for tipo in CONTAS_DE_CAIXA)})
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
        saldo_antes = float(conn.execute(text(SQL_SALDO_CAIXA)).scalar() or 0)

    alvos = filtrar_alvos(linhas)
    total = sum(float(a["valor"]) for a in alvos)
    liquidadas = sum(float(a["valor"]) for a in alvos if a["status"] == "settled")

    for a in alvos:
        print(f"  {a['data']}  {a['conta']:<18} {float(a['valor']):>12,.2f}  "
              f"[{a['status']}/{a['origem']}] {a['descricao']} ({a['categoria']})")
    descartadas = len(linhas) - len(alvos)
    print(f"\nLinhas a inverter: {len(alvos)}  (descartadas como resgate/recebimento: {descartadas})")
    print(f"Soma dos valores: R$ {total:,.2f}  (liquidadas: R$ {liquidadas:,.2f})")
    print(f"Saldo das contas de caixa: R$ {saldo_antes:,.2f} -> "
          f"R$ {saldo_antes - 2 * liquidadas:,.2f} (previsto)")

    if not alvos:
        print("Nada a corrigir.")
        return 0
    if not args.apply:
        print("\nSimulação. Rode com --apply para gravar.")
        return 0

    with engine.begin() as conn:
        n = conn.execute(text(SQL_INVERTE), {"ids": [a["id"] for a in alvos]}).rowcount
        saldo_depois = float(conn.execute(text(SQL_SALDO_CAIXA)).scalar() or 0)
    print(f"\nInvertidas: {n}. Saldo das contas de caixa agora: R$ {saldo_depois:,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
