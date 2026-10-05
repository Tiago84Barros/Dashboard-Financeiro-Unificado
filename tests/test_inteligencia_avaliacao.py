"""Avaliação multicritério do ativo: qualidade, valuation e mercado.

01/10/2026, pedido do usuário: "avaliar a qualidade do ativo através de todas
as métricas disponíveis ... comparar com outras empresas, avaliar setores
críticos, justificar o argumento ... não só do ponto de vista fundamentalista,
mas também de sentimento do mercado". Cada critério carrega o número e a
referência; alerta eliminatório não é compensado por média.
"""
from dataclasses import replace

import pytest

from core.inteligencia_ativos import analise, secoes
from core.inteligencia_ativos import avaliacao as av
from core.inteligencia_ativos import fundamentos as f
from core.inteligencia_ativos import informacoes as inf
from core.inteligencia_ativos import modelos as m
from core.inteligencia_ativos import resumida as rs
from core.inteligencia_ativos import valuation as val
from tests.test_inteligencia_ativos_adequacao import _analise, _ctx, _pos
from tests.test_inteligencia_ativos_resumida import _sem_cor_literal, _sug
from views import inteligencia_ativos_resumida as tela

CARTEIRA = {"total_mercado": 11000.0, "posicoes": [
    _pos("WEGE3", "Ações BR", 10.0, "Bens Industriais"),
    _pos("TAEE11", "Ações BR", 10.0, "Energia Elétrica"),
    _pos("CSNA3", "Ações BR", 10.0, "Siderurgia"),
    _pos("BBAS3", "Ações BR", 10.0, "Bancos"),
    _pos("HGLG11", "FII", 30.0, "Logística"),
    _pos("TESOURO SELIC 2029", "Tesouro Direto", 30.0),
]}


@pytest.fixture(scope="module")
def an():
    # Provedores reais leem os artefatos publicados em data/public: quando a
    # rotina noturna commitou notícias da WEGE3 (05/10/2026), o teste "sem
    # dado" passou a ver dado. Aqui toda seção nasce vazia; quem precisa de
    # dado injeta com _com.
    with pytest.MonkeyPatch.context() as mp:
        for chave in secoes.SECOES:
            mp.setitem(secoes.PROVEDORES, chave, secoes._pendente(chave))
        ctx = _ctx(CARTEIRA)
        return {pp["ticker"]: _analise(pp["ticker"], ctx)
                for pp in CARTEIRA["posicoes"]}


def _fund(tipo=f.ACAO, **valores):
    return f.montar(tipo, {k: f.Dado(v, "teste", "2025") for k, v in
                           valores.items()}).como_dict()


def _linha(chave, rotulo, atual, hist=None, pares=None, unidade=f.X, **kw):
    est_h = val.estatistica(hist) if hist else None
    est_p = val.estatistica(pares) if pares else None
    return val.LinhaValuation(
        chave, rotulo, unidade, True, atual=atual, historico=est_h,
        pares=est_p,
        posicao_historica=val.posicao(atual, est_h.mediana) if est_h
        else val.SEM_DADO,
        posicao_pares=val.posicao(atual, est_p.mediana) if est_p
        else val.SEM_DADO, **kw)


def _com(a, fund=None, linhas=None, noticias=None):
    if fund is not None:
        a = replace(a, fundamentos=replace(a.fundamentos, dados=fund))
    if linhas is not None:
        v = val.Valuation(f.ACAO, "BRL", tuple(linhas))
        a = replace(a, valuation=replace(a.valuation, dados=v.como_dict()))
    if noticias is not None:
        ns = inf.Noticias(itens=tuple(noticias), janela_dias=60)
        a = replace(a, noticias=replace(a.noticias, dados=ns.como_dict()))
    return a


def _noticia(titulo, categoria="resultado"):
    return inf.Noticia(headline=titulo, date="2026-09-20", source="S",
                       url=None, impact_level=inf.LOW, affected_dimension=(),
                       summary=None, categoria=categoria, motivo="m")


BOA = dict(roe=22.0, roic=18.0, crescimento_receita=9.0, lucro_liquido=100.0,
           fluxo_caixa_operacional=110.0, fluxo_caixa_livre=60.0,
           divida_liquida_ebitda=0.8, cobertura_juros=8.0, payout=50.0)


# -- perfil setorial ------------------------------------------------------------------

def test_perfil_le_o_setor(an):
    assert av.avaliar(an["BBAS3"]).perfil == av.FINANCEIRA
    assert av.avaliar(an["TAEE11"]).perfil == av.REGULADA
    assert av.avaliar(an["CSNA3"]).perfil == av.CICLICA
    assert av.avaliar(an["WEGE3"]).perfil == av.GERAL


def test_sem_dado_nao_inventa_leitura(an):
    a = av.avaliar(an["WEGE3"])
    assert a.qualidade == av.INSUFICIENTE
    assert a.preco == av.SEM_LEITURA and a.mercado == av.SEM_LEITURA
    assert not a.criticos


# -- qualidade ------------------------------------------------------------------------

def test_empresa_boa_sai_forte_com_cada_criterio_justificado(an):
    a = av.avaliar(_com(an["WEGE3"], _fund(**BOA)))
    q = a.dimensao(av.QUALIDADE)
    assert q.leitura == av.FORTE and q.desfavoraveis == 0
    textos = " | ".join(c.texto for c in q.criterios)
    assert "ROE de 22,0%" in textos and "confortável" in textos
    assert "o lucro vira caixa" in textos


def test_alerta_eliminatorio_nao_se_compensa_com_media(an):
    # tudo bom, menos a alavancagem: frágil mesmo assim
    a = av.avaliar(_com(an["WEGE3"],
                        _fund(**{**BOA, "divida_liquida_ebitda": 5.0})))
    assert a.qualidade == av.FRAGIL
    assert a.criticos and "acima do limite de 4,5" in a.criticos[0].texto


def test_setor_regulado_tolera_alavancagem_maior(an):
    fund = _fund(**{**BOA, "divida_liquida_ebitda": 3.5})
    geral = av.avaliar(_com(an["WEGE3"], fund)).dimensao(av.QUALIDADE)
    regulada = av.avaliar(_com(an["TAEE11"], fund)).dimensao(av.QUALIDADE)
    assert any(c.sinal < 0 and "Dívida" in c.texto for c in geral.criterios)
    assert not any("Dívida" in c.texto and c.sinal < 0
                   for c in regulada.criterios)


def test_banco_nao_e_medido_por_divida_nem_caixa(an):
    a = av.avaliar(_com(an["BBAS3"], _fund(roe=8.0, divida_liquida_ebitda=9.0,
                                           fluxo_caixa_livre=-1.0,
                                           lucro_liquido=10.0)))
    textos = " ".join(c.texto for c in a.dimensao(av.QUALIDADE).criterios)
    assert "Dívida" not in textos and "caixa livre" not in textos
    assert "ROE de 8,0%" in textos and "baixo" in textos
    assert not a.criticos


def test_payout_acima_do_lucro_com_caixa_negativo(an):
    fund = _fund(**{**BOA, "payout": 140.0, "fluxo_caixa_livre": -5.0})
    geral = av.avaliar(_com(an["WEGE3"], fund))
    regulada = av.avaliar(_com(an["TAEE11"], fund))
    assert geral.criticos and "insustentável" in geral.criticos[0].texto
    # setor regulado: ciclo de investimento, alerta sem eliminar
    assert not regulada.criticos
    assert any("ciclo de investimento" in al.texto for al in regulada.alertas)


def test_lucro_pequeno_nao_vira_conversao_absurda(an):
    a = av.avaliar(_com(an["WEGE3"], _fund(lucro_liquido=1.0,
                                           fluxo_caixa_operacional=80.0)))
    c = [c for c in a.dimensao(av.QUALIDADE).criterios
         if "caixa operacional" in c.texto.lower()]
    assert c and c[0].sinal == 0 and "80" not in c[0].texto


def test_fii_vacancia_e_inadimplencia(an):
    fund = _fund(f.FII, vacancia_financeira=35.0, inadimplencia=2.0,
                 alavancagem=5.0, wault=6.0)
    a = av.avaliar(_com(an["HGLG11"], fund))
    assert a.perfil == av.FII and a.qualidade == av.FRAGIL
    assert "vacância financeira de 35,0%" in a.criticos[0].texto


# -- valuation ------------------------------------------------------------------------

def test_duas_reguas_concordando_valem_mais_que_uma(an):
    duas = av.avaliar(_com(an["WEGE3"], linhas=[
        _linha("p_l", "P/L", 10.0, [20, 20, 20], [18, 18, 18])]))
    uma = av.avaliar(_com(an["WEGE3"], linhas=[
        _linha("p_l", "P/L", 10.0, [20, 20, 20])]))
    assert duas.preco == av.BARATO and uma.preco == av.BARATO
    meia = av.avaliar(_com(an["WEGE3"], linhas=[
        _linha("p_l", "P/L", 10.0, [20, 20, 20], [5, 5, 5])]))
    assert meia.preco == av.JUSTO      # réguas discordando se anulam


def test_dividend_yield_alto_e_barato(an):
    a = av.avaliar(_com(an["WEGE3"], linhas=[
        _linha("dividend_yield", "Dividend yield", 3.0, [6, 6, 6], [6, 6, 6],
               unidade=f.PCT)]))
    assert a.preco == av.CARO
    assert "3,0%" in a.dimensao(av.VALUATION).criterios[0].texto


def test_pl_baixo_em_ciclica_nao_conta_como_barato(an):
    linhas = [_linha("p_l", "P/L", 4.0, [9, 9, 9], [8, 8, 8])]
    ciclica = av.avaliar(_com(an["CSNA3"], linhas=linhas))
    assert ciclica.preco == av.JUSTO
    assert "pico" in ciclica.dimensao(av.VALUATION).nota
    assert av.avaliar(_com(an["WEGE3"], linhas=linhas)).preco == av.BARATO


def test_pl_abaixo_de_3_nao_conta_como_barato(an):
    a = av.avaliar(_com(an["WEGE3"], linhas=[
        _linha("p_l", "P/L", 1.9, [9, 9, 9], [8, 8, 8])]))
    assert a.preco == av.JUSTO
    assert "não recorrente" in a.dimensao(av.VALUATION).nota


def test_fragil_e_barato_avisa_armadilha_de_valor(an):
    a = av.avaliar(_com(an["WEGE3"],
                        _fund(**{**BOA, "divida_liquida_ebitda": 6.0}),
                        [_linha("p_vp", "P/VP", 0.5, [1, 1, 1], [1, 1, 1])]))
    assert a.qualidade == av.FRAGIL and a.preco == av.BARATO
    assert "armadilha de valor" in a.dimensao(av.QUALIDADE).nota


# -- mercado e notícias ---------------------------------------------------------------

def test_recuperacao_judicial_no_noticiario_e_eliminatoria(an):
    a = av.avaliar(_com(an["WEGE3"], _fund(**BOA), noticias=[
        _noticia("Empresa pede recuperação judicial", "recuperacao_judicial")]))
    assert a.qualidade == av.FRAGIL and a.mercado == av.NEGATIVO
    assert "recuperação judicial" in a.criticos[0].texto.lower()


def test_sem_noticia_diz_por_que(an):
    m_ = av.avaliar(an["WEGE3"]).dimensao(av.MERCADO)
    assert m_.leitura == av.SEM_LEITURA and "60 dias" in m_.nota


# -- decisão --------------------------------------------------------------------------

def _peso(a, peso):
    return replace(a, ativo=replace(a.ativo, peso_atual=peso),
                   acao=m.Acao(m.EXPOSICAO_ADEQUADA, ("x",), False))


def test_alerta_eliminatorio_vira_avaliar_troca(an):
    a = _com(_peso(an["WEGE3"], 2.0), _fund(**BOA), noticias=[
        _noticia("Empresa pede recuperação judicial", "recuperacao_judicial")])
    d = rs.decisao(a, _sug(5.0))     # o peso pedia comprar
    assert d.codigo == rs.VENDER and "eliminatório" in d.detalhe
    assert "Nenhum ponto bom compensa" in d.motivo and "custo" in d.motivo
    # já acima do peso: o alerta vence o "vender parte"
    d = rs.decisao(replace(a, ativo=replace(a.ativo, peso_atual=20.0)),
                   _sug(5.0))
    assert d.detalhe == "avaliar troca: alerta eliminatório"
    assert d.motivo.lower().startswith("recuperação judicial")


def test_qualidade_fragil_nao_deixa_aportar(an):
    fund = _fund(roe=4.0, roic=3.0, crescimento_receita=-5.0,
                 fluxo_caixa_livre=-10.0)
    a = _com(_peso(an["WEGE3"], 2.0), fund)
    d = rs.decisao(a, _sug(5.0))
    assert d.codigo == rs.MANTER and "não aportar" in d.detalhe
    assert d.motivo.startswith("Qualidade frágil:")


def test_fragil_e_caro_vira_vender(an):
    fund = _fund(roe=4.0, roic=3.0, crescimento_receita=-5.0,
                 fluxo_caixa_livre=-10.0)
    a = _com(_peso(an["WEGE3"], 5.0), fund,
             [_linha("p_l", "P/L", 30.0, [15, 15, 15], [14, 14, 14])])
    d = rs.decisao(a, _sug(5.0))
    assert d.codigo == rs.VENDER and "preço acima do normal" in d.detalhe


def test_comprar_com_preco_caro_aporta_aos_poucos(an):
    a = _com(_peso(an["WEGE3"], 2.0), _fund(**BOA),
             [_linha("p_l", "P/L", 30.0, [15, 15, 15], [14, 14, 14])])
    d = rs.decisao(a, _sug(5.0))
    assert d.codigo == rs.COMPRAR and "aportar aos poucos" in d.detalhe


def test_sem_dados_a_decisao_por_peso_nao_muda(an):
    a = _peso(an["WEGE3"], 2.0)
    assert rs.decisao(a, _sug(5.0)).codigo == rs.COMPRAR
    assert rs.decisao(_peso(an["WEGE3"], 5.0), _sug(5.0)).codigo == rs.MANTER


def test_argumentos_trazem_os_criterios_do_lado_da_leitura(an):
    a = av.avaliar(_com(an["WEGE3"], _fund(**BOA)))
    args = av.argumentos(a)
    assert args[0].startswith("Qualidade e fundamentos: forte — ROE")
    assert len(args) == 3


# -- LLM e tela -----------------------------------------------------------------------

def test_llm_recebe_a_avaliacao(an):
    ctx = _ctx(CARTEIRA)
    a = _com(an["WEGE3"], _fund(**BOA))
    txt = analise.texto_para_llm(a, ctx)
    assert "[Avaliação multicritério (regras) — WEGE3]" in txt
    assert "Régua setorial" in txt and "ROE de 22,0%" in txt


def test_cartao_da_avaliacao_so_usa_tokens_e_escapa(an):
    a = _com(an["WEGE3"], _fund(**BOA), noticias=[
        _noticia("<b>pede</b> recuperação judicial", "recuperacao_judicial")])
    h = tela.cartao_avaliacao(av.avaliar(a))
    assert _sem_cor_literal(h), h
    assert "&lt;b&gt;pede" in h and "<b>pede" not in h
    assert "Alerta eliminatório" in h and "Régua setorial" not in h
    assert "Qualidade e fundamentos" in h and "Mercado e notícias" in h


def test_noticia_da_saida_da_recuperacao_nao_elimina(an):
    a = av.avaliar(_com(an["WEGE3"], _fund(**BOA), noticias=[
        _noticia("Recuperação judicial é página virada? CEO fala do futuro",
                 "recuperacao_judicial")]))
    assert not a.criticos and a.qualidade == av.FORTE
    assert any("fala da saída" in al.texto for al in a.alertas)
