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


def test_medir_conta_lucro_e_pl_uma_vez_por_empresa_e_lpa_por_ticker():
    brapi = pd.DataFrame([
        {"ticker": "AAAA3", "asset_type": "stock", "cd_cvm": 1, "nome": "A", "ano": 2025,
         "net_income": 100.0, "equity": 1000.0, "eps": 1.0},
        {"ticker": "AAAA4", "asset_type": "stock", "cd_cvm": 1, "nome": "A", "ano": 2025,
         "net_income": 100.0, "equity": 1000.0, "eps": 1.5},
        {"ticker": "AAAA11", "asset_type": "unit", "cd_cvm": 1, "nome": "A", "ano": 2025,
         "net_income": 100.0, "equity": 1000.0, "eps": 9.0},
    ])
    cvm = {(1, 2025): {"lucro": 100.0, "pl": 1500.0, "consolidado": True,
                       "lpa_por_classe": {"3": 1.0, "4": 1.1}}}
    df = m.medir(brapi, cvm)
    assert len(df[df["campo"] == "lucro"]) == 1
    assert df.loc[df["campo"] == "pl", "categoria"].tolist() == ["forte"]
    lpa = df[df["campo"] == "lpa"].set_index("ticker")["categoria"].to_dict()
    assert lpa == {"AAAA3": "concorda", "AAAA4": "forte"}   # unit fica fora


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
