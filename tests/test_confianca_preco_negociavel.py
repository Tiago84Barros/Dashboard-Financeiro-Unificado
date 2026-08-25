"""A-134: fundamento nao compensa a falta de preco negociavel.

O caso que originou estes testes e real e foi medido no Supabase em
25/08/2026: LUXM3 marcava score 75,2 com rotulo "Alta" e ultimo preco de
13/05/2015. Cobertura e integridade perfeitas carregavam 82% do score, e o
frescor de preco -- os 18% restantes -- nao tinha peso para derrubar. O papel
entrava no universo de decisao (gate >= 55) como se fosse comprIavel.
"""
from core.data_confidence import (LIMIAR_MEDIA, TETO_SEM_PRECO_NEGOCIAVEL,
                                  score_ticker)

ANO = 2026

# Fundamento impecavel: todas as metricas-chave, balanco do ano, zero flags.
# E exatamente esse perfil que mascarava o preco morto.
_PERFEITO = {"n_key_ttm": 9, "ymax": 2026, "n_flags": 0,
             "frac_px_invalida": 0.0}


def _com_preco(dias):
    return dict(_PERFEITO, dias_preco=dias)


def test_preco_de_2015_nao_e_apto():
    """O caso LUXM3. Antes de A-134 isto devolvia 75,2/'Alta'."""
    r = score_ticker(_com_preco(4122), ANO)
    assert r["score"] <= TETO_SEM_PRECO_NEGOCIAVEL
    assert r["score"] < LIMIAR_MEDIA, "passaria no gate do universo de decisao"
    assert r["label"] == "Baixa"


def test_preco_parado_ha_seis_meses_nao_e_apto():
    """A familia de 205 dias (BOBR3, MGEL3, VSPT3, APTI3, BAUH3, CASN4...)."""
    assert score_ticker(_com_preco(205), ANO)["score"] < LIMIAR_MEDIA


def test_sem_preco_algum_nao_e_apto():
    r = score_ticker(dict(_PERFEITO, dias_preco=None), ANO)
    assert r["score"] < LIMIAR_MEDIA


def test_ticker_liquido_nao_e_penalizado():
    """O teto so pode morder quem perdeu o preco. PETR4 marca 100,0."""
    r = score_ticker(_com_preco(0), ANO)
    assert r["score"] == 100.0
    assert r["label"] == "Alta"


def test_iliquidez_moderada_sobrevive():
    """29 dias ainda negocia: o fator de frescor decai, mas nao zera, entao o
    teto NAO se aplica. A fronteira importa -- se o teto mordesse aqui, o
    modulo perderia small caps legitimamente pouco liquidas."""
    r = score_ticker(_com_preco(29), ANO)
    assert r["score"] > LIMIAR_MEDIA


def test_fronteira_exata_dos_30_dias():
    """Em 30 dias o proprio modulo ja declara o preco velho; o teto herda esse
    limiar em vez de inventar outro."""
    assert score_ticker(_com_preco(30), ANO)["score"] <= TETO_SEM_PRECO_NEGOCIAVEL
