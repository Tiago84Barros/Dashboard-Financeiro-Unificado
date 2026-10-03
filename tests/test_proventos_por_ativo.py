from datetime import date

from core.proventos import serie_por_ativo


def _ev(ticker, total, pagamento, tipo="dividend"):
    return {"ticker": ticker, "tipo": tipo, "total_amount": total,
            "payment_date": pagamento}


EVENTOS = [
    _ev("BBAS3", 100.0, date(2021, 3, 10)),
    _ev("BBAS3", 50.0, date(2023, 6, 1)),
    _ev("BBAS3", 25.0, date(2023, 6, 20), tipo="jcp"),
    _ev("MXRF11", 10.0, date(2023, 1, 15), tipo="reit_income"),
    _ev("MXRF11", 999.0, date(2023, 2, 15), tipo="amortization"),
]


def test_anual_tem_anos_contiguos_com_zero_no_ano_sem_pagamento():
    d = serie_por_ativo(EVENTOS, "anual")
    assert d["periodos"] == ["2021", "2022", "2023"]
    assert d["series"]["BBAS3"] == [100.0, 0.0, 75.0]
    assert d["series"]["MXRF11"] == [0.0, 0.0, 10.0]


def test_amortizacao_nao_conta_como_provento():
    d = serie_por_ativo(EVENTOS, "anual")
    assert d["totais"]["MXRF11"] == 10.0


def test_ordem_por_total_decrescente():
    assert serie_por_ativo(EVENTOS)["ordem"] == ["BBAS3", "MXRF11"]


def test_mensal_dentro_de_um_ano_passado_tem_doze_meses():
    d = serie_por_ativo(EVENTOS, "mensal", ano=2023)
    assert len(d["periodos"]) == 12
    assert d["periodos"][0] == "Jan/23"
    assert d["series"]["BBAS3"][5] == 75.0
    assert d["series"]["MXRF11"][0] == 10.0


def test_mensal_historico_completo_e_contiguo():
    d = serie_por_ativo(EVENTOS, "mensal")
    assert d["periodos"][0] == "Mar/21"
    assert d["periodos"][-1] == "Jun/23"
    assert len(d["periodos"]) == 28


def test_filtro_de_tickers_e_payment_date_em_texto():
    eventos = [*EVENTOS, _ev("ITSA4", 5.0, "2022-08-01")]
    d = serie_por_ativo(eventos, "anual", tickers=["ITSA4"])
    assert d["ordem"] == ["ITSA4"]
    assert d["periodos"] == ["2022"]


def test_ano_corrente_nao_mostra_meses_futuros():
    hoje = date.today()
    d = serie_por_ativo([_ev("X", 1.0, date(hoje.year, 1, 5))], "mensal", ano=hoje.year)
    assert len(d["periodos"]) == hoje.month


def test_sem_eventos_devolve_estrutura_vazia():
    assert serie_por_ativo([], "anual")["ordem"] == []


# ── Fracionário (BBAS3F) é o mesmo ativo que o lote padrão (BBAS3) ──────────

from core.proventos import _montar_dict  # noqa: E402


def _ev_fonte(ticker, total, pagamento, ext, nome=None):
    return {"ticker": ticker, "nome": nome or ticker, "classe": "Ações",
            "cor": "#000", "tipo": "dividend", "label_tipo": "Dividendo",
            "total_amount": total, "payment_date": pagamento, "external_id": ext}


HOJE = date(2026, 10, 2)


def test_fracionario_soma_no_lote_padrao():
    d = _montar_dict([
        _ev_fonte("BBAS3", 100.0, date(2025, 3, 1), "b3mov-1", "Banco do Brasil"),
        _ev_fonte("BBAS3F", 7.5, date(2025, 3, 1), "xpcsl-1", "BBAS3F"),
    ], HOJE)
    assert [a["ticker"] for a in d["por_ativo"]] == ["BBAS3"]
    assert d["por_ativo"][0]["total"] == 107.5
    assert d["por_ativo"][0]["nome"] == "Banco do Brasil"
    assert d["num_ativos"] == 1


def test_mesmo_pagamento_nos_dois_tickers_conta_uma_vez_e_fica_o_da_b3():
    d = _montar_dict([
        _ev_fonte("BBAS3F", 50.0, date(2025, 6, 1), "xpcsl-9"),
        _ev_fonte("BBAS3", 50.0, date(2025, 6, 1), "b3mov-9"),
    ], HOJE)
    assert d["total_historico"] == 50.0
    assert [e["external_id"] for e in d["eventos"]] == ["b3mov-9"]


def test_pagamentos_iguais_no_mesmo_ticker_nao_sao_descartados():
    d = _montar_dict([
        _ev_fonte("BBAS3", 50.0, date(2025, 6, 1), "b3mov-1"),
        _ev_fonte("BBAS3", 50.0, date(2025, 6, 1), "b3mov-2"),
    ], HOJE)
    assert d["total_historico"] == 100.0


def test_fii_e_unit_mantem_o_ticker():
    d = _montar_dict([
        _ev_fonte("MXRF11", 10.0, date(2025, 1, 1), "b3mov-1"),
        _ev_fonte("MXRF11F", 2.0, date(2025, 1, 1), "xpcsl-1"),
        _ev_fonte("TAEE11", 3.0, date(2025, 1, 1), "b3mov-2"),
    ], HOJE)
    totais = {a["ticker"]: a["total"] for a in d["por_ativo"]}
    assert totais == {"MXRF11": 12.0, "TAEE11": 3.0}


def test_serie_por_ativo_ve_um_so_ativo():
    d = _montar_dict([
        _ev_fonte("BBAS3", 100.0, date(2024, 3, 1), "b3mov-1"),
        _ev_fonte("BBAS3F", 7.5, date(2025, 3, 1), "xpcsl-1"),
    ], HOJE)
    s = serie_por_ativo(d["eventos"], "anual")
    assert s["ordem"] == ["BBAS3"]
    assert s["series"]["BBAS3"] == [100.0, 7.5]


# ── Filtro por situação na carteira ──────────────────────────────────────────

from core.proventos import (  # noqa: E402
    SITUACOES_ATIVO,
    filtrar_por_situacao,
    tickers_em_carteira,
)

ORDEM = ["BBAS3", "ITUB3", "MXRF11", "SAPR3"]


def test_tickers_em_carteira_ignora_saldo_zero_e_junta_fracionario():
    carteira = {"posicoes": [
        {"ticker": "BBAS3F", "quantidade": 7},
        {"ticker": "MXRF11", "quantidade": 100},
        {"ticker": "ITUB3", "quantidade": 0},
        {"ticker": None, "quantidade": 5},
    ]}
    assert tickers_em_carteira(carteira) == {"BBAS3", "MXRF11"}
    assert tickers_em_carteira(None) == set()


def test_filtrar_na_carteira_e_sairam_sao_complementares_e_mantem_ordem():
    em = {"BBAS3", "MXRF11"}
    na = filtrar_por_situacao(ORDEM, em, "Na carteira")
    sairam = filtrar_por_situacao(ORDEM, em, "Saíram da carteira")
    assert na == ["BBAS3", "MXRF11"]
    assert sairam == ["ITUB3", "SAPR3"]
    assert filtrar_por_situacao(ORDEM, em, "Todos") == ORDEM


def test_filtrar_compara_pelo_ticker_base():
    assert filtrar_por_situacao(["BBAS3F"], {"BBAS3"}, "Na carteira") == ["BBAS3F"]


def test_situacoes_oferecidas_na_tela():
    assert SITUACOES_ATIVO == ("Todos", "Na carteira", "Saíram da carteira")
    import inspect

    import views.investimentos as inv
    corpo = inspect.getsource(inv._proventos_por_ativo)
    assert "filtrar_por_situacao(base[\"ordem\"], em_carteira, situacao)" in corpo
    assert "_proventos_por_ativo(proventos, carteira)" in inspect.getsource(inv._tab_historico)
