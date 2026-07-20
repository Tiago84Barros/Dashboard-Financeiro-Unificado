"""Diversificação por correlação na Criação de Portfólio B3.

A seleção por segmento é decidida sem olhar para os outros segmentos —
produz "diversificação nominal": segmentos distintos que, na prática,
compartilham o mesmo fator de risco. Estes testes fixam a regra de
substituição (_aplicar_diversificacao_correlacao): troca o mais fraco de um
par muito correlacionado pelo próximo do ranking DO MESMO segmento, só
quando isso reduz a correlação média — nunca troca entre segmentos.
"""
import numpy as np
import pandas as pd
import pytest

from views.portfolio_b3 import _aplicar_diversificacao_correlacao


def _item(tk, score, setor="Materiais", subsetor="Mineração", segmento="Mineração", peso=0.2):
    return {
        "tk": tk, "score": score, "peso": peso, "setor": setor,
        "subsetor": subsetor, "segmento": segmento, "motivos": [],
    }


def _returns_com_fator_comum(tickers_correlacionados, tickers_independentes, n=36, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2021-01-31", periods=n, freq="ME")
    fator = rng.normal(0, 0.05, n)
    data = {}
    for tk in tickers_correlacionados:
        data[tk] = fator + rng.normal(0, 0.005, n)
    for tk in tickers_independentes:
        data[tk] = rng.normal(0, 0.05, n)
    return pd.DataFrame(data, index=idx)


def test_sem_par_acima_do_limiar_nao_altera_nada():
    items = [
        _item("BRAP3", 0.80, segmento="Mineração"),
        _item("WEGE3", 0.75, segmento="Bens de Capital"),
    ]
    aprovados = [
        {"setor": "Materiais", "subsetor": "Mineração", "segmento": "Mineração",
         "score_proximo": {"BRAP3": 0.80, "VALE3": 0.70}},
        {"setor": "Industrial", "subsetor": "Máquinas", "segmento": "Bens de Capital",
         "score_proximo": {"WEGE3": 0.75}},
    ]
    returns = _returns_com_fator_comum([], ["BRAP3", "WEGE3", "VALE3"])

    result, log = _aplicar_diversificacao_correlacao(
        items, aprovados, returns, entry_guard={}, threshold=0.65,
    )

    assert [it["tk"] for it in result] == ["BRAP3", "WEGE3"]
    assert log == []


def test_substitui_o_mais_fraco_por_candidato_do_mesmo_segmento():
    # BRAP3 e UNIP6 correlacionados (mesmo fator commodities); LEVE3 independente.
    items = [
        _item("BRAP3", 0.80, setor="Materiais", subsetor="Mineração", segmento="Mineração"),
        _item("UNIP6", 0.60, setor="Materiais", subsetor="Químicos", segmento="Petroquímica"),
    ]
    aprovados = [
        {"setor": "Materiais", "subsetor": "Mineração", "segmento": "Mineração",
         "score_proximo": {"BRAP3": 0.80}},
        {"setor": "Materiais", "subsetor": "Químicos", "segmento": "Petroquímica",
         "score_proximo": {"UNIP6": 0.60, "BRASKEM6": 0.55}},
    ]
    correlacionadas = ["BRAP3", "UNIP6"]
    independentes = ["BRASKEM6"]
    returns = _returns_com_fator_comum(correlacionadas, independentes)

    result, log = _aplicar_diversificacao_correlacao(
        items, aprovados, returns, entry_guard={}, threshold=0.5,
    )

    tickers = {it["tk"] for it in result}
    assert "BRAP3" in tickers          # o mais forte do par nunca é trocado
    assert "UNIP6" not in tickers      # o mais fraco saiu
    assert "BRASKEM6" in tickers       # substituto do MESMO segmento entrou
    assert len(log) == 1
    assert log[0] == {
        "sai": "UNIP6", "entra": "BRASKEM6", "rho": pytest.approx(log[0]["rho"]),
        "segmento": "Materiais › Petroquímica",
    }
    entrou = next(it for it in result if it["tk"] == "BRASKEM6")
    assert any("diversificação" in m for m in entrou["motivos"])


def test_sem_substituto_disponivel_mantem_selecao():
    """Segmento sem 2o candidato: correlação alta persiste, mas nada quebra."""
    items = [
        _item("BRAP3", 0.80, setor="Materiais", subsetor="Mineração", segmento="Mineração"),
        _item("UNIP6", 0.60, setor="Materiais", subsetor="Químicos", segmento="Petroquímica"),
    ]
    aprovados = [
        {"setor": "Materiais", "subsetor": "Mineração", "segmento": "Mineração",
         "score_proximo": {"BRAP3": 0.80}},
        {"setor": "Materiais", "subsetor": "Químicos", "segmento": "Petroquímica",
         "score_proximo": {"UNIP6": 0.60}},  # único nome do segmento — sem substituto
    ]
    returns = _returns_com_fator_comum(["BRAP3", "UNIP6"], [])

    result, log = _aplicar_diversificacao_correlacao(
        items, aprovados, returns, entry_guard={}, threshold=0.5,
    )

    assert {it["tk"] for it in result} == {"BRAP3", "UNIP6"}
    assert log == []


def test_entry_guard_exclui_candidato_ilegivel():
    """Substituto reprovado no Score de Entrada não é usado — próximo da fila não existe aqui."""
    items = [
        _item("BRAP3", 0.80, setor="Materiais", subsetor="Mineração", segmento="Mineração"),
        _item("UNIP6", 0.60, setor="Materiais", subsetor="Químicos", segmento="Petroquímica"),
    ]
    aprovados = [
        {"setor": "Materiais", "subsetor": "Mineração", "segmento": "Mineração",
         "score_proximo": {"BRAP3": 0.80}},
        {"setor": "Materiais", "subsetor": "Químicos", "segmento": "Petroquímica",
         "score_proximo": {"UNIP6": 0.60, "BRASKEM6": 0.55}},
    ]
    returns = _returns_com_fator_comum(["BRAP3", "UNIP6"], ["BRASKEM6"])
    entry_guard = {"BRASKEM6": {"status_entrada": "excluido", "score_entrada": 0.0}}

    result, log = _aplicar_diversificacao_correlacao(
        items, aprovados, returns, entry_guard=entry_guard, threshold=0.5,
    )

    assert {it["tk"] for it in result} == {"BRAP3", "UNIP6"}
    assert log == []


def test_nunca_troca_entre_segmentos_distintos():
    """O substituto só pode vir do ranking do MESMO segmento do ativo trocado."""
    items = [
        _item("BRAP3", 0.80, setor="Materiais", subsetor="Mineração", segmento="Mineração"),
        _item("UNIP6", 0.60, setor="Materiais", subsetor="Químicos", segmento="Petroquímica"),
    ]
    aprovados = [
        {"setor": "Materiais", "subsetor": "Mineração", "segmento": "Mineração",
         "score_proximo": {"BRAP3": 0.80, "CSNA3": 0.78}},  # candidato de OUTRO segmento
        {"setor": "Materiais", "subsetor": "Químicos", "segmento": "Petroquímica",
         "score_proximo": {"UNIP6": 0.60}},
    ]
    returns = _returns_com_fator_comum(["BRAP3", "UNIP6"], ["CSNA3"])

    result, log = _aplicar_diversificacao_correlacao(
        items, aprovados, returns, entry_guard={}, threshold=0.5,
    )

    # CSNA3 pertence ao segmento de BRAP3 (o mais FORTE do par) — não é
    # candidato válido para substituir UNIP6 (do segmento de Petroquímica).
    assert "CSNA3" not in {it["tk"] for it in result}
    assert log == []


def test_respeita_limite_de_substituicoes():
    """max_substituicoes limita o número de trocas por rodada, mesmo com vários pares correlacionados."""
    items = [
        _item("A1", 0.90, setor="S1", subsetor="Sub1", segmento="Seg1"),
        _item("A2", 0.60, setor="S2", subsetor="Sub2", segmento="Seg2"),
        _item("A3", 0.55, setor="S3", subsetor="Sub3", segmento="Seg3"),
    ]
    aprovados = [
        {"setor": "S1", "subsetor": "Sub1", "segmento": "Seg1", "score_proximo": {"A1": 0.90}},
        {"setor": "S2", "subsetor": "Sub2", "segmento": "Seg2",
         "score_proximo": {"A2": 0.60, "B2": 0.58}},
        {"setor": "S3", "subsetor": "Sub3", "segmento": "Seg3",
         "score_proximo": {"A3": 0.55, "B3": 0.50}},
    ]
    returns = _returns_com_fator_comum(["A1", "A2", "A3"], ["B2", "B3"])

    result, log = _aplicar_diversificacao_correlacao(
        items, aprovados, returns, entry_guard={}, threshold=0.5, max_substituicoes=1,
    )

    assert len(log) <= 1
