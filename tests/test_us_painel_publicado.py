# -*- coding: utf-8 -*-
"""Painel PIT publicado pronto: serve só enquanto corresponde às fontes.

Montar o painel a cada visita (safras + preços + desfechos) foi o maior egress
do Supabase no ciclo set/2026. O painel pronto corta isso, mas um painel velho
servido como atual seria pior que o egress: o backtest mediria safras ou preços
que já não são os da vitrine, sem aviso. Daí a impressão das fontes.
"""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

VERSAO = "0.9.0"


def _engine():
    eng = create_engine("sqlite:///:memory:", poolclass=StaticPool,
                        connect_args={"check_same_thread": False})
    with eng.begin() as c:
        c.execute(text("ATTACH ':memory:' AS market_us"))
        c.execute(text("CREATE TABLE market_us.score_vintages (symbol TEXT, "
                       "score_version TEXT, as_of_date TEXT, track TEXT, "
                       "score REAL)"))
        c.execute(text("CREATE TABLE market_us.prices_monthly (symbol TEXT, "
                       "month_end TEXT, adjusted_close REAL)"))
        c.execute(text("CREATE TABLE market_us.score_panel_pub ("
                       "score_version TEXT, horizon_months INT, ordem INT, "
                       "date TEXT, symbol TEXT, score REAL, fwd_return REAL, "
                       "censored BOOLEAN)"))
        c.execute(text("CREATE TABLE market_us.score_panel_pub_meta ("
                       "score_version TEXT, horizon_months INT, impressao TEXT, "
                       "atributos TEXT, n_linhas INT, publicado_em TEXT)"))
        for sym, score in (("AAA", 70.0), ("BBB", 40.0), ("CCC", 55.0)):
            c.execute(text("INSERT INTO market_us.score_vintages VALUES "
                           "(:s, :v, '2020-06-30', 'fundamental', :sc)"),
                      {"s": sym, "v": VERSAO, "sc": score})
        precos = [("AAA", "2020-06-30", 10.0), ("AAA", "2021-06-30", 13.0),
                  ("BBB", "2020-06-30", 20.0), ("BBB", "2021-06-30", 18.0),
                  # CCC sai antes do fim do dado: censurada na última cotação.
                  ("CCC", "2020-06-30", 5.0), ("CCC", "2020-12-31", 2.0),
                  ("AAA", "2021-09-30", 14.0)]
        for sym, me, px in precos:
            c.execute(text("INSERT INTO market_us.prices_monthly VALUES "
                           "(:s, :m, :p)"), {"s": sym, "m": me, "p": px})
    return eng


@pytest.fixture
def ambiente(monkeypatch):
    import core.us_read as ur
    eng = _engine()
    monkeypatch.setattr(ur, "_engine", lambda: eng)
    return ur, eng


def _publicar(eng, aplicar=True):
    from scripts.publish_us_score_panel import publicar
    return publicar(remoto=eng, aplicar=aplicar, versao=VERSAO, horizonte=12)


def test_publicado_e_identico_ao_montado_ao_vivo(ambiente):
    ur, eng = ambiente
    ao_vivo = ur.load_score_panel(score_version=VERSAO, publicado=False)
    assert ao_vivo.attrs["fonte"] == "ao vivo" and len(ao_vivo) == 3

    resumo = _publicar(eng)
    assert resumo["ok"] and resumo["aplicado"] and resumo["conferido"]

    servido = ur.load_score_panel(score_version=VERSAO)
    assert servido.attrs["fonte"] == "publicado"
    cols = ["date", "symbol", "score", "fwd_return", "censored"]
    assert servido[cols].equals(ao_vivo[cols].reset_index(drop=True))
    for k in ("n_censored", "n_inobservavel", "n_convencionado", "n_desfechos"):
        assert servido.attrs[k] == ao_vivo.attrs[k], k
    assert servido.attrs["n_censored"] == 1


def test_mes_novo_de_preco_derruba_o_publicado(ambiente):
    """O publicado envelhece quando a fonte muda; o leitor volta a montar."""
    ur, eng = ambiente
    _publicar(eng)
    with eng.begin() as c:
        c.execute(text("INSERT INTO market_us.prices_monthly VALUES "
                       "('AAA', '2021-12-31', 15.0)"))
    assert ur.load_score_panel(score_version=VERSAO).attrs["fonte"] == "ao vivo"


def test_safra_republicada_derruba_o_publicado(ambiente):
    ur, eng = ambiente
    _publicar(eng)
    with eng.begin() as c:
        c.execute(text("INSERT INTO market_us.score_vintages VALUES "
                       "('DDD', :v, '2020-06-30', 'fundamental', 50.0)"),
                  {"v": VERSAO})
    assert ur.load_score_panel(score_version=VERSAO).attrs["fonte"] == "ao vivo"


def test_outra_versao_ou_horizonte_nao_serve_o_publicado(ambiente):
    ur, eng = ambiente
    _publicar(eng)
    painel = ur.load_score_panel(score_version=VERSAO, horizon_months=6)
    assert painel.attrs.get("fonte") != "publicado"


def test_simulacao_nao_grava(ambiente):
    ur, eng = ambiente
    resumo = _publicar(eng, aplicar=False)
    assert resumo["ok"] and not resumo["aplicado"] and resumo["linhas"] == 3
    with eng.connect() as c:
        n = c.execute(text("SELECT COUNT(*) FROM market_us.score_panel_pub")).scalar()
    assert n == 0
    assert ur.load_score_panel(score_version=VERSAO).attrs["fonte"] == "ao vivo"


def test_republicar_substitui_sem_duplicar(ambiente):
    ur, eng = ambiente
    _publicar(eng)
    _publicar(eng)
    with eng.connect() as c:
        n = c.execute(text("SELECT COUNT(*) FROM market_us.score_panel_pub")).scalar()
        m = c.execute(text("SELECT COUNT(*) FROM market_us.score_panel_pub_meta")).scalar()
    assert (n, m) == (3, 1)
