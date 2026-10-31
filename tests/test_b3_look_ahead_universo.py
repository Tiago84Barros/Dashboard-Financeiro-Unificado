"""Look-ahead da aba B3 (auditoria app4): o passado não pode ler a foto de hoje.

Três pontos, um teste por ponto:

- Os pisos de valor de mercado e de volume com o retrato de HOJE filtravam o
  universo de todos os anos da reconstrução. Quem encolheu sumia do passado,
  e o Pesos Iguais de cada safra perdia justamente as quedas. Agora cada ano
  usa o tamanho e o volume da época; o retrato de hoje só decide a carteira
  atual (``fora_hoje`` em ``_processar_segmento``).
- O tamanho da época é estimado pelo valor de hoje vezes a razão de preços
  sem dividendos (``core.b3_universo_pit.abaixo_do_tamanho_por_ano``).
- A Selic de ano sem observação era a média da série inteira, futuro
  incluído; agora repete o último valor conhecido
  (``core.b3_safras.selic_sem_futuro``).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd

from core import b3_universo_pit as upit
from core.b3_safras import selic_sem_futuro

ROOT = Path(__file__).resolve().parents[1]


def _fech(ticker: str, pontos: dict[str, float]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"ticker": ticker, "mes": pd.Timestamp(m).date(), "fechamento": v}
         for m, v in pontos.items()]
    )


# ── tamanho por época ───────────────────────────────────────────────────────

def test_quem_era_pequeno_na_epoca_fica_fora_daquele_ano_e_volta_depois():
    # Hoje vale R$ 4 bi a R$ 40; em dez/2018 o preço era R$ 5 (R$ 0,5 bi) e em
    # dez/2020, R$ 20 (R$ 2 bi). Piso de R$ 1 bi.
    df = _fech("CRES3", {"2018-12-01": 5.0, "2020-12-01": 20.0, "2026-08-01": 40.0})
    out = upit.abaixo_do_tamanho_por_ano(df, {"CRES3": 4e9}, [2019, 2021], 1e9)
    assert out[2019] == {"CRES3"}
    assert out[2021] == set()


def test_quem_encolheu_continua_no_passado_em_que_era_grande():
    # O caso que o filtro de hoje apagava: hoje R$ 0,4 bi, em 2019 R$ 4 bi.
    df = _fech("ENCO3", {"2018-12-01": 50.0, "2026-08-01": 5.0})
    out = upit.abaixo_do_tamanho_por_ano(df, {"ENCO3": 4e8}, [2019], 1e9)
    assert out[2019] == set()


def test_le_o_ultimo_fechamento_da_janela_de_folga_e_nao_o_futuro():
    # Sem dezembro, vale o último mês até dois meses antes; janeiro do ano da
    # decisão já é futuro para a régua de 31/12.
    df = _fech("FOLG3", {"2018-10-01": 1.0, "2019-01-01": 100.0, "2026-08-01": 10.0})
    out = upit.abaixo_do_tamanho_por_ano(df, {"FOLG3": 5e9}, [2019], 1e9)
    assert out[2019] == {"FOLG3"}  # 5 bi × 1/10 = 0,5 bi


def test_sem_preco_na_janela_ou_sem_valor_de_hoje_nao_marca():
    df = pd.concat([
        _fech("VELH3", {"2016-06-01": 1.0, "2026-08-01": 10.0}),
        _fech("SEMV3", {"2018-12-01": 1.0, "2026-08-01": 10.0}),
    ])
    out = upit.abaixo_do_tamanho_por_ano(df, {"VELH3": 5e9}, [2019], 1e9)
    assert out[2019] == set()


def test_piso_zero_nao_marca_ninguem():
    df = _fech("CRES3", {"2018-12-01": 5.0, "2026-08-01": 40.0})
    assert upit.abaixo_do_tamanho_por_ano(df, {"CRES3": 4e9}, [2019], 0.0) == {2019: set()}


def test_tamanho_vale_mesmo_no_ano_sem_volume_medido():
    eleg = {2019: {"medido": False, "abaixo": {"ILIQ3"}, "parados": set(),
                   "tickers_medidos": 0}}
    eleg = upit.incorporar_tamanho(eleg, {2019: {"CRES3"}, 2020: {"CRES3"}})
    # ILIQ3 não sai (volume não medido não filtra); CRES3 sai pelo tamanho.
    assert upit.filtrar(["CRES3", "ILIQ3", "GRAN3"], eleg, 2019) == ["ILIQ3", "GRAN3"]
    # Ano que só tinha tamanho ganha a entrada.
    assert upit.filtrar(["CRES3", "GRAN3"], eleg, 2020) == ["GRAN3"]


def test_tamanho_soma_ao_volume_medido():
    eleg = {2019: {"medido": True, "abaixo": {"ILIQ3"}, "parados": {"PARA3"},
                   "tickers_medidos": 3}}
    eleg = upit.incorporar_tamanho(eleg, {2019: {"CRES3"}})
    assert upit.filtrar(["CRES3", "ILIQ3", "PARA3", "GRAN3"], eleg, 2019) == ["GRAN3"]


def test_incorporar_nao_altera_a_elegibilidade_recebida():
    eleg = {2019: {"medido": True, "abaixo": set(), "parados": set(),
                   "tickers_medidos": 1}}
    upit.incorporar_tamanho(eleg, {2019: {"CRES3"}})
    assert "abaixo_tamanho" not in eleg[2019]


# ── Selic sem futuro ────────────────────────────────────────────────────────

def test_ano_ausente_repete_o_anterior_e_nao_a_media():
    selic = {2015: 0.14, 2016: 0.14, 2018: 0.065, 2023: 0.1175}
    out = selic_sem_futuro(selic, 2026)
    assert out[2017] == 0.14          # média daria ~0,116, com 2023 dentro
    assert out[2019] == out[2022] == 0.065
    assert out[2024] == out[2026] == 0.1175
    assert 2014 not in out            # antes do primeiro: fallback de quem chama


def test_selic_vazia_continua_vazia():
    assert selic_sem_futuro({}, 2026) == {}


# ── a view não volta a filtrar o passado com a foto de hoje ─────────────────

def _render_src() -> str:
    return (ROOT / "views" / "portfolio_b3.py").read_text(encoding="utf-8")


def test_reconstrucao_le_o_universo_historico_e_carteira_le_o_de_hoje():
    src = _render_src()
    assert 'all_tickers = tuple(sorted(df_set_hist["ticker"].unique()))' in src
    assert "df_set_vivas = df_set\n        df_set = df_set_hist" in src
    assert "fora_hoje={_ticker_key(t) for t in _fora_hoje}" in src
    # O volume da época e o tamanho da época leem o universo inteiro.
    assert 'for t in df_set_hist["ticker"].unique()}\n        )]' in src
    assert "_upit.abaixo_do_tamanho_por_ano(" in src
    assert "_upit.incorporar_tamanho(" in src


def test_backtest_e_safras_recebem_a_selic_sem_futuro():
    arvore = ast.parse(_render_src())
    chamadas = {}
    for no in ast.walk(arvore):
        if isinstance(no, ast.Call) and isinstance(no.func, ast.Name) \
                and no.func.id in {"_processar_segmento", "render_safras"}:
            chamadas[no.func.id] = no
    proc = chamadas["_processar_segmento"]
    # 8º posicional é selic_macro.
    assert isinstance(proc.args[7], ast.Name) and proc.args[7].id == "selic_hist"
    safras = chamadas["render_safras"]
    kw = {k.arg: k.value for k in safras.keywords}
    assert isinstance(kw["selic_por_ano"], ast.Name)
    assert kw["selic_por_ano"].id == "selic_hist"


def _hist_seg(tks: list[str], anos: range) -> dict[str, pd.DataFrame]:
    return {
        tk: pd.DataFrame({
            "Ticker": tk,
            "Data": [pd.Timestamp(a, 12, 31) for a in anos],
            "ROE": [0.12 + i * 0.03] * len(anos),
            "ROIC": [0.10 + i * 0.02] * len(anos),
            "Margem_Liquida": [0.08] * len(anos),
            "P/L": [8.0] * len(anos),
            "P/VP": [1.2] * len(anos),
            "DY": [0.04] * len(anos),
            "AvailableAt": [pd.Timestamp(a + 1, 3, 20) for a in anos],
        })
        for i, tk in enumerate(tks)
    }


def test_fora_hoje_tira_da_carteira_atual_sem_tirar_do_passado():
    """Quem está abaixo do piso hoje concorre (e lidera) nos anos em que a
    época o aceitava, mas não entra no score do próximo ano."""
    import numpy as np

    import views.portfolio_b3 as portfolio

    hoje = pd.Timestamp.now()
    tks = ["AAAA3", "BBBB3", "CCCC3", "ENCO3"]  # ENCO3 tem o melhor ROE
    idx = pd.date_range("2016-01-31", periods=(hoje.year - 2016) * 12, freq="ME")
    precos = pd.DataFrame({
        tk: 100.0 * np.cumprod(np.full(len(idx), 1.004 + i * 0.001))
        for i, tk in enumerate(tks)
    }, index=idx)
    res = portfolio._processar_segmento(
        tks, _hist_seg(tks, range(2012, hoje.year)), precos, "S", "SS", "SEG",
        taxa_selic_aa=0.0, selic_macro={}, macro_history={}, aporte=1000.0,
        ano_inicio=2018, gamma=1.0, cap=1.0, soft=0.0, fora_hoje={"ENCO3"},
    )
    assert res is not None
    anos_lider = {a for a, lids in res["lids_por_ano"].items() if "ENCO3" in lids}
    # Lidera no passado (o decaimento por liderança longa tira alguns anos);
    # com o filtro de hoje no universo inteiro, não apareceria em nenhum.
    assert 2018 in anos_lider and len(anos_lider) >= 3
    assert "ENCO3" not in res["score_proximo"]
    assert "ENCO3" not in res["lids_prox"]
    assert "ENCO3" in res["tickers"]


def test_segmento_sem_compravel_hoje_nem_reconstroi(monkeypatch):
    import views.portfolio_b3 as portfolio

    chamado = []
    monkeypatch.setattr(portfolio, "_score_historico_ano_com_cobertura",
                        lambda *a, **k: chamado.append(1) or ({}, None))
    res = portfolio._processar_segmento(
        ["AAAA3"], {}, pd.DataFrame(), "S", "SS", "SEG",
        0.1, {}, {}, 1000.0, 2020, 1.0, 0.5, 0.0, fora_hoje={"AAAA3"},
    )
    assert res is None and not chamado


def test_score_de_hoje_tem_o_mesmo_decaimento_da_reconstrucao(monkeypatch):
    """Sobra do look-ahead (2.32.0): a reconstrução penaliza liderança longa e
    o score da carteira de hoje não penalizava -- a evidência media uma regra
    que a tela não entregava."""
    import numpy as np

    import views.portfolio_b3 as portfolio

    real = portfolio._apply_decay_penalty
    chamadas = []

    def espiao(score_map, anos_lideranca, *a, **k):
        saida = real(score_map, anos_lideranca, *a, **k)
        chamadas.append((dict(score_map), dict(anos_lideranca), saida))
        return saida

    monkeypatch.setattr(portfolio, "_apply_decay_penalty", espiao)
    hoje = pd.Timestamp.now()
    tks = ["AAAA3", "BBBB3", "CCCC3", "DDDD3"]
    idx = pd.date_range("2016-01-31", periods=(hoje.year - 2016) * 12, freq="ME")
    precos = pd.DataFrame({
        tk: 100.0 * np.cumprod(np.full(len(idx), 1.004 + i * 0.001))
        for i, tk in enumerate(tks)
    }, index=idx)
    res = portfolio._processar_segmento(
        tks, _hist_seg(tks, range(2012, hoje.year)), precos, "S", "SS", "SEG",
        taxa_selic_aa=0.0, selic_macro={}, macro_history={}, aporte=1000.0,
        ano_inicio=2018, gamma=1.0, cap=1.0, soft=0.0,
    )
    assert res is not None
    anos_hist = sorted(res["lids_por_ano"])
    assert len(chamadas) == len(anos_hist) + 1  # um por safra + o de hoje
    bruto, anos, saida = chamadas[-1]
    assert res["score_proximo"] == saida
    # a sequência que chega ao score de hoje é a que termina em ano_atual-1
    ultimo = anos_hist[-1]
    esperado = {}
    for tk in res["lids_por_ano"][ultimo]:
        n, a = 0, ultimo
        while a in res["lids_por_ano"] and tk in res["lids_por_ano"][a]:
            n, a = n + 1, a - 1
        esperado[tk] = n
    assert anos == esperado
    # e a penalidade de fato morde quem lidera há anos
    lider = max(esperado, key=esperado.get)
    assert esperado[lider] >= 1
    assert res["score_proximo"][lider] < bruto[lider]
