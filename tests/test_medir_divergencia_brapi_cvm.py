# -*- coding: utf-8 -*-
"""Regras de classificação da medição brapi x CVM (só leitura, sem banco)."""
import pandas as pd
import pytest

from scripts import medir_divergencia_brapi_cvm as m


@pytest.mark.parametrize("b, c, esperado", [
    (100.0, 100.0, "concorda"),
    (101.0, 100.0, "concorda"),
    (105.0, 100.0, "moderada"),
    (150.0, 100.0, "forte"),
    (0.0, 5e9, "forte"),            # zero é valor, não ausência
    (-10.0, 10.0, "sinal_trocado"),
    (100e3, 100.0, "escala"),       # brapi em reais, CVM em milhares
    (100.0, 100e6, "escala"),
    (None, 100.0, "so_cvm"),
    (100.0, None, "so_brapi"),
    (None, None, "nenhum"),
    (float("nan"), 100.0, "so_cvm"),
])
def test_classificar(b, c, esperado):
    assert m.classificar(b, c)[0] == esperado


def _uni(*linhas):
    return pd.DataFrame([{"ticker": t, "asset_type": a, "cd_cvm": cd, "nome": n}
                         for t, a, cd, n in linhas])


def _brapi(uni, linhas):
    return pd.DataFrame(linhas).merge(uni, on="ticker", how="inner")


def test_medir_conta_lucro_e_pl_uma_vez_por_empresa_e_lpa_por_ticker():
    uni = _uni(("AAAA3", "stock", 1, "A"), ("AAAA4", "stock", 1, "A"),
               ("AAAA11", "unit", 1, "A"))
    brapi = _brapi(uni, [
        {"ticker": "AAAA3", "ano": 2025, "net_income": 100.0, "equity": 1000.0, "eps": 1.0},
        {"ticker": "AAAA4", "ano": 2025, "net_income": 100.0, "equity": 1000.0, "eps": 1.5},
        {"ticker": "AAAA11", "ano": 2025, "net_income": 100.0, "equity": 1000.0, "eps": 9.0},
    ])
    cvm = {(1, 2025): {"lucro": 100.0, "pl": 1500.0, "consolidado": True,
                       "lpa_por_classe": {"3": 1.0, "4": 1.1}}}
    df = m.medir(uni, brapi, cvm, [2025])
    assert len(df[df["campo"] == "lucro"]) == 1
    assert df.loc[df["campo"] == "pl", "categoria"].tolist() == ["forte"]
    lpa = df[df["campo"] == "lpa"].set_index("ticker")["categoria"].to_dict()
    assert lpa == {"AAAA3": "concorda", "AAAA4": "forte"}   # unit fica fora


def test_exercicio_sem_arquivo_da_dfp_fica_fora_da_medicao():
    """2025 sem arquivo: as linhas da brapi de 2025 não viram "so_brapi"."""
    uni = _uni(("AAAA3", "stock", 1, "A"))
    brapi = _brapi(uni, [
        {"ticker": "AAAA3", "ano": 2024, "net_income": 100.0, "equity": 1000.0, "eps": 1.0},
        {"ticker": "AAAA3", "ano": 2025, "net_income": 120.0, "equity": 1100.0, "eps": 1.2},
    ])
    cvm = {(1, 2024): {"lucro": 100.0, "pl": 1000.0, "consolidado": True,
                       "lpa_por_classe": {"3": 1.0}}}
    df = m.medir(uni, brapi, cvm, [2024])
    assert set(df["ano"]) == {2024}
    assert set(df["categoria"]) == {"concorda"}
    assert "Exercício 2024: 0 de 1 empresas" in m.resumo(df)


def test_ler_cvm_devolve_so_os_exercicios_com_arquivo(tmp_path, monkeypatch):
    import data_pipeline.market.b3_saidas as bs
    (tmp_path / "dfp_cia_aberta_2024.zip").write_bytes(b"x")
    monkeypatch.setattr(bs, "ler_dfp", lambda _b, _a, _c: {})
    out, lidos = m._ler_cvm(tmp_path, [2024, 2025], {1})
    assert lidos == [2024] and out == {}


def test_empresa_ano_sem_brapi_conta_como_so_cvm():
    uni = _uni(("AAAA3", "stock", 1, "A"), ("BBBB3", "stock", 2, "B"))
    brapi = _brapi(uni, [
        {"ticker": "AAAA3", "ano": 2025, "net_income": 100.0, "equity": 1000.0, "eps": 1.0},
    ])
    cvm = {(1, 2024): {"lucro": 90.0, "pl": 900.0, "consolidado": True,
                       "lpa_por_classe": {"3": 0.9}},
           (1, 2025): {"lucro": 100.0, "pl": 1000.0, "consolidado": True,
                       "lpa_por_classe": {"3": 1.0}},
           (2, 2025): {"lucro": 50.0, "pl": 500.0, "consolidado": False,
                       "lpa_por_classe": {"3": 0.5}}}
    df = m.medir(uni, brapi, cvm, [2024, 2025])
    so_cvm = df[df["categoria"] == "so_cvm"]
    assert set(zip(so_cvm["cd_cvm"], so_cvm["ano"], so_cvm["campo"])) == {
        (1, 2024, "lucro"), (1, 2024, "pl"), (1, 2024, "lpa"),
        (2, 2025, "lucro"), (2, 2025, "pl"), (2, 2025, "lpa")}


def test_lucro_e_pl_vem_cada_um_do_ticker_que_o_tem():
    uni = _uni(("AAAA3", "stock", 1, "A"), ("AAAA4", "stock", 1, "A"))
    brapi = _brapi(uni, [
        {"ticker": "AAAA3", "ano": 2025, "net_income": 100.0, "equity": None, "eps": None},
        {"ticker": "AAAA4", "ano": 2025, "net_income": None, "equity": 1000.0, "eps": None},
    ])
    cvm = {(1, 2025): {"lucro": 100.0, "pl": 1000.0, "consolidado": True,
                       "lpa_por_classe": {}}}
    df = m.medir(uni, brapi, cvm, [2025]).set_index("campo")
    assert df.loc["lucro", "categoria"] == "concorda" and df.loc["lucro", "ticker"] == "AAAA3"
    assert df.loc["pl", "categoria"] == "concorda" and df.loc["pl", "ticker"] == "AAAA4"


def test_lpa_zero_da_cvm_e_valor_na_medicao_e_ausencia_no_valuation():
    from data_pipeline.market.b3_saidas import lpa_da_classe
    contas = {"3.99.01.01": ("ON", 0.0), "3.99.01.02": ("PN", 0.7)}
    assert lpa_da_classe(contas, "3") is None                 # valuation: não divide por zero
    assert lpa_da_classe(contas, "3", manter_zero=True) == 0.0
    assert lpa_da_classe(contas, "4", manter_zero=True) == 0.7
    assert m.classificar(1.0, lpa_da_classe(contas, "3", manter_zero=True))[0] == "forte"


def test_resumo_conta_empresas_com_divergencia_no_ultimo_ano():
    df = pd.DataFrame([
        {"campo": "lucro", "ano": 2025, "cd_cvm": 1, "ticker": "AAAA3", "categoria": "forte",
         "brapi": 1.0, "cvm": 2.0, "divergencia": 0.5},
        {"campo": "lucro", "ano": 2025, "cd_cvm": 2, "ticker": "BBBB3", "categoria": "concorda",
         "brapi": 1.0, "cvm": 1.0, "divergencia": 0.0},
        {"campo": "lucro", "ano": 2025, "cd_cvm": 3, "ticker": "CCCC3", "categoria": "so_brapi",
         "brapi": 1.0, "cvm": None, "divergencia": None},
    ])
    texto = m.resumo(df)
    assert "1 de 2 empresas comparáveis (50.0%)" in texto
    assert "AAAA3" in texto
