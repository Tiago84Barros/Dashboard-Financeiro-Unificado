"""
tests/test_inteligencia_ativos_informacoes.py
Camada de informações recentes: o filtro e o classificador de manchetes
(core/inteligencia_ativos/informacoes.py), os leitores do arquivo e dos
eventos (fontes_informacoes.py), o agrupamento do publicador
(scripts/publish_informacoes_recentes.py), os provedores e os cartões.

Os casos de manchete são reais, tirados do acervo ao calibrar o filtro.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

import pytest

from core.inteligencia_ativos import destaques_relatorios as dr
from core.inteligencia_ativos import fontes_informacoes as fi
from core.inteligencia_ativos import informacoes as inf
from core.inteligencia_ativos import modelos as m
from core.inteligencia_ativos import secoes

# -- classificador de manchete --------------------------------------------------------

@pytest.mark.parametrize("ticker,titulo,resolvidos,esperado", [
    # posição de terceiro não é fato da empresa
    ("NVDA", "NVIDIA Corporation $NVDA Shares Acquired by Acme Capital",
     ("NVDA",), inf.D_13F),
    ("NVDA", "Fund takes 5% voting stake in NVIDIA (NVDA)", ("NVDA",),
     inf.D_13F),
    # o nome coincide com expressão comum
    ("TGT", "Analyst raises price target on Nike", ("TGT",), inf.D_NAO_ASSUNTO),
    # banco citado como autor da recomendação sobre outra empresa
    ("MS", "Morgan Stanley upgrades Nvidia to Overweight", ("MS", "NVDA"),
     inf.D_NAO_ASSUNTO),
    # ticker literal americano é sensível à caixa: "Post" não é POST
    ("POST", "Stocks rally in Post Earnings session", (), inf.D_NAO_ASSUNTO),
    # tomada de conta não é aquisição
    ("META", "Meta warns of account takeovers on Instagram", ("META",),
     inf.D_SEM_CATEGORIA),
    ("LUXM3", "Ibovespa hoje: destaques das empresas LUXM3", (), inf.D_RUIDO),
])
def test_descartes(ticker, titulo, resolvidos, esperado):
    assert inf.avaliar_manchete(ticker, titulo, resolvidos) == esperado


def test_rebaixamento_por_agencia_e_divida_alta():
    cl = inf.avaliar_manchete("BBAS3", "Fitch rebaixa rating do Banco do Brasil",
                              ("BBAS3",))
    assert isinstance(cl, inf.Classificacao)
    assert cl.categoria == "divida" and cl.nivel == inf.HIGH
    assert "divida" in cl.dimensoes and "risco" in cl.dimensoes


def test_recomendacao_manda_sem_fato_alto():
    cl = inf.classificar_titulo("Nike gets Buy rating and higher revenue view")
    assert cl.categoria == "recomendacao" and cl.nivel == inf.LOW
    assert cl.categorias == ("recomendacao",)


def test_pergunta_rebaixa_um_grau():
    cl = inf.classificar_titulo("Could Apple acquire Disney?")
    assert cl.categoria == "fusao_aquisicao" and cl.nivel == inf.MEDIUM
    assert "especulativa" in cl.motivo
    firme = inf.classificar_titulo("Apple acquires Disney")
    assert firme.nivel == inf.HIGH


def test_aquisicao_de_bem_nao_e_m_a():
    assert inf.classificar_titulo("Azul conclui aquisição de aeronaves") is None
    cl = inf.classificar_titulo("Strategy acquires 1,000 bitcoin")
    assert cl is None or "fusao_aquisicao" not in cl.categorias


def test_limite_de_palavra():
    # "cade" não casa "cadeia", "cri" não casa "crise", "fined" não "finance"
    assert inf.classificar_titulo("Cadeia de suprimentos em crise") is None
    assert inf.classificar_titulo("Company finance chief speaks") is None
    assert inf.classificar_titulo("De acordo com analistas, dia calmo") is None


def test_manchete_dividida_desce_um_grau():
    cl = inf.avaliar_manchete("VALE3", "Vale e Petrobras anunciam fusão",
                              ("VALE3", "PETR4"))
    assert isinstance(cl, inf.Classificacao) and cl.nivel == inf.MEDIUM
    assert "outra empresa" in cl.motivo


def test_resumo_com_varias_empresas():
    assert inf.avaliar_manchete(
        "VALE3", "Vale, Petrobras e Itaú: lucro do trimestre",
        ("VALE3", "PETR4", "ITUB4")) == inf.D_RESUMO


def test_duplicata_ignora_ticker_e_pontuacao():
    a = inf.chave_de_duplicata("MPF aciona Vale (VALE3) por barragem!")
    b = inf.chave_de_duplicata("MPF aciona Vale por barragem")
    assert a == b


# -- prazo de resultado e eventos -----------------------------------------------------

@pytest.mark.parametrize("hoje,esperado", [
    (date(2026, 9, 26), (date(2026, 11, 14), "ITR 3T26")),
    (date(2026, 8, 14), (date(2026, 8, 14), "ITR 2T26")),
    (date(2026, 8, 15), (date(2026, 11, 14), "ITR 3T26")),
    (date(2027, 1, 10), (date(2027, 3, 31), "DFP 2026")),
    (date(2027, 4, 2), (date(2027, 5, 15), "ITR 1T27")),
])
def test_prazo_de_resultado(hoje, esperado):
    assert inf.prazo_de_resultado(hoje) == esperado


def test_eventos_de_proventos_escolhe_a_proxima_data():
    hoje = date(2026, 9, 26)
    linhas = [
        {"ex_date": date(2026, 10, 1), "payment_date": date(2026, 10, 15),
         "amount": 1.1, "type": "rendimento", "source": "brapi"},
        {"ex_date": date(2026, 9, 1), "payment_date": date(2026, 10, 5),
         "amount": 0.5, "type": "jcp"},
        {"ex_date": date(2026, 8, 1), "payment_date": date(2026, 8, 20),
         "amount": 0.5, "type": "dividendo"},     # passado: sai
    ]
    ev = fi.eventos_de_proventos(linhas, hoje)
    assert [(e.data, e.tipo) for e in ev] == [("2026-10-01", "dividend"),
                                              ("2026-10-05", "dividend")]
    assert "data-com" in ev[0].descricao and "pagamento" in ev[1].descricao
    assert ev[0].source == "brapi" and ev[1].source == fi.FONTE_PROVENTOS
    assert ev[0].reference_date == "2026-10-01"


def test_montar_eventos_por_classe():
    hoje = date(2026, 9, 26)
    acao = fi.montar_eventos("acao", "PETR4", "BRL", hoje, proventos=[])
    assert [e.tipo for e in acao.itens] == ["earnings"]
    assert "prazo regulatório" in acao.itens[0].natureza
    assert set(acao.tipos_consultados) == {"dividend", "earnings"}
    # ação americana: sem prazo da CVM
    eua = fi.montar_eventos("acao", "AAPL", "USD", hoje, proventos=[])
    assert not eua.itens and eua.motivo.startswith(inf.NAO_DISPONIVEL)
    # fonte de proventos não consultada ≠ consultada e vazia
    fii = fi.montar_eventos("fii", "HGLG11", "BRL", hoje, proventos=None)
    assert fii.tipos_consultados == ()
    assert "Assembleia" in inf.texto_eventos(fii, "HGLG11")


def test_montar_eventos_tesouro():
    class _T:
        vencimento = date(2035, 5, 15)
        security_key = "IPCA+ 2035"
    ev = fi.montar_eventos("renda_fixa", "T", "BRL", date(2026, 9, 26),
                           proventos=None, titulo=_T(), eh_tesouro=True)
    assert ev.itens[0].tipo == "debt_maturity"
    assert ev.itens[0].data == "2035-05-15"


# -- leitores do arquivo --------------------------------------------------------------

ART = {
    "gerado_em": "2026-09-26T03:00:00+00:00",
    "noticias": {"janela_dias": 30, "base_ate": "2026-09-25", "fonte": "acervo",
                 "por_ticker": {
                     "PETR3": {"itens": [{
                         "headline": "Petrobras anuncia aquisição", "date": "2026-09-20",
                         "source": "Valor", "url": "https://x/1",
                         "impact_level": "HIGH",
                         "affected_dimension": ["estrategia", "valuation"],
                         "summary": None, "categoria": "fusao_aquisicao",
                         "motivo": "Fusão, aquisição ou venda de ativo",
                         "retrieved_at": None}],
                         "descartadas": {inf.D_13F: 2}},
                     "VALE3": {"itens": [], "descartadas": {inf.D_RUIDO: 3}}}},
    "relatorios": {"janela_dias": 180, "base_ate": {"b3": "2026-09-04"},
                   "fonte": {"b3": "CVM/IPE"},
                   "por_ticker": {"PETR4": [{
                       "tipo": "fato_relevante", "titulo": "Fato relevante",
                       "reference_date": "2026-09-01", "source": "CVM",
                       "source_url": "https://cvm/1", "retrieved_at": None,
                       "dimensoes": []}]}},
}


def test_noticias_de_usa_classe_irma_e_explica_ausencia():
    n = fi.noticias_de(ART, "PETR4", "acao")
    assert len(n.itens) == 1 and n.itens[0].affected_dimension == (
        "estrategia", "valuation")
    assert n.retrieved_at == "2026-09-26T03:00:00+00:00"
    v = fi.noticias_de(ART, "VALE3", "acao")
    assert not v.itens and "3 notícia(s)" in v.motivo
    assert v.descartadas == {inf.D_RUIDO: 3}
    assert fi.noticias_de(ART, "ZZZZ3", "acao").motivo.startswith(
        inf.NAO_DISPONIVEL)
    assert fi.noticias_de({}, "PETR4", "acao").motivo == fi.SEM_ARQUIVO
    assert "renda fixa" in fi.noticias_de(ART, "X", "renda_fixa").motivo


def test_relatorios_de():
    r = fi.relatorios_de(ART, "PETR3", "acao")
    assert r.documentos[0].tipo == inf.FATO_RELEVANTE
    assert r.base_ate == "2026-09-04" and r.fonte == "CVM/IPE"
    assert r.indicios()["mudou"] == r.documentos
    assert "SEC" in fi.relatorios_de(ART, "AAPL", "acao").motivo
    assert "04/09/2026" in fi.relatorios_de(ART, "ZZZZ3", "acao").motivo


def test_como_dict_ida_e_volta():
    n = fi.noticias_de(ART, "PETR4", "acao")
    assert inf.Noticias.de_dict(n.como_dict()) == n
    r = fi.relatorios_de(ART, "PETR4", "acao")
    assert inf.Relatorios.de_dict(r.como_dict()) == r
    e = fi.montar_eventos("acao", "PETR4", "BRL", date(2026, 9, 26), proventos=[])
    assert inf.Eventos.de_dict(e.como_dict()) == e


# -- publicador (puro) ----------------------------------------------------------------

def test_agrupar_noticias_filtra_e_deduplica():
    from scripts import publish_informacoes_recentes as pub
    quando = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
    linhas = [
        {"titulo": "MPF aciona Vale (VALE3) por barragem", "publicado_em": quando,
         "entidades": {"tickers": ["VALE3"]}, "url": "https://a", "veiculo": "A"},
        {"titulo": "MPF aciona Vale por barragem", "publicado_em": quando,
         "entidades": {"tickers": ["VALE3"]}, "url": "https://b", "veiculo": "B"},
        {"titulo": "Vale a pena investir em tecnologia?", "publicado_em": quando,
         "entidades": {"tickers": []}},
    ]
    resolver = lambda t: ("VALE3",) if "Vale" in t else ()  # noqa: E731
    saida = pub.agrupar_noticias(linhas, resolver, lambda t: False)
    vale = saida["VALE3"]
    assert len(vale["itens"]) == 1 and vale["itens"][0]["source"] == "A"
    assert vale["itens"][0]["categoria"] == "litigio"
    assert vale["descartadas"] == {inf.D_DUPLICADA: 1, inf.D_NAO_ASSUNTO: 1}


def test_agrupar_documentos_tipa_e_ignora_o_resto():
    from scripts import publish_informacoes_recentes as pub
    linhas = [
        {"ticker": "petr4", "categoria": "Fato Relevante", "tipo": "",
         "titulo": "Aquisição de ativo", "data": date(2026, 9, 1),
         "fonte": "CVM", "url": "https://cvm/1"},
        {"ticker": "PETR4", "categoria": "Fato Relevante", "tipo": "",
         "titulo": "Aquisição de ativo", "data": date(2026, 9, 1),
         "fonte": "CVM", "url": "https://cvm/1"},             # repetido
        {"ticker": "PETR4", "categoria": "Política de divulgação", "tipo": "",
         "titulo": "Política", "data": date(2026, 9, 2)},     # não interessa
    ]
    docs = pub.agrupar_documentos(linhas)["PETR4"]
    assert len(docs) == 1 and docs[0]["tipo"] == inf.FATO_RELEVANTE
    assert docs[0]["reference_date"] == "2026-09-01"
    assert "estrategia" in docs[0]["dimensoes"]


# -- provedores, texto para a LLM e cartões -------------------------------------------

def _info():
    return m.InfoBasica("PETR4", "Petrobras", "Ações BR", None, None, None,
                        "BRL", None, None, 5.0)


def _leitor(info):
    e = fi.montar_eventos("acao", "PETR4", "BRL", date(2026, 9, 26), proventos=[])
    return (fi.noticias_de(ART, "PETR4", "acao"),
            fi.relatorios_de(ART, "PETR4", "acao"), e)


def test_provedores():
    sn = secoes.provedor_noticias(_info(), None, leitor=_leitor)
    sr = secoes.provedor_relatorios(_info(), None, leitor=_leitor)
    se = secoes.provedor_eventos(_info(), None, leitor=_leitor)
    assert (sn.estado, sr.estado, se.estado) == (m.DISPONIVEL,) * 3
    assert sn.fonte == "acervo" and sr.fonte == "CVM/IPE"
    assert se.fonte == fi.FONTE_PRAZO_CVM
    vazio = secoes.coletar(_info(), None)   # fixture do conftest: sem arquivo
    for chave in ("noticias", "relatorios", "eventos"):
        assert vazio[chave].estado == m.SEM_DADOS
        assert vazio[chave].resumo == inf.NAO_DISPONIVEL


def test_texto_para_llm_separa_dado_e_nao_inventa():
    n, r, e = _leitor(None)
    tn = inf.texto_noticias(n, "PETR4")
    assert "DADO" in tn and "INTERPRETAÇÃO" in tn
    assert "Petrobras anuncia aquisição" in tn and "https://x/1" in tn
    tr = inf.texto_relatorios(r, "PETR4")
    assert "O que melhorou: " + inf.NAO_DISPONIVEL in tr
    te = inf.texto_eventos(inf.Eventos(), "PETR4")
    assert inf.NAO_DISPONIVEL in te and "Sem fonte de data para" in te


TRECHOS = (
    dr.Trecho("resultado", "O lucro foi de R$ 3 bi, alta de 8%.", "2026-08-10",
              "Release 2T26", "Press-release"),
    dr.Trecho("proventos", "<b>JCP</b> de R$ 0,50 por ação.", "2026-08-11",
              "Aviso || pauta longa", "Aviso"),
)


def test_provedor_relatorios_le_os_trechos_e_leva_a_llm():
    sr = secoes.provedor_relatorios(_info(), None, leitor=_leitor,
                                    trechos=lambda tk: TRECHOS)
    r = inf.Relatorios.de_dict(sr.dados)
    assert r.trechos == TRECHOS
    assert "2 fato(s) de 2 documento(s)" in sr.resumo
    assert "resultado, proventos e recompra" in sr.resumo
    tr = inf.texto_relatorios(r, "PETR4")
    assert "só metadados" not in tr and "Trechos · Resultado:" in tr
    assert "O lucro foi de R$ 3 bi" in tr and "10/08/2026" in tr
    # Sem documentos na vitrine, o corpus não é consultado.
    vazio = secoes.provedor_relatorios(
        _info(), None, leitor=lambda i: (None, inf.Relatorios(), None),
        trechos=lambda tk: TRECHOS)
    assert vazio.estado == m.SEM_DADOS


def test_corpo_relatorios_mostra_o_que_os_documentos_dizem():
    from views import inteligencia_ativos as tela
    hr = tela.corpo_relatorios(inf.Relatorios(documentos=(), trechos=TRECHOS))
    assert hr.index("Resultado") < hr.index("Proventos e recompra")
    assert "O lucro foi de R$ 3 bi" in hr and "10/08/2026" in hr
    assert "<b>JCP</b>" not in hr and "&lt;b&gt;JCP" in hr
    assert "pauta longa" not in hr and "Aviso" in hr
    assert "<table" not in hr and "documentos publicados" not in hr


def test_texto_da_analise_inclui_as_tres_camadas(monkeypatch):
    from core.inteligencia_ativos import analise
    monkeypatch.setattr(secoes, "_ler_informacoes", _leitor)
    import tests.test_inteligencia_ativos_adequacao as ad
    ctx = ad._ctx()
    a = ad._analise("TAEE11", ctx)
    txt = analise.texto_para_llm(a, ctx)
    ini = txt.index("[Notícias recentes")
    assert ini < txt.index("[Relatórios") < txt.index("[Próximos eventos")
    assert txt.index("[Próximos eventos") < txt.index("[Ação a considerar")


def test_cartoes_escapam_e_so_linkam_http():
    from views import inteligencia_ativos as tela
    n = inf.Noticias(itens=(inf.Noticia(
        headline="<script>x</script>", date="2026-09-20", source="S",
        url="javascript:alert(1)", impact_level=inf.HIGH,
        affected_dimension=("risco",), summary="<b>r</b>",
        categoria="litigio", motivo="Litígio"),), janela_dias=30)
    hn = tela.corpo_noticias(n)
    assert "<script>" not in hn and "&lt;script&gt;" in hn
    assert "javascript:" not in hn and "<b>r</b>" not in hn
    for cab in ("Data", "Impacto", "Dimensões", "Manchete", "Fonte"):
        assert cab in hn
    nn, r, e = _leitor(None)
    hl = tela.corpo_noticias(nn)
    assert 'href="https://x/1"' in hl and 'rel="noopener noreferrer"' in hl
    hr, he = tela.corpo_relatorios(r), tela.corpo_eventos(e)
    # Sem texto no acervo: aviso, não tabela de documentos.
    assert "ainda não está no acervo" not in hr and "<table" not in hr
    assert "Onde procurar" not in hr and "Pergunta" not in hr
    for cab in ("Evento", "Data", "Relevância", "Possível impacto"):
        assert cab in he
    assert "Sem fonte de data para" not in he   # vai ao log
    assert tela.corpo_noticias(inf.Noticias()) == inf.NAO_DISPONIVEL
    for html in (hn, hl, hr, he):
        assert not re.search(r"#[0-9a-fA-F]{3,6}\b", html)
