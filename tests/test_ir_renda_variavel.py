"""Apuração do IR sobre renda variável (core/ir_renda_variavel.py)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from core.ir_renda_variavel import apurar, tipo_fiscal, vencimento_darf

HOJE = date(2026, 9, 24)


def tx(dia, ticker, lado, qtd, preco, classe="stock", fees=0):
    return {"transaction_date": date.fromisoformat(dia), "ticker": ticker,
            "classe": classe, "type": lado, "quantity": qtd,
            "unit_price": preco, "fees": fees}


def mes(res, m):
    return next(x for x in res["meses"] if x["mes"] == m)


def test_venda_de_acoes_ate_20_mil_e_isenta():
    r = apurar([tx("2025-01-10", "PETR4", "buy", 1000, 10),
                tx("2025-02-10", "PETR4", "sell", 1000, 15)], hoje=HOJE)
    m = mes(r, "2025-02")
    assert m["isento_acoes"]
    assert m["cestas"]["comum"]["ganho_isento"] == Decimal("5000")
    assert m["darf"] == 0
    assert r["anos"][0]["ganho_isento"] == Decimal("5000")


def test_acima_de_20_mil_paga_15_por_cento_menos_o_irrf():
    r = apurar([tx("2025-01-10", "PETR4", "buy", 2000, 10),
                tx("2025-02-10", "PETR4", "sell", 2000, 12.5)], hoje=HOJE)
    m = mes(r, "2025-02")
    assert not m["isento_acoes"]
    assert m["cestas"]["comum"]["base"] == Decimal("5000")
    assert m["imposto"] == Decimal("750")
    assert m["irrf_estimado"] == Decimal("1.25")      # 0,005% de 25.000
    assert m["darf"] == Decimal("748.75")


def test_prejuizo_compensa_o_ganho_seguinte():
    r = apurar([tx("2025-01-02", "VALE3", "buy", 3000, 10),
                tx("2025-01-20", "VALE3", "sell", 3000, 9),      # -3.000, vendas 27 mil
                tx("2025-02-02", "ITUB4", "buy", 3000, 10),
                tx("2025-02-20", "ITUB4", "sell", 3000, 12)],    # +6.000, vendas 36 mil
               hoje=HOJE)
    jan, fev = mes(r, "2025-01"), mes(r, "2025-02")
    assert jan["cestas"]["comum"]["prejuizo_a_compensar"] == Decimal("3000")
    assert fev["cestas"]["comum"]["prejuizo_compensado"] == Decimal("3000")
    assert fev["cestas"]["comum"]["base"] == Decimal("3000")
    assert fev["imposto"] == Decimal("450")


def test_mes_isento_compensa_as_acoes_entre_si_antes_de_isentar():
    r = apurar([tx("2025-01-02", "PETR4", "buy", 100, 10),
                tx("2025-01-02", "VALE3", "buy", 100, 50),
                tx("2025-03-05", "PETR4", "sell", 100, 20),      # +1.000
                tx("2025-03-05", "VALE3", "sell", 100, 47)],     # -300; vendas 6.700
               hoje=HOJE)
    c = mes(r, "2025-03")["cestas"]["comum"]
    assert c["ganho_isento"] == Decimal("700")
    assert c["prejuizo_a_compensar"] == 0


def test_prejuizo_em_mes_isento_continua_compensavel():
    r = apurar([tx("2025-01-02", "PETR4", "buy", 100, 50),
                tx("2025-01-20", "PETR4", "sell", 100, 40)], hoje=HOJE)
    assert mes(r, "2025-01")["cestas"]["comum"]["prejuizo_a_compensar"] == Decimal("1000")


def test_fii_paga_20_por_cento_sem_isencao_e_nao_usa_prejuizo_de_acoes():
    r = apurar([tx("2025-01-02", "VALE3", "buy", 100, 50),
                tx("2025-01-10", "VALE3", "sell", 100, 40),                 # -1.000 comum
                tx("2025-01-02", "HGLG11", "buy", 10, 100, classe="reit"),
                tx("2025-01-20", "HGLG11", "sell", 10, 150, classe="reit")],  # +500 FII
               hoje=HOJE)
    m = mes(r, "2025-01")
    assert m["cestas"]["fii"]["base"] == Decimal("500")
    assert m["cestas"]["fii"]["imposto"] == Decimal("100")
    assert m["cestas"]["comum"]["prejuizo_a_compensar"] == Decimal("1000")
    assert m["darf"] == Decimal("100")


def test_day_trade_20_por_cento_e_irrf_de_1_por_cento():
    r = apurar([tx("2025-04-10", "PETR4", "buy", 1000, 10),
                tx("2025-04-10", "PETR4", "sell", 1000, 12)], hoje=HOJE)
    m = mes(r, "2025-04")
    assert m["cestas"]["day_trade"]["base"] == Decimal("2000")
    assert m["cestas"]["day_trade"]["imposto"] == Decimal("400")
    assert m["irrf_estimado"] == Decimal("20")
    assert m["darf"] == Decimal("380")
    assert m["vendas_acoes"] == 0          # day trade não conta para os 20 mil


def test_day_trade_parcial_separa_do_swing_sem_tocar_o_preco_medio():
    r = apurar([tx("2025-01-02", "PETR4", "buy", 100, 10),
                tx("2025-02-10", "PETR4", "buy", 100, 12),
                tx("2025-02-10", "PETR4", "sell", 150, 13)], hoje=HOJE)
    c = mes(r, "2025-02")["cestas"]
    assert c["day_trade"]["resultado"] == Decimal("100")      # 100 x (13 - 12)
    assert c["comum"]["ganho_isento"] == Decimal("150")       # 50 x (13 - 10)


def test_venda_sem_custo_marca_incompleto_e_contamina_o_prejuizo_seguinte():
    r = apurar([tx("2025-01-10", "BBAS3", "sell", 1000, 30),   # comprado antes de 2019
                tx("2025-02-02", "VALE3", "buy", 100, 50),
                tx("2025-02-10", "VALE3", "sell", 100, 40)], hoje=HOJE)
    jan, fev = mes(r, "2025-01"), mes(r, "2025-02")
    assert jan["incompleto"]
    assert jan["cestas"]["comum"]["sem_custo"] == ["BBAS3"]
    assert jan["cestas"]["comum"]["valor_sem_custo"] == Decimal("30000")
    assert jan["cestas"]["comum"]["resultado"] == 0          # não vira ganho nem perda
    assert not jan["isento_acoes"]                           # o valor vendido é conhecido
    assert not fev["incompleto"]
    assert fev["cestas"]["comum"]["prejuizo_incerto"]
    assert r["anos"][0]["meses_incompletos"] == ["2025-01"]


def test_darf_abaixo_de_10_reais_acumula_para_o_mes_seguinte():
    r = apurar([tx("2025-01-02", "IVVB11", "buy", 100, 100, classe="etf"),
                tx("2025-01-20", "IVVB11", "sell", 100, 100.5, classe="etf"),   # +50 → 7,50
                tx("2025-02-02", "IVVB11", "buy", 100, 100, classe="etf"),
                tx("2025-02-20", "IVVB11", "sell", 100, 100.2, classe="etf")],  # +20 → 3,00
               hoje=HOJE)
    jan, fev = mes(r, "2025-01"), mes(r, "2025-02")
    assert jan["isento_acoes"] and jan["cestas"]["comum"]["ganho_isento"] == 0   # ETF não isenta
    assert jan["darf"] == 0 and jan["acumulado_proximo"] == Decimal("7.5")
    assert fev["darf"] == Decimal("10.5")


def test_desdobro_da_movimentacao_preserva_o_custo():
    ev = [{"event_date": date(2025, 3, 1), "movement": "desdobro",
           "direction": "Credito", "ticker": "PETR4", "quantity": 100,
           "unit_price": None, "total_value": None}]
    r = apurar([tx("2025-01-02", "PETR4", "buy", 100, 20),
                tx("2025-04-10", "PETR4", "sell", 200, 11)], ev, hoje=HOJE)
    c = mes(r, "2025-04")["cestas"]["comum"]
    assert c["ganho_isento"] == Decimal("200")
    assert c["sem_custo"] == []


def test_fracionario_e_lote_sao_o_mesmo_ativo():
    r = apurar([tx("2025-01-02", "PETR4", "buy", 100, 10),
                tx("2025-02-10", "PETR4F", "sell", 30, 12)], hoje=HOJE)
    c = mes(r, "2025-02")["cestas"]["comum"]
    assert c["sem_custo"] == [] and c["ganho_isento"] == Decimal("60")


def test_corretagem_entra_no_custo_e_sai_da_receita():
    r = apurar([tx("2025-01-02", "PETR4", "buy", 100, 10, fees=10),
                tx("2025-02-10", "PETR4", "sell", 100, 12, fees=5)], hoje=HOJE)
    assert mes(r, "2025-02")["cestas"]["comum"]["ganho_isento"] == Decimal("185")


@pytest.mark.parametrize("ticker,classe,esperado", [
    ("PETR4", "stock", "acao"), ("TAEE11", "stock", "acao"), ("AAPL34", "stock", "bdr"),
    ("HGLG11", "reit", "fii"), ("BOVA11", "etf", "etf"), ("BTC", "crypto", None),
    ("GMAT1", "stock", "direito"), ("ITSA2", "stock", "direito"),
])
def test_tipo_fiscal(ticker, classe, esperado):
    assert tipo_fiscal(ticker, classe) == esperado


def test_bdr_nao_entra_na_isencao():
    r = apurar([tx("2025-01-02", "AAPL34", "buy", 100, 50),
                tx("2025-02-10", "AAPL34", "sell", 100, 60)], hoje=HOJE)
    m = mes(r, "2025-02")
    assert m["cestas"]["comum"]["ganho_isento"] == 0
    assert m["imposto"] == Decimal("150")


def test_direito_recebido_tem_custo_zero_e_nao_isenta():
    r = apurar([tx("2025-03-10", "GMAT1", "sell", 1000, 2)], hoje=HOJE)   # vendas 2.000
    m = mes(r, "2025-03")
    c = m["cestas"]["comum"]
    assert not m["incompleto"] and c["sem_custo"] == []
    assert c["ganho_isento"] == 0 and m["vendas_acoes"] == 0
    assert c["base"] == Decimal("2000") and m["imposto"] == Decimal("300")


def test_direito_comprado_usa_o_custo_da_compra():
    r = apurar([tx("2025-03-03", "GMAT1", "buy", 1000, 1.5),
                tx("2025-03-10", "GMAT1", "sell", 1000, 2)], hoje=HOJE)
    assert mes(r, "2025-03")["cestas"]["comum"]["base"] == Decimal("500")


def test_direito_de_fii_tem_custo_zero_na_cesta_fii():
    r = apurar([tx("2025-03-10", "HGLG12", "sell", 10, 5, classe="reit")], hoje=HOJE)
    c = mes(r, "2025-03")["cestas"]["fii"]
    assert c["sem_custo"] == [] and c["imposto"] == Decimal("10")


def test_classe_fora_do_escopo_vira_alerta():
    r = apurar([tx("2025-01-02", "BTC", "buy", 1, 100, classe="crypto")], hoje=HOJE)
    assert r["meses"] == []
    assert r["alertas"][0]["type"] == "fora_do_escopo"


def test_vencimento_no_ultimo_dia_util_do_mes_seguinte():
    assert vencimento_darf(2026, 1) == date(2026, 2, 27)    # 28/02 é sábado
    assert vencimento_darf(2025, 12) == date(2026, 1, 30)
    assert vencimento_darf(2026, 3) == date(2026, 4, 30)
    assert vencimento_darf(2024, 2) == date(2024, 3, 28)    # 29/03/2024 é sexta santa


def test_darf_pendente_e_o_do_mes_fechado_ainda_nao_vencido():
    r = apurar([tx("2026-08-03", "PETR4", "buy", 2000, 10),
                tx("2026-08-20", "PETR4", "sell", 2000, 12.5)], hoje=HOJE)
    assert r["pendente"]["mes"] == "2026-08"
    assert r["pendente"]["vencimento"] == date(2026, 9, 30)


# ── Tela ─────────────────────────────────────────────────────────────────────

def _tela_com(monkeypatch, res, escolha=None) -> dict:
    """Roda a tela com a apuração ``res`` e devolve o que ela desenhou.
    ``escolha`` é a opção do seletor de ano; None fica com a sugerida."""
    import views.ir_renda_variavel as tela

    monkeypatch.setattr(tela, "_dados", lambda: {
        "apuracao": res, "transacoes": [], "eventos": [], "proventos": [], "posicoes": [],
        "emissores": {}, "mercado": {}, "etfs": set(), "nomes_fii": {}, "falhas": []})
    visto = {"avisos": [], "lacunas": [], "titulos": [], "cards": [], "tabelas": [],
             "opcoes": []}
    monkeypatch.setattr(tela.st, "warning", lambda msg, **k: visto["avisos"].append(msg))
    monkeypatch.setattr(tela, "aviso_lacuna", lambda msg, **k: visto["lacunas"].append(msg))
    monkeypatch.setattr(tela, "secao_titulo", lambda t, *a, **k: visto["titulos"].append(t))
    monkeypatch.setattr(tela, "card_metrica", lambda t, *a, **k: visto["cards"].append(t))
    monkeypatch.setattr(tela.st, "dataframe", lambda df, **k: visto["tabelas"].append(df))

    def seletor(rotulo, opcoes, index=0, **k):
        visto["opcoes"] = [k["format_func"](o) for o in opcoes]
        return opcoes[index] if escolha is None else escolha

    monkeypatch.setattr(tela.st, "selectbox", seletor)
    tela.render()
    return visto


def _meses_na_tela(visto: dict) -> list[str]:
    return [m for df in visto["tabelas"] if "Mês" in df.columns for m in df["Mês"]]


def test_tela_renderiza_com_mes_incompleto_e_darf_pendente(monkeypatch):
    import views.ir_renda_variavel as tela

    res = apurar([tx("2025-01-10", "BBAS3", "sell", 1000, 30),
                  tx("2026-08-03", "PETR4", "buy", 2000, 10),
                  tx("2026-08-20", "PETR4", "sell", 2000, 12.5)], hoje=date.today())
    visto = _tela_com(monkeypatch, res)
    # Venda sem custo vai para o administrador, sem faixa amarela na tela
    assert not any("BBAS3" in a for a in visto["avisos"])
    assert any("BBAS3" in x for x in visto["lacunas"])
    df = tela._tabela_mensal(res["meses"])
    assert "Incompleto: venda sem custo" in set(df["Situação"])
    assert tela._tabela_anual(res["anos"])["Meses incompletos"].sum() == 1


def _dois_anos():
    ano = date.today().year - 1
    return ano, apurar([tx(f"{ano - 1}-03-10", "PETR4", "buy", 2000, 10),
                        tx(f"{ano - 1}-05-20", "PETR4", "sell", 1000, 12.5),
                        tx(f"{ano}-05-20", "PETR4", "sell", 1000, 13)], hoje=date.today())


def test_ano_escolhido_recorta_a_tela_inteira(monkeypatch):
    ano, res = _dois_anos()
    visto = _tela_com(monkeypatch, res)
    assert _meses_na_tela(visto) == [f"mai/{ano}"]
    assert f"Declaração IRPF {ano + 1}" in visto["titulos"]
    assert "Resumo por ano" not in visto["titulos"]
    # Ano encerrado: os números de 31/12, não os do mês corrente
    assert f"DARFs de {ano}" in visto["cards"] and f"Prejuízo em 31/12/{ano}" in visto["cards"]
    assert "Mês em curso" not in visto["cards"]
    assert visto["opcoes"][-1] == "Visão geral · todos os anos"


def test_ano_fechado_com_darf_de_dezembro_a_vencer_mantem_o_total_do_ano(monkeypatch):
    # Em janeiro, o DARF de dezembro ainda não venceu: o card segue no total do ano
    ano = date.today().year - 1
    res = apurar([tx(f"{ano}-03-10", "PETR4", "buy", 2000, 10),
                  tx(f"{ano}-12-10", "PETR4", "sell", 2000, 15)],
                 hoje=date(ano + 1, 1, 15))
    assert res["pendente"]["mes"] == f"{ano}-12"
    visto = _tela_com(monkeypatch, res)
    assert f"DARFs de {ano}" in visto["cards"]
    assert "DARF em aberto" not in visto["cards"]


def test_visao_geral_junta_todos_os_anos_sem_as_fichas(monkeypatch):
    import views.ir_renda_variavel as tela

    ano, res = _dois_anos()
    visto = _tela_com(monkeypatch, res, escolha=tela._VISAO_GERAL)
    assert _meses_na_tela(visto) == [f"mai/{ano}", f"mai/{ano - 1}"]
    assert "Resumo por ano" in visto["titulos"]
    assert not any(t.startswith("Declaração IRPF") for t in visto["titulos"])
    assert "Mês em curso" in visto["cards"]


def test_investimentos_tem_a_aba_de_imposto():
    import inspect

    import views.investimentos as inv
    fonte = inspect.getsource(inv.render)
    assert "🧾  Imposto de Renda" in fonte
    assert "views.ir_renda_variavel" in fonte


def test_aba_de_imposto_e_a_ultima():
    import inspect

    import views.investimentos as inv
    fonte = inspect.getsource(inv.render)
    rotulos = fonte[fonte.index("st.tabs(["):]
    rotulos = rotulos[:rotulos.index("]")]
    assert rotulos.rstrip().rstrip(",").endswith('"🧾  Imposto de Renda"')
