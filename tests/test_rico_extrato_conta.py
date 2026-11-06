"""Extrato da conta da Rico: reconhecimento, leitura e deduplicação.

O arquivo real tem a aba "Planilha1", e por isso o uploader da B3 o recusava
("1 arquivo não reconhecido"). Os testes montam um extrato sintético com o
mesmo leiaute — sem nome nem conta de ninguém.
"""
from __future__ import annotations

import io
import re
import zipfile
from datetime import date, datetime

import pytest

openpyxl = pytest.importorskip("openpyxl")
from sqlalchemy import create_engine, text  # noqa: E402

from data_pipeline.importers.investments import rico_extrato_conta as rico  # noqa: E402
from data_pipeline.importers.investments.b3_sniffer import detect  # noqa: E402

_CAB = (None, "Movimentação", "Liquidação", "Lançamento", None,
        "Valor (R$)", "Saldo (R$)")


def _lanc(dia: int, historico: str, valor: float) -> tuple:
    d = datetime(2026, 10, dia)
    return (None, d, d, historico, None, valor, 0.0)


_LANCAMENTOS = [
    _lanc(30, "Taxa de intermediação doador  GMAT3 2026102801177238840001-1", -0.49),
    _lanc(30, "Crédito Ref. Taxa de Remuneração BTC GMAT", 0.98),
    _lanc(22, "RENDIMENTOS DE CLIENTES MANA11 S/            368", 44.16),
    _lanc(15, "DIVIDENDOS DE CLIENTES BBAS3 S/             100", 52.10),
    _lanc(15, "JUROS S/ CAPITAL DE CLIENTES ITSA4 S/        200", 20.00),
    _lanc(13, "TED BCO 348 AGE 1  CTA 0000000  - RETIRADA EM C/C", -100.0),
    _lanc(7, "RENDIMENTO FUNDO FECHADO BALCÃO   PIER11 - Pátria Infr", 346.32),
    (None, datetime(2026, 10, 2), datetime(2026, 10, 6),
     "OPERAÇÕES EM BOLSA PR 02/10/2026 NOTA Nº 1", None, 1343.11, 0.0),
]


def _extrato(*, titulo: bool = True, futuros: list[tuple] | None = None,
             aba: str = "Planilha1") -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = aba
    ws.append(())
    if titulo:
        ws.append((None,) * 6 + ("Extrato da conta",))
    ws.append((None, "Saldo total projetado", None, None, None, None, 327.45))
    ws.append(())
    ws.append(_CAB)
    for linha in _LANCAMENTOS:
        ws.append(linha)
    ws.append(())
    ws.append((None, "Lançamentos futuros"))
    ws.append(_CAB)
    for linha in futuros or [(None, "", "", "Não há lançamentos para o período",
                              None, "", "")]:
        ws.append(linha)
    ws.append((None, "Extrato para simples conferência"))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _com_dimensao_curta(dados: bytes, ref: str = "B1:G6") -> bytes:
    """Reescreve a <dimension> como o export da Rico faz: menor que o conteúdo."""
    entrada = zipfile.ZipFile(io.BytesIO(dados))
    saida_buf = io.BytesIO()
    with zipfile.ZipFile(saida_buf, "w", zipfile.ZIP_DEFLATED) as saida:
        for item in entrada.infolist():
            conteudo = entrada.read(item.filename)
            if item.filename.startswith("xl/worksheets/sheet"):
                conteudo = re.sub(
                    rb'<dimension ref="[^"]+"', f'<dimension ref="{ref}"'.encode(),
                    conteudo,
                )
            saida.writestr(item, conteudo)
    return saida_buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# Reconhecimento
# ─────────────────────────────────────────────────────────────────────────────

def test_detector_da_b3_reconhece_o_extrato_pela_planilha():
    assert detect(_extrato()) == "rico_cc"


def test_sem_o_titulo_o_arquivo_continua_recusado():
    """Só o cabeçalho não basta: "Planilha1" com colunas parecidas pode ser
    qualquer coisa, e gravar sob a fonte errada é pior que recusar."""
    assert detect(_extrato(titulo=False)) is None


def test_aba_planilha1_vazia_continua_recusada():
    wb = openpyxl.Workbook()
    wb.active.title = "Planilha1"
    buf = io.BytesIO()
    wb.save(buf)
    assert detect(buf.getvalue()) is None


def test_lote_roda_o_extrato_da_rico_depois_da_movimentacao():
    from views.configuracoes import planejar_lote_b3

    planejados, recusados = planejar_lote_b3(
        [("rico.xlsx", b"r"), ("mov.xlsx", b"m"), ("neg.xlsx", b"n")],
        detector=lambda b: {b"r": "rico_cc", b"m": "b3_mov",
                            b"n": "b3_neg"}[b],
        listar_abas=lambda b: [],
    )
    assert [c for c, _, _ in planejados] == ["b3_neg", "b3_mov", "rico_cc"]
    assert recusados == []


def test_tela_tem_job_e_parser_para_o_extrato():
    from views.configuracoes import _B3_JOBS, _B3_ORDEM

    assert "rico_cc" in _B3_ORDEM
    cfg = _B3_JOBS["rico_cc"]
    import data_pipeline.importers.investments as inv

    assert hasattr(inv, cfg["parser_attr"])


# ─────────────────────────────────────────────────────────────────────────────
# Leitura
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "historico, esperado",
    [
        ("RENDIMENTOS DE CLIENTES MANA11 S/            368",
         {"ticker": "MANA11", "tipo": "reit_income", "quantidade": 368.0}),
        ("DIVIDENDOS DE CLIENTES BBAS3 S/ 1.200",
         {"ticker": "BBAS3", "tipo": "dividend", "quantidade": 1200.0}),
        ("RENDIMENTO FUNDO FECHADO BALCÃO   PIER11 - Pátria Infr",
         {"ticker": "PIER11", "tipo": "reit_income", "quantidade": None}),
        ("JUROS S/ CAPITAL DE CLIENTES ITSA4 S/ 200", None),
        ("Crédito Ref. Taxa de Remuneração BTC GMAT", None),
        ("OPERAÇÕES EM BOLSA PR 07/10/2026 NOTA Nº 145930013", None),
        ("TED BCO 348 AGE 1  CTA 1  - RETIRADA EM C/C", None),
    ],
)
def test_classificar_lancamento(historico, esperado):
    assert rico.classificar_lancamento(historico) == esperado


def test_ler_lancamentos_pega_so_proventos_pagos():
    wb = openpyxl.load_workbook(io.BytesIO(_extrato(
        futuros=[_lanc(28, "RENDIMENTOS DE CLIENTES XPML11 S/ 10", 9.0)],
    )))
    proventos, ignorados = rico.ler_lancamentos(
        wb.active.iter_rows(values_only=True)
    )
    por_ticker = {p["ticker"]: p for p in proventos}

    # Lançamento futuro não entra: ainda não foi pago.
    assert set(por_ticker) == {"MANA11", "BBAS3", "PIER11"}
    assert ignorados == len(_LANCAMENTOS) - 3
    mana = por_ticker["MANA11"]
    assert mana["total"] == 44.16
    assert mana["quantidade"] == 368.0
    assert mana["por_unidade"] == pytest.approx(0.12)
    assert mana["pagamento"] == date(2026, 10, 22)
    pier = por_ticker["PIER11"]
    # O extrato não diz quantas cotas: nada de "1 cota a R$ 346,32".
    assert (pier["quantidade"], pier["por_unidade"]) == (None, None)
    assert pier["total"] == 346.32


def test_parse_le_alem_da_dimensao_declarada(monkeypatch):
    """O export da Rico declara dimensão menor que o conteúdo; em read_only o
    openpyxl pararia nela e perderia os últimos lançamentos sem erro."""
    dados = _com_dimensao_curta(_extrato())
    assert detect(dados) == "rico_cc"
    engine = _engine()
    _sem_banco_real(monkeypatch)

    resumo = rico.parse(dados, engine)

    assert resumo["incomes_imported"] == 2, resumo


# ─────────────────────────────────────────────────────────────────────────────
# Gravação e deduplicação
# ─────────────────────────────────────────────────────────────────────────────

def _engine():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE dividends (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                user_id TEXT NOT NULL, asset_id TEXT NOT NULL,
                type TEXT NOT NULL, amount_per_unit NUMERIC NOT NULL,
                quantity NUMERIC NOT NULL, total_amount NUMERIC NOT NULL,
                ex_date DATE, payment_date DATE NOT NULL, external_id TEXT
            )
        """))
        conn.execute(text("""
            CREATE UNIQUE INDEX ux_div_ext ON dividends(external_id)
            WHERE external_id IS NOT NULL
        """))
    return engine


def _sem_banco_real(monkeypatch):
    monkeypatch.setattr(rico.settings, "OWNER_USER_ID", "user-1")
    monkeypatch.setattr(rico, "ensure_external_id_columns", lambda e: None)
    monkeypatch.setattr(
        rico, "get_or_create_asset",
        lambda conn, ticker, name=None, asset_class=None: f"asset-{ticker}",
    )


def test_reimportar_o_mesmo_extrato_nao_duplica(monkeypatch):
    engine = _engine()
    _sem_banco_real(monkeypatch)

    primeiro = rico.parse(_extrato(), engine)
    segundo = rico.parse(_extrato(), engine)

    assert primeiro["incomes_imported"] == 2
    assert segundo["incomes_imported"] == 0
    assert segundo["duplicates_skipped"] == 2
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM dividends")).scalar() == 2


def test_provento_ja_vindo_da_movimentacao_b3_nao_entra_de_novo(monkeypatch):
    """O mesmo rendimento pela B3 (external_id b3mov) e pela Rico: um só."""
    engine = _engine()
    _sem_banco_real(monkeypatch)
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO dividends (user_id, asset_id, type, amount_per_unit,
                quantity, total_amount, payment_date, external_id)
            VALUES ('user-1', 'asset-MANA11', 'reit_income', 0.12, 368,
                    44.16, '2026-10-22', 'b3mov-123')
        """))

    resumo = rico.parse(_extrato(), engine)

    assert resumo["incomes_imported"] == 1
    assert resumo["duplicates_skipped"] == 1
    with engine.connect() as conn:
        n = conn.execute(text(
            "SELECT COUNT(*) FROM dividends WHERE asset_id = 'asset-MANA11'"
        )).scalar()
    assert n == 1


def test_sem_owner_falha_sem_gravar(monkeypatch):
    engine = _engine()
    _sem_banco_real(monkeypatch)
    monkeypatch.setattr(rico.settings, "OWNER_USER_ID", "")

    resumo = rico.parse(_extrato(), engine)

    assert resumo["status"] == "failed"
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM dividends")).scalar() == 0


def test_provento_sem_quantidade_nao_e_gravado_e_vem_nomeado(monkeypatch):
    """Banco ainda sem a SQL 083 (quantity NOT NULL): o rendimento de fundo de
    balcão sem quantidade sai como aviso, com valor e data, em vez de virar 1
    cota inventada. Com a 083, ver o teste seguinte."""
    engine = _engine()
    _sem_banco_real(monkeypatch)

    resumo = rico.parse(_extrato(), engine)

    assert resumo["status"] == "partial_success"
    assert any("PIER11" in e and "346.32" in e for e in resumo["errors"])
    with engine.connect() as conn:
        n = conn.execute(text(
            "SELECT COUNT(*) FROM dividends WHERE asset_id = 'asset-PIER11'"
        )).scalar()
    assert n == 0


def test_com_a_083_provento_sem_quantidade_grava_vazio(monkeypatch):
    """Com quantity/amount_per_unit anuláveis, o PIER11 entra com os dois
    vazios -- o total é fato, a quantidade não foi dita."""
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
    _sem_banco_real(monkeypatch)

    resumo = rico.parse(_extrato(), engine)

    assert resumo["status"] == "success", resumo["errors"]
    assert resumo["incomes_imported"] == 3
    with engine.connect() as conn:
        linha = conn.execute(text(
            "SELECT quantity, amount_per_unit, total_amount FROM dividends "
            "WHERE asset_id = 'asset-PIER11'"
        )).one()
    assert (linha.quantity, linha.amount_per_unit) == (None, None)
    assert float(linha.total_amount) == 346.32


def test_aba_ilegivel_nao_derruba_o_reconhecimento(monkeypatch):
    """Um .xlsx de outra origem que quebre ao ler células é recusado, não
    derruba o lote inteiro em `planejar_lote`."""
    def _quebra(aba, limite=12):
        raise ValueError("célula ilegível")

    monkeypatch.setattr(rico, "primeiras_linhas", _quebra)
    assert rico.reconhece(_extrato()) is False
    assert detect(_extrato()) is None
