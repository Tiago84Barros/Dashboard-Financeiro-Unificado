"""Filtros de setor/indústria da aba Análise Avançada (Empresas Americanas).

Bug real observado no deploy: a lista de Setor mostrava "Consumo Defensivo" 3×,
"Indústria" 5× e "Outros setores" repetido. Causa: as opções eram os valores
BRUTOS do SIC e a tradução (muitos-para-um) acontecia só no format_func. Além do
ruído visual, escolher um item filtrava UM único código SIC — devolvendo um
fragmento do setor sem avisar.
"""
from pathlib import Path

import pandas as pd

from core.market_companies import sector_industry_labels, translate_us_sector

_ROOT = Path(__file__).resolve().parents[1]


def _universo_com_sic_repetido() -> pd.DataFrame:
    """Vários SIC distintos que colapsam no mesmo setor macro (caso do print)."""
    return pd.DataFrame([
        {"symbol": "MSFT", "sector": "Services-Prepackaged Software",
         "industry": "Services-Prepackaged Software"},
        {"symbol": "GOOGL", "sector": "Services-Computer Programming, Data Processing",
         "industry": "Services-Computer Programming, Data Processing"},
        {"symbol": "KO", "sector": "Beverages", "industry": "Beverages"},
        {"symbol": "PG", "sector": "Soap, Detergents, Cleaning Preparations",
         "industry": "Soap, Detergents, Cleaning Preparations"},
        {"symbol": "CAT", "sector": "Construction Machinery & Equip",
         "industry": "Construction Machinery & Equip"},
        {"symbol": "GE", "sector": "Motors & Generators", "industry": "Motors & Generators"},
    ])


def test_opcoes_de_setor_sem_duplicatas():
    df = _universo_com_sic_repetido()
    setor, _ = sector_industry_labels(df)

    # os valores BRUTOS são todos distintos (era isso que virava opção antes)
    assert df["sector"].nunique() == 6
    # já os RÓTULOS colapsam — e a lista de opções não pode repetir
    opcoes = sorted(setor.unique())
    assert len(opcoes) == len(set(opcoes)), f"rótulos duplicados: {opcoes}"
    assert len(opcoes) < 6, "os SIC deveriam colapsar em menos setores macro"


def test_filtrar_por_rotulo_traz_todas_as_empresas_do_setor():
    """O bug pior: filtrar pelo bruto devolvia só um fragmento do setor."""
    df = _universo_com_sic_repetido()
    setor, _ = sector_industry_labels(df)

    for rotulo in setor.unique():
        esperado = {s for s, lbl in zip(df["symbol"], setor) if lbl == rotulo}
        obtido = set(df[setor == rotulo]["symbol"])
        assert obtido == esperado

        # comportamento ANTIGO (filtrar pelo valor bruto) perderia empresas
        # sempre que mais de um SIC mapear para o mesmo rótulo
        if len(esperado) > 1:
            um_bruto = df[setor == rotulo]["sector"].iloc[0]
            antigo = set(df[df["sector"] == um_bruto]["symbol"])
            assert antigo < esperado, (
                f"'{rotulo}': filtrar pelo bruto devolveria {antigo}, "
                f"mas o setor tem {esperado}")


def test_labels_alinhados_ao_indice_e_quadro_vazio():
    df = _universo_com_sic_repetido().set_index("symbol")
    setor, industria = sector_industry_labels(df)
    assert list(setor.index) == list(df.index)
    assert list(industria.index) == list(df.index)

    vazio_setor, vazio_ind = sector_industry_labels(pd.DataFrame())
    assert vazio_setor.empty and vazio_ind.empty


def test_sem_sector_ou_industry_nao_quebra():
    setor, industria = sector_industry_labels(pd.DataFrame({"symbol": ["X", "Y"]}))
    assert len(setor) == 2 and len(industria) == 2
    assert all(isinstance(v, str) for v in setor)


def test_view_usa_rotulo_no_filtro_e_nao_o_bruto():
    """Trava a regressão: a aba não pode voltar a listar valores brutos."""
    fonte = (_ROOT / "views" / "empresas_americanas.py").read_text(encoding="utf-8")
    trecho = fonte.split("Filtros do Universo", 1)[1][:2500]
    assert "sector_industry_labels(scored)" in trecho
    assert "setor_label == sector" in trecho, "filtro de setor deve casar pelo rótulo"
    # o format_func traduzindo o bruto era exatamente o que gerava as duplicatas
    assert "format_func=lambda x: x if x == \"Todos\"" not in trecho


def test_nan_em_setor_vira_rotulo_valido():
    df = pd.DataFrame([{"symbol": "A", "sector": None, "industry": None}])
    setor, _ = sector_industry_labels(df)
    assert setor.iloc[0] == translate_us_sector("", "")
