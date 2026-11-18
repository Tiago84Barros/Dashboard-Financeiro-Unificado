"""Gêmeo nos EUA do achado D2 da B3: percentil sem par vira nota extrema.

Na B3 (PR #763, SCORE_VERSION 2.33.0) o segmento de uma empresa só saía com
score 0. Nos EUA o grupo de UMA empresa já caía para o universo -- mas o corte
contava EMPRESAS da indústria, não VALORES VÁLIDOS da métrica. Numa indústria
de cinco empresas em que só uma publica ROIC, esse ROIC era percentil de um
valor só: 1,0, o melhor do grupo, mesmo sendo o pior do universo. Na métrica
em que menor é melhor (dívida líquida/EBITDA) o mesmo valor solitário saía
0,0. Medido no armazém em 17/11/2026: 2.710 pares empresa×métrica de 73.558,
em 686 das 4.349 empresas; 252 deles com um único valor no grupo.

A empresa sem indústria tinha o defeito simétrico: o ``groupby`` descarta a
chave nula, e a empresa saía neutra (0,5) em todas as métricas, por melhor ou
pior que fosse.

Correção (0.9.2): a métrica com menos de ``min_group`` valores válidos na
indústria -- ou sem indústria -- é ranqueada no universo, que é o que a regra
"grupos pequenos usam o universo" sempre quis dizer. E o percentil de menos de
dois valores é neutro, numa função só (``percentil_winsorizado``) que o
laboratório de Análise Avançada também usa.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.us_advanced_lab import _percentile
from core.us_score import (
    NEUTRAL,
    _ALL_METRICS,
    _rank_within,
    percentil_winsorizado,
    score_cross_section,
)


def _universo(seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    linhas = []
    for i in range(40):
        linha = {"symbol": f"U{i}", "industry": "GRANDE", "sector": "Tech"}
        linha.update({m: rng.normal() for m in _ALL_METRICS})
        linhas.append(linha)
    # Indústria de cinco empresas em que só S0 publica ROIC e dívida/EBITDA.
    for i in range(5):
        linha = {"symbol": f"S{i}", "industry": "ESPARSA", "sector": "Tech"}
        linha.update({m: rng.normal() for m in _ALL_METRICS})
        linha["roic"] = np.nan
        linha["net_debt_ebitda"] = np.nan
        linhas.append(linha)
    linhas[40]["roic"] = -50.0            # o pior ROIC do universo
    linhas[40]["net_debt_ebitda"] = -50.0  # a menor alavancagem do universo
    linha = {"symbol": "SOLO", "industry": "SOZINHA", "sector": "Tech"}
    linha.update({m: rng.normal() for m in _ALL_METRICS})
    linhas.append(linha)
    linha = {"symbol": "SEMIND", "industry": None, "sector": "Tech"}
    linha.update({m: 3.0 for m in _ALL_METRICS})  # topo em tudo
    linhas.append(linha)
    return pd.DataFrame(linhas)


def _pct(df: pd.DataFrame, simbolo: str, metrica: str) -> float:
    pct = _rank_within(df, "industry", 4)
    return float(pct.loc[df.index[df["symbol"] == simbolo][0], metrica])


def _no_universo(df: pd.DataFrame, simbolo: str, metrica: str) -> float:
    """O percentil da empresa se a métrica fosse ranqueada sem indústria."""
    sem_grupo = df.drop(columns=["industry"])
    pct = _rank_within(sem_grupo, "industry", 4)
    return float(pct.loc[df.index[df["symbol"] == simbolo][0], metrica])


def test_valor_solitario_na_industria_nao_vira_o_melhor_do_grupo():
    df = _universo()
    roic = _pct(df, "S0", "roic")
    assert roic < 0.1, f"pior ROIC do universo saiu {roic:.2f} (era 1,0)"
    assert roic == pytest.approx(_no_universo(df, "S0", "roic"))


def test_valor_solitario_em_menor_e_melhor_nao_vira_zero():
    df = _universo()
    alav = _pct(df, "S0", "net_debt_ebitda")
    assert alav > 0.9, f"menor alavancagem do universo saiu {alav:.2f} (era 0,0)"
    assert alav == pytest.approx(_no_universo(df, "S0", "net_debt_ebitda"))


def test_empresa_sem_industria_e_ranqueada_no_universo_nao_neutra():
    df = _universo()
    for metrica in ("roic", "gross_margin", "net_debt_ebitda"):
        assert _pct(df, "SEMIND", metrica) == pytest.approx(
            _no_universo(df, "SEMIND", metrica))
    assert _pct(df, "SEMIND", "gross_margin") > 0.9


def test_industria_de_uma_empresa_continua_no_universo():
    """O caso literal do D2 da B3 já era tratado nos EUA: não muda."""
    df = _universo()
    for metrica in ("roic", "gross_margin"):
        assert _pct(df, "SOLO", metrica) == pytest.approx(
            _no_universo(df, "SOLO", metrica))


def test_industria_com_valores_suficientes_continua_relativa():
    """Quem tem par na indústria segue ranqueado contra a indústria."""
    df = _universo()
    esparsa = df[df["industry"] == "ESPARSA"]
    pct = _rank_within(df, "industry", 4)
    so_eles = percentil_winsorizado(pd.to_numeric(esparsa["gross_margin"]))
    assert pct.loc[esparsa.index, "gross_margin"].tolist() == pytest.approx(
        so_eles.tolist())


def test_nota_final_do_pior_roic_cai():
    """A nota inteira acompanha: o ROIC ruim deixa de somar como o melhor."""
    df = _universo()
    sc = score_cross_section(df)
    s0 = sc.loc[sc["symbol"] == "S0", "score_capital_efficiency"].iloc[0]
    assert s0 < 20.0


# ── A função única ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("valores", [[], [np.nan, np.nan], [7.0], [np.nan, 7.0]])
def test_menos_de_dois_valores_e_neutro(valores):
    s = pd.Series(valores, dtype=float)
    assert (percentil_winsorizado(s) == NEUTRAL).all()
    assert (percentil_winsorizado(s, maior_melhor=False) == NEUTRAL).all()


def test_dois_valores_ordenam():
    s = pd.Series([1.0, 2.0])
    assert percentil_winsorizado(s).tolist() == [0.5, 1.0]
    assert percentil_winsorizado(s, maior_melhor=False).tolist() == [0.5, 0.0]


def _percentile_antigo(values: pd.Series, higher: bool = True) -> pd.Series:
    """Cópia de ``core.us_advanced_lab._percentile`` antes da 0.9.1 (a 0.9.1 só
    trocou o valor solitário por 50)."""
    numeric = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan)
    valid = numeric.dropna()
    if valid.empty:
        return pd.Series(50.0, index=numeric.index)
    if len(valid) >= 4:
        numeric = numeric.clip(valid.quantile(.05), valid.quantile(.95))
    score = numeric.rank(pct=True, method="average") * 100
    if not higher:
        score = 100 - score
    return score.fillna(50.0).clip(0, 100)


@pytest.mark.parametrize("n", [2, 3, 4, 5, 10, 30, 300])
@pytest.mark.parametrize("higher", [True, False])
def test_laboratorio_usa_a_mesma_conta_do_score(n, higher):
    """Equivalência dos dois caminhos: com dois valores ou mais, o laboratório
    dá o mesmo número de antes, e é 100× o percentil do score.

    O laboratório só winsorizava com quatro valores ou mais; o score, sempre.
    Abaixo de ~20 valores o corte de 5% fica entre o menor e o segundo menor
    valor e não cria empate, então a ordem -- e o percentil -- é a mesma.
    """
    rng = np.random.default_rng(n)
    s = pd.Series(rng.standard_t(3, size=n))
    s.iloc[0] = np.nan if n > 3 else s.iloc[0]
    if n >= 10:
        s.iloc[1] = np.inf
    novo = _percentile(s, higher)
    assert novo.tolist() == pytest.approx(_percentile_antigo(s, higher).tolist())
    limpo = pd.to_numeric(s).replace([np.inf, -np.inf], np.nan)
    assert novo.tolist() == pytest.approx(
        (percentil_winsorizado(limpo, maior_melhor=higher) * 100).tolist())


def test_laboratorio_com_um_valor_sai_neutro():
    """Antes: 100 (ou 0, se menor é melhor) para o único valor observado."""
    s = pd.Series([np.nan, 0.12, np.nan])
    assert _percentile(s, True).tolist() == [50.0, 50.0, 50.0]
    assert _percentile(s, False).tolist() == [50.0, 50.0, 50.0]
