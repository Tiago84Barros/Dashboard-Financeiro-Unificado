"""Provento sem quantidade fica sem quantidade (migração 083).

Antes, B3 Movimentação sem "Quantidade" e todo o Consolidado da XP gravavam
1 cota com o pagamento inteiro como valor por cota, e a tela de Proventos
mostrava isso como fato. Estes testes prendem:

* os três caminhos de gravação (B3 em lote, XP e Rico linha a linha) produzem
  a mesma linha para o mesmo provento, em qualquer ordem;
* a fonte que traz a quantidade completa a linha vazia ou inventada;
* a reimportação sem quantidade esvazia a própria linha inventada;
* uma posição real de 1 cota não é apagada.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from data_pipeline.importers.investments import common
from data_pipeline.importers.investments.b3_movimentacao import _parse_row

_DIA = date(2026, 10, 22)


def _engine():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE dividends (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                user_id TEXT NOT NULL, asset_id TEXT NOT NULL,
                type TEXT NOT NULL, amount_per_unit NUMERIC,
                quantity NUMERIC, total_amount NUMERIC NOT NULL,
                ex_date DATE, payment_date DATE NOT NULL, external_id TEXT
            )
        """))
        conn.execute(text("""
            CREATE UNIQUE INDEX ux_div_ext ON dividends(external_id)
            WHERE external_id IS NOT NULL
        """))
    return engine


def _row(ext: str, qtd: float | None, total: float = 44.16) -> dict:
    q, apu = common.quantidade_e_valor_unitario(qtd, total)
    return {
        "user_id": "u", "asset_id": "MANA11", "type": "reit_income",
        "amount_per_unit": apu, "quantity": q, "total_amount": total,
        "ex_date": None, "payment_date": _DIA, "external_id": ext,
    }


def _via_lote(conn, row: dict) -> None:
    common.batch_insert_dividends(conn, [row])


def _via_linha(conn, row: dict) -> None:
    common.insert_dividend(
        conn, user_id=row["user_id"], asset_id=row["asset_id"],
        div_type=row["type"], amount_per_unit=row["amount_per_unit"],
        quantity=row["quantity"], total_amount=row["total_amount"],
        ex_date=None, payment_date=row["payment_date"],
        external_id=row["external_id"],
    )


def _linhas(engine) -> list[tuple]:
    with engine.connect() as conn:
        return [
            (r[0], None if r[1] is None else float(r[1]),
             None if r[2] is None else float(r[2]), float(r[3]))
            for r in conn.execute(text(
                "SELECT external_id, quantity, amount_per_unit, total_amount "
                "FROM dividends ORDER BY external_id"
            ))
        ]


# ─────────────────────────────────────────────────────────────────────────────
# Leitura: ninguém inventa quantidade
# ─────────────────────────────────────────────────────────────────────────────

def test_quantidade_e_valor_unitario():
    assert common.quantidade_e_valor_unitario(None, 44.16) == (None, None)
    assert common.quantidade_e_valor_unitario(0, 44.16) == (None, None)
    assert common.quantidade_e_valor_unitario(368, 44.16) == (368, 0.12)


def test_movimentacao_b3_sem_quantidade_nao_inventa_uma_cota():
    linha = ("Credito", "22/10/2026", "Rendimento", "PIER11 - PATRIA INFRA",
             "RICO", None, None, "346,32")
    parsed = _parse_row(linha)
    assert parsed["quantity"] is None
    assert parsed["amount_per_unit"] is None
    assert parsed["total_amount"] == 346.32


def test_movimentacao_b3_com_quantidade_continua_igual():
    linha = ("Credito", "22/10/2026", "Rendimento", "MANA11 - FII",
             "RICO", "368", "0,12", "44,16")
    parsed = _parse_row(linha)
    assert (parsed["quantity"], parsed["amount_per_unit"]) == (368.0, 0.12)


# ─────────────────────────────────────────────────────────────────────────────
# Equivalência entre os caminhos e entre as ordens
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("primeiro", [_via_lote, _via_linha])
@pytest.mark.parametrize("segundo", [_via_lote, _via_linha])
@pytest.mark.parametrize("ordem", ["vazia_antes", "cheia_antes"])
def test_mesmo_provento_por_duas_fontes_acaba_com_a_quantidade(
    primeiro, segundo, ordem,
):
    """B3 sem quantidade + Rico com quantidade (ou o contrário), por lote ou
    linha a linha: uma linha só, com 368 cotas a R$ 0,12."""
    engine = _engine()
    vazia, cheia = _row("b3mov-1", None), _row("ricocc-1", 368)
    a, b = (vazia, cheia) if ordem == "vazia_antes" else (cheia, vazia)
    with engine.begin() as conn:
        primeiro(conn, a)
        segundo(conn, b)

    linhas = _linhas(engine)
    assert len(linhas) == 1
    assert linhas[0][1:] == (368.0, 0.12, 44.16)


def test_fonte_com_quantidade_corrige_a_linha_inventada_de_outra_fonte():
    """A linha da XP gravada antes desta correção (1 cota = total) recebe a
    quantidade da Movimentação."""
    engine = _engine()
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO dividends (user_id, asset_id, type, amount_per_unit,
                quantity, total_amount, payment_date, external_id)
            VALUES ('u', 'MANA11', 'reit_income', 44.16, 1, 44.16,
                    :d, 'xpcsl-inc-velho')
        """), {"d": _DIA})
        _via_lote(conn, _row("b3mov-1", 368))

    assert _linhas(engine) == [("xpcsl-inc-velho", 368.0, 0.12, 44.16)]


def test_reimportar_sem_quantidade_esvazia_a_propria_linha_inventada():
    engine = _engine()
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO dividends (user_id, asset_id, type, amount_per_unit,
                quantity, total_amount, payment_date, external_id)
            VALUES ('u', 'MANA11', 'reit_income', 44.16, 1, 44.16,
                    :d, 'b3mov-1')
        """), {"d": _DIA})
        _via_lote(conn, _row("b3mov-1", None))

    assert _linhas(engine) == [("b3mov-1", None, None, 44.16)]


def test_posicao_real_de_uma_cota_nao_e_apagada():
    """1 cota de verdade, informada pela fonte, fica: reimportar a mesma
    linha com quantidade 1 não muda nada, e outra fonte sem quantidade não
    mexe em linha alheia."""
    engine = _engine()
    with engine.begin() as conn:
        _via_lote(conn, _row("b3mov-1", 1))
        _via_lote(conn, _row("b3mov-1", 1))
        _via_linha(conn, _row("ricocc-1", None))

    assert _linhas(engine) == [("b3mov-1", 1.0, 44.16, 44.16)]


def test_outro_total_nao_e_o_mesmo_provento():
    engine = _engine()
    with engine.begin() as conn:
        _via_lote(conn, _row("b3mov-1", None, total=44.16))
        _via_linha(conn, _row("ricocc-1", 368, total=50.00))

    assert [(e, q) for e, q, _apu, _t in _linhas(engine)] == [
        ("b3mov-1", None), ("ricocc-1", 368.0),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Migração
# ─────────────────────────────────────────────────────────────────────────────

_SQL_083 = (Path(__file__).resolve().parents[1] / "supabase_unificado"
            / "schema" / "083_dividends_quantidade_opcional.sql")


def test_migracao_083_existe_e_libera_as_duas_colunas():
    sql = _SQL_083.read_text(encoding="utf-8")
    assert "ALTER COLUMN quantity        DROP NOT NULL" in sql
    assert "ALTER COLUMN amount_per_unit DROP NOT NULL" in sql


def test_limpeza_da_xp_so_roda_na_transicao():
    """Rodar o arquivo de novo não pode apagar 1 cota real da XP gravada
    depois: a limpeza fica dentro do IF que testa o NOT NULL."""
    sql = _SQL_083.read_text(encoding="utf-8")
    inicio_if = sql.index("is_nullable = 'NO'")
    fim_if = sql.index("END IF")
    assert inicio_if < sql.index("UPDATE dividends") < fim_if


def test_nenhum_importador_roda_a_migracao_sozinho():
    """Migração muda schema e dado: exige confirmação humana (AGENTS.md). O
    importador só consulta se ela já rodou."""
    pasta = Path(common.__file__).parent
    for arquivo in pasta.glob("*.py"):
        codigo = arquivo.read_text(encoding="utf-8")
        assert "DROP NOT NULL" not in codigo, arquivo.name
        assert "083_dividends" not in codigo or arquivo.name == "common.py", (
            arquivo.name
        )


def test_reimportacao_incompleta_da_xp_nao_apaga_uma_cota_real():
    """O external_id da XP não leva a quantidade: a mesma linha pode vir com 1
    cota real e depois sem a coluna. Só a B3, cujo id muda com a quantidade,
    esvazia o placeholder."""
    engine = _engine()
    with engine.begin() as conn:
        _via_linha(conn, _row("xpcsl-inc-1", 1))
        _via_linha(conn, _row("xpcsl-inc-1", None))
        _via_linha(conn, _row("ricocc-1", None))

    assert _linhas(engine) == [("xpcsl-inc-1", 1.0, 44.16, 44.16)]


def test_esvaziar_nao_mexe_na_linha_de_outro_dono():
    """O external_id do admin não leva o dono (`make_external_id`); a linha de
    outro usuário com o mesmo id não é da importação que está rodando."""
    engine = _engine()
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO dividends (user_id, asset_id, type, amount_per_unit,
                quantity, total_amount, payment_date, external_id)
            VALUES ('outro', 'MANA11', 'reit_income', 44.16, 1, 44.16,
                    :d, 'b3mov-1')
        """), {"d": _DIA})
        common.ajustar_quantidades_dividendos(conn, [_row("b3mov-1", None)])

    assert _linhas(engine) == [("b3mov-1", 1.0, 44.16, 44.16)]


# ─────────────────────────────────────────────────────────────────────────────
# Pelo importador da Movimentação, não só pelo helper
# ─────────────────────────────────────────────────────────────────────────────

def _movimentacao_xlsx(*linhas) -> bytes:
    import io

    import openpyxl

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


def _b3_sem_banco_real(monkeypatch, engine):
    from data_pipeline.importers.investments import b3_movimentacao as mov

    monkeypatch.setattr(mov.settings, "OWNER_USER_ID", "u")
    monkeypatch.setattr(mov, "ensure_external_id_columns", lambda e: None)
    monkeypatch.setattr(mov, "ensure_movement_events_table", lambda e: False)
    monkeypatch.setattr(mov, "get_or_create_b3_account", lambda c, u: "conta")
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
    return mov


def test_reimportar_a_movimentacao_corrige_a_linha_inventada_ja_gravada(
    monkeypatch,
):
    """O importador filtra as linhas já gravadas antes do INSERT; elas têm de
    passar pelo ajuste mesmo assim, ou a reimportação nunca corrige nada."""
    engine = _engine()
    mov = _b3_sem_banco_real(monkeypatch, engine)
    linha = ("Credito", "22/10/2026", "Rendimento", "PIER11 - PATRIA",
             "RICO", None, None, "346,32")
    ext = mov._parse_row(linha)["external_id"]
    with engine.begin() as conn:  # como a versão antiga gravava
        conn.execute(text("""
            INSERT INTO dividends (user_id, asset_id, type, amount_per_unit,
                quantity, total_amount, payment_date, external_id)
            VALUES ('u', 'PIER11', 'reit_income', 346.32, 1, 346.32,
                    :d, :e)
        """), {"d": date(2026, 10, 22), "e": ext})

    resumo = mov.parse(_movimentacao_xlsx(linha), engine)

    assert resumo["duplicates_skipped"] == 1
    assert resumo["incomes_imported"] == 0
    assert _linhas(engine) == [(ext, None, None, 346.32)]
