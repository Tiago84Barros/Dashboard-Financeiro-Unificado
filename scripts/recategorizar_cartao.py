"""
scripts/recategorizar_cartao.py
Recategorização (limpeza) das transações da fatura de cartão de crédito.

As categorias importadas do CSV da operadora são as categorias "MCC" do
processador (ex.: "Elétrico", "Marketing Direto", "Associação"), genéricas e
frequentemente mal colocadas. Este script remapeia os lançamentos de cartão para
uma taxonomia enxuta de 13 categorias, decidindo por ESTABELECIMENTO (merchant)
quando a categoria da operadora é ambígua, e por categoria de origem no restante.

SEGURANÇA / REVERSIBILIDADE:
  - Só toca em transações de cartão (account.type='credit_card', source='csv').
  - Filtra sempre por OWNER_USER_ID.
  - Nunca altera transaction.type (os tipos já estão corretos no banco).
  - NÃO mexe em 'Pagamento de Cartão' nem 'Créditos e Estornos' (mapeiam para si).
  - Antes de aplicar, grava um CSV de-para (tx_id -> category_id antigo). O modo
    --revert restaura exatamente a partir desse CSV.

USO:
  python scripts/recategorizar_cartao.py            # dry-run (padrão): só relata
  python scripts/recategorizar_cartao.py --apply     # aplica (grava backup antes)
  python scripts/recategorizar_cartao.py --revert <backup.csv>   # desfaz

Requer as mesmas credenciais do app (.env / secrets) e MOCK_MODE desativado.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

from sqlalchemy import text

# Permite rodar de qualquer diretório (adiciona a raiz do projeto ao path).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# A taxonomia, as regras por estabelecimento e o fallback por categoria vivem em
# core.card_categorization (fonte única, compartilhada com o importador).

_SQL_FETCH = """
    SELECT t.id::text AS id,
           t.category_id::text AS category_id,
           COALESCE(c.name, '(sem categoria)') AS categoria,
           t.description AS descricao,
           ABS(t.amount) AS valor
    FROM   transactions t
    LEFT JOIN accounts   a ON a.id = t.account_id
    LEFT JOIN categories c ON c.id = t.category_id
    WHERE  t.user_id = CAST(:uid AS uuid)
      AND  COALESCE(a.type, '')   = 'credit_card'
      AND  COALESCE(t.source, '') = 'csv'
    ORDER BY c.name, valor DESC
"""


def _scratch_path(nome: str) -> str:
    base = os.environ.get("TEMP") or os.environ.get("TMP") or "."
    d = os.path.join(base, "claude")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, nome)


def _load(conn, owner: str) -> list[dict]:
    # Carrega após ajustar sys.path, pois este script também roda diretamente.
    from core.card_categorization import classify
    from core.controle import get_card_category_rules

    rows = conn.execute(text(_SQL_FETCH), {"uid": owner}).fetchall()
    user_rules = get_card_category_rules()   # respeita regras aprendidas pelo usuário
    out = []
    for r in rows:
        # Valor POSITIVO (convenção do CSV): compras são positivas. Assim a regra
        # estrutural "value<0 -> estorno" não dispara para despesas; pagamento e
        # estorno são detectados pelas palavras-chave na descrição.
        nova, regra = classify(r.categoria, r.descricao, float(r.valor or 0.0),
                               user_rules=user_rules)
        out.append({
            "id": r.id,
            "old_category_id": r.category_id,
            "categoria_atual": r.categoria,
            "descricao": " ".join(str(r.descricao or "").split()),
            "valor": float(r.valor or 0.0),
            "categoria_nova": nova,
            "regra": regra,
        })
    return out


def _resumo(items: list[dict]) -> None:
    from collections import defaultdict

    from core.card_categorization import REVIEW_SENTINEL

    agg = defaultdict(lambda: [0, 0.0])
    mudam = 0
    revisar = []
    for it in items:
        agg[it["categoria_nova"]][0] += 1
        agg[it["categoria_nova"]][1] += it["valor"]
        if it["categoria_nova"] != it["categoria_atual"]:
            mudam += 1
        if it["categoria_nova"] == REVIEW_SENTINEL:
            revisar.append(it)
    print(f"Total de lançamentos de cartão: {len(items)}")
    print(f"Lançamentos que MUDAM de categoria: {mudam}")
    print(f"Categorias-alvo: {len(agg)}  (antes: dispersas em ~27)")
    print("-" * 58)
    for nc, (n, tot) in sorted(agg.items(), key=lambda x: -x[1][0]):
        print(f"  {nc:<36} {n:>4} | R$ {tot:>11,.2f}")
    print("-" * 58)
    if revisar:
        print(f"ATENÇÃO — {len(revisar)} sem regra (ficariam 'A revisar'):")
        for it in revisar:
            print(f"   [{it['categoria_atual']}] {it['descricao'][:50]} R$ {it['valor']:.2f}")


def cmd_dry_run(items: list[dict]) -> None:
    _resumo(items)
    preview = _scratch_path("preview_recat_cartao.csv")
    with open(preview, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["id", "categoria_atual", "descricao", "valor", "categoria_nova", "regra"])
        for it in items:
            w.writerow([it["id"], it["categoria_atual"], it["descricao"],
                        f"{it['valor']:.2f}", it["categoria_nova"], it["regra"]])
    print(f"\nPreview salvo em: {preview}")
    print("Nada foi alterado no banco (dry-run). Use --apply para aplicar.")


def cmd_apply(engine, owner: str, items: list[dict]) -> None:
    from core.card_categorization import CATEGORY_TYPE, REVIEW_SENTINEL
    from core.controle import _get_or_create_category

    if any(it["categoria_nova"] == REVIEW_SENTINEL for it in items):
        print("ABORTADO: há lançamentos sem regra ('A revisar'). Ajuste as regras antes.")
        return

    to_change = [it for it in items if it["categoria_nova"] != it["categoria_atual"]]
    if not to_change:
        print("Nada a alterar — todas as categorias já estão no padrão.")
        return

    # 1) Backup de-para (antes de qualquer escrita).
    backup = _scratch_path("backup_recat_cartao_depara.csv")
    with open(backup, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["tx_id", "old_category_id", "old_category_name",
                    "new_category_name"])
        for it in to_change:
            w.writerow([it["id"], it["old_category_id"] or "",
                        it["categoria_atual"], it["categoria_nova"]])
    print(f"Backup de-para gravado em: {backup}")
    print("   (guarde este arquivo — é o que permite reverter 100%)")

    # 2) UPDATE em transação única.
    nomes_alvo = sorted({it["categoria_nova"] for it in to_change})
    ok = 0
    with engine.begin() as conn:
        cat_ids = {}
        for nome in nomes_alvo:
            cid = _get_or_create_category(conn, owner, nome, CATEGORY_TYPE.get(nome, "expense"))
            if not cid:
                raise RuntimeError(f"Falha ao obter/criar categoria: {nome}")
            cat_ids[nome] = cid
        for it in to_change:
            conn.execute(
                text("""
                    UPDATE transactions
                    SET    category_id = CAST(:cid AS uuid)
                    WHERE  id      = CAST(:tid AS uuid)
                      AND  user_id = CAST(:uid AS uuid)
                """),
                {"cid": cat_ids[it["categoria_nova"]], "tid": it["id"], "uid": owner},
            )
            ok += 1

    _clear_caches()
    print(f"\n✅ {ok} lançamento(s) recategorizado(s).")
    print("Categorias-alvo usadas:", ", ".join(nomes_alvo))


def cmd_revert(engine, owner: str, backup_csv: str) -> None:
    if not os.path.isfile(backup_csv):
        print(f"Arquivo de backup não encontrado: {backup_csv}")
        return
    with open(backup_csv, newline="", encoding="utf-8-sig") as f:
        linhas = list(csv.DictReader(f))
    linhas = [r for r in linhas if r.get("old_category_id")]
    if not linhas:
        print("Backup sem 'old_category_id' — nada a reverter.")
        return
    ok = 0
    with engine.begin() as conn:
        for r in linhas:
            conn.execute(
                text("""
                    UPDATE transactions
                    SET    category_id = CAST(:cid AS uuid)
                    WHERE  id      = CAST(:tid AS uuid)
                      AND  user_id = CAST(:uid AS uuid)
                """),
                {"cid": r["old_category_id"], "tid": r["tx_id"], "uid": owner},
            )
            ok += 1
    _clear_caches()
    print(f"✅ Revertido: {ok} lançamento(s) voltaram à categoria original.")


def _clear_caches() -> None:
    try:
        from core.controle import _clear_controle_caches
        _clear_controle_caches()
    except Exception:
        pass


def main() -> int:
    from core.config import settings
    from core.database import get_engine

    ap = argparse.ArgumentParser(description="Recategorização das transações de cartão de crédito.")
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--apply", action="store_true", help="Aplica as mudanças (grava backup antes).")
    grp.add_argument("--revert", metavar="BACKUP_CSV", help="Desfaz a partir de um CSV de-para.")
    args = ap.parse_args()

    if settings.MOCK_MODE:
        print("MOCK_MODE ativo — abortado (não há banco real para alterar).")
        return 1
    if not settings.has_database or not settings.OWNER_USER_ID:
        print("Banco ou OWNER_USER_ID não configurados — abortado.")
        return 1

    engine = get_engine()
    owner = settings.OWNER_USER_ID

    if args.revert:
        cmd_revert(engine, owner, args.revert)
        return 0

    with engine.connect() as conn:
        items = _load(conn, owner)

    if args.apply:
        cmd_apply(engine, owner, items)
    else:
        cmd_dry_run(items)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
