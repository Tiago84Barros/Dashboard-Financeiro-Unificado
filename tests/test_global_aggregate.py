"""Quadro unificado de posicoes das tres carteiras."""
import pandas as pd
import pytest

from core.global_portfolio.aggregate import classes_sem_posicao, montar_posicoes

SNAPS = {
    "b3": {
        "PETR4": {"identity": {"symbol": "PETR4", "name": "Petrobras",
                               "sector": "Petróleo, Gás e Biocombustíveis"},
                  "metrics": {"weight": 0.6}},
        "ITUB4": {"identity": {"symbol": "ITUB4", "name": "Itaú",
                               "sector": "Financeiro"},
                  "metrics": {"weight": 0.4}},
    },
    "us": {
        "AAPL": {"identity": {"symbol": "AAPL", "name": "Apple",
                              "sector": "Technology"},
                 "metrics": {"weight": 1.0}},
    },
    "fii": {
        "HGLG11": {"identity": {"symbol": "HGLG11", "name": "CSHG Log",
                                "sector": "Logística", "segment": "Tijolo"},
                   "metrics": {"weight": 1.0}},
    },
}

ALVOS = {"b3": 0.5, "us": 0.3, "fii": 0.2}


def test_peso_global_e_alvo_da_classe_vezes_peso_no_modelo():
    df = montar_posicoes(SNAPS, ALVOS)
    petr = df[df["symbol"] == "PETR4"].iloc[0]
    assert petr["weight_global"] == pytest.approx(0.5 * 0.6)


def test_pesos_globais_somam_um():
    df = montar_posicoes(SNAPS, ALVOS)
    assert df["weight_global"].sum() == pytest.approx(1.0)


def test_pesos_do_modelo_sao_renormalizados_dentro_da_classe():
    snaps = {"b3": {
        "A3": {"identity": {"symbol": "A3"}, "metrics": {"weight": 0.3}},
        "B3X": {"identity": {"symbol": "B3X"}, "metrics": {"weight": 0.3}},
    }}
    df = montar_posicoes(snaps, {"b3": 1.0})
    assert df["weight_global"].sum() == pytest.approx(1.0)
    assert df["weight_class"].tolist() == pytest.approx([0.5, 0.5])


def test_setor_canonico_e_preenchido_e_o_bruto_preservado():
    df = montar_posicoes(SNAPS, ALVOS).set_index("symbol")
    assert df.loc["PETR4", "sector"] == "energy"
    assert df.loc["PETR4", "sector_raw"] == "Petróleo, Gás e Biocombustíveis"
    assert df.loc["AAPL", "sector"] == "technology"
    assert df.loc["HGLG11", "sector"] == "real_estate"


def test_moeda_e_pais_vem_do_registro_da_classe():
    df = montar_posicoes(SNAPS, ALVOS).set_index("symbol")
    assert df.loc["PETR4", "currency"] == "BRL"
    assert df.loc["PETR4", "country"] == "BR"
    assert df.loc["AAPL", "currency"] == "USD"
    assert df.loc["AAPL", "country"] == "US"


def test_valor_brl_sai_do_total_informado_sem_conversao_cambial():
    df = montar_posicoes(SNAPS, ALVOS, total_brl=100000.0).set_index("symbol")
    assert df.loc["AAPL", "valor_brl"] == pytest.approx(30000.0)


def test_sem_total_informado_valor_brl_fica_nulo():
    df = montar_posicoes(SNAPS, ALVOS)
    assert df["valor_brl"].isna().all()


def test_classe_no_alvo_sem_snapshot_e_ignorada():
    df = montar_posicoes({"b3": SNAPS["b3"]}, {"b3": 0.5, "us": 0.5})
    assert set(df["asset_class"]) == {"b3"}
    assert df["weight_global"].sum() == pytest.approx(0.5)


def test_classe_com_snapshot_fora_do_alvo_aparece_com_peso_zero():
    df = montar_posicoes(SNAPS, {"b3": 1.0})
    fora = df[df["asset_class"] != "b3"]
    assert not fora.empty
    assert (fora["weight_global"] == 0.0).all()


def test_ordenacao_e_deterministica():
    df = montar_posicoes(SNAPS, ALVOS)
    pesos = df["weight_global"].tolist()
    assert pesos == sorted(pesos, reverse=True)


def test_snapshots_vazios_devolvem_dataframe_vazio_com_as_colunas():
    df = montar_posicoes({}, {})
    assert df.empty
    for coluna in ("asset_class", "symbol", "sector", "weight_global"):
        assert coluna in df.columns


def test_classe_com_todos_pesos_zero_nao_falha():
    """Edge case: renormalizacao com total_classe == 0."""
    snaps = {"b3": {
        "X1": {"identity": {"symbol": "X1", "name": "X1"}, "metrics": {"weight": 0.0}},
        "X2": {"identity": {"symbol": "X2", "name": "X2"}, "metrics": {"weight": 0.0}},
    }}
    df = montar_posicoes(snaps, {"b3": 1.0})
    assert not df.empty
    assert (df["weight_class"] == 0.0).all()
    assert (df["weight_global"] == 0.0).all()


def test_payload_sem_bloco_identity():
    """Payload malformado: sem bloco 'identity'."""
    snaps = {"b3": {
        "NOTNAME": {"metrics": {"weight": 0.5}},
    }}
    df = montar_posicoes(snaps, {"b3": 1.0})
    row = df.iloc[0]
    assert row["symbol"] == "NOTNAME"
    assert row["name"] == "NOTNAME"  # fallback para o simbolo
    # Peso renormalizado: unico ativo na classe, entao 100% da classe.
    # weight_global = 1.0 (alvo) * 1.0 (peso_classe renormalizado) = 1.0
    assert row["weight_global"] == pytest.approx(1.0)


def test_metrics_weight_nao_numerico():
    """Payload malformado: weight nao e numero."""
    snaps = {"b3": {
        "BADWEIGHT": {"identity": {"symbol": "BADWEIGHT", "name": "Bad"},
                      "metrics": {"weight": "abc"}},
    }}
    df = montar_posicoes(snaps, {"b3": 1.0})
    row = df.iloc[0]
    assert row["weight_global"] == pytest.approx(0.0)


def test_classe_com_alvo_e_sem_snapshot_e_reportada():
    """Sem isso, o patrimonio soma menos que 1 sem nenhum aviso na tela."""
    achados = classes_sem_posicao({"b3": SNAPS["b3"]}, {"b3": 0.7, "us": 0.3})
    assert achados == [("us", 0.3)]


def test_classe_com_alvo_e_snapshot_nao_e_reportada():
    achados = classes_sem_posicao(SNAPS, ALVOS)
    assert achados == []


def test_classe_com_snapshot_e_sem_alvo_nao_e_reportada():
    """Ja fica visivel com peso zero (comportamento ja coberto em outro teste)."""
    achados = classes_sem_posicao(SNAPS, {"b3": 1.0})
    assert achados == []


def test_classe_com_alvo_zero_e_sem_snapshot_nao_e_reportada():
    """Alvo zero significa que nada foi esperado dessa classe."""
    achados = classes_sem_posicao({"b3": SNAPS["b3"]}, {"b3": 1.0, "us": 0.0})
    assert achados == []


def test_classes_sem_posicao_ordenada_por_classe():
    achados = classes_sem_posicao({}, {"us": 0.3, "b3": 0.2, "fii": 0.5})
    assert achados == [("b3", 0.2), ("fii", 0.5), ("us", 0.3)]
