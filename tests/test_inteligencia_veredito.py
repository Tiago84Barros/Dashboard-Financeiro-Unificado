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
