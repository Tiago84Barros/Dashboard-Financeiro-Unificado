from datetime import date

from scripts.sync_dividends_to_supabase import canonical_key, plan_sync

TODAY = date(2026, 7, 24)


def _row(ticker, ex, amount, type_="RENDIMENTO", id_=None):
    return {"id": id_, "ticker": ticker, "ex_date": ex, "amount": amount,
            "type": type_, "payment_date": None, "source": "brapi.dev",
            "event_date": None}


def test_canonical_key_normaliza_amount():
    # 0.190000 (numeric do banco) e 0.19 são o mesmo provento
    a = _row("TRNT11", date(2024, 4, 15), "0.190000")
    b = _row("TRNT11", date(2024, 4, 15), "0.19")
    assert canonical_key(a) == canonical_key(b)


def test_plan_adiciona_so_local_e_remove_eco_antigo():
    local = [_row("CEBR5", date(2025, 8, 1), "1.00")]
    remote = [
        _row("CEBR5", date(2023, 5, 2), "2.00", id_=10),   # eco antigo -> remove
    ]
    plan = plan_sync(local, remote, today=TODAY)
    assert [r["ex_date"] for r in plan["to_insert"]] == [date(2025, 8, 1)]
    assert [r["id"] for r in plan["to_delete"]] == [10]
    assert not plan["kept_recent"] and not plan["kept_uncovered"]


def test_plan_preserva_recentes_e_ticker_sem_cobertura():
    local = [_row("MCRE11", date(2026, 1, 15), "0.10")]
    remote = [
        _row("MCRE11", date(2026, 1, 15), "0.10", id_=9),  # presente nos dois
        # mesmo ticker, evento recente que o local ainda não ingeriu -> preserva
        _row("MCRE11", date(2026, 7, 15), "0.11", id_=1),
        # ticker sem nenhuma linha local -> local não é autoritativo -> preserva
        _row("BTHF11", date(2020, 3, 2), "0.50", id_=2),
    ]
    plan = plan_sync(local, remote, today=TODAY, recency_days=60)
    assert not plan["to_delete"] and not plan["to_insert"]
    assert [r["id"] for r in plan["kept_recent"]] == [1]
    assert [r["id"] for r in plan["kept_uncovered"]] == [2]


def test_plan_linha_igual_nos_dois_nao_gera_acao():
    shared_local = _row("PETR4", date(2024, 6, 1), "0.75")
    shared_remote = _row("PETR4", date(2024, 6, 1), "0.750000", id_=7)
    plan = plan_sync([shared_local], [shared_remote], today=TODAY)
    assert plan == {"to_insert": [], "to_delete": [],
                    "kept_recent": [], "kept_uncovered": []}


def test_plan_ex_date_nula_no_remoto_e_preservada():
    local = [_row("XPTO11", date(2024, 1, 1), "0.30")]
    remote = [_row("XPTO11", None, "0.99", id_=3)]
    plan = plan_sync(local, remote, today=TODAY)
    assert not plan["to_delete"]
    assert [r["id"] for r in plan["kept_recent"]] == [3]
