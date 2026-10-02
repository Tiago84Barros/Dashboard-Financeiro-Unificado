"""Item 3 do caso DIRR3 (02/10/2026): as LLMs da Empresas B3 leem o mesmo
veredito da Inteligência dos Ativos.

O chat dizia "comprar mais" enquanto a Inteligência dizia "Vender" por um
alerta eliminatório. ``veredito`` entrega à LLM a mesma ``Avaliacao`` e o
limite que ela impõe; o relatório por empresa tem a perspectiva limitada em
código.
"""
from dataclasses import replace

from core import llm_b3
from core.inteligencia_ativos import avaliacao as av
from core.inteligencia_ativos import fundamentos as f
from core.inteligencia_ativos import informacoes as inf
from core.inteligencia_ativos import pares as p
from core.inteligencia_ativos import resumida as r
from core.inteligencia_ativos import valuation as val
from core.inteligencia_ativos import veredito as vd
from tests.test_inteligencia_avaliacao import (  # noqa: F401
    BOA,
    _com,
    _fund,
    an,
)


def _av(qualidade=av.ADEQUADA, preco=av.JUSTO, mercado=av.NEUTRO,
        alertas=()):
    return av.Avaliacao(av.GERAL, (av.Dimensao(av.QUALIDADE, qualidade),
                                   av.Dimensao(av.VALUATION, preco),
                                   av.Dimensao(av.MERCADO, mercado)),
                        tuple(alertas))


_CRITICO = av.Alerta("dívida bruta de 4,00x o patrimônio", True)


def _leitores(a):
    """Leitores que devolvem o que está nas seções de ``a``: o veredito tem
    de chegar à mesma avaliação que a Inteligência fez com elas."""
    fund = f.Fundamentos.de_dict(a.fundamentos.dados)
    vp = (val.Valuation.de_dict(a.valuation.dados),
          p.ComparacaoPares.de_dict(a.pares.dados))
    infos = (inf.Noticias.de_dict(a.noticias.dados), None, None)
    return dict(ler_fundamentos=lambda _: fund, ler_valuation=lambda _: vp,
                ler_informacoes=lambda _: infos)


def test_mesma_avaliacao_que_a_inteligencia(an):  # noqa: F811
    for a in (an["WEGE3"],
              _com(an["WEGE3"], _fund(**{**BOA, "divida_liquida_ebitda": 6.0}))):
        a = replace(a, ativo=replace(a.ativo, subclasse=None))
        esperado = av.avaliar(a)
        obtido = vd.avaliar_acao_b3(a.ativo.ticker, a.ativo.nome,
                                    a.ativo.setor, **_leitores(a))
        assert obtido == esperado


def test_limite_segue_os_passos_da_inteligencia():
    d = r.Decisao(r.COMPRAR, "abaixo do alvo", "")
    casos = [
        (_av(alertas=[_CRITICO]), vd.TROCAR, r.VENDER),
        (_av(qualidade=av.FRAGIL, preco=av.CARO), vd.TROCAR, r.VENDER),
        (_av(qualidade=av.FRAGIL, mercado=av.NEGATIVO), vd.TROCAR, r.VENDER),
        (_av(qualidade=av.FRAGIL), vd.NAO_APORTAR, r.MANTER),
        (_av(qualidade=av.FORTE, preco=av.BARATO), vd.LIVRE, r.COMPRAR),
    ]
    for aval, limite, codigo in casos:
        assert vd.limite(aval)[0] == limite
        assert r.com_avaliacao(d, aval).codigo == codigo


def test_alerta_eliminatorio_vira_limite_no_bloco():
    bloco, avs = vd.bloco_para_llm(
        [{"ticker": "dirr3", "setor": "Construção Civil"}, {"ticker": "DIRR3"}],
        avaliador=lambda tk, nome, setor: _av(qualidade=av.FRAGIL,
                                              alertas=[_CRITICO]))
    assert list(avs) == ["DIRR3"]
    assert bloco.startswith("=== AVALIAÇÃO POR REGRAS")
    assert bloco.count("LIMITE da recomendação para DIRR3: avaliar troca") == 1
    assert "4,00x o patrimônio" in bloco


def test_ticker_que_falha_e_nomeado_e_excesso_e_listado():
    def avaliador(tk, nome, setor):
        if tk == "ERRO3":
            raise RuntimeError("banco fora")
        return _av()
    itens = [{"ticker": t} for t in ("ERRO3", "WEGE3", "ITUB4")]
    bloco, avs = vd.bloco_para_llm(itens, avaliador=avaliador, max_tickers=2)
    assert "ERRO3: avaliação por regras indisponível agora (RuntimeError)" in bloco
    assert "LIMITE da recomendação para WEGE3: livre" in bloco
    assert "ITUB4." in bloco and "ITUB4" not in avs
    assert vd.bloco_para_llm([], avaliador=avaliador) == ("", {})


def test_relatorio_tem_a_perspectiva_limitada():
    analise = {"perspectiva": "forte", "resumo": "Boa tese.",
               "tese_final": "Boa tese."}
    out = vd.coerente(analise, _av(alertas=[_CRITICO]))
    assert out["perspectiva"] == "fraca"
    assert out["resumo"].startswith("Perspectiva limitada a 'fraca'")
    assert "4,00x o patrimônio" in out["tese_final"]
    assert analise["perspectiva"] == "forte"  # puro
    assert vd.coerente(analise, _av(qualidade=av.FRAGIL))["perspectiva"] \
        == "moderada"
    # dentro do teto, ou sem avaliação, nada muda
    fraca = {**analise, "perspectiva": "fraca"}
    assert vd.coerente(fraca, _av(alertas=[_CRITICO])) is fraca
    assert vd.coerente(analise, _av()) is analise
    assert vd.coerente(analise, None) is analise


def test_regra_do_relatorio_cita_o_teto():
    txt = vd.regra_relatorio(_av(alertas=[_CRITICO]), "DIRR3")
    assert "avaliar troca" in txt and "não pode passar de 'fraca'" in txt
    assert "não pode passar" not in vd.regra_relatorio(_av(), "WEGE3")
    assert vd.regra_relatorio(None, "WEGE3") == ""


def test_chat_leva_a_regra_no_prompt(monkeypatch):
    visto = {}

    def falso(messages, **kw):
        visto["system"] = messages[0]["content"]
        return "ok"
    monkeypatch.setattr(llm_b3, "_chat_complete", falso)
    llm_b3.chat_com_portfolio("ctx", [], "Compro mais DIRR3?")
    assert vd.REGRA_VEREDITO in visto["system"]


def test_chat_da_empresas_b3_anexa_o_bloco(monkeypatch):
    from views import analise_portfolio_b3 as view
    visto = {}

    def bloco(itens, **kw):
        visto["tickers"] = [i["ticker"] for i in itens]
        return "=== AVALIAÇÃO POR REGRAS ===\nDIRR3", {}
    monkeypatch.setattr(view.veredito, "bloco_para_llm", bloco)
    txt, _ = view._veredito_do_chat([{"ticker": "WEGE3"}], ["DIRR3"])
    assert visto["tickers"] == ["WEGE3", "DIRR3"] and "DIRR3" in txt

    def quebra(itens, **kw):
        raise RuntimeError("x")
    monkeypatch.setattr(view.veredito, "bloco_para_llm", quebra)
    txt, avs = view._veredito_do_chat([], None)
    assert "Indisponível agora (RuntimeError)" in txt and avs == {}


# ── Conferência pós-resposta ────────────────────────────────────────────────

_AVS = {"DIRR3": _av(alertas=(_CRITICO,)),          # avaliar troca
        "MRVE3": _av(qualidade=av.FRAGIL),           # não aportar
        "WEGE3": _av(qualidade=av.FORTE)}            # livre


def test_conferencia_pega_compra_contra_o_limite():
    txt = ("**Conclusão prática**\n\n- **DIRR3**: comprar mais, a Dív/PL de "
           "1,27x é confortável.\n- WEGE3: aumentar posição.")
    v = vd.conferir_resposta(txt, _AVS)
    assert [(x.ticker, x.acao, x.limite) for x in v] == [
        ("DIRR3", "comprar/aumentar", vd.TROCAR)]
    assert "1,27x" in v[0].trecho and "dívida bruta" in v[0].motivo


def test_conferencia_ignora_negacao_e_ticker_vizinho():
    txt = ("Não recomendo comprar DIRR3 nem aumentar a posição em MRVE3. "
           "Prefira reforçar WEGE3.\nPara DIRR3, evite aportar: o alerta "
           "eliminatório pede avaliar troca.")
    assert vd.conferir_resposta(txt, _AVS) == []
    # "não" de outra oração não protege o verbo
    assert vd.conferir_resposta(
        "A dívida não preocupa; recomendo comprar DIRR3.", _AVS)


def test_conferencia_manter_depende_do_limite():
    sem_ressalva = "DIRR3: manter a posição, empresa sólida."
    com_ressalva = "DIRR3: manter por ora, mas o alerta de dívida pede troca."
    assert [x.acao for x in vd.conferir_resposta(sem_ressalva, _AVS)] == [
        "manter sem ressalva"]
    assert vd.conferir_resposta(com_ressalva, _AVS) == []
    assert vd.conferir_resposta("MRVE3: manter, sem reforçar.", _AVS) == []
    assert vd.conferir_resposta("MRVE3: aumentar peso.", _AVS)


def test_conferencia_ignora_bloco_de_graficos():
    txt = ('DIRR3 tem alerta.\n```charts\n[{"tipo": "comparison", '
           '"titulo": "comprar DIRR3?", "tickers": ["DIRR3"]}]\n```')
    assert vd.conferir_resposta(txt, _AVS) == []


def _chat_em_sequencia(monkeypatch, respostas):
    chamadas = []

    def falso(messages, **kw):
        chamadas.append(messages)
        r_ = respostas.pop(0)
        if isinstance(r_, Exception):
            raise r_
        return r_
    monkeypatch.setattr(llm_b3, "_chat_complete", falso)
    return chamadas


def test_chat_coerente_reescreve_a_contradicao(monkeypatch):
    ruim = "DIRR3: comprar mais.\n```charts\n[]\n```"
    boa = "DIRR3: avaliar troca pelo alerta de dívida.\n```charts\n[]\n```"
    chamadas = _chat_em_sequencia(monkeypatch, [ruim, boa])
    assert llm_b3.chat_coerente("ctx", [], "Compro DIRR3?", _AVS) == boa
    pedido = chamadas[1][-1]["content"]
    assert "CONFERÊNCIA AUTOMÁTICA" in pedido and "DIRR3" in pedido
    assert chamadas[1][-2] == {"role": "assistant", "content": ruim}


def test_chat_coerente_sem_contradicao_faz_uma_chamada(monkeypatch):
    chamadas = _chat_em_sequencia(monkeypatch, ["WEGE3: comprar mais."])
    assert llm_b3.chat_coerente("ctx", [], "e WEGE3?", _AVS) == "WEGE3: comprar mais."
    assert len(chamadas) == 1


def test_chat_coerente_avisa_quando_persiste_ou_falha(monkeypatch):
    ruim = "DIRR3: comprar mais."
    _chat_em_sequencia(monkeypatch, [ruim, "DIRR3: aumentar posição."])
    txt = llm_b3.chat_coerente("ctx", [], "?", _AVS)
    assert txt.startswith("> ⚠️ **Conferência automática:**")
    assert txt.endswith("DIRR3: aumentar posição.")

    _chat_em_sequencia(monkeypatch, [ruim, RuntimeError("cota")])
    txt = llm_b3.chat_coerente("ctx", [], "?", _AVS)
    assert "Conferência automática" in txt and txt.endswith(ruim)


# ── Generalização: todas as classes e todos os chats ───────────────────────

def test_info_do_ativo_por_mercado():
    us = vd.info_ativo("aapl", mercado="us")
    assert (us.ticker, us.classe, us.moeda, us.classe_politica) == (
        "AAPL", "Ações EUA", "USD", "exterior")
    fii = vd.info_ativo("HGLG11.SA", mercado="fii")
    assert (fii.ticker, fii.classe_politica) == ("HGLG11", "fiis")
    assert vd.info_acao_b3("WEGE3.SA").classe_politica == "acoes_br"


def test_bloco_usa_o_mercado_de_cada_item(monkeypatch):
    vistos = []

    def falso(tk, nome, setor, *, mercado):
        vistos.append((tk, mercado))
        return _av()
    monkeypatch.setattr(vd, "avaliar_ativo", falso)
    vd.bloco_para_llm([{"ticker": "AAPL"}, {"ticker": "HGLG11", "mercado": "fii"}],
                      mercado="us")
    assert vistos == [("AAPL", "us"), ("HGLG11", "fii")]


def _dec(codigo, detalhe):
    return r.Decisao(codigo, detalhe, "", _av(alertas=[_CRITICO]))


def test_decisao_da_carteira_vira_veredito():
    casos = [(_dec(r.VENDER, "avaliar troca por uma alternativa"), vd.TROCAR),
             (_dec(r.VENDER, "vender parte: acima do alvo"), vd.REDUZIR),
             (_dec(r.MANTER, "no alvo"), vd.NAO_APORTAR),
             (_dec(r.COMPRAR, "abaixo do alvo"), vd.LIVRE)]
    for d, esperado in casos:
        v = vd.veredito_de_decisao("DIRR3", d)
        assert v.limite == esperado and v.decisao == d.rotulo
        assert "DECISÃO da Inteligência dos Ativos para DIRR3" in \
            vd.texto_ticker(v, "DIRR3")


def test_decisao_pronta_vence_o_avaliador_e_nao_conta_no_teto():
    v = vd.veredito_de_decisao("DIRR3", _dec(r.VENDER, "vender parte: x"))
    chamados = []

    def avaliador(tk, nome, setor):
        chamados.append(tk)
        return _av()
    bloco, avs = vd.bloco_para_llm([{"ticker": "DIRR3"}, {"ticker": "WEGE3"}],
                                   avaliador=avaliador, decisoes={"dirr3": v})
    assert chamados == ["WEGE3"] and avs["DIRR3"] is v
    assert "LIMITE da recomendação para DIRR3: reduzir" in bloco


def test_vereditos_da_carteira_sem_analise_e_vazio():
    assert vd.vereditos_da_carteira(None) == {}
    assert vd.vereditos_da_carteira({"analysis_available": False}) == {}


def test_mercado_da_posicao():
    assert vd.mercado_da_posicao({"classe": "Ações BR"}) == "b3"
    assert vd.mercado_da_posicao({"classe": "FII"}) == "fii"
    assert vd.mercado_da_posicao({"classe": "Ações EUA", "moeda": "USD"}) == "us"
    for pos in ({"classe": "ETF Internacional", "moeda": "USD"},
              {"classe": "Renda Fixa"}, {"classe": "BDR"}):
        assert vd.mercado_da_posicao(pos) is None


def test_bloco_da_carteira_decisao_primeiro_e_citados(monkeypatch):
    vistos = []

    def falso(itens, **kw):
        vistos.append(([i["ticker"] for i in itens], kw))
        return "bloco", {}
    monkeypatch.setattr(vd, "bloco_seguro", falso)
    v = vd.veredito_de_decisao("DIRR3", _dec(r.MANTER, "no alvo"))
    pos = [{"ticker": "WEGE3", "classe": "Ações BR", "valor_mercado": 10},
           {"ticker": "DIRR3", "classe": "Ações BR", "valor_mercado": 5},
           {"ticker": "CDB1", "classe": "Renda Fixa", "valor_mercado": 99}]
    vd.bloco_da_carteira(pos, {"DIRR3": v}, "e MRVE3?")
    tickers, kw = vistos[0]
    assert tickers == ["MRVE3", "DIRR3", "WEGE3"]
    assert kw["max_tickers"] == vd.MAX_TICKERS + 1


def test_conferencia_com_ticker_americano_e_reduzir():
    v = vd.veredito_de_decisao("AAPL", _dec(r.VENDER, "vender parte: acima"))
    avs = {"AAPL": v, "MSFT": _av(qualidade=av.FORTE)}
    out = vd.conferir_resposta("AAPL: aumentar a posição. MSFT: comprar.", avs)
    assert [(x.ticker, x.limite) for x in out] == [("AAPL", vd.REDUZIR)]
    assert [x.acao for x in vd.conferir_resposta("AAPL: manter.", avs)] == [
        "manter sem ressalva"]
    # "A" e "E" são palavras, não tickers: não viram menção
    assert vd.conferir_resposta(
        "E comprar mais? A MSFT sim; AAPL não, evite aumentar.", avs) == []


def test_responder_coerente_reescreve_uma_vez():
    respostas = ["DIRR3: comprar mais.", "DIRR3: avaliar troca."]
    turnos = []

    def chat(h, msg):
        turnos.append((list(h), msg))
        return respostas.pop(0)
    assert vd.responder_coerente(chat, [], "?", _AVS) == "DIRR3: avaliar troca."
    assert len(turnos) == 2 and "CONFERÊNCIA AUTOMÁTICA" in turnos[1][1]


def test_portao_da_selecao_veta_e_substitui():
    avs = {"DIRR3": _av(alertas=[_CRITICO]), "MRVE3": _av(qualidade=av.FRAGIL),
           "CYRE3": _av(qualidade=av.FORTE), "EVEN3": _av()}

    def avaliador(tk):
        if tk == "TEND3":
            raise RuntimeError("fora")
        return avs[tk]
    log = vd.novo_log_selecao()
    pesos = {"DIRR3": 0.6, "TEND3": 0.4}
    ranked = [("DIRR3", 9), ("TEND3", 8), ("MRVE3", 7), ("EVEN3", 6), ("CYRE3", 5)]
    out = vd.filtrar_selecao(["DIRR3", "TEND3"], ranked, avaliador=avaliador,
                             pesos=pesos, seg_label="Construção", log=log,
                             exclui=lambda tk: tk == "EVEN3")
    assert out == ["CYRE3", "TEND3"]  # TEND3 falhou: não veta (fail-open)
    assert pesos["CYRE3"] == 0.6
    assert [v["tk"] for v in log["vetados"]] == ["DIRR3", "MRVE3"]
    assert log["vetados"][0]["limite"] == "avaliar troca"
    assert log["substituicoes"] == [{"entra": "CYRE3", "sai": "DIRR3",
                                     "segmento": "Construção"}]
    assert [i["tk"] for i in log["indisponiveis"]] == ["TEND3"]

    log = vd.novo_log_selecao()
    assert vd.filtrar_selecao(["DIRR3"], [("DIRR3", 1), ("MRVE3", 0)],
                              avaliador=avaliador, pesos={}, seg_label="s",
                              log=log) == []
    assert log["vagas_vazias"] == [{"sai": "DIRR3", "segmento": "s"}]


def test_regra_do_veredito_em_todos_os_prompts(monkeypatch):
    from core import llm_ativo, llm_carteira, llm_fii, llm_global
    sistemas = []

    def falso(messages, **kw):
        sistemas.append(messages[0]["content"])
        return "ok"
    for mod in (llm_fii, llm_global, llm_ativo, llm_carteira):
        if hasattr(mod, "_chat_complete"):
            monkeypatch.setattr(mod, "_chat_complete", falso)
    monkeypatch.setattr(llm_b3, "_chat_complete", falso)
    llm_fii.chat_com_fiis("ctx", [], "?")
    llm_global.chat_com_portfolio_global("ctx", [], "?")
    llm_ativo.chat_com_ativo("ctx", [], "?", mercado="us", ticker="AAPL")
    llm_carteira.chat_com_carteira("ctx", [], "?", classe="acoes")
    assert len(sistemas) == 4
    assert all(vd.REGRA_VEREDITO in s for s in sistemas)


def test_chat_global_cita_primeiro_e_leva_o_mercado():
    import pandas as pd

    from views import portfolio_global as pg
    df = pd.DataFrame([
        {"asset_class": "b3", "symbol": "WEGE3", "sector": "Industrial"},
        {"asset_class": "us", "symbol": "AAPL", "sector": "Tech"},
        {"asset_class": "fii", "symbol": "HGLG11"},
        {"asset_class": "rf", "symbol": "CDB"}])
    itens = pg._itens_do_veredito(df, "Vale manter HGLG11?")
    assert [(i["ticker"], i["mercado"]) for i in itens] == [
        ("HGLG11", "fii"), ("WEGE3", "b3"), ("AAPL", "us")]


def test_reotimizar_sem_vetados_remonta_ate_limpar():
    avs = {"A11": _av(alertas=[_CRITICO]), "B11": _av(), "C11": _av(),
           "D11": _av(qualidade=av.FRAGIL), "E11": _av()}

    def avaliador(tk):
        if tk == "C11":
            raise RuntimeError("fora")
        return avs[tk]
    ordem = ["A11", "B11", "C11", "D11", "E11"]
    chamadas = []

    def montar(excl):
        chamadas.append(excl)
        return {"items": [{"ticker": t, "tipo": "Tijolo"}
                          for t in ordem if t not in excl][:3]}
    log = vd.novo_log_selecao()
    res, excl = vd.reotimizar_sem_vetados(
        montar, avaliador=avaliador, log=log,
        itens_de=lambda r: [(i["ticker"], i["tipo"]) for i in r["items"]])
    # A11 sai → D11 entra e é barrado → E11 entra.
    assert [i["ticker"] for i in res["items"]] == ["B11", "C11", "E11"]
    assert excl == {"A11", "D11"}
    assert [v["tk"] for v in log["vetados"]] == ["A11", "D11"]
    assert log["substituicoes"] == [{"entra": "E11", "sai": "D11",
                                     "segmento": "Tijolo"}]
    assert [i["tk"] for i in log["indisponiveis"]] == ["C11"]  # fail-open
    assert log["persistentes"] == [] and len(chamadas) == 3


def test_reotimizar_declara_vetado_que_persiste():
    log = vd.novo_log_selecao()
    res, _ = vd.reotimizar_sem_vetados(
        lambda excl: {"items": [{"ticker": "A11", "tipo": "Papel"}]},
        avaliador=lambda tk: _av(alertas=[_CRITICO]), log=log, max_rodadas=2,
        itens_de=lambda r: [(i["ticker"], i["tipo"]) for i in r["items"]])
    assert [i["ticker"] for i in res["items"]] == ["A11"]
    assert [v["tk"] for v in log["persistentes"]] == ["A11"]
    assert log["substituicoes"] == []


def test_portao_na_selecao_americana():
    from core.us_portfolio_creation import (
        USPortfolioCreationParams,
        build_portfolio_creation,
    )
    from tests.test_us_portfolio_creation import _universe
    params = USPortfolioCreationParams(
        top_n=15, leaders_per_industry=1, min_companies_per_industry=4,
        min_entry_score=50, min_score_edge=0, min_market_cap=1_000_000_000,
        apply_quality_floor=False,
    )
    base = build_portfolio_creation(_universe(), params)
    lideres = list(base["candidates"]["symbol"])
    alvo = lideres[0]
    res = build_portfolio_creation(
        _universe(), params,
        avaliador=lambda tk: _av(alertas=[_CRITICO]) if tk == alvo else _av())
    escolhidos = set(res["candidates"]["symbol"])
    assert alvo not in escolhidos
    assert alvo not in set(res["holdings"].get("symbol", []))
    log = res["inteligencia_log"]
    assert [v["tk"] for v in log["vetados"]] == [alvo]
    entra = log["substituicoes"][0]["entra"]
    assert entra in escolhidos and entra[:3] == alvo[:3]  # mesma indústria
    linha = res["candidates"].set_index("symbol").loc[entra]
    assert "Inteligência" in linha["selection_reason"]
    # Sem avaliador, nada muda.
    assert list(build_portfolio_creation(_universe(), params)["candidates"]
                ["symbol"]) == lideres
