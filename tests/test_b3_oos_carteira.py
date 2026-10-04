"""Testes de core/b3_oos_carteira.py (B3-03): aprovação PIT, montagem,
custos do giro, veto hipotético e coerência com core.b3_safras."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

import core.b3_oos_carteira as oos
from core.b3_safras import SafraCarteira, retorno_da_safra
from core.b3_vigencia import janela_de_vigencia
from core.transaction_costs import CostConfig, custo_compra, custo_venda

CFG = CostConfig.brasil_pf_default()

PARAMS = {
    "criterio_modo": "economico", "thr_selic": 15.0, "thr_ew": 0.0,
    "usar_ew": False, "max_anos_lid": 5, "exigir_resiliencia": False,
    "thr_roic_spread": 0.0, "exigir_vantagem_selecao": False,
}


def _m(**kw):
    base = {"val_est": 130.0, "val_selic": 100.0, "val_ew": 110.0,
            "rank_ic_mean": 0.05, "selecao_excesso": None,
            "ultimo_lid": {"AAAA3": 2020}, "roic_spread_mean": 0.05,
            "roic_hit_rate": 0.8}
    base.update(kw)
    return base


# ── aprovação ───────────────────────────────────────────────────────────────

def test_aprova_quando_bate_selic_pela_margem():
    assert oos.aprova_economico(_m(), PARAMS, 2021) == (True, "")


def test_reprova_margem_selic_e_ic_contra():
    assert not oos.aprova_economico(_m(val_est=110.0), PARAMS, 2021)[0]
    assert oos.aprova_economico(_m(rank_ic_mean=-0.2), PARAMS, 2021)[1] == "Rank-IC contra"


def test_recencia_medida_contra_a_safra_e_nao_contra_hoje():
    # Liderança em 2015: vale para a safra 2021 (5 anos), não para a 2022.
    m = _m(ultimo_lid={"AAAA3": 2015})
    assert oos.aprova_economico(m, PARAMS, 2021)[0]
    assert oos.aprova_economico(m, PARAMS, 2022)[1] == "liderança antiga"


def test_resiliencia_e_margem_ew():
    p = {**PARAMS, "exigir_resiliencia": True, "thr_roic_spread": 0.10}
    assert "resiliência" in oos.aprova_economico(_m(), p, 2021)[1]
    p = {**PARAMS, "usar_ew": True, "thr_ew": 20.0}
    assert oos.aprova_economico(_m(), p, 2021)[1] == "margem sobre pesos iguais"


def test_criterio_estatistico_nao_e_fingido():
    with pytest.raises(ValueError):
        oos.aprova_economico(_m(), {**PARAMS, "criterio_modo": "sinal"}, 2021)


# ── seleção e veto ──────────────────────────────────────────────────────────

def _res_seg():
    return {
        "lids_por_ano": {2021: ["AAAA3", "BBBB3"]},
        "pesos_por_ano": {2021: {"AAAA3": 0.6, "BBBB3": 0.4}},
        "score_rows": [
            {"Ano": 2021, "ticker": "AAAA3", "Score_Ajustado": 3.0},
            {"Ano": 2021, "ticker": "BBBB3", "Score_Ajustado": 2.0},
            {"Ano": 2021, "ticker": "CCCC3", "Score_Ajustado": 1.0},
        ],
    }


def test_selecao_lider_mais_maior_participacao_com_peso_do_top_n():
    def pode(tk, ultimo, scores, ano_ref, max_anos):
        assert ano_ref == 2020  # safra N usa N-1, como a tela usa ano_atual-1
        return (True, "", None, None)
    sel, pesos, ranking = oos.selecao_do_segmento(
        _res_seg(), {"maior": "BBBB3", "ultimo_lid": {}}, 2021,
        max_anos_lid=5, pode_incluir_maior=pode)
    assert sel == ["AAAA3", "BBBB3"]
    assert ranking[0][0] == "AAAA3"
    # maior fora do top-n entra na seleção mas sem peso -- a montagem descarta
    sel, pesos, _ = oos.selecao_do_segmento(
        _res_seg(), {"maior": "CCCC3", "ultimo_lid": {}}, 2021,
        max_anos_lid=5, pode_incluir_maior=pode)
    assert "CCCC3" in sel and pesos.get("CCCC3", 0.0) == 0.0


def _seg(setor, sel, pesos, ranking):
    return (setor, list(sel), dict(pesos), [(tk, float(10 - k)) for k, tk in enumerate(ranking)])


TETOS = {"cap": 1.0, "teto_setor": 1.0, "teto_ciclico": 1.0}


def test_substituto_e_o_proximo_do_ranking_fora_da_selecao_e_herda_peso():
    segs = [_seg("Financeiro", ["AAAA3", "BBBB3"], {"AAAA3": 0.6, "BBBB3": 0.4},
                 ["AAAA3", "BBBB3", "CCCC3"])]
    itens, trocas = oos.vetar_na_carteira(segs, "AAAA3")
    assert itens == [("Financeiro", ["BBBB3", "CCCC3"],
                      {"AAAA3": 0.6, "BBBB3": 0.4, "CCCC3": 0.6})]
    assert trocas == [{"sai": "AAAA3", "entra": "CCCC3", "setor": "Financeiro"}]
    # não muta a entrada: o sem-portão e as outras opções partem do mesmo lugar
    assert segs[0][1] == ["AAAA3", "BBBB3"] and "CCCC3" not in segs[0][2]
    assert oos.vetar_na_carteira(segs, None)[0] == [segs[0][:3]]


def _carteira_de_lideres():
    """Quatro segmentos, cada um com UM nome (o caso que empatava na 1.0.0)."""
    segs = [_seg("S1", ["L1"], {"L1": 1.0}, ["L1", "R1"]),
            _seg("S2", ["L2"], {"L2": 1.0}, ["L2", "R2"]),
            _seg("S3", ["L3"], {"L3": 1.0}, ["L3", "R3"]),
            _seg("S4", ["L4"], {"L4": 1.0}, ["L4", "R4"])]
    rets = {"L1": 0.40, "L2": 0.10, "L3": -0.30, "L4": 0.00,
            "R1": 0.00, "R2": 0.05, "R3": 0.20, "R4": 0.00}
    return segs, rets


def test_variantes_divergem_quando_cada_segmento_tem_um_unico_nome():
    segs, rets = _carteira_de_lideres()
    adv = oos.escolher_veto(segs, rets, veta="melhor", **TETOS)
    fav = oos.escolher_veto(segs, rets, veta="pior", **TETOS)
    sem = sum(0.25 * rets[f"L{i}"] for i in range(1, 5))
    # adversário tira o líder que mais rendeu (L1 +40% -> R1 0%): -10 pp
    assert adv["vetado"] == "L1" and adv["bruto"] == pytest.approx(sem - 0.10)
    # favorável tira o que mais perdeu (L3 -30% -> R3 +20%): +12,5 pp
    assert fav["vetado"] == "L3" and fav["bruto"] == pytest.approx(sem + 0.125)
    assert adv["vetado"] != fav["vetado"]
    assert adv["bruto"] < sem < fav["bruto"]
    assert set(adv["carteira"]["pesos"]) == {"R1", "L2", "L3", "L4"}
    assert fav["carteira"]["pesos"]["R3"] == pytest.approx(0.25)


def test_um_so_segmento_com_um_so_nome_ainda_da_banda_com_largura():
    # Na 1.0.0 as duas variantes vetavam o mesmo líder e empatavam. Com "não
    # vetar" como opção, a banda vai de min a max de {líder, substituto}.
    segs = [_seg("S1", ["L1"], {"L1": 1.0}, ["L1", "R1"])]
    rets = {"L1": 0.10, "R1": -0.05}
    adv = oos.escolher_veto(segs, rets, veta="melhor", **TETOS)
    fav = oos.escolher_veto(segs, rets, veta="pior", **TETOS)
    assert adv["vetado"] == "L1" and adv["bruto"] == pytest.approx(-0.05)
    assert fav["vetado"] is None and fav["bruto"] == pytest.approx(0.10)
    assert fav["bruto"] - adv["bruto"] == pytest.approx(0.15)


def test_vetado_sem_substituto_esvazia_o_segmento_e_o_orcamento_vai_aos_demais():
    # Segmento de um nome só e sem mais ninguém no ranking: o veto o tira da
    # carteira ("→ ninguém") e a remontagem divide o orçamento entre os outros.
    segs = [_seg("S1", ["L1"], {"L1": 1.0}, ["L1"]),
            _seg("S2", ["L2"], {"L2": 1.0}, ["L2"])]
    rets = {"L1": -0.40, "L2": 0.10}
    fav = oos.escolher_veto(segs, rets, veta="pior", **TETOS)
    assert fav["vetado"] == "L1"
    assert fav["trocas"] == [{"sai": "L1", "entra": None, "setor": "S1"}]
    assert fav["carteira"]["pesos"] == pytest.approx({"L2": 1.0})
    assert fav["bruto"] == pytest.approx(0.10)


def test_vetar_a_maior_sem_peso_poe_o_substituto_com_o_peso_proprio():
    # A maior participação fora do top-n entra sem peso; o portão da tela roda
    # antes do filtro de peso, e vetá-la põe o próximo do ranking (B3, que tem
    # peso próprio no top-n) na carteira. Aqui esse é o melhor veto: vetar A3
    # o tira também de S2, onde o substituto C3 afunda.
    segs = [_seg("S1", ["A3", "Z3"], {"A3": 0.5, "B3": 0.5}, ["A3", "Z3", "B3"]),
            _seg("S2", ["A3"], {"A3": 1.0}, ["A3", "C3"])]
    rets = {"A3": 0.0, "Z3": 0.0, "B3": 0.60, "C3": -1.0}
    assert "Z3" in oos.candidatos_a_veto(segs)
    sem = oos.montar_carteira(oos.vetar_na_carteira(segs, None)[0], **TETOS)
    assert sem["pesos"] == pytest.approx({"A3": 1.0})
    fav = oos.escolher_veto(segs, rets, veta="pior", **TETOS)
    assert fav["vetado"] == "Z3"
    assert fav["trocas"] == [{"sai": "Z3", "entra": "B3", "setor": "S1"}]
    assert fav["carteira"]["pesos"] == pytest.approx({"A3": 0.75, "B3": 0.25})
    assert fav["bruto"] == pytest.approx(0.15)


def test_escolhe_pelo_impacto_na_carteira_e_nao_pelo_maior_retorno():
    # A3 rendeu mais, mas tem 10% do segmento e um substituto quase igual;
    # B3 rendeu menos com peso cheio e um substituto que perdeu.
    segs = [_seg("S1", ["A3", "X3"], {"A3": 0.1, "X3": 0.9}, ["X3", "A3", "SA3"]),
            _seg("S2", ["B3"], {"B3": 1.0}, ["B3", "SB3"])]
    rets = {"A3": 0.80, "SA3": 0.78, "X3": 0.0, "B3": 0.30, "SB3": -0.20}
    adv = oos.escolher_veto(segs, rets, veta="melhor", **TETOS)
    assert adv["vetado"] == "B3"


def test_veto_vale_para_o_papel_em_todo_segmento_que_o_escolheu():
    segs = [_seg("S1", ["D3"], {"D3": 1.0}, ["D3", "E3"]),
            _seg("S2", ["D3", "F3"], {"D3": 0.5, "F3": 0.5}, ["D3", "F3", "G3"])]
    itens, trocas = oos.vetar_na_carteira(segs, "D3")
    assert [t["entra"] for t in trocas] == ["E3", "G3"]
    assert all("D3" not in sel for _s, sel, _p in itens)
    rets = {"D3": 0.5, "E3": 0.0, "F3": 0.0, "G3": 0.0}
    assert oos.tickers_da_banda(segs) == ["D3", "E3", "F3", "G3"]
    adv = oos.escolher_veto(segs, rets, veta="melhor", **TETOS)
    assert adv["vetado"] == "D3" and adv["bruto"] == pytest.approx(0.0)


def test_empate_fica_com_nao_vetar_mesmo_com_nome_sem_peso_candidato():
    # Z3 entra sem peso e o substituto B3 também não tem: vetar Z3 não muda a
    # carteira, empata com "não vetar" e o empate fica com não vetar.
    segs = [_seg("S1", ["A3", "Z3"], {"A3": 1.0}, ["A3", "Z3", "B3"])]
    assert oos.candidatos_a_veto(segs) == ["A3", "Z3"]
    rets = {"A3": 0.1, "B3": 0.1, "Z3": 9.0}
    for veta in ("melhor", "pior"):
        esc = oos.escolher_veto(segs, rets, veta=veta, **TETOS)
        assert esc["vetado"] is None and esc["trocas"] == []
    with pytest.raises(ValueError):
        oos.escolher_veto(segs, rets, veta="medio", **TETOS)


def test_banda_respeita_o_cap_na_remontagem():
    # X0 tem 10% do segmento (3,3% da carteira crua), mas o cap de 25% com 4
    # ativos leva todos a 25%: vetá-lo custa 15 pp, não os 2 pp da troca crua.
    segs = [_seg("S0", ["L0", "X0"], {"L0": 0.9, "X0": 0.1}, ["L0", "X0", "R0"]),
            _seg("S1", ["L1"], {"L1": 1.0}, ["L1", "R1"]),
            _seg("S2", ["L2"], {"L2": 1.0}, ["L2", "R2"])]
    rets = {"L0": 0.0, "X0": 0.6, "R0": 0.0, "L1": 0.0, "R1": 0.0,
            "L2": 0.0, "R2": 0.0}
    tetos = {"cap": 0.25, "teto_setor": 1.0, "teto_ciclico": 1.0}
    sem = oos.montar_carteira(oos.vetar_na_carteira(segs, None)[0], **tetos)
    assert sem["pesos"]["X0"] == pytest.approx(0.25)
    adv = oos.escolher_veto(segs, rets, veta="melhor", **tetos)
    assert adv["vetado"] == "X0" and adv["bruto"] == pytest.approx(0.0)
    assert max(adv["carteira"]["pesos"].values()) <= 0.25 + 1e-9


# ── montagem ────────────────────────────────────────────────────────────────

def test_orcamento_igual_por_segmento_e_duplicata_somada():
    itens = [("Financeiro", ["A3", "B3"], {"A3": 0.5, "B3": 0.5}),
             ("Utilidade Pública", ["C3"], {"C3": 1.0}),
             ("Consumo", ["D3", "A3"], {"D3": 0.5, "A3": 0.5}),
             ("Saúde", ["E3"], {"E3": 1.0})]
    cart = oos.montar_carteira(itens, cap=1.0, teto_setor=1.0, teto_ciclico=1.0)
    assert cart["pesos"]["A3"] == pytest.approx(0.25)
    assert cart["pesos"]["C3"] == pytest.approx(0.25)
    assert sum(cart["pesos"].values()) == pytest.approx(1.0)
    assert not cart["inviavel"]


def test_cap_aplicado_e_inviavel_marcado():
    itens = [(f"S{i}", [f"T{i}"], {f"T{i}": 1.0}) for i in range(5)]
    itens[0] = ("S0", ["T0", "X0"], {"T0": 0.9, "X0": 0.1})
    cart = oos.montar_carteira(itens, cap=0.25, teto_setor=1.0, teto_ciclico=1.0)
    assert max(cart["pesos"].values()) <= 0.25 + 1e-9
    poucos = oos.montar_carteira(itens[:2], cap=0.25, teto_setor=1.0, teto_ciclico=1.0)
    assert poucos["inviavel"] and poucos["exige_revisao"]


# ── retorno e custos ────────────────────────────────────────────────────────

def _precos():
    idx = pd.date_range("2020-01-31", "2023-06-30", freq="ME")
    rng = np.random.default_rng(7)
    cols = [f"T{i:02d}3" for i in range(40)]
    rets = rng.normal(0.01, 0.08, size=(len(idx), len(cols)))
    return pd.DataFrame(100 * np.cumprod(1 + rets, axis=0), index=idx, columns=cols)


def test_retorno_por_ticker_bate_com_core_b3_safras():
    df = _precos()
    universo = list(df.columns)
    pesos = {"T013": 0.5, "T053": 0.3, "T093": 0.2}
    ini, fim = janela_de_vigencia(2021)
    base = retorno_da_safra(
        SafraCarteira(safra=2021, ano_base=2020, inicio=ini, fim=fim, completa=True,
                      pesos=pesos, universo=tuple(universo), segmentos=3),
        df, selic_por_ano={2021: 0.05, 2022: 0.10}, taxa_selic_aa=0.08)
    rets = oos.retornos_por_ticker(list(pesos), universo, df, 2021)
    assert sum(w * rets[tk] for tk, w in pesos.items()) == pytest.approx(
        base["retorno_estrategia"], abs=1e-12)


def test_primeira_safra_paga_compra_cheia_e_giro_zero_nao_paga():
    pesos = {"AAAA3": 0.5, "BBBB3": 0.5}
    zero = {"AAAA3": 0.0, "BBBB3": 0.0}
    out = oos.simular_custos([{"safra": 2020, "pesos": pesos, "retornos": zero},
                              {"safra": 2021, "pesos": pesos, "retornos": zero}], CFG)
    esperado = sum(custo_compra(tk, 0.5 * oos.CAPITAL_NOCIONAL, CFG) for tk in pesos)
    assert out[0]["custo_pct"] == pytest.approx(esperado / oos.CAPITAL_NOCIONAL)
    assert out[0]["liquido"] < out[0]["bruto"]
    assert out[1]["custo_pct"] == pytest.approx(0.0, abs=1e-12)
    assert out[1]["giro"] == pytest.approx(0.0, abs=1e-12)


def test_troca_total_paga_venda_e_compra():
    out = oos.simular_custos(
        [{"safra": 2020, "pesos": {"AAAA3": 1.0}, "retornos": {"AAAA3": 0.0}},
         {"safra": 2021, "pesos": {"BBBB3": 1.0}, "retornos": {"BBBB3": 0.0}}], CFG)
    assert out[0]["giro"] == pytest.approx(1.0)
    assert out[1]["giro"] == pytest.approx(1.0)
    v1 = oos.CAPITAL_NOCIONAL * (1 + out[0]["liquido"])
    esperado = custo_venda("AAAA3", v1, 0.0, 0.0, CFG)[0] + custo_compra("BBBB3", v1, CFG)
    assert out[1]["custo_pct"] == pytest.approx(esperado / v1, rel=1e-3)


def test_safra_sem_carteira_fica_fora_e_zera_posicoes():
    out = oos.simular_custos(
        [{"safra": 2020, "pesos": {"AAAA3": 1.0}, "retornos": {"AAAA3": 0.1}},
         {"safra": 2021, "pesos": {}},
         {"safra": 2022, "pesos": {"AAAA3": 1.0}, "retornos": {"AAAA3": 0.0}}], CFG)
    assert out[1] == {"safra": 2021, "medida": False}
    assert out[2]["giro"] == pytest.approx(1.0)  # recompra a partir de caixa


def test_ir_compensa_prejuizo_anterior():
    # Safra 1: AAAA3 cai 50%; safra 2: troca (prejuízo realizado, sem IR);
    # BBBB3 sobe 100%; safra 3: troca de novo (ganho), compensado pelo prejuízo.
    seq = [{"safra": 2020, "pesos": {"AAAA3": 1.0}, "retornos": {"AAAA3": -0.5}},
           {"safra": 2021, "pesos": {"BBBB3": 1.0}, "retornos": {"BBBB3": 1.0}},
           {"safra": 2022, "pesos": {"CCCC3": 1.0}, "retornos": {"CCCC3": 0.0}}]
    out = oos.simular_custos(seq, CFG, capital=100_000.0)
    assert out[1]["ir_pct"] == 0.0
    # ganho ~50k, prejuízo ~50k a compensar: IR bem menor que 15% do ganho cheio
    assert out[2]["ir_pct"] < 0.15 * 0.5 * 0.2
    assert out[2]["liquido_com_ir"] <= out[2]["liquido"]


def test_carteira_pequena_fica_isenta_de_ir():
    seq = [{"safra": 2020, "pesos": {"AAAA3": 1.0}, "retornos": {"AAAA3": 1.0}},
           {"safra": 2021, "pesos": {"BBBB3": 1.0}, "retornos": {"BBBB3": 0.0}}]
    out = oos.simular_custos(seq, CFG, capital=5_000.0)
    assert out[1]["ir_pct"] == 0.0  # vendas de ~10k < isenção de 20k/mês


# ── métricas PIT ────────────────────────────────────────────────────────────

def test_metricas_pit_corta_precos_e_lideres_na_safra():
    df = _precos()
    vistos = {}

    def simular(df_seg, lids, pesos, *a, details_out=None, **k):
        vistos.setdefault("max_data", []).append(df_seg.index.max())
        vistos["anos"] = sorted(lids)
        if details_out is not None:
            details_out["monthly_returns"] = [
                {"strategy": 0.01, "equal_weight": 0.0}] * len(df_seg)
        return 120.0, 100.0, 110.0, {"T013": 5.0, "T023": 9.0}

    res = {
        "lids_por_ano": {2019: ["T013"], 2020: ["T023"], 2021: ["T013"]},
        "pesos_por_ano": {2019: {"T013": 1.0}, 2020: {"T023": 1.0}, 2021: {"T013": 1.0}},
        "ic_pairs": [(2020, s, s) for s in range(6)] + [(2021, s, -s) for s in range(6)],
        "_oos_ctx": {"tickers_entrada": ["T013", "T023"], "ano_inicio": 2020,
                     "janela_val_anos": 1, "aporte": 1000.0, "taxa_selic_aa": 0.1,
                     "selic_macro": {2019: 0.05, 2020: 0.02, 2021: 0.09},
                     "roic": {"T013": [(2020, 0.12), (2021, 0.30)]}},
    }
    m = oos.metricas_pit(res, 2021, df, simular=simular)
    assert all(d < pd.Timestamp("2021-04-01") for d in vistos["max_data"])
    assert vistos["anos"] == [2019, 2020]          # 2021 é a própria safra
    assert m["maior"] == "T023"
    assert m["ultimo_lid"] == {"T013": 2019, "T023": 2020}
    assert m["rank_ic_mean"] == pytest.approx(1.0)  # só o IC de 2020 (+1)
    # ROIC de 2021 fica fora; T023 não tem ROIC -> só 0,12 − 0,02
    assert m["roic_spread_mean"] == pytest.approx(0.10)


# ── resumo e leitura ────────────────────────────────────────────────────────

def test_leitura_diz_quando_o_ic_cruza_zero():
    linhas = [{"safra": 2015 + i, "x": v} for i, v in
              enumerate([0.05, -0.04, 0.02, -0.03, 0.01, 0.00])]
    r = oos.resumir(linhas, "x")
    assert r["n_safras"] == 6 and r["safras_negativas"] == [2016, 2018]
    assert r["ic_cruza_zero"]
    assert "NÃO distingue" in oos.leitura_honesta(r)
    assert "insuficiente" in oos.leitura_honesta(oos.resumir(linhas[:1], "x"))


def test_leitura_diz_quanto_a_media_depende_da_melhor_safra():
    linhas = [{"safra": 2017, "x": 0.58}] + [
        {"safra": 2018 + i, "x": v} for i, v in enumerate([0.02, -0.01, 0.03, 0.01])]
    r = oos.resumir(linhas, "x")
    assert r["melhor_safra"] == 2017
    assert r["media_sem_melhor_safra"] == pytest.approx(0.0125)
    assert "Sem a melhor safra (2017)" in oos.leitura_honesta(r)


def test_vencida_por_versao():
    dados = {"versao_metodologia": "2.30.0", "versao_presets": "b3-presets-1.0.0",
             "versao_medicao": oos.VERSAO_MEDICAO}
    assert oos.vencida(dados, "2.30.0", "b3-presets-1.0.0") == []
    assert oos.vencida(dados, "2.31.0", "b3-presets-1.0.0")
    # banda da 1.0.0 (um veto por segmento) não pode ler como a de hoje
    velha = {**dados, "versao_medicao": "oos-carteira-1.0.0"}
    assert any("medição" in m for m in oos.vencida(velha, "2.30.0", "b3-presets-1.0.0"))
    assert oos.vencida(None, "2.30.0", "x") == ["sem medição gravada"]
    assert math.isfinite(oos.CAPITAL_NOCIONAL)


# ── tela ────────────────────────────────────────────────────────────────────

def test_tela_le_a_medicao_e_diz_quando_cruza_o_zero(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    resumo = {"media": 0.04, "ic95": [-0.01, 0.10], "n_safras": 9,
              "safras_negativas": [2018], "ic_cruza_zero": True}
    var = {"vs_equal_weight": resumo, "vs_equal_weight_bruto": resumo,
           "vs_equal_weight_com_ir": resumo, "vs_selic": resumo,
           "vs_ibovespa": resumo, "giro_medio": 0.6, "custo_medio": 0.002,
           "safras_inviaveis_no_cap": [2019], "safras_com_revisao": [],
           "safras_sem_carteira": [], "safras": [
               {"safra": 2018, "segmentos_aprovados": 4, "segmentos_avaliados": 9,
                "n_ativos": 6, "liquido": 0.1, "ew": 0.12, "selic": 0.06,
                "ibov": 0.08, "excesso_ew": -0.02, "maiores": "AAAA3 25%"}]}
    caminho = tmp_path / "oos.json"
    caminho.write_text(__import__("json").dumps({
        "versao_metodologia": "0.0.1", "versao_presets": "x", "janela": "abril/2016",
        "medido_em": "2026-10-04", "portao_llm": oos.PORTAO_LLM,
        "fora_do_pit": list(oos.FORA_DO_PIT),
        "perfis": {"Equilibrado (recomendado)": {"variantes": {v: var for v in oos.VARIANTES}}},
    }), encoding="utf-8")
    monkeypatch.setattr(oos, "CAMINHO_MEDICAO", caminho)

    def _app():
        from views.portfolio_b3_oos_carteira import render_oos_carteira
        render_oos_carteira()

    at = AppTest.from_function(_app).run(timeout=60)
    assert not at.exception
    avisos = " ".join(w.value for w in at.warning)
    assert "VENCIDA" in avisos and "NÃO distingue" in avisos
