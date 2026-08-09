"""Mapa canonico de setor entre B3, EUA e FII."""
import pytest

from core.global_portfolio.taxonomy import (
    ROTULOS,
    SETORES_CANONICOS,
    nao_mapeados,
    setor_canonico,
)


def test_todo_setor_canonico_tem_rotulo():
    assert set(ROTULOS) == set(SETORES_CANONICOS)


def test_setores_canonicos_sao_deterministicos():
    assert SETORES_CANONICOS == tuple(sorted(SETORES_CANONICOS))


@pytest.mark.parametrize("setor,esperado", [
    ("Petróleo, Gás e Biocombustíveis", "energy"),
    ("Materiais Básicos", "materials"),
    ("Bens Industriais", "industrials"),
    ("Consumo não Cíclico", "consumer_staples"),
    ("Consumo Cíclico", "consumer"),
    ("Saúde", "health_care"),
    ("Tecnologia da Informação", "technology"),
    ("Comunicações", "telecom"),
    ("Utilidade Pública", "utilities"),
    ("Financeiro", "financials"),
])
def test_setores_da_b3(setor, esperado):
    assert setor_canonico("b3", setor) == esperado


@pytest.mark.parametrize("setor,esperado", [
    ("Energy", "energy"),
    ("Basic Materials", "materials"),
    ("Materials", "materials"),
    ("Industrials", "industrials"),
    ("Consumer Defensive", "consumer_staples"),
    ("Consumer Cyclical", "consumer"),
    ("Healthcare", "health_care"),
    ("Health Care", "health_care"),
    ("Technology", "technology"),
    ("Information Technology", "technology"),
    ("Communication Services", "telecom"),
    ("Utilities", "utilities"),
    ("Financial Services", "financials"),
    ("Financials", "financials"),
    ("Real Estate", "real_estate"),
])
def test_setores_do_mercado_americano(setor, esperado):
    assert setor_canonico("us", setor) == esperado


def test_fii_sempre_cai_em_real_estate_independente_do_segmento():
    assert setor_canonico("fii", "Logística", "Tijolo") == "real_estate"
    assert setor_canonico("fii", "Papel", "Papel") == "real_estate"
    assert setor_canonico("fii", None, None) == "real_estate"


def test_comparacao_ignora_acento_caixa_e_espaco():
    assert setor_canonico("b3", "  consumo NAO ciclico  ") == "consumer_staples"
    assert setor_canonico("us", "  HEALTHCARE ") == "health_care"


def test_setor_desconhecido_cai_em_other_sem_levantar():
    assert setor_canonico("b3", "Setor Inventado") == "other"
    assert setor_canonico("us", None) == "other"


def test_nao_mapeados_lista_os_pares_que_cairam_em_other():
    linhas = [
        {"asset_class": "b3", "sector": "Financeiro"},
        {"asset_class": "b3", "sector": "Setor Inventado"},
        {"asset_class": "us", "sector": "Outro Inventado"},
        {"asset_class": "b3", "sector": "Setor Inventado"},   # repetido
    ]
    assert nao_mapeados(linhas) == [("b3", "Setor Inventado"), ("us", "Outro Inventado")]


def test_nao_mapeados_ignora_setor_vazio():
    assert nao_mapeados([{"asset_class": "b3", "sector": None}]) == []
