"""Partes puras da aba IPCA Pessoal (tabelas, rótulos, gráfico)."""
from __future__ import annotations

from datetime import date

from pytest import approx

from core.ipca_grupos import INDICE_GERAL
from core.ipca_pessoal import (
    ORIGEM_ITENS,
    ORIGEM_SEM_GRUPO,
    CategoriaIpca,
    PontoMensal,
    ResultadoIpcaPessoal,
)
from views import controle_ipca_pessoal as v


def _res():
    cats = (
        CategoriaIpca("Moradia", "Habitação", True, 0.6, 0.05, ORIGEM_ITENS, 11, 0.9, 0.03),
        CategoriaIpca("Outros", INDICE_GERAL, False, 0.4, 0.04, ORIGEM_SEM_GRUPO, 0, 0.0, 0.016),
    )
    return ResultadoIpcaPessoal(
        ok=True, inicio=date(2025, 11, 1), fim=date(2026, 10, 1),
        acumulado_12m=0.046, oficial_12m=0.048,
        mensal=(PontoMensal(date(2025, 11, 1), 0.004, 0.003, 0.004, 0.003),),
        categorias=cats,
        pesos_por_grupo=(("Habitação", 0.6, 0.15), ("Educação", 0.0, None),
                         ("Sem grupo definido", 0.4, None)),
    )


def test_rotulo_origem():
    moradia, outros = _res().categorias
    assert v.rotulo_origem(moradia) == "seus itens (11 de 12 meses)"
    assert v.rotulo_origem(outros) == "sem grupo definido"


def test_tabela_categorias():
    df = v.tabela_categorias(_res())
    assert list(df.columns) == ["Categoria", "Grupo do IPCA", "Peso",
                                "Variação de preço (12m)", "Origem", "Contribuição"]
    linha = df.iloc[0]
    assert (linha["Categoria"], linha["Peso"], linha["Variação de preço (12m)"],
            linha["Contribuição"]) == ("Moradia", "60,0%", "5,00%", "+3,00 p.p.")


def test_tabela_pesos():
    df = v.tabela_pesos(_res())
    assert list(df.columns) == ["Grupo do IPCA", "Seu peso", "Peso no IPCA (IBGE)"]
    assert df.iloc[0]["Peso no IPCA (IBGE)"] == "15,0%"
    assert df.iloc[1]["Peso no IPCA (IBGE)"] == "—"
    assert df.iloc[2]["Grupo do IPCA"] == "Sem grupo definido"


def test_grafico_tem_as_duas_linhas_em_percentual():
    fig = v.grafico_acumulado(_res())
    assert [t.name for t in fig.data] == ["IPCA Pessoal", "IPCA oficial"]
    assert fig.data[0].y[0] == approx(0.4)
    assert fig.data[1].y[0] == approx(0.3)
