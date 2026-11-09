"""Reembolso de aluguel de ações (BTC) tem tipo próprio (migração 084).

O que quem tomou suas ações emprestadas paga no lugar do dividendo ou do JCP
era gravado como `reit_income` e aparecia como "Rendimento FII" -- 30 linhas
de BBAS3, PETR3, SBSP3 & cia. em 06/11/2026. Estes testes prendem:

* B3 e XP classificam o reembolso igual, ou a deduplicação canônica (que
  compara o tipo) deixaria o mesmo pagamento entrar pelas duas fontes;
* antes da 084 nada quebra: grava como antes e avisa;
* depois da 084 a reimportação corrige o tipo da linha antiga, sem duplicar;
* o reembolso continua contando como renda e não vai para ficha de IRPF
  nenhuma sozinho.
"""
from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from pathlib import Path

import openpyxl
from sqlalchemy import create_engine, text

from data_pipeline.importers.investments import b3_movimentacao as mov
from data_pipeline.importers.investments import common
from data_pipeline.importers.investments import xp_consolidado as xp

_LINHA_B3 = ("Credito", "11/09/2026", "Reembolso", "BBAS3 - BANCO DO BRASIL S/A",
             "RICO", "0", "-", "170,35")


def _engine():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE dividends (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                user_id TEXT NOT NULL, asset_id TEXT NOT NULL,
                type TEXT NOT NULL, amount_per_unit NUMERIC,
                quantity NUMERIC, total_amount NUMERIC NOT NULL,
                ex_date DATE, payment_date DATE NOT NULL, external_id TEXT,
                quantity_placeholder BOOLEAN NOT NULL DEFAULT 0
            )
        """))
        conn.execute(text("""
            CREATE UNIQUE INDEX ux_div_ext ON dividends(external_id)
            WHERE external_id IS NOT NULL
        """))
    return engine


def _linhas(engine) -> list[tuple]:
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text(
            "SELECT external_id, type, total_amount FROM dividends ORDER BY 1"
        ))]


def _movimentacao_xlsx(*linhas) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Movimentação"
    ws.append(["Entrada/Saída", "Data", "Movimentação", "Produto",
               "Instituição", "Quantidade", "Preço unitário",
               "Valor da Operação"])
    for linha in linhas:
        ws.append(list(linha))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _b3_sem_banco_real(monkeypatch, *, aplicada_084: bool = True):
    monkeypatch.setattr(mov.settings, "OWNER_USER_ID", "u")
    monkeypatch.setattr(mov, "ensure_external_id_columns", lambda e: None)
    monkeypatch.setattr(mov, "ensure_movement_events_table", lambda e: False)
    monkeypatch.setattr(mov, "get_or_create_b3_account", lambda c, u: "conta")
    monkeypatch.setattr(mov, "migracao_084_aplicada", lambda e: aplicada_084)
    monkeypatch.setattr(
        mov, "batch_get_or_create_assets",
        lambda c, itens: {t: t for t, _n, _c in itens},
    )
    monkeypatch.setattr(
        mov, "batch_insert_investment_transactions", lambda c, rows: 0,
    )

    def _existentes(conn, tabela, ids):  # o original usa ANY(), só Postgres
        marcas = ", ".join(f":i{n}" for n in range(len(ids)))
        return {r[0] for r in conn.execute(
            text(f"SELECT external_id FROM {tabela} "
                 f"WHERE external_id IN ({marcas})"),
            {f"i{n}": v for n, v in enumerate(ids)},
        )}

    monkeypatch.setattr(mov, "batch_filter_existing_external_ids", _existentes)


# ─────────────────────────────────────────────────────────────────────────────
# Classificação
# ─────────────────────────────────────────────────────────────────────────────

def test_movimentacao_classifica_reembolso_com_tipo_proprio():
    parsed = mov._parse_row(_LINHA_B3)
    assert parsed["div_type"] == common.TIPO_REEMBOLSO_BTC
    assert parsed["quantity"] is None  # a B3 publica quantidade 0


def test_b3_e_xp_classificam_o_reembolso_igual():
    """A chave canônica compara o tipo: se as duas fontes divergirem, o mesmo
    reembolso entra duas vezes."""
    assert common.classify_movement("Reembolso") == (
        "income", common.TIPO_REEMBOLSO_BTC)
    assert xp._XP_INCOME_TYPE_MAP["reembolso"] == common.TIPO_REEMBOLSO_BTC


def test_tipo_e_aceito_na_gravacao():
    assert common.TIPO_REEMBOLSO_BTC in common.TIPOS_DIVIDENDO


# ─────────────────────────────────────────────────────────────────────────────
# Antes e depois da 084, pelo importador
# ─────────────────────────────────────────────────────────────────────────────

def test_antes_da_084_grava_como_antes_e_avisa(monkeypatch):
    engine = _engine()
    _b3_sem_banco_real(monkeypatch, aplicada_084=False)

    resumo = mov.parse(_movimentacao_xlsx(_LINHA_B3), engine)

    assert resumo["status"] == "success"
    assert [t for _e, t, _v in _linhas(engine)] == ["reit_income"]
    assert any("084" in a for a in resumo["avisos"])


def test_depois_da_084_grava_com_o_tipo_proprio(monkeypatch):
    engine = _engine()
    _b3_sem_banco_real(monkeypatch)

    resumo = mov.parse(_movimentacao_xlsx(_LINHA_B3), engine)

    assert resumo["avisos"] == []
    assert [t for _e, t, _v in _linhas(engine)] == [common.TIPO_REEMBOLSO_BTC]


def test_reimportar_depois_da_084_corrige_o_tipo_sem_duplicar(monkeypatch):
    """A linha gravada antes da 084 tem o mesmo external_id (o id da B3 leva o
    texto do movimento, não o tipo): a reimportação a encontra e corrige."""
    engine = _engine()
    _b3_sem_banco_real(monkeypatch, aplicada_084=False)
    mov.parse(_movimentacao_xlsx(_LINHA_B3), engine)

    _b3_sem_banco_real(monkeypatch, aplicada_084=True)
    resumo = mov.parse(_movimentacao_xlsx(_LINHA_B3), engine)

    assert resumo["duplicates_skipped"] == 1
    linhas = _linhas(engine)
    assert len(linhas) == 1
    assert linhas[0][1] == common.TIPO_REEMBOLSO_BTC


def test_corrigir_tipo_so_mexe_na_linha_do_dono():
    engine = _engine()
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO dividends (user_id, asset_id, type, total_amount,
                                   payment_date, external_id)
            VALUES ('outro', 'BBAS3', 'reit_income', 170.35, :d, 'b3mov-1')
        """), {"d": date(2026, 9, 11)})
        n = common.corrigir_tipo_reembolso(conn, [{
            "user_id": "u", "asset_id": "BBAS3", "payment_date": date(2026, 9, 11),
            "total_amount": 170.35, "type": common.TIPO_REEMBOLSO_BTC,
        }])

    assert n == 0
    assert _linhas(engine)[0][1] == "reit_income"


def test_reembolso_antigo_da_xp_nao_duplica_quando_a_b3_chega(monkeypatch):
    """O reembolso que entrou pela XP antes da 084 está como `reit_income`. A
    B3 trazendo o mesmo pagamento com o tipo novo não pode gravar um segundo
    provento -- dobraria a renda --, e a linha antiga ganha o tipo certo."""
    engine = _engine()
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO dividends (user_id, asset_id, type, total_amount,
                                   payment_date, external_id)
            VALUES ('u', 'BBAS3', 'reit_income', 170.35, :d, 'xpcsl-inc-velho')
        """), {"d": date(2026, 9, 11)})
    _b3_sem_banco_real(monkeypatch)

    resumo = mov.parse(_movimentacao_xlsx(_LINHA_B3), engine)

    assert resumo["incomes_imported"] == 0
    assert _linhas(engine) == [
        ("xpcsl-inc-velho", common.TIPO_REEMBOLSO_BTC, 170.35)]


def test_reembolso_e_rendimento_de_valor_diferente_continuam_dois():
    engine = _engine()
    base = {"user_id": "u", "asset_id": "BBAS3", "amount_per_unit": None,
            "quantity": None, "ex_date": None, "payment_date": date(2026, 9, 11)}
    with engine.begin() as conn:
        common.batch_insert_dividends(conn, [
            {**base, "type": "reit_income", "total_amount": 50.0,
             "external_id": "b3mov-a"},
            {**base, "type": common.TIPO_REEMBOLSO_BTC, "total_amount": 170.35,
             "external_id": "b3mov-b"},
        ])
        common.corrigir_tipo_reembolso(conn, [{
            **base, "type": common.TIPO_REEMBOLSO_BTC, "total_amount": 170.35}])

    assert [t for _e, t, _v in _linhas(engine)] == [
        "reit_income", common.TIPO_REEMBOLSO_BTC]


# ─────────────────────────────────────────────────────────────────────────────
# XP: id estável e mesma deduplicação
# ─────────────────────────────────────────────────────────────────────────────

def _xp_bytes(*linhas) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Proventos Recebidos"
    ws.append(["Produto", "Pagamento", "Tipo de Evento", "Instituição",
               "Quantidade", "Preço unitário", "Valor líquido"])
    for linha in linhas:
        ws.append(list(linha))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _xp_sem_banco_real(monkeypatch, *, aplicada_084: bool = True):
    monkeypatch.setattr(xp.settings, "OWNER_USER_ID", "u")
    monkeypatch.setattr(xp, "ensure_external_id_columns", lambda e: None)
    monkeypatch.setattr(xp, "migracao_083_aplicada", lambda e: True)
    monkeypatch.setattr(xp, "migracao_084_aplicada", lambda e: aplicada_084)
    monkeypatch.setattr(xp, "_ensure_portfolio", lambda c, u: "p")
    monkeypatch.setattr(xp, "_ensure_xp_account", lambda c, u: "a")
    monkeypatch.setattr(
        xp, "get_or_create_asset",
        lambda conn, ticker, name=None, asset_class=None: ticker,
    )


_LINHA_XP = ["BBAS3 - BANCO DO BRASIL", "11/09/2026", "Reembolso", "XP",
             0, 0, 170.35]


def test_id_da_xp_continua_o_da_versao_antiga(monkeypatch):
    """Com o tipo novo no hash, a linha já gravada ganharia outro id e a
    reimportação a gravaria de novo ao lado da antiga."""
    engine = _engine()
    _xp_sem_banco_real(monkeypatch)
    antigo = common.make_external_id(
        "xpcsl-inc", ["2026-09-11", "BBAS3", "reit_income", "170.35"])
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO dividends (user_id, asset_id, type, total_amount,
                                   payment_date, external_id)
            VALUES ('u', 'BBAS3', 'reit_income', 170.35, :d, :e)
        """), {"d": date(2026, 9, 11), "e": antigo})

    resumo = xp._parse_single(
        ("relatorio-consolidado-mensal-2026-setembro.xlsx", _xp_bytes(_LINHA_XP)),
        engine,
    )

    assert resumo["duplicates_skipped"] == 1
    assert _linhas(engine) == [(antigo, common.TIPO_REEMBOLSO_BTC, 170.35)]


def test_reembolso_pela_b3_e_pela_xp_entra_uma_vez(monkeypatch):
    engine = _engine()
    _b3_sem_banco_real(monkeypatch)
    _xp_sem_banco_real(monkeypatch)

    mov.parse(_movimentacao_xlsx(_LINHA_B3), engine)
    resumo = xp._parse_single(
        ("relatorio-consolidado-mensal-2026-setembro.xlsx", _xp_bytes(_LINHA_XP)),
        engine,
    )

    assert resumo["duplicates_skipped"] == 1
    assert len(_linhas(engine)) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Leitura: renda, rótulo e IRPF
# ─────────────────────────────────────────────────────────────────────────────

def test_reembolso_conta_como_renda_com_rotulo_proprio():
    from core.proventos import _TIPO_LABEL, TIPOS_RENDA_PESSOAL

    assert common.TIPO_REEMBOLSO_BTC in TIPOS_RENDA_PESSOAL
    assert "FII" not in _TIPO_LABEL[common.TIPO_REEMBOLSO_BTC]


def test_irpf_manda_o_reembolso_para_a_classificar_inclusive_em_fii():
    from core.irpf_declaracao import rendimentos

    ps = [
        {"data": date(2025, 6, 12), "ticker": "BBAS3",
         "tipo": common.TIPO_REEMBOLSO_BTC, "valor": 517.32, "nome": None},
        {"data": date(2025, 6, 12), "ticker": "HGLG11",
         "tipo": common.TIPO_REEMBOLSO_BTC, "valor": 10, "nome": None},
    ]
    r = rendimentos(ps, 2025, {}, {}, set())

    assert r["fii"]["total"] == Decimal("0")
    assert r["dividendos"]["total"] == Decimal("0")
    assert sorted(x["tickers"][0] for x in r["a_classificar"]["linhas"]) == [
        "BBAS3", "HGLG11"]


def test_migracao_084_amplia_a_check_e_reclassifica_pelo_lancamento_cru():
    sql = (Path(__file__).resolve().parents[1] / "supabase_unificado" / "schema"
           / "084_dividends_reembolso_btc.sql").read_text(encoding="utf-8")
    assert "'lending_reimbursement'" in sql
    assert "lower(e.movement) = 'reembolso'" in sql
    assert "d.type = 'reit_income'" in sql
