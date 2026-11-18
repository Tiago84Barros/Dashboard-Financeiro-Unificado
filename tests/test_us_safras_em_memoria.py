"""A medição em memória e a regeneração das safras usam a mesma conta.

Subir `US_FUNDAMENTAL_SCORE_VERSION` esvazia o painel PIT até alguém regenerar
`market_us.score_vintages` -- uma escrita no armazém. A medição de vantagem OOS
passou a poder medir a versão nova sem essa escrita
(`medir_vantagem_oos.py --motor us --em-memoria`). Isso só vale se as safras
que ela calcula forem as que `compute_score_history` gravaria: os dois leem o
mesmo gerador, `safras_pit`, e estes testes prendem isso.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy import create_engine, event, text

import data_pipeline.us.scoring_history as sh
from core.us_data import HORIZONTE_PAINEL_MESES
import scripts.medir_vantagem_oos as medir


def _safras_falsas(conn, datas, *, limit_companies=None):
    for i, as_of in enumerate(datas):
        yield as_of, pd.DataFrame({
            "company_id": [1, 2], "symbol": ["AAA", "BBB"],
            "score": [70.0 + i, 40.0 - i], "coverage": [1.0, 0.8],
            "score_confidence": [0.9, 0.7],
            "score_status": ["decision_grade", "research_grade"],
            "critical_missing": [[], []],
        })


class _Conn:
    def __init__(self):
        self.payloads = []

    def execute(self, stmt, params=None):
        if "INSERT INTO market_us.score_vintages" in str(stmt):
            self.payloads.extend(params)


class _Engine:
    def __init__(self):
        self.conn = _Conn()

    def begin(self):
        engine = self

        class _Ctx:
            def __enter__(self):
                return engine.conn

            def __exit__(self, *exc):
                return False
        return _Ctx()


def test_gravacao_escreve_exatamente_o_que_o_gerador_produz(monkeypatch):
    monkeypatch.setattr(sh, "safras_pit", _safras_falsas)
    eng = _Engine()
    datas = [date(2020, 6, 30), date(2021, 6, 30)]
    r = sh.compute_score_history(eng, datas, score_version="9.9.9",
                                 limit_companies=2)
    assert r["written"] == 4
    gravado = {(p["asof"], p["sym"]): p["score"] for p in eng.conn.payloads}
    esperado = {(as_of, s): v for as_of, df in _safras_falsas(None, datas)
                for s, v in zip(df["symbol"], df["score"])}
    assert gravado == esperado
    assert {p["ver"] for p in eng.conn.payloads} == {"9.9.9"}


def _armazem_sqlite():
    eng = create_engine("sqlite://")

    @event.listens_for(eng, "connect")
    def _anexa(dbapi_conn, _):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS market_us")
        dbapi_conn.execute(
            "CREATE TABLE market_us.prices_monthly "
            "(symbol TEXT, month_end TEXT, adjusted_close REAL)")
        precos = [("AAA", f"{a}-06-30", 100.0 * (1.1 ** (a - 2006)))
                  for a in range(2006, 2027)]
        precos += [("BBB", f"{a}-06-30", 100.0 * (0.95 ** (a - 2006)))
                   for a in range(2006, 2027)]
        dbapi_conn.executemany(
            "INSERT INTO market_us.prices_monthly VALUES (?,?,?)", precos)
        dbapi_conn.commit()  # o rollback da medição não pode levar os preços
    return eng


def test_painel_em_memoria_e_o_painel_das_safras_que_seriam_gravadas(monkeypatch):
    monkeypatch.setattr(sh, "safras_pit", _safras_falsas)
    eng = _armazem_sqlite()
    painel = medir.painel_us_em_memoria(eng)

    datas = sh.annual_asof_dates(*medir.US_SAFRAS_ANOS)
    vintages = pd.concat([
        df[["symbol", "score"]].assign(as_of_date=pd.Timestamp(d))
        for d, df in _safras_falsas(None, datas)], ignore_index=True)
    with eng.connect() as conn:
        monthly = pd.read_sql(text(
            "SELECT symbol, month_end, adjusted_close "
            "FROM market_us.prices_monthly"), conn)
    esperado = sh.build_annual_panel(vintages, monthly,
                                     horizon_months=HORIZONTE_PAINEL_MESES,
                                     saidas={})

    assert not painel.empty
    pd.testing.assert_frame_equal(
        painel.reset_index(drop=True), esperado.reset_index(drop=True),
        check_like=True)
    assert painel.attrs["n_safras"] == len(vintages)


def test_safras_cobrem_os_anos_da_rotina():
    """As datas-base da medição são as da rotina que regenera as safras."""
    from pathlib import Path
    rotina = Path("scripts/run_us_refresh_background.py").read_text(encoding="utf-8")
    ini, fim = medir.US_SAFRAS_ANOS
    assert f'"--start-year", "{ini}"' in rotina
    assert f'"--end-year", "{fim}"' in rotina
