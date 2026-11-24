# -*- coding: utf-8 -*-
"""Peças puras da medição da §23 (pequenas baratas e dividendo de 6%)."""
from __future__ import annotations

from scripts import medir_estrategias_fundamentalistas_b3 as me
from scripts.medir_nota_fatores_b3 import caminho_do_cache


def test_excesso_sobre_outro_ew_pareia_por_safra():
    braco = [{"safra": 2020, "liquido": 0.10}, {"safra": 2021, "liquido": -0.05},
             {"safra": 2022, "liquido": 0.02}]
    ref = [{"safra": 2020, "equal_weight": 0.04}, {"safra": 2021, "equal_weight": 0.01}]
    out = me.excesso_sobre_outro_ew(braco, ref)
    assert [t["safra"] for t in out] == [2020, 2021]
    assert out[0]["dif"] == 0.10 - 0.04
    assert out[1]["dif"] == -0.05 - 0.01


def test_rodada_sem_piso_tem_pickle_proprio(tmp_path):
    padrao = caminho_do_cache(tmp_path, "Equilibrado")
    sem_piso = caminho_do_cache(tmp_path, me.CHAVE_CACHE_SEM_PISO)
    personalizado = caminho_do_cache(tmp_path, "Personalizado")
    assert len({padrao, sem_piso, personalizado}) == 3


def test_rodada_sem_piso_so_troca_o_valor_de_mercado():
    from core.b3_portfolio_presets import PERSONALIZADO, PRESETS, RECOMENDADO, \
        identificar_perfil
    base = dict(PRESETS[RECOMENDADO].valores)
    valores = {**base, **me.VALORES_SEM_PISO}
    trocadas = {k for k in valores if valores[k] != base.get(k)}
    assert trocadas == {"pb3_min_mcap"}
    assert valores["pb3_min_adtv"] == base["pb3_min_adtv"]
    assert identificar_perfil(valores) == PERSONALIZADO
