# -*- coding: utf-8 -*-
"""A-161: zero censura em painel longo é assinatura, não limpeza.

O comentário anterior em `walk_forward` afirmava que zero censura significa
"nenhuma ação sumiu no meio". Medido no painel reconstruído (22.290
observações, 16 anos): zero. Nenhuma some porque o universo é montado a partir
de quem sobreviveu. A tela calava, e silêncio é lido como ausência de problema.
"""
import pandas as pd

import core.us_backtest as bt


def _painel(anos, censurado=False):
    linhas = []
    for i, ano in enumerate(anos):
        for j, sym in enumerate(("AAA", "BBB", "CCC", "DDD")):
            linhas.append({"date": pd.Timestamp(f"{ano}-06-30"), "symbol": sym,
                           "score": 50 + j * 5, "fwd_return": 0.05 * (j - 1),
                           "censored": bool(censurado and i == 0 and j == 0)})
    return pd.DataFrame(linhas)


def test_painel_longo_sem_nenhuma_saida_e_sinalizado():
    res = bt.walk_forward(_painel(range(2010, 2026)), top_n=2, periods_per_year=1)
    assert res["ok"]
    assert res["censura"]["sem_saida"] is True
    assert res["censura"]["anos"] == 16


def test_painel_com_uma_saida_nao_e_sinalizado():
    """Uma saída já basta: o alerta é sobre universo sem saída nenhuma."""
    res = bt.walk_forward(_painel(range(2010, 2026), censurado=True),
                          top_n=2, periods_per_year=1)
    assert res["censura"]["n_censurado"] == 1
    assert res["censura"]["sem_saida"] is False


def test_painel_curto_sem_saida_nao_acusa_vies():
    """Dois anos sem deslistagem é plausível; acusar viés aí seria ruído."""
    res = bt.walk_forward(_painel(range(2024, 2026)), top_n=2, periods_per_year=1)
    assert res["censura"]["anos"] == 2
    assert res["censura"]["sem_saida"] is False
