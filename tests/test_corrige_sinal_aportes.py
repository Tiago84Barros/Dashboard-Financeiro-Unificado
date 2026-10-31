"""O script de correção de sinal só mira aporte positivo de conta de caixa."""
from __future__ import annotations

from sqlalchemy import text

from scripts import corrige_sinal_aportes_importados as script


def test_sql_do_alvo_exige_transfer_positiva_de_caixa_em_investimento():
    sql = script.SQL_CANDIDATAS
    assert "t.amount > 0" in sql
    assert "t.type = 'transfer'" in sql
    assert "'checking'" in sql and "'credit_card'" not in sql
    assert "c.type = 'investment'" in sql and "'Renda Fixa'" in sql
    assert set(text(sql)._bindparams) == set()


def test_update_e_idempotente_e_usa_cast_no_bind():
    assert set(text(script.SQL_INVERTE)._bindparams) == {"ids"}
    assert "CAST(:ids AS uuid[])" in script.SQL_INVERTE
    assert "AND amount > 0" in script.SQL_INVERTE


def test_resgate_e_recebimento_ficam_fora_do_alvo():
    linhas = [
        {"descricao": "Tesouro Direto [Renda Fixa]"},
        {"descricao": "Transferido para a Rico [Renda Variável]"},
        {"descricao": "Resgate Tesouro Direto [Renda Fixa]"},
        {"descricao": "Pix recebido de NOMAD FINTECH INC"},
        {"descricao": "Dividendos recebidos [Renda Variável]"},
    ]
    alvos = [linha["descricao"] for linha in script.filtrar_alvos(linhas)]
    assert alvos == ["Tesouro Direto [Renda Fixa]", "Transferido para a Rico [Renda Variável]"]


def test_saldo_e_somas_separados_por_moeda():
    """Conta em dólar não entra como real no saldo nem na soma dos aportes."""
    assert "GROUP BY upper(currency)" in script.SQL_SALDO_CAIXA
    assert "upper(a.currency) AS moeda" in script.SQL_CANDIDATAS
    alvos = [
        {"valor": 100, "moeda": "BRL", "status": "settled"},
        {"valor": 30, "moeda": "BRL", "status": "pending"},
        {"valor": 50, "moeda": "USD", "status": "settled"},
        {"valor": 7, "moeda": None, "status": "settled"},
    ]
    assert script.somar_por_moeda(alvos) == {"BRL": 137.0, "USD": 50.0}
    assert script.somar_por_moeda(alvos, so_liquidadas=True) == {"BRL": 107.0, "USD": 50.0}
    assert script._fmt("USD", 1234.5) == "USD 1,234.50"
    assert script._fmt("BRL", 10) == "R$ 10.00"
