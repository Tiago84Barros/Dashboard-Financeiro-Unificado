"""O backtest da camada macro tem que ser incapaz de fabricar desempenho.

Estes testes não verificam o número que o script produz -- ele depende do
armazém local e muda quando o dado muda. Eles verificam as três propriedades
sem as quais o número não significa nada:

1. o peso do mês não pode ler o retorno do mês;
2. a correção de autocorrelação tem que **derrubar** o t de série sobreposta,
   nunca elevá-lo;
3. mês em que o impacto é constante entre ativos não vira observação de IC.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.backtest_macro_tilt import efeito_na_carteira, rank_ic, t_newey_west


def _painel_e_retorno(impacto_igual_ao_retorno: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    idx = pd.date_range("2020-01-31", periods=24, freq="ME", tz="UTC")
    rng = np.random.default_rng(7)
    ret = pd.DataFrame(rng.normal(0, 0.05, (24, 4)),
                       index=idx, columns=list("ABCD"))
    if impacto_igual_ao_retorno:
        # O caso adversário: o impacto do mês É o retorno do mês, em escala de
        # impacto. Se o simulador olhasse o próprio mês, isto viraria ganho
        # enorme; olhando o mês seguinte, tem que virar ruído.
        painel = ret * 1000
    else:
        painel = pd.DataFrame(rng.normal(0, 10, (24, 4)),
                              index=idx, columns=list("ABCD"))
    return painel, ret


def test_peso_do_mes_nao_le_o_retorno_do_mes():
    painel, ret = _painel_e_retorno(impacto_igual_ao_retorno=True)
    res = efeito_na_carteira(painel, ret, k=0.15)
    # Com clarividência perfeita e leitura contemporânea, o ganho seria de
    # vários por cento ao mês. O ``shift(1)`` tem que reduzir isto a ruído.
    assert abs(res["dif_mensal"]) < 0.005, (
        f"clarividencia vazou para o resultado: {res['dif_mensal']:+.4%}/mes")


def test_carteira_nao_extrapola_a_janela_do_painel():
    painel, ret = _painel_e_retorno(impacto_igual_ao_retorno=False)
    # Preço com muito mais história que o painel de impacto: os meses de fora
    # não podem entrar como observação de impacto zero.
    extra = pd.date_range("2010-01-31", "2019-12-31", freq="ME", tz="UTC")
    antigo = pd.DataFrame(0.01, index=extra, columns=ret.columns)
    res = efeito_na_carteira(painel, pd.concat([antigo, ret]), k=0.15)
    assert res["meses"] <= len(painel), (
        f"{res['meses']} meses simulados contra {len(painel)} cortes de painel")


def test_tilt_de_k_zero_nao_produz_estatistica():
    """Sem inclinação a diferença é zero em todo mês, e zero não tem t.

    O caminho degenerado devolve só a contagem de meses. Isto é deliberado:
    ``t = 0/0`` sairia como ``nan`` ou, pior, como um número, e um t de uma
    série constante não é um teste de nada.
    """
    painel, ret = _painel_e_retorno(impacto_igual_ao_retorno=False)
    res = efeito_na_carteira(painel, ret, k=0.0)
    assert "dif_mensal" not in res
    assert res["meses"] > 0


def test_newey_west_derruba_t_de_serie_autocorrelacionada():
    rng = np.random.default_rng(11)
    ruido = rng.normal(0.3, 1.0, 400)
    # Média móvel de 12: exatamente a estrutura que janela sobreposta de 12
    # meses cria. O t simples aqui é inflado por construção.
    serie = pd.Series(ruido).rolling(12).mean().dropna().to_numpy()
    t_simples = serie.mean() / (serie.std(ddof=1) / np.sqrt(len(serie)))
    t_corrigido = t_newey_west(serie, 11)
    assert abs(t_corrigido) < abs(t_simples), (
        f"correcao nao derrubou o t: simples={t_simples:+.2f} NW={t_corrigido:+.2f}")


def test_mes_sem_dispersao_nao_vira_observacao_de_ic():
    idx = pd.date_range("2020-01-31", periods=12, freq="ME", tz="UTC")
    ret = pd.DataFrame(np.random.default_rng(3).normal(0, 0.05, (12, 4)),
                       index=idx, columns=list("ABCD"))
    painel = pd.DataFrame(5.0, index=idx, columns=list("ABCD"))  # constante
    res = rank_ic(painel, ret, horizonte=1)
    assert res["meses"] == 0, "mes sem opiniao entrou como observacao"
    assert res["descartados"] == 12
