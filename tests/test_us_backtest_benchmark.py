"""Benchmark de mercado no backtest dos EUA (A-003).

O defeito reproduzido aqui: o seletor "Benchmark de mercado" era gravado e
exibido, mas o motor comparava a estratégia só com o equal-weight do universo —
trocar SPY por QQQ não mudava nenhum número. Cada teste abaixo falha na versão
anterior do motor.

Todas as séries são SINTÉTICAS e injetadas por loader: nenhum teste toca banco,
warehouse ou rede.
"""
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import core.us_backtest as bt
import core.us_benchmark as bm


# ── Painel e séries sintéticas ────────────────────────────────────────────────
def _panel(periods=8, names=6, seed=0):
    """Painel ANUAL: data-base 30/jun de cada ano, fwd_return de 12 meses."""
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(periods):
        for i in range(names):
            rows.append({"date": f"{2010 + d}-06-30", "symbol": f"S{i}",
                         "score": names - i,
                         "fwd_return": 0.01 * (names - i) + rng.normal(0, 0.01)})
    return pd.DataFrame(rows)


def _serie(retorno_anual: float, *, anos=12, inicio="2009-06-30",
           preco0=100.0) -> pd.Series:
    """Série mensal de preço total-return com retorno anual constante."""
    idx = pd.date_range(inicio, periods=anos * 12 + 1, freq="ME")
    mensal = (1.0 + retorno_anual) ** (1 / 12) - 1.0
    return pd.Series(preco0 * (1.0 + mensal) ** np.arange(len(idx)), index=idx)


# SPY sobe 5% a.a. e QQQ sobe 25% a.a.: desempenhos DIVERGENTES de propósito,
# para que qualquer métrica que ignore a escolha do benchmark fique igual nos
# dois casos e o teste denuncie.
_SPY = _serie(0.05)
_QQQ = _serie(0.25)


def _loader(simbolos):
    """Formato largo de core.us_read.load_precos_mensais_us, sem banco."""
    tabela = {"SPY": _SPY, "QQQ": _QQQ, "IWB": _serie(0.06)}
    cols = {s: tabela[s] for s in simbolos if s in tabela}
    return pd.DataFrame(cols) if cols else pd.DataFrame()


# ── Mapa rótulo → símbolo ─────────────────────────────────────────────────────
def test_mapa_cobre_os_rotulos_do_seletor_da_ui():
    # Os rótulos são contrato com views/empresas_americanas.py; mudar um sem
    # mudar o outro é como o benchmark deixou de participar do backtest.
    assert bm.BENCHMARK_LABELS == (
        "S&P 500 (SPY)", "Russell 1000 (IWB)", "Nasdaq-100 (QQQ)",
        "Pesos iguais do universo")
    assert bm.resolver("S&P 500 (SPY)").simbolo == "SPY"
    assert bm.resolver("Nasdaq-100 (QQQ)").simbolo == "QQQ"
    assert bm.resolver("Russell 1000 (IWB)").simbolo == "IWB"
    assert bm.US_BENCHMARK_MAP_VERSION


def test_resolver_aceita_simbolo_cru_e_caixa_diferente():
    assert bm.resolver("spy").simbolo == "SPY"
    assert bm.resolver("  Nasdaq-100 (QQQ) ").simbolo == "QQQ"


def test_resolver_distingue_sem_indice_de_desconhecido():
    sem_indice = bm.resolver("Pesos iguais do universo")
    assert sem_indice is not None and sem_indice.simbolo is None
    assert sem_indice.usa_indice is False
    assert bm.resolver("Ibovespa") is None          # fora do mapa → falha fechada


# ── Trocar o benchmark muda o excesso ─────────────────────────────────────────
def test_trocar_benchmark_muda_as_metricas_de_excesso():
    painel = _panel()
    spy = bt.walk_forward(painel, top_n=2, weighting="equal", periods_per_year=1,
                          benchmark="S&P 500 (SPY)", benchmark_loader=_loader,
                          benchmark_horizon_months=12, bootstrap_samples=200)
    qqq = bt.walk_forward(painel, top_n=2, weighting="equal", periods_per_year=1,
                          benchmark="Nasdaq-100 (QQQ)", benchmark_loader=_loader,
                          benchmark_horizon_months=12, bootstrap_samples=200)

    assert spy["benchmark"]["ok"] and qqq["benchmark"]["ok"]
    assert spy["benchmark"]["simbolo"] == "SPY"
    assert qqq["benchmark"]["simbolo"] == "QQQ"
    # 5% a.a. contra 25% a.a.: o excesso TEM de ser menor contra o QQQ.
    assert spy["excess_ann_vs_benchmark"] > qqq["excess_ann_vs_benchmark"]
    assert spy["benchmark_stats"]["ann_return"] == pytest.approx(0.05, abs=1e-6)
    assert qqq["benchmark_stats"]["ann_return"] == pytest.approx(0.25, abs=1e-6)
    assert spy["bootstrap_excess_vs_benchmark"] != qqq["bootstrap_excess_vs_benchmark"]
    assert len(spy["benchmark_equity_curve"]) == len(spy["benchmark_dates"]) == 8


def test_excesso_contra_benchmark_bate_com_a_diferenca_dos_anualizados():
    res = bt.walk_forward(_panel(), top_n=2, weighting="equal", periods_per_year=1,
                          benchmark="SPY", benchmark_loader=_loader,
                          benchmark_horizon_months=12, bootstrap_samples=200)
    esperado = (res["portfolio_on_benchmark_window"]["ann_return"]
                - res["benchmark_stats"]["ann_return"])
    assert res["excess_ann_vs_benchmark"] == pytest.approx(esperado)


def test_horizonte_do_benchmark_segue_o_do_painel():
    """12 meses (painel anual) e 1 mês (painel mensal) não podem dar o mesmo."""
    painel = _panel()
    anual = bt.walk_forward(painel, top_n=2, weighting="equal", periods_per_year=1,
                            benchmark="QQQ", benchmark_loader=_loader,
                            benchmark_horizon_months=12, bootstrap_samples=200)
    mensal = bt.walk_forward(painel, top_n=2, weighting="equal", periods_per_year=1,
                             benchmark="QQQ", benchmark_loader=_loader,
                             benchmark_horizon_months=1, bootstrap_samples=200)
    assert anual["benchmark_stats"]["total_return"] > mensal["benchmark_stats"]["total_return"]
    # Sem horizonte explícito, deriva de periods_per_year (anual → 12 meses).
    derivado = bt.walk_forward(painel, top_n=2, weighting="equal", periods_per_year=1,
                               benchmark="QQQ", benchmark_loader=_loader,
                               bootstrap_samples=200)
    assert derivado["benchmark"]["horizonte_meses"] == 12
    assert bm.horizonte_padrao(12) == 1 and bm.horizonte_padrao(1) == 12


def test_retorno_do_benchmark_nao_usa_preco_futuro_na_ponta_inicial():
    """p0 é o último preço ≤ data de decisão; p1, o primeiro ≥ data + horizonte."""
    serie = pd.Series([100.0, 110.0, 121.0],
                      index=pd.to_datetime(["2020-06-30", "2021-06-30", "2022-06-30"]))
    r = bm.retornos_realizados(serie, ["2020-06-30", "2021-06-30"], horizonte_meses=12)
    assert r.loc["2020-06-30"] == pytest.approx(0.10)
    assert r.loc["2021-06-30"] == pytest.approx(0.10)
    # A última data não tem ponta futura: some do pareamento, não vira 0%.
    assert "2022-06-30" not in r.index


def test_retorno_12m_exige_endpoint_no_mes_alvo_sem_usar_mes_13():
    """Uma lacuna em junho não pode virar retorno de 13 meses anualizado como 12."""
    serie = pd.Series([100.0, 110.0], index=pd.to_datetime([
        "2020-06-30", "2021-07-31",  # o segundo ponto está no 13º mês
    ]))
    retornos = bm.retornos_realizados(serie, ["2020-06-30"], horizonte_meses=12)
    assert retornos.empty


def test_retorno_12m_normaliza_datas_para_o_fechamento_mensal():
    serie = pd.Series([100.0, 110.0], index=pd.to_datetime([
        "2020-06-30", "2021-06-30",
    ]))
    data_base = "2020-06-30 12:00:00"
    retornos = bm.retornos_realizados(serie, [data_base], horizonte_meses=12)
    assert retornos.loc[data_base] == pytest.approx(0.10)


# ── Equal-weight permanece intacto ────────────────────────────────────────────
def test_equal_weight_nao_muda_quando_ha_benchmark():
    painel = _panel()
    sem = bt.walk_forward(painel, top_n=2, weighting="equal", periods_per_year=1,
                          bootstrap_samples=200)
    com = bt.walk_forward(painel, top_n=2, weighting="equal", periods_per_year=1,
                          benchmark="Nasdaq-100 (QQQ)", benchmark_loader=_loader,
                          benchmark_horizon_months=12, bootstrap_samples=200)
    assert com["equal_weight"] == sem["equal_weight"]
    assert com["excess_ann_vs_ew"] == sem["excess_ann_vs_ew"]
    assert com["bootstrap_excess"] == sem["bootstrap_excess"]
    assert com["portfolio"] == sem["portfolio"]
    # As duas baselines convivem e são reportadas separadamente.
    assert com["excess_ann_vs_ew"] != com["excess_ann_vs_benchmark"]


def test_sem_benchmark_o_resultado_nao_ganha_chave_nenhuma():
    res = bt.walk_forward(_panel(), top_n=2, weighting="equal", periods_per_year=1,
                          bootstrap_samples=200)
    assert res["ok"] and "benchmark" not in res
    for chave in ("excess_ann_vs_benchmark", "benchmark_stats",
                  "bootstrap_excess_vs_benchmark"):
        assert chave not in res


def test_opcao_pesos_iguais_declara_ausencia_de_indice_sem_erro():
    res = bt.walk_forward(_panel(), top_n=2, weighting="equal", periods_per_year=1,
                          benchmark="Pesos iguais do universo",
                          benchmark_loader=_loader, bootstrap_samples=200)
    assert res["benchmark"]["ok"] is True
    assert res["benchmark"]["modo"] == bm.MODO_SEM_INDICE
    assert "excess_ann_vs_benchmark" not in res      # não há índice a comparar
    assert res["excess_ann_vs_ew"] is not None


# ── Falha fechada ─────────────────────────────────────────────────────────────
def test_benchmark_desconhecido_falha_fechado():
    res = bt.walk_forward(_panel(), top_n=2, weighting="equal", periods_per_year=1,
                          benchmark="Ibovespa", benchmark_loader=_loader,
                          bootstrap_samples=200)
    assert res["ok"] is True                          # a estratégia segue medida
    assert res["benchmark"]["ok"] is False
    assert res["benchmark"]["erro"] == bm.ERRO_DESCONHECIDO
    assert "Ibovespa" in res["benchmark"]["mensagem"]
    assert "excess_ann_vs_benchmark" not in res
    # e não pode ter caído em equal-weight disfarçado de índice
    assert "benchmark_stats" not in res


def test_benchmark_sem_serie_publicada_falha_fechado():
    def _vazio(_simbolos):
        return pd.DataFrame()

    res = bt.walk_forward(_panel(), top_n=2, weighting="equal", periods_per_year=1,
                          benchmark="Nasdaq-100 (QQQ)", benchmark_loader=_vazio,
                          benchmark_horizon_months=12, bootstrap_samples=200)
    assert res["benchmark"]["ok"] is False
    assert res["benchmark"]["erro"] == bm.ERRO_SEM_SERIE
    assert res["benchmark"]["simbolo"] == "QQQ"
    assert "excess_ann_vs_benchmark" not in res


def test_loader_que_levanta_excecao_vira_ausencia_declarada():
    def _quebrado(_simbolos):
        raise RuntimeError("conexão recusada")

    res = bt.walk_forward(_panel(), top_n=2, weighting="equal", periods_per_year=1,
                          benchmark="SPY", benchmark_loader=_quebrado,
                          benchmark_horizon_months=12, bootstrap_samples=200)
    assert res["ok"] is True and res["benchmark"]["erro"] == bm.ERRO_SEM_SERIE


# ── Interseção temporal e piso de observações ─────────────────────────────────
def test_intersecao_parcial_mede_o_excesso_so_na_janela_comum():
    """Série do índice começa tarde: o excesso usa apenas as datas cobertas."""
    curta = _serie(0.05, anos=6, inicio="2013-06-30")   # cobre 2013-06 em diante

    def _loader_curto(simbolos):
        return pd.DataFrame({s: curta for s in simbolos if s == "SPY"})

    painel = _panel(periods=8)                          # 2010..2017
    res = bt.walk_forward(painel, top_n=2, weighting="equal", periods_per_year=1,
                          benchmark="SPY", benchmark_loader=_loader_curto,
                          benchmark_horizon_months=12, bootstrap_samples=200)
    assert res["benchmark"]["ok"] is True
    coberto = res["benchmark"]["n_periodos"]
    assert 0 < coberto < res["n_periods"]
    assert res["benchmark"]["datas_sem_serie"]          # lacuna declarada, não some
    assert res["benchmark"]["cobertura"] == pytest.approx(coberto / res["n_periods"])
    # A carteira é remedida na MESMA janela — nunca 8 anos contra 4.
    assert res["portfolio_on_benchmark_window"]["n"] == coberto
    assert res["benchmark_stats"]["n"] == coberto
    assert res["portfolio_on_benchmark_window"]["n"] != res["portfolio"]["n"]


def test_intersecao_abaixo_do_piso_falha_fechado():
    quase_nada = _serie(0.05, anos=3, inicio="2015-06-30")

    def _loader_curtissimo(simbolos):
        return pd.DataFrame({s: quase_nada for s in simbolos if s == "SPY"})

    res = bt.walk_forward(_panel(periods=8), top_n=2, weighting="equal",
                          periods_per_year=1, benchmark="SPY",
                          benchmark_loader=_loader_curtissimo,
                          benchmark_horizon_months=12, benchmark_min_obs=6,
                          bootstrap_samples=200)
    assert res["benchmark"]["ok"] is False
    assert res["benchmark"]["erro"] == bm.ERRO_INTERSECAO_INSUFICIENTE
    assert res["benchmark"]["n_periodos"] < 6
    assert "excess_ann_vs_benchmark" not in res
    assert res["equal_weight"]["n"] == 8               # baseline própria intacta


def test_piso_padrao_do_modulo_e_o_mesmo_do_rank_ic():
    assert bm.MIN_OBS_BENCHMARK == 3
    duas = pd.Series([100.0, 105.0, 110.0],
                     index=pd.to_datetime(["2010-06-30", "2011-06-30", "2012-06-30"]))
    estado = bm.preparar("SPY", ["2010-06-30", "2011-06-30"], horizonte_meses=12,
                         serie=duas)
    assert estado["ok"] is False
    assert estado["erro"] == bm.ERRO_INTERSECAO_INSUFICIENTE
    assert estado["n_periodos"] == 2


# ── Higiene da série ──────────────────────────────────────────────────────────
def test_preco_nao_positivo_e_data_ilegivel_viram_ausencia():
    serie = pd.Series([100.0, 0.0, 121.0, 130.0],
                      index=["2020-06-30", "2021-06-30", "2022-06-30", "nao-e-data"])
    limpa = bm._higieniza_serie(serie, "SPY")
    assert list(limpa.values) == [100.0, 121.0]
    assert len(limpa) == 2


@pytest.mark.parametrize("invalido", [np.inf, -np.inf])
def test_higiene_rejeita_precos_infinitos_e_falha_fechado(invalido):
    serie = pd.Series([100.0, invalido, 121.0], index=pd.to_datetime([
        "2020-06-30", "2021-06-30", "2022-06-30",
    ]))
    limpa = bm._higieniza_serie(serie, "SPY")
    assert np.isfinite(limpa.to_numpy()).all()
    assert list(limpa.values) == [100.0, 121.0]
    # Sem endpoint válido para 12m, não se produz retorno parcial ou infinito.
    assert bm.retornos_realizados(serie, ["2020-06-30"], horizonte_meses=12).empty


def test_retorno_infinito_nao_vira_metrica_de_performance():
    panel = _panel()
    panel.loc[panel.index[0], "fwd_return"] = np.inf
    res = bt.walk_forward(panel, top_n=2, weighting="equal", periods_per_year=1,
                          bootstrap_samples=20)
    assert res["ok"] is False
    assert res["reason"] == "retornos ou scores não finitos"


# ── Facade que a view chama ───────────────────────────────────────────────────
def test_facade_us_data_propaga_o_benchmark_ao_motor(monkeypatch):
    """É por core.us_data.backtest que a view fia o seletor — o elo que faltava."""
    import core.us_data as us

    painel = _panel()
    monkeypatch.setattr(us, "score_panel", lambda **_kw: painel)
    spy = us.backtest(top_n=2, weighting="equal", benchmark="S&P 500 (SPY)",
                      benchmark_loader=_loader)
    qqq = us.backtest(top_n=2, weighting="equal", benchmark="Nasdaq-100 (QQQ)",
                      benchmark_loader=_loader)
    assert spy["benchmark"]["simbolo"] == "SPY"
    assert qqq["benchmark"]["simbolo"] == "QQQ"
    assert spy["excess_ann_vs_benchmark"] != qqq["excess_ann_vs_benchmark"]
    # O painel é anual: o benchmark tem de medir os mesmos 12 meses.
    assert spy["benchmark"]["horizonte_meses"] == us.HORIZONTE_PAINEL_MESES == 12
    # Chamada antiga (sem benchmark) continua válida e sem chave nova.
    assert "benchmark" not in us.backtest(top_n=2, weighting="equal")
    assert us.benchmark_options() == bm.BENCHMARK_LABELS


def test_serie_declara_fonte_moeda_e_versao_do_mapa():
    """G1/G2: unidade, moeda, fonte e versão viajam junto com o número."""
    estado = bm.preparar("S&P 500 (SPY)", [f"{2010 + d}-06-30" for d in range(8)],
                         horizonte_meses=12, loader=_loader)
    assert estado["moeda"] == "USD"
    assert "prices_monthly" in estado["fonte"]
    assert estado["mapa_versao"] == bm.US_BENCHMARK_MAP_VERSION
    assert estado["horizonte_meses"] == 12


def test_simulacao_da_ui_entrega_o_benchmark_escolhido_ao_motor():
    """O controle da simulação não pode virar apenas um rótulo na tela.

    A view é carregada com muitas dependências de dados; esta asserção de
    contrato verifica o trecho estreito que transforma a escolha da UI no
    argumento do facade, sem banco, rede ou runtime Streamlit.
    """
    view = (Path(__file__).resolve().parents[1] / "views"
            / "empresas_americanas.py").read_text(encoding="utf-8")
    body = view.split("def _render_us_lab_backtest() -> None:", 1)[1].split(
        "\n\n_US_COMPARE_METRICS", 1)[0]
    assert 'benchmark = st.selectbox("Benchmark de mercado", us.benchmark_options(),' in body
    call = re.search(r"us\.backtest\((.*?)\)", body, re.S)
    assert call and "benchmark=benchmark" in re.sub(r"\s+", "", call.group(1))
    assert "excess_ann_vs_benchmark" in body
    assert "benchmark_equity_curve" in body


def test_backtest_historico_original_entrega_o_benchmark_escolhido_ao_motor():
    """A rota de validação histórica também deve encaminhar a escolha, não só o laboratório."""
    view = (Path(__file__).resolve().parents[1] / "views"
            / "empresas_americanas.py").read_text(encoding="utf-8")
    body = view.split("def _tab_backtests(status: dict) -> None:", 1)[1].split(
        "\n\n# ── Qualidade dos Dados", 1)[0]
    assert 'benchmark = st.selectbox("Benchmark de mercado", us.benchmark_options(),' in body
    call = re.search(r"us\.backtest\((.*?)\)", body, re.S)
    assert call and "benchmark=benchmark" in re.sub(r"\s+", "", call.group(1))


def test_backtest_teorico_publica_concentracao_e_bloqueia_conclusao_quando_violada():
    rows = []
    for ano in range(3):
        rows.extend([
            {"date": f"{2020 + ano}-06-30", "symbol": "LIDER", "score": 100.0,
             "fwd_return": 0.10},
            {"date": f"{2020 + ano}-06-30", "symbol": "COADJUVANTE", "score": 0.0,
             "fwd_return": 0.00},
        ])
    res = bt.walk_forward(pd.DataFrame(rows), top_n=2, weighting="score",
                          periods_per_year=1, bootstrap_samples=20)
    concentration = res["concentration"]
    assert concentration["strategy_mode"] == "teorica_sem_cap"
    assert concentration["max_weight"] > concentration["policy_max_weight"]
    assert concentration["max_hhi"] > 0.99
    assert concentration["eligible_for_conclusion"] is False
