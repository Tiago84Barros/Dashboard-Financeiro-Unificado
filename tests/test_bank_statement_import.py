from datetime import date

from core.bank_statement_import import (
    VALOR_ALTO_FINANCIAMENTO,
    build_bank_statement_hash,
    classify_bank_movement,
    classify_bank_movements,
    parse_c6_bank_text,
)


def _categories():
    return [
        {"id": "cat-salario", "nome": "Salario", "tipo": "income"},
        {"id": "cat-moradia", "nome": "Moradia", "tipo": "expense"},
        {"id": "cat-transporte", "nome": "Transporte", "tipo": "expense"},
        {"id": "cat-saude", "nome": "Saude", "tipo": "expense"},
        {"id": "cat-alimentacao", "nome": "Alimentacao", "tipo": "expense"},
        {"id": "cat-impostos", "nome": "Impostos e Taxas", "tipo": "expense"},
        {"id": "cat-telefone", "nome": "Telefone / Internet", "tipo": "expense"},
        {"id": "cat-resgate", "nome": "Resgate de Investimento", "tipo": "transfer"},
    ]


def test_parse_c6_text_extracts_dates_values_direction_and_hash():
    text = """
    01/05/2026 Entrada PIX Pix recebido de TIAGO DA SILVA BARROS R$ 10.000,00
    02/05/2026 Boleto EUROVILLE SALINAS RESORT R$ -650,00
    03/05/2026 Pagamento RESERVA DO LAGO R$ -1.500,00
    04/05/2026 Saida PIX Pix enviado para FULANO R$ -80,00
    05/05/2026 RESGATE DE CDB R$ 1.000,00
    """
    parsed = parse_c6_bank_text(text, file_name="c6.pdf")

    assert parsed["ok"] is True
    assert parsed["summary"]["rows"] == 5
    assert parsed["rows"][0]["data_movimento"] == date(2026, 5, 1)
    assert parsed["rows"][0]["valor"] == 10000.00
    assert parsed["rows"][0]["direcao"] == "entrada"
    assert parsed["rows"][1]["valor"] == -650.00
    assert parsed["rows"][1]["direcao"] == "saida"
    assert parsed["rows"][0]["hash_lancamento"] == build_bank_statement_hash(parsed["rows"][0])


def test_explicit_classification_rules_use_existing_category_ids():
    parsed = parse_c6_bank_text(
        """
        01/05/2026 Entrada PIX Pix recebido de TIAGO DA SILVA BARROS R$ 10.000,00
        02/05/2026 Boleto EUROVILLE SALINAS RESORT R$ -650,00
        03/05/2026 Pagamento RESERVA DO LAGO R$ -1.500,00
        """,
        file_name="c6.pdf",
    )

    classified = classify_bank_movements(parsed["rows"], _categories())

    salary, condo, financing = classified
    assert salary["categoria_id"] == "cat-salario"
    assert salary["categoria_sugerida_texto"] == "Salario"
    assert salary["status_classificacao"] == "sugerida"
    assert salary["confianca_classificacao"] >= 0.95

    assert condo["categoria_id"] == "cat-moradia"
    assert condo["categoria_sugerida_texto"] == "Condominio"
    assert condo["status_classificacao"] == "sugerida"

    assert abs(financing["valor"]) >= VALOR_ALTO_FINANCIAMENTO
    assert financing["categoria_id"] == "cat-moradia"
    assert financing["categoria_sugerida_texto"] == "Financiamento"


def test_automatic_rules_and_pending_behaviour():
    parsed = parse_c6_bank_text(
        """
        05/05/2026 RESGATE DE CDB R$ 1.000,00
        06/05/2026 Pagamento TIM CELULAR R$ -89,90
        07/05/2026 Pagamento EQUATORIAL R$ -220,00
        08/05/2026 Pagamento SECRETARIA DO TESOURO NACIONAL R$ -110,00
        09/05/2026 Saida PIX Pix enviado para FULANO R$ -80,00
        """,
        file_name="c6.pdf",
    )

    classified = classify_bank_movements(parsed["rows"], _categories())

    assert classified[0]["categoria_id"] == "cat-resgate"
    assert classified[0]["categoria_sugerida_texto"] == "Resgate de Investimento"
    assert classified[1]["categoria_id"] == "cat-telefone"
    assert classified[2]["categoria_id"] == "cat-moradia"
    assert classified[3]["categoria_id"] == "cat-impostos"
    assert classified[4]["categoria_id"] is None
    assert classified[4]["status_classificacao"] == "pendente"


def test_saved_rule_has_priority_over_automatic_rule():
    row = parse_c6_bank_text(
        "10/05/2026 Pagamento IFOOD RESTAURANTE R$ -55,00",
        file_name="c6.pdf",
    )["rows"][0]
    saved_rules = [
        {
            "banco": "C6 Bank",
            "tipo_original_banco": "Pagamento",
            "palavra_chave": "ifood",
            "descricao_normalizada": "",
            "category_id": "cat-transporte",
        }
    ]

    classified = classify_bank_movement(row, _categories(), saved_rules)

    assert classified["categoria_id"] == "cat-transporte"
    assert classified["status_classificacao"] == "confirmada"
    assert classified["classificacao_motivo"] == "Regra salva pelo usuario"
