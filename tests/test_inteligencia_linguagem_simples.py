"""A recomendação em linguagem simples (pedido do usuário de 12/11/2026).

"A linguagem é muito pesada, técnica e com jargões" e, depois, "seja mais
objetivo, evite repetir informações já ditas". A conversa reconta a decisão
sem sigla solta, sem repetir os cartões de cima nem um número já dito, e o
texto técnico continua o mesmo para as LLMs.
"""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from core.inteligencia_ativos import avaliacao as av
from core.inteligencia_ativos import fundamentos as f
from core.inteligencia_ativos import linguagem_simples as ls
from core.inteligencia_ativos import recomendacao as rec
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


def test_venda_forcada_por_alerta_do_balanco_nao_tem_certeza_baixa(an):
    """Venda por alerta eliminatório confirmado no balanço: a regra diz que
    nenhum ponto bom compensa, então o preço normal não divide a decisão
    nem a deixa com certeza baixa (12/11/2026)."""
    a = _taee(an)
    d = rs.decisao(a, _sug(4.1))
    cv = ls.conversar(a, d, _sug(4.1))
    assert d.codigo == rs.VENDER
    assert d.conviccao == rec.MODERADA and ls.CERTEZA[d.conviccao] == "Média"
    assert d.conviccao_sustentada == rs.MOTIVO_PISO_ALERTA
    assert "elevada a moderada" in d.porque_conviccao
    assert cv.certeza_curta == "o alerta grave pesa mais que o resto"
    # os sinais contra continuam visíveis na balança
    assert d.sinais_contra


def test_piso_sobe_o_grau_e_o_teto_prevalece():
    lidas = SimpleNamespace(qualidade=av.FORTE, preco=av.JUSTO,
                            mercado=av.POSITIVO)
    c = rec.conviccao(["a"], ["b", "c"], lidas)
    assert c.grau == rec.BAIXA and not c.sustentada
    c = rec.conviccao(["a"], ["b", "c"], lidas, piso=rec.MODERADA,
                      motivo_piso="m")
    assert c.grau == rec.MODERADA and c.sustentada == "m"
    # o teto vem depois e prevalece; piso que não subiu nada não aparece
    c = rec.conviccao(["a", "b"], [], lidas, teto=rec.MODERADA,
                      motivo_teto="t", piso=rec.MODERADA, motivo_piso="m")
    assert c.grau == rec.MODERADA and c.limitada == "t" and not c.sustentada


def test_alerta_so_do_noticiario_nao_ganha_piso(an):
    a = _taee(an)
    avl = av.avaliar(a)
    avl = replace(avl, alertas=tuple(replace(x, noticia=True) if x.critico
                                     else x for x in avl.alertas))
    d = rs.decisao(a, _sug(4.1), avl)
    assert d.codigo == rs.ACOMPANHAR and not d.conviccao_sustentada


# -- comparação, troca e economia em palavras (13/11/2026) -----------------------
#
# O caso do print: PSSA3 com "Substituir por BBSE3" e a frase "sem ser mais
# cara". BBSE3 tem P/VP de 6,82x contra 1,78x: mais cara nessa métrica. A
# regra soma as métricas de preço (o P/L menor compensa), e o texto tem de
# dizer isso em vez de negar.

from core.cenario import modelo as cen  # noqa: E402
from core.inteligencia_ativos import pares as p  # noqa: E402

PSSA3 = {"roe": 22.8, "margem_liquida": 15.0, "p_l": 9.0, "p_vp": 1.78}
BBSE3 = {"roe": 84.4, "margem_liquida": 60.0, "p_l": 7.0, "p_vp": 6.82,
         "porte": 300.0}


def _seguradora(an):
    a = an["WEGE3"]
    grupo = p.GrupoPares("segmento", "Seguros", (
        p.Par("BBSE3", "BB Seguridade", 0.2, "porte maior", BBSE3),))
    cand = p.Candidato("WEGE3", "WEGE3", f.ACAO, "B3",
                       (("segmento", "Seguros"),), 100.0, 30.0,
                       "volatilidade", {**PSSA3, "porte": 100.0}, "WEGE", 2025)
    c = p.comparar(cand, grupo)
    return replace(a, pares=replace(a.pares, dados=c.como_dict()))


def test_diferencas_e_confronto_contam_a_mesma_coisa(an):
    a = _seguradora(an)
    par = rs.substitutos(a, ())[0]
    difs = rec.diferencas(a, par)
    cf = rec.confrontar(a, par)
    assert cf.diferencas == difs and cf.n == len(difs) == 4
    assert cf.melhor == tuple(d.texto for d in difs if d.par_melhor is True)
    assert cf.pior == tuple(d.texto for d in difs if d.par_melhor is False)
    assert cf.pior == ("P/VP (6,82x contra 1,78x)",)
    # passa pelo SALDO: o P/L menor compensa o P/VP maior
    assert cf.saldo_valuation == 0 and cf.claramente_superior


def test_troca_diz_o_que_o_substituto_tem_de_pior(an):
    a = _seguradora(an)
    t = ls.trocar_por(a, rs.substitutos(a, ())[0])
    assert t.ticker == "BBSE3"
    assert t.apresentacao == "Do mesmo setor (Seguros), bem maior que WEGE3."
    assert any("mais lucro sobre o dinheiro dos sócios (84,4% contra 22,8%"
               in x for x in t.vantagens)
    assert t.desvantagens == ("preço mais alto em relação ao patrimônio "
                              "(6,82x contra 1,78x de WEGE3)",)
    assert "passa no critério de troca" in t.conclusao
    assert "mesmo perdendo" in t.conclusao
    html = tela.cartao_substitutos(a, rs.substitutos(a, ()))
    assert "✗ preço mais alto em relação ao patrimônio" in html
    assert _sem_cor_literal(html)
    for termo in JARGAO + ("EV/EBIT", "drawdown", "o do ativo"):
        assert termo not in html.lower().replace("(p/vp)", ""), termo


def test_porque_da_troca_nao_nega_o_preco_pior(an):
    a = _seguradora(an)
    cf = rec.confrontar(a, rs.substitutos(a, ())[0])
    frases = ls._porque_troca(cf)
    txt = " ".join(frases)
    assert "sem ser mais cara" not in txt
    assert txt.startswith("BBSE3, do mesmo setor, sai melhor na comparação: "
                          "tem mais lucro sobre o dinheiro dos sócios")
    assert frases[-1] == ("Em troca, tem preço mais alto em relação ao "
                          "patrimônio.")
    sem_ticker = txt.replace("BBSE3", "")
    assert not any(ch.isdigit() for ch in sem_ticker)   # números ficam no cartão


def test_troca_que_nao_passa_diz_por_que(an):
    a = _seguradora(an)
    caro = p.Par("X", "X", 0.2, "", {"roe": 30.0, "margem_liquida": 20.0,
                                     "p_l": 15.0, "p_vp": 3.0})
    t = ls.trocar_por(a, caro)
    assert "sai mais cara" in t.conclusao and "não compensa" in t.conclusao
    pouco = ls.trocar_por(a, p.Par("Y", "Y", 0.2, "", {"roe": 30.0}))
    assert "poucos números" in pouco.conclusao


@pytest.mark.parametrize("tecnico, simples", [
    ("Selic 15,00% a.a. (10,50% em 2024)",
     "Selic em 15,00% ao ano (era 10,50% em 2024)."),
    ("prefixado de ~1 ano 14,20% a.a. contra Selic 15,00%: mercado precifica "
     "corte; ~5 anos 13,40%",
     "O Tesouro prefixado de 1 ano paga 14,20% ao ano, abaixo da Selic "
     "(15,00%): o mercado espera juros caindo. O de 5 anos paga 13,40%."),
    ("IPCA acumulado em 2026 até o último mês divulgado: 3,9%; anos fechados "
     "2024 4,8% → 2025 4,3%",
     "Os preços subiram 3,9% em 2026 até o último mês divulgado. Nos anos "
     "anteriores: 4,8% em 2024 e 4,3% em 2025."),
    ("inflação implícita de ~2 anos 4,6% a.a. contra IPCA 2025 4,3%",
     "O mercado espera inflação de 4,6% ao ano nos próximos 2 anos (em 2025 "
     "foi 4,3%)."),
    ("US$ 1 = R$ 5,4321 (+2,1% desde 01/10/2026)",
     "O dólar vale R$ 5,43 (+2,1% desde 01/10/2026)."),
    ("PIB 2025 -0,5%", "A economia encolheu 0,5% em 2025 (PIB)."),
    ("dívida do governo central 78,1% do PIB em 2026 (74,4% em 2024)",
     "A dívida do governo equivale a 78,1% de tudo o que o país produz em um "
     "ano (dado de 2026). Era 74,4% em 2024."),
    ("juro real de mercado (IPCA+ ~3 anos) 7,8% a.a.; Selic 15,00%",
     "O Tesouro IPCA+ de 3 anos paga 7,8% ao ano acima da inflação (a Selic "
     "está em 15,00%)."),
    ("spread high yield EUA 3,1 pp (4,5 pp em 01/2024); spread menor = mais "
     "apetite a risco",
     "Empresas arriscadas dos EUA pagam 3,1 pontos a mais que o governo de lá "
     "(eram 4,5 em 01/2024). Quanto menor essa diferença, mais apetite por "
     "risco."),
])
def test_valor_do_cenario_sem_sigla_de_mercado(tecnico, simples):
    assert ls.valor_simples(tecnico) == simples


def test_vocabulario_da_economia_cobre_todo_o_modelo():
    assert set(ls.ROTULO_CENARIO) == set(cen.CHAVES)
    assert set(ls.O_QUE_E) == set(cen.CHAVES)
    for classe, porque in ls.POR_QUE_PESA.items():
        assert set(porque) <= set(cen.relevantes(classe)), classe
        assert set(cen.relevantes(classe)) <= set(porque), classe


def test_comparacao_com_parecidas_sem_mediana_nem_segmento(an):
    a = _seguradora(an)
    html = tela.cartao_pares(rs.tabela_pares(a))
    assert "Como se compara com empresas parecidas · Seguros" in html
    assert "Típico do setor" in html and "Lucro sobre o dinheiro dos sócios" in html
    low = html.lower()
    assert "mediana" not in low and "segmento" not in low
    assert _sem_cor_literal(html)
