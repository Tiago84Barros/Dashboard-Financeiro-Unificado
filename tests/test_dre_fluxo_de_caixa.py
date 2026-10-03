"""Lacuna 0c806394: o chat de ISAE3 dizia faltar o fluxo de caixa operacional
e livre para avaliar a cobertura de juros, e o P/FCO aparecia como N/D sem
motivo. As duas séries estão em `market.cash_flow_statements` e chegam por
`load_demonstracoes` (colunas FCO e FCF); a DRE histórica do chat só mandava
receita e lucro."""
import pandas as pd

import core.llm_context_b3 as ctx


def _demo(fco, fcf, ebit=(3_942_557_000.0, 5_131_278_000.0, 4_131_906_000.0)):
    return pd.DataFrame({
        "Ticker": ["ISAE3"] * 3,
        "Data": pd.to_datetime(["2023-12-31", "2024-12-31", "2025-12-31"]),
        "Receita_Liquida": [6.2e9, 7.97e9, 9.41e9],
        "Lucro_Liquido": [2.89e9, 3.55e9, 2.51e9],
        "EBITDA": [float("nan")] * 3,
        "EBIT": list(ebit),
        "FCO": list(fco),
        "FCF": list(fcf),
    })


def _ctx(monkeypatch, df):
    monkeypatch.setattr(ctx._db, "load_demonstracoes", lambda tk: df)
    return ctx.get_dre_history_context(["ISAE3"])


def test_fco_e_ebit_chegam_ao_contexto(monkeypatch):
    txt = _ctx(monkeypatch, _demo((589_848_000.0, -181_476_990.0, -1_215_500_000.0),
                                  (283_457_980.0, -400e6, -1_841_569_000.0)))
    assert "2025: Rec=9,410" in txt
    assert "FCO=-1,216" in txt
    assert "EBIT=4,132" in txt
    assert "FCL=-1,842" in txt


def test_fco_negativo_explica_o_pfco_ausente(monkeypatch):
    txt = _ctx(monkeypatch, _demo((589_848_000.0, -181_476_990.0, -1_215_500_000.0),
                                  (283_457_980.0, -400e6, -1_841_569_000.0)))
    assert "P/FCO" in txt and "FCO negativo" in txt


def test_fcl_maior_que_fco_nao_e_repassado(monkeypatch):
    # Dado real de ISAE3 em 2024: FCO -181 mi e "FCL" do provedor +1.136 mi.
    # Fluxo livre = FCO - capex nunca supera o FCO; o número não é repassado
    # como fato e a ausência fica nomeada.
    txt = _ctx(monkeypatch, _demo((589_848_000.0, -181_476_990.0, -1_215_500_000.0),
                                  (283_457_980.0, 1_136_300_000.0, -841_569_000.0)))
    assert "1,136" not in txt
    assert "FCL=N/D" in txt


def test_sem_colunas_de_caixa_nao_quebra(monkeypatch):
    df = _demo((1.0, 1.0, 1.0), (1.0, 1.0, 1.0)).drop(columns=["FCO", "FCF", "EBIT"])
    txt = _ctx(monkeypatch, df)
    assert "Rec=" in txt and "FCO" not in txt.split("\n", 1)[1]
