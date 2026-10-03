"""Página resumida da Inteligência dos Ativos (rascunho do usuário, 30/09/2026).

Reserva de emergência e renda fixa em lista; ações com uma caixa por ativo:
% atual, % devida, manter / comprar / vender, substituto, dois pares numa
tabela, notícias, relatórios e macro. Tudo reagrupa o que a análise já tem.
"""
import re
from dataclasses import replace

import pytest

from core.inteligencia_ativos import fundamentos as f
from core.inteligencia_ativos import informacoes as inf
from core.inteligencia_ativos import modelos as m
from core.inteligencia_ativos import pares as p
from core.inteligencia_ativos import resumida as rs
from tests.test_inteligencia_ativos_adequacao import _analise, _ctx, _pos
from views import inteligencia_ativos_resumida as tela

CARTEIRA = {"total_mercado": 11000.0, "posicoes": [
    _pos("TESOURO SELIC 2029", "Tesouro Direto", 10.0,
         nome="Tesouro Selic 2029"),
    _pos("TESOURO IPCA+ 2035", "Tesouro Direto", 30.0,
         nome="Tesouro IPCA+ 2035"),
    _pos("BBAS3", "Ações BR", 15.0, "Bancos"),
    _pos("TAEE11", "Ações BR", 5.0, "Utilidades"),
    _pos("HGLG11", "FII", 30.0, "Logística"),
    _pos("AAPL", "Ações BR", 10.0, "Tecnologia", moeda="USD"),
]}


@pytest.fixture
def carteira():
    ctx = _ctx(CARTEIRA)
    return ctx, [_analise(pp["ticker"], ctx) for pp in CARTEIRA["posicoes"]]


def _por(analises, ticker):
    return next(a for a in analises if a.ativo.ticker == ticker)


def _com_acao(a, estado, *just):
    return replace(a, acao=m.Acao(estado, tuple(just), False))


# -- grupos ---------------------------------------------------------------------------

def test_selic_e_reserva_o_resto_da_renda_fixa_e_renda_fixa(carteira):
    ctx, an = carteira
    assert rs.grupo(_por(an, "TESOURO SELIC 2029")) == rs.RESERVA
    assert rs.grupo(_por(an, "TESOURO IPCA+ 2035")) == rs.RENDA_FIXA
    assert rs.grupo(_por(an, "BBAS3")) == rs.ACOES
    assert rs.grupo(_por(an, "HGLG11")) == rs.FIIS
    assert rs.grupo(_por(an, "AAPL")) == rs.EXTERIOR


def test_blocos_na_ordem_do_rascunho_com_valor_peso_e_alvo(carteira):
    ctx, an = carteira
    bs = rs.blocos(an, ctx)
    assert [b.chave for b in bs] == [rs.RESERVA, rs.RENDA_FIXA, rs.ACOES,
                                     rs.FIIS, rs.EXTERIOR]
    acoes = bs[2]
    assert [a.ativo.ticker for a in acoes.analises] == ["BBAS3", "TAEE11"]
    assert acoes.peso == 20.0 and acoes.valor == pytest.approx(2200.0)
    assert acoes.alvo_classe == 20
    # a reserva conta no alvo da renda fixa: não se inventa alvo próprio
    assert bs[0].alvo_classe == bs[1].alvo_classe == 40
    assert bs[0].peso_classe == 40.0


def test_meta_da_reserva_vem_da_estrategia_ou_nao_aparece():
    assert rs.meta_reserva(None) is None
    assert rs.meta_reserva({}) is None
    txt = rs.meta_reserva({"emergency_reserve_months": {"value": 6}})
    assert "6 meses" in txt
    assert "ainda não há reserva" in rs.meta_reserva(
        {"has_emergency_reserve": {"value": False}})


# -- manter / comprar / vender --------------------------------------------------------

@pytest.mark.parametrize("estado,codigo", [
    (m.APORTE_COMPATIVEL, rs.COMPRAR),
    (m.REAVALIAR_TESE, rs.VENDER),
    (m.REDUZIR_CONCENTRACAO, rs.VENDER),
    (m.COMPARAR_ALTERNATIVAS, rs.VENDER),
    (m.REAVALIAR_APORTES, rs.MANTER),
    (m.EXPOSICAO_ADEQUADA, rs.MANTER),
    (m.MANTER, rs.MANTER),
])
def test_cada_acao_vira_uma_das_tres_palavras(carteira, estado, codigo):
    _, an = carteira
    d = rs.decisao(_com_acao(an[2], estado, "porque sim"))
    assert d.codigo == codigo and d.motivo == "porque sim"
    assert set(rs.DECISAO) == set(m.ROTULO_ACAO)


def _sug(peso):
    return rs.AlvoSugerido(peso, 20.0, False, 25.0, rs.METODO_VOL)


def _pesando(a, peso, estado=m.EXPOSICAO_ADEQUADA):
    a = _com_acao(a, estado, "leitura da classe")
    return replace(a, ativo=replace(a.ativo, peso_atual=peso))


def test_decisao_por_ativo_compara_o_peso_com_a_porcentagem_devida(carteira):
    # 01/10/2026: a classe dentro do alvo dava "Manter" a todos; BBAS3 com
    # 8,5% numa classe que reparte 25% entre dez ativos precisa sair "Vender".
    _, an = carteira
    a = an[2]
    d = rs.decisao(_pesando(a, 8.5), _sug(2.5))
    assert d.codigo == rs.VENDER and "6,0 pp acima da % devida 2,5%" in d.detalhe
    assert "parar de aportar" in d.motivo
    d = rs.decisao(_pesando(a, 0.6), _sug(2.5))
    assert d.codigo == rs.COMPRAR and "faltam 1,9 pp" in d.detalhe
    # dentro da folga (20% do alvo, mínimo 0,5 pp) fica como está
    d = rs.decisao(_pesando(a, 2.9), _sug(2.5))
    assert d.codigo == rs.MANTER and d.motivo == "leitura da classe"
    assert rs.tolerancia_pp(1.0) == 0.5 and rs.tolerancia_pp(10.0) == 2.0


def test_decisao_por_ativo_respeita_classe_acima_do_alvo_e_venda_da_analise(
        carteira):
    _, an = carteira
    a = an[2]
    # abaixo da % devida, mas a classe já passou do alvo: não compra
    d = rs.decisao(_pesando(a, 0.6, m.REAVALIAR_APORTES), _sug(2.5))
    assert d.codigo == rs.MANTER and "classe já passou do alvo" in d.detalhe
    # vender que vem da análise (tese, teto) não é desfeito pela % devida
    d = rs.decisao(_pesando(a, 0.6, m.REAVALIAR_TESE), _sug(2.5))
    assert d.codigo == rs.VENDER and d.motivo == "leitura da classe"
    # sem % devida, vale a leitura da classe
    assert rs.decisao(_pesando(a, 8.5)).codigo == rs.MANTER
    # a faixa do usuário vence a sugestão
    b = _pesando(a, 8.5)
    b = replace(b, faixa=replace(b.faixa, alvo_ativo=8.0))
    assert rs.decisao(b, _sug(2.5)).codigo == rs.MANTER
    assert tela.rotulo_expander(b, _sug(2.5)).endswith("8,5% → 8,0% · Manter")
    assert (tela.rotulo_expander(_pesando(a, 8.5), _sug(2.5))
            == f"{a.ativo.ticker} · 8,5% → 2,5% · Vender")


def test_peso_devido_sem_alvo_da_classe_fica_no_limite_ou_sem_alvo(carteira):
    _, an = carteira
    assert rs.peso_devido(an[2]).valor == "sem alvo"
    ctx = _ctx(CARTEIRA, single_asset_limit_pct=10)
    a = _analise("BBAS3", ctx)
    devido = rs.peso_devido(a)
    assert devido.valor == "até 10%" and "não tem alvo" in devido.detalhe
    # o alvo que o usuário definiu vence a sugestão
    fx = replace(a.faixa, alvo_ativo=8.0, piso_ativo=5.0)
    sug = rs.AlvoSugerido(12.0, 20.0, False, 20.0, rs.METODO_VOL)
    assert rs.peso_devido(replace(a, faixa=fx), sug).valor == "8,0%"


def _acoes(carteira, teto=None):
    ctx, an = carteira
    b = next(b for b in rs.blocos(an, ctx) if b.chave == rs.ACOES)
    analises = tuple(replace(a, faixa=replace(a.faixa, teto_ativo=teto))
                     for a in b.analises)
    return replace(b, analises=analises)


def test_alvo_sugerido_divide_a_classe_pelo_inverso_da_volatilidade(
        carteira, monkeypatch):
    """30/09/2026: o usuário pediu que a % devida seja sugerida. Alvo da
    classe (20%) dividido para que cada ativo contribua com risco parecido."""
    vols = {"BBAS3": 30.0, "TAEE11": 15.0}
    monkeypatch.setattr(rs, "volatilidade", lambda a: vols[a.ativo.ticker])
    alvos = _acoes(carteira).alvos
    assert alvos["TAEE11"].peso == pytest.approx(20 * 2 / 3)
    assert alvos["BBAS3"].peso == pytest.approx(20 / 3)
    assert sum(x.peso for x in alvos.values()) == pytest.approx(20.0)
    assert {x.metodo for x in alvos.values()} == {rs.METODO_VOL}
    a = next(x for x in _acoes(carteira).analises if x.ativo.ticker == "TAEE11")
    devido = rs.peso_devido(a, alvos["TAEE11"])
    assert devido.valor == "13,3%"
    assert "sugestão" in devido.detalhe and "15,0% a.a." in devido.detalhe
    assert "Faltam" in devido.detalhe


def test_alvo_sugerido_respeita_o_limite_e_repassa_a_sobra(carteira, monkeypatch):
    vols = {"BBAS3": 30.0, "TAEE11": 10.0}
    monkeypatch.setattr(rs, "volatilidade", lambda a: vols[a.ativo.ticker])
    alvos = _acoes(carteira, teto=12.0).alvos
    assert alvos["TAEE11"].peso == pytest.approx(12.0) and alvos["TAEE11"].limitado
    assert alvos["BBAS3"].peso == pytest.approx(8.0) and not alvos["BBAS3"].limitado


def test_alvo_sugerido_sem_volatilidade_usa_mediana_ou_pesos_iguais(
        carteira, monkeypatch):
    monkeypatch.setattr(rs, "volatilidade", lambda a: None)
    alvos = _acoes(carteira).alvos
    assert [x.peso for x in alvos.values()] == pytest.approx([10.0, 10.0])
    assert {x.metodo for x in alvos.values()} == {rs.METODO_IGUAL}
    vols = {"BBAS3": 20.0, "TAEE11": None}
    monkeypatch.setattr(rs, "volatilidade", lambda a: vols[a.ativo.ticker])
    alvos = _acoes(carteira).alvos
    assert alvos["TAEE11"].peso == pytest.approx(10.0)   # mediana = 20


def test_alvo_sugerido_nao_se_aplica_a_reserva_nem_sem_alvo_da_classe(carteira):
    ctx, an = carteira
    bs = {b.chave: b for b in rs.blocos(an, ctx)}
    assert bs[rs.RESERVA].alvos == {} and bs[rs.RENDA_FIXA].alvos == {}
    assert replace(bs[rs.ACOES], alvo_classe=None).alvos == {}


def test_distribuir_com_todos_no_teto_nao_passa_do_teto():
    pesos, presos = rs._distribuir({"a": 1.0, "b": 1.0}, 30.0,
                                   {"a": 10.0, "b": 10.0})
    assert pesos == {"a": 10.0, "b": 10.0} and presos == {"a", "b"}


# -- pares e substitutos ---------------------------------------------------------------

def _cand(tk, **metricas):
    return p.Candidato(tk, tk, f.ACAO, "B3", (("segmento", "Bancos"),),
                       100.0, 30.0, "volatilidade", metricas, tk[:4], 2025)


def _com_pares(a):
    grupo = p.GrupoPares("segmento", "Bancos", (
        p.Par("ITUB4", "Itaú", 0.5, "porte parecido", {"p_l": 9.0}),
        p.Par("TAEE11", "Taesa", 0.1, "já na carteira", {"p_l": 7.0}),
        p.Par("BBDC4", "Bradesco", 0.3, "risco parecido", {"p_l": 8.0}),
        p.Par("SANB11", "Santander", 0.9, "longe", {"p_l": 11.0}),
    ))
    c = p.comparar(_cand("BBAS3", p_l=5.0, roe=20.0), grupo)
    return replace(a, pares=replace(a.pares, dados=c.como_dict()))


def test_substitutos_ficam_fora_da_carteira_e_por_proximidade(carteira):
    _, an = carteira
    a = _com_pares(_por(an, "BBAS3"))
    subs = rs.substitutos(a, [x.ativo.ticker for x in an])
    assert [s.ticker for s in subs] == ["BBDC4", "ITUB4"]


def test_tabela_traz_o_ativo_e_os_dois_pares_mais_proximos(carteira):
    _, an = carteira
    t = rs.tabela_pares(_com_pares(_por(an, "BBAS3")))
    assert [tk for tk, _ in t.linhas] == ["BBAS3", "TAEE11", "BBDC4"]
    assert "P/L" in t.colunas and "ROE" in t.colunas
    assert "EV/EBIT" not in t.colunas          # ninguém tem o dado
    assert t.linhas[0][1][t.colunas.index("P/L")] == "5,00x"
    assert t.linhas[1][1][t.colunas.index("ROE")] == "—"
    assert t.titulo == "mesmo segmento (Bancos)"


def test_sem_pares_a_tabela_diz_por_que(carteira):
    _, an = carteira
    t = rs.tabela_pares(an[2])
    assert not t.linhas and "Sem universo de comparação" in t.motivo
    assert rs.substitutos(an[2], ()) == ()


# -- notícias, relatórios e macro --------------------------------------------------------

def _noticia(data, titulo):
    return inf.Noticia(headline=titulo, date=data, source="S", url=None,
                       impact_level=inf.LOW, affected_dimension=(),
                       summary=None, categoria="x", motivo="m")


def test_noticias_mais_recentes_primeiro_e_no_maximo_tres(carteira):
    _, an = carteira
    ns = inf.Noticias(itens=tuple(_noticia(f"2026-09-{d:02d}", f"n{d}")
                                  for d in (1, 20, 5, 12)))
    a = replace(an[2], noticias=replace(an[2].noticias, dados=ns.como_dict()))
    itens, motivo = rs.noticias(a)
    assert [i.headline for i in itens] == ["n20", "n12", "n5"] and motivo is None
    assert rs.noticias(an[2])[1]          # sem dado: diz por quê


def test_macro_sem_cenario_lista_os_canais_da_classe(carteira):
    ctx, an = carteira
    mc = rs.macro(_por(an, "BBAS3"), ctx)
    assert mc.sem_cenario and "Câmbio" in mc.canais
    assert "Taxa de juros" in rs.macro(_por(an, "HGLG11"), ctx).canais


# -- a tela ------------------------------------------------------------------------------

def _sem_cor_literal(html: str) -> bool:
    return not re.search(r"#(?!\d+;)", html.replace("&#", ""))


def test_cartoes_so_usam_tokens_e_nao_viram_link_perigoso(carteira):
    ctx, an = carteira
    a = _com_acao(_com_pares(_por(an, "BBAS3")), m.REDUZIR_CONCENTRACAO, "x")
    ns = inf.Noticias(itens=(replace(_noticia("2026-09-20", "<b>t</b>"),
                                     url="javascript:alert(1)"),))
    a = replace(a, noticias=replace(a.noticias, dados=ns.como_dict()))
    htmls = [
        tela.cabecalho_grupo(rs.blocos(an, ctx)[0], "6 meses"),
        tela.cartao_lista(rs.blocos(an, ctx)[1]),
        tela.cartao_posicao(a),
        tela.cartao_substitutos(rs.substitutos(a, ())),
        tela.cartao_pares(rs.tabela_pares(a)),
        tela.cartao_papel(a), tela.cartao_noticias(a),
        tela.cartao_relatorios(a), tela.cartao_macro(rs.macro(a, ctx), "sobe"),
    ]
    for h in htmls:
        assert _sem_cor_literal(h), h
    assert "javascript:" not in htmls[6] and "&lt;b&gt;t" in htmls[6]
    assert "Vender" in htmls[2] and "Porcentagem devida" in htmls[2]
    assert "Leitura do Portfolio Fit" in htmls[-1]
    assert tela.rotulo_expander(a) == "BBAS3 · 15,0% · Vender"


def test_relatorios_mostram_o_que_o_documento_diz_sem_link(carteira):
    from core.inteligencia_ativos import destaques_relatorios as dr
    _, an = carteira
    a = _por(an, "BBAS3")
    d = dr.Destaque("Release 2T26", "2026-08-10", "Release",
                    ("O lucro foi de R$ 3 bilhões, <alta> de 8%.",))
    html = tela.cartao_relatorios(a, (d,))
    assert "Release 2T26" in html and "&lt;alta&gt;" in html
    assert "<a href" not in html and "Trechos literais" in html
    assert _sem_cor_literal(html)


# -- cenário econômico e político (complemento das notícias) ------------------------

def _geral(titulo, tipo, nota=50.0, paises=("BR",), tickers=(), url=None):
    return {"titulo": titulo, "tipo_evento": tipo, "nota": nota,
            "veiculo": "V", "url": url, "publicado_em": "2026-10-03T11:00:00",
            "entidades": {"paises": list(paises), "tickers": list(tickers)}}


GERAIS = [
    _geral("Sable Offshore cai 51%", "indefinido", 90.0, ("US",)),
    _geral("Tecnologia para clima quer financiamento", "operacional", 80.0),
    _geral("Petrobras sobe com dólar", "cambio", 85.0, tickers=("PETR4",)),
    _geral("Fed mantém juros", "juros_politica_monetaria", 70.0, ("US",)),
    _geral("Copom sinaliza corte da Selic", "juros_politica_monetaria", 60.0),
    _geral("Câmbio: real se valoriza", "cambio", 55.0),
    _geral("Congresso aprova orçamento", "fiscal_politico", 40.0),
    _geral("IPCA de setembro desacelera", "inflacao", 65.0),
]


def test_cenario_so_traz_fato_do_pais_nunca_outra_empresa(carteira):
    _, an = carteira
    itens = rs.noticias_cenario(_por(an, "BBAS3"), GERAIS, n=10)
    titulos = [i.titulo for i in itens]
    assert "Sable Offshore cai 51%" not in titulos
    assert "Tecnologia para clima quer financiamento" not in titulos
    assert "Petrobras sobe com dólar" not in titulos      # cita ticker
    assert "Copom sinaliza corte da Selic" in titulos


def test_cenario_prioriza_tema_da_classe_e_pais_do_ativo(carteira):
    _, an = carteira
    acao = [i.titulo for i in rs.noticias_cenario(_por(an, "BBAS3"), GERAIS)]
    # Ações BR: juros, atividade, câmbio, fiscal pesam; Brasil antes dos EUA;
    # inflação não está entre os canais da classe e fica depois.
    assert acao[0] == "Copom sinaliza corte da Selic"
    assert "Fed mantém juros" not in acao
    assert "IPCA de setembro desacelera" not in acao
    fii = [i.titulo for i in rs.noticias_cenario(_por(an, "HGLG11"), GERAIS)]
    assert "IPCA de setembro desacelera" in fii           # inflação pesa em FII
    exterior = rs.noticias_cenario(_por(an, "AAPL"), GERAIS)
    assert exterior[0].titulo == "Fed mantém juros" and exterior[0].pais == "US"


def test_cartao_mostra_cenario_e_diz_os_temas(carteira):
    _, an = carteira
    a = _por(an, "BBAS3")
    html = tela.cartao_noticias(a, (GERAIS, "Acervo local"))
    assert "Cenário econômico e político" in html
    assert "Copom sinaliza corte da Selic" in html
    assert "Sable Offshore" not in html and "Petrobras" not in html
    assert "não de outras empresas" in html and "Fonte: Acervo local" in html
    vazio = tela.cartao_noticias(a, ([GERAIS[0]], "Acervo local"))
    assert "Nenhuma manchete de juros" in vazio and "Sable" not in vazio
