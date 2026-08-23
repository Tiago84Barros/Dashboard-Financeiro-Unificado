"""A covariancia do painel de risco precisa ser positiva semidefinida.

`_covariancia_confiavel` monta a matriz com `DataFrame.cov(min_periods=...)`,
que e PAR A PAR: cada entrada pode repousar sobre uma amostra diferente.
Matriz assim nao e garantidamente positiva semidefinida, e uma matriz nao-PSD
nao descreve risco nenhum — `contribuicao_marginal` continua fechando na
identidade de Euler (soma dos MCR == sigma_p), que e o teste central de
`risk.py`, mesmo quando as parcelas estao erradas. Medido em 23/08/2026 sobre
precos reais do armazem, alimentando o calculo com series desalinhadas: 10%
das carteiras davam matriz nao-PSD e, no pior caso, um ativo aparecia
contribuindo -26,2% do risco (ou seja, protegendo a carteira) quando a matriz
corrigida dizia +3,5% (ou seja, adicionando risco).

Em producao isso NAO acontece, e o motivo mora em outro modulo: o passo 2 de
`retornos_mensais` mantem apenas os meses em que TODOS os sobreviventes tem
retorno (`finito.all(axis=1)`), entao o quadro publicado e de casos completos
e o `min_periods` par a par nunca chega a atuar sobre amostras distintas.

E dai a razao destes testes: a garantia e um efeito colateral de uma decisao
tomada em `returns.py` por outro motivo (nao renormalizar peso mes a mes).
Nada declarava a dependencia. Afrouxar a janela comum — algo que o proprio
docstring de la considera, ao lamentar o truncamento de series longas — traria
de volta o defeito medido acima, em silencio.
"""
import numpy as np
import pandas as pd

from core.global_portfolio.correlation import _covariancia_confiavel
from core.global_portfolio.returns import retornos_mensais


def _posicoes():
    return pd.DataFrame([
        {"asset_class": "b3", "symbol": "PETR4", "weight_global": 0.4},
        {"asset_class": "b3", "symbol": "VALE3", "weight_global": 0.3},
        {"asset_class": "fii", "symbol": "HGLG11", "weight_global": 0.3},
    ])


def _loader_desalinhado():
    """Historicos de comprimentos diferentes e com buraco interno.

    E o caso normal da carteira, nao uma anomalia: um FII recem-listado ao
    lado de uma acao com decadas de serie.
    """
    def carregar(tickers):
        idx = pd.date_range("2020-01-31", periods=60, freq="ME")
        rng = np.random.default_rng(11)
        dados = {}
        for i, ticker in enumerate(sorted(tickers)):
            serie = pd.Series(
                100 * np.cumprod(1 + rng.normal(0.01, 0.06, 60)), index=idx)
            serie.iloc[: 12 * i] = np.nan          # entrou depois
            serie.iloc[30 + i] = np.nan            # buraco interno
            dados[ticker] = serie
        return pd.DataFrame(dados, index=idx)
    return carregar


def test_quadro_publicado_nao_tem_buraco():
    """Sem esta janela comum, a covariancia vira par a par de verdade."""
    retornos, _ = retornos_mensais(_posicoes(), loader=_loader_desalinhado())

    assert not retornos.empty
    assert not retornos.isna().to_numpy().any()


def test_covariancia_do_quadro_publicado_e_positiva_semidefinida():
    retornos, _ = retornos_mensais(_posicoes(), loader=_loader_desalinhado())
    cov = _covariancia_confiavel(retornos)

    assert cov.shape[0] >= 2
    autovalores = np.linalg.eigvalsh(cov.to_numpy(dtype=float))
    assert autovalores.min() >= -1e-12, (
        "covariancia nao-PSD: as contribuicoes de risco fecham por Euler mas "
        "nao sao risco")


def test_sem_a_janela_comum_a_matriz_par_a_par_deixa_de_ser_psd():
    """O invariante nao e decorativo — este e o defeito que ele barra.

    Tres ativos com historicos de comprimentos diferentes, todos os pares com
    sobreposicao acima do piso (senao a limpeza de NaN ja descartaria o ativo,
    que e a protecao que `_covariancia_confiavel` de fato oferece). Cada
    covariancia par a par repousa sobre uma janela diferente e e defensavel
    isolada; as tres juntas descrevem um mundo que nao existe, e a matriz tem
    autovalor negativo.
    """
    idx = pd.date_range("2020-01-31", periods=48, freq="ME")
    rng = np.random.default_rng(0)
    x, y = rng.normal(0.0, 0.05, 48), rng.normal(0.0, 0.05, 48)
    a = pd.Series(x, index=idx)
    b = pd.Series(np.r_[x[:24], -y[24:]], index=idx)
    c = pd.Series(np.r_[-x[:24], -y[24:]], index=idx)
    a.iloc[36:] = np.nan      # A saiu antes do fim
    c.iloc[:12] = np.nan      # C entrou depois do comeco
    quadro = pd.DataFrame({"A": a, "B": b, "C": c})

    cov = _covariancia_confiavel(quadro, min_obs=12)
    assert cov.shape[0] == 3, "os tres pares passam no piso de sobreposicao"
    autovalores = np.linalg.eigvalsh(cov.to_numpy(dtype=float))
    assert autovalores.min() < -1e-12
