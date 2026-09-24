"""Orçamento por categoria (sem limite inventado) e conciliação manual × extrato."""
from __future__ import annotations

from datetime import date

from core import orcamento as orc
from core.financeiro import calcular_saude_score

SET = date(2026, 9, 1)


def _r(cat, limite, mes):
    return {"categoria": cat, "limite": limite, "mes": mes}


# ── Vigência ──────────────────────────────────────────────────────────────────


def test_limite_vale_do_mes_gravado_em_diante():
    regs = [_r("Mercado", 1500, date(2026, 6, 1)), _r("Lazer", 400, date(2026, 8, 1))]
    assert orc.vigentes(regs, SET) == {"Mercado": 1500.0, "Lazer": 400.0}
    # Antes de gravar, não havia orçamento.
    assert orc.vigentes(regs, date(2026, 5, 1)) == {}


def test_o_ultimo_gravado_vence_e_mes_futuro_ainda_nao_vale():
    regs = [
        _r("Mercado", 1500, date(2026, 6, 1)),
        _r("Mercado", 1800, date(2026, 8, 1)),
        _r("Mercado", 2000, date(2026, 10, 1)),
    ]
    assert orc.vigentes(regs, SET) == {"Mercado": 1800.0}


def test_zero_encerra_o_orcamento_daqui_para_a_frente():
    regs = [_r("Lazer", 400, date(2026, 6, 1)), _r("Lazer", 0, date(2026, 8, 1))]
    assert orc.vigentes(regs, SET) == {}
    assert orc.vigentes(regs, date(2026, 7, 1)) == {"Lazer": 400.0}


# ── Consumo ───────────────────────────────────────────────────────────────────


def test_categoria_sem_limite_nao_ganha_limite_implicito():
    linha = orc.linha_categoria("Mercado", 1000.0, None)
    assert linha["orcamento"] is None and linha["pct_usado"] is None
    assert linha["tipo_badge"] == "neutro"


def test_badge_segue_os_limiares():
    assert orc.linha_categoria("a", 95, 100)["tipo_badge"] == "erro"
    assert orc.linha_categoria("a", 80, 100)["tipo_badge"] == "alerta"
    assert orc.linha_categoria("a", 10, 100)["tipo_badge"] == "sucesso"


def test_consumo_mantem_categoria_orcada_sem_gasto():
    linhas = orc.consumo({"Mercado": 900.0}, {"Mercado": 1000.0, "Lazer": 300.0})
    assert [(c["nome"], c["gasto"], c["pct_usado"]) for c in linhas] == [
        ("Mercado", 900.0, 90.0), ("Lazer", 0.0, 0.0)]


def test_resumo_mede_cobertura_do_gasto():
    linhas = orc.consumo({"Mercado": 950.0, "Lazer": 50.0, "Saúde": 1000.0},
                         {"Mercado": 1000.0, "Lazer": 300.0})
    r = orc.resumo(linhas)
    assert r["categorias_orcadas"] == 2
    assert r["categorias_estouradas"] == 1
    assert r["limite_total"] == 1300.0
    assert r["gasto_orcado"] == 1000.0
    assert r["cobertura_pct"] == 50.0


def test_resumo_sem_gasto_nao_divide_por_zero():
    assert orc.resumo([])["cobertura_pct"] is None


# ── Nota de saúde ─────────────────────────────────────────────────────────────


def test_sem_orcamento_a_nota_e_reescalada_e_nao_ganha_20_de_graca():
    # 30% de poupança (40) + 6 meses (30) + rentabilidade (10) = 80 de 80.
    assert calcular_saude_score(30, 6, 0, 0, True) == 100
    # 20 + 15 = 35 de 80 -> 44; o antigo dava 55 (35 + 20 grátis).
    assert calcular_saude_score(15, 3, 0, 0, False) == 44


def test_com_orcamento_estourar_custa_pontos():
    todas_ok = calcular_saude_score(30, 6, 0, 4, True)
    metade = calcular_saude_score(30, 6, 2, 4, True)
    assert todas_ok == 100 and metade == 90


# ── Conciliação ───────────────────────────────────────────────────────────────


def _tx(i, valor, d, source="manual", conta="Nubank", fluxo="expense", tipo=""):
    return {"id": i, "valor": valor, "data": d, "source": source, "conta": conta,
            "tipo_fluxo": fluxo, "account_type": tipo}


def test_manual_e_extrato_do_mesmo_gasto_formam_par():
    pares = orc.duplicatas_provaveis([
        _tx("m1", -120.0, date(2026, 9, 10)),
        _tx("i1", -120.0, date(2026, 9, 12), source="import"),
    ])
    assert [(p["manual"]["id"], p["importado"]["id"], p["dias"]) for p in pares] == [
        ("m1", "i1", 2)]
    assert pares[0]["valor"] == 120.0


def test_fora_da_tolerancia_outra_conta_ou_outro_fluxo_nao_casam():
    base = _tx("m1", -120.0, date(2026, 9, 10))
    assert orc.duplicatas_provaveis(
        [base, _tx("i1", -120.0, date(2026, 9, 14), source="import")]) == []
    assert orc.duplicatas_provaveis(
        [base, _tx("i1", -120.0, date(2026, 9, 10), source="import", conta="Itaú")]) == []
    assert orc.duplicatas_provaveis(
        [base, _tx("i1", 120.0, date(2026, 9, 10), source="import", fluxo="income")]) == []
    assert orc.duplicatas_provaveis(
        [base, _tx("i1", -120.01, date(2026, 9, 10), source="import")]) == []


def test_casamento_e_um_para_um_pela_data_mais_proxima():
    pares = orc.duplicatas_provaveis([
        _tx("m1", -12.0, date(2026, 9, 10)),
        _tx("m2", -12.0, date(2026, 9, 12)),
        _tx("i1", -12.0, date(2026, 9, 12), source="import"),
    ])
    # Só um extrato: só um par, e com o manual do mesmo dia.
    assert [(p["manual"]["id"], p["importado"]["id"]) for p in pares] == [("m2", "i1")]


def test_dois_manuais_sem_extrato_nao_sao_duplicata():
    assert orc.duplicatas_provaveis([
        _tx("m1", -12.0, date(2026, 9, 10)),
        _tx("m2", -12.0, date(2026, 9, 10)),
    ]) == []


def test_fatura_csv_do_cartao_fica_fora():
    assert orc.duplicatas_provaveis([
        _tx("m1", -80.0, date(2026, 9, 10), conta="Cartão"),
        _tx("c1", -80.0, date(2026, 9, 10), source="csv", conta="Cartão",
            tipo="credit_card"),
    ]) == []
