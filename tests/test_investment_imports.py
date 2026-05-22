"""
tests/test_investment_imports.py
================================
Testes dos helpers puros de importação de investimentos.

Não dependem de banco, Streamlit ou arquivos reais — rodam em qualquer
ambiente Python com sqlalchemy + openpyxl instalados (mas openpyxl não é
exercitado aqui).

Execução:
    pytest tests/test_investment_imports.py -v

Se pytest não estiver disponível, é possível rodar manualmente:
    python tests/test_investment_imports.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

# Permite rodar standalone: adiciona raiz do projeto ao sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_pipeline.importers.investments.common import (  # noqa: E402
    classify_movement,
    classify_ticker,
    make_external_id,
    parse_date_br,
    parse_ticker_from_produto,
    safe_error,
    to_float_br,
)


# ─────────────────────────────────────────────────────────────────────────────
# to_float_br
# ─────────────────────────────────────────────────────────────────────────────

def test_to_float_br_decimal_brasileiro():
    assert to_float_br("1.234,56") == 1234.56


def test_to_float_br_inteiro():
    assert to_float_br("1234") == 1234.0


def test_to_float_br_com_moeda():
    assert to_float_br("R$ 1.234,56") == 1234.56


def test_to_float_br_vazio_e_hifen():
    assert to_float_br("") is None
    assert to_float_br("-") is None
    assert to_float_br(None) is None


def test_to_float_br_float_e_int_passthrough():
    assert to_float_br(3.14) == 3.14
    assert to_float_br(7) == 7.0


def test_to_float_br_negativo():
    assert to_float_br("-1.234,56") == -1234.56


# ─────────────────────────────────────────────────────────────────────────────
# parse_date_br
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_date_br_formato_padrao():
    assert parse_date_br("15/03/2025") == date(2025, 3, 15)


def test_parse_date_br_iso():
    assert parse_date_br("2025-03-15") == date(2025, 3, 15)


def test_parse_date_br_invalido():
    assert parse_date_br("ontem") is None
    assert parse_date_br("") is None
    assert parse_date_br(None) is None


def test_parse_date_br_datetime_objeto():
    from datetime import datetime
    assert parse_date_br(datetime(2025, 3, 15, 10, 0)) == date(2025, 3, 15)


# ─────────────────────────────────────────────────────────────────────────────
# make_external_id
# ─────────────────────────────────────────────────────────────────────────────

def test_make_external_id_determinstico():
    a = make_external_id("b3neg", ["15/03/2025", "buy", "PETR4", 100, 30.00])
    b = make_external_id("b3neg", ["15/03/2025", "buy", "PETR4", 100, 30.00])
    assert a == b
    assert a.startswith("b3neg-")
    assert len(a) == len("b3neg-") + 16


def test_make_external_id_muda_quando_qualquer_parte_muda():
    a = make_external_id("b3neg", ["15/03/2025", "buy", "PETR4", 100, 30.00])
    b = make_external_id("b3neg", ["15/03/2025", "buy", "PETR4", 100, 30.01])
    assert a != b


# ─────────────────────────────────────────────────────────────────────────────
# classify_ticker
# ─────────────────────────────────────────────────────────────────────────────

def test_classify_ticker_fii():
    assert classify_ticker("MXRF11") == "reit"
    assert classify_ticker("HGLG11") == "reit"


def test_classify_ticker_acao():
    assert classify_ticker("PETR4") == "stock"
    assert classify_ticker("ITUB3") == "stock"


def test_classify_ticker_renda_fixa():
    assert classify_ticker("TESOURO_SELIC_2029") == "fixed_income"
    assert classify_ticker("CDB-INTER") == "fixed_income"


def test_classify_ticker_vazio_ou_invalido():
    assert classify_ticker("") == "other"
    assert classify_ticker("XYZ") == "stock"  # heurística cautelosa


# ─────────────────────────────────────────────────────────────────────────────
# classify_movement
# ─────────────────────────────────────────────────────────────────────────────

def test_classify_movement_dividendo():
    assert classify_movement("Dividendo") == ("income", "dividend")


def test_classify_movement_jcp():
    assert classify_movement("Juros sobre Capital Próprio") == ("income", "jcp")


def test_classify_movement_rendimento():
    assert classify_movement("Rendimento") == ("income", "reit_income")


def test_classify_movement_bonificacao():
    assert classify_movement("Bonificação em Ativos", "Credito") == ("transaction", "buy")


def test_classify_movement_desdobro():
    assert classify_movement("Desdobro", "Credito") == ("transaction", "buy")


def test_classify_movement_compra_e_venda_sao_skipadas():
    # Vêm do arquivo Negociação — ignorar aqui para não duplicar
    assert classify_movement("Compra")[0] == "skip"
    assert classify_movement("Venda")[0] == "skip"


def test_classify_movement_emprestimo_skip():
    assert classify_movement("Empréstimo")[0] == "skip"


def test_classify_movement_desconhecido_none():
    assert classify_movement("Estranhíssimo") is None


# ─────────────────────────────────────────────────────────────────────────────
# parse_ticker_from_produto
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_ticker_simples():
    assert parse_ticker_from_produto("PETR4") == ("PETR4", "PETR4")


def test_parse_ticker_com_nome():
    assert parse_ticker_from_produto("BBAS3 - BANCO DO BRASIL") == ("BBAS3", "BANCO DO BRASIL")


def test_parse_ticker_vazio():
    assert parse_ticker_from_produto("") == ("", "")
    assert parse_ticker_from_produto(None) == ("", "")


# ─────────────────────────────────────────────────────────────────────────────
# safe_error
# ─────────────────────────────────────────────────────────────────────────────

def test_safe_error_mascara_connection_string():
    msg = safe_error("erro: postgresql://user:senha@host:5432/db indisponivel")
    assert "postgresql://" not in msg
    assert "***" in msg


def test_safe_error_mascara_senha():
    msg = safe_error("falha: senha=12345abcde no payload")
    assert "12345abcde" not in msg
    assert "***" in msg


def test_safe_error_limita_tamanho():
    long = "x" * 500
    assert len(safe_error(long, max_len=80)) == 80


# ─────────────────────────────────────────────────────────────────────────────
# Nomad — helpers de parsing USD
# ─────────────────────────────────────────────────────────────────────────────

from data_pipeline.importers.investments.nomad_pdf import (  # noqa: E402
    _to_float_usd, _parse_iso_date, _parse_us_date,
    _is_apex, _is_drivewealth, _is_monthly_statement,
)


def test_nomad_to_float_usd_decimal_americano():
    assert _to_float_usd("1,234.56") == 1234.56


def test_nomad_to_float_usd_decimal_europeu():
    assert _to_float_usd("1.234,56") == 1234.56


def test_nomad_to_float_usd_com_dollar():
    assert _to_float_usd("$420.50") == 420.50


def test_nomad_to_float_usd_negativo_parenteses():
    assert _to_float_usd("(123.45)") == -123.45


def test_nomad_to_float_usd_vazio():
    assert _to_float_usd("") == 0.0
    assert _to_float_usd("-") == 0.0
    assert _to_float_usd(None) == 0.0


def test_nomad_parse_iso_date():
    assert _parse_iso_date("2025-03-15") == date(2025, 3, 15)
    assert _parse_iso_date("invalido") is None


def test_nomad_parse_us_date():
    assert _parse_us_date("3/15/2025") == date(2025, 3, 15)
    assert _parse_us_date("03/15/2025") == date(2025, 3, 15)
    assert _parse_us_date("2025-03-15") is None


def test_nomad_is_apex_reconhece_apex():
    text = "2025-03-15 2025-03-17  You bought SPY\n2025-03-15 2025-03-17 SPY ..."
    assert _is_apex(text) is True


def test_nomad_is_apex_descarta_outros():
    assert _is_apex("Algum texto qualquer") is False


def test_nomad_is_drivewealth_reconhece():
    text = "DriveWealth Securities ... Principal Amount $123.45"
    assert _is_drivewealth(text) is True


def test_nomad_is_monthly_statement_por_filename():
    assert _is_monthly_statement("", "investments_uuid_monthly_statement_2025103102_pdf.pdf") is True
    assert _is_monthly_statement("", "monthly-statement-jan-2026.pdf") is True


def test_nomad_is_monthly_statement_por_conteudo():
    text = "ACCOUNT STATEMENT\nStatement Period: 10/01/2025 - 10/31/2025\nBeginning Balance: $1,000"
    assert _is_monthly_statement(text, "nota.pdf") is True


def test_nomad_is_monthly_statement_descarta_notas_negociacao():
    text = "You bought 100 shares of SPY at $420.50"
    assert _is_monthly_statement(text, "trade_confirmation.pdf") is False


# ─────────────────────────────────────────────────────────────────────────────
# Runner standalone (sem pytest)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import traceback
    passed = 0
    failed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  OK   {name}")
            passed += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL {name}: {exc}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passou, {failed} falhou.")
    sys.exit(0 if failed == 0 else 1)
