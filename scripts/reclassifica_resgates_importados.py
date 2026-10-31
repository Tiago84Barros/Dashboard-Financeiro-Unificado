"""Move resgates de investimento de "Outros Rendimentos" para a categoria própria.

Por que existe
--------------
O importador de extrato lançava "RESGATE DE CDB" (e afins) como receita em
"Outros Rendimentos": 84 linhas, 16,3% da receita de 2024 e 17,1% da de 2025
(auditoria de 04/10/2026, CF-B4). Resgate é dinheiro que volta de um
investimento -- nem receita nem despesa. O importador já classifica certo; este
script corrige o que ficou gravado.

Alvo (todas as condições)
-------------------------
* ``type = 'income'``, ``amount > 0``, categoria "Outros Rendimentos";
* conta de caixa (``checking``, ``savings``, ``digital_wallet``);
* descrição com a palavra "resgate" e sem marca de rendimento/dividendo/juros.

Efeito: ``category_id`` -> "Resgate de Investimento" (a do usuário, ou a de
sistema, ``user_id IS NULL``) e ``type`` -> ``'transfer'``. O saldo da conta não
muda (``amount`` fica igual); só a receita do fluxo de caixa cai.
Idempotente: depois de movida, a linha deixa de ser ``income``/"Outros
Rendimentos". Não cria categoria: se "Resgate de Investimento" não existir, para.

Uso (raiz do projeto)::

    python scripts/reclassifica_resgates_importados.py           # só mostra
    python scripts/reclassifica_resgates_importados.py --apply   # grava
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONTAS_DE_CAIXA = ("checking", "savings", "digital_wallet")
CATEGORIA_ALVO = "Resgate de Investimento"

SQL_CANDIDATAS = f"""
SELECT t.id::text AS id, t.user_id::text AS user_id, t.due_date AS data,
       t.description AS descricao, t.amount AS valor
FROM transactions t
JOIN accounts a   ON a.id = t.account_id
JOIN categories c ON c.id = t.category_id
WHERE t.type = 'income'
  AND t.amount > 0
  AND c.name = 'Outros Rendimentos'
  AND a.type IN ({",".join(f"'{tipo}'" for tipo in CONTAS_DE_CAIXA)})
  AND t.description ILIKE '%resgate%'
ORDER BY t.due_date, t.description
"""

SQL_CATEGORIA = """
SELECT id::text FROM categories
WHERE name = :nome AND type = 'transfer'
  AND (user_id = CAST(:uid AS uuid) OR user_id IS NULL)
ORDER BY user_id IS NULL
LIMIT 1
"""

SQL_MOVE = """
UPDATE transactions
SET category_id = CAST(:cat AS uuid), type = 'transfer'
WHERE id = ANY(CAST(:ids AS uuid[]))
  AND type = 'income'
"""

_RENDIMENTO = re.compile(r"\b(rendimentos?|juros|dividendos?|proventos?)\b")


def filtrar_alvos(linhas: list[dict]) -> list[dict]:
    """Fica só o resgate; "rendimento de resgate" etc. continua receita."""
    from core.bank_statement_import import _norm

    return [
        x for x in linhas
        if re.search(r"\bresgate\b", _norm(x.get("descricao") or ""))
        and not _RENDIMENTO.search(_norm(x.get("descricao") or ""))
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true",
                        help="grava a reclassificação (sem isso, só mostra)")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    from sqlalchemy import text

    from core.database import get_engine

    engine = get_engine()
    with engine.connect() as conn:
        linhas = [dict(r._mapping) for r in conn.execute(text(SQL_CANDIDATAS))]
    alvos = filtrar_alvos(linhas)
    for a in alvos:
        print(f"  {a['data']}  {float(a['valor']):>12,.2f}  {a['descricao']}")
    print(f"\nLinhas a reclassificar: {len(alvos)}  "
          f"(descartadas por marca de rendimento: {len(linhas) - len(alvos)})")
    print(f"Receita removida: R$ {sum(float(a['valor']) for a in alvos):,.2f}")
    if not alvos:
        print("Nada a corrigir.")
        return 0
    if not args.apply:
        print("\nSimulação. Rode com --apply para gravar.")
        return 0

    movidas = 0
    with engine.begin() as conn:
        for uid in sorted({a["user_id"] for a in alvos}):
            cat = conn.execute(text(SQL_CATEGORIA), {"nome": CATEGORIA_ALVO, "uid": uid}).scalar()
            if not cat:
                print(f"Categoria '{CATEGORIA_ALVO}' (transfer) não existe para {uid}; abortando.")
                raise SystemExit(1)
            ids = [a["id"] for a in alvos if a["user_id"] == uid]
            movidas += conn.execute(text(SQL_MOVE), {"cat": cat, "ids": ids}).rowcount
    print(f"\nReclassificadas: {movidas}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
