"""Extracao de campo canonico do payload, seja qual for a classe."""
import pytest

from core.global_portfolio.fields import CAMPOS, disponivel, valor

B3 = {"fundamentals": {"P/L": 8.5, "P/VP": 1.2, "DY": 6.4, "ROE": 15.0,
                       "Valor de mercado": 1.2e11}}
US = {"fundamentals": {"pe_ratio": 28.4, "price_to_book": 45.0,
                       "dividend_yield": 0.5, "return_on_equity": 120.0,
                       "market_cap": 3.4e12}}
FII = {"fundamentals": {"pvp": 0.95, "dy_12m": 8.4, "patrimonio_liquido": 3.2e9}}


def test_campos_sao_deterministicos():
    assert CAMPOS == tuple(sorted(CAMPOS))


@pytest.mark.parametrize("payload,classe,campo,esperado", [
    (B3, "b3", "pe", 8.5),
    (B3, "b3", "dy", 6.4),
    (B3, "b3", "market_cap", 1.2e11),
    (US, "us", "pe", 28.4),
    (US, "us", "dy", 0.5),
    (US, "us", "roe", 120.0),
    (FII, "fii", "pvp", 0.95),
    (FII, "fii", "dy", 8.4),
    (FII, "fii", "market_cap", 3.2e9),
])
def test_extrai_o_campo_certo_por_classe(payload, classe, campo, esperado):
    assert valor(payload, classe, campo) == esperado


def test_campo_nao_aplicavel_a_classe_devolve_none():
    # FII nao tem P/L: nao existe lucro contabil comparavel.
    assert valor(FII, "fii", "pe") is None


def test_campo_ausente_devolve_none_e_nao_zero():
    assert valor({"fundamentals": {}}, "b3", "pe") is None


def test_payload_sem_bloco_fundamentals_devolve_none():
    assert valor({}, "b3", "pe") is None


def test_valor_nao_numerico_devolve_none():
    assert valor({"fundamentals": {"P/L": "n/d"}}, "b3", "pe") is None


def test_campo_desconhecido_levanta_erro_claro():
    with pytest.raises(KeyError, match="ebitda"):
        valor(B3, "b3", "ebitda")


def test_disponivel_reflete_a_presenca_do_valor():
    assert disponivel(B3, "b3", "pe") is True
    assert disponivel(FII, "fii", "pe") is False
