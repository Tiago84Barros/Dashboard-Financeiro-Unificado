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
    assert sentimento.METODO == "lexico_app4_1.1.0"
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
    assert "1 pelo provedor, 1 pelo léxico" in crit.texto
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
