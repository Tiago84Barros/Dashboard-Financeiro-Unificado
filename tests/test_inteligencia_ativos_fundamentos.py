"""Camada de fundamentos da Inteligência dos Ativos.

Regras sob teste: catálogo próprio por classe; ausência vira
"Dado não disponível." (nunca estimativa); DADO separado de INTERPRETAÇÃO;
os leitores só traduzem o que a fonte tem.
"""
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from core.inteligencia_ativos import fontes_fundamentos as ff
from core.inteligencia_ativos import fundamentos as f
from core.inteligencia_ativos import modelos as m
from core.inteligencia_ativos import secoes

# -- catálogo e montagem ------------------------------------------------------------

def test_cada_classe_tem_o_seu_catalogo():
    chaves = {t: {mt.chave for mt in f.CATALOGO[t]} for t in f.CATALOGO}
    assert {"roe", "roic", "divida_liquida_ebitda", "cobertura_juros",
            "guidance"} <= chaves[f.ACAO]
    assert {"p_vp", "vacancia_fisica", "vacancia_financeira", "wault",
            "revisional", "inadimplencia"} <= chaves[f.FII]
    assert {"duration", "cobertura_fgc", "taxa_mercado",
            "marcacao_mercado"} <= chaves[f.RENDA_FIXA]
    assert {"expense_ratio", "tracking_error",
            "exposicao_setorial"} <= chaves[f.ETF]
    assert "p_vp" not in chaves[f.ACAO] and "roe" not in chaves[f.FII]


@pytest.mark.parametrize("classe,moeda,tipo", [
    ("FII", "BRL", f.FII), ("Ações BR", "BRL", f.ACAO), ("BDR", "BRL", f.ACAO),
    ("Tesouro Direto", "BRL", f.RENDA_FIXA), ("Renda Fixa", "BRL", f.RENDA_FIXA),
    ("ETF", "BRL", f.ETF), ("ETF Internacional", "USD", f.ETF),
    ("Ações EUA", "USD", f.ACAO), ("Cripto", "USD", None), ("Outros", "BRL", None),
])
def test_tipo_do_ativo(classe, moeda, tipo):
    assert f.tipo_do_ativo(classe, moeda) == tipo


def test_ausente_sai_como_dado_nao_disponivel():
    fund = f.montar(f.FII, {"p_vp": f.Dado(0.95, "fonte X", "2026-08-31")})
    assert fund.indicador("p_vp").texto() == "0,95x"
    assert fund.indicador("wault").texto() == f.NAO_DISPONIVEL == "Dado não disponível."
    assert len(fund.disponiveis) == 1 and len(fund.ausentes) == 13
    assert fund.fontes == ("fonte X",)


def test_valor_vazio_ou_nan_nao_conta_como_dado():
    fund = f.montar(f.ACAO, {"roe": f.Dado(float("nan"), "x"),
                             "guidance": f.Dado("  ", "x")})
    assert not fund.disponiveis


def test_montar_recusa_metrica_de_outra_classe():
    with pytest.raises(ValueError, match="p_vp"):
        f.montar(f.ACAO, {"p_vp": f.Dado(1.0, "x")})


def test_classe_sem_catalogo():
    fund = f.montar(None, {})
    assert fund.tipo is None and not fund.indicadores
    assert "sem catálogo" in f.resumo(fund)


def test_resumo_sem_nenhum_dado():
    assert f.resumo(f.montar(f.ETF, {})) == (
        "ETF: nenhum dos 8 indicadores da classe tem dado disponível.")


def test_texto_separa_dado_de_interpretacao():
    fund = f.montar(f.ACAO, {"roe": f.Dado(18.4, "Métricas B3", "2025-12-31")})
    t = f.texto(fund, "WEGE3")
    dado, interp = t.split("[INTERPRETAÇÃO")
    assert "[DADO" in dado and "ROE: 18,4%" in dado and "ref. 2025-12-31" in dado
    assert "Guidance: Dado não disponível." in dado
    assert "Dado não disponível" not in interp  # a interpretação só pergunta
    assert "ROE" in interp


def test_de_dict_reconstroi_o_que_como_dict_guardou():
    fund = f.montar(f.RENDA_FIXA, {"vencimento": f.Dado("2035-05-15", "T", "r", "n")},
                    moeda="BRL")
    assert f.Fundamentos.de_dict(fund.como_dict()) == fund


def test_formatacao_de_moeda():
    assert f.formatar(1.5e9, f.MOEDA, "USD") == "US$ 1,50 bi"
    assert f.formatar(-2.3e6, f.MOEDA) == "R$ -2,30 mi"


# -- FII -----------------------------------------------------------------------------

def _linha_fii(**extra):
    base = {"ticker": "HGLG11", "pvp": 0.93, "dy_12m": 0.087,
            "vacancia_fisica": 0.052, "vacancia_ref_date": "2026-07-31",
            "vacancia_financeira": None, "delinquency": 0.01,
            "tenant_concentration": 0.18, "regions": {"SP": 0.7, "MG": 0.3},
            "lease_expiry_concentration_24m": 0.25,
            "contract_profile_text": ["T�pico At�pico", "-"],
            "wault_anos": 4.2, "shares_outstanding": 33_000_000,
            "leverage": 0.04, "property_count": 21,
            "metric_metadata": {"pvp": {"reference_date": "2026-08-29",
                                        "source": "b3"}}}
    base.update(extra)
    return base


def test_dados_fii_traduz_o_snapshot():
    d = ff.dados_fii(_linha_fii())
    assert d["p_vp"].valor == 0.93 and d["p_vp"].referencia == "2026-08-29"
    assert "origem b3" in d["p_vp"].fonte
    assert d["dividend_yield"].valor == pytest.approx(8.7)
    assert d["vacancia_fisica"].referencia == "2026-07-31"
    assert d["ocupacao"].valor == pytest.approx(94.8)
    assert "vacância física" in d["ocupacao"].nota
    assert "vacancia_financeira" not in d  # ausente não é zero
    assert d["concentracao_geografica"].valor == "SP 70,0%, MG 30,0%"
    assert "Típico + Atípico" in d["prazo_contratos"].valor  # mojibake traduzido
    assert "revisional" not in d  # nenhuma fonte publica
    fund = f.montar(f.FII, d)  # tudo dentro do catálogo
    assert fund.indicador("revisional").texto() == f.NAO_DISPONIVEL


def test_fii_sem_vacancia_nao_inventa_ocupacao():
    d = ff.dados_fii(_linha_fii(vacancia_fisica=None))
    assert "ocupacao" not in d and "vacancia_fisica" not in d


def test_ler_fii_ticker_fora_do_snapshot(monkeypatch):
    import core.market_read as mr
    monkeypatch.setattr(mr, "load_fii_methodology_inputs",
                        lambda: pd.DataFrame([_linha_fii()]))
    assert ff.ler_fii("hglg11")["p_vp"].valor == 0.93
    assert ff.ler_fii("XPTO11") == {}


# -- renda fixa ---------------------------------------------------------------------

def _titulo(chave, **extra):
    base = dict(security_key=chave, titulo="Tesouro Prefixado 2029",
                vencimento=date(2029, 1, 1), report_date=date(2026, 8, 31),
                lotes=[SimpleNamespace(taxa_contratada=SimpleNamespace(
                    indexador="PRE", valor=0.1234))],
                avaliacoes=[SimpleNamespace(du_restante=504,
                                            data_avaliacao=date(2026, 9, 25))],
                taxa_mercado_venda=0.1301, data_curva=date(2026, 9, 25),
                ganho_mtm_reais=-150.0, mtm_pct=-0.012, indexador="PRE",
                marcado_a_mercado=True)
    base.update(extra)
    return SimpleNamespace(**base)


def test_titulo_da_posicao_exato_familia_e_ambiguo():
    principal, cupom = _titulo("TIPCA2032"), _titulo("TIPCAJ2032")
    assert ff.titulo_da_posicao("TIPCA2032", [principal, cupom]) is principal
    assert ff.titulo_da_posicao("TIPCA2032", [cupom]) is cupom  # único da família
    assert ff.titulo_da_posicao("TIPCAX2032", [principal, cupom]) is None
    assert ff.titulo_da_posicao("TPRE2032", [principal]) is None


_TESOURO = {"ticker": "TPRE2029", "nome": "Tesouro Prefixado 2029",
            "classe": "Tesouro Direto", "moeda": "BRL"}


def test_tesouro_com_titulo():
    d = ff.dados_renda_fixa(_TESOURO, _titulo("TPRE2029"))
    from core.inteligencia_ativos import calculos
    assert d["emissor"].valor == calculos.TESOURO_NACIONAL
    assert "não é rating" in d["risco_credito"].nota
    assert "Não se aplica" in d["cobertura_fgc"].valor
    assert d["indexador"].valor == "Prefixado"
    assert d["vencimento"].valor == "2029-01-01"
    assert d["duration"].valor == pytest.approx(2.0)
    assert d["taxa_contratada"].valor == "12,34% a.a."
    assert d["taxa_mercado"].valor == "13,01% a.a."
    assert d["marcacao_mercado"].valor == -150.0
    assert "-1,20%" in d["marcacao_mercado"].nota
    f.montar(f.RENDA_FIXA, d)


def test_duration_so_sem_cupom():
    com_cupom = _titulo("TIPCAJ2035", titulo="Tesouro IPCA+ com Juros Semestrais",
                        indexador="IPCA")
    assert "duration" not in ff.dados_renda_fixa(_TESOURO, com_cupom)
    selic = _titulo("TSELIC2029", indexador="SELIC")
    assert "duration" not in ff.dados_renda_fixa(_TESOURO, selic)


def test_tesouro_sem_marcacao_nao_publica_ganho():
    d = ff.dados_renda_fixa(_TESOURO, _titulo("TPRE2029", marcado_a_mercado=False))
    assert "marcacao_mercado" not in d


def test_fgc_pela_natureza_do_papel():
    cdb = {"ticker": "CDB BANCO X", "nome": "CDB Banco X 110% CDI",
           "classe": "Renda Fixa", "moeda": "BRL"}
    cri = {"ticker": "CRI ABC", "nome": "CRI ABC IPCA+", "classe": "Renda Fixa",
           "moeda": "BRL"}
    d_cdb, d_cri = ff.dados_renda_fixa(cdb), ff.dados_renda_fixa(cri)
    assert "coberto pelo FGC" in d_cdb["cobertura_fgc"].valor
    assert "Sem cobertura" in d_cri["cobertura_fgc"].valor
    assert "emissor" not in d_cdb  # o banco não está na carteira
    assert "vencimento" not in d_cdb and "taxa_contratada" not in d_cdb


# -- ETF ------------------------------------------------------------------------------

def test_etf_tudo_indisponivel():
    fund = f.montar(f.ETF, ff.dados_etf({"ticker": "IVVB11"}))
    assert not fund.disponiveis
    assert all(i.texto() == f.NAO_DISPONIVEL for i in fund.indicadores)


# -- ações ---------------------------------------------------------------------------

def _demo(receitas, lucros):
    anos = range(2025 - len(receitas) + 1, 2026)
    return pd.DataFrame({
        "Data": [pd.Timestamp(a, 12, 31) for a in anos],
        "Receita_Liquida": receitas, "Lucro_Liquido": lucros,
        "FCO": 50.0, "FCF": 30.0, "Divida_Total": 300.0,
        "Divida_Liquida": 200.0, "EBITDA": 100.0})


def test_acao_b3_demonstracoes_e_multiplos():
    mult = pd.Series({"Margem_Operacional": 0.2, "Margem_Liquida": 0.1,
                      "ROE": 0.15, "ROIC": 0.12, "Payout": 0.5, "DY": 0.06,
                      "data": pd.Timestamp(2026, 6, 30)})
    d = ff.dados_acao_b3(_demo([1000.0, 1100.0], [-10.0, 80.0]), mult)
    assert d["receita"].valor == 1100.0 and d["receita"].referencia == "exercício 2025"
    assert d["crescimento_receita"].valor == pytest.approx(10.0)
    assert "crescimento_lucro" not in d  # base negativa: sem leitura
    assert d["divida_liquida_ebitda"].valor == pytest.approx(2.0)
    assert d["roe"].valor == pytest.approx(15.0)
    assert "antes de impostos" in d["roic"].nota
    assert d["roe"].referencia == "2026-06-30"
    for ausente in ("margem_bruta", "cobertura_juros", "guidance", "dividendos_12m"):
        assert ausente not in d
    f.montar(f.ACAO, d)


def test_acao_b3_anos_nao_consecutivos_sem_crescimento():
    demo = _demo([1000.0, 1100.0], [10.0, 20.0])
    demo.loc[0, "Data"] = pd.Timestamp(2022, 12, 31)
    assert "crescimento_receita" not in ff.dados_acao_b3(demo, None)


def test_acao_b3_sem_nada():
    assert ff.dados_acao_b3(pd.DataFrame(), pd.Series(dtype=float)) == {}


def test_acao_eua():
    metricas = {"_revenue": 3.9e11, "_net_income": 9.4e10, "gross_margin": 0.46,
                "roic": 0.55, "interest_coverage": 29.0, "revenue_cagr_3y": None}
    fin = pd.DataFrame({"fiscal_year": [2024, 2025],
                        "operating_cash_flow": [1.1e11, 1.2e11],
                        "total_debt": [1.0e11, 9.0e10]})
    d = ff.dados_acao_eua(metricas, fin)
    assert d["margem_bruta"].valor == pytest.approx(46.0)
    assert d["cobertura_juros"].valor == 29.0
    assert d["fluxo_caixa_operacional"].valor == 1.2e11
    assert d["receita"].referencia == "exercício 2025"
    assert "crescimento_receita" not in d and "guidance" not in d
    fund = f.montar(f.ACAO, d, moeda="USD")
    assert fund.indicador("receita").texto("USD") == "US$ 390,00 bi"


def test_ler_despacha_pela_classe(monkeypatch):
    chamadas = []
    monkeypatch.setattr(ff, "ler_acao_b3", lambda t: chamadas.append(("b3", t)) or {})
    monkeypatch.setattr(ff, "ler_acao_eua", lambda t: chamadas.append(("eua", t)) or {})
    monkeypatch.setattr(ff, "ler_fii", lambda t: chamadas.append(("fii", t)) or {})
    ff.ler("WEGE3", "WEG", "Ações BR", "BRL")
    ff.ler("AAPL", "Apple", "Ações EUA", "USD")
    ff.ler("HGLG11", "CSHG Log", "FII", "BRL")
    assert chamadas == [("b3", "WEGE3"), ("eua", "AAPL"), ("fii", "HGLG11")]
    assert ff.ler("BTC", "Bitcoin", "Cripto", "USD").tipo is None


# -- seção e tela -------------------------------------------------------------------

def _info(classe="FII", ticker="HGLG11", moeda="BRL"):
    return m.InfoBasica(ticker, ticker, classe, None, None, None, moeda,
                        None, None, 5.0)


def test_provedor_disponivel_e_sem_dados():
    cheio = secoes.provedor_fundamentos(
        _info(), None,
        leitor=lambda i: f.montar(f.FII, {"p_vp": f.Dado(0.9, "S")}))
    assert cheio.estado == m.DISPONIVEL and cheio.fonte == "S"
    assert cheio.dados["indicadores"][0]["texto"] == "0,90x"
    vazio = secoes.provedor_fundamentos(_info(), None,
                                        leitor=lambda i: f.montar(f.FII, {}))
    assert vazio.estado == m.SEM_DADOS and vazio.fonte is None


def test_leitor_que_quebra_vira_sem_dados(monkeypatch):
    def _quebra(info):
        raise RuntimeError("banco fora")
    monkeypatch.setattr(secoes, "_ler_fundamentos", _quebra)
    s = secoes.coletar(_info(), None)["fundamentos"]
    assert s.estado == m.SEM_DADOS


def test_corpo_fundamentos_so_com_tokens():
    from views import inteligencia_ativos as tela
    fund = f.montar(f.FII, {"p_vp": f.Dado(0.9, "Snapshot", "2026-08-29",
                                           "nota <b>")})
    html = tela.corpo_fundamentos(fund)
    assert "Dado · fornecido pelo sistema" in html and "Interpretação" in html
    assert "0,90x" in html and html.count("Dado não disponível.") >= 13
    assert "&lt;b&gt;" in html  # escapado
    assert "#" not in html.replace("&#", "")
