# -*- coding: utf-8 -*-
"""Publicação da safra PIT dos EUA na vitrine, e a prontidão que o painel exige.

Dois defeitos, um de cada lado da mesma fronteira:

* o **leitor** declarava prontidão por `market_us.companies`, tabela que ele não
  consulta e que a vitrine nunca teve. Publicar a safra não destravaria nada;
* o **publicador** poderia mandar para a vitrine safra de outra metodologia,
  que o leitor filtra fora -- espaço gasto, painel igualmente vazio.
"""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

import core.us_read as ur
from scripts.publish_us_score_vintages import ler_precos_mensais, ler_safras, publicar


def _banco(*, com_safras=(), com_precos=(), com_companies=False):
    eng = create_engine("sqlite:///:memory:", poolclass=StaticPool,
                        connect_args={"check_same_thread": False})
    with eng.begin() as c:
        c.execute(text("ATTACH ':memory:' AS market_us"))
        c.execute(text("CREATE TABLE market_us.score_vintages (symbol TEXT, "
                       "score_version TEXT, as_of_date TEXT, track TEXT, "
                       "score REAL, coverage REAL, score_confidence REAL)"))
        c.execute(text("CREATE TABLE market_us.prices_monthly (symbol TEXT, "
                       "month_end TEXT, close REAL, adjusted_close REAL, "
                       "volume INTEGER, total_return REAL)"))
        if com_companies:
            c.execute(text("CREATE TABLE market_us.companies (id INT)"))
        for sym, versao, data in com_safras:
            c.execute(text("INSERT INTO market_us.score_vintages VALUES "
                           "(:s,:v,:d,'fundamental',60.0,90.0,80.0)"),
                      {"s": sym, "v": versao, "d": data})
        for sym, mes, preco in com_precos:
            c.execute(text("INSERT INTO market_us.prices_monthly VALUES "
                           "(:s,:m,:p,:p,1000,0.0)"),
                      {"s": sym, "m": mes, "p": preco})
    return eng


# ── leitor ──────────────────────────────────────────────────────────────────

def test_painel_nao_exige_tabela_que_nao_le(monkeypatch):
    """A vitrine não tem `companies`, e o painel não precisa dela."""
    eng = _banco(com_safras=[("AAPL", "0.5.0", "2020-06-30"),
                             ("AAPL", "0.5.0", "2021-06-30")],
                 com_precos=[("AAPL", "2020-06-30", 100.0),
                             ("AAPL", "2021-06-30", 120.0),
                             ("AAPL", "2022-06-30", 130.0)],
                 com_companies=False)
    monkeypatch.setattr(ur, "_engine", lambda: eng)
    monkeypatch.setattr(ur, "schema_ready", lambda *a, **k: False)
    painel = ur.load_score_panel(score_version="0.5.0")
    assert not painel.empty, painel.attrs.get("motivo")
    assert set(painel["symbol"]) == {"AAPL"}


def test_sem_a_tabela_da_safra_o_motivo_nomeia_ela(monkeypatch):
    eng = create_engine("sqlite:///:memory:", poolclass=StaticPool,
                        connect_args={"check_same_thread": False})
    monkeypatch.setattr(ur, "_engine", lambda: eng)
    monkeypatch.setattr(ur, "schema_ready", lambda *a, **k: True)
    painel = ur.load_score_panel(score_version="0.5.0")
    assert painel.empty
    motivo = painel.attrs["motivo"]
    assert "score_vintages" in motivo and "publish_us_score_vintages" in motivo


def test_tabelas_ausentes_nao_contamina_a_consulta_seguinte():
    """Sonda que falha não pode derrubar por arrasto a tabela que existe."""
    eng = _banco(com_safras=[("AAPL", "0.5.0", "2020-06-30")])
    with eng.connect() as conn:
        assert ur._tabelas_ausentes(conn, "inexistente", "score_vintages") == [
            "inexistente"]
        assert conn.execute(text(
            "SELECT count(*) FROM market_us.score_vintages")).scalar() == 1


# ── publicador ──────────────────────────────────────────────────────────────

def test_recusa_publicar_quando_a_versao_corrente_nao_tem_safra():
    """Publicar 0.4.0 gastaria espaço e deixaria o painel vazio do mesmo jeito."""
    local = _banco(com_safras=[("AAPL", "0.4.0", "2020-06-30")])
    resumo = publicar(local=local, remoto=None, aplicar=False, versao="0.5.0")
    assert resumo["ok"] is False
    assert "0.4.0" in resumo["motivo"] and "score-history" in resumo["motivo"]


def test_safra_sem_simbolo_nao_viaja():
    """O painel junta por símbolo; safra sem ele seria linha órfã na vitrine."""
    local = _banco(com_safras=[("AAPL", "0.5.0", "2020-06-30"),
                               (None, "0.5.0", "2020-06-30")])
    with local.connect() as conn:
        assert [r[0] for r in ler_safras(conn, "0.5.0")] == ["AAPL"]


def test_preco_publicado_e_a_grade_inteira_do_simbolo_da_safra():
    """Não só os meses de rebalanço: é o fim da série que denuncia deslistagem.

    MSFT tem preço e nenhuma safra -- fica fora. AAPL tem safra só em junho e
    leva todos os meses, incluindo o setembro em que a série termina.
    """
    local = _banco(com_safras=[("AAPL", "0.5.0", "2020-06-30")],
                   com_precos=[("AAPL", "2020-06-30", 100.0),
                               ("AAPL", "2020-09-30", 20.0),
                               ("MSFT", "2020-06-30", 50.0)])
    with local.connect() as conn:
        linhas = ler_precos_mensais(conn, "0.5.0")
    assert {r[0] for r in linhas} == {"AAPL"}
    assert {r[1] for r in linhas} == {"2020-06-30", "2020-09-30"}


def test_simbolo_pontuado_sem_preco_aparece_no_resumo():
    """A diferença entre universo pontuado e mensurável não pode sumir calada."""
    local = _banco(com_safras=[("AAPL", "0.5.0", "2020-06-30"),
                               ("XYZ", "0.5.0", "2020-06-30")],
                   com_precos=[("AAPL", "2020-06-30", 100.0)])
    resumo = publicar(local=local, remoto=None, aplicar=False, versao="0.5.0")
    assert resumo["ok"] is True
    assert resumo["simbolos"] == 2
    assert resumo["simbolos_sem_preco"] == 1
    assert resumo["exemplos_sem_preco"] == ["XYZ"]
    assert resumo["gravado"] is False


@pytest.mark.parametrize("nome", ["DDL_VINTAGES", "DDL_PRECOS"])
def test_ddl_do_publicador_e_idempotente(nome):
    """Republicar não pode depender de a migration 057 ter rodado antes."""
    import scripts.publish_us_score_vintages as pub
    assert "IF NOT EXISTS" in getattr(pub, nome)
