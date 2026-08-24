"""
VaR e CVaR de 95% precisam dizer sobre quantas observacoes a cauda repousa.

`metricas_de_risco` aceita series a partir de `MIN_OBS = 18` meses. Com 18
observacoes, `np.percentile(x, 5)` cai entre o pior e o segundo pior mes, a
cauda `valores <= corte` fica com UM elemento, e o CVaR 95% passa a ser, por
definicao, o proprio pior mes. Dois cartoes lado a lado -- "VaR 95%" e
"CVaR 95%" -- exibiam entao a mesma unica observacao como se fossem duas
medidas independentes, e o texto de ajuda prometia "1 a cada 20 meses" sobre
uma serie de 18 e "perda media nos MESES" sobre um mes so.

Medido em 24/08/2026, 200 carteiras sorteadas de 12 ativos por tamanho de
janela:

  n = 18 meses -> cauda de 1,0 mes  -> CVaR identico ao pior mes em 100%
  n = 29 meses -> cauda de 2,0 meses (a carteira real do usuario)
  n = 60 meses -> cauda de 3,0 meses

Remover um unico mes move o CVaR em ~20% do proprio valor em qualquer tamanho
de janela: e uma estatistica de cauda sobre poucos pontos, e o app precisa
dizer isso em vez de exibir tres casas decimais de autoridade.

A correcao nao muda o calculo -- o percentil empirico continua sendo a escolha
certa, pelo motivo que o docstring de `risk.py` ja defende. Ela expoe
`n_cauda` e faz a UI declarar.
"""
import numpy as np
import pandas as pd

from core.global_portfolio.risk import metricas_de_risco


def _serie(n, rng, k=4):
    idx = pd.date_range("2020-01-31", periods=n, freq="ME")
    dados = rng.normal(0.005, 0.06, size=(n, k))
    return pd.DataFrame(dados, index=idx, columns=[f"A{i}" for i in range(k)])


def test_no_piso_de_observacoes_a_cauda_tem_um_mes_e_isso_e_declarado():
    rng = np.random.default_rng(1)
    ret = _serie(18, rng)
    pesos = {c: 0.25 for c in ret.columns}

    r = metricas_de_risco(ret, pesos)
    assert r is not None and r.n_obs == 18
    assert r.n_cauda == 1, "no piso de 18 meses a cauda de 5% tem um ponto so"

    # E, sendo um ponto so, o CVaR nao e uma media: e o pior mes.
    serie = (ret * 0.25).sum(axis=1)
    assert r.cvar_95 == -float(serie.min())


def test_janela_maior_alarga_a_cauda():
    rng = np.random.default_rng(2)
    pesos = {f"A{i}": 0.25 for i in range(4)}
    caudas = [metricas_de_risco(_serie(n, rng), pesos).n_cauda for n in (18, 40, 80)]
    assert caudas == sorted(caudas) and caudas[-1] > caudas[0]


def test_a_ui_declara_o_tamanho_da_cauda_nos_cartoes():
    # Sem `n_cauda` chegando a tela, o usuario ve duas metricas com a mesma
    # aparencia de independencia. O que se trava aqui e a declaracao.
    fonte = open("views/portfolio_global.py", encoding="utf-8").read()
    trecho = fonte[fonte.index("def _painel_risco"):]
    trecho = trecho[:trecho.index("\ndef ", 10)]
    assert "n_cauda" in trecho
    assert "pior mês da série" in trecho, (
        "o caso de cauda unitaria precisa ser dito com todas as letras"
    )
    assert "1 a cada 20 meses" not in trecho.split("if r.n_obs < 20")[0], (
        "a promessa de frequencia nao pode ser incondicional"
    )
