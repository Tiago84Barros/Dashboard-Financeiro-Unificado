"""A-126: os quatro modulos que implementam o parecer da banca (2026-05-23).

`core/correlations.py` (M2), `core/copulas.py` (M2c),
`core/survivorship_ingestion.py` (C3c) e `core/survivorship_prices.py` (C3cc+)
foram escritos, nunca ligados a nenhuma tela e nunca testados. Estes testes
travam o comportamento medido, para que a decisao de ligar (ou nao) cada um
seja de metodologia e nao refem de um modulo que ninguem sabe se roda.
"""
from __future__ import annotations

import numpy as np


def _retornos(n: int = 140, k: int = 4, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    fator = rng.normal(0.0, 0.05, size=(n, 1))
    return fator + rng.normal(0.0, 0.05, size=(n, k))


# --- core/correlations.py -----------------------------------------------


def test_ewma_correlation_exige_ndarray_e_devolve_matriz_valida():
    from core.correlations import ewma_correlation_matrix

    ret = _retornos()
    m = ewma_correlation_matrix(ret, halflife=12)
    assert m.shape == (ret.shape[1], ret.shape[1])
    assert np.allclose(np.diag(m), 1.0)
    assert np.allclose(m, m.T)
    assert np.nanmax(np.abs(m)) <= 1.0 + 1e-9


def test_ewma_volatility_respeita_periods_per_year():
    """Passar retorno mensal com o default diario (252) infla a vol ~4,6x."""
    from core.correlations import ewma_volatility

    ret = _retornos()
    mensal = ewma_volatility(ret, halflife=12, periods_per_year=12)
    diaria = ewma_volatility(ret, halflife=12, periods_per_year=252)
    assert np.all(mensal > 0)
    assert np.allclose(diaria / mensal, np.sqrt(252 / 12))


def test_ewma_difere_de_pearson_estatico_em_dado_com_regime():
    """Se EWMA igualasse Pearson, liga-lo nao mudaria decisao nenhuma."""
    from core.correlations import ewma_correlation_matrix

    calmo = _retornos(n=80, k=3, seed=1) * 0.3
    crise = _retornos(n=40, k=3, seed=2)
    ret = np.vstack([calmo, crise])
    ewma = ewma_correlation_matrix(ret, halflife=6)
    pearson = np.corrcoef(ret, rowvar=False)
    iu = np.triu_indices(3, 1)
    assert np.abs(ewma[iu] - pearson[iu]).max() > 0.01


# --- core/copulas.py ----------------------------------------------------


def test_tail_dependence_fica_no_intervalo_unitario():
    from core.copulas import tail_dependence_matrix

    m = tail_dependence_matrix(_retornos(), q=0.10)
    assert np.all(m >= 0.0) and np.all(m <= 1.0)
    assert np.allclose(np.diag(m), 1.0)


def test_compare_pearson_vs_copula_entrega_as_chaves_de_decisao():
    from core.copulas import compare_pearson_vs_copula

    out = compare_pearson_vs_copula(_retornos(), q=0.10)
    for chave in ("pearson_matrix", "copula_matrix", "tail_dep_lower",
                  "avg_pearson", "avg_tail_dep", "crisis_index"):
        assert chave in out, chave
    assert out["crisis_index"] >= 0.0


# --- core/survivorship_ingestion.py -------------------------------------


def test_universo_delisted_offline_nao_fica_vazio():
    """Sem rede, o pool curado precisa sustentar o backtest sozinho."""
    from core.survivorship_ingestion import universo_delisted_total

    pool = universo_delisted_total()
    assert len(pool) >= 20
    assert len({t.ticker for t in pool}) == len(pool)


def test_resumo_ingestao_separa_curados_de_fontes_externas():
    from core.survivorship_ingestion import resumo_ingestao

    r = resumo_ingestao()
    assert r["curados"] >= 20
    assert r["total_unicos"] >= r["curados"]


# --- core/survivorship_prices.py ----------------------------------------


def test_reconstrucao_leva_falencia_a_zero_e_nao_a_ultimo_preco():
    """O defeito que A-116/A-118/A-119 corrigiram na mao mora aqui resolvido."""
    from core.survivorship_ingestion import universo_delisted_total
    from core.survivorship_prices import summary_reconstruction

    resumo = summary_reconstruction(universo_delisted_total())
    falencias = resumo[resumo["motivo"] == "falencia"]
    assert not falencias.empty
    assert (falencias["residual"] <= 0.01).all()
    assert (falencias["variacao_pct"] <= -99.0).all()


def test_reconstrucao_nunca_produz_variacao_abaixo_de_menos_cem():
    from core.survivorship_ingestion import universo_delisted_total
    from core.survivorship_prices import summary_reconstruction

    resumo = summary_reconstruction(universo_delisted_total())
    assert (resumo["variacao_pct"] >= -100.0).all()
    assert (resumo["residual"] >= 0.0).all()


# --- A-126: o gate que ignorava tudo isso -------------------------------


def test_manifesto_mede_o_universo_de_deslistadas_em_vez_de_declarar():
    from core.b3_validation import _survivorship_status

    st = _survivorship_status()
    assert st["delisted_total"] >= 20
    # A-137: `cvm_canceladas` virou `cvm_mapeadas` (as que viraram ticker) e
    # `cvm_canceladas_registro` (o cadastro bruto, que nao e universo de bolsa).
    assert set(st["delisted_por_fonte"]) == {
        "curados", "locais", "b3_cache", "cvm_mapeadas",
        "cvm_canceladas_registro",
    }
    # 27/08/2026: o motivo deixou de citar `delisted_total` (147 tickers unicos).
    # Aquele numero comparava tickers com companhias e fazia a cobertura parecer
    # quase completa; estratificado, sao 59 de 133 companhias relevantes. O
    # payload continua carregando o total, mas o texto mostra a medicao que
    # decide o gate.
    cob = st["cobertura_relevante"]
    assert st["delisted_total"] >= cob["cobertas"]
    if cob["share"] is not None:
        assert f"{cob['cobertas']} de {cob['relevantes']}" in st["reason"]


def test_survivorship_permanece_nao_estrito_com_apenas_o_pool_curado():
    """Medir nao pode afrouxar o gate: 22 curados nao sao universo completo."""
    from core.b3_validation import _survivorship_status, validation_readiness

    st = _survivorship_status()
    assert st["strict_available"] is False
    pronto = validation_readiness(
        {"pit": {"strict_available": True}, "survivorship": st}
    )
    assert pronto["ready"] is False
    assert any("deslistadas" in b for b in pronto["blockers"])


def test_status_nao_quebra_o_manifesto_quando_a_ingestao_falha(monkeypatch):
    import core.survivorship_ingestion as si
    from core.b3_validation import _survivorship_status

    def _boom(*a, **k):
        raise RuntimeError("disco fora")

    monkeypatch.setattr(si, "resumo_ingestao", _boom)
    st = _survivorship_status()
    assert st["strict_available"] is False
    assert "nao pode ser medido" in st["reason"]


def test_o_bloqueio_nomeia_o_portao_mesmo_sem_cache(monkeypatch):
    """Sem cache da CVM o motivo nao dizia de que universo estava falando.

    `validation_readiness` devolve bloqueadores como prosa -- e quem le so tem
    a prosa para saber qual portao falou. O ramo "nao medido" era o unico que
    nao se identificava, entao no CI, sem cache, o bloqueio do survivorship
    virava uma frase sobre "universo historico" indistinguivel de qualquer
    outra. Quem depende de reconhecer o portao ficava cego exatamente quando a
    medicao falhava.
    """
    import core.survivorship_ingestion as si
    from core.b3_validation import _survivorship_status, validation_readiness

    monkeypatch.setattr(si, "cobertura_relevante",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sem cache")))
    st = _survivorship_status()
    assert st["cobertura_relevante"]["share"] is None
    assert "deslistadas" in st["reason"]
    pronto = validation_readiness({"pit": {"strict_available": True},
                                   "survivorship": st})
    assert any("deslistadas" in b for b in pronto["blockers"])
