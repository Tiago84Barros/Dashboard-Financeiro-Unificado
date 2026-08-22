"""Extracao de campo canonico do payload, seja qual for a classe."""
import inspect

import pytest

from core.global_portfolio.fields import CAMPOS, disponivel, valor

# Fixtures com chaves reais de cada classe (verificadas contra as fontes).
B3 = {"fundamentals": {"P/L": 8.5, "P/VP": 1.2, "DY": 6.4, "ROE": 15.0}}
US = {"fundamentals": {"pe": 28.4, "roe": 120.0, "_market_cap": 3.4e12}}
FII = {"fundamentals": {"pvp": 0.95, "dy_12m": 8.4, "patrimonio_liquido": 3.2e9}}


def test_campos_sao_deterministicos():
    assert CAMPOS == tuple(sorted(CAMPOS))


@pytest.mark.parametrize("payload,classe,campo,esperado", [
    (B3, "b3", "pe", 8.5),
    (B3, "b3", "dy", 6.4),
    (US, "us", "pe", 28.4),
    (US, "us", "roe", 120.0),
    (US, "us", "market_cap", 3.4e12),
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


# Testes de seam: verificam que a de-para permanece fiel aos produtores reais.
# Estes testes quebram se um produtor renomeia uma chave, alertando que a
# de-para ficou desatualizada.


def test_seam_b3_chaves_existem_em_mult_cols():
    """B3: todas as chaves da de-para estao em _MULT_COLS."""
    from core.global_portfolio.fields import _ORIGEM
    from core.market_read import _MULT_COLS

    b3_keys = _ORIGEM["pe"].get("b3"), _ORIGEM["pvp"].get("b3"), \
              _ORIGEM["dy"].get("b3"), _ORIGEM["roe"].get("b3")
    for key in b3_keys:
        if key is not None:
            assert key in _MULT_COLS, \
                f"chave B3 {key!r} nao esta em _MULT_COLS"


def test_seam_fii_chaves_existem_em_fundamentos():
    """FII: todas as chaves da de-para estao em _FUNDAMENTOS.values()."""
    from core.global_portfolio.fields import _ORIGEM
    from core.portfolio.adapters.fii import _FUNDAMENTOS

    fii_fundamentos = set(_FUNDAMENTOS.values())
    fii_keys = _ORIGEM["pvp"].get("fii"), _ORIGEM["dy"].get("fii"), \
               _ORIGEM["market_cap"].get("fii")
    for key in fii_keys:
        if key is not None:
            assert key in fii_fundamentos, \
                f"chave FII {key!r} nao esta em _FUNDAMENTOS.values()"


def test_seam_us_chaves_existem_em_compute_company_metrics():
    """US: todas as chaves da de-para estao em compute_company_metrics."""
    from core import us_metrics
    from core.global_portfolio.fields import _ORIGEM

    # Fonte: nao ha lista exportada de metricas, entao verificar contra
    # o source code da funcao e elementos conhecidos.
    source = inspect.getsource(us_metrics.compute_company_metrics)

    us_keys = _ORIGEM["pe"].get("us"), _ORIGEM["roe"].get("us"), \
              _ORIGEM["market_cap"].get("us")

    for key in us_keys:
        if key is not None:
            # Buscar "pe":, "roe":, "_market_cap": na source
            assert f'"{key}"' in source, \
                f"chave US {key!r} nao aparece em compute_company_metrics"
