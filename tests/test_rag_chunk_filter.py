"""Limpeza de rodapé e filtro de relevância de chunks CVM/IPE (core.rag_b3)."""
from core.rag_b3 import (
    _chunk_is_relevant,
    _clean_chunk_text,
    format_rag_context,
)

# Trechos reais (encurtados) de documentos CVM/IPE da PETR3.
_FOOTER = ("www.petrobras.com.br/ri Para mais informações : PETRÓLEO BRASILEIRO "
           "S.A. – PETROBRAS | Relações com Investidores E-mail: petroinvest@petrobras.com.br "
           "Av. Henrique Valadares 28 – 9º andar – 20031-030 – Rio de Janeiro, RJ "
           "Tel.: 55 (21) 3224-1510/9947 | 0800-282-1540")
_DISCLAIMER = ("Este documento pode conter previsões segundo o significado da Seção 27A da "
               "Lei de Valores Mobiliários de 1933 que refletem apenas expectativas dos "
               "administradores da Companhia. Os resultados futuros das operações podem "
               "diferir das atuais expectativas, e o leitor não deve se basear "
               "exclusivamente nas informações aqui contidas.")
_FATO = ("Petrobras revisa projeção de CAPEX para 2024 — Rio de Janeiro, 08 de agosto de "
         "2024 – A Petrobras informa que sua projeção de CAPEX total para 2024 foi revista "
         "para um patamar entre US$ 13,5 bilhões e US$ 14,5 bilhões.")


def test_footer_removido():
    cleaned = _clean_chunk_text(_FOOTER)
    # site, e-mail, telefone, 0800, CEP/endereço e "Para mais informações" saem
    for ruido in ("www.", "@petrobras", "0800", "3224-1510", "20031-030", "Para mais informações"):
        assert ruido not in cleaned
    # o resíduo é curto e sem sinal factual → não-relevante
    assert _chunk_is_relevant(cleaned) is False


def test_disclaimer_descartado():
    # disclaimer jurídico sem nenhum número/fato → descartado
    assert _chunk_is_relevant(_clean_chunk_text(_DISCLAIMER)) is False


def test_fato_relevante_mantido():
    cleaned = _clean_chunk_text(_FATO)
    assert _chunk_is_relevant(cleaned) is True
    assert "CAPEX" in cleaned and "13,5 bilhões" in cleaned


def test_disclaimer_colado_a_fato_e_mantido():
    # chunker às vezes cola o fim do disclaimer ao início do fato; o sinal factual prevalece
    assert _chunk_is_relevant(_clean_chunk_text(_DISCLAIMER + " " + _FATO)) is True


def test_format_ordena_cronologicamente():
    chunks = [
        {"chunk_text": "Petrobras informa que pagará dividendos.", "data_doc": "2025-01-10",
         "tipo_doc": "IPE", "titulo": "Dividendos"},
        {"chunk_text": "Petrobras revisa guidance de produção para 2,8 milhões boed.",
         "data_doc": "2023-09-11", "tipo_doc": "IPE", "titulo": "Guidance"},
    ]
    out = format_rag_context(chunks, max_chars=4000)
    # o evento de 2023 deve aparecer ANTES do de 2025 (ordem temporal)
    assert out.index("2023-09-11") < out.index("2025-01-10")
