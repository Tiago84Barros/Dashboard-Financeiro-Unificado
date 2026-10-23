"""Fichas do IRPF a partir da apuração (core/irpf_declaracao.py).

Dados sintéticos: nenhum valor aqui vem de carteira real.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from core.ir_renda_variavel import _classe_fiscal, apurar, tipo_fiscal
from core.irpf_declaracao import (
    _discriminacao_rf, _limpar, bens_posicao_consolidada, bens_renda_variavel,
    ficha_fii, ficha_renda_variavel, montar, rendimentos,
)

HOJE = date(2026, 9, 24)
CORTES = ("2024-12-31", "2025-12-31")
EMISSORES = {
    "PETR4": ["33.000.167/0001-01", "PETROLEO BRASILEIRO S.A. PETROBRAS"],
    "SAPR11": ["76.484.013/0001-45", "CIA SANEAMENTO DO PARANA - SANEPAR"],
    "ITUB4": ["60.872.504/0001-23", "ITAU UNIBANCO HOLDING S.A."],
}
MERCADO = {"HGLG11": ("11.728.688/0001-47", "CSHG LOGISTICA FII")}


def tx(dia, ticker, lado, qtd, preco, classe="stock", fees=0):
    return {"transaction_date": date.fromisoformat(dia), "ticker": ticker,
            "classe": classe, "type": lado, "quantity": qtd,
            "unit_price": preco, "fees": fees}


def ap(transacoes):
    return apurar(transacoes, hoje=HOJE, cortes=CORTES)


def prov(dia, ticker, tipo, valor, nome=None):
    return {"data": date.fromisoformat(dia), "ticker": ticker, "tipo": tipo,
            "valor": valor, "nome": nome}


# ── Renda variável ───────────────────────────────────────────────────────────

def test_ficha_rv_tem_12_meses_em_centavos_e_repete_o_prejuizo():
    r = ap([tx("2025-01-10", "PETR4", "buy", 3000, 10),
            tx("2025-03-10", "PETR4", "sell", 3000, 9)])       # -3.000 em 27.000 vendidos
    f = ficha_renda_variavel(r, 2025)
    assert [m["rotulo"] for m in f][:3] == ["JAN", "FEV", "MAR"] and len(f) == 12
    mar = f[2]["comum"]
    assert mar["mercado_vista_acoes"] == Decimal("-3000.00")
    assert mar["prejuizo_compensar"] == Decimal("3000.00")
    assert mar["aliquota"] == Decimal("0.15")
    # Mês sem operação carrega o prejuízo, como o programa
    assert f[3]["comum"]["resultado_negativo_anterior"] == Decimal("3000.00")
    assert f[11]["comum"]["prejuizo_compensar"] == Decimal("3000.00")
    # Nada de 0E-24: tudo com duas casas
    assert all(str(v).count(".") <= 1 and "E" not in str(v)
               for m in f for v in m["consolidacao"].values())
    # IRRF de 0,005% sobre 27.000 = 1,35 sem imposto para abater -> Pago/Retido 03
    assert f[2]["consolidacao"]["irrf_lei_mes"] == Decimal("1.35")
    d = montar(r, 2025, [], [], EMISSORES, MERCADO, set())
    assert d["pago_retido"]["codigo"] == "03"
    assert d["pago_retido"]["valor"] == Decimal("1.35")


def test_ganho_isento_vai_para_isentos_20_e_nao_para_a_ficha():
    r = ap([tx("2025-01-10", "PETR4", "buy", 1000, 10),
            tx("2025-02-10", "PETR4", "sell", 1000, 15)])
    d = montar(r, 2025, [], [], EMISSORES, MERCADO, set())
    assert d["renda_variavel"][1]["comum"]["mercado_vista_acoes"] == Decimal("0.00")
    assert d["isentos"]["acoes_ate_20mil"]["valor"] == Decimal("5000.00")
    assert d["isentos"]["acoes_ate_20mil"]["codigo"] == "20"


def test_ficha_fii_carrega_o_prejuizo_para_o_mes_seguinte():
    r = ap([tx("2025-01-10", "HGLG11", "buy", 100, 160, classe="reit"),
            tx("2025-04-10", "HGLG11", "sell", 100, 150, classe="reit")])
    f = ficha_fii(r, 2025)
    assert f[3]["linhas"]["resultado_liquido"] == Decimal("-1000.00")
    assert f[4]["linhas"]["resultado_negativo_anterior"] == Decimal("1000.00")
    assert f[3]["linhas"]["aliquota"] == Decimal("0.20")


# ── Rendimentos ──────────────────────────────────────────────────────────────

def test_rendimentos_por_ficha_e_fonte_pagadora():
    ps = [
        prov("2025-03-01", "PETR4", "dividend", 100),
        prov("2025-06-01", "PETR3", "dividend", 50),      # mesma empresa, outro código
        prov("2025-03-01", "ITUB4", "jcp", 85),
        prov("2025-05-15", "HGLG11", "reit_income", 110),
        prov("2025-05-15", "SAPR11", "reit_income", 40),   # unit: termina em 11, não é FII
        prov("2025-08-01", "4606422UNA", "other", 300,
             nome="4606422UNA - PATRIA INFRAESTRUTURA FUNDO DE INV EM PARTICIPACOES"),
        prov("2025-09-01", "HGLG11", "amortization", 20),
        prov("2024-12-30", "PETR4", "dividend", 999),      # outro ano
    ]
    r = rendimentos(ps, 2025, EMISSORES, MERCADO, set())
    div = r["dividendos"]["linhas"]
    assert len(div) == 1 and div[0]["valor"] == Decimal("150.00")
    assert div[0]["cnpj"] == "33.000.167/0001-01" and div[0]["tickers"] == ["PETR3", "PETR4"]
    assert r["jcp"]["total"] == Decimal("85.00")
    assert r["fii"]["linhas"][0]["cnpj"] == "11.728.688/0001-47"
    assert r["fii"]["total"] == Decimal("110.00")
    assert r["fip_ie"]["total"] == Decimal("300.00")
    assert r["fip_ie"]["linhas"][0]["nome"].startswith("PATRIA INFRAESTRUTURA")
    # "Rendimento" de ação não se adivinha
    assert [(x["tickers"], x["tipo"]) for x in r["a_classificar"]["linhas"]] == [
        (["SAPR11"], "reit_income")]
    assert r["fora"] == [{"ticker": "HGLG11", "tipo": "amortization", "valor": Decimal("20.00")}]


# ── Bens e Direitos ──────────────────────────────────────────────────────────

def test_bens_renda_variavel_custo_medio_e_vendido_no_ano():
    r = ap([tx("2024-05-10", "PETR4", "buy", 100, 30),
            tx("2025-02-10", "PETR4", "sell", 100, 35),
            tx("2025-03-10", "SAPR11", "buy", 10, 25)])
    bens = {b["ticker"]: b for b in bens_renda_variavel(r["fotos"], 2025, EMISSORES, MERCADO)}
    assert (bens["PETR4"]["grupo"], bens["PETR4"]["codigo"]) == ("03", "01")
    assert bens["PETR4"]["situacao_anterior"] == Decimal("3000.00")
    assert bens["PETR4"]["situacao_atual"] == Decimal("0.00")
    assert bens["SAPR11"]["situacao_atual"] == Decimal("250.00")
    assert "COM O TOTAL DE 10 ACOES" in bens["SAPR11"]["discriminacao"]
    assert bens["SAPR11"]["cnpj"] == "76.484.013/0001-45"


def test_conferencia_de_custodia_troca_de_codigo_e_ausencia():
    r = ap([tx("2024-05-10", "VELH3", "buy", 30, 10),
            tx("2024-05-10", "NOVO3", "buy", 5, 10),
            tx("2024-05-10", "SUMI3", "buy", 7, 10)])
    pos = [{"data": date(2025, 12, 31), "ticker": "NOVO3", "nome": "", "tipo": "stock",
            "quantidade": 35, "investido": 0, "instituicao": ""}]
    d = montar(r, 2025, [], pos, {}, {}, set())
    assert d["custodia_maior"] == [{"ticker": "NOVO3", "custodia": 35, "apurado": Decimal("5"),
                                    "troca_de_codigo": "VELH3"}]
    assert [c["ticker"] for c in d["custodia_menor"]] == ["SUMI3"]


def test_custodia_que_bate_tira_a_marca_de_sem_custo():
    # Venda sem custo em 2023, recompra em 2024: a foto de 2025 tem custo
    r = ap([tx("2023-03-10", "PETR4", "sell", 50, 30),
            tx("2024-05-10", "PETR4", "buy", 20, 30)])
    pos = [{"data": date(2025, 12, 31), "ticker": "PETR4", "nome": "", "tipo": "stock",
            "quantidade": 20, "investido": 0, "instituicao": ""}]
    sem = montar(r, 2025, [], [], EMISSORES, {}, set())
    com = montar(r, 2025, [], pos, EMISSORES, {}, set())
    assert sem["bens"][0]["sem_custo"] is True
    assert com["bens"][0]["sem_custo"] is False


def test_renda_fixa_pela_posicao_consolidada():
    pos = [
        {"data": date(2025, 12, 31), "ticker": "TIPCA2035", "nome": "Tesouro IPCA+ 2035",
         "tipo": "fixed_income", "quantidade": Decimal("1.5"), "investido": 4000,
         "instituicao": "XP INVESTIMENTOS CCTVM S/A"},
        {"data": date(2025, 12, 31), "ticker": "CDBXYZ", "nome": "CDB � BANCO PETRO S/A",
         "tipo": "fixed_income", "quantidade": 1, "investido": 1000, "instituicao": "XP"},
    ]
    emissores = {"BPET3": ["11.111.111/0001-11", "BANCO PETRO S.A."]}
    bens = {b["ticker"]: b for b in bens_posicao_consolidada(pos, 2025, emissores)}
    assert bens["TIPCA2035"]["discriminacao"] == "NTN-B PRINCIPAL 15/05/2035"
    assert bens["TIPCA2035"]["cnpj"] == "02.332.886/0001-04"
    assert (bens["TIPCA2035"]["grupo"], bens["TIPCA2035"]["codigo"]) == ("04", "02")
    assert bens["CDBXYZ"]["cnpj"] == "11.111.111/0001-11"
    assert bens["CDBXYZ"]["situacao_anterior"] == Decimal("0.00")


def test_limpar_e_discriminacao():
    assert _limpar("CDB � BANCO BMG S/A") == "CDB - BANCO BMG S/A"
    assert _limpar("Ações  Ordinárias") == "ACOES ORDINARIAS"
    assert _discriminacao_rf("TPRE2027", "") == "LTN 01/01/2027"
    assert _discriminacao_rf("TSELIC2029", "") == "LFT 01/03/2029"


# ── Classificação fiscal ─────────────────────────────────────────────────────

def test_codigo_fora_da_b3_nao_e_renda_variavel():
    assert tipo_fiscal("CDB123GD5SV", "stock") is None
    assert tipo_fiscal("PETR4", "stock") == "acao"


def test_classe_fiscal_unit_e_etf():
    assert _classe_fiscal("SAPR11", "reit", set(), {"SAPR11"}) == "stock"
    assert _classe_fiscal("BOVA11", "stock", {"BOVA11"}, set()) == "etf"
    assert _classe_fiscal("HGLG11", "reit", set(), {"SAPR11"}) == "reit"


def test_publicar_cnpj_emissores_monta_do_fca_e_do_cadastro():
    from scripts.publicar_cnpj_emissores import formatar_cnpj, montar as montar_cnpj
    from tests.test_cvm_cadastro import _CAD, _FCA

    out = montar_cnpj(_CAD, {2026: _FCA})
    assert out["PETR4"] == ["33.000.167/0001-01", "PETROLEO BRASILEIRO S.A. PETROBRAS"]
    assert set(out) == {"PETR3", "PETR4", "ITUB4"}
    assert formatar_cnpj("191") == "00.000.000/0001-91"


# ── Tela ─────────────────────────────────────────────────────────────────────

def _insumos(transacoes, proventos=(), posicoes=()):
    return {"apuracao": apurar(transacoes, hoje=date.today(),
                               cortes=tuple(f"{a}-12-31" for a in range(2023, date.today().year + 1))),
            "transacoes": transacoes, "eventos": [], "proventos": list(proventos),
            "posicoes": list(posicoes), "emissores": EMISSORES, "mercado": MERCADO,
            "etfs": set(), "nomes_fii": {}, "falhas": []}


def test_tela_monta_as_fichas_com_os_rotulos_do_programa(monkeypatch):
    import views.ir_renda_variavel as tela

    ano = date.today().year - 1
    ins = _insumos([tx(f"{ano}-01-10", "PETR4", "buy", 3000, 10),
                    tx(f"{ano}-03-10", "PETR4", "sell", 3000, 12)],
                   [prov(f"{ano}-05-01", "SAPR11", "reit_income", 40)])
    monkeypatch.setattr(tela, "_dados", lambda: ins)
    tela.render()

    d = montar(ins["apuracao"], ano, ins["proventos"], [], EMISSORES, MERCADO, set())
    t = tela.ficha_rv_tabelas(d["renda_variavel"])
    linhas = list(t["comum"]["Linha da ficha"])
    assert linhas[0] == "✍️ Mercado à vista – ações"
    assert "Resultado negativo até o mês anterior (✍️ só em JAN)" in linhas
    assert t["comum"].loc[0, "MAR"] == "6.000,00"
    assert t["comum"].loc[5, "MAR"] == "15%"
    assert "✍️ Imposto pago" in list(t["consolidacao"]["Linha da ficha"])
    conferir = tela._conferencias(d)
    assert any("SAPR11" in c and "Rendimento" in c for c in conferir)
    # Comprado e vendido no mesmo ano: zero nas duas datas, nada a declarar em Bens
    assert d["bens"] == [] and tela.tabela_bens(d["bens"], ano).empty
