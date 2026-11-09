"""Inteligência dos Ativos: o confronto da carteira com a carteira
recomendada do Portfólio Global -- qualidade pela mesma régua nos dois lados,
pesos por classe contra o modelo e a estratégia, divergências por ativo com a
conclusão (sem contradizer a decisão da Inteligência) e inclusões."""
import pathlib
from types import SimpleNamespace

import pytest

from core.inteligencia_ativos import avaliacao as av_
from core.inteligencia_ativos import confronto_modelo as cf
from core.inteligencia_ativos import referencia_modelo as rm
from core.inteligencia_ativos import veredito as vd
from views import inteligencia_ativos as tela


def _snap(nome: str, peso: float, setor: str = "x") -> dict:
    return {"identity": {"name": nome, "sector": setor},
            "metrics": {"weight": peso}}


SNAPSHOTS = {
    "b3": {"PETR4": _snap("Petrobras", 0.5), "VALE3": _snap("Vale", 0.3),
           "WEGE3": _snap("WEG", 0.2)},
    "fii": {"HGLG11": _snap("CSHG Logística", 1.0)},
    "us": {"AAPL": _snap("Apple", 1.0)},
}
ALVOS = {"b3": 0.5, "fii": 0.3, "us": 0.2}


def _pos(tk, classe, valor, moeda="BRL", pais="BR"):
    return {"ticker": tk, "nome": tk, "classe": classe, "moeda": moeda,
            "pais": pais, "valor_mercado": valor}


POSICOES = [
    _pos("PETR4", "Ações BR", 300.0),     # 30% real × 15% modelo
    _pos("ITSA4", "Ações BR", 100.0),     # 10% real, fora do modelo
    _pos("HGLG11", "FII", 50.0),          # 5% real × 18% modelo
    _pos("TESOURO IPCA", "Tesouro Direto", 550.0),
]


def _ref(**kw):
    return rm.montar(kw.get("snapshots", SNAPSHOTS),
                     {"targets": ALVOS, "renda_fixa": kw.get("rf", 0.4)},
                     kw.get("posicoes", POSICOES))


def _av(qualidade=av_.ADEQUADA, preco=av_.JUSTO, mercado=av_.NEUTRO,
        criticos=()):
    alertas = tuple(av_.Alerta(t, critico=True) for t in criticos)
    return av_.Avaliacao(
        perfil=av_.GERAL,
        dimensoes=(av_.Dimensao(av_.QUALIDADE, qualidade,
                                (av_.Criterio("roe", 1),)),
                   av_.Dimensao(av_.VALUATION, preco),
                   av_.Dimensao(av_.MERCADO, mercado)),
        alertas=alertas)


def _vered(tk, codigo, av=None, *, proporcao=None):
    from core.inteligencia_ativos import resumida as rs
    av = av or _av()
    d = SimpleNamespace(codigo=codigo, rotulo=rs.ROTULO_DECISAO[codigo],
                        detalhe=f"detalhe {codigo}", proporcao=proporcao,
                        substituto=None, avaliacao=av)
    limite = vd._LIMITE_DA_DECISAO[codigo]
    return vd.Veredito(tk, limite, f"motivo {codigo}", d.rotulo, d.detalhe,
                       av, d)


ESTRATEGIA = {"renda_fixa": 50.0, "acoes_br": 30.0, "fiis": 15.0,
              "exterior": 5.0}


def _conf(vereditos=None, avals=None, **kw):
    return cf.montar(kw.pop("ref", None) or _ref(), vereditos or {
        "PETR4": _vered("PETR4", "manter", _av(av_.FORTE)),
        "ITSA4": _vered("ITSA4", "manter", _av(av_.ADEQUADA)),
        "HGLG11": _vered("HGLG11", "acompanhar", _av(av_.FRAGIL)),
    }, avals if avals is not None else {
        "VALE3": _av(av_.FORTE), "WEGE3": _av(av_.FRAGIL),
        "AAPL": _av(av_.FORTE),
    }, alocacao_estrategia=kw.pop("estrategia", ESTRATEGIA), **kw)


def test_qualidade_mesma_regua_e_ponderada_pelo_peso():
    c = _conf()
    # carteira (risco): PETR4 30 forte, ITSA4 10 adequada, HGLG11 5 frágil
    assert c.carteira.nota == pytest.approx((30 * 3 + 10 * 2 + 5 * 1) / 45)
    assert c.carteira.peso_fragil == pytest.approx(5 / 45 * 100)
    # modelo: PETR4 15 forte, VALE3 9 forte, WEGE3 6 frágil, HGLG11 18
    # frágil (a avaliação do HGLG11 é a mesma da carteira), AAPL 12 forte
    assert c.modelo.nota == pytest.approx(
        (15 * 3 + 9 * 3 + 6 * 1 + 18 * 1 + 12 * 3) / 60)
    assert c.modelo.cobertura == pytest.approx(100.0)
    assert {q.classe for q in c.por_classe} == {"b3", "fii", "us"}


def test_sem_avaliacao_reduz_cobertura_sem_virar_nota_zero():
    c = _conf(avals={"VALE3": None})
    assert c.modelo.cobertura < 100.0
    assert c.modelo.nota is not None and c.modelo.nota >= 1.0


def test_aderencia_soma_o_minimo_dos_dois_lados():
    c = _conf()
    assert c.aderencia == pytest.approx(15.0 + 5.0)   # PETR4 e HGLG11


def test_acima_do_modelo_sem_venda_da_inteligencia_mantem_a_escolha():
    d = _conf().da_carteira("PETR4")
    assert d.diferenca == pytest.approx(15.0)
    assert d.conclusao == cf.PERMANECER
    assert any("Qualidade forte" in m for m in d.motivos)
    assert any("custo e imposto" in m for m in d.motivos)


def test_fora_do_modelo_nao_e_argumento_contra():
    d = _conf().da_carteira("ITSA4")
    assert d.situacao == rm.SO_NA_CARTEIRA and d.conclusao == cf.PERMANECER
    assert any("não é defeito do ativo" in m for m in d.motivos)


def test_abaixo_do_modelo_nao_manda_aportar_quando_a_inteligencia_barra():
    d = _conf().da_carteira("HGLG11")
    assert d.diferenca == pytest.approx(-13.0)
    assert d.conclusao == cf.PERMANECER
    assert "não libera aporte" in d.texto


def test_abaixo_do_modelo_com_compra_liberada_vira_aporte():
    v = {"PETR4": _vered("PETR4", "manter"),
         "HGLG11": _vered("HGLG11", "comprar")}
    d = _conf(v).da_carteira("HGLG11")
    assert d.conclusao == cf.MUDAR and "libera compra" in d.texto


def test_vender_da_inteligencia_sem_par_no_segmento_nao_troca_de_setor():
    """Pedido de 08/11/2026 (BBAS3 e PSSA3 viravam CPFE3): o modelo só tem
    ativo de outro setor, e a troca não atravessa setores. O ativo em pauta
    fica sem substituto, dito com todas as letras."""
    v = {"PETR4": _vered("PETR4", "vender", _av(criticos=("dívida",))),
         "ITSA4": _vered("ITSA4", "manter", _av(av_.ADEQUADA))}
    c = _conf(v, analises={"PETR4": _analise("PETR4", PETR,
                                             setor="Petróleo")})
    d = c.da_carteira("PETR4")
    assert d.conclusao == cf.MUDAR and "não por causa do modelo" in d.texto
    assert c.substituicao_de("PETR4") is None
    sem = next(x for x in c.sem_substituto if x.sai == "PETR4")
    assert "Sem par do mesmo segmento" in sem.texto

def test_reduzir_parcial_acima_do_modelo_diz_a_proporcao():
    v = {"PETR4": _vered("PETR4", "reduzir_parcial", proporcao=0.25)}
    d = _conf(v).da_carteira("PETR4")
    assert d.conclusao == cf.MUDAR and "25%" in d.texto


def test_conclusao_nunca_contradiz_o_limite_da_inteligencia():
    for codigo in ("manter", "acompanhar", "rebalancear", "reduzir_parcial",
                   "vender", "substituir", "comprar"):
        c = _conf({tk: _vered(tk, codigo) for tk in ("PETR4", "ITSA4",
                                                     "HGLG11")})
        for d in c.divergencias:
            if d.diferenca < 0 and d.conclusao == cf.MUDAR:
                assert codigo in ("comprar", "vender", "substituir"), codigo
            if d.diferenca > 0 and d.conclusao == cf.MUDAR:
                assert codigo in ("reduzir_parcial", "vender",
                                  "substituir"), codigo


def test_candidatos_pela_avaliacao_do_proprio_ativo():
    c = _conf()
    por = {d.ticker: d for d in c.candidatos}
    # VALE3 forte, acima da média da classe na carteira (2,75): candidato
    assert por["VALE3"].conclusao == cf.INCLUIR
    assert any("acima da média" in m for m in por["VALE3"].motivos)
    # WEGE3 frágil: o limite da Inteligência barra
    assert por["WEGE3"].conclusao == cf.NAO_INCLUIR
    # AAPL: você não tem Internacional e a estratégia prevê 5%
    assert por["AAPL"].conclusao == cf.INCLUIR
    assert any("não tem posição" in m for m in por["AAPL"].motivos)
    # O modelo usa 12%, mas a estratégia só prevê 5% em Internacional.
    assert any(m.startswith("Peso de referência: 5,0%") and "o modelo usa"
               in m for m in por["AAPL"].motivos)
    # Empresas B3 já está acima da estratégia: incluir é trocar, não somar.
    assert any("trocar por outro ativo" in m for m in por["VALE3"].motivos)
    assert c.candidatos[0].conclusao == cf.INCLUIR


def test_candidato_respeita_classe_zerada_na_estrategia_e_teto_por_ativo():
    c = _conf(estrategia={**ESTRATEGIA, "exterior": 0.0},
              limite_por_ativo=5.0)
    por = {d.ticker: d for d in c.candidatos}
    assert por["AAPL"].conclusao == cf.NAO_INCLUIR
    assert any("limite por ativo" in m for m in por["VALE3"].motivos)


def test_candidato_pior_que_a_media_da_classe_nao_entra():
    v = {"PETR4": _vered("PETR4", "manter", _av(av_.FORTE)),
         "ITSA4": _vered("ITSA4", "manter", _av(av_.FORTE))}
    c = _conf(v, {"VALE3": _av(av_.ADEQUADA)})
    vale = next(d for d in c.candidatos if d.ticker == "VALE3")
    assert vale.conclusao == cf.NAO_INCLUIR and "abaixo da média" in vale.texto
    assert c.nao_avaliados == 2   # WEGE3 e AAPL não foram avaliados


def test_classe_perto_da_estrategia_permanece_e_longe_rebalanceia():
    c = _conf(estrategia={"renda_fixa": 55.0, "acoes_br": 40.0, "fiis": 5.0,
                          "exterior": 0.0})
    por = {k.classe: k for k in c.classes}
    # B3: 40% real, 30% modelo, 40% estratégia → manter
    assert por["b3"].acentuada and por["b3"].conclusao == cf.PERMANECER
    c2 = _conf()
    por2 = {k.classe: k for k in c2.classes}
    # Renda fixa: 55% real, 40% modelo, 50% estratégia → perto (tol 10)
    assert por2["renda_fixa"].conclusao == cf.PERMANECER
    # FII: 5% real, 18% modelo, 15% estratégia → rebalancear, mesma direção
    assert por2["fii"].conclusao == cf.MUDAR
    assert "mesma direção" in por2["fii"].texto


def test_estrategia_reescalada_sem_renda_fixa_no_modelo():
    assert cf._alvo_na_base(ESTRATEGIA, "b3", rm.BASE_SEM_RENDA_FIXA) == \
        pytest.approx(60.0)
    assert cf._alvo_na_base(ESTRATEGIA, "b3", rm.BASE_PATRIMONIO) == 30.0
    assert cf._alvo_na_base({}, "b3", rm.BASE_PATRIMONIO) is None


def test_renda_fixa_nao_vira_divergencia_de_ativo():
    assert _conf().da_carteira("TESOURO IPCA") is None


def test_indisponivel_quando_a_referencia_esta():
    c = cf.montar(rm.indisponivel("sem modelo"), {}, {})
    assert not c.disponivel and c.para_llm("PETR4")["estado"] == "indisponível"


def test_para_llm_traz_o_ativo_a_qualidade_e_os_candidatos():
    llm = _conf().para_llm("PETR4")
    assert llm["ativo"]["conclusao"] == "Manter a sua escolha"
    assert llm["qualidade_carteira"]["cobertura_pct"] == 100.0
    assert {d["ticker"] for d in llm["candidatos_a_inclusao"]} == {"VALE3",
                                                                    "AAPL"}
    sem = _conf().para_llm("WEGE3")
    assert "nenhuma divergência" in sem["ativo"]["divergencia"]


def test_a_avaliar_pega_os_maiores_do_modelo_fora_da_carteira():
    assert [x.ticker for x in cf.a_avaliar(_ref(), 2)] == ["AAPL", "VALE3"]
    assert _ref().linha("VALE3").setor == "x"
    assert _ref().linha("ITSA4").classe_real == "b3"


def test_decisao_nao_importa_o_confronto():
    raiz = pathlib.Path(__file__).resolve().parents[1] / "core" / "inteligencia_ativos"
    for nome in ("veredito.py", "analise.py", "adequacao.py", "calculos.py",
                 "contexto.py", "painel.py", "resumida.py", "avaliacao.py"):
        assert "confronto_modelo" not in (raiz / nome).read_text("utf-8"), nome


def test_cartoes_escapam_e_mostram_os_numeros():
    c = _conf()
    html = tela.cartao_confronto(c)
    assert "Qualidade média" in html and "Sua estratégia" in html
    assert "Aderência ao modelo" in html
    div = tela.cartao_divergencias(c)
    assert "PETR4" in div and "Manter a sua escolha" in div
    cand = tela.cartao_candidatos(c)
    assert "VALE3" in cand and "Candidato a inclusão" in cand
    fora = tela.cartao_confronto(cf.indisponivel("<b>x</b>"))
    assert "<b>x" not in fora


def test_avaliacoes_do_modelo_lembra_sucesso_e_nao_lembra_falha(monkeypatch):
    chamadas = []

    def ler(linha):
        chamadas.append(linha.ticker)
        if linha.ticker == "AAPL" and chamadas.count("AAPL") == 1:
            raise ConnectionError("sem rede")
        return _av(), {"roe": 20.0}
    monkeypatch.setattr(tela, "_ler_ativo_do_modelo", ler)
    monkeypatch.setattr(tela.st, "session_state", {})
    ref = _ref()
    avals, metricas = tela._avaliacoes_do_modelo(ref)
    assert avals["AAPL"] is None and avals["VALE3"] is not None
    assert "AAPL" not in metricas and metricas["VALE3"] == {"roe": 20.0}
    avals, _ = tela._avaliacoes_do_modelo(ref)
    assert avals["AAPL"] is not None
    assert chamadas.count("VALE3") == 1 and chamadas.count("AAPL") == 2


# -- substituições --------------------------------------------------------------

def _linhas(tk, valores):
    return [{"ativo": tk, "chave": k, "metrica": k.upper(), "unidade": "pct",
             "valor": v} for k, v in valores.items()]


def _analise(tk, valores, *, pares=(), setor="Mineração", vm=300.0,
             vi=200.0, nivel="segmento", relaxamentos=()):
    """O que ``recomendacao.confrontar`` e ``custo_da_venda`` leem de uma
    ``AnaliseAtivo``: a comparação com os pares e a posição."""
    dados = {"ativo": tk, "tipo": "acao", "moeda": "BRL",
             "grupo": {"nivel": nivel, "valor": setor,
                       "relaxamentos": list(relaxamentos), "pares": [
                           {"ticker": p, "metricas": m} for p, m in pares]},
             "linhas": _linhas(tk, valores)}
    ativo = SimpleNamespace(ticker=tk, nome=tk, setor=setor, moeda="BRL",
                            classe_politica="acoes_br", valor_mercado=vm,
                            valor_investido=vi)
    return SimpleNamespace(ativo=ativo, pares=SimpleNamespace(dados=dados))


PETR = {"roe": 10.0, "margem_liquida": 8.0, "p_l": 9.0, "p_vp": 1.5,
        "volatilidade": 30.0}
VALE_MELHOR = {"roe": 18.0, "margem_liquida": 12.0, "p_l": 7.0, "p_vp": 1.2,
               "volatilidade": 25.0}


def _conf_sub(codigo="manter", qual_sai=av_.ADEQUADA, qual_vale=av_.ADEQUADA,
              *, pares=(("VALE3", VALE_MELHOR),), metricas=None,
              setor_sai="Mineração", **kw):
    v = {"PETR4": _vered("PETR4", codigo, _av(qual_sai)),
         "ITSA4": _vered("ITSA4", "manter", _av(av_.FORTE))}
    avals = {"VALE3": _av(qual_vale), "WEGE3": _av(av_.FRAGIL),
             "AAPL": _av(av_.FORTE)}
    analises = {"PETR4": _analise("PETR4", PETR, pares=pares,
                                  setor=setor_sai)}
    return _conf(v, avals, analises=analises,
                 metricas_modelo=metricas or {}, **kw)


def test_par_do_mesmo_segmento_claramente_superior_vira_troca_gradual():
    c = _conf_sub()
    x = c.substituicao_de("PETR4")
    assert x is not None and x.entra == "VALE3" and x.mesmo_segmento
    # A Inteligência mantém PETR4: troca pelos aportes, sem vender.
    assert x.modo == cf.TROCA_GRADUAL and "Vender agora não se justifica" in x.texto
    assert any(m.startswith("Comparação: VALE3 supera em") for m in x.motivos)
    assert any("não no fato de ele estar no modelo" in m for m in x.motivos)
    # A divergência e o candidato apontam a troca.
    assert any("Substituição sugerida: VALE3" in m
               for m in c.da_carteira("PETR4").motivos)
    vale = next(d for d in c.candidatos if d.ticker == "VALE3")
    assert vale.conclusao == cf.INCLUIR and "no lugar de PETR4" in vale.texto


def test_venda_da_inteligencia_vira_troca_com_custo():
    x = _conf_sub("vender").substituicao_de("PETR4")
    assert x.modo == cf.TROCA and x.texto.startswith("Trocar PETR4 por VALE3")
    assert any("Venda de 100% da posição" in m for m in x.motivos)


def test_reduzir_parcial_troca_so_a_proporcao():
    v = _vered("PETR4", "reduzir_parcial", _av(), proporcao=0.3)
    c = cf.montar(_ref(), {"PETR4": v}, {"VALE3": _av()},
                  alocacao_estrategia=ESTRATEGIA,
                  analises={"PETR4": _analise("PETR4", PETR, pares=(
                      ("VALE3", VALE_MELHOR),))})
    x = c.substituicao_de("PETR4")
    assert x.modo == cf.TROCA and "30% da posição de PETR4" in x.texto
    assert any("Venda de 30% da posição" in m for m in x.motivos)


def test_qualidade_fragil_com_manter_segue_pelos_aportes():
    # Até o achado P1 do Codex (#697), frágil vendia por atalho; agora a
    # venda só sai da decisão, que já pesa a qualidade.
    x = _conf_sub("manter", qual_sai=av_.FRAGIL).substituicao_de("PETR4")
    assert x.modo == cf.TROCA_GRADUAL


def test_par_que_nao_e_claramente_superior_nao_troca():
    pior_no_preco = {**VALE_MELHOR, "p_l": 20.0, "p_vp": 3.0}
    assert _conf_sub(pares=(("VALE3", pior_no_preco),)
                     ).substituicao_de("PETR4") is None


def test_qualidade_menor_nunca_troca():
    assert _conf_sub(qual_sai=av_.FORTE, qual_vale=av_.ADEQUADA
                     ).substituicao_de("PETR4") is None


def test_mesmo_setor_fora_dos_pares_usa_as_metricas_do_proprio_ativo():
    # O setor do snapshot é "x"; a comparação ignora caixa e espaço.
    c = _conf_sub(pares=(), setor_sai=" X ", metricas={"VALE3": VALE_MELHOR})
    x = c.substituicao_de("PETR4")
    assert x is not None and x.mesmo_segmento
    assert any("supera em" in m for m in x.motivos)


def test_setores_diferentes_nunca_trocam():
    """Até 08/11/2026, setores diferentes trocavam com um nível de qualidade
    acima, e um banco virava elétrica. Agora a troca não atravessa setores,
    nem com qualidade bem melhor."""
    for qual in (av_.ADEQUADA, av_.FORTE):
        c = _conf_sub(pares=(), setor_sai="Petróleo", qual_vale=qual,
                      metricas={"VALE3": VALE_MELHOR})
        assert c.substituicao_de("PETR4") is None

def test_ativo_que_a_inteligencia_manda_comprar_nao_e_trocado():
    assert _conf_sub("comprar").substituicao_de("PETR4") is None


def test_entra_so_quem_passa_no_limite_e_na_estrategia():
    # Frágil (WEGE3) nunca entra; classe zerada na estratégia também não.
    assert not cf.elegivel_para_entrar(_av(av_.FRAGIL), None)
    assert not cf.elegivel_para_entrar(_av(criticos=("dívida",)), None)
    assert not cf.elegivel_para_entrar(_av(), 0.0)
    assert cf.elegivel_para_entrar(_av(av_.FORTE), 10.0)
    assert all(x.entra != "WEGE3" for x in _conf_sub().substituicoes)


def test_substituicao_nunca_contradiz_a_decisao():
    """Venda só sai quando a Inteligência já deixa o ativo (vender,
    substituir, reduzir); nos demais, aportes."""
    for codigo in ("manter", "acompanhar", "rebalancear", "reduzir_parcial",
                   "vender", "substituir"):
        x = _conf_sub(codigo).substituicao_de("PETR4")
        vende = codigo in ("vender", "substituir", "reduzir_parcial")
        assert x.modo == (cf.TROCA if vende else cf.TROCA_GRADUAL), codigo


def test_metricas_de_pares_le_o_valor_do_proprio_ativo():
    dados = _analise("VALE3", {"roe": 18.0, "p_l": None}).pares.dados
    assert cf.metricas_de_pares(dados) == {"roe": 18.0}
    assert cf.metricas_de_pares(None) == {}


def test_substituicoes_vao_para_a_llm_e_para_a_tela():
    c = _conf_sub()
    llm = c.para_llm("PETR4")
    assert llm["substituicao_do_ativo"]["entra"] == "VALE3"
    assert llm["substituicoes_sugeridas"][0]["sai"] == "PETR4"
    assert "nenhum ativo do modelo" in c.para_llm("ITSA4")["substituicao_do_ativo"]
    html = tela.cartao_substituicoes(c)
    assert "PETR4" in html and "VALE3" in html and "pelos aportes" in html
    assert "da carteira do Portfólio Global" in html
    # ITSA4 (holding) contra VALE3 (outro setor): sem troca.
    assert _conf(analises={"ITSA4": _analise("ITSA4", PETR,
                                             setor="Holdings")}
                 ).substituicao_de("ITSA4") is None
    assert _conf().substituicao_de("ITSA4") is None
    fortes = {tk: _vered(tk, "manter", _av(av_.FORTE))
              for tk in ("PETR4", "ITSA4", "HGLG11")}
    vazio = tela.cartao_substituicoes(_conf(fortes))
    assert "nenhuma troca sugerida" in vazio


def test_alerta_so_do_noticiario_nao_vira_venda():
    """Codex P1 (#697): alerta eliminatório só do noticiário leva a
    Inteligência a "acompanhar" até o documento oficial; a troca não pode
    mandar vender antes dela."""
    noticia = av_.Avaliacao(
        perfil=av_.GERAL,
        dimensoes=_av(av_.ADEQUADA).dimensoes,
        alertas=(av_.Alerta("recuperação judicial na manchete", critico=True,
                            noticia=True),))
    v = {"PETR4": _vered("PETR4", "acompanhar", noticia)}
    c = cf.montar(_ref(), v, {"VALE3": _av(av_.FORTE)},
                  alocacao_estrategia=ESTRATEGIA,
                  analises={"PETR4": _analise("PETR4", PETR, pares=(
                      ("VALE3", VALE_MELHOR),))})
    x = c.substituicao_de("PETR4")
    assert x is not None and x.modo == cf.TROCA_GRADUAL


def test_modo_da_troca_segue_so_a_decisao():
    """A venda sai da decisão da Inteligência, nunca de um atalho próprio:
    qualidade frágil com "acompanhar" continua pelos aportes."""
    x = _conf_sub("acompanhar", qual_sai=av_.FRAGIL).substituicao_de("PETR4")
    assert x.modo == cf.TROCA_GRADUAL


def test_mesmo_segmento_sem_metricas_nao_troca_so_pela_qualidade():
    """Codex P2 (#697): no mesmo segmento, a régua é a comparação métrica a
    métrica. Sem métricas para comparar, falta evidência: não troca (dado
    ausente não vira classificação)."""
    sem_metricas = _conf_sub(pares=(), setor_sai="x", qual_vale=av_.FORTE,
                             metricas={})
    assert sem_metricas.substituicao_de("PETR4") is None
    sem_sobreposicao = _conf_sub(pares=(), setor_sai="x", qual_vale=av_.FORTE,
                                 metricas={"VALE3": {"cap_rate": 9.0}})
    assert sem_sobreposicao.substituicao_de("PETR4") is None


def test_setor_desconhecido_nao_vira_segmento_diferente():
    """Codex P1 (#697, 2ª rodada): setor ausente não prova segmento
    diferente. Sem setor conhecido dos dois lados e sem o ativo entre os
    pares, falta evidência: não troca pela qualidade."""
    sem_setor_sai = _conf_sub(pares=(), setor_sai=None, qual_vale=av_.FORTE,
                              metricas={"VALE3": VALE_MELHOR})
    assert sem_setor_sai.substituicao_de("PETR4") is None
    # Setor vazio no snapshot vira "other" na chave canônica, que não é
    # setor: a referência guarda só o setor bruto.
    snaps = {**SNAPSHOTS, "b3": {**SNAPSHOTS["b3"],
                                 "VALE3": _snap("Vale", 0.3, setor="")}}
    assert _ref(snapshots=snaps).linha("VALE3").setor is None
    v = {"PETR4": _vered("PETR4", "manter", _av(av_.ADEQUADA))}
    c = cf.montar(_ref(snapshots=snaps), v, {"VALE3": _av(av_.FORTE)},
                  alocacao_estrategia=ESTRATEGIA,
                  analises={"PETR4": _analise("PETR4", PETR, pares=(),
                                              setor="Petróleo")})
    assert c.substituicao_de("PETR4") is None



# -- substituto do mesmo segmento fora do modelo (pedido de 08/11/2026) -----------

BANCO = {"roe": 12.0, "margem_liquida": 15.0, "p_l": 5.0, "p_vp": 0.8,
         "volatilidade": 30.0}
BANCO_MELHOR = {"roe": 21.0, "margem_liquida": 22.0, "p_l": 4.5,
                "p_vp": 0.8, "volatilidade": 25.0}
BANCO_CARO = {"roe": 21.0, "margem_liquida": 22.0, "p_l": 9.0, "p_vp": 2.0,
              "volatilidade": 25.0}


def _banco(codigo="reduzir_parcial", pares=(), qual=av_.FRAGIL, avals=None):
    """BBAS3 na carteira, frágil, sem banco no modelo (só PETR4/VALE3/
    WEGE3, de outros setores): o substituto tem de vir dos pares."""
    posicoes = POSICOES + [_pos("BBAS3", "Ações BR", 100.0)]
    v = {"BBAS3": _vered("BBAS3", codigo, _av(qual), proporcao=1 / 3),
         "PETR4": _vered("PETR4", "manter", _av(av_.FORTE))}
    avals = avals or {"VALE3": _av(av_.FORTE), "WEGE3": _av(av_.FORTE),
                      "AAPL": _av(av_.FORTE)}
    analises = {"BBAS3": _analise("BBAS3", BANCO, pares=pares,
                                  setor="Bancos")}
    return _conf(v, avals, ref=_ref(posicoes=posicoes), analises=analises)


def test_banco_nao_vira_eletrica_nem_mineradora():
    c = _banco(pares=())
    assert c.substituicao_de("BBAS3") is None
    sem = next(x for x in c.sem_substituto if x.sai == "BBAS3")
    assert "Nenhuma troca sugerida" in sem.texto


def test_par_do_segmento_fora_do_modelo_vira_substituto():
    c = _banco(pares=(("ITUB4", BANCO_MELHOR),))
    x = c.substituicao_de("BBAS3")
    assert x is not None and x.entra == "ITUB4" and x.origem == cf.PARES
    assert x.modo == cf.TROCA and "fora do Portfólio Global" in x.texto
    assert any("só pelas métricas publicadas" in m for m in x.motivos)
    assert not any("estar no modelo" in m for m in x.motivos)
    html = tela.cartao_substituicoes(c)
    assert "par do segmento, fora do modelo" in html


def test_sem_par_superior_mostra_alternativas_do_segmento():
    """O par tem qualidade melhor mas é mais caro: não é claramente
    superior. A tela não sugere a troca; mostra a alternativa e o que ela
    perde."""
    c = _banco(pares=(("ITUB4", BANCO_CARO),))
    assert c.substituicao_de("BBAS3") is None
    sem = next(x for x in c.sem_substituto if x.sai == "BBAS3")
    assert "perdem no preço ou no risco" in sem.texto
    assert sem.alternativas and sem.alternativas[0].startswith("ITUB4")
    assert "perde em" in sem.alternativas[0]
    html = tela.cartao_substituicoes(c)
    assert "Sem substituto claramente superior no segmento" in html
    assert c.para_llm("BBAS3")["sem_substituto_no_segmento"]


def test_ativo_fora_de_pauta_nao_ganha_alternativas():
    c = _banco("manter", pares=(("ITUB4", BANCO_CARO),), qual=av_.ADEQUADA)
    assert not any(x.sai == "BBAS3" for x in c.sem_substituto)


def test_modelo_do_mesmo_segmento_vence_par_de_fora():
    """VALE3 no modelo e entre os pares de PETR4, e um par de fora também
    superior: vence o do modelo, que passou pela avaliação completa."""
    pares = (("VALE3", VALE_MELHOR), ("PRIO3", VALE_MELHOR))
    x = _conf_sub("vender", pares=pares).substituicao_de("PETR4")
    assert x.entra == "VALE3" and x.origem == cf.MODELO


# -- achados do Codex no #701 -----------------------------------------------------

def test_ativo_do_modelo_reprovado_nao_volta_como_par():
    """P1: WEGE3 está no modelo, mas frágil (reprovado para entrar). Ele
    também é par de BBAS3: não pode voltar pela porta dos pares, só com
    métricas, nem ser chamado de "fora do Portfólio Global"."""
    c = _banco(pares=(("WEGE3", BANCO_MELHOR),),
               avals={"VALE3": _av(av_.FORTE), "WEGE3": _av(av_.FRAGIL)})
    assert c.substituicao_de("BBAS3") is None
    assert not any(x.entra == "WEGE3" for x in c.substituicoes)


def test_classe_zerada_na_estrategia_nao_ganha_troca_de_nenhuma_fonte():
    """P2: com acoes_br = 0 na estratégia, nenhum substituto da classe,
    nem dos pares de fora do modelo."""
    v = {"BBAS3": _vered("BBAS3", "reduzir_parcial", _av(av_.FRAGIL),
                         proporcao=1 / 3)}
    posicoes = POSICOES + [_pos("BBAS3", "Ações BR", 100.0)]
    c = cf.montar(_ref(posicoes=posicoes), v, {},
                  alocacao_estrategia={**ESTRATEGIA, "acoes_br": 0.0},
                  analises={"BBAS3": _analise(
                      "BBAS3", BANCO, pares=(("ITUB4", BANCO_MELHOR),),
                      setor="Bancos")})
    assert c.substituicao_de("BBAS3") is None


def test_poucas_metricas_nao_viram_desvantagem_de_preco_ou_risco():
    """P2: com só 2 métricas em comum, não dá para dizer que o par perde
    no preço ou no risco; a tela diz que faltam métricas."""
    c = _banco(pares=(("ITUB4", {"roe": 21.0, "margem_liquida": 22.0}),))
    sem = next(x for x in c.sem_substituto if x.sai == "BBAS3")
    assert "perdem no preço ou no risco" not in sem.texto
    assert "métricas" in sem.texto and "não dá para concluir" in sem.texto



def test_grupo_de_pares_ampliado_nao_conta_como_mesmo_segmento():
    """Codex P1 (#701, 2ª rodada): com poucos pares, a seleção alarga para
    um nível mais largo (FII de logística junto com shopping). Par desse
    grupo não prova mesmo segmento: nem par de fora, nem ativo do modelo
    por estar no grupo."""
    from core.inteligencia_ativos import pares as prs
    largo = ("Menos de 3 pares no mesmo segmento (Bancos): nível mais largo.",)
    posicoes = POSICOES + [_pos("BBAS3", "Ações BR", 100.0)]
    v = {"BBAS3": _vered("BBAS3", "reduzir_parcial", _av(av_.FRAGIL),
                         proporcao=1 / 3)}
    c = cf.montar(_ref(posicoes=posicoes), v, {"VALE3": _av(av_.FORTE)},
                  alocacao_estrategia=ESTRATEGIA,
                  analises={"BBAS3": _analise(
                      "BBAS3", BANCO, setor="Bancos", nivel="setor",
                      relaxamentos=largo,
                      pares=(("ITUB4", BANCO_MELHOR),
                             ("VALE3", BANCO_MELHOR)))})
    assert c.substituicao_de("BBAS3") is None
    # o campo estruturado e a leitura de dados antigos, só com o texto
    assert prs.GrupoPares.de_dict({"relaxamentos": list(largo)}).ampliado
    assert not prs.GrupoPares.de_dict(
        {"relaxamentos": ["Ativo sem porte conhecido: tamanho não filtra."]}
    ).ampliado



def test_se_sair_qual_substituir_tambem_ignora_grupo_ampliado():
    """Gêmeo: a função única ``resumida.substitutos`` alimenta o "Se sair,
    qual substituir?" da decisão; grupo ampliado não dá substituto."""
    from core.inteligencia_ativos import resumida as rs
    largo = ("Menos de 3 pares no mesmo segmento (Logística): nível mais "
             "largo.",)
    a = _analise("HGLG11", BANCO, setor="Logística", nivel="tipo",
                 relaxamentos=largo, pares=(("XPML11", BANCO_MELHOR),))
    assert rs.substitutos(a, set(), 5) == ()
    fino = _analise("HGLG11", BANCO, setor="Logística",
                    pares=(("BTLG11", BANCO_MELHOR),))
    assert [p.ticker for p in rs.substitutos(fino, set(), 5)] == ["BTLG11"]
