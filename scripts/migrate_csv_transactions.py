# -*- coding: utf-8 -*-
"""
scripts/migrate_csv_transactions.py

Migra transacoes do CSV exportado do App de Controle Financeiro isolado
para a tabela `transactions` do Dashboard Financeiro Unificado (App 4).

Pre-requisitos (.env ou variaveis de ambiente):
    SUPABASE_UNIFICADO_URL=postgresql://...
    OWNER_USER_ID=<uuid-do-usuario-no-app4>

Uso:
    python scripts/migrate_csv_transactions.py \\
        --csv "C:/Users/Tiago Barros/Downloads/transactions_rows.csv" \\
        [--dry-run]    # simula sem inserir
        [--all-users]  # importa 2o usuario do CSV tambem (padrao: so principal)
        [--verbose]    # lista transacoes puladas por duplicidade
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

# Script standalone -- nao importa nada de core/ (evita dependencia do Streamlit)
_ROOT = Path(__file__).resolve().parent.parent

# Carrega .env do projeto
try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except ImportError:
    pass

# Carrega secrets.toml do Streamlit como fallback
try:
    import toml as _toml
    _sp = _ROOT / ".streamlit" / "secrets.toml"
    if _sp.exists():
        for k, v in _toml.load(_sp).items():
            if isinstance(v, str) and k not in os.environ:
                os.environ[k] = v
except Exception:
    pass

# ---------------------------------------------------------------------------
# Mapeamentos
# ---------------------------------------------------------------------------

_TYPE_MAP: dict[str, str] = {
    "entrada":      "income",
    "saida":        "expense",
    "investimento": "investment",
}

_CAT_TYPE: dict[str, str] = {
    "Salario":             "income",
    "Salário":             "income",
    "Renda Extra":         "income",
    "Reembolso":           "income",
    "Renda Variavel":      "investment",
    "Renda Variável":      "investment",
    "Renda Fixa":          "investment",
    "Exterior":            "investment",
    "Internet":            "expense",
    "Saude":               "expense",
    "Saúde":               "expense",
    "Financiamento":       "expense",
    "Despesas Domesticas": "expense",
    "Despesas Domésticas": "expense",
    "Educacao":            "expense",
    "Educação":            "expense",
    "Condominio":          "expense",
    "Condomínio":          "expense",
    "Pagamento de Cartao": "expense",
    "Pagamento de Cartão": "expense",
    "Combustivel":         "expense",
    "Combustível":         "expense",
    "Mercado":             "expense",
    "Luz":                 "expense",
    "Assinaturas":         "expense",
    "Transporte":          "expense",
    "Compras":             "expense",
    "Lazer":               "expense",
    "Outros":              "expense",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_db_url() -> str:
    for key in ("SUPABASE_UNIFICADO_URL", "DATABASE_URL", "SUPABASE_DB_URL"):
        v = os.getenv(key, "").strip()
        if v:
            return v
    raise RuntimeError(
        "Nenhuma URL de banco configurada.\n"
        "Defina SUPABASE_UNIFICADO_URL no arquivo .env do projeto."
    )


def _account_name(payment_type: str, card_name: str) -> str:
    pt = (payment_type or "").strip()
    cn = (card_name or "").strip()
    if pt == "Cartao de credito" or pt == "Cartão de crédito":
        return f"Cartao {cn}" if cn else "Cartao"
    # Conta e Pix -> conta principal
    return "Conta"


def _get_or_create_category(conn, owner_uid: str, name: str,
                             cache: dict, dry_run: bool) -> str | None:
    if name in cache:
        return cache[name]
    row = conn.execute(
        text("""
            SELECT id::text FROM categories
            WHERE name = :name AND (user_id = :uid OR user_id IS NULL)
            LIMIT 1
        """),
        {"name": name, "uid": owner_uid},
    ).fetchone()
    if row:
        cache[name] = row[0]
        return row[0]
    cat_type = _CAT_TYPE.get(name, "expense")
    print(f"    [+] Nova categoria: {name!r} (type={cat_type})")
    if not dry_run:
        r = conn.execute(
            text("INSERT INTO categories (user_id, name, type)"
                 " VALUES (:uid, :name, :type) RETURNING id::text"),
            {"uid": owner_uid, "name": name, "type": cat_type},
        ).fetchone()
        cid = r[0] if r else None
        cache[name] = cid
        return cid
    cache[name] = None
    return None


def _get_or_create_account(conn, owner_uid: str, name: str,
                            cache: dict, dry_run: bool) -> str | None:
    if name in cache:
        return cache[name]
    # Search by exact name first, then by alias (e.g. "Conta" -> "Conta Corrente")
    name_lower = name.lower()
    candidates = [name]
    if name_lower == "conta":
        candidates.append("Conta Corrente")
    for candidate in candidates:
        row = conn.execute(
            text("SELECT id::text FROM accounts"
                 " WHERE name = :name AND user_id = :uid LIMIT 1"),
            {"name": candidate, "uid": owner_uid},
        ).fetchone()
        if row:
            cache[name] = row[0]
            return row[0]
    acct_type = (
        "credit_card"
        if "cartao" in name_lower or "cartão" in name_lower
        else "checking"
    )
    print(f"    [+] Nova conta: {name!r} (type={acct_type})")
    if not dry_run:
        r = conn.execute(
            text(
                "INSERT INTO accounts (user_id, name, type, initial_balance, currency, active)"
                " VALUES (:uid, :name, :type, 0.00, 'BRL', true) RETURNING id::text"
            ),
            {"uid": owner_uid, "name": name, "type": acct_type},
        ).fetchone()
        aid = r[0] if r else None
        cache[name] = aid
        return aid
    cache[name] = None
    return None


def _load_existing(conn, owner_uid: str) -> tuple[set[tuple], dict[tuple, int], dict]:
    """
    Retorna:
      existing_desc_set   — fingerprints por (desc, data, valor) para dedup exato
      existing_da_counts  — contagem de transacoes por (data_str, abs_valor) no banco
      date_range          — {"min": str, "max": str} do banco
    """
    rows = conn.execute(
        text("SELECT description, due_date, ABS(amount)"
             " FROM transactions WHERE user_id = :uid"),
        {"uid": owner_uid},
    ).fetchall()

    from collections import Counter
    desc_set: set[tuple]          = set()
    da_counts: dict[tuple, int]   = Counter()
    dates: list[str]              = []

    for r in rows:
        desc_norm = (r[0] or "").lower().strip()
        date_str  = str(r[1])
        abs_val   = round(float(r[2] or 0), 2)
        desc_set.add((desc_norm, date_str, abs_val))
        da_counts[(date_str, abs_val)] += 1
        dates.append(date_str)

    date_range = {
        "min": min(dates) if dates else "?",
        "max": max(dates) if dates else "?",
    }
    return desc_set, da_counts, date_range

# ---------------------------------------------------------------------------
# Migracao principal
# ---------------------------------------------------------------------------

def run_migration(csv_path: str, dry_run: bool,
                  include_all_users: bool, verbose: bool) -> None:
    tag = "[DRY RUN] " if dry_run else ""
    print(f"\n{tag}=== Migracao de Transacoes CSV -> App 4 ===")
    print(f"Arquivo : {csv_path}")
    print(f"Dry run : {dry_run}")
    print()

    # -- CSV ------------------------------------------------------------------
    df = pd.read_csv(csv_path, dtype=str)
    df.columns = df.columns.str.strip()
    for col in ("payment_type", "card_name", "installments", "description", "category"):
        if col not in df.columns:
            df[col] = ""
    df["installments"] = pd.to_numeric(df["installments"], errors="coerce").fillna(1).astype(int)
    df["card_name"]    = df["card_name"].fillna("").astype(str)
    df["payment_type"] = df["payment_type"].fillna("Conta").astype(str)
    df["description"]  = df["description"].fillna("").astype(str)
    df["category"]     = df["category"].fillna("Outros").astype(str)
    df["amount"]       = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)

    print(f"Linhas no CSV           : {len(df)}")

    uid_counts   = df["user_id"].value_counts()
    main_uid_csv = uid_counts.index[0]
    all_uids_csv = set(uid_counts.index)
    print("user_ids no CSV         :")
    for uid, cnt in uid_counts.items():
        flag = " <-- principal" if uid == main_uid_csv else ""
        print(f"  {uid}: {cnt} transacoes{flag}")

    uids_sel = all_uids_csv if include_all_users else {main_uid_csv}
    df_sel   = df[df["user_id"].isin(uids_sel)].copy()
    print(f"Transacoes selecionadas : {len(df_sel)}\n")

    # -- Banco ----------------------------------------------------------------
    db_url    = _resolve_db_url()
    owner_uid = os.getenv("OWNER_USER_ID", "").strip()
    if not owner_uid:
        raise RuntimeError("OWNER_USER_ID nao configurado no .env")

    masked = db_url[:55] + "..." if len(db_url) > 55 else db_url
    print(f"Banco         : {masked}")
    print(f"OWNER_USER_ID : {owner_uid}\n")

    kw: dict = {"pool_pre_ping": True}
    if not db_url.startswith("sqlite"):
        kw.update({
            "pool_size": 2,
            "max_overflow": 1,
            "connect_args": {"connect_timeout": 15, "sslmode": "require"},
        })
    engine = create_engine(db_url, **kw)

    from collections import Counter

    cat_cache:  dict[str, str | None] = {}
    acct_cache: dict[str, str | None] = {}
    to_insert:  list[dict]            = []
    skipped_dup   = 0
    skipped_type  = 0
    log_dups: list[str] = []

    with engine.begin() as conn:
        existing_desc, existing_da, date_range = _load_existing(conn, owner_uid)
        n_existing = sum(existing_da.values())
        print(f"Transacoes ja no banco  : {n_existing}")
        print(f"Periodo no banco        : {date_range['min']} a {date_range['max']}\n")
        print("Processando linhas do CSV...")

        # Contagem de ocorrencias de cada (data, valor) no CSV para dedup preciso
        csv_da_counts: dict[tuple, int] = Counter()
        for _, row in df_sel.iterrows():
            if _TYPE_MAP.get(str(row.get("type", "")).strip().lower()):
                csv_da_counts[(str(row["date"]).strip(),
                               round(float(row["amount"]), 2))] += 1

        # Rastreia quantas de cada (data, valor) ja processamos do CSV
        csv_da_seen: dict[tuple, int] = Counter()

        for _, row in df_sel.iterrows():
            csv_type = str(row.get("type", "")).strip().lower()
            db_type  = _TYPE_MAP.get(csv_type)
            if not db_type:
                skipped_type += 1
                continue

            raw_amount = round(float(row["amount"]), 2)
            amount     = raw_amount if db_type == "income" else -raw_amount
            desc_base  = row["description"].strip()
            installments = int(row["installments"])
            desc_full  = f"{desc_base} ({installments}x)" if installments > 1 else desc_base
            date_str   = str(row["date"]).strip()

            da_key = (date_str, raw_amount)
            csv_da_seen[da_key] += 1

            # DEDUP PRIMARIO: descrição exata + data + valor (captura migracoes anteriores)
            fp_desc = (desc_base.lower().strip(), date_str, raw_amount)
            if fp_desc in existing_desc:
                skipped_dup += 1
                log_dups.append(f"  [desc] SKIP | {date_str} | R${raw_amount:>10.2f} | {desc_base[:50]}")
                continue

            # DEDUP SECUNDARIO: (data, valor) com contagem — cobre descricoes diferentes
            # Ex: DB tem 1 transacao de R$226.40 em 2026-01-06 e CSV tem 2 (Salinas1 + Salinas2)
            # A 1a ocorrencia do CSV e pulada; a 2a e inserida
            if csv_da_seen[da_key] <= existing_da.get(da_key, 0):
                skipped_dup += 1
                log_dups.append(f"  [data+val] SKIP | {date_str} | R${raw_amount:>10.2f} | {desc_base[:50]}")
                continue

            cat_name  = row["category"].strip() or "Outros"
            acct_name = _account_name(row["payment_type"], row["card_name"])

            cat_id  = _get_or_create_category(conn, owner_uid, cat_name,  cat_cache,  dry_run)
            acct_id = _get_or_create_account( conn, owner_uid, acct_name, acct_cache, dry_run)

            to_insert.append({
                "uid":         owner_uid,
                "account_id":  acct_id,
                "category_id": cat_id,
                "description": desc_full,
                "amount":      amount,
                "due_date":    date_str,
                "type":        db_type,
            })

        # -- Resumo -----------------------------------------------------------
        print(f"\n{'='*60}")
        print("RESUMO")
        print(f"{'='*60}")
        print(f"Total selecionado do CSV : {len(df_sel)}")
        print(f"Ja existem (pulados)     : {skipped_dup}")
        print(f"Tipo invalido (pulados)  : {skipped_type}")
        print(f"A inserir                : {len(to_insert)}")

        if verbose and log_dups:
            print(f"\nTransacoes puladas ({skipped_dup}):")
            for line in log_dups:
                print(line)

        if to_insert:
            df_ins = pd.DataFrame(to_insert)
            df_ins["mes"] = pd.to_datetime(df_ins["due_date"]).dt.to_period("M").astype(str)
            por_mes = (
                df_ins.groupby(["mes", "type"])
                .agg(qtd=("amount", "count"), total=("amount", lambda s: s.abs().sum()))
                .reset_index()
            )
            print("\nDistribuicao por mes e tipo:")
            print(f"  {'Mes':<10} {'Tipo':<12} {'Qtd':>4}  {'Total R$':>13}")
            print(f"  {'-'*44}")
            for _, mr in por_mes.iterrows():
                print(f"  {mr['mes']:<10} {mr['type']:<12} {mr['qtd']:>4}  {mr['total']:>13,.2f}")

        new_cats  = sorted(cat_cache.keys())
        new_accts = sorted(acct_cache.keys())
        if new_cats:
            print(f"\nCategorias necessarias : {new_cats}")
        if new_accts:
            print(f"Contas necessarias     : {new_accts}")

        # -- Insert -----------------------------------------------------------
        if dry_run:
            print(f"\n{tag}Simulacao concluida. Remova --dry-run para inserir no banco.")
            return

        if not to_insert:
            print("\nOK: nada novo para inserir -- banco ja esta atualizado.")
            return

        inserted = 0
        for params in to_insert:
            conn.execute(
                text("""
                    INSERT INTO transactions
                        (user_id, account_id, category_id, description,
                         amount, due_date, type, status, source)
                    VALUES
                        (:uid, :account_id, :category_id, :description,
                         :amount, :due_date, :type, 'settled', 'csv_migration')
                """),
                params,
            )
            inserted += 1

        print(f"\nOK: {inserted} transacoes inseridas! (source='csv_migration')")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="Migra CSV de transacoes para o App 4")
    p.add_argument("--csv",       required=True, help="Caminho para transactions_rows.csv")
    p.add_argument("--dry-run",   action="store_true", help="Simula sem inserir")
    p.add_argument("--all-users", action="store_true",
                   help="Importa todos os user_ids do CSV (padrao: so o principal)")
    p.add_argument("--verbose",   action="store_true",
                   help="Lista transacoes puladas por duplicidade")
    args = p.parse_args()
    run_migration(
        csv_path=args.csv,
        dry_run=args.dry_run,
        include_all_users=args.all_users,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
