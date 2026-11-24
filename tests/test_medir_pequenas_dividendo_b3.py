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
    from core.b3_portfolio_presets import (
        PERSONALIZADO,
        PRESETS,
        RECOMENDADO,
        identificar_perfil,
    )
    base = dict(PRESETS[RECOMENDADO].valores)
    valores = {**base, **me.VALORES_SEM_PISO}
    trocadas = {k for k in valores if valores[k] != base.get(k)}
    assert trocadas == {"pb3_min_mcap"}
    assert valores["pb3_min_adtv"] == base["pb3_min_adtv"]
    assert identificar_perfil(valores) == PERSONALIZADO


def _df_com_fonte(fonte=None, erro=None):
    import pandas as pd

    df = pd.DataFrame({"AAAA3": [1.0, 2.0]})
    if fonte is not None:
        df.attrs["fonte_precos"] = fonte
    if erro is not None:
        df.attrs["fonte_precos_erro"] = erro
    return df


def test_procedencia_do_armazem_e_registrada():
    out = me.procedencia_dos_precos(_df_com_fonte(me.FONTE_DO_ARMAZEM))
    assert out == {"fonte_precos": me.FONTE_DO_ARMAZEM, "fonte_precos_erro": None}


def test_procedencia_recusa_o_fallback_do_yfinance():
    import pytest

    with pytest.raises(RuntimeError, match="yfinance.*OperationalError"):
        me.procedencia_dos_precos(_df_com_fonte("yfinance", "OperationalError"))


def test_procedencia_recusa_fonte_nao_rotulada():
    import pytest

    with pytest.raises(RuntimeError, match="não rotulada"):
        me.procedencia_dos_precos(_df_com_fonte())


def test_meio_a_meio_e_a_media_so_nas_safras_em_comum():
    # §25: metade em cada carteira; safra que só uma mediu fica fora.
    a = [{"safra": 2020, "excesso_ew": 0.10}, {"safra": 2021, "excesso_ew": -0.04},
         {"safra": 2022, "excesso_ew": None}]
    b = [{"safra": 2020, "excesso_ew": 0.02}, {"safra": 2021, "excesso_ew": 0.06},
         {"safra": 2022, "excesso_ew": 0.03}, {"safra": 2023, "excesso_ew": 0.01}]
    out = me.meio_a_meio(a, b, "excesso_ew")
    assert [t["safra"] for t in out] == [2020, 2021]
    assert abs(out[0]["dif"] - 0.06) < 1e-12
    assert abs(out[1]["dif"] - 0.01) < 1e-12


def _conferencia(**muda):
    base = {"fonte_precos": me.FONTE_DO_ARMAZEM, "fonte_precos_erro": None,
            "pontos_comparados": 100, "pontos_que_a_aba_tem_e_o_longo_nao": 0,
            "maior_diferenca_relativa": 0.0}
    return {**base, **muda}


def test_precos_conferidos_passam_e_vao_ao_json():
    conf = _conferencia()
    assert me.exigir_precos_conferidos(conf) is conf


def test_precos_que_divergem_do_armazem_param_a_medicao():
    # Achado do Codex no #804: o pickle perde attrs; só a releitura prova a fonte.
    import pytest

    for muda, motivo in (({"maior_diferenca_relativa": 0.02}, "diferença"),
                         ({"maior_diferenca_relativa": None}, "diferença"),
                         ({"pontos_comparados": 0}, "nenhum ponto"),
                         ({"pontos_que_a_aba_tem_e_o_longo_nao": 3}, "sem par"),
                         ({"fonte_precos": "yfinance"}, "releitura de yfinance")):
        with pytest.raises(RuntimeError, match=motivo):
            me.exigir_precos_conferidos(_conferencia(**muda))


def test_fonte_dos_precos_da_aba_usa_a_releitura(monkeypatch):
    import pytest

    df = _df_com_fonte()
    monkeypatch.setattr(me, "precos_sem_corte", lambda _df: (_df, _conferencia()))
    out = me.fonte_dos_precos_da_aba(df)
    assert out["conferencia_dos_precos"]["pontos_comparados"] == 100
    assert me.FONTE_DO_ARMAZEM in out["precos"]

    monkeypatch.setattr(me, "precos_sem_corte",
                        lambda _df: (_df, _conferencia(maior_diferenca_relativa=0.5)))
    with pytest.raises(RuntimeError, match="não conferem"):
        me.fonte_dos_precos_da_aba(df)
