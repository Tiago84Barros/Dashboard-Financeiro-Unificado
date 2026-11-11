"""Cotacao corrente de acao/unit B3: o fechamento do COTAHIST vence a ultima
barra de `historical_prices` quando e de pregao mais novo (11/11/2026). A cota
gratuita da brapi acabou em 29/10. Regra unica com FIIs (#713). O close cru
NUNCA entra na serie ajustada: vai para `market.b3_cotacao_atual` (migration
089). Dados sinteticos."""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from data_pipeline.market import acoes_cotacao as ac
from data_pipeline.market import cotacao_pregao as cp
from data_pipeline.market import fii as fz

_BRT = ZoneInfo("America/Sao_Paulo")
D0 = dt.date(2026, 10, 29)
D1 = dt.date(2026, 11, 6)


def _fita(*linhas):
    return [(d, c, 1, e) for d, c, e in linhas]


# ── regra unica: FII e acao usam a mesma funcao ──────────────────────────────

def test_fii_reexporta_a_regra_unica():
    assert fz.mais_nova_vence is cp.mais_nova_vence
    assert fz.instante is cp.instante
    assert fz.fechamento_pregao_em is cp.fechamento_pregao_em
    assert fz.FONTE_FECHAMENTO_PREGAO == cp.FONTE_FECHAMENTO_PREGAO == "b3_cotahist"


@pytest.mark.parametrize("base,pregao,vence", [
    ("2026-10-29T16:14:00Z", D1, True),            # fita mais nova
    ("2026-11-07T15:00:00Z", D1, False),           # brapi mais nova
    ("2026-10-29T13:44:00Z", D0, True),            # close do mesmo dia > intradia
    (None, D1, False),                             # sem data: o atual fica
])
def test_equivalencia_fii_e_acao_decidem_igual(base, pregao, vence):
    nova = cp.fechamento_pregao_em(pregao)
    fii = fz.aplicar_fechamento_pregao(
        {"ticker": "X11", "price": 10.0, "pvp": 1.0, "dy_12m": 0.1}, (pregao, 9.0), base)
    assert (fii["price"] == 9.0) == vence
    barra = None if base is None else (cp.instante(base), 10.0)
    atual = cp.preco_vigente(barra, (9.0, nova))
    if base is None:
        assert atual[0] == 9.0 if barra is None else atual[0] == 10.0
    else:
        assert (atual[0] == 9.0) == vence


# ── leitor unico ─────────────────────────────────────────────────────────────

def test_preco_vigente_fechamento_do_mesmo_dia_vence_a_barra():
    p, quando, fonte = cp.preco_vigente((D0, 10.0), (9.5, cp.fechamento_pregao_em(D0)))
    assert (p, fonte) == (9.5, "b3_cotahist")
    assert quando == dt.datetime(2026, 10, 29, 18, 0, tzinfo=_BRT)


def test_preco_vigente_barra_mais_nova_vence_o_slot():
    p, _, fonte = cp.preco_vigente((dt.date(2026, 11, 9), 11.0),
                                   (9.5, cp.fechamento_pregao_em(D1)))
    assert (p, fonte) == (11.0, "brapi")


def test_preco_vigente_ausencias():
    assert cp.preco_vigente(None, None) is None
    assert cp.preco_vigente((D0, 10.0), None)[0] == 10.0
    assert cp.preco_vigente(None, (9.0, cp.fechamento_pregao_em(D1)))[0] == 9.0


# ── guarda de evento societario ──────────────────────────────────────────────

def test_sem_evento_passa():
    f = _fita((D0, 10.0, "ON  NM"), (dt.date(2026, 10, 30), 10.4, "ON  NM"), (D1, 9.9, "ON  NM"))
    assert cp.evento_entre(f, D0) is None


def test_jcp_e_dividendo_nao_pulam():
    f = _fita((D0, 10.0, "PN      N1"), (D1, 9.8, "PN  EJ  N1"),)
    assert cp.evento_entre(f, D0) is None
    f = _fita((D0, 10.0, "ON      NM"), (D1, 9.8, "ON  ED  NM"),)
    assert cp.evento_entre(f, D0) is None


def test_grupamento_pula_por_marcador_e_por_razao():
    f = _fita((D0, 0.20, "ON      NM"), (D1, 20.0, "ON  EG  NM"))
    assert "EG" in cp.evento_entre(f, D0)
    sem_marca = _fita((D0, 0.20, "ON      NM"), (D1, 20.0, "ON      NM"))
    assert "fator redondo" in cp.evento_entre(sem_marca, D0)


def test_desdobramento_sem_marcador_pula_pela_razao_e_bonificacao_pelo_marcador():
    f = _fita((D0, 30.0, "ON"), (D1, 15.0, "ON"))
    assert cp.evento_entre(f, D0) is not None
    f = _fita((D0, 10.0, "ON"), (D1, 9.1, "ON  EB"))
    assert "EB" in cp.evento_entre(f, D0)


def test_salto_acima_de_50pct_pula_e_mudanca_do_fator_de_cotacao_pula():
    f = _fita((D0, 10.0, "ON"), (D1, 16.1, "ON"))
    assert "salto" in cp.evento_entre(f, D0)
    f = [(D0, 10.0, 1, "ON"), (D1, 10.1, 100, "ON")]
    assert "fator de cotação" in cp.evento_entre(f, D0)


def test_evento_anterior_a_barra_nao_conta():
    f = _fita((dt.date(2026, 10, 20), 1.0, "ON"), (dt.date(2026, 10, 21), 10.0, "ON  EG"),
              (D0, 10.0, "ON"), (D1, 10.2, "ON"))
    assert cp.evento_entre(f, D0) is None


# ── decisao por ticker ───────────────────────────────────────────────────────

def test_decidir_promove_quando_a_fita_e_mais_nova():
    d = ac.decidir((D0, 10.0), _fita((D0, 9.5, "ON"), (D1, 9.0, "ON")))
    assert d["acao"] == "promover" and d["price"] == 9.0 and d["trade_date"] == D1
    assert d["price_at"] == dt.datetime(2026, 11, 6, 18, 0, tzinfo=_BRT)
    assert d["antes"] == 10.0


def test_decidir_mantem_se_a_barra_e_mais_nova():
    assert ac.decidir((dt.date(2026, 11, 9), 10.0), _fita((D1, 9.0, "ON")))["acao"] == "manter"


def test_decidir_nao_regrava_o_mesmo_fechamento():
    slot = (9.0, cp.fechamento_pregao_em(D1))
    d = ac.decidir((D0, 10.0), _fita((D0, 9.5, "ON"), (D1, 9.0, "ON")), slot)
    assert d["acao"] == "ja_no_fechamento"


def test_decidir_evento_nomeia_o_motivo_e_nao_promove():
    d = ac.decidir((D0, 0.2), _fita((D0, 0.2, "ON"), (D1, 20.0, "ON  EG")))
    assert d["acao"] == "evento" and "EG" in d["motivo"]


def test_decidir_sem_barra_preenche_a_ausencia_e_sem_fita_nao_inventa():
    assert ac.decidir(None, [(D1, 9.0, 1, "ON")])["acao"] == "promover"
    assert ac.decidir((D0, 10.0), [])["acao"] == "sem_fita"


# ── etapa que grava ──────────────────────────────────────────────────────────

class _Res:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def __iter__(self):
        return iter(self.rows)

    def fetchall(self):
        return self.rows

    def scalar(self):
        return self.rows[0][0] if self.rows else None


class _Conn:
    def __init__(self, eng):
        self.eng = eng

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        s = str(stmt)
        self.eng.log.append((s, params))
        return self.eng.responder(s, params)


class _Engine:
    def __init__(self, existe=True):
        self.log = []
        self.existe = existe

    def connect(self):
        return _Conn(self)

    def begin(self):
        return _Conn(self)

    def responder(self, s, params):
        if "to_regclass" in s:
            return _Res([(self.existe,)])
        if "FROM market.historical_prices" in s:
            return _Res([("VELHA3", D0, 10.0), ("NOVA3", dt.date(2026, 11, 9), 5.0),
                         ("SPLIT3", D0, 0.2)])
        if "FROM market.assets" in s:
            return _Res([("VELHA3",), ("NOVA3",), ("SPLIT3",), ("SEMBARRA3",)])
        if "b3_cotacao_atual" in s and s.lstrip().upper().startswith("SELECT"):
            return _Res([])
        if "DISTINCT ON (s.ticker)" in s:
            return _Res([("SEMBARRA3", D1, 7.0, 1, "ON")])
        if "FROM market.b3_security_history" in s:
            return _Res([("VELHA3", D0, 9.5, 1, "ON"), ("VELHA3", D1, 9.0, 1, "ON"),
                         ("NOVA3", D1, 4.0, 1, "ON"),
                         ("SPLIT3", D0, 0.2, 1, "ON"), ("SPLIT3", D1, 20.0, 1, "ON  EG")])
        return _Res()


def _preparar(monkeypatch, existe=True):
    eng = _Engine(existe)
    import data_pipeline.market.ingest as ing
    monkeypatch.setattr(ing, "_engine", lambda: eng)
    monkeypatch.setattr(ac.repo, "reset_db_cols_cache", lambda: None)
    gravados = []
    monkeypatch.setattr(ac.repo, "upsert_por_colunas",
                        lambda conn, t, rows: gravados.append((t, rows)) or len(rows))
    return eng, gravados


def test_etapa_grava_so_quem_o_pregao_vence_e_nomeia_o_pulado(monkeypatch):
    _, gravados = _preparar(monkeypatch)
    rep = ac.promover_fechamento_pregao_acoes()
    assert rep["status"] == "completed" and rep["atualizados"] == 2
    t, rows = gravados[0]
    assert t == "b3_cotacao_atual"
    assert {r["ticker"] for r in rows} == {"VELHA3", "SEMBARRA3"}
    assert rep["pulados_evento"] == [{"ticker": "SPLIT3", "motivo": rep["pulados_evento"][0]["motivo"]}]
    assert "EG" in rep["pulados_evento"][0]["motivo"]
    assert "NOVA3" not in {r["ticker"] for r in rows}  # barra mais nova que a fita
    assert all(r["price_source"] == "b3_cotahist" for r in rows)


def test_dry_run_mede_sem_gravar_e_mede_a_variacao(monkeypatch):
    _, gravados = _preparar(monkeypatch)
    rep = ac.promover_fechamento_pregao_acoes(dry_run=True)
    assert gravados == [] and rep["atualizados"] == 0 and rep["mudariam"] == 2
    assert rep["amostra"][0]["ticker"] == "VELHA3"
    assert rep["variacao"]["acima_5pct"] == 1


def test_nada_vai_para_historical_prices(monkeypatch):
    eng, gravados = _preparar(monkeypatch)
    ac.promover_fechamento_pregao_acoes()
    assert {t for t, _ in gravados} == {"b3_cotacao_atual"}
    assert not [s for s, _ in eng.log if re.search(r"(INSERT|UPDATE)\s+.*historical_prices", s, re.I)]


def test_sem_a_tabela_nomeia_a_migration_e_nao_grava(monkeypatch, caplog):
    _, gravados = _preparar(monkeypatch, existe=False)
    with caplog.at_level("ERROR"):
        rep = ac.promover_fechamento_pregao_acoes()
    assert rep["status"] == "pendente_migration" and rep["tabela_existe"] is False
    assert gravados == [] and "089_market_b3_cotacao_atual.sql" in caplog.text


def test_sem_a_tabela_o_dry_run_ainda_mede(monkeypatch):
    _preparar(monkeypatch, existe=False)
    rep = ac.promover_fechamento_pregao_acoes(dry_run=True)
    assert rep["status"] == "completed" and rep["mudariam"] == 2


# ── repositorio e migration ──────────────────────────────────────────────────

def test_repositorio_conhece_a_tabela_nova():
    from data_pipeline.market import repository as repo
    assert repo._CONFLICT["b3_cotacao_atual"] == "ticker"
    assert "price_at" in repo._UPDATE_COLS["b3_cotacao_atual"]


_SQL = Path(__file__).resolve().parents[1] / "supabase_unificado/schema/089_market_b3_cotacao_atual.sql"


def test_migration_e_aditiva_idempotente_e_documenta_rollback():
    s = _SQL.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS market.b3_cotacao_atual" in s
    assert "Rollback: DROP TABLE IF EXISTS market.b3_cotacao_atual" in s
    assert "NAO APLICADA" in s and "Backup" in s
    assert not re.search(r"^\s*(DROP TABLE|DELETE|TRUNCATE|ALTER TABLE market\.historical_prices)",
                         s.replace("-- Rollback", ""), re.M | re.I)
    assert "PRIMARY KEY" in s


# ── agenda e runner ──────────────────────────────────────────────────────────

def test_agenda_poe_acoes_cotahist_no_fim_do_pregao():
    from core import publicacao_agenda as pa
    cmds = [p[1] for p in pa._CADEIA_PREGAO if p[0] == "run_market_ingest.py"]
    assert cmds[-1] == "acoes-cotahist"
    assert pa._CADEIA_PREGAO[-1] == ("run_market_ingest.py", "acoes-cotahist",
                                     "--warehouse", "--json")
    # depois de a fita de acoes ser carregada
    assert pa._CADEIA_PREGAO.index(pa._CADEIA_PREGAO[-1]) > max(
        i for i, p in enumerate(pa._CADEIA_PREGAO) if "ingerir_precos_b3.py" in p[0])


def test_agenda_publica_a_cotacao_depois_do_publish_da_brapi():
    from core import publicacao_agenda as pa
    scripts = [p[0] for p in pa._CADEIA_B3_BRAPI]
    assert scripts.index("scripts/publish_b3_cotacao_from_local.py") > scripts.index(
        "scripts/publish_b3_brapi_from_local.py")


def test_runner_aceita_o_comando(monkeypatch):
    import sys

    import run_market_ingest
    chamadas = []
    monkeypatch.setattr(ac, "promover_fechamento_pregao_acoes",
                        lambda dry_run=False: chamadas.append(dry_run) or {
                            "status": "completed", "atualizados": 0})
    monkeypatch.setattr(sys, "argv", ["run_market_ingest.py", "acoes-cotahist", "--dry-run"])
    assert run_market_ingest.main() == 0
    assert chamadas == [True]


def test_runner_sai_0_com_migration_pendente_e_1_com_falha(monkeypatch):
    import sys

    import run_market_ingest
    for status, esperado in (("pendente_migration", 0), ("failed", 1)):
        monkeypatch.setattr(ac, "promover_fechamento_pregao_acoes",
                            lambda dry_run=False, s=status: {"status": s})
        monkeypatch.setattr(sys, "argv", ["run_market_ingest.py", "acoes-cotahist"])
        assert run_market_ingest.main() == esperado


# ── leitores gemeos: frescor, dossie, reprocess ──────────────────────────────

def test_selo_de_frescor_olha_a_data_efetiva_com_o_mesmo_universo():
    import re

    from core import frescor
    sql = re.sub(r"\s+", " ", frescor.SQL_DATA_DO_PRECO_B3_VIGENTE)
    assert "GREATEST(max(hp.date), max(c.trade_date))" in sql
    assert "LEFT JOIN market.b3_cotacao_atual c ON c.ticker = hp.ticker" in sql
    assert "percentile_disc(0.5)" in sql and "GROUP BY hp.ticker" in sql
    for pred in ("a.asset_type IN ('stock','unit')", "a.is_active IS TRUE",
                 "a.company_id IS NOT NULL"):
        assert pred in sql


def test_selo_sem_a_tabela_usa_a_sql_antiga():
    from core import frescor
    usados = []

    class _C:
        def __enter__(self): return self
        def __exit__(self, *a): return False

        def execute(self, stmt):
            s = str(stmt)
            usados.append(s)

            class _R:
                def scalar(_):
                    return False if "to_regclass" in s else dt.date(2026, 10, 29)
            return _R()

    class _E:
        def connect(self): return _C()

    assert frescor.data_do_preco("b3", engine=_E()) == dt.date(2026, 10, 29)
    assert "b3_cotacao_atual c" not in usados[-1]


def test_dossie_usa_o_fechamento_quando_a_tabela_existe(monkeypatch):
    from core import dossie_b3 as d
    monkeypatch.setattr(d, "_table_exists", lambda t: True)
    monkeypatch.setattr(d, "_rows", lambda sql, **p: [
        {"price": 9.0, "price_at": cp.fechamento_pregao_em(D1)}])
    out = {"preco": 10.0, "data_preco": str(D0)}
    d._aplicar_cotacao_vigente("X3", out, {"date": D0, "close": 10.0})
    assert out["preco"] == 9.0 and out["data_preco"] == str(D1)
    assert out["fonte_preco"] == "b3_cotahist"


def test_dossie_sem_a_tabela_fica_na_barra(monkeypatch):
    from core import dossie_b3 as d
    monkeypatch.setattr(d, "_table_exists", lambda t: False)
    out = {"preco": 10.0, "data_preco": str(D0)}
    d._aplicar_cotacao_vigente("X3", out, {"date": D0, "close": 10.0})
    assert out == {"preco": 10.0, "data_preco": str(D0)}


def test_preco_atual_do_reprocess_usa_o_leitor_unico():
    class _Q:
        def __init__(self, r): self.r = r
        def fetchone(self): return self.r

    class _C:
        def execute(self, stmt, params=None):
            s = str(stmt)
            if "historical_prices" in s:
                return _Q((D0, 10.0))
            return _Q((9.0, cp.fechamento_pregao_em(D1)))

    assert ac.preco_atual(_C(), "X3", existe=True) == 9.0
    assert ac.preco_atual(_C(), "X3", existe=False) == 10.0


# ── publicador local -> Supabase ─────────────────────────────────────────────

def test_publicador_retem_o_que_a_vitrine_nao_conhece_e_nomeia():
    from scripts import publish_b3_cotacao_from_local as pub
    ok, retidos = pub.selecionar(
        [{"ticker": "A3"}, {"ticker": "FOSSIL3"}], {"A3"})
    assert [r["ticker"] for r in ok] == ["A3"] and retidos == ["FOSSIL3"]


def test_publicador_nomeia_a_tabela_ausente_sem_escrever():
    from scripts import publish_b3_cotacao_from_local as pub

    class _C:
        def __init__(self, existe): self.e = existe
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, stmt, p=None):
            class _R:
                def scalar(_): return self.e
                def fetchall(_): return []
            return _R()

    class _E:
        def __init__(self, existe): self.e = existe
        def connect(self): return _C(self.e)
        def begin(self): raise AssertionError("não devia escrever")

    r = pub.publish(apply=True, source=_E(False), target=_E(True))
    assert r["status"] == "tabela_ausente_no_armazem" and r["migration"] == pub.MIGRATION
    r = pub.publish(apply=True, source=_E(True), target=_E(False))
    # com o armazém "existente" a leitura devolveria linhas; aqui só importa a vitrine
    assert r["status"] in ("tabela_ausente_na_vitrine",) and r["migration"] == pub.MIGRATION


def test_grau_de_confianca_da_b3_declara_a_origem_da_cotacao():
    import inspect

    from core import confianca_secao as cs
    assert "COTAHIST" in cs.NOTA_COTACAO_ACAO
    assert "NOTA_COTACAO_ACAO" in inspect.getsource(cs.confianca_b3)


def test_decidir_mesma_data_e_mesmo_preco_nao_regrava():
    assert ac.decidir((D0, 6.02), _fita((D0, 6.02, "ON")))["acao"] == "manter"
    assert ac.decidir((D0, 6.02), _fita((D0, 6.10, "ON")))["acao"] == "promover"
