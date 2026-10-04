"""Saltos implausiveis no preco mensal da B3 e winsorizacao das safras (B3-01)."""
import numpy as np
import pandas as pd
import pytest

from core.b3_precos_saneamento import (
    VERSAO_SANEAMENTO,
    limites_winsor,
    neutralizar_saltos_mensais,
)

IDX = pd.date_range("2024-01-31", periods=8, freq="ME")


def _painel(**cols):
    return pd.DataFrame(cols, index=IDX)


def test_grupamento_sem_retroajuste_vira_retorno_zero_e_preserva_o_resto():
    # grupamento 1:10 -> +900% no mes 4; antes e depois sobem 10% ao mes
    bruto = [10.0, 11.0, 12.1, 133.1, 146.41, 161.05, 177.16, 194.87]
    limpo, rel = neutralizar_saltos_mensais(_painel(AAA3=bruto))
    r = limpo["AAA3"].pct_change()
    assert r.abs().max() < 0.11
    assert r.iloc[3] == pytest.approx(0.0, abs=1e-9)  # o salto virou retorno zero
    assert r.drop(r.index[3]).dropna().round(4).eq(0.10).all()  # o resto, intacto
    assert limpo["AAA3"].iloc[-1] == pytest.approx(bruto[-1])  # ancora no fim
    assert rel.n_saltos == 1 and rel.n_tickers == 1 and rel.versao == VERSAO_SANEAMENTO


def test_ajustado_corrompido_com_ida_e_volta():
    # MMAQ4: explode e volta (retorno > +100% seguido de queda > -60%)
    bruto = [2.0, 2.0, 2.0, 50.0, 2.0, 2.0, 2.0, 2.0]
    limpo, rel = neutralizar_saltos_mensais(_painel(MMAQ4=bruto))
    assert limpo["MMAQ4"].pct_change().abs().max() < 0.01
    assert rel.n_saltos == 2


def test_serie_normal_nao_e_tocada_e_ausencia_continua_ausencia():
    base = [10, 10.5, np.nan, 11, 10, 9.5, 10, 10.2]
    p = _painel(AAA3=base, BBB3=[np.nan] * 3 + [5, 5.1, 5, 5.2, 5.3])
    limpo, rel = neutralizar_saltos_mensais(p)
    pd.testing.assert_frame_equal(limpo, p)
    assert rel.n_saltos == 0


def test_queda_terminal_de_quem_saiu_da_bolsa_e_preservada():
    # quebrou: -80% no ultimo pregao; o painel segue ate o fim por OUTRO papel
    p = _painel(QUEB3=[10, 10, 9, 8, 1.6, np.nan, np.nan, np.nan],
                VIVO3=[10, 10, 10, 10, 10, 10, 10, 10])
    limpo, rel = neutralizar_saltos_mensais(p)
    assert limpo["QUEB3"].iloc[4] == pytest.approx(1.6)
    assert limpo["QUEB3"].iloc[0] == pytest.approx(10.0)
    assert rel.n_saltos == 0


def test_mesma_queda_em_papel_vivo_e_neutralizada():
    p = _painel(VIVO3=[10, 10, 9, 8, 1.6, 1.6, 1.7, 1.8])
    _, rel = neutralizar_saltos_mensais(p)
    assert rel.n_saltos == 1


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
