"""Score B3 de grupo sem par comparável (caso WEGE3, 16/11/2026).

A nota é percentil dentro do grupo, e a régua da decisão são as compráveis de
hoje -- análoga às elegíveis da época que a reconstrução usa. Com uma só
comprável, nenhum indicador tem dois valores e a nota não mede nada: a WEGE3
aparecia como #1 da revisão com score 0,0 e puxava a média. Desde a 2.33.0
(achado D2, 17/11/2026) o indicador sem ordem vale o neutro e a nota sai 50,
não 0 -- mas continua sem comparar ninguém, por isso a marca fica. A régua
fica (alargá-la tiraria a decisão da regra que o backtest mediu); a leitura
muda: "sem par comparável" no cartão, fora da média, fora do texto da LLM.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.b3_methodology import media_scores_comparaveis


def _hist_seg(tks: list[str]) -> dict[str, pd.DataFrame]:
    anos = list(range(2016, pd.Timestamp.now().year))
    return {
        tk: pd.DataFrame({
            "Ticker": tk,
            "Data": [pd.Timestamp(a, 12, 31) for a in anos],
            "ROE": [0.12 + i * 0.03] * len(anos),
            "ROIC": [0.10 + i * 0.02] * len(anos),
            "Margem_Liquida": [0.08 + i * 0.01] * len(anos),
            "P/L": [8.0 + i] * len(anos),
            "P/VP": [1.2 + 0.1 * i] * len(anos),
            "DY": [0.04] * len(anos),
            "AvailableAt": [pd.Timestamp(a + 1, 3, 20) for a in anos],
        })
        for i, tk in enumerate(tks)
    }


def _precos(tks: list[str]) -> pd.DataFrame:
    hoje = pd.Timestamp.now()
    idx = pd.date_range("2016-01-31", periods=(hoje.year - 2016) * 12, freq="ME")
    return pd.DataFrame({
        tk: 100.0 * np.cumprod(np.full(len(idx), 1.004 + i * 0.001))
        for i, tk in enumerate(tks)
    }, index=idx)


def _segmento(tks, fora_hoje=None):
    from views import portfolio_b3 as portfolio

    return portfolio._processar_segmento(
        tks, _hist_seg(tks), _precos(tks), "S", "SS", "SEG",
        taxa_selic_aa=0.0, selic_macro={}, macro_history={}, aporte=1000.0,
        ano_inicio=2018, gamma=1.0, cap=1.0, soft=0.0, fora_hoje=fora_hoje,
    )


TKS = ["AAAA3", "BBBB3", "CCCC3"]


def test_unica_compravel_sai_marcada_sem_par_e_segue_lider():
    so_uma = _segmento(TKS, fora_hoje={"AAAA3", "BBBB3"})
    assert so_uma is not None
    assert set(so_uma["score_proximo"]) == {"CCCC3"}
    assert so_uma["lids_prox"] == ["CCCC3"]
    # Sem par, todo indicador vale o neutro: a nota é 50 antes dos ajustes
    # multiplicativos, nunca o 0,0 de antes da 2.33.0 (achado D2). Neutro
    # também não mede nada -- por isso a marca.
    nota = so_uma["score_proximo"]["CCCC3"]
    assert nota > 0.0
    assert nota == pytest.approx(50.0, abs=20.0)
    assert so_uma["score_sem_par"] is True


def test_regua_da_decisao_continua_sendo_a_das_compraveis(monkeypatch):
    # Quem não é comprável hoje não entra na régua, como as não elegíveis da
    # época não entram na do backtest: a nota das duas compráveis é a mesma
    # de um grupo que só tivesse as duas. Medida ANTES do decaimento por anos
    # de liderança: esse vem do histórico (com três elegíveis a BBBB3 lidera
    # um ano e a sequência da CCCC3 recomeça), não da régua de hoje.
    from views import portfolio_b3 as portfolio

    monkeypatch.setattr(portfolio, "_apply_decay_penalty", lambda s, _anos: dict(s))
    duas = _segmento(TKS, fora_hoje={"BBBB3"})
    so_elas = _segmento(["AAAA3", "CCCC3"])
    assert duas["score_sem_par"] is False
    assert set(duas["score_proximo"]) == {"AAAA3", "CCCC3"}
    for tk in ("AAAA3", "CCCC3"):
        assert duas["score_proximo"][tk] == pytest.approx(so_elas["score_proximo"][tk])


def test_grupo_com_par_nao_e_marcado():
    res = _segmento(TKS)
    assert res["score_sem_par"] is False


def test_grupo_de_uma_empresa_e_sem_par_comparavel():
    res = _segmento(["AAAA3"])
    assert res is not None
    assert res["score_sem_par"] is True


_PESOS = {"ROE": (0.5, True), "P/L": (0.5, False)}


def _mult(linhas: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(linhas)
    df["SEGMENTO"] = "SEG"
    return df


def test_motor_oficial_grupo_de_uma_empresa_sai_no_neutro():
    """Achado D2: sem dois valores, o indicador vale 0,5 do peso, não zero."""
    from views.empresas_b3 import _score_universo

    df = _mult([{"Ticker": "SOLO3", "ROE": 0.18, "P/L": 9.0}])
    out = _score_universo(df, ["SOLO3"], _PESOS, group_col_prefer="SEGMENTO")
    assert float(out["score_raw"].iloc[0]) == pytest.approx(50.0)


def test_motor_oficial_indicador_sem_ordem_nao_muda_a_ordem_do_grupo():
    """Um indicador com um valor só soma a mesma constante a todos: a ordem
    do grupo com par é a do indicador que ordena, com ou sem ele."""
    from views.empresas_b3 import _score_universo

    base = [{"Ticker": "A3", "ROE": 0.25}, {"Ticker": "B3", "ROE": 0.15},
            {"Ticker": "C3", "ROE": 0.05}]
    com_isolado = [dict(base[0], **{"P/L": 8.0}), base[1], base[2]]
    so_roe = _score_universo(_mult(base), ["A3", "B3", "C3"], {"ROE": (1.0, True)},
                             group_col_prefer="SEGMENTO")
    ambos = _score_universo(_mult(com_isolado), ["A3", "B3", "C3"], _PESOS,
                            group_col_prefer="SEGMENTO")
    def ordem(o):
        return o.sort_values("score_raw", ascending=False)["Ticker"].tolist()

    assert ordem(so_roe) == ordem(ambos) == ["A3", "B3", "C3"]
    raw = dict(zip(ambos["Ticker"], ambos["score_raw"]))
    # A metade do peso do P/L sem ordem é neutra (25 pontos) para os três.
    assert min(raw.values()) >= 25.0 - 1e-9


def test_media_ignora_nota_sem_par_e_nota_ausente():
    itens = [{"score": 0.0, "score_sem_par": True}, {"score": 60.0},
             {"score": 40.0}, {"score": None}, {"score": float("nan")}]
    assert media_scores_comparaveis(itens) == pytest.approx(50.0)
    assert media_scores_comparaveis([{"score": 0.0, "score_sem_par": True}]) is None
    # Zero observado num grupo com par continua entrando.
    assert media_scores_comparaveis([{"score": 0.0}, {"score": 10.0}]) == pytest.approx(5.0)


def test_cartao_da_revisao_diz_sem_par_e_fica_fora_da_media():
    from design import portfolio_review as pr

    sem_par = {"ticker": "WEGE3", "weight": 0.2, "setor": "Bens Industriais",
               "segmento": "Motores", "score_segmento": 0.0, "score_sem_par": True,
               "aprovado": True, "evidencia_segmento": 10.0, "quality": 2.0}
    comum = {"ticker": "CXSE3", "weight": 0.2, "setor": "Financeiro",
             "segmento": "Seguros", "score_segmento": 65.5, "score_sem_par": False,
             "aprovado": True, "evidencia_segmento": 5.0, "quality": 1.0}
    card = pr._html_card(1, sem_par)
    assert "sem par comparável" in card
    assert ">0.0<" not in card
    assert pr._score(sem_par) is None
    resumo = pr._html_resumo({"items": [sem_par, comum], "allocated_weight": 0.4,
                              "unallocated_weight": 0.6})
    assert "65.5" in resumo and "de 0.0" not in resumo


def test_rota_de_revisao_leva_a_marca_ate_o_item():
    from core import portfolio_review_routes as routes

    seg = {"setor": "Bens Industriais", "subsetor": "Máquinas", "segmento": "Motores",
           "score_proximo": {"WEGE3": 0.0}, "score_sem_par": True}
    fundamentals = pd.DataFrame([{"ticker": "WEGE3", "setor": "Bens Industriais"}])
    result = routes.b3_review([seg], fundamentals, None, cap=0.2, sector_cap=0.4,
                              cycle_cap=0.6, selic=0.1)
    assert result["items"]
    for item in result["items"]:
        assert item["score_sem_par"] is True


def test_dashboard_geral_nao_mostra_zero_para_media_ausente():
    from views.dashboard_geral import _texto_score_medio

    assert _texto_score_medio({"score_medio": None}) == "—"
    assert _texto_score_medio({}) == "—"
    assert _texto_score_medio({"score_medio": 47.6}) == "47.60"
    assert _texto_score_medio({"score_medio": 0.0}) == "0.00"
