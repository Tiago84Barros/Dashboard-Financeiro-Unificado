"""Painel da Inteligência dos Ativos: resumo da carteira, cartões e histórico."""
import datetime as dt
from dataclasses import replace

import pytest

from core.inteligencia_ativos import analise, painel
from core.inteligencia_ativos import calculos as calc
from core.inteligencia_ativos import historico as hist
from core.inteligencia_ativos import modelos as m
from tests.test_inteligencia_ativos_adequacao import _ctx
from tests.test_inteligencia_ativos_tela import CARTEIRA_COMPLETA, _liberada, _rodar
from views import inteligencia_ativos_painel as tela_painel

# A aba mostra os blocos da estratégia e do cenário: repositórios em memória.
pytestmark = pytest.mark.usefixtures("estrategia_falsa", "cenario_falso")

HOJE = dt.date(2026, 9, 26)
CARTEIRA_APP = {"total_investido": 10000.0, "rentabilidade_total_pct": 10.0,
                "rentabilidade_total_disponivel": True}
PROVENTOS = {"total_12m": 500.0, "total_historico": 900.0,
             "data_source": "supabase"}


def _montar(**extra):
    ctx = _ctx(**extra)
    return ctx, analise.analisar_carteira(ctx)


# -- resumo --------------------------------------------------------------------------

def test_resumo_traz_patrimonio_rentabilidade_renda_e_estrategia():
    ctx, an = _montar(single_asset_limit_pct=10)
    r = painel.resumo(ctx, an, CARTEIRA_APP, PROVENTOS, hoje=HOJE)
    assert r.patrimonio == 11000.0 and r.total_investido == 10000.0
    assert r.rentabilidade_pct == 10.0 and r.resultado == 1000.0
    assert (r.renda_12m, r.renda_total) == (500.0, 900.0)
    assert r.objetivo == "Renda passiva" and r.estrategia == "Dividendos"
    assert r.versao_politica == 2
    linhas = {ln.rotulo: ln for ln in r.alocacao}
    assert linhas["Ações Brasil"].desvio == 10.0
    assert linhas["Ações Brasil"].status == calc.ACIMA
    assert linhas["Fundos imobiliários"].status == calc.ABAIXO
    assert any(c.chave == "TESOURO IPCA+ 2035" for c in r.concentracoes)
    assert r.alertas and all(a.severidade in (calc.ALTA, calc.MEDIA)
                             for a in r.alertas)
    assert len(r.alertas) <= painel.MAX_ALERTAS
    # quem pede revisão, na ordem de prioridade da ação
    assert [x.ticker for x in r.revisao][:1] == ["TESOURO IPCA+ 2035"]
    assert all(x.acao in painel.ACOES_DE_REVISAO for x in r.revisao)


def test_resumo_nao_inventa_rentabilidade_nem_renda():
    ctx, an = _montar()
    r = painel.resumo(ctx, an, {"total_investido": 10000.0,
                                "rentabilidade_total_pct": 12.0,
                                "rentabilidade_total_disponivel": False},
                      {"total_12m": 500.0, "data_source": "error"}, hoje=HOJE)
    assert r.rentabilidade_pct is None and r.resultado is None
    assert r.renda_12m is None and r.renda_total is None
    html = tela_painel.cartao_resumo(r)
    assert "sem cotação suficiente" not in html
    assert "proventos indisponíveis" not in html


def test_resumo_lista_so_eventos_futuros():
    ctx, an = _montar()
    ev = m.Secao("eventos", "Próximos eventos", m.DISPONIVEL, "ok", dados={
        "itens": [
            {"tipo": "earnings", "data": "2026-11-14", "descricao": "3T26",
             "natureza": "anunciado"},
            {"tipo": "earnings", "data": "2026-08-01", "descricao": "2T26",
             "natureza": "anunciado"}]})
    an = [replace(a, eventos=ev) if a.ativo.ticker == "TAEE11" else a
          for a in an]
    r = painel.resumo(ctx, an, hoje=HOJE)
    assert [(e.ticker, e.data[:10]) for e in r.eventos] == [
        ("TAEE11", "2026-11-14")]
    card = next(c for c in painel.cards(an, ctx, hoje=HOJE)
                if c.ticker == "TAEE11")
    assert card.evento == "Divulgação de resultado em 14/11/2026"


# -- cartões --------------------------------------------------------------------------

def test_cards_tem_os_dez_campos_e_revisao_vem_primeiro():
    ctx, an = _montar(single_asset_limit_pct=10)
    cs = painel.cards(an, ctx, hoje=HOJE)
    assert len(cs) == 5
    revisao = [c.em_revisao for c in cs]
    assert revisao == sorted(revisao, reverse=True)       # revisão antes
    fora = [c for c in cs if not c.em_revisao]
    assert [c.peso for c in fora] == sorted((c.peso for c in fora), reverse=True)
    c = next(x for x in cs if x.ticker == "HGLG11")
    assert c.peso == 12.0 and c.valor == 1320.0
    assert c.faixa == "classe alvo 30% · teto 10%"
    assert c.papel == "renda imobiliária"
    assert c.tese_rotulo in painel.ROTULO_TESE.values()
    assert "concentração" in c.risco.lower()
    assert c.acao == m.REDUZIR_CONCENTRACAO
    html = tela_painel.cartao_ativo(c)
    for rotulo in ("Peso na carteira", "Target / faixa", "Papel",
                   "Status da tese", "Valuation", "Principal risco",
                   "Próximo evento", "Ação a considerar"):
        assert rotulo in html
    assert "#" not in html.replace("&#", "")


def test_riscos_poe_gatilho_disparado_antes_do_alerta():
    ctx, an = _montar(single_asset_limit_pct=10)
    a = next(x for x in an if x.ativo.ticker == "HGLG11")
    rs = painel.riscos(a, ctx)
    disparados = [g for g in a.tese.gatilhos if g.disparado]
    assert disparados, "o limite de 10% dispara o gatilho de concentração"
    assert rs[0].startswith(disparados[0].descricao)
    assert any("HGLG11 representa 12%" in r for r in rs)
    assert len(rs) == len(set(rs))
    ctx2, an2 = _montar()
    xp = next(x for x in an2 if x.ativo.ticker == "XPML11")
    assert painel.card(xp, ctx2, hoje=HOJE).risco == painel.SEM_RISCO


def test_valuation_compara_sem_veredito():
    ctx, an = _montar()
    for a in an:
        txt = painel.texto_valuation(a).lower()
        assert "barato" not in txt and "caro" not in txt


def test_resumo_so_usa_tokens_de_tema():
    ctx, an = _montar(single_asset_limit_pct=10)
    r = painel.resumo(ctx, an, CARTEIRA_APP, PROVENTOS, hoje=HOJE)
    html = tela_painel.cartao_resumo(r, ["Desde a última análise, x."])
    for trecho in ("Patrimônio total", "Rentabilidade", "Renda gerada",
                   "Alocação atual vs alvo", "Concentrações",
                   "Objetivo e estratégia", "Cenário", "Alertas relevantes",
                   "Próximos eventos", "Ativos que merecem revisão",
                   "Desde a última análise"):
        assert trecho in html
    assert "#" not in html.replace("&#", "")


def test_anterior_pula_a_foto_gravada_nesta_sessao():
    a = hist.Snapshot("X", hist.Auditoria("2026-09-01T00:00:00", 1))
    b = replace(a, auditoria=hist.Auditoria("2026-09-26T00:00:00", 1))
    assert tela_painel.anterior([a, b], gravada_agora=True) is a
    assert tela_painel.anterior([a, b], gravada_agora=False) is b
    assert tela_painel.anterior([b], gravada_agora=True) is None


# -- tela ---------------------------------------------------------------------------

def test_aba_mostra_resumo_cartoes_e_abre_a_analise(_historico_em_memoria):
    app = _rodar(_liberada(), CARTEIRA_COMPLETA)
    assert not app.exception
    htmls = [md.value for md in app.markdown]
    assert any("Patrimônio total" in h for h in htmls)
    cartoes = [h for h in htmls if "Manter, comprar ou vender" in h
               and "Porcentagem devida" in h]
    assert len(cartoes) == 2
    # a primeira visita grava a foto de cada ativo e a da carteira
    guardado = hist.ler(_historico_em_memoria["extra"])
    assert set(guardado) == {"HGLG11", "TAEE11", hist.CARTEIRA}
    assert guardado["HGLG11"][0].motivo == hist.PRIMEIRA

    app.button(key="ia_resumo_TAEE11").click().run(timeout=30)
    assert not app.exception
    assert app.selectbox(key="ia_ativo").value == "TAEE11"
    assert any("TAEE11" in md.value for md in app.markdown)
    assert not app.json  # o contexto da LLM não vai à tela
    # o histórico é gravado, mas não aparece na tela do investidor
    assert not any("Histórico da análise" in md.value for md in app.markdown)
    assert not any(b.key == "ia_hist_salvar_TAEE11" for b in app.button)
    # a nova execução não grava de novo: uma tentativa por sessão
    assert len(hist.ler(_historico_em_memoria["extra"])["TAEE11"]) == 1


def test_falha_do_historico_nao_derruba_a_aba(monkeypatch):
    from core.inteligencia_ativos import historico_repo as hrepo

    def quebra(*_a, **_k):
        raise RuntimeError("banco fora")
    monkeypatch.setattr(hrepo, "registrar", quebra)
    app = _rodar(_liberada(), CARTEIRA_COMPLETA)
    assert not app.exception
    app.toggle(key="ia_detalhe").set_value(True).run(timeout=30)
    assert not app.exception
    assert not any("Histórico da análise" in md.value for md in app.markdown)
