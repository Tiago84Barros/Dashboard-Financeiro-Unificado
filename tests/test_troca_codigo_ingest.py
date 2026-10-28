"""Troca de código B3 na ingestão: o histórico tem um dono só.

ELET3 → AXIA3, CCRO3 → MOTV3, ARZZ3 → AZZA3... O universo listava os dois
códigos, a ingestão reativava os dois em market.assets e a aba de portfólio
contava a mesma companhia duas vezes. Aqui:

* sem banco: a ingestão grava sob o código atual quando a troca é provada
  (mesma companhia e classe) e só REGISTRA a troca como pendente — mover o
  histórico antigo é do script, com aprovação; pula o código já substituído
  antes da rede e mantém o caminho legado para o que não é troca (AZUL4 →
  AZUL3 é classe);
* com banco (armazém local, banco descartável): ``unificar`` em dry-run não
  grava, aplicado deixa um ativo por (companhia, classe), é idempotente e não
  mistura classes nem companhias.
"""
from __future__ import annotations

import copy
import uuid

import pytest
from sqlalchemy import create_engine, text

from core.b3_troca_codigo import MOTIVO_TROCA, MOTIVO_TROCA_PENDENTE, mapa_de_trocas
from data_pipeline.market import ingest as ig
from data_pipeline.market import repository as repo
from data_pipeline.market import troca_codigo as tc

CVM = {"ELET3": 2437, "AXIA3": 2437, "ELET6": 2437}


def _data(sym, precos=(("2025-01-02", 40.0),)):
    return {
        "companies": [{"codigo_cvm": None, "name": "Eletrobras"}],
        "assets": [{"ticker": sym, "asset_type": "stock", "exchange": "B3",
                    "currency": "BRL", "is_active": True}],
        "historical_prices": [{"ticker": sym, "date": d, "close": c} for d, c in precos],
        "income_statements": [], "balance_sheets": [], "cash_flow_statements": [],
        "dividends": [], "calculated_metrics": [],
    }


# ── Sem banco ────────────────────────────────────────────────────────────────

class _Result:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchall(self):
        return self._rows

    def scalar(self):
        return None


class _Conn:
    def __init__(self):
        self.executed: list[tuple[str, dict]] = []

    def execute(self, clause, params=None):
        self.executed.append((str(clause), params or {}))
        return _Result()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Engine:
    def __init__(self, conn):
        self.conn = conn

    def begin(self):
        return self.conn

    def connect(self):
        return self.conn


@pytest.fixture
def falso(monkeypatch):
    """ingest_ticker com brapi e repositório trocados por gravadores; a
    migração do histórico (``unificar``) não pode ser chamada pela ingestão."""
    conn = _Conn()
    upserts: dict[str, list] = {}

    def _upsert(c, table, rows):
        upserts.setdefault(table, []).extend(copy.deepcopy(rows or []))
        return len(rows or [])

    monkeypatch.setattr(ig.repo, "upsert", _upsert)
    monkeypatch.setattr(ig.repo, "save_raw_payload", lambda *a, **k: 7)
    monkeypatch.setattr(ig.repo, "company_id_by_codigo", lambda c, cod: 1)
    monkeypatch.setattr(ig.repo, "append_metric_vintages", lambda c, rows: 0)
    monkeypatch.setattr(tc, "unificar", lambda *a, **k: pytest.fail(
        "a ingestão não migra o histórico: só o script, com aprovação"))
    return conn, upserts


def _ingerir(monkeypatch, conn, tk, data, cvm, trocas=None):
    monkeypatch.setattr(ig.sched, "with_backoff", lambda fetch, **k: {"results": [{}]})
    monkeypatch.setattr(ig.nz, "normalize_all", lambda q: copy.deepcopy(data))
    prog = ig._new_progress()
    ig.ingest_ticker(_Engine(conn), tk, range_="1mo", full=False, cvm_map=cvm,
                     prog=prog, trocas=trocas)
    return prog


def _alias_legado(conn):
    return [p for s, p in conn.executed
            if "INSERT INTO market.ticker_alias" in s and "s" in p]


def _alias_pendente(conn):
    return [{k: p[k] for k in ("a", "n", "c", "m")} for s, p in conn.executed
            if "INSERT INTO market.ticker_alias" in s and p.get("m") == MOTIVO_TROCA_PENDENTE]


def test_troca_provada_grava_sob_o_codigo_atual_e_fica_pendente(monkeypatch, falso):
    conn, upserts = falso
    prog = _ingerir(monkeypatch, conn, "ELET3", _data("AXIA3"), CVM)

    for tabela in ("assets", "historical_prices"):
        assert {r["ticker"] for r in upserts[tabela]} == {"AXIA3"}, tabela
    assert upserts["companies"][0]["codigo_cvm"] == 2437
    # o histórico do ELET3 não muda de dono aqui (AGENTS.md: migração só com
    # confirmação humana): a troca fica registrada, à espera do --apply
    assert prog["trocas_pendentes"] == 1
    assert _alias_pendente(conn) == [
        {"a": "ELET3", "n": "AXIA3", "c": 2437, "m": MOTIVO_TROCA_PENDENTE}]
    # nem o alias legado invertido (brapi_symbol = novo)
    assert not _alias_legado(conn)


def test_codigo_substituido_e_pulado_antes_da_rede(monkeypatch, falso):
    conn, upserts = falso

    def _rede(*a, **k):
        raise AssertionError("código substituído não pode ir à brapi")

    monkeypatch.setattr(ig.sched, "with_backoff", _rede)
    prog = ig._new_progress()
    ig.ingest_ticker(_Engine(conn), "ELET3", range_="1mo", full=False, cvm_map=CVM,
                     prog=prog, trocas={"ELET3": "AXIA3"})
    assert prog["codigos_substituidos"] == 1
    assert prog["tickers"] == 0
    assert not upserts and not conn.executed


def test_troca_de_classe_nao_e_troca_de_codigo(monkeypatch, falso):
    # AZUL4 → AZUL3: a brapi devolve a ON para a PN. Mesma companhia, classe
    # diferente — continua o caminho legado, PN gravada sob AZUL4.
    conn, upserts = falso
    _ingerir(monkeypatch, conn, "AZUL4", _data("AZUL3"), {"AZUL4": 24112, "AZUL3": 24112})
    assert {r["ticker"] for r in upserts["assets"]} == {"AZUL4"}
    assert not _alias_pendente(conn)
    assert _alias_legado(conn)[0] == {"s": "AZUL3", "b": "AZUL4", "c": 24112}


def test_companhia_diferente_nao_e_troca(monkeypatch, falso):
    conn, upserts = falso
    _ingerir(monkeypatch, conn, "ELET3", _data("AXIA3"), {"ELET3": 2437, "AXIA3": 9999})
    assert {r["ticker"] for r in upserts["assets"]} == {"ELET3"}
    assert not _alias_pendente(conn)


def test_sem_prova_de_companhia_fica_o_caminho_legado(monkeypatch, falso):
    # sem o CVM do símbolo novo e sem ativos no banco: não adivinha
    conn, upserts = falso
    _ingerir(monkeypatch, conn, "ELET3", _data("AXIA3"), {"ELET3": 2437})
    assert {r["ticker"] for r in upserts["assets"]} == {"ELET3"}
    assert not _alias_pendente(conn)
    assert _alias_legado(conn)


def test_brapi_nao_inverte_uma_troca_conhecida():
    # a brapi devolver ELET3 para AXIA3 não faz de ELET3 o código atual
    conn = _Conn()
    assert not tc.e_troca(conn, CVM, "AXIA3", "ELET3")
    assert not tc.e_troca(conn, CVM, "AXIA3", "XPTO3", trocas={"XPTO3": "AXIA3"})
    assert tc.e_troca(conn, CVM, "ELET3", "AXIA3")
    assert not tc.e_troca(conn, CVM, "ELET3", "ELET6")


def test_renormalize_tambem_registra_a_troca_provada(monkeypatch, falso):
    conn, upserts = falso

    class _ConnPayload(_Conn):
        def execute(self, clause, params=None):
            sql = str(clause)
            self.executed.append((sql, params or {}))
            if "brapi_raw_payloads" in sql:
                return _Result([("ELET3", 99, {"results": [{"symbol": "AXIA3"}]})])
            return _Result()

    cp = _ConnPayload()
    monkeypatch.setattr(ig, "_engine", lambda: _Engine(cp))
    monkeypatch.setattr(ig.repo, "reset_db_cols_cache", lambda: None)
    monkeypatch.setattr(ig.repo, "schema_exists", lambda c: True)
    monkeypatch.setattr(ig.repo, "load_cvm_to_ticker", lambda c: dict(CVM))
    monkeypatch.setattr(ig.nz, "normalize_all", lambda q: _data("AXIA3"))

    prog = ig.renormalize()
    assert prog["erros"] == 0
    assert {r["ticker"] for r in upserts["assets"]} == {"AXIA3"}
    assert prog["trocas_pendentes"] == 1
    assert [(p["a"], p["n"]) for p in _alias_pendente(cp)] == [("ELET3", "AXIA3")]
    assert not _alias_legado(cp)


def test_codigo_atual_herda_o_cvm_do_antigo_trocado(monkeypatch, falso):
    # A referência da CVM só conhece ELET3. Depois da unificação a ingestão
    # pede AXIA3: sem o elo da troca, o codigo_cvm sairia vazio e o upsert
    # apagaria o company_id do ativo.
    conn, upserts = falso
    _ingerir(monkeypatch, conn, "AXIA3", _data("AXIA3"), {"ELET3": 2437},
             trocas={"ELET3": "AXIA3"})
    assert upserts["companies"][0]["codigo_cvm"] == 2437
    assert upserts["assets"][0]["company_id"] == 1


def test_sem_codigo_cvm_o_ativo_mantem_o_company_id(monkeypatch, falso):
    _, upserts = falso

    class _ConnAtivo(_Conn):
        def execute(self, clause, params=None):
            r = super().execute(clause, params)
            if "SELECT company_id FROM market.assets" in str(clause):
                r.scalar = lambda: 42
            return r

    monkeypatch.setattr(ig.repo, "company_id_by_codigo",
                        lambda c, cod: pytest.fail("sem código não há o que buscar"))
    _ingerir(monkeypatch, _ConnAtivo(), "AXIA3", _data("AXIA3"), {})
    assert "companies" not in upserts
    assert upserts["assets"][0]["company_id"] == 42


# ── Com banco: armazém local, banco descartável ──────────────────────────────

_DDL = """
CREATE SCHEMA market;
CREATE TABLE market.companies (id serial PRIMARY KEY, codigo_cvm int UNIQUE, name text);
CREATE TABLE market.assets (
    ticker text PRIMARY KEY, company_id int, asset_type text, exchange text,
    currency text, is_active boolean DEFAULT true);
CREATE TABLE market.historical_prices (
    ticker text REFERENCES market.assets(ticker) ON DELETE CASCADE
        DEFERRABLE INITIALLY DEFERRED,
    date date, close numeric, source text, UNIQUE (ticker, date));
CREATE TABLE market.dividends (
    ticker text REFERENCES market.assets(ticker) ON DELETE CASCADE
        DEFERRABLE INITIALLY DEFERRED,
    event_date date, type text, amount numeric(18, 6), source text,
    UNIQUE (ticker, event_date, type, amount));
CREATE TABLE market.calculated_metrics (
    ticker text REFERENCES market.assets(ticker) ON DELETE CASCADE
        DEFERRABLE INITIALLY DEFERRED,
    period text, year int, quarter int, metric_name text, metric_value numeric,
    UNIQUE (ticker, period, year, quarter, metric_name));
CREATE TABLE market.calculated_metric_vintages (
    id serial PRIMARY KEY, ticker text, period text, year int, quarter int,
    metric_name text, metric_value numeric, available_at timestamptz);
CREATE TABLE market.ticker_alias (
    brapi_symbol text PRIMARY KEY, b3_ticker text NOT NULL, codigo_cvm int,
    motivo text, created_at timestamptz DEFAULT now());
CREATE TABLE public.setores (
    ticker text PRIMARY KEY, nome_empresa text, "SETOR" text, "SUBSETOR" text,
    "SEGMENTO" text, "LISTAGEM" text);
"""

# Eletrobras como estava no armazém em 28/10: ELET3 com o histórico fundo e a
# linha do arquivo oficial (LISTAGEM preenchida), AXIA3 com dois pregões e uma
# classificação inferida, o alias legado invertido, e a PNB (ELET6) que é
# outra classe e tem de continuar de pé. A Cielo (CIEL3) é outra companhia
# com alias para a ELET3 que não pode ser tocado. CARD3 → CSUD3 é a troca
# sem o ativo novo (como no Supabase).
_SEMENTE = """
INSERT INTO market.companies (id, codigo_cvm, name) VALUES
    (1, 2437, 'Eletrobras'), (2, 21610, 'CSU'), (3, 99999, 'Outra');
INSERT INTO market.assets VALUES
    ('ELET3', 1, 'stock', 'B3', 'BRL', true),
    ('AXIA3', 1, 'stock', 'B3', 'BRL', true),
    ('ELET6', 1, 'stock', 'B3', 'BRL', true),
    ('CARD3', 2, 'stock', 'B3', 'BRL', true),
    ('OUTR3', 3, 'stock', 'B3', 'BRL', true);
INSERT INTO market.historical_prices (ticker, date, close) VALUES
    ('ELET3', '2010-01-04', 20), ('ELET3', '2024-12-30', 38),
    ('ELET3', '2025-01-02', 39),
    ('AXIA3', '2025-01-02', 40), ('AXIA3', '2025-01-03', 41),
    ('ELET6', '2025-01-02', 44),
    ('CARD3', '2020-01-02', 10);
INSERT INTO market.dividends (ticker, event_date, type, amount) VALUES
    ('ELET3', '2023-05-02', 'DIVIDENDO', 1.5);
INSERT INTO market.calculated_metrics VALUES
    ('ELET3', 'annual', 2023, NULL, 'ROE', 0.10),
    ('ELET3', 'annual', 2024, NULL, 'ROE', 0.11),
    ('AXIA3', 'annual', 2024, NULL, 'ROE', 0.12);
INSERT INTO market.calculated_metric_vintages
    (ticker, period, year, quarter, metric_name, metric_value, available_at) VALUES
    ('ELET3', 'annual', 2023, NULL, 'ROE', 0.10, '2024-03-20');
INSERT INTO market.ticker_alias (brapi_symbol, b3_ticker, codigo_cvm, motivo) VALUES
    ('AXIA3', 'ELET3', 2437, 'auto-ingest: símbolo brapi divergente do ticker B3'),
    ('CIEL3', 'ELET3', NULL, 'outra coisa');
INSERT INTO public.setores VALUES
    ('ELET3', 'ELETROBRAS', 'Utilidade Pública', 'Energia Elétrica', 'Energia Elétrica', 'N1'),
    ('AXIA3', 'AXIA ENERGIA', 'Outros', 'Outros', 'Outros', NULL),
    ('ELET6', 'ELETROBRAS', 'Utilidade Pública', 'Energia Elétrica', 'Energia Elétrica', 'N1'),
    ('CARD3', 'CSU DIGITAL', 'Tecnologia', 'Programas e Serviços', 'Programas e Serviços', 'NM');
"""


@pytest.fixture
def banco():
    try:
        from scripts.publish_fii_selection_from_local import _warehouse_url
        url = _warehouse_url()
    except Exception as erro:  # noqa: BLE001 - sem armazém, não medimos
        pytest.skip(f"sem armazém local ({erro})")
    nome = f"teste_troca_{uuid.uuid4().hex[:10]}"
    administrador = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with administrador.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{nome}"'))
    except Exception as erro:  # noqa: BLE001
        administrador.dispose()
        pytest.skip(f"armazém local não respondeu ({erro})")
    engine = create_engine(url.rsplit("/", 1)[0] + f"/{nome}")
    with engine.begin() as conn:
        conn.exec_driver_sql(_DDL)
        conn.exec_driver_sql(_SEMENTE)
    repo.reset_db_cols_cache()
    try:
        yield engine
    finally:
        repo.reset_db_cols_cache()
        engine.dispose()
        with administrador.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{nome}" WITH (FORCE)'))
        administrador.dispose()


def _linhas(engine, sql, **p):
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text(sql), p).fetchall()]


def _foto(engine):
    tabelas = ("market.assets", "market.historical_prices", "market.dividends",
               "market.calculated_metrics", "market.calculated_metric_vintages",
               "market.ticker_alias", "public.setores")
    return {t: sorted(map(str, _linhas(engine, f"SELECT * FROM {t}"))) for t in tabelas}


def _ativos_por_classe(engine):
    """{(company_id, classe): [códigos ativos]}"""
    out: dict[tuple, list[str]] = {}
    for tk, cid in _linhas(engine, "SELECT ticker, company_id FROM market.assets "
                                   "WHERE is_active ORDER BY ticker"):
        out.setdefault((cid, tk[4:]), []).append(tk)
    return out


def _unificar(engine, antigo, novo, aplicar, **k):
    with engine.begin() as conn:
        return tc.unificar(conn, antigo, novo, aplicar=aplicar, **k)


def test_dry_run_conta_e_nao_grava(banco):
    antes = _foto(banco)
    rel = _unificar(banco, "ELET3", "AXIA3", aplicar=False)
    assert _foto(banco) == antes
    assert rel["aplicado"] is False
    assert rel["fatos"]["historical_prices"] == {"mover": 2, "descartar": 1}
    assert rel["fatos"]["calculated_metrics"] == {"mover": 1, "descartar": 1}
    assert rel["fatos"]["dividends"] == {"mover": 1, "descartar": 0}
    assert rel["fatos"]["income_statements"] == "tabela ausente"
    assert rel["alias"]["gravar"] == "ELET3->AXIA3"
    assert rel["alias"]["apagar"] == [
        "AXIA3->ELET3 (auto-ingest: símbolo brapi divergente do ticker B3)"]
    assert rel["setores"]["origem"] == "ELET3"
    assert rel["setores"]["alinhar"] == ["AXIA3"]


def test_aplicado_deixa_um_ativo_por_companhia_e_classe(banco):
    assert _ativos_por_classe(banco)[(1, "3")] == ["AXIA3", "ELET3"]
    _unificar(banco, "ELET3", "AXIA3", aplicar=True, codigo_cvm=2437)
    por_classe = _ativos_por_classe(banco)
    assert por_classe[(1, "3")] == ["AXIA3"]
    # ON e PN da mesma companhia continuam separadas
    assert por_classe[(1, "6")] == ["ELET6"]

    # o histórico tem um dono só; na mesma data vale a linha do código atual
    assert _linhas(banco, "SELECT ticker, date::text, close::float FROM "
                          "market.historical_prices WHERE ticker IN ('ELET3','AXIA3') "
                          "ORDER BY date") == [
        ("AXIA3", "2010-01-04", 20.0), ("AXIA3", "2024-12-30", 38.0),
        ("AXIA3", "2025-01-02", 40.0), ("AXIA3", "2025-01-03", 41.0)]
    assert _linhas(banco, "SELECT year, metric_value::float FROM market.calculated_metrics "
                          "WHERE ticker = 'AXIA3' ORDER BY year") == [(2023, 0.10), (2024, 0.12)]
    assert _linhas(banco, "SELECT count(*) FROM market.calculated_metric_vintages "
                          "WHERE ticker = 'AXIA3'") == [(1,)]
    for t in ("historical_prices", "dividends", "calculated_metrics",
              "calculated_metric_vintages"):
        assert _linhas(banco, f"SELECT count(*) FROM market.{t} WHERE ticker = 'ELET3'") == [(0,)]
    # o ativo antigo fica (inativo) — a fita e os payloads ainda o citam
    assert _linhas(banco, "SELECT is_active FROM market.assets WHERE ticker='ELET3'") == [(False,)]


def test_aplicado_grava_alias_antigo_para_atual_e_apaga_o_invertido(banco):
    _unificar(banco, "ELET3", "AXIA3", aplicar=True, codigo_cvm=2437)
    assert _linhas(banco, "SELECT brapi_symbol, b3_ticker, codigo_cvm, motivo FROM "
                          "market.ticker_alias ORDER BY brapi_symbol") == [
        ("CIEL3", "ELET3", None, "outra coisa"),
        ("ELET3", "AXIA3", 2437, MOTIVO_TROCA)]
    with banco.connect() as conn:
        assert tc.carregar_trocas(conn) == {"ELET3": "AXIA3"}


def test_aplicado_da_aos_dois_codigos_a_classificacao_oficial(banco):
    # Regra: vale a linha com LISTAGEM (arquivo oficial da B3); os dois
    # códigos ficam no mesmo segmento.
    _unificar(banco, "ELET3", "AXIA3", aplicar=True)
    assert _linhas(banco, 'SELECT ticker, "SETOR", "SUBSETOR", "SEGMENTO" FROM public.setores '
                          "WHERE ticker IN ('ELET3', 'AXIA3') ORDER BY ticker") == [
        ("AXIA3", "Utilidade Pública", "Energia Elétrica", "Energia Elétrica"),
        ("ELET3", "Utilidade Pública", "Energia Elétrica", "Energia Elétrica")]


def test_aplicar_duas_vezes_nao_muda_nada(banco):
    _unificar(banco, "ELET3", "AXIA3", aplicar=True, codigo_cvm=2437)
    depois = _foto(banco)
    rel = _unificar(banco, "ELET3", "AXIA3", aplicar=True, codigo_cvm=2437)
    assert _foto(banco) == depois
    assert all(v == {"mover": 0, "descartar": 0}
               for v in rel["fatos"].values() if isinstance(v, dict))
    assert rel["alias"]["gravar"] is None and rel["alias"]["apagar"] == []
    assert rel["setores"]["alinhar"] == []


def test_troca_sem_o_ativo_novo_cria_o_ativo_e_a_linha_do_universo(banco):
    # CARD3 → CSUD3 no Supabase: o código novo nem tem ativo nem setor.
    _unificar(banco, "CARD3", "CSUD3", aplicar=True)
    assert _ativos_por_classe(banco)[(2, "3")] == ["CSUD3"]
    assert _linhas(banco, "SELECT ticker FROM market.historical_prices "
                          "WHERE date = '2020-01-02' AND close = 10") == [("CSUD3",)]
    assert _linhas(banco, 'SELECT nome_empresa, "SEGMENTO", "LISTAGEM" FROM public.setores '
                          "WHERE ticker = 'CSUD3'") == [
        ("CSU DIGITAL", "Programas e Serviços", None)]


def test_recusa_classe_diferente_e_companhia_diferente(banco):
    with pytest.raises(ValueError):
        _unificar(banco, "ELET3", "ELET6", aplicar=True)
    with pytest.raises(ValueError):
        _unificar(banco, "OUTR3", "AXIA3", aplicar=True)
    assert _ativos_por_classe(banco)[(1, "3")] == ["AXIA3", "ELET3"]


def test_ingestao_registra_a_troca_e_so_o_script_move_o_historico(banco, monkeypatch):
    # A brapi devolve AXIA3 para ELET3 (foi assim que o alias legado nasceu).
    # A ingestão roda desatendida no GitHub Actions: ela prova a troca e grava
    # o dado novo sob AXIA3, mas mover o histórico do ELET3 é migração —
    # AGENTS.md pede confirmação humana, então fica pendente para o script.
    monkeypatch.setattr(ig.sched, "with_backoff", lambda fetch, **k: {"results": [{}]})
    monkeypatch.setattr(ig.nz, "normalize_all", lambda q: _data(
        "AXIA3", precos=(("2025-01-03", 41.0), ("2025-01-06", 42.0))))
    monkeypatch.setattr(ig.repo, "save_raw_payload", lambda *a, **k: None)
    monkeypatch.setattr(ig.repo, "append_metric_vintages", lambda *a, **k: 0)
    script_unificar = tc.unificar
    monkeypatch.setattr(tc, "unificar", lambda *a, **k: pytest.fail("ingestão não migra"))

    def _rodada():
        prog = ig._new_progress()
        ig.ingest_ticker(banco, "ELET3", range_="1mo", full=False, cvm_map=CVM, prog=prog,
                         trocas=ig._carregar_trocas(banco))
        return prog

    def _precos():
        return _linhas(banco, "SELECT ticker, min(date)::text, max(date)::text, count(*) "
                              "FROM market.historical_prices WHERE ticker IN ('ELET3', 'AXIA3') "
                              "GROUP BY ticker ORDER BY ticker")

    def _alias_elet3():
        return _linhas(banco, "SELECT b3_ticker, codigo_cvm, motivo FROM market.ticker_alias "
                              "WHERE brapi_symbol = 'ELET3'")

    assert _rodada()["trocas_pendentes"] == 1
    assert _precos() == [("AXIA3", "2025-01-02", "2025-01-06", 3),
                         ("ELET3", "2010-01-04", "2025-01-02", 3)]
    assert _alias_elet3() == [("AXIA3", 2437, MOTIVO_TROCA_PENDENTE)]
    assert _linhas(banco, "SELECT is_active FROM market.assets WHERE ticker = 'ELET3'") == [(True,)]
    # Pendente não pula o código antigo na rodada seguinte (o histórico ainda
    # é dele)... e regravar a mesma prova não muda nada.
    assert ig._carregar_trocas(banco) == {}
    antes = _foto(banco)
    _rodada()
    assert _foto(banco) == antes

    # A tela já trata o par como troca, mesmo antes do --apply.
    with banco.connect() as conn:
        aliases = conn.execute(text(
            "SELECT brapi_symbol, b3_ticker, motivo FROM market.ticker_alias")).fetchall()
    assert mapa_de_trocas(None, aliases)["ELET3"] == "AXIA3"

    # Aprovado o script, o histórico muda de dono e a troca deixa de ser pendente.
    monkeypatch.setattr(tc, "unificar", script_unificar)
    lote = _unificar(banco, "ELET3", "AXIA3", aplicar=True, codigo_cvm=2437)["backup"]["lote"]
    assert _alias_elet3() == [("AXIA3", 2437, MOTIVO_TROCA)]
    assert _ativos_por_classe(banco)[(1, "3")] == ["AXIA3"]
    assert _precos() == [("AXIA3", "2010-01-04", "2025-01-06", 5)]

    # Rodada seguinte: o código antigo nem vai à rede e o ativo dele não volta.
    def _rede(*a, **k):
        raise AssertionError("código substituído não pode ir à brapi")

    monkeypatch.setattr(ig.sched, "with_backoff", _rede)
    prog = _rodada()
    assert prog["codigos_substituidos"] == 1
    assert _ativos_por_classe(banco)[(1, "3")] == ["AXIA3"]

    # A reversão devolve a troca ao estado pendente, não a apaga.
    _reverter(banco, lote)
    assert _alias_elet3() == [("AXIA3", 2437, MOTIVO_TROCA_PENDENTE)]


def test_ingestao_nao_rebaixa_troca_ja_aplicada(banco, monkeypatch):
    # Rodada chamada sem o mapa de trocas (trocas=None): a prova se repete,
    # mas o alias já aplicado não pode voltar a pendente.
    _unificar(banco, "ELET3", "AXIA3", aplicar=True, codigo_cvm=2437)
    monkeypatch.setattr(ig.sched, "with_backoff", lambda fetch, **k: {"results": [{}]})
    monkeypatch.setattr(ig.nz, "normalize_all", lambda q: _data("AXIA3"))
    monkeypatch.setattr(ig.repo, "save_raw_payload", lambda *a, **k: None)
    monkeypatch.setattr(ig.repo, "append_metric_vintages", lambda *a, **k: 0)
    ig.ingest_ticker(banco, "ELET3", range_="1mo", full=False, cvm_map=CVM,
                     prog=ig._new_progress(), trocas=None)
    assert _linhas(banco, "SELECT b3_ticker, motivo FROM market.ticker_alias "
                          "WHERE brapi_symbol = 'ELET3'") == [("AXIA3", MOTIVO_TROCA)]


@pytest.mark.parametrize("cvm", [{"ELET3": 2437}, {}], ids=["cvm-so-do-antigo", "sem-cvm"])
def test_ingestao_do_codigo_atual_nao_apaga_a_companhia(banco, monkeypatch, cvm):
    _unificar(banco, "ELET3", "AXIA3", aplicar=True, codigo_cvm=2437)
    monkeypatch.setattr(ig.sched, "with_backoff", lambda fetch, **k: {"results": [{}]})
    monkeypatch.setattr(ig.nz, "normalize_all", lambda q: _data(
        "AXIA3", precos=(("2025-01-06", 42.0),)))
    monkeypatch.setattr(ig.repo, "save_raw_payload", lambda *a, **k: None)
    monkeypatch.setattr(ig.repo, "append_metric_vintages", lambda *a, **k: 0)

    ig.ingest_ticker(banco, "AXIA3", range_="1mo", full=False, cvm_map=cvm,
                     prog=ig._new_progress(), trocas=ig._carregar_trocas(banco))
    assert _linhas(banco, "SELECT company_id FROM market.assets WHERE ticker = 'AXIA3'") == [(1,)]
    assert _ativos_por_classe(banco)[(1, "3")] == ["AXIA3"]


# ── Backup e reversão ────────────────────────────────────────────────────────

def _reverter(engine, lote):
    with engine.begin() as conn:
        return tc.reverter(conn, lote)


def test_aplicado_guarda_cada_linha_que_mexe(banco):
    rel = _unificar(banco, "ELET3", "AXIA3", aplicar=True, codigo_cvm=2437)
    lote = rel["backup"]["lote"]
    guardado = dict(((t, a), c) for t, a, c in _linhas(
        banco, "SELECT tabela, acao, count(*) FROM market.troca_codigo_backup "
               "WHERE lote = :l GROUP BY tabela, acao", l=lote))
    assert guardado.pop(("market.troca_codigo_backup", "abrir")) == 1
    assert guardado == {
        ("market.assets", "antes"): 2,
        ("market.ticker_alias", "antes"): 1,          # o legado invertido AXIA3 -> ELET3
        ("public.setores", "antes"): 2,
        ("market.historical_prices", "mover"): 2,
        ("market.historical_prices", "descartar"): 1,
        ("market.dividends", "mover"): 1,
        ("market.calculated_metrics", "mover"): 1,
        ("market.calculated_metrics", "descartar"): 1,
        ("market.calculated_metric_vintages", "mover"): 1,
    }
    assert rel["backup"]["linhas"] == sum(guardado.values())
    # a linha descartada fica inteira no backup
    assert _linhas(banco, "SELECT linha->>'close' FROM market.troca_codigo_backup "
                          "WHERE lote = :l AND acao = 'descartar' "
                          "AND tabela = 'market.historical_prices'", l=lote) == [("39",)]


def test_indice_unico_que_a_chave_nao_cobre_descarta_em_vez_de_mover(banco):
    # O Supabase tem uq_metric_vintage_artifact nas safras; o armazém não. Lá
    # a ODPV3 e a SAUD3 gravaram LPA no mesmo instante com valores diferentes:
    # não é a mesma safra pela chave de FATOS, mas mover viola o índice.
    with banco.begin() as conn:
        conn.exec_driver_sql("""
            ALTER TABLE market.calculated_metric_vintages
                ADD COLUMN recorded_at timestamptz NOT NULL DEFAULT now();
            CREATE UNIQUE INDEX uq_metric_vintage_artifact
                ON market.calculated_metric_vintages
                   (ticker, period, year, quarter, metric_name, available_at, recorded_at);
            INSERT INTO market.calculated_metric_vintages
                (ticker, period, year, quarter, metric_name, metric_value,
                 available_at, recorded_at) VALUES
                ('ELET3', 'spot', 0, 0, 'LPA', 1.10, '2026-07-06', '2026-07-06'),
                ('AXIA3', 'spot', 0, 0, 'LPA', 1.25, '2026-07-06', '2026-07-06');
        """)
    rel = _unificar(banco, "ELET3", "AXIA3", aplicar=False)
    assert rel["fatos"]["calculated_metric_vintages"] == {"mover": 1, "descartar": 1}

    lote = _unificar(banco, "ELET3", "AXIA3", aplicar=True, codigo_cvm=2437)["backup"]["lote"]
    assert _linhas(banco, "SELECT metric_name, metric_value::float FROM "
                          "market.calculated_metric_vintages WHERE ticker = 'AXIA3' "
                          "ORDER BY metric_name") == [("LPA", 1.25), ("ROE", 0.10)]
    assert _linhas(banco, "SELECT acao, linha->>'metric_value' FROM market.troca_codigo_backup "
                          "WHERE lote = :l AND tabela = 'market.calculated_metric_vintages' "
                          "ORDER BY acao", l=lote) == [("descartar", "1.10"), ("mover", "0.10")]


def test_reverter_devolve_o_banco_ao_que_era(banco):
    antes = _foto(banco)
    lote = _unificar(banco, "ELET3", "AXIA3", aplicar=True, codigo_cvm=2437)["backup"]["lote"]
    assert _foto(banco) != antes

    rel = _reverter(banco, lote)
    assert _foto(banco) == antes
    assert rel["fatos"]["historical_prices"] == {"tiradas_do_atual": 2, "restauradas": 3}
    assert rel["ativo_novo"] == "restaurado"
    with banco.connect() as conn:
        assert tc.lotes(conn)[0]["revertido_em"] is not None

    with pytest.raises(ValueError, match="já foi revertido"):
        _reverter(banco, lote)
    with pytest.raises(ValueError, match="desconhecido"):
        _reverter(banco, "nao-existe")
    assert _foto(banco) == antes


def test_reverter_arquiva_a_revisao_gravada_depois_da_migracao(banco):
    # A ingestão revisou um pregão movido (2024-12-30) depois do --apply. A
    # reversão tira a linha do código atual pela chave — sem arquivar, o
    # valor revisado sumiria e só o de antes da migração voltaria.
    backup = _unificar(banco, "ELET3", "AXIA3", aplicar=True, codigo_cvm=2437)["backup"]
    lote = backup["lote"]
    with banco.begin() as conn:
        conn.execute(text("UPDATE market.historical_prices SET close = 38.5, source = 'brapi' "
                          "WHERE ticker = 'AXIA3' AND date = '2024-12-30'"))
    rel = _reverter(banco, lote)
    assert rel["fatos"]["historical_prices"] == {"tiradas_do_atual": 2, "restauradas": 3}
    assert _linhas(banco, "SELECT linha->>'date', linha->>'close', linha->>'source' "
                          "FROM market.troca_codigo_backup WHERE lote = :l "
                          "AND acao = 'retirada' AND tabela = 'market.historical_prices' "
                          "ORDER BY 1", l=lote) == [
        ("2010-01-04", "20", None), ("2024-12-30", "38.5", "brapi")]
    assert _linhas(banco, "SELECT close::float FROM market.historical_prices "
                          "WHERE ticker = 'ELET3' AND date = '2024-12-30'") == [(38.0,)]
    with banco.connect() as conn:
        (x,) = [x for x in tc.lotes(conn) if x["lote"] == lote]
    # cada linha movida que saiu do código atual: 2 preços, 1 provento,
    # 1 indicador, 1 safra — e o que a unificação guardou não muda
    assert x["retiradas"] == 5
    assert x["linhas"] == backup["linhas"]


def test_reverter_apaga_o_ativo_que_a_troca_criou(banco):
    antes = _foto(banco)
    lote = _unificar(banco, "CARD3", "CSUD3", aplicar=True)["backup"]["lote"]
    rel = _reverter(banco, lote)
    assert rel["ativo_novo"] == "apagado (não existia antes)"
    assert _foto(banco) == antes


def test_reverter_mantem_o_ativo_novo_que_ganhou_dado_depois(banco):
    # Apagar o ativo levaria em cascata o que a ingestão gravou depois.
    lote = _unificar(banco, "CARD3", "CSUD3", aplicar=True)["backup"]["lote"]
    with banco.begin() as conn:
        conn.execute(text("INSERT INTO market.historical_prices (ticker, date, close) "
                          "VALUES ('CSUD3', '2026-10-27', 25)"))
    rel = _reverter(banco, lote)
    assert rel["ativo_novo"].startswith("mantido: 1 linhas")
    assert _linhas(banco, "SELECT ticker, date::text FROM market.historical_prices "
                          "WHERE ticker IN ('CARD3', 'CSUD3') ORDER BY date") == [
        ("CARD3", "2020-01-02"), ("CSUD3", "2026-10-27")]
    assert _ativos_por_classe(banco)[(2, "3")] == ["CARD3", "CSUD3"]


@pytest.mark.parametrize("axia_ajustado, fator", [(42.0, 1.05), (40.0, 1.0)],
                         ids=["outra-base", "mesma-base"])
def test_adjusted_close_movido_vai_para_a_base_do_codigo_atual(banco, axia_ajustado, fator):
    # O provedor ajusta cada código na própria base: emendar sem reescalar
    # deixaria um salto de 5% em 2025-01-02.
    with banco.begin() as conn:
        conn.execute(text("ALTER TABLE market.historical_prices ADD COLUMN adjusted_close numeric"))
        conn.execute(text("""
            UPDATE market.historical_prices h SET adjusted_close = v.aj
            FROM (VALUES ('ELET3', date '2010-01-04', 20.0), ('ELET3', date '2024-12-30', 38.0),
                         ('ELET3', date '2025-01-02', 40.0),
                         ('AXIA3', date '2025-01-02', CAST(:ax AS numeric)),
                         ('AXIA3', date '2025-01-03', 43.0)) AS v(tk, d, aj)
            WHERE h.ticker = v.tk AND h.date = v.d"""), {"ax": axia_ajustado})
    antes = _foto(banco)

    dry = _unificar(banco, "ELET3", "AXIA3", aplicar=False)
    assert dry["base_ajuste"]["fator"] == pytest.approx(fator)
    assert dry["base_ajuste"]["comuns"] == 1
    assert dry["base_ajuste"]["reescala"] is (fator != 1.0)

    lote = _unificar(banco, "ELET3", "AXIA3", aplicar=True, codigo_cvm=2437)["backup"]["lote"]
    assert _linhas(banco, "SELECT date::text, close::float, adjusted_close::float "
                          "FROM market.historical_prices WHERE ticker = 'AXIA3' "
                          "ORDER BY date") == [
        ("2010-01-04", 20.0, pytest.approx(20.0 * fator)),
        ("2024-12-30", 38.0, pytest.approx(38.0 * fator)),
        ("2025-01-02", 40.0, axia_ajustado),
        ("2025-01-03", 41.0, 43.0)]

    _reverter(banco, lote)
    assert _foto(banco) == antes


def test_adjusted_close_sem_pregao_comum_nao_e_reescalado(banco):
    with banco.begin() as conn:
        conn.execute(text("ALTER TABLE market.historical_prices ADD COLUMN adjusted_close numeric"))
        conn.execute(text("UPDATE market.historical_prices SET adjusted_close = close"))
    rel = _unificar(banco, "CARD3", "CSUD3", aplicar=True)
    assert rel["base_ajuste"] == {"fator": None, "comuns": 0, "reescala": False}
    assert _linhas(banco, "SELECT adjusted_close::float FROM market.historical_prices "
                          "WHERE ticker = 'CSUD3'") == [(10.0,)]


def test_falha_no_meio_nao_deixa_nada_gravado(banco, monkeypatch):
    antes = _foto(banco)

    def _quebra(*a, **k):
        raise RuntimeError("caiu no meio")

    monkeypatch.setattr(tc, "_setores", _quebra)
    with pytest.raises(RuntimeError):
        _unificar(banco, "ELET3", "AXIA3", aplicar=True, codigo_cvm=2437)
    assert _foto(banco) == antes
    assert _linhas(banco, "SELECT to_regclass('market.troca_codigo_backup') IS NULL") == [(True,)]


# ── O script de migração ─────────────────────────────────────────────────────

def _script(monkeypatch, engine, capsys, *argv):
    import scripts.unificar_trocas_de_codigo_b3 as script
    monkeypatch.setattr(script, "_engine", engine if callable(engine) else lambda alvo: engine)
    monkeypatch.setattr("sys.argv", ["unificar_trocas_de_codigo_b3.py", *argv])
    codigo = script.main()
    return codigo, capsys.readouterr().out


def test_script_dry_run_nao_grava_e_lista_os_pares(banco, monkeypatch, capsys):
    antes = _foto(banco)
    codigo, saida = _script(monkeypatch, banco, capsys, "--alvo", "warehouse")
    assert codigo == 0
    assert _foto(banco) == antes
    assert "DRY-RUN" in saida
    assert "ELET3 -> AXIA3: PENDENTE" in saida
    assert "CARD3 -> CSUD3: PENDENTE" in saida
    # PNB é outra classe: não vira par nem duplicata
    assert "ELET6" not in saida


def test_script_aplicado_e_idempotente(banco, monkeypatch, capsys):
    codigo, _ = _script(monkeypatch, banco, capsys, "--alvo", "warehouse", "--apply")
    assert codigo == 0
    por_classe = _ativos_por_classe(banco)
    assert por_classe[(1, "3")] == ["AXIA3"]
    assert por_classe[(1, "6")] == ["ELET6"]
    assert por_classe[(2, "3")] == ["CSUD3"]
    depois = _foto(banco)

    # a segunda passada não acha nada a fazer, nem aplicando
    codigo, saida = _script(monkeypatch, banco, capsys, "--alvo", "warehouse")
    assert codigo == 0 and "pares pendentes: 0 de 12" in saida
    _script(monkeypatch, banco, capsys, "--alvo", "warehouse", "--apply")
    assert _foto(banco) == depois


def test_script_imprime_o_lote_lista_e_reverte(banco, monkeypatch, capsys):
    antes = _foto(banco)
    codigo, saida = _script(monkeypatch, banco, capsys, "--alvo", "warehouse", "--apply")
    assert codigo == 0
    with banco.connect() as conn:
        ids = [x["lote"] for x in tc.lotes(conn)]
    # um lote por par: os 10 sem ativo na semente só ganham o alias, e esse
    # alias também tem de poder ser desfeito
    assert len(ids) == 12
    assert all(f"--reverter {i} --apply" in saida for i in ids)

    # par já unificado não abre lote novo
    _script(monkeypatch, banco, capsys, "--alvo", "warehouse", "--apply")
    codigo, saida = _script(monkeypatch, banco, capsys, "--alvo", "warehouse", "--lotes")
    assert codigo == 0 and len(saida.strip().splitlines()) == 12

    depois = _foto(banco)
    codigo, saida = _script(monkeypatch, banco, capsys, "--alvo", "warehouse",
                            "--reverter", *ids)
    assert codigo == 0 and "dry-run" in saida
    assert _foto(banco) == depois

    codigo, saida = _script(monkeypatch, banco, capsys, "--alvo", "warehouse",
                            "--reverter", *ids, "--apply")
    assert codigo == 0
    assert _foto(banco) == antes
    codigo, saida = _script(monkeypatch, banco, capsys, "--alvo", "warehouse",
                            "--reverter", ids[0], "--apply")
    assert codigo == 1 and "já foi revertido" in saida


def test_script_recusa_aplicar_no_supabase_sem_confirmacao(monkeypatch, capsys):
    def _sem_banco(alvo):
        raise AssertionError("não pode nem conectar")

    codigo, saida = _script(monkeypatch, _sem_banco, capsys, "--alvo", "supabase", "--apply")
    assert codigo == 2 and "--confirmo-producao" in saida
