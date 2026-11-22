"""Sinais da data da decisão contra os piores nomes da carteira B3."""
from __future__ import annotations

import pandas as pd
import pytest

import core.b3_oos_carteira as oos
import core.b3_sinais_piores as sp

TETOS = {"cap": 1.0, "teto_setor": 1.0, "teto_ciclico": 1.0}


def _seg(setor, sel, pesos, ranking):
    return (setor, list(sel), dict(pesos), [(tk, float(10 - k)) for k, tk in enumerate(ranking)])


def _carteira():
    """Quatro segmentos de um nome; L3 é o pior realizado."""
    segs = [_seg("S1", ["L1"], {"L1": 1.0}, ["L1", "R1"]),
            _seg("S2", ["L2"], {"L2": 1.0}, ["L2", "R2"]),
            _seg("S3", ["L3"], {"L3": 1.0}, ["L3", "R3"]),
            _seg("S4", ["L4"], {"L4": 1.0}, ["L4", "R4"])]
    rets = {"L1": 0.40, "L2": 0.10, "L3": -0.30, "L4": 0.00,
            "R1": 0.00, "R2": 0.05, "R3": 0.20, "R4": 0.00}
    return segs, rets


def _sinais(**por_sinal):
    """{sinal: {tk: valor}} -> {tk: {sinal: valor}}, com os demais None."""
    tks = {tk for valores in por_sinal.values() for tk in valores}
    return {tk: {n: por_sinal.get(n, {}).get(tk) for n in sp.NOMES_SINAIS} for tk in tks}


def _mensal(inicio, valores):
    idx = pd.date_range(inicio, periods=len(valores), freq="ME")
    return pd.Series(valores, index=idx, dtype=float)


# ── direções registradas ────────────────────────────────────────────────────

def test_todo_sinal_tem_direcao_registrada_e_nome_unico():
    assert len(set(sp.NOMES_SINAIS)) == len(sp.SINAIS)
    assert all(s.direcao in (-1, +1) for s in sp.SINAIS)
    assert "37%" in sp.AVISO_COMPARACOES  # 1 - 0,95^9


# ── sinais de preço: só o passado ───────────────────────────────────────────

def test_sinais_de_preco_ignoram_tudo_a_partir_da_decisao():
    decisao = pd.Timestamp(2020, 4, 1)
    antes = _mensal("2018-01-31", [10 + i for i in range(27)])  # até 31/03/2020
    depois_a = _mensal("2020-04-30", [1.0] * 12)
    depois_b = _mensal("2020-04-30", [999.0] * 12)
    sa = sp.sinais_de_preco(pd.concat([antes, depois_a]), decisao)
    sb = sp.sinais_de_preco(pd.concat([antes, depois_b]), decisao)
    assert sa == sb
    # 31/03/2020 = 36; 31/03/2019 = 24; 30/09/2019 = 30
    assert sa["momento_12m"] == pytest.approx(36 / 24 - 1)
    assert sa["momento_6m"] == pytest.approx(36 / 30 - 1)
    assert sa["queda_do_topo_12m"] == pytest.approx(0.0)
    assert sa["volatilidade_12m"] is not None


def test_sinais_de_preco_sem_cotacao_recente_sao_nulos():
    decisao = pd.Timestamp(2020, 4, 1)
    velha = _mensal("2017-01-31", [10.0] * 24)  # para em dez/2018
    assert all(v is None for v in sp.sinais_de_preco(velha, decisao).values())
    assert all(v is None for v in sp.sinais_de_preco(None, decisao).values())


def test_queda_do_topo_mede_o_fundo_dos_12_meses():
    decisao = pd.Timestamp(2020, 4, 1)
    valores = [10.0] * 14 + [20.0] + [10.0] * 12  # topo em 2019, volta a 10
    s = sp.sinais_de_preco(_mensal("2018-01-31", valores), decisao)
    assert s["queda_do_topo_12m"] == pytest.approx(10 / 20 - 1)


# ── balanço e ranking ───────────────────────────────────────────────────────

def test_balanco_ausente_fica_nulo_nunca_zero():
    s = sp.sinais_de_balanco({"ROE": float("nan"), "P/VP": 2.0,
                              "Patrimonio_Negativo": 1, "FCO_Negativo": 0})
    assert s == {"roe": None, "endividamento": None, "p_vp": 2.0, "alertas_balanco": 1.0}
    assert all(v is None for v in sp.sinais_de_balanco(None).values())


def test_folga_do_score_usa_o_substituto_que_herdaria_a_vaga():
    segs = [_seg("S", ["A", "B"], {"A": 0.5, "B": 0.5}, ["A", "B", "C", "D"])]
    # scores 10, 9, 8, 7; substituto = C (8)
    assert sp.folga_do_score("A", segs) == pytest.approx(2.0)
    assert sp.folga_do_score("B", segs) == pytest.approx(1.0)
    # guarda barra C: o substituto passa a ser D
    assert sp.folga_do_score("B", segs, exclui=lambda tk: tk == "C") == pytest.approx(2.0)
    assert sp.folga_do_score("Z", segs) is None


def test_sinais_da_carteira_casa_o_retrato_sem_sufixo():
    segs, _ = _carteira()
    df_pit = pd.DataFrame([{"Ticker": "L1", "ROE": 0.2, "P/VP": 1.0}])
    precos = pd.DataFrame({"L1": _mensal("2018-01-31", [10.0 + i for i in range(27)])})
    s = sp.sinais_da_carteira(["L1", "L2"], segs, precos, df_pit, 2020)
    assert s["L1"]["roe"] == 0.2 and s["L1"]["momento_12m"] is not None
    assert s["L2"]["roe"] is None and s["L2"]["momento_12m"] is None
    assert s["L1"]["folga_do_score"] == pytest.approx(1.0)


# ── quem o sinal aponta ─────────────────────────────────────────────────────

def test_mais_suspeito_segue_a_direcao_e_ignora_nulo():
    sinais = _sinais(momento_12m={"A": 0.5, "B": -0.2, "C": None},
                     volatilidade_12m={"A": 0.3, "B": 0.1, "C": 0.9})
    assert sp.mais_suspeito(["A", "B", "C"], sinais, "momento_12m") == "B"
    assert sp.mais_suspeito(["A", "B", "C"], sinais, "volatilidade_12m") == "C"
    assert sp.mais_suspeito(["A", "B", "C"], sinais, "roe") is None


def test_empate_no_extremo_nao_veta():
    ninguem = _sinais(alertas_balanco={"A": 0.0, "B": 0.0, "C": 0.0})
    assert sp.mais_suspeito(["A", "B", "C"], ninguem, "alertas_balanco") is None
    dois = _sinais(alertas_balanco={"A": 1.0, "B": 1.0, "C": 0.0})
    assert sp.mais_suspeito(["A", "B", "C"], dois, "alertas_balanco") is None
    um = _sinais(alertas_balanco={"A": 0.0, "B": 2.0, "C": 1.0})
    assert sp.mais_suspeito(["A", "B", "C"], um, "alertas_balanco") == "B"
    so_um = _sinais(alertas_balanco={"A": 0.0})
    assert sp.mais_suspeito(["A"], so_um, "alertas_balanco") == "A"


def test_ic_de_posto_negativo_quando_o_sinal_aponta_quem_perdeu():
    nomes = ["A", "B", "C", "D"]
    rets = {"A": 0.3, "B": 0.1, "C": -0.1, "D": -0.4}
    bom = _sinais(momento_12m={"A": 0.4, "B": 0.2, "C": 0.0, "D": -0.3})
    assert sp.ic_de_posto(nomes, bom, rets, "momento_12m") == pytest.approx(-1.0)
    poucos = _sinais(momento_12m={"A": 0.4, "B": 0.2})
    assert sp.ic_de_posto(nomes, poucos, rets, "momento_12m") is None


# ── a safra ─────────────────────────────────────────────────────────────────

def test_medir_safra_bate_com_a_banda_e_com_o_veto_manual():
    segs, rets = _carteira()
    sinais = _sinais(momento_12m={"L1": 0.5, "L2": 0.2, "L3": -0.4, "L4": 0.1},
                     volatilidade_12m={"L1": 0.9, "L2": 0.1, "L3": 0.2, "L4": 0.3})
    linha = sp.medir_safra(2020, segs, rets, sinais, **TETOS)
    sem = sum(0.25 * rets[f"L{i}"] for i in range(1, 5))
    assert linha["rendimento_sem_portao"] == pytest.approx(sem)
    assert linha["pior_realizado"] == "L3"

    # momento aponta L3: é o veto favorável da banda
    m = linha["sinais"]["momento_12m"]
    fav = oos.escolher_veto(segs, rets, veta="pior", **TETOS)
    assert m["vetado"] == "L3" and m["acertou_o_pior"]
    assert m["excesso"] == pytest.approx(fav["efetivo"] - sem)
    assert linha["oraculo_pior"] == pytest.approx(m["excesso"])

    # volatilidade aponta L1 (o que mais rendeu): é o adversário
    v = linha["sinais"]["volatilidade_12m"]
    adv = oos.escolher_veto(segs, rets, veta="melhor", **TETOS)
    assert v["vetado"] == "L1" and not v["acertou_o_pior"]
    assert v["excesso"] == pytest.approx(adv["efetivo"] - sem)

    # ao acaso = média dos quatro vetos possíveis
    manual = [0.25 * (rets[f"R{i}"] - rets[f"L{i}"]) for i in range(1, 5)]
    assert linha["veto_ao_acaso"] == pytest.approx(sum(manual) / 4)
    assert m["sobre_acaso"] == pytest.approx(m["excesso"] - linha["veto_ao_acaso"])

    # sinal sem valor não veta: excesso zero
    assert linha["sinais"]["roe"]["vetado"] is None
    assert linha["sinais"]["roe"]["excesso"] == 0.0


def test_veto_que_deixa_a_carteira_sem_salvar_rende_o_caixa():
    # cap de 30%: quatro nomes fecham; vetar L1 sem substituto deixa três
    segs = [_seg("S1", ["L1"], {"L1": 1.0}, ["L1"]),
            _seg("S2", ["L2"], {"L2": 1.0}, ["L2"]),
            _seg("S3", ["L3"], {"L3": 1.0}, ["L3"]),
            _seg("S4", ["L4"], {"L4": 1.0}, ["L4"])]
    rets = {"L1": -0.5, "L2": 0.1, "L3": 0.1, "L4": 0.1}
    sinais = _sinais(momento_12m={"L1": -1.0, "L2": 0.0, "L3": 0.0, "L4": 0.0})
    linha = sp.medir_safra(2020, segs, rets, sinais, cap=0.3, teto_setor=1.0,
                           teto_ciclico=1.0, caixa=0.05)
    sem = 0.25 * (-0.5 + 0.3)
    assert linha["rendimento_sem_portao"] == pytest.approx(sem)
    assert linha["sinais"]["momento_12m"]["excesso"] == pytest.approx(0.05 - sem)


def test_safra_sem_carteira_salvavel_fica_de_fora():
    segs = [_seg("S1", ["L1"], {"L1": 1.0}, ["L1"])]
    assert sp.medir_safra(2020, segs, {"L1": 0.1}, {}, cap=0.3, teto_setor=1.0,
                          teto_ciclico=1.0, caixa=0.05) is None


def test_resumo_conta_acertos_contra_o_acaso_e_le_sobre_o_acaso():
    segs, rets = _carteira()
    sinais = _sinais(momento_12m={"L1": 0.5, "L2": 0.2, "L3": -0.4, "L4": 0.1})
    linhas = [sp.medir_safra(s, segs, rets, sinais, **TETOS) for s in (2018, 2019, 2020)]
    res = sp.resumir_sinais(linhas + [None])
    m = res["sinais"]["momento_12m"]
    assert res["n_safras"] == 3
    assert m["acertos"] == 3 and m["acertos_ao_acaso"] == pytest.approx(0.75)
    assert m["fracao_do_oraculo"] == pytest.approx(1.0)
    assert "vetar um nome ao acaso" in m["leitura"]
    assert m["veto"]["n_safras"] == 3
    assert res["aviso"] == sp.AVISO_COMPARACOES


def test_volatilidade_conta_os_12_retornos_inclusive_o_de_abril():
    decisao = pd.Timestamp(2020, 4, 1)
    # queda forte em abril/2019 e depois tudo parado: só o retorno de abril varia
    valores = [10.0] * 15 + [5.0] * 12  # jan/2018..mar/2019 = 10; abr/2019.. = 5
    s = sp.sinais_de_preco(_mensal("2018-01-31", valores), decisao)
    rets = pd.Series([-0.5] + [0.0] * 11)
    assert s["volatilidade_12m"] == pytest.approx(float(rets.std(ddof=1)))


def test_empate_no_pior_realizado_conta_qualquer_nome_do_empate():
    segs, rets = _carteira()
    rets = dict(rets, L2=-0.30)  # L2 empata com L3 no piso
    sinais = _sinais(momento_12m={"L1": 0.5, "L2": -0.4, "L3": 0.2, "L4": 0.1})
    linhas = [sp.medir_safra(2020, segs, rets, sinais, **TETOS)]
    assert linhas[0]["piores_realizados"] == ["L2", "L3"]
    assert linhas[0]["sinais"]["momento_12m"]["acertou_o_pior"]
    res = sp.resumir_sinais(linhas)
    assert res["sinais"]["momento_12m"]["acertos_ao_acaso"] == pytest.approx(0.5)
