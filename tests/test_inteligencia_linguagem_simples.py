"""A recomendação em linguagem simples (pedido do usuário de 12/11/2026).

"A linguagem é muito pesada, técnica e com jargões" e, depois, "seja mais
objetivo, evite repetir informações já ditas". A conversa reconta a decisão
sem sigla solta, sem repetir os cartões de cima nem um número já dito, e o
texto técnico continua o mesmo para as LLMs.
"""
from dataclasses import replace

import pytest

from core.inteligencia_ativos import avaliacao as av
from core.inteligencia_ativos import fundamentos as f
from core.inteligencia_ativos import linguagem_simples as ls
from core.inteligencia_ativos import resumida as rs
from core.inteligencia_ativos import secoes
from tests.test_inteligencia_ativos_adequacao import _analise, _ctx, _pos
from tests.test_inteligencia_ativos_resumida import _sem_cor_literal, _sug
from tests.test_inteligencia_avaliacao import BOA, _com, _fund, _linha
from views import inteligencia_ativos_resumida as tela

CARTEIRA = {"total_mercado": 11000.0, "posicoes": [
    _pos("TAEE11", "Ações BR", 1.4, "Utilidade Pública"),
    _pos("WEGE3", "Ações BR", 10.0, "Bens Industriais"),
]}

JARGAO = ("eliminatório", "percentil", "inverso da volatilidade", "p.p.",
          "mediana", "régua", "brapi", "gatilho", "% devida")


@pytest.fixture(scope="module")
def an():
    with pytest.MonkeyPatch.context() as mp:
        for chave in secoes.SECOES:
            mp.setitem(secoes.PROVEDORES, chave, secoes._pendente(chave))
        ctx = _ctx(CARTEIRA)
        return {p["ticker"]: _analise(p["ticker"], ctx)
                for p in CARTEIRA["posicoes"]}


def _taee(an):
    """O caso do print: elétrica com dívida de 7,07x o EBITDA, P/L abaixo
    dos pares e dividend yield no fundo do próprio histórico."""
    a = _com(an["TAEE11"],
             _fund(roe=20.0, roic=10.0, lucro_liquido=100.0,
                   divida_liquida_ebitda=7.07, cobertura_juros=2.0),
             [_linha("p_l", "P/L", 8.58, [9, 10, 11, 12], [9.5, 10.2, 11]),
              _linha("dividend_yield", "Dividend yield", 2.5,
                     [8, 9.6, 10, 11], [4, 4.9, 6], unidade=f.PCT,
                     percentil_historico=0.0)])
    return replace(a, ativo=replace(a.ativo, peso_atual=1.4,
                                    valor_mercado=5460.56,
                                    valor_investido=4000.0))


def _texto(cv: ls.Conversa) -> str:
    return " ".join([*cv.porque, *(x for lt in cv.leituras for x in lt.frases),
                     *cv.pratica, *cv.pesos, *cv.mudaria, *cv.investigar,
                     cv.acao_curta, cv.devida_curta, cv.certeza_curta])


def test_divida_alta_vira_frase_simples_com_o_numero(an):
    a = _taee(an)
    d = rs.decisao(a, _sug(4.1))
    cv = ls.conversar(a, d, _sug(4.1))
    assert d.codigo == rs.VENDER
    assert cv.acao_curta == "dívida alta demais"
    assert "7,1 vezes o que a operação gera por ano" in cv.porque[0]
    assert "5,5 vezes" in cv.porque[0]
    txt = _texto(cv).lower()
    for termo in JARGAO:
        assert termo not in txt, termo
    # a contradição "vender" × "faltam 2,7 pontos" é explicada
    assert cv.devida_curta == "faltam 2,7 pontos"
    assert any("caberia comprar mais" in x for x in cv.porque)


def test_nao_repete_numero_ja_dito(an):
    a = _taee(an)
    cv = ls.conversar(a, rs.decisao(a, _sug(4.1)), _sug(4.1))
    saude = next(lt for lt in cv.leituras if lt.rotulo == "Saúde da empresa")
    # a dívida já está no porquê: não volta no "o que eu vi"
    assert not any("7,1" in x for x in saude.frases)
    # o dividendo no fundo do histórico já está no preço: não volta em alerta
    assert not any("9,8%" in x for x in cv.investigar)
    assert ls._ja_dito("P/L de 30,00x", "P/L 30,0x")


def test_venda_traz_valor_e_imposto_sem_custo_irrisorio(an):
    a = _taee(an)
    cv = ls.conversar(a, rs.decisao(a, _sug(4.1)), _sug(4.1))
    assert cv.pratica[0] == "Vender toda a posição (≈ R$ 5.461)."
    assert "Lucro de R$ 1.461" in cv.pratica[1] and "R$ 20 mil" in cv.pratica[1]
    assert not any("custo" in x for x in cv.pratica)


def test_preco_dito_como_barato_ou_caro(an):
    a = _taee(an)
    cv = ls.conversar(a, rs.decisao(a, _sug(4.1)), _sug(4.1))
    preco = next(lt for lt in cv.leituras if lt.rotulo == "Preço")
    assert preco.leitura == "sinais mistos"
    assert preco.frases[0].startswith("Pelo lucro (P/L 8,58x): barata")
    assert "às concorrentes (10,2x)" in preco.frases[0]
    assert "Pelos dividendos (2,5% ao ano): cara" in preco.frases[1]


def test_mercado_sem_noticia_nao_vira_noticiario_favoravel(an):
    a = _taee(an)
    avl = av.avaliar(a)
    assert "noticiário" not in ls.sinal_simples("mercado e notícias positivos",
                                                avl)


def test_empresa_solida_e_cara_reduz_parte_e_diz_por_que(an):
    a = _com(an["WEGE3"], _fund(**BOA),
             [_linha("p_l", "P/L", 30.0, [10, 15, 20, 25, 35], [14, 14, 14],
                     percentil_historico=85.0)])
    a = replace(a, ativo=replace(a.ativo, peso_atual=6.0))
    d = rs.decisao(a, _sug(2.5))
    cv = ls.conversar(a, d, _sug(2.5))
    assert d.codigo == rs.REDUZIR_PARCIAL
    assert cv.pratica[0].startswith("Vender um terço da posição")
    assert "saudável" in cv.porque[0]
    # o P/L de 30x já apareceu no preço: o alerta do topo do histórico some
    assert not cv.investigar


def test_cartao_nao_repete_kpi_nem_aviso_generico_e_guarda_o_tecnico(an):
    a = _taee(an)
    avl = av.avaliar(a)
    d = rs.decisao(a, _sug(4.1), avl)
    cv = ls.conversar(a, d, _sug(4.1))
    html = (tela.cartao_posicao(a, _sug(4.1), avl, d, cv)
            + tela.cartao_conversa(cv) + tela.bloco_tecnico(d, avl))
    assert _sem_cor_literal(html)
    visivel, tecnico = html.split("<details", 1)
    assert "Recomendação por regras" not in html
    assert "Por quê" in visivel and "Na prática" in visivel
    assert "Dívida líquida/EBITDA de 7,07x" not in visivel
    assert "Dívida líquida/EBITDA de 7,07x" in tecnico
    assert "Justificativa" in tecnico
    sujo = replace(cv, porque=("<script>x</script>",))
    assert "<script>" not in tela.cartao_conversa(sujo)


def test_texto_para_a_llm_continua_o_tecnico(an):
    from core.inteligencia_ativos import veredito as vd
    a = _taee(an)
    d = rs.decisao(a, _sug(4.1))
    txt = vd.texto_ticker(vd.veredito_de_decisao("TAEE11", d), "TAEE11")
    assert "alavancagem de 7,07x o EBITDA" in txt


def test_estilo_da_tela_so_usa_tokens_e_cabe_num_markdown():
    """O CSS da caixa (12/11/2026) não crava cor e não tem quebra de linha:
    linha em branco fecha o bloco HTML do Streamlit."""
    assert _sem_cor_literal(tela.CSS)
    assert "\n" not in tela.CSS
    assert tela.CSS.startswith("<style>") and tela.CSS.endswith("</style>")
