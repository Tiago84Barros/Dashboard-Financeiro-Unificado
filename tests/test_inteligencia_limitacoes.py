"""As três limitações declaradas no PR #422, fechadas em 01/10/2026.

Pedido do usuário: "Então resolva o problema das limitações".

1. Sentimento: o léxico do app não reconhecia 801 de 1.377 notícias, e o
   arquivo publicado descartava o tom que o provedor (Alpha Vantage) mede.
2. Momento de preço não entrava na avaliação.
3. Dívida líquida/EBITDA faltava na maior parte da B3 (a base contábil não
   tem EBITDA).
"""
from dataclasses import replace
from datetime import datetime, timezone

import pandas as pd

from core.inteligencia_ativos import avaliacao as av
from core.inteligencia_ativos import fontes_fundamentos as ff
from core.inteligencia_ativos import fundamentos as f
from core.inteligencia_ativos import informacoes as inf
from core.inteligencia_ativos import pares as p
from core.noticias import sentimento
from scripts import publish_valuation_historico as pv
from tests.test_inteligencia_avaliacao import (  # noqa: F401
    BOA,
    _com,
    _fund,
    _noticia,
    an,
)

# -- 1. sentimento ---------------------------------------------------------------------


def test_lexico_reconhece_vocabulario_de_mercado():
    assert sentimento.METODO == "lexico_app4_1.2.0"
    assert sentimento.calcular("Ação dispara após balanço", "pt") > 0
    assert sentimento.calcular("Banco eleva preço-alvo da WEG", "pt") > 0
    assert sentimento.calcular("Papel despenca e tomba na bolsa", "pt") < 0
    assert sentimento.calcular("Stock surges after upgrade", "en") > 0
    assert sentimento.calcular("Shares plunge on downgrade", "en") < 0


def test_lexico_nao_confunde_corte_de_juros_com_noticia_ruim():
    # "corta"/"reduz" saíram: corte de juros ou de custo não é notícia ruim
    assert sentimento.calcular("Empresa reduz custos", "pt") is None


def test_publicador_de_noticias_carrega_o_tom_do_provedor():
    from scripts import publish_informacoes_recentes as pub
    quando = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
    linhas = [{"titulo": "Vale (VALE3) amplia produção de minério",
               "publicado_em": quando, "entidades": {"tickers": ["VALE3"]},
               "url": "https://a", "veiculo": "A",
               "sentimento_api": 0.312345678, "rotulo_sentimento":
               "Somewhat-Bullish"},
              {"titulo": "Vale (VALE3) aprova dividendos bilionários", "publicado_em":
               quando, "entidades": {"tickers": ["VALE3"]}, "url": "https://b",
               "veiculo": "B", "sentimento_api": float("nan")}]
    itens = pub.agrupar_noticias(linhas, lambda t: ("VALE3",),
                                 lambda t: False)["VALE3"]["itens"]
    por_fonte = {i["source"]: i for i in itens}
    assert por_fonte["A"]["sentimento_api"] == 0.3123
    assert por_fonte["A"]["rotulo_sentimento"] == "Somewhat-Bullish"
    assert por_fonte["B"]["sentimento_api"] is None


def test_noticia_le_arquivo_novo_e_ignora_chave_desconhecida():
    item = {"headline": "X", "date": None, "source": None, "url": None,
            "impact_level": inf.LOW, "affected_dimension": [], "summary": None,
            "categoria": "resultado", "motivo": "m", "sentimento_api": 0.4,
            "chave_futura": 1}
    n = inf.Noticias.de_dict({"itens": [item]}).itens[0]
    assert n.sentimento_api == 0.4


def test_avaliacao_prefere_o_tom_do_provedor_e_conta_os_metodos(an):  # noqa: F811
    # O léxico leria positivo ("dispara"); o provedor diz negativo e vence.
    a = av.avaliar(_com(an["WEGE3"], _fund(**BOA), noticias=[
        replace(_noticia("Ação dispara"), sentimento_api=-0.4),
        _noticia("Lucro recorde e alta forte"),
    ]))
    crit = next(c for c in a.dimensao(av.MERCADO).criterios
                if "notícia(s) própria(s)" in c.texto)
    assert ("1 pelo provedor para o artigo inteiro, 1 pelo léxico"
            in crit.texto)
    assert "1 com provedor e léxico em sinais opostos" in crit.texto


def test_manchete_sem_idioma_detectado_tenta_os_dois_lexicos():
    n = _noticia("Shares surge")
    assert av._sentimento(n, "BRL") > 0


# -- 2. momento --------------------------------------------------------------------


def _precos(tk, valores, inicio="2025-09-01"):
    meses = pd.date_range(inicio, periods=len(valores), freq="MS")
    return pd.DataFrame({"ticker": tk, "mes": meses,
                         "data": [m + pd.Timedelta(days=20) for m in meses],
                         "preco": valores})


def test_momento_retorno_de_12_meses_com_referencia():
    df = _precos("AAA3", [10.0] * 10 + [11, 12, 13])
    mom, excl = pv.momento(df, pd.Timestamp("2026-10-01"))
    assert mom["AAA3"]["retorno_12m"] == 30.0
    assert mom["AAA3"]["retorno_3m"] == 30.0
    assert mom["AAA3"]["momento_ref"] == "2025-09-21 a 2026-09-21"
    assert excl == {"preco_velho": 0, "salto_na_janela": 0,
                    "historico_curto": 0}


def test_momento_recusa_preco_velho_salto_e_historico_curto():
    df = pd.concat([
        _precos("VELHO3", [10.0] * 13, inicio="2025-01-01"),
        _precos("SALTO3", [10.0] * 6 + [40.0] * 7),
        _precos("CURTO3", [10.0] * 5, inicio="2026-05-01"),
    ])
    mom, excl = pv.momento(df, pd.Timestamp("2026-10-01"))
    assert mom == {}
    assert excl == {"preco_velho": 1, "salto_na_janela": 1,
                    "historico_curto": 1}


def test_pares_comparam_retorno_por_pontos_percentuais():
    grupo = p.GrupoPares("Segmento", "X", tuple(
        p.Par(f"P{i}", None, 0.0, "", {"retorno_12m": r})
        for i, r in enumerate((1.0, 2.0, 3.0))))
    alvo = p.Candidato("AAA3", None, p.ACAO, "b3", metricas={"retorno_12m": 6.0})
    ln = p.comparar(alvo, grupo).linha("retorno_12m")
    # 6% contra mediana de 2%: tolerância relativa diria "acima"; 4 p.p. não
    assert ln.posicao == p.EM_LINHA and ln.diferenca_pct is None
    assert "5 p.p." in ln.interpretacao


def _com_pares(a, retorno, retornos_pares):
    grupo = p.GrupoPares("Segmento", "X", tuple(
        p.Par(f"P{i}", None, 0.0, "", {"retorno_12m": r})
        for i, r in enumerate(retornos_pares)))
    alvo = p.Candidato(a.ativo.ticker, None, p.ACAO, "b3",
                       metricas={"retorno_12m": retorno})
    cp = p.comparar(alvo, grupo)
    return replace(a, pares=replace(a.pares, dados=cp.como_dict()))


def test_momento_entra_no_mercado_contra_os_pares(an):  # noqa: F811
    forte = av.avaliar(_com_pares(an["WEGE3"], 43.3, (5.0, 10.0, 15.0)))
    crit = next(c for c in forte.dimensao(av.MERCADO).criterios
                if "Retorno de 12 meses" in c.texto)
    assert crit.sinal == 1 and "acima" in crit.texto and "3 pares" in crit.texto
    fraco = av.avaliar(_com_pares(an["CSNA3"], -23.3, (0.0, 5.0, 10.0)))
    crit = next(c for c in fraco.dimensao(av.MERCADO).criterios
                if "Retorno de 12 meses" in c.texto)
    assert crit.sinal == -1 and "abaixo" in crit.texto


def test_queda_forte_alerta_sem_eliminar(an):  # noqa: F811
    a = av.avaliar(_com_pares(an["WEGE3"], -40.0, (-38.0, -35.0, -42.0)))
    alerta = next(x for x in a.alertas if "caiu 40,0%" in x.texto)
    assert not alerta.critico


# -- 3. alavancagem B3 -------------------------------------------------------------


def _linha_brapi(tk, ebitda, divida, caixa, ebit=None, desp=None,
                 quando="2026-09-19"):
    anuais = ([{"type": "yearly", "endDate": "2025-12-31", "ebit": ebit,
                "financialExpenses": desp}] if ebit is not None else [])
    return {"ticker": tk, "fetched_at": datetime.fromisoformat(quando),
            "financial_data": {"ebitda": ebitda, "totalDebt": divida,
                               "totalCash": caixa}, "anuais": anuais}


def test_alavancagem_brapi_calcula_razao_e_cobertura():
    alav, excl = pv.alavancagem_brapi([
        _linha_brapi("CSNA3", 10.0, 80.0, 10.8, ebit=6.0, desp=-10.0),
        _linha_brapi("AMER3", -5.0, 50.0, 1.0),
        _linha_brapi("BBAS3", None, None, None),
        _linha_brapi("VELH3", 10.0, 5.0, 1.0, quando="2025-01-01"),
    ], datetime(2026, 10, 1))
    assert alav["CSNA3"]["divida_liquida_ebitda"] == 6.92
    assert alav["CSNA3"]["cobertura_juros"] == 0.6
    assert alav["AMER3"]["ebitda_negativo"] is True
    assert "BBAS3" not in alav and "VELH3" not in alav
    assert excl == {"foto_velha": 1, "sem_ebitda": 1}


def test_fundamentos_usam_brapi_so_quando_a_base_nao_tem():
    alav = {"divida_liquida_ebitda": 6.92, "cobertura_juros": 0.6,
            "alavancagem_ref": "brapi financialData de 2026-09-19",
            "cobertura_ref": "exercício encerrado em 2025-12-31"}
    d = ff.dados_acao_b3(None, None, alav)
    assert d["divida_liquida_ebitda"].valor == 6.92
    assert d["divida_liquida_ebitda"].fonte == ff.FONTE_B3_BRAPI
    assert d["cobertura_juros"].referencia == "exercício encerrado em 2025-12-31"


def test_ebitda_negativo_vira_alerta_eliminatorio(an):  # noqa: F811
    dados = ff.dados_acao_b3(None, None, {"ebitda_negativo": True,
                                          "alavancagem_ref": "brapi"})
    fund = f.montar(f.ACAO, {**{k: f.Dado(v, "teste", "2025")
                                for k, v in BOA.items()
                                if k != "divida_liquida_ebitda"},
                             **dados}).como_dict()
    a = av.avaliar(_com(an["WEGE3"], fund))
    assert a.qualidade == av.FRAGIL
    assert any("EBITDA de 12 meses negativo" in x.texto for x in a.criticos)


def test_alavancagem_da_brapi_dispara_as_reguas_existentes(an):  # noqa: F811
    dados = ff.dados_acao_b3(None, None, {"divida_liquida_ebitda": 6.92,
                                          "cobertura_juros": 0.6})
    fund = f.montar(f.ACAO, {**{k: f.Dado(v, "teste", "2025")
                                for k, v in BOA.items()
                                if k not in dados}, **dados}).como_dict()
    a = av.avaliar(_com(an["CSNA3"], fund))
    textos = " | ".join(x.texto for x in a.criticos)
    assert "alavancagem de 6,92x o EBITDA" in textos
    assert "não paga os juros" in textos


def _balanco(divida_total):
    # exercício anual sem EBITDA: a razão só pode vir da brapi
    return pd.DataFrame({"Data": [pd.Timestamp(2025, 12, 31)],
                         "Receita_Liquida": [4.343e9],
                         "Lucro_Liquido": [9.8e8],
                         "Divida_Total": [divida_total],
                         "Divida_Liquida": [None], "EBITDA": [None]})


# DIRR3 no arquivo de 01/10/2026: 6,44x sobre EBITDA de R$ 1,252 bi
_ALAV_DIRR3 = {"divida_liquida_ebitda": 6.44, "ebitda_12m": 1252058000.0,
               "alavancagem_ref": "brapi financialData de 2026-09-19"}


def test_brapi_com_liquida_maior_que_a_bruta_do_balanco_fica_retida():
    d = ff.dados_acao_b3(_balanco(3.5e9), None, _ALAV_DIRR3)
    dado = d["divida_liquida_ebitda"]
    assert dado.valor is None and dado.fonte == ff.FONTE_B3_BRAPI
    assert dado.nota.startswith(ff.NOTA_FONTES_DIVERGENTES)
    assert "R$ 8,06 bi" in dado.nota and "R$ 3,50 bi" in dado.nota
    assert "exercício 2025" in dado.nota


def test_brapi_dentro_da_folga_segue_valendo():
    # líquida de R$ 8,06 bi contra bruta de R$ 6 bi: cabe na folga de 1,5×
    d = ff.dados_acao_b3(_balanco(6.0e9), None, _ALAV_DIRR3)
    assert d["divida_liquida_ebitda"].valor == 6.44


def test_sem_bruta_ou_sem_ebitda_nao_ha_conferencia():
    assert ff.dados_acao_b3(None, None, _ALAV_DIRR3)[
        "divida_liquida_ebitda"].valor == 6.44
    sem_ebitda = {k: v for k, v in _ALAV_DIRR3.items() if k != "ebitda_12m"}
    assert ff.dados_acao_b3(_balanco(3.5e9), None, sem_ebitda)[
        "divida_liquida_ebitda"].valor == 6.44


def test_fontes_divergentes_avisam_sem_eliminar(an):  # noqa: F811
    dados = ff.dados_acao_b3(_balanco(3.5e9), None, _ALAV_DIRR3)
    fund = f.montar(f.ACAO, {**{k: f.Dado(v, "teste", "2025")
                                for k, v in BOA.items()
                                if k not in dados}, **dados}).como_dict()
    a = av.avaliar(_com(an["WEGE3"], fund))
    assert not any("alavancagem de" in x.texto for x in a.alertas)
    assert not any("Fontes divergentes" in x.texto for x in a.criticos)
    aviso = next(x for x in a.alertas if "Fontes divergentes" in x.texto)
    assert not aviso.critico


# -- negação com escopo (léxico 1.2.0) ---------------------------------------------


def test_negacao_antes_do_verbo_que_diminui_vira_positivo():
    # 1.1.0 dava negativo: negava "dividendos" em vez de "reduz"
    assert sentimento.calcular("Empresa não reduz dividendos", "pt") > 0
    assert sentimento.calcular("Company does not cut dividend", "en") > 0


def test_verbo_que_diminui_inverte_o_termo_seguinte():
    assert sentimento.calcular("Empresa corta dividendos", "pt") < 0
    assert sentimento.calcular("Empresa reduz prejuízo", "pt") > 0
    assert sentimento.calcular("Company slashes its dividend", "en") < 0
    # sozinho não pontua: corte de custo ou de juros não é notícia ruim
    assert sentimento.calcular("Empresa reduz custos", "pt") is None
    assert sentimento.calcular("Copom corta a Selic", "pt") is None


def test_negacao_alcanca_termo_de_varias_palavras():
    assert sentimento.calcular("Empresa não registra prejuízo", "pt") > 0
    assert sentimento.calcular("Analyst says it is not a buy rating", "en") < 0


def test_pontuacao_e_conjuncao_fecham_o_escopo():
    assert sentimento.calcular("BBI reduz projeção, rebaixa Klabin", "pt") < 0
    assert sentimento.calcular("Ação não cai, mas lucro sobe", "pt") > 0
    # vírgula decimal não separa oração
    assert sentimento.calcular("Lucro de R$ 1,2 bi", "pt") > 0


def test_termo_mais_longo_conta_uma_vez():
    assert sentimento.calcular("Banco eleva preço-alvo", "pt") == 0.5


# -- tom do provedor por ticker (01/10/2026) -------------------------------------
# Pedido do usuário: "Resolva também o tom do provedor por ticker". O arquivo
# publicado levava o tom do artigo inteiro; numa matéria sobre vários ativos ele
# mistura todos.


def test_acervo_grava_o_tom_de_cada_ticker_resolvido():
    import json
    from dataclasses import replace as rep

    from core.noticias import armazenamento as arm
    from core.noticias import modelos
    from tests.apoio_noticias import noticia
    from tests.test_noticias_reingestao import _avaliada

    n = noticia("Alfa compra Beta", "https://v.teste/alfa-beta")
    n = rep(n, entidades=modelos.Entidades(tickers=("ALFA", "BETA")),
            bruto={"ticker_sentiment": {"ALFA": -0.2, "BETA": 0.61234567,
                                        "GAMA": 0.9}})
    ent = json.loads(arm.linha_item(_avaliada(n))["entidades"])
    # GAMA foi citado pelo provedor, mas a resolução não o manteve
    assert ent["sentimento_por_ticker"] == {"ALFA": -0.2, "BETA": 0.6123}
    assert ent["tickers"] == ["ALFA", "BETA"]

    sem = json.loads(arm.linha_item(_avaliada(rep(n, bruto={})))["entidades"])
    assert "sentimento_por_ticker" not in sem


def test_tom_por_ticker_ignora_valor_invalido():
    from core.noticias.armazenamento import sentimento_por_ticker
    assert sentimento_por_ticker(
        {"ticker_sentiment": {"A": "x", "B": float("nan"), "C": 2}},
        ("A", "B", "C")) == {"C": 1.0}
    assert sentimento_por_ticker(None, ("A",)) == {}


def test_marketaux_guarda_o_tom_de_cada_entidade():
    from core.noticias.provedores.marketaux import Marketaux
    e = Marketaux._entidades([
        {"symbol": "aapl", "sentiment_score": 0.5},
        {"symbol": "AAPL", "sentiment_score": 0.3},
        {"symbol": "MSFT", "sentiment_score": -0.4},
        {"symbol": "GOOG"},
    ])
    assert e["por_ticker"] == {"AAPL": 0.4, "MSFT": -0.4}
    assert abs(e["sentimento"] - (0.5 + 0.3 - 0.4) / 3) < 1e-9


def test_publicador_prefere_o_tom_do_ticker_ao_do_artigo():
    from scripts import publish_informacoes_recentes as pub
    quando = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
    linhas = [{"titulo": "Vale (VALE3) e Petrobras (PETR4) fecham acordo",
               "publicado_em": quando, "url": "https://a", "veiculo": "A",
               "entidades": {"tickers": ["VALE3", "PETR4"],
                             "sentimento_por_ticker": {"VALE3": -0.45}},
               "sentimento_api": 0.2, "rotulo_sentimento": "Somewhat-Bullish"}]
    grupos = pub.agrupar_noticias(linhas, lambda t: ("VALE3", "PETR4"),
                                  lambda t: False)
    vale = grupos["VALE3"]["itens"][0]
    assert (vale["sentimento_api"], vale["sentimento_escopo"]) == (-0.45,
                                                                   "ticker")
    assert vale["rotulo_sentimento"] is None   # o rótulo é do artigo
    petr = grupos["PETR4"]["itens"][0]
    assert (petr["sentimento_api"], petr["sentimento_escopo"]) == (0.2,
                                                                   "artigo")
    assert petr["rotulo_sentimento"] == "Somewhat-Bullish"


def test_avaliacao_separa_tom_do_ticker_e_do_artigo(an):  # noqa: F811
    a = av.avaliar(_com(an["WEGE3"], _fund(**BOA), noticias=[
        replace(_noticia("Ação dispara"), sentimento_api=-0.4,
                sentimento_escopo="ticker"),
        replace(_noticia("Resultado do trimestre"), sentimento_api=0.3,
                sentimento_escopo="artigo"),
    ]))
    crit = next(c for c in a.dimensao(av.MERCADO).criterios
                if "notícia(s) própria(s)" in c.texto)
    assert ("1 pelo provedor para o ticker, 1 pelo provedor para o artigo "
            "inteiro") in crit.texto


def test_backfill_so_acrescenta_o_que_falta_para_os_tickers_da_linha():
    from scripts.backfill_sentimento_por_ticker import planejar
    tons = {("alphavantage", "https://a"): {"AAPL": 0.3, "MSFT": -0.2}}
    linhas = [
        {"id_dedup": "1", "provedor": "alphavantage", "url": "https://a",
         "entidades": {"tickers": ["AAPL"]}},
        {"id_dedup": "2", "provedor": "alphavantage", "url": "https://a",
         "entidades": '{"tickers": ["AAPL"], '
                      '"sentimento_por_ticker": {"AAPL": 0.3}}'},
        {"id_dedup": "3", "provedor": "marketaux", "url": "https://a",
         "entidades": {"tickers": ["AAPL"]}},
        {"id_dedup": "4", "provedor": "alphavantage", "url": "https://a",
         "entidades": {"tickers": ["NVDA"]}},
    ]
    # 2 já tem; 3 é de outro provedor; 4 não tem ticker com tom
    assert planejar(linhas, tons) == [("1", {"AAPL": 0.3})]


# -- item 2: régua de dívida coerente com a Empresas B3 (DIRR3, 10/2026) -----------
# A Inteligência dizia "Vender" por 6,44x o EBITDA enquanto a Empresas B3 e o
# chat liam Dív/PL de 1,27x. Agora a dívida é a do balanço em todas as ações,
# o Dív/PL entra com o teto do mesmo arquétipo, e a incorporadora é julgada
# por ele, não por dívida/EBITDA.


def _balanco_completo(divida_total=3.5e9, divida_liquida=1.7e9, pl=2.76e9):
    return pd.DataFrame({"Data": [pd.Timestamp(2025, 12, 31)],
                         "Receita_Liquida": [4.343e9],
                         "Lucro_Liquido": [9.8e8],
                         "Divida_Total": [divida_total],
                         "Divida_Liquida": [divida_liquida],
                         "Patrimonio_Liquido": [pl], "EBITDA": [None]})


def test_divida_liquida_do_balanco_sobre_ebitda_do_provedor():
    d = ff.dados_acao_b3(_balanco_completo(), None, _ALAV_DIRR3)
    dado = d["divida_liquida_ebitda"]
    assert abs(dado.valor - 1.7e9 / 1252058000.0) < 1e-9
    assert ff.FONTE_B3_DEMO in dado.fonte and ff.FONTE_B3_BRAPI in dado.fonte
    assert "exercício 2025" in dado.referencia


def test_divida_bruta_patrimonio_prefere_a_metrica_da_empresas_b3():
    d = ff.dados_acao_b3(_balanco_completo(), None, None)
    assert abs(d["divida_bruta_patrimonio"].valor - 3.5 / 2.76) < 1e-9
    mult = pd.Series({"Endividamento_Total": 1.27, "data": "2026-09-30"})
    d = ff.dados_acao_b3(_balanco_completo(), mult, None)
    assert d["divida_bruta_patrimonio"].valor == 1.27
    assert d["divida_bruta_patrimonio"].fonte == ff.FONTE_B3_MET
    # PL negativo: a razão não tem leitura
    assert "divida_bruta_patrimonio" not in ff.dados_acao_b3(
        _balanco_completo(pl=-1.0e9), None, None)


def _incorporadora(a):
    return replace(a, ativo=replace(a.ativo, ticker="DIRR3",
                                    setor="Construção Civil",
                                    subclasse="Incorporações"))


def test_incorporadora_nao_e_eliminada_por_divida_ebitda(an):  # noqa: F811
    a = av.avaliar(_com(_incorporadora(an["WEGE3"]),
                        _fund(**{**BOA, "divida_liquida_ebitda": 6.44,
                                 "divida_bruta_patrimonio": 1.27})))
    assert a.perfil == av.INCORPORADORA
    assert not a.criticos
    q = a.dimensao(av.QUALIDADE)
    ctx = next(c for c in q.criterios if "Dívida líquida/EBITDA" in c.texto)
    assert ctx.sinal == 0
    dpl = next(c for c in q.criterios if "Dívida bruta/patrimônio" in c.texto)
    assert dpl.sinal == 1 and "1,80x" in dpl.texto
    assert "Empresas B3" in dpl.texto


def test_incorporadora_muito_alavancada_no_patrimonio_e_eliminada(an):  # noqa: F811
    a = av.avaliar(_com(_incorporadora(an["WEGE3"]),
                        _fund(**{**BOA, "divida_bruta_patrimonio": 4.0})))
    assert any("dívida bruta de 4,00x o patrimônio" in x.texto
               for x in a.criticos)
    a = av.avaliar(_com(_incorporadora(an["WEGE3"]),
                        _fund(**{**BOA, "divida_bruta_patrimonio": 2.5})))
    assert not a.criticos
    assert any(c.sinal == -1 and "acima do teto de 1,80x" in c.texto
               for c in a.dimensao(av.QUALIDADE).criterios)


def test_divida_patrimonio_acima_do_teto_pesa_em_qualquer_acao_b3(an):  # noqa: F811
    # WEGE3 é industrial: teto 2,0x, o mesmo da Empresas B3
    a = av.avaliar(_com(an["WEGE3"],
                        _fund(**{**BOA, "divida_bruta_patrimonio": 2.6})))
    assert a.perfil == av.GERAL and not a.criticos
    assert any(c.sinal == -1 and "acima do teto de 2,00x" in c.texto
               for c in a.dimensao(av.QUALIDADE).criterios)
    # dentro do teto, fora da incorporadora, não soma ponto: a dívida já
    # conta pela razão sobre o EBITDA
    a = av.avaliar(_com(an["WEGE3"],
                        _fund(**{**BOA, "divida_bruta_patrimonio": 1.0})))
    assert not any("Dívida bruta/patrimônio" in c.texto
                   for c in a.dimensao(av.QUALIDADE).criterios)


def test_banco_nao_tem_teto_de_divida_patrimonio(an):  # noqa: F811
    a = av.avaliar(_com(an["BBAS3"],
                        _fund(roe=20.0, divida_bruta_patrimonio=9.0)))
    assert not any("Dívida bruta/patrimônio" in c.texto
                   for c in a.dimensao(av.QUALIDADE).criterios)


def test_teto_sobe_para_o_p90_dos_pares_como_na_empresas_b3(an):  # noqa: F811
    grupo = p.GrupoPares("segmento", "Incorporações", tuple(
        p.Par(f"P{i}", None, 0.0, "m", {"divida_bruta_patrimonio": x})
        for i, x in enumerate((1.0, 1.5, 2.0, 2.4, 3.0))))
    c = p.ComparacaoPares("DIRR3", f.ACAO, "BRL", grupo, ())
    fund = f.Fundamentos.de_dict(_fund(divida_bruta_patrimonio=2.5))
    teto, origem = av.teto_divida_patrimonio(_incorporadora(an["WEGE3"]),
                                             fund, c)
    assert teto == 2.76 and "p90 de 5 pares" in origem
    # ação de fora da B3: a régua é da base brasileira
    fund_us = f.Fundamentos.de_dict(f.montar(
        f.ACAO, {"divida_bruta_patrimonio": f.Dado(2.5, "t", "2025")},
        moeda="USD").como_dict())
    assert av.teto_divida_patrimonio(an["WEGE3"], fund_us, None) is None
