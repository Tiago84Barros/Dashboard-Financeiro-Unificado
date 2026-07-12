"""Regressão: linhas de histórico com AvailableAt=NaT (backfill migration_baseline)
devem PASSAR pelo corte fiscal — não ser excluídas pelo filtro point-in-time.

Bug (2026-07): quando a 1ª vintage real (first_seen_proxy) apareceu no banco, a
coluna AvailableAt passou a vir no batch inteiro (NaT p/ linhas de backfill) e o
filtro `notna() & <= decisão` zerava o histórico → Criação de Portfólio caía em
"Nenhum segmento retornou dados suficientes".
"""
import pandas as pd

from views.empresas_b3 import _score_historico_ano, _get_pesos_setor


def _hist(tk, anos, availableat=None):
    df = pd.DataFrame({
        "Ticker": tk,
        "Data": [pd.Timestamp(a, 12, 31) for a in anos],
        "ROE": [0.15 + 0.01 * i for i in range(len(anos))],
        "ROIC": [0.12] * len(anos),
        "Margem_Liquida": [0.10] * len(anos),
        "P/L": [8.0] * len(anos),
        "P/VP": [1.2] * len(anos),
        "DY": [0.05] * len(anos),
    })
    if availableat is not None:
        df["AvailableAt"] = availableat
    return df


_TKG = {t: {"SETOR": "Materiais Básicos", "SUBSETOR": "x", "SEGMENTO": "y"}
        for t in ("AAAA3", "BBBB3", "CCCC3")}
_PESOS = _get_pesos_setor("Materiais Básicos")


def test_nat_availableat_passa_pelo_corte_fiscal():
    anos = list(range(2015, 2024))
    batch = {
        # backfill puro: AvailableAt existe mas é toda NaT
        "AAAA3": _hist("AAAA3", anos, availableat=[pd.NaT] * len(anos)),
        "BBBB3": _hist("BBBB3", anos, availableat=[pd.NaT] * len(anos)),
        "CCCC3": _hist("CCCC3", anos, availableat=[pd.NaT] * len(anos)),
    }
    sm = _score_historico_ano(batch, list(batch), 2020, _PESOS, _TKG, lag=1)
    assert sm, "NaT (backfill) não pode zerar o score — corte fiscal já foi aplicado"
    assert set(sm) == {"AAAA3", "BBBB3", "CCCC3"}


def test_availableat_real_respeita_ponto_no_tempo():
    anos = [2019, 2020]
    # vintage real: dado de 2020 só ficou disponível em jun/2021 → para decisão
    # de 2021 (rebalance no início do ano) a linha de 2020 NÃO pode entrar,
    # mas a de 2019 (NaT, backfill) entra pelo corte fiscal.
    av = [pd.NaT, pd.Timestamp(2021, 6, 30, tz="UTC")]
    batch = {"AAAA3": _hist("AAAA3", anos, availableat=av),
             "BBBB3": _hist("BBBB3", anos, availableat=[pd.NaT, pd.NaT]),
             "CCCC3": _hist("CCCC3", anos, availableat=[pd.NaT, pd.NaT])}
    sm = _score_historico_ano(batch, list(batch), 2021, _PESOS, _TKG, lag=1)
    assert "AAAA3" in sm  # entra com a linha de 2019 (a de 2020 é barrada)


def test_sem_coluna_availableat_segue_corte_fiscal():
    anos = list(range(2015, 2024))
    batch = {t: _hist(t, anos) for t in ("AAAA3", "BBBB3", "CCCC3")}
    sm = _score_historico_ano(batch, list(batch), 2020, _PESOS, _TKG, lag=1)
    assert set(sm) == {"AAAA3", "BBBB3", "CCCC3"}
