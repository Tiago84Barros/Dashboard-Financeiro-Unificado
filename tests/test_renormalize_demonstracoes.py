"""renormalize_demonstracoes: o payload arquivado no armazém vira demonstração.

Em 04/10/2026 o armazém tinha o payload de 19/09 da BBDC4 (com o 2T26) e as
demonstrações dela ainda saíam do payload de julho; o ``renormalize`` comum não
resolvia porque lê o payload mais novo, que é a cotação diária, só preço.
"""
import sys

from data_pipeline.market import ingest as ig


class _Result:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, payload_rows):
        self._payload_rows = payload_rows
        self.executed: list[tuple[str, dict]] = []

    def execute(self, clause, params=None):
        sql = str(clause)
        self.executed.append((sql, params or {}))
        if "brapi_raw_payloads" in sql:
            return _Result(self._payload_rows)
        return _Result([])

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeEngine:
    def __init__(self, conn):
        self._conn = conn

    def connect(self):
        return self._conn

    def begin(self):
        return self._conn


def _data(sym, com_demonstracao=True):
    linha = {"ticker": sym, "period": "quarterly", "year": 2026, "quarter": 2}
    return {
        "companies": [{"codigo_cvm": 906}],
        "assets": [{"ticker": sym}],
        "historical_prices": [{"ticker": sym, "date": "2026-09-19", "close": 15}],
        "income_statements": [dict(linha)] if com_demonstracao else [],
        "balance_sheets": [dict(linha)] if com_demonstracao else [],
        "cash_flow_statements": [dict(linha)] if com_demonstracao else [],
        "dividends": [{"ticker": sym, "amount": 1.0}],
        "calculated_metrics": [{"ticker": sym, "metric_name": "P_L"}],
    }


def _patch(monkeypatch, payload_rows, por_ticker):
    conn = _FakeConn(payload_rows)
    upserts: dict[str, list] = {}
    monkeypatch.setattr(ig, "_engine", lambda: _FakeEngine(conn))
    monkeypatch.setattr(ig.repo, "reset_db_cols_cache", lambda: None)
    monkeypatch.setattr(ig.repo, "schema_exists", lambda c: True)

    def _upsert(c, table, rows):
        upserts.setdefault(table, []).extend(rows or [])
        return len(rows or [])
    monkeypatch.setattr(ig.repo, "upsert", _upsert)
    monkeypatch.setattr(ig.nz, "normalize_all", lambda quote: por_ticker[quote["symbol"]])
    return conn, upserts


def test_grava_so_as_tres_demonstracoes_com_a_procedencia(monkeypatch):
    rows = [("BBDC4", 99879, {"symbol": "BBDC4"})]
    _, upserts = _patch(monkeypatch, rows, {"BBDC4": _data("BBDC4")})

    prog = ig.renormalize_demonstracoes()

    assert set(upserts) == {"income_statements", "balance_sheets", "cash_flow_statements"}
    for linhas in upserts.values():
        assert [(r["ticker"], r["raw_payload_id"]) for r in linhas] == [("BBDC4", 99879)]
    assert prog["atualizados"] == ["BBDC4"] and prog["erros"] == 0


def test_payload_sem_linha_nao_conta_como_atualizado(monkeypatch):
    rows = [("BBDC4", 1, {"symbol": "BBDC4"})]
    _patch(monkeypatch, rows, {"BBDC4": _data("BBDC4", com_demonstracao=False)})

    prog = ig.renormalize_demonstracoes()

    assert prog["atualizados"] == [] and prog["tickers"] == 1


def test_grava_sob_o_ticker_requisitado_e_nao_o_da_brapi(monkeypatch):
    rows = [("ELET3", 7, {"symbol": "AXIA3"})]
    _, upserts = _patch(monkeypatch, rows, {"AXIA3": _data("AXIA3")})

    prog = ig.renormalize_demonstracoes()

    assert all(r["ticker"] == "ELET3" for r in upserts["income_statements"])
    assert prog["atualizados"] == ["ELET3"]


def test_escolhe_o_payload_mais_novo_que_traz_demonstracao(monkeypatch):
    conn, _ = _patch(monkeypatch, [], {})

    ig.renormalize_demonstracoes(["bbdc4.sa"])

    sql, params = next((s, p) for s, p in conn.executed if "brapi_raw_payloads" in s)
    # cotação diária (só preço) não entra
    assert "?| array['incomeStatementHistoryQuarterly'" in sql
    # o arquivamento dá id novo a payload antigo: a idade é fetched_at
    assert "ORDER BY ticker, fetched_at DESC, id DESC" in sql
    # o que já saiu deste payload não é regravado
    assert "s.raw_payload_id = u.id" in sql
    assert params == {"tks": ["BBDC4"]}


def test_cli_recalcula_indicadores_so_dos_atualizados(monkeypatch):
    import run_market_ingest as cli

    chamadas = []
    monkeypatch.setattr(ig, "renormalize_demonstracoes",
                        lambda t, lim, forcar=False: {**ig._new_progress(), "atualizados": ["BBDC4"]})
    monkeypatch.setattr(ig, "reprocess_metrics",
                        lambda t, lim=None: chamadas.append(t) or {"indicadores": 12, "erros": 0})
    monkeypatch.setattr(sys, "argv", ["run_market_ingest.py", "renormalize-demonstracoes"])

    assert cli.main() == 0
    assert chamadas == [["BBDC4"]]


def test_cli_sem_atualizado_nao_recalcula(monkeypatch):
    import run_market_ingest as cli

    chamadas = []
    monkeypatch.setattr(ig, "renormalize_demonstracoes",
                        lambda t, lim, forcar=False: {**ig._new_progress(), "atualizados": []})
    monkeypatch.setattr(ig, "reprocess_metrics",
                        lambda t, lim=None: chamadas.append(t) or {})
    monkeypatch.setattr(sys, "argv", ["run_market_ingest.py", "renormalize-demonstracoes"])

    assert cli.main() == 0
    assert chamadas == []


def test_forcar_regrava_o_que_ja_saiu_do_payload(monkeypatch):
    # Correção do normalizador não muda o payload: sem forçar, a linha gravada
    # dele nunca seria reescrita.
    conn, _ = _patch(monkeypatch, [], {})

    ig.renormalize_demonstracoes(["VIVA3"], forcar=True)

    sql = next(s for s, _ in conn.executed if "brapi_raw_payloads" in s)
    assert "s.raw_payload_id = u.id" not in sql
    assert "ORDER BY ticker, fetched_at DESC, id DESC" in sql


def test_cli_repassa_forcar(monkeypatch):
    import run_market_ingest as cli

    vistos = []
    monkeypatch.setattr(ig, "renormalize_demonstracoes",
                        lambda t, lim, forcar=False: vistos.append(forcar)
                        or {**ig._new_progress(), "atualizados": []})
    monkeypatch.setattr(sys, "argv", ["run_market_ingest.py",
                                      "renormalize-demonstracoes", "--forcar"])

    assert cli.main() == 0
    assert vistos == [True]
