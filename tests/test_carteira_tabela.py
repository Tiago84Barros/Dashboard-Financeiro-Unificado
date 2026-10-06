"""Tabela de posições da Visão Geral: agrupamento, métricas, aporte e corretora."""
from datetime import date

from core.carteira_tabela import (
    ABERTURA,
    corretoras_por_foto,
    corretoras_por_movimentacao,
    grupo_da_posicao,
    montar_tabela,
    nome_corretora,
    primeiros_aportes,
    ticker_base,
)
from design.tabela_posicoes import (
    fmt_data_aporte,
    fmt_quantidade,
    render_tabela_posicoes,
)


def _pos(ticker, classe, setor, vm, ti=None, moeda="BRL", pais="BR", **kw):
    return {"ticker": ticker, "nome": kw.pop("nome", ticker), "classe": classe,
            "setor": setor, "moeda": moeda, "pais": pais, "quantidade": kw.pop("qtd", 10),
            "preco_medio": kw.pop("pm", 1.0), "total_investido": ti if ti is not None else vm,
            "valor_mercado": vm, **kw}


POSICOES = [
    _pos("PETR4", "Ações BR", "Energia", 3000),
    _pos("PRIO3", "Ações BR", "Energia", 1000),
    _pos("HGLG11", "FII", "Imóveis / FII", 2000),
    _pos("VOO", "ETF Internacional", "ETF Internacional", 2500, moeda="USD", pais="US"),
    _pos("Tesouro Selic 2029", "Tesouro Direto", "Governo", 1000),
    _pos("CDB XP", "Renda Fixa", "Financeiro", 500),
]


def test_ticker_base_fracionario_e_recibo_de_fii():
    assert ticker_base("petr4f") == "PETR4"
    assert ticker_base("HGLG11F") == "HGLG11"
    assert ticker_base("HGLG13") == "HGLG11"
    assert ticker_base("VOO") == "VOO"


def test_nome_corretora_apelidos_sufixos_e_b3_neutra():
    assert nome_corretora("XP INVESTIMENTOS CCTVM S/A") == "XP"
    assert nome_corretora("NU INVEST CORRETORA DE VALORES S.A.") == "Nu Invest"
    assert nome_corretora("ITAU CORRETORA DE VALORES S/A") == "Itaú"
    assert nome_corretora("BTG PACTUAL CTVM S/A") == "BTG Pactual"
    assert nome_corretora("B3 - Area do Investidor") == ""
    assert nome_corretora(None) == ""
    assert nome_corretora("FOOBAR CORRETORA DE CAMBIO LTDA") == "Foobar"


def test_grupos_na_ordem_pedida_e_renda_fixa_reune_tesouro():
    tab = montar_tabela(POSICOES + [_pos("BOVA11", "ETF Brasil", "Índice", 100)], {})
    nomes = [g["grupo"] for g in tab["grupos"]]
    assert nomes == ["Ações", "FII", "Americanas", "Renda Fixa", "ETF Brasil"]
    rf = next(g for g in tab["grupos"] if g["grupo"] == "Renda Fixa")
    assert {ln["ticker"] for ln in rf["linhas"]} == {"Tesouro Selic 2029", "CDB XP"}


def test_exterior_vai_para_americanas_mesmo_com_classe_de_acao():
    assert grupo_da_posicao({"classe": "Ações BR", "moeda": "USD", "pais": "US"}) == "Americanas"


def test_percentuais_patrimonio_e_setor():
    tab = montar_tabela(POSICOES, {})
    acoes = tab["grupos"][0]
    petr = acoes["linhas"][0]
    assert petr["ticker"] == "PETR4"                       # ordenado por valor
    assert round(petr["pct_patrimonio"], 4) == round(3000 / 10000 * 100, 4)
    assert petr["pct_setor"] == 75.0                        # 3000 / (3000+1000)
    assert acoes["valor_mercado"] == 4000
    assert tab["total"]["pct_patrimonio"] == 100.0
    assert sum(g["pct_patrimonio"] for g in tab["grupos"]) == 100.0


def test_dividendos_somam_fracionario_no_papel_inteiro():
    tab = montar_tabela(POSICOES, {"PETR4": 100.0, "PETR4F": 5.5, "HGLG11": 80.0})
    petr = tab["grupos"][0]["linhas"][0]
    assert petr["dividendos_12m"] == 105.5
    assert tab["total"]["dividendos_12m"] == 185.5


def test_primeiro_aporte_extrato_vence_foto_e_abertura_vence_tudo():
    aportes = primeiros_aportes(
        compras=[{"ticker": "PETR4", "data": date(2021, 3, 1)}],
        creditos=[{"ticker": "PETR4F", "data": date(2020, 5, 2)},
                  {"ticker": "BBAS3", "data": date(2020, 1, 1)}],
        fotos=[{"ticker": "PETR4", "data": date(2019, 1, 1)},
               {"ticker": "CDB XP", "data": date(2024, 6, 30)}],
        abertura={"BBAS3"},
    )
    assert aportes["PETR4"] == {"data": "2020-05-02", "fonte": "extrato"}
    assert aportes["CDB XP"] == {"data": "2024-06-30", "fonte": "foto"}
    assert aportes["BBAS3"] == {"data": None, "fonte": ABERTURA}


def test_corretora_pela_data_de_credito_mais_recente():
    eventos = [
        {"data": "2022-01-10", "ticker": "PETR4", "direcao": "Credito",
         "instituicao": "CLEAR CORRETORA - GRUPO XP"},
        {"data": "2024-02-01", "ticker": "PETR4", "direcao": "Debito",
         "instituicao": "CLEAR CORRETORA - GRUPO XP"},
        {"data": "2024-02-01", "ticker": "PETR4", "direcao": "Credito",
         "instituicao": "NU INVEST CORRETORA DE VALORES S.A."},
        {"data": "2025-05-20", "ticker": "HGLG11", "direcao": "Crédito",
         "instituicao": "XP INVESTIMENTOS CCTVM S/A"},
        {"data": "2025-05-20", "ticker": "HGLG11", "direcao": "Crédito",
         "instituicao": "ITAU CORRETORA DE VALORES S/A"},
    ]
    corr = corretoras_por_movimentacao(eventos)
    assert corr["PETR4"] == ["Nu Invest"]
    assert corr["HGLG11"] == ["Itaú", "XP"]


def test_corretora_da_foto_ignora_a_constante_da_b3():
    fotos = [
        {"ticker": "CDB XP", "data": "2025-01-31", "instituicao": "XP INVESTIMENTOS"},
        {"ticker": "PETR4", "data": "2025-01-31", "instituicao": "B3 - Area do Investidor"},
    ]
    corr = corretoras_por_foto(fotos)
    assert corr == {"CDB XP": ["XP"]}


def test_celulas_de_formato():
    assert fmt_quantidade(1200) == "1.200"
    assert fmt_quantidade(0.5) == "0,5"
    assert fmt_quantidade(3.14159) == "3,1416"
    assert "antes de nov/2019" in fmt_data_aporte(None, ABERTURA)
    cel = fmt_data_aporte("2021-03-15", "extrato", hoje=date(2026, 10, 6))
    assert "15/03/2021" in cel and "há 5 anos" in cel
    assert fmt_data_aporte("2024-06-30", "foto").startswith("até 30/06/2024")
    assert "—" in fmt_data_aporte(None, None)


def test_render_tem_colunas_na_ordem_e_escapa_texto():
    extras = {"primeiro_aporte": {"PETR4": {"data": "2020-01-02", "fonte": "extrato"}},
              "corretoras": {"PETR4": ["XP"]}}
    pos = POSICOES + [_pos("XYZ3", "Ações BR", "Energia", 10, nome="<script>x</script>")]
    html = render_tabela_posicoes(montar_tabela(pos, {}, extras), ["extrato de Negociação"])
    rotulos = ["Ativo", "Valor investido", "Quantidade", "Dividendos 12M", "Preço médio",
               "Valor de mercado", "% patrimônio", "% do setor", "1º aporte", "Corretora"]
    posicoes_rot = [html.index(f">{r}</th>") for r in rotulos]
    assert posicoes_rot == sorted(posicoes_rot)
    assert "<script>x</script>" not in html
    assert "Indisponível agora" in html
    # Linhas sem indentação: o markdown do Streamlit viraria bloco de código.
    assert not any(linha.startswith("    ") for linha in html.splitlines())


def test_render_vazio_sem_posicoes():
    assert render_tabela_posicoes(montar_tabela([], {})) == ""
