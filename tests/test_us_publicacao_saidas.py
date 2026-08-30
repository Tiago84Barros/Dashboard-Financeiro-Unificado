# -*- coding: utf-8 -*-
"""A saída derivada só conta como saída se chegar à vitrine.

O portão do painel conta deslistadas em produção e junta ao painel PIT por
símbolo. Enquanto a tabela existiu apenas no warehouse local, ele respondia
"nenhuma saída em 16 safras" -- a assinatura de universo sobrevivente, agora
produzida pela publicação em vez da derivação.

Duas coisas precisam continuar valendo aqui:

* a linha **refutada** viaja, para que a vitrine distinga "não conferida" de
  "conferida e negada"; quem lê filtra, quem publica não decide por ele;
* a linha **sem símbolo** também viaja, porque ela é o denominador. Publicar só
  o que junta inflaria a fração de saídas que entra no painel.
"""
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from scripts.publish_us_delistings import ler_saidas, publicar


def _banco(*saidas):
    """`saidas`: tuplas (cik, symbol, refuted_form)."""
    eng = create_engine("sqlite:///:memory:", poolclass=StaticPool,
                        connect_args={"check_same_thread": False})
    with eng.begin() as c:
        c.execute(text("ATTACH ':memory:' AS market_us"))
        c.execute(text(
            "CREATE TABLE market_us.delistings (cik INTEGER, symbol TEXT, "
            "symbol_source TEXT, symbol_as_of TEXT, "
            "last_annual_report_year INTEGER, absence_year INTEGER, "
            "delisted_date TEXT, reason TEXT, source TEXT, refuted_form TEXT, "
            "refuted_date TEXT, checked_at TEXT)"))
        for cik, symbol, refutada in saidas:
            c.execute(text(
                "INSERT INTO market_us.delistings VALUES (:cik, :s, "
                "'dei:TradingSymbol', '2020-03-01', 2019, 2020, '2020-12-31', "
                "'ausencia_de_relatorio_anual', 'sec_full_index', :r, NULL, "
                "'2026-08-29')"), {"cik": cik, "s": symbol, "r": refutada})
    return eng


def test_recusa_publicar_tabela_vazia():
    """Vitrine vazia por derivação não rodada não pode virar 'publicado, ok'."""
    resumo = publicar(local=_banco(), remoto=None, aplicar=False)
    assert resumo["ok"] is False
    assert "ingerir_deslistadas_us" in resumo["motivo"]


def test_refutada_e_sem_simbolo_viajam_e_aparecem_separadas():
    local = _banco((1, "AKRX", None), (2, None, None), (3, "BNS", "40-F"))
    with local.connect() as conn:
        assert len(ler_saidas(conn)) == 3
    resumo = publicar(local=local, remoto=None, aplicar=False)
    assert resumo["saidas"] == 3
    assert resumo["refutadas"] == 1
    # `com_simbolo` conta só entre as não refutadas: BNS tem símbolo e não é
    # saída nenhuma, somá-la contaria uma empresa viva como entrada no painel.
    assert resumo["com_simbolo"] == 1
    assert resumo["gravado"] is False


def test_ddl_do_publicador_e_idempotente():
    """Republicar não pode depender de a migration 059 ter rodado antes."""
    import scripts.publish_us_delistings as pub
    assert pub.DDL_SAIDAS.count("IF NOT EXISTS") >= 3
