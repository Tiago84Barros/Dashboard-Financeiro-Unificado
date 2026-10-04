"""Saltos implausiveis no preco mensal da B3 e winsorizacao das safras (B3-01)."""
import numpy as np
import pandas as pd
import pytest

from core.b3_precos_saneamento import (
    VERSAO_SANEAMENTO,
    e_fator_redondo,
    limites_winsor,
    neutralizar_saltos_mensais,
)

IDX = pd.date_range("2024-01-31", periods=8, freq="ME")


def _painel(**cols):
    return pd.DataFrame(cols, index=IDX)


def test_grupamento_exato_sem_retroajuste_vira_retorno_zero():
    # grupamento 1:10 -> +900% no mes 4; antes e depois sobem 10% ao mes
    bruto = [10.0, 11.0, 12.1, 133.1, 146.41, 161.05, 177.16, 194.87]
    limpo, rel = neutralizar_saltos_mensais(_painel(AAA3=bruto))
    r = limpo["AAA3"].pct_change()
    assert r.iloc[3] == pytest.approx(0.0, abs=1e-9)  # o salto virou retorno zero
    assert r.drop(r.index[3]).dropna().round(4).eq(0.10).all()  # o resto, intacto
    assert limpo["AAA3"].iloc[-1] == pytest.approx(bruto[-1])  # ancora no fim
    assert rel.n_saltos == 1 and rel.n_fator_redondo == 1
    assert rel.versao == VERSAO_SANEAMENTO


def test_queda_real_que_nao_e_fator_redondo_fica():
    # AMER3 em 01/2023: -81,87% (razao 0,1813; o fator redondo mais proximo,
    # 1/5, esta a 8% de distancia) com o close bruto caindo junto
    amer = [10.0, 10.0, 9.0, 1.63, 1.5, 1.6, 1.7, 1.8]
    p = _painel(AMER3=amer)
    limpo, rel = neutralizar_saltos_mensais(p, p.copy())
    pd.testing.assert_frame_equal(limpo, p)
    assert rel.n_saltos == 0


def test_amer3_no_painel_real_nao_e_neutralizada():
    # Caso real medido em 04/10/2026: -81,865% em 31/01/2023 e -89,509% em
    # 31/08/2024 (razoes 0,18135 e 0,10491; fatores 1/5 e 1/10 a 8,4% e 4,8%)
    idx = pd.to_datetime(["2022-12-31", "2023-01-31", "2024-07-31", "2024-08-31"])
    p = pd.DataFrame({"AMER3": [1.0, 0.18135, 0.5, 0.05245]}, index=idx)
    limpo, rel = neutralizar_saltos_mensais(p, p.copy())
    assert limpo["AMER3"].pct_change().loc["2023-01-31"] == pytest.approx(-0.81865)
    assert rel.n_saltos == 0


def test_ajustado_explode_com_close_estavel_e_neutralizado():
    # MMAQ4: ajustado corrompido, close bruto plano
    aj = [2.0, 2.0, 2.0, 55.0, 55.0, 55.0, 55.0, 55.0]
    bruto = [2.0] * 8
    limpo, rel = neutralizar_saltos_mensais(_painel(MMAQ4=aj), _painel(MMAQ4=bruto))
    assert limpo["MMAQ4"].pct_change().abs().max() < 1e-9
    assert rel.n_close_estavel == 1


def test_alta_sem_evidencia_so_acima_de_300_por_cento():
    # +237% (razao 3,37, nao redonda) fica; +420% (5,2) sai
    p = _painel(A3=[1, 1, 3.37, 3.4, 3.4, 3.5, 3.5, 3.5],
                B3=[1, 1, 5.2, 5.3, 5.3, 5.4, 5.4, 5.4])
    limpo, rel = neutralizar_saltos_mensais(p)
    assert limpo["A3"].iloc[2] / limpo["A3"].iloc[1] == pytest.approx(3.37)
    assert abs(limpo["B3"].iloc[2] / limpo["B3"].iloc[1] - 1) < 1e-9
    assert rel.n_alta_sem_evidencia == 1


def test_e_fator_redondo():
    assert e_fator_redondo(0.1) and e_fator_redondo(10.0) and e_fator_redondo(1.5)
    assert e_fator_redondo(1 / 3) and e_fator_redondo(0.9999 * 2)
    assert not e_fator_redondo(0.18135) and not e_fator_redondo(0.105)
    assert not e_fator_redondo(float("nan")) and not e_fator_redondo(-1.0)


def test_serie_normal_nao_e_tocada_e_ausencia_continua_ausencia():
    base = [10, 10.5, np.nan, 11, 10, 9.5, 10, 10.2]
    p = _painel(AAA3=base, BBB3=[np.nan] * 3 + [5, 5.1, 5, 5.2, 5.3])
    limpo, rel = neutralizar_saltos_mensais(p)
    pd.testing.assert_frame_equal(limpo, p)
    assert rel.n_saltos == 0


def test_painel_vazio_e_devolvido_como_veio():
    limpo, rel = neutralizar_saltos_mensais(pd.DataFrame())
    assert limpo.empty and rel.n_saltos == 0


def test_limites_winsor_exige_minimo_de_papeis():
    assert limites_winsor([0.1] * 10, pct_baixo=1, pct_alto=99, minimo=30) is None
    lo, hi = limites_winsor(list(range(100)), pct_baixo=1, pct_alto=99, minimo=30)
    assert lo == pytest.approx(0.99) and hi == pytest.approx(98.01)
    assert limites_winsor([np.nan, None] * 20, pct_baixo=1, pct_alto=99,
                          minimo=30) is None


def test_safra_winsoriza_universo_e_estrategia_com_os_mesmos_limites():
    from core.b3_safras import SafraCarteira, retorno_da_safra

    datas = ["2024-04-30", "2025-03-31"]
    tickers = [f"T{i:03d}3" for i in range(300)]
    cols = {t: [10.0, 11.0] for t in tickers}      # +10% cada
    cols["T0003"] = [1.0, 500.0]                    # +49.900% (lixo)
    df = pd.DataFrame(cols, index=pd.DatetimeIndex(datas))
    cart = SafraCarteira(safra=2024, ano_base=2023,
                         inicio=pd.Timestamp("2024-04-01"),
                         fim=pd.Timestamp("2025-03-31"), completa=True,
                         pesos={"T0003": 1.0}, universo=tuple(tickers),
                         segmentos=1, saidas={})
    m = retorno_da_safra(cart, df, selic_por_ano={2024: 0.1, 2025: 0.1},
                         taxa_selic_aa=0.1)
    # sem corte o EW seria ~ (299*0,10 + 499)/300 = 1,76; com 1/99 o lixo
    # vira o percentil 99 (~+10% mais a cauda interpolada)
    assert m["retorno_equal_weight_bruto"] > 1.5
    assert m["retorno_equal_weight"] < 0.5
    assert m["retorno_estrategia"] < 6.0   # a posicao no lixo tambem e cortada
    assert m["n_winsorizados_universo"] >= 1
    assert m["excesso_equal_weight"] == pytest.approx(
        m["retorno_estrategia"] - m["retorno_equal_weight"])
