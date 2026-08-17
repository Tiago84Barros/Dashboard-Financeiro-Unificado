"""O motor consulta mesmo os analisadores (Fase 3b, Task 5, spec secao 8).

A secao 8 exige nominalmente este teste: "cada recomendacao registra qual
analisador a disparou. Se `concentration`, `correlation` e `factors` nao
aparecem como gatilho de nenhuma sugestao, eles nao estao no caminho da
decisao e sao apenas exibicao." O projeto ja pagou o preco de nao verificar
isso: um motor de analise que ninguem consulta na decisao e decoracao, e
so aparece quando alguem roda o codigo de ponta a ponta -- nao ao ler.

Por isso este arquivo NAO constroi `Sinal`s a mao (isso testaria o motor,
nao os analisadores) e NAO parte do que faria `analisadores` conter o nome
certo (isso testaria a suite, nao a carteira). A carteira sintetica abaixo e
construida a partir da situacao economica que cada analisador existe para
detectar, e os tres analisadores de verdade -- `correlation.pares_redundantes`,
`factors.exposicao_do_portfolio` / `factors.betas_do_ativo` -- rodam sobre
series de retorno sinteticas, nunca sobre numeros pre-calculados para bater
com o resultado esperado. `concentration` e verificado por
`signals.sinais_concentracao` (via `gerar_sinais`) diretamente sobre o peso
da carteira, sem retornos envolvidos.

A carteira:

- **GRANDE** (55% do patrimonio): posicao de fato concentrada -- muito acima
  do limite de 10% por ativo (`LIMITE_ATIVO_DEFAULT`) -- e, por ser a maior
  fatia do patrimonio, tambem a maior responsavel pelo tilt da carteira ao
  fator "mercado_br".
- **PILHA1** e **PILHA2**: duas posicoes menores cujos retornos mensais
  reais saem correlacionados em ~0,98 (bem acima do limiar de redundancia
  de 0,80) -- a mesma aposta sob dois tickers -- e que TAMBEM carregam
  exposicao real e estatisticamente significativa ao mesmo fator que ja
  domina a carteira: exatamente o "empilha na mesma aposta macro em vez de
  diversificar" que o sinal de fator (Task 2b) existe para flagrar.
- **FILLER1/2/3**: tres posicoes pequenas e puramente idiossincraticas (sem
  carga em nenhum fator, sem correlacao com ninguem, peso individual abaixo
  do limite). Servem de controle negativo: nao tem por que disparar nada, e
  de fato nao disparam (ficam `indeterminado`, sem nenhum sinal).

Cada uma das tres asserções vive em seu proprio teste -- exatamente para que,
se um analisador sair do caminho de decisao numa "simplificacao" futura do
motor, o teste que cai diga QUAL analisador sumiu, nao so que a suite ficou
vermelha.
"""
from datetime import date

import numpy as np
import pandas as pd

from core.global_portfolio.advisor import recomendar
from core.global_portfolio.correlation import LIMIAR_REDUNDANCIA, pares_redundantes
from core.global_portfolio.factors import betas_do_ativo, exposicao_do_portfolio
from core.global_portfolio.signals import LIMITE_ATIVO_DEFAULT, gerar_sinais
from core.rebalancing import CalendarRebalance
from core.transaction_costs import CLASSE_B3, CLASSE_FII, CLASSE_US, CostConfig

# CalendarRebalance com intervalo 0 sempre dispara: gate real de politica,
# nao um fake que force a acao (mesmo padrao de tests/test_global_advisor.py).
_SEMPRE_REBALANCEAR = CalendarRebalance(intervalo_dias=0)
_HOJE = date(2026, 8, 16)

_N_MESES = 60  # acima do piso de 24 que factors.MIN_OBS_REGRESSAO exige
_SEED = 13


def _retornos_e_fatores() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Serie mensal sintetica, mas com a estrutura economica real que cada
    analisador precisa para disparar -- nunca um numero pos-calculado para
    caber no resultado esperado.
    """
    rng = np.random.default_rng(_SEED)
    idx = pd.date_range("2019-01-31", periods=_N_MESES, freq="ME")
    mercado_br = rng.normal(0, 0.04, _N_MESES)

    # GRANDE: maior peso da carteira, e carrega parte real do beta de
    # mercado -- e o peso dominante, nao so o rotulo, que puxa o tilt da
    # carteira para "mercado_br".
    grande = 0.8 * mercado_br + rng.normal(0, 0.05, _N_MESES)
    # PILHA1/PILHA2: pouco ruido idiossincratico proprio sobre o mesmo
    # fator -- correlacao alta entre elas (redundancia) E carga real no
    # mesmo fator que ja domina a carteira (empilha no tilt).
    pilha1 = 1.3 * mercado_br + rng.normal(0, 0.005, _N_MESES)
    pilha2 = 1.1 * mercado_br + rng.normal(0, 0.005, _N_MESES)
    # Controle negativo: tres posicoes sem nenhuma carga em mercado_br e sem
    # correlacao com ninguem -- nao tem motivo economico para disparar nada.
    filler1 = rng.normal(0, 0.09, _N_MESES)
    filler2 = rng.normal(0, 0.09, _N_MESES)
    filler3 = rng.normal(0, 0.09, _N_MESES)

    retornos = pd.DataFrame({
        "GRANDE": grande, "PILHA1": pilha1, "PILHA2": pilha2,
        "FILLER1": filler1, "FILLER2": filler2, "FILLER3": filler3,
    }, index=idx)
    fatores = pd.DataFrame({"mercado_br": mercado_br}, index=idx)
    return retornos, fatores


def _df_posicoes() -> pd.DataFrame:
    return pd.DataFrame([
        {"asset_class": "b3", "symbol": "GRANDE", "sector": "industrials", "country": "BR",
         "currency": "BRL", "weight_global": 0.55, "payload": {}},
        {"asset_class": "us", "symbol": "PILHA1", "sector": "tech", "country": "US",
         "currency": "USD", "weight_global": 0.10, "payload": {}},
        {"asset_class": "us", "symbol": "PILHA2", "sector": "tech", "country": "US",
         "currency": "USD", "weight_global": 0.08, "payload": {}},
        {"asset_class": "fii", "symbol": "FILLER1", "sector": "diversos", "country": "JP",
         "currency": "BRL", "weight_global": 0.09, "payload": {}},
        {"asset_class": "fii", "symbol": "FILLER2", "sector": "saude", "country": "CA",
         "currency": "BRL", "weight_global": 0.09, "payload": {}},
        {"asset_class": "fii", "symbol": "FILLER3", "sector": "energia", "country": "MX",
         "currency": "BRL", "weight_global": 0.09, "payload": {}},
    ])


def _recomendacoes() -> list:
    """Pipeline real de ponta a ponta, sem nenhum atalho:

    retornos sinteticos -> `correlation.pares_redundantes` /
    `factors.exposicao_do_portfolio` / `factors.betas_do_ativo` (analisadores
    de verdade) -> `signals.gerar_sinais` -> `advisor.recomendar`. Nenhuma
    saida de analisador e fabricada a mao neste teste; se um deles nao
    disparar sobre a carteira sintetica, e um defeito no analisador ou no
    motor, nao um teste mal construido.
    """
    df = _df_posicoes()
    pesos = dict(zip(df["symbol"], df["weight_global"]))
    retornos, fatores = _retornos_e_fatores()

    pares = pares_redundantes(retornos)
    exposicao_portfolio = exposicao_do_portfolio(retornos, pesos, fatores)
    exposicoes_ativos = {s: betas_do_ativo(retornos[s], fatores) for s in retornos.columns}

    sinais = gerar_sinais(
        df, pares_redundantes=pares,
        exposicao_portfolio=exposicao_portfolio, exposicoes_ativos=exposicoes_ativos,
    )

    custos = {
        CLASSE_B3: CostConfig.brasil_pf_default(),
        CLASSE_US: CostConfig(classe=CLASSE_US, corretagem_fixa=0.0, spread_bps_large=5.0,
                              spread_bps_small=5.0, ir_rate=0.0, isencao_mes=1e12, calibrado=True),
        CLASSE_FII: CostConfig(classe=CLASSE_FII),
    }
    return recomendar(
        df, sinais, alvos={}, politica=_SEMPRE_REBALANCEAR, custos=custos,
        patrimonio_total=1_000_000.0, data_atual=_HOJE, sensibilidade=1.0,
    )


# ---------------------------------------------------------------------------
# Premissas da carteira sintetica, verificadas antes das asserções que
# importam de verdade -- para o teste falhar apontando para a premissa
# economica errada, e nao para um sintoma no motor.
# ---------------------------------------------------------------------------

def test_premissa_grande_esta_de_fato_acima_do_limite_de_concentracao():
    df = _df_posicoes()
    peso_grande = float(df.loc[df["symbol"] == "GRANDE", "weight_global"].iloc[0])
    assert peso_grande > LIMITE_ATIVO_DEFAULT


def test_premissa_pilha1_e_pilha2_estao_de_fato_redundantes():
    retornos, _fatores = _retornos_e_fatores()
    pares = pares_redundantes(retornos)
    redundantes = {(a, b) for a, b, corr in pares if corr >= LIMIAR_REDUNDANCIA}
    assert ("PILHA1", "PILHA2") in redundantes or ("PILHA2", "PILHA1") in redundantes


# ---------------------------------------------------------------------------
# As tres asserções que a §8 exige nominalmente -- uma por teste, para que a
# falha diga qual analisador saiu do caminho de decisao.
# ---------------------------------------------------------------------------

def test_concentracao_dispara_recomendacao_ao_ultrapassar_o_limite_de_ativo():
    """GRANDE fica com 55% do patrimonio contra o limite de 10% por ativo --
    posicao de fato concentrada, construida a partir do peso real da
    carteira, nao ajustada para o motor. Se `concentration` sumir do
    caminho de decisao, e esta asserção que cai."""
    acoes = _recomendacoes()
    assert any("concentration" in acao.analisadores for acao in acoes)


def test_correlacao_dispara_recomendacao_para_o_par_redundante():
    """PILHA1 e PILHA2 tem retornos mensais reais correlacionados em torno
    de 0,98 -- muito acima do limiar de redundancia de 0,80 -- a mesma
    aposta sob dois tickers, medida por `correlation.pares_redundantes`
    de verdade. Se `correlation` sumir do caminho de decisao, e esta
    asserção que cai."""
    acoes = _recomendacoes()
    assert any("correlation" in acao.analisadores for acao in acoes)


def test_fator_dispara_recomendacao_para_quem_empilha_no_tilt_dominante():
    """A regressao ponderada da carteira contra o fator `mercado_br` fecha
    com beta bem acima do limiar de tilt extremo (0,5), porque GRANDE (55%
    do patrimonio) e PILHA1/PILHA2 carregam, todos, exposicao real e
    estatisticamente significativa ao mesmo fator -- exatamente o "empilha
    na mesma aposta macro em vez de diversificar" que o sinal de fator
    (Task 2b) existe para flagrar. Se `factors` sumir do caminho de decisao,
    e esta asserção que cai."""
    acoes = _recomendacoes()
    assert any("factors" in acao.analisadores for acao in acoes)


# ---------------------------------------------------------------------------
# Controle negativo: as posicoes sem nenhuma evidencia nao inventam sinal.
# ---------------------------------------------------------------------------

def test_posicoes_sem_evidencia_ficam_indeterminadas_nao_manter():
    """FILLER1/2/3 nao tem carga em nenhum fator, nao correlacionam com
    ninguem e ficam abaixo do limite de concentracao -- sem evidencia
    nenhuma dos tres analisadores sob teste. `indeterminado` (nao
    "manter") confirma que o motor nao fabricou um sinal so para preencher
    a carteira."""
    acoes = _recomendacoes()
    por_symbol = {acao.symbol: acao for acao in acoes}
    for symbol in ("FILLER1", "FILLER2", "FILLER3"):
        assert por_symbol[symbol].acao == "indeterminado"
        assert por_symbol[symbol].analisadores == frozenset()
