"""Recomendação de movimentação com cautela: esticamento, substituto, custo
da venda, convicção e a árvore que os junta.

29/10/2026, pedido do usuário: "Não recomende vender uma empresa de qualidade
apenas porque a cotação ultrapassou um percentual ou preço previamente
definido. Um limite pode servir como alerta para uma análise mais profunda,
mas não deve gerar uma ordem automática de venda." Cada ação vem com
justificativa, convicção, condições e limitações; alerta fica à parte.
"""
from dataclasses import replace

import pytest

from core.inteligencia_ativos import avaliacao as av
from core.inteligencia_ativos import fundamentos as f
from core.inteligencia_ativos import pares as p
from core.inteligencia_ativos import recomendacao as rec
from core.inteligencia_ativos import resumida as rs
from core.inteligencia_ativos import secoes
from core.inteligencia_ativos import veredito as vd
from tests.test_inteligencia_ativos_adequacao import _analise, _ctx, _pos
from tests.test_inteligencia_ativos_resumida import _sem_cor_literal, _sug
from tests.test_inteligencia_avaliacao import BOA, _com, _fund, _linha
from tests.test_inteligencia_veredito import _av
from views import inteligencia_ativos_resumida as tela

CARTEIRA = {"total_mercado": 11000.0, "posicoes": [
    _pos("WEGE3", "Ações BR", 10.0, "Bens Industriais"),
    _pos("CSNA3", "Ações BR", 10.0, "Siderurgia"),
    _pos("HGLG11", "FII", 30.0, "Logística"),
    _pos("AAPL", "Ações BR", 10.0, "Tecnologia", moeda="USD"),
    _pos("TESOURO SELIC 2029", "Tesouro Direto", 40.0),
]}

FRAGIL = dict(roe=4.0, roic=3.0, crescimento_receita=-5.0,
              fluxo_caixa_livre=-10.0)


@pytest.fixture(scope="module")
def an():
    # Seções vazias: quem precisa de dado injeta (ver test_inteligencia_avaliacao).
    with pytest.MonkeyPatch.context() as mp:
        for chave in secoes.SECOES:
            mp.setitem(secoes.PROVEDORES, chave, secoes._pendente(chave))
        ctx = _ctx(CARTEIRA)
        return {pp["ticker"]: _analise(pp["ticker"], ctx)
                for pp in CARTEIRA["posicoes"]}


def _caro(percentil, chave="p_l", rotulo="P/L"):
    """Múltiplo acima da mediana do histórico e dos pares (caro), no
    ``percentil`` do próprio histórico."""
    return _linha(chave, rotulo, 30.0, [10, 15, 20, 25, 35], [14, 14, 14],
                  percentil_historico=percentil)


def _peso(a, peso):
    return replace(a, ativo=replace(a.ativo, peso_atual=peso))


def _solida(an, peso, *linhas):
    return _peso(_com(an["WEGE3"], _fund(**BOA), list(linhas)), peso)


def _est(a):
    return rec.esticamento(a, av.avaliar(a))


# -- esticamento ----------------------------------------------------------------------

def test_esticado_e_caro_e_no_topo_do_proprio_historico(an):
    a = _com(an["WEGE3"], _fund(**BOA), [_caro(85.0)])
    assert av.avaliar(a).preco == av.CARO
    e = _est(a)
    assert e.nivel == rec.ESTICADO and e.medido and e.rotulo == "esticado"
    assert "P/L de 30,00x no percentil 85 do próprio histórico" in e.evidencias[0]
    assert "mediana 20,00x" in e.evidencias[0]
    # percentil 90, ou dois múltiplos no 80: muito esticado
    assert _est(_com(a, linhas=[_caro(95.0)])).nivel == rec.MUITO_ESTICADO
    dois = [_caro(85.0), _caro(82.0, "p_vp", "P/VP")]
    assert _est(_com(a, linhas=dois)).nivel == rec.MUITO_ESTICADO
    # abaixo do percentil 80 não estica, mesmo caro
    e = _est(_com(a, linhas=[_caro(70.0)]))
    assert e.nivel == 0 and e.medido and not e.evidencias


def test_yield_baixo_conta_como_preco_alto(an):
    dy = _linha("dividend_yield", "Dividend yield", 2.0, [4, 5, 6], [6, 6, 6],
                unidade=f.PCT, percentil_historico=8.0)
    e = _est(_com(an["WEGE3"], _fund(**BOA), [_caro(50.0), dy]))
    assert e.nivel == rec.MUITO_ESTICADO
    assert any("yield baixo é preço alto" in x for x in e.evidencias)


def test_topo_do_historico_sem_estar_caro_e_so_alerta(an):
    # caro contra o próprio histórico, barato contra os pares: o valuation
    # fica "em linha" e o topo vira evidência, não motivo de realizar
    so_hist = _linha("p_l", "P/L", 30.0, [10, 15, 20, 25, 35], [40, 40, 40],
                     percentil_historico=95.0)
    a = _com(an["WEGE3"], _fund(**BOA), [so_hist])
    assert av.avaliar(a).preco != av.CARO
    e = _est(a)
    assert e.nivel == 0 and e.evidencias


def test_pl_de_ciclica_nao_mede_esticamento(an):
    a = _com(an["CSNA3"], _fund(**BOA), [_caro(95.0)])
    e = _est(a)
    assert e.nivel == 0 and not e.medido


# -- a árvore: qualidade antes do preço ------------------------------------------------

def test_empresa_solida_acima_da_meta_rebalanceia_sem_vender(an):
    d = rs.decisao(_solida(an, 8.5, _caro(70.0)), _sug(2.5))
    assert d.codigo == rs.REBALANCEAR and not d.movimenta
    assert d.proporcao is None and d.custos is None
    assert "sem imposto nem custo de saída" in d.motivo
    assert any("não vender por isso" in x for x in d.alertas)
    assert d.condicoes and d.justificativas


def test_peso_acima_e_preco_esticado_reduz_so_o_excesso(an):
    d = rs.decisao(_solida(an, 3.5, _caro(85.0)), _sug(2.5))
    assert d.codigo == rs.REDUZIR_PARCIAL
    assert d.detalhe.startswith("reduzir cerca de 30%")
    assert d.proporcao == pytest.approx(0.30)      # 1,0 pp de 3,5%
    assert "sem abandonar a tese" in d.motivo
    # o excesso maior que 1/3 da posição para em 1/3: o núcleo fica
    d = rs.decisao(_solida(an, 8.5, _caro(85.0)), _sug(2.5))
    assert d.proporcao == pytest.approx(rs.FRACAO_MAX_REDUCAO)


def test_na_meta_muito_esticado_reduz_um_quinto_e_mantem_o_nucleo(an):
    d = rs.decisao(_solida(an, 2.5, _caro(95.0)), _sug(2.5))
    assert d.codigo == rs.REDUZIR_PARCIAL and "manter o núcleo" in d.detalhe
    assert d.proporcao == pytest.approx(rs.FRACAO_REDUCAO_ESTICADO)
    assert d.custos is not None and d.custos.fracao == pytest.approx(0.20)
    assert "percentil 95" in d.motivo
    assert any("convenção do sistema, não calibrada" in x
               for x in d.limitacoes)
    # esticado, mas não muito: manter e aportar em outro ativo
    d = rs.decisao(_solida(an, 2.5, _caro(85.0)), _sug(2.5))
    assert d.codigo == rs.MANTER and "sem novos aportes" in d.detalhe
    assert d.proporcao is None


def test_alerta_e_acao_ficam_separados(an):
    d = rs.decisao(_solida(an, 8.5, _caro(70.0)), _sug(2.5))
    assert not any("rebalancear" == x.lower()[:11] for x in d.alertas)
    html = tela.cartao_alertas(d)
    assert tela.TITULO_ALERTAS in html and "var(--app-warning)" in html
    assert _sem_cor_literal(html)
    sem = rs.decisao(_solida(an, 2.5), _sug(2.5))
    assert tela.cartao_alertas(replace(sem, alertas=())) == ""


# -- substituto ---------------------------------------------------------------------

def _com_par(a, do_ativo, do_par, ticker="RAPT4"):
    grupo = p.GrupoPares("segmento", "Bens Industriais", (
        p.Par(ticker, "Par", 0.2, "porte parecido", do_par),))
    cand = p.Candidato(a.ativo.ticker, a.ativo.ticker, f.ACAO, "B3",
                       (("segmento", "Bens Industriais"),), 100.0, 30.0,
                       "volatilidade", do_ativo, a.ativo.ticker[:4], 2025)
    c = p.comparar(cand, grupo)
    return replace(a, pares=replace(a.pares, dados=c.como_dict()))


ATIVO = {"roe": 4.0, "margem_liquida": 3.0, "p_l": 12.0, "p_vp": 1.5}
SUPERIOR = {"roe": 18.0, "margem_liquida": 12.0, "p_l": 10.0, "p_vp": 1.5}
SO_MAIS_BARATO = {"roe": 3.0, "margem_liquida": 2.0, "p_l": 5.0, "p_vp": 0.7}


def test_confronto_exige_qualidade_melhor_sem_preco_nem_risco_pior(an):
    a = _com_par(an["WEGE3"], ATIVO, SUPERIOR)
    cf = rec.confrontar(a, rs.substitutos(a, ())[0])
    assert cf.claramente_superior and cf.n == 4
    assert cf.saldo_qualidade == 2 and cf.saldo_valuation == 1
    assert cf.resumo.startswith("RAPT4 supera em ROE")
    barato = rec.confrontar(_com_par(an["WEGE3"], ATIVO, SO_MAIS_BARATO),
                            p.Par("RAPT4", "Par", 0.2, "", SO_MAIS_BARATO))
    assert not barato.claramente_superior and barato.saldo_valuation == 2
    # menos de três métricas comparadas não sustenta troca
    pouco = rec.confrontar(a, p.Par("RAPT4", "Par", 0.2, "", {"roe": 30.0}))
    assert pouco.n == 1 and not pouco.claramente_superior
    # P/L negativo não é "barato"
    neg = rec.confrontar(a, p.Par("X", "X", 0.2, "", {"p_l": -3.0}))
    assert neg is None


def test_fragil_com_par_superior_vira_substituir(an):
    a = _com_par(_peso(_com(an["WEGE3"], _fund(**FRAGIL)), 2.5),
                 ATIVO, SUPERIOR)
    d = rs.decisao(a, _sug(2.5), na_carteira=("WEGE3",))
    assert d.codigo == rs.SUBSTITUIR and d.movimenta
    assert d.substituto is not None and d.substituto.ticker == "RAPT4"
    assert d.conviccao != rec.ALTA          # o par não passou pela avaliação
    assert any("RAPT4 foi comparado só pelas métricas" in x
               for x in d.limitacoes)
    html = tela.cartao_recomendacao(d)
    assert "Substituto comparado" in html and "RAPT4 supera em" in html
    # o par já na carteira não é substituto
    d = rs.decisao(a, _sug(2.5), na_carteira=("WEGE3", "RAPT4"))
    assert d.codigo == rs.ACOMPANHAR and d.substituto is None


def test_par_so_mais_barato_nao_justifica_troca(an):
    a = _com_par(_peso(_com(an["WEGE3"], _fund(**FRAGIL)), 2.5),
                 ATIVO, SO_MAIS_BARATO)
    d = rs.decisao(a, _sug(2.5))
    assert d.codigo == rs.ACOMPANHAR and d.substituto is None
    assert "não aportar" in d.detalhe


def test_empresa_solida_nem_procura_troca(an):
    a = _com_par(_solida(an, 2.5), ATIVO, SUPERIOR)
    d = rs.decisao(a, _sug(2.5))
    assert not d.movimenta and d.substituto is None


# -- custo da venda ------------------------------------------------------------------

def _posicao(a, mercado, investido):
    return replace(a, ativo=replace(a.ativo, valor_mercado=mercado,
                                    valor_investido=investido))


def test_custo_de_acao_acima_da_isencao_paga_15_por_cento(an):
    c = rec.custo_da_venda(_posicao(an["WEGE3"], 100_000.0, 60_000.0), 0.5)
    assert c.valor_bruto == pytest.approx(50_000) and c.ganho == pytest.approx(20_000)
    assert c.ir == pytest.approx(3_000)
    assert "IR de 15% ≈ R$ 3.000" in c.linhas[1]
    assert any("IRRF" in x for x in c.linhas)
    assert c.custo_ordem is not None and c.custo_ordem > 0
    assert not c.limitacoes


def test_custo_de_acao_abaixo_da_isencao_depende_das_outras_vendas(an):
    c = rec.custo_da_venda(_posicao(an["WEGE3"], 30_000.0, 20_000.0), 0.5)
    assert "isento se as vendas de ações no mês somarem até R$ 20.000" in c.linhas[1]
    assert any("outras vendas de ações do mês" in x for x in c.limitacoes)


def test_custo_com_prejuizo_nao_tem_ir(an):
    c = rec.custo_da_venda(_posicao(an["WEGE3"], 50_000.0, 80_000.0), 0.2)
    assert c.ir == 0.0 and "prejuízo de R$ 6.000" in c.linhas[1]


def test_fii_paga_20_por_cento_e_spread_nao_e_inventado(an):
    c = rec.custo_da_venda(_posicao(an["HGLG11"], 40_000.0, 30_000.0), 1.0)
    assert c.ir == pytest.approx(2_000) and "FII não tem isenção" in c.linhas[1]
    assert c.custo_ordem is None
    assert any("Spread de venda não calibrado" in x for x in c.limitacoes)


def test_exterior_e_sem_custo_de_aquisicao_nao_calculam_imposto(an):
    c = rec.custo_da_venda(_posicao(an["AAPL"], 10_000.0, 5_000.0), 0.5)
    assert c.ir is None and c.custo_ordem is None
    assert "exterior não calculados" in c.limitacoes[0]
    c = rec.custo_da_venda(_posicao(an["WEGE3"], 10_000.0, None), 0.5)
    assert c.ganho is None and c.ir is None
    assert any("Sem custo de aquisição" in x for x in c.limitacoes)
    c = rec.custo_da_venda(_posicao(an["WEGE3"], None, None), 0.5)
    assert c.linhas == () and "Sem valor de mercado" in c.limitacoes[0]


# -- convicção -----------------------------------------------------------------------

def test_conviccao_pela_cobertura_e_pelos_sinais():
    tudo = _av(qualidade=av.FORTE)
    assert rec.conviccao(["a", "b"], [], tudo).grau == rec.ALTA
    assert rec.conviccao(["a"], [], tudo).grau == rec.MODERADA
    assert rec.conviccao(["a", "b"], ["c"], tudo).grau == rec.MODERADA
    assert rec.conviccao(["a"], ["c"], tudo).grau == rec.BAIXA
    assert rec.conviccao(["a", "b", "c"], ["x", "y"], tudo).grau == rec.BAIXA
    pouco = _av(qualidade=av.INSUFICIENTE, preco=av.SEM_LEITURA)
    c = rec.conviccao(["a", "b"], [], pouco)
    assert c.grau == rec.BAIXA and "Só 1 de 3 dimensões" in c.porque
    falta = rec.conviccao(["a", "b"], [], _av(mercado=av.SEM_LEITURA))
    assert falta.grau == rec.MODERADA and "falta a leitura de mercado" in falta.porque
    c = rec.conviccao(["a", "b"], [], tudo, rec.MODERADA, "manchete")
    assert c.grau == rec.MODERADA and "limitada a moderada: manchete" in c.porque
    assert c.porque[0].isupper() and c.porque.endswith(".")


# -- o que chega à LLM e à tela -------------------------------------------------------

def test_veredito_leva_a_recomendacao_inteira_para_a_llm(an):
    d = rs.decisao(_solida(an, 2.5, _caro(95.0)), _sug(2.5))
    v = vd.veredito_de_decisao("WEGE3", d)
    assert v.limite == vd.REDUZIR and v.recomendacao is d
    assert v.decisao == "Reduzir parcialmente"
    txt = vd.texto_ticker(v, "WEGE3")
    assert "Reduzir parcialmente" in txt
    assert "Convicção:" in txt and "Proporção sugerida: 20% da posição." in txt
    assert "Condições que mudariam a recomendação:" in txt
    assert "Custo estimado da venda:" in txt
    assert "teto da sua recomendação, não uma ordem de venda" in vd.REGRA_VEREDITO
    assert "indicador isolado" in vd.REGRA_VEREDITO


def test_cartao_da_recomendacao_so_usa_tokens_e_escapa(an):
    d = rs.decisao(_solida(an, 2.5, _caro(95.0)), _sug(2.5))
    html = tela.cartao_recomendacao(d)
    for secao in ("Justificativa", "Proporção e custo estimado da venda",
                  "Condições que mudariam a recomendação",
                  "Vender cerca de 20% da posição."):
        assert secao in html
    assert _sem_cor_literal(html)
    sujo = replace(d, justificativas=("<script>x</script>",))
    assert "&lt;script&gt;" in tela.cartao_recomendacao(sujo)
    assert "<script>" not in tela.cartao_recomendacao(sujo)
