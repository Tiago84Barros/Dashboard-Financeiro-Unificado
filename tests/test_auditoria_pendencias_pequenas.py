"""Quatro pendências da auditoria de 24/09/2026 que mostravam número ou opção
errados sem aviso: rótulo do mês no Dashboard, política de rebalanceamento do
Portfólio Global, "Inverso da volatilidade" nos EUA e URL livre na importação
de Postgres."""
from __future__ import annotations

import inspect
from datetime import date

from core.rebalancing import ThresholdRebalance

# ── 1. Dashboard: rótulo do período segue a origem dos KPIs ──────────────────

def test_rotulo_do_periodo_com_mes_corrente_e_o_de_hoje():
    from views.dashboard_geral import _rotulo_do_periodo
    assert _rotulo_do_periodo(True, {"mes_referencia": "Ago 2026"}, date(2026, 9, 1)) == "Set 2026"


def test_rotulo_do_periodo_sem_mes_corrente_e_o_do_ultimo_mes_com_dado():
    from views.dashboard_geral import _rotulo_do_periodo
    assert _rotulo_do_periodo(False, {"mes_referencia": "Ago 2026"}, date(2026, 9, 1)) == "Ago 2026"


def test_rotulo_do_periodo_sem_referencia_cai_no_de_hoje():
    from views.dashboard_geral import _rotulo_do_periodo
    assert _rotulo_do_periodo(False, {}, date(2026, 9, 1)) == "Set 2026"


# ── 2. Portfólio Global: política não depende de data que a tela não tem ─────

def test_portfolio_global_nao_cai_em_primeira_execucao():
    from views import portfolio_global
    pol = portfolio_global._POLITICA_REBALANCEAMENTO
    assert isinstance(pol, ThresholdRebalance)
    # A tela nunca informa `ultimo_rebal`; com a carteira dentro da banda, não
    # pode haver movimento.
    deve, motivo = pol.deve_rebalancear({"A": 0.52, "B": 0.48}, {"A": 0.5, "B": 0.5},
                                        date(2026, 9, 24), None)
    assert (deve, motivo) == (False, "")
    deve, motivo = pol.deve_rebalancear({"A": 0.70, "B": 0.30}, {"A": 0.5, "B": 0.5},
                                        date(2026, 9, 24), None)
    assert deve and "Threshold" in motivo


def test_portfolio_global_declara_a_banda_nos_limiares():
    from views import portfolio_global
    assert "5 p.p." in portfolio_global._texto_de_limiares_motor()
    fonte = inspect.getsource(portfolio_global._gerar_recomendacoes)
    assert "CalendarRebalance" not in fonte


# ── 3. EUA: nenhuma opção de ponderação que o motor não calcula ──────────────

def test_eua_nao_oferece_inverso_da_volatilidade():
    from views import empresas_americanas
    assert "inverse_vol" not in empresas_americanas._WEIGHTING_LABELS
    assert "inverse_vol" not in inspect.getsource(empresas_americanas)
