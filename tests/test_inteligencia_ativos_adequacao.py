"""Inteligência dos Ativos: contexto, papéis, adequação e fluxo da análise."""
import pytest

from core.estrategia import politica as pol
from core.estrategia import repositorio as repo
from core.inteligencia_ativos import analise, contexto, papeis, secoes
from core.inteligencia_ativos import modelos as m

BASE = {
    "objective": "renda_passiva", "time_horizon": "longo",
    "risk_profile": "moderado", "liquidity_need": "baixa",
    "predominant_strategy": "dividendos",
    "asset_class_targets": {"renda_fixa": 40, "acoes_br": 20, "fiis": 30,
                            "exterior": 10},
}


def _registro(status="COMPLETED", **extra):
    politica = pol.aplicar({}, {**BASE, **extra}, fonte="manual")[0]
    return repo.Registro(
        id="r1", version=2, status_gravado=status,
        schema_version=pol.SCHEMA_VERSION, politica=politica, entrevista=[],
        completion_pct=100, completed_at=None, created_at=None,
        updated_at=None)


def _pos(ticker, classe, pct, setor=None, moeda="BRL", nome=None):
    return {"ticker": ticker, "nome": nome or ticker, "classe": classe,
            "setor": setor, "moeda": moeda, "pct_carteira": pct,
            "total_investido": pct * 100, "valor_mercado": pct * 110}


CARTEIRA = {"total_mercado": 11000.0, "posicoes": [
    _pos("HGLG11", "FII", 12.0, "Logística"),
    _pos("XPML11", "FII", 8.0, "Shoppings"),
    _pos("TAEE11", "Ações BR", 30.0, "Utilidades"),
    _pos("TESOURO IPCA+ 2035", "Tesouro Direto", 45.0,
         nome="Tesouro IPCA+ 2035"),
    _pos("AAPL", "Ações BR", 5.0, "Tecnologia", moeda="USD"),
]}


def _ctx(carteira=CARTEIRA, **extra):
    return contexto.montar(_registro(**extra), carteira)


def _analise(ticker, ctx):
    pos = next(p for p in ctx.posicoes if p["ticker"] == ticker)
    return analise.analisar(pos, ctx)


# -- contexto -------------------------------------------------------------------

def test_moeda_vence_o_rotulo_e_classe_nao_mapeada_fica_fora():
    assert contexto.classe_politica({"classe": "Ações BR", "moeda": "USD"}) == "exterior"
    assert contexto.classe_politica({"classe": "Tesouro Direto"}) == "renda_fixa"
    assert contexto.classe_politica({"classe": "Cripto"}) is None


def test_contexto_soma_a_carteira_por_classe_e_setor():
    ctx = _ctx()
    assert ctx.peso_por_classe == {"renda_fixa": 45.0, "acoes_br": 30.0,
                                   "fiis": 20.0, "exterior": 5.0}
    assert ctx.peso_por_setor["Logística"] == 12.0
    assert ctx.versao_politica == 2 and ctx.estrategia == "dividendos"
    assert len(ctx.posicoes) == 5


def test_contexto_recusa_politica_nao_concluida():
    with pytest.raises(contexto.PoliticaNaoConcluida):
        contexto.montar(_registro(status="IN_PROGRESS"), CARTEIRA)
    with pytest.raises(contexto.PoliticaNaoConcluida):
        contexto.montar(None, CARTEIRA)


def test_analisar_exige_o_contexto():
    with pytest.raises(TypeError):
        analise.analisar(CARTEIRA["posicoes"][0], None)


# -- papéis ---------------------------------------------------------------------

def test_papeis_em_linguagem_natural():
    a = _analise("HGLG11", _ctx())
    assert papeis.em_linguagem_natural(a.papeis) == [
        "Papel principal: renda imobiliária.",
        "Papel secundário: geração de renda."]
    assert a.papel_principal.codigo == "real_estate_income"


def test_papeis_por_tipo_de_ativo():
    ctx = _ctx()
    assert _analise("TESOURO IPCA+ 2035", ctx).papel_principal.codigo == \
        "inflation_protection"
    assert _analise("TAEE11", ctx).papel_principal.codigo == "dividend"
    assert _analise("AAPL", ctx).papel_principal.codigo == \
        "international_diversification"


def test_papeis_do_usuario_substituem_os_inferidos():
    ctx = _ctx()
    pos = CARTEIRA["posicoes"][0]
    escolhidos = papeis.papeis_do_usuario(["income"])
    a = analise.analisar(pos, ctx, papeis_usuario=escolhidos)
    assert [p.codigo for p in a.papeis] == ["income"]
    assert a.papeis[0].fonte == "usuario"


# -- faixa e ação -----------------------------------------------------------------

def test_faixa_usa_alvo_da_classe_sem_inventar_alvo_do_ativo():
    fx = _analise("HGLG11", _ctx()).faixa
    assert fx.alvo_classe == 30 and fx.peso_classe == 20.0
    assert fx.desvio_classe == -10.0
    assert fx.teto_ativo is None  # política sem limite por ativo


def test_classe_abaixo_do_alvo_e_aporte_compativel():
    a = _analise("HGLG11", _ctx())
    assert a.acao.estado == m.APORTE_COMPATIVEL
    assert a.acao.completa is False
    assert "tolerância de 5 pp adotada pelo sistema" in a.acao.justificativas[0]


def test_classe_acima_do_alvo_pede_reavaliar_aportes():
    assert _analise("TAEE11", _ctx()).acao.estado == m.REAVALIAR_APORTES


def test_classe_na_faixa_e_exposicao_adequada():
    assert _analise("AAPL", _ctx()).acao.estado == m.EXPOSICAO_ADEQUADA


def test_limite_por_ativo_estourado_vence_como_concentracao():
    a = _analise("TAEE11", _ctx(single_asset_limit_pct=20))
    assert a.acao.estado == m.REDUZIR_CONCENTRACAO
    # a regra perdedora (classe acima do alvo) continua como justificativa
    assert any("acima do alvo" in j for j in a.acao.justificativas[1:])
    conc = next(g for g in a.tese.gatilhos if g.codigo == "concentracao")
    assert conc.disparado is True and a.tese.valida is False


def test_ativo_no_limite_com_classe_abaixo_nao_e_aporte():
    a = _analise("HGLG11", _ctx(single_asset_limit_pct=12.5))
    assert a.acao.estado == m.EXPOSICAO_ADEQUADA


def test_classe_com_alvo_zero_reavalia_a_tese():
    alvo = {"renda_fixa": 50, "acoes_br": 20, "fiis": 30, "exterior": 0}
    a = _analise("AAPL", _ctx(asset_class_targets=alvo))
    assert a.acao.estado == m.REAVALIAR_TESE
    funcao = next(g for g in a.tese.gatilhos if g.codigo == "funcao_original")
    assert funcao.disparado is True


def test_classe_fora_da_politica_reavalia_a_tese():
    carteira = {"total_mercado": 100.0,
                "posicoes": [_pos("BTC", "Cripto", 100.0)]}
    a = _analise("BTC", _ctx(carteira))
    assert a.ativo.classe_politica is None
    assert a.acao.estado == m.REAVALIAR_TESE


def test_gatilhos_de_mercado_ficam_pendentes():
    a = _analise("TAEE11", _ctx())
    pendentes = {g.codigo for g in a.tese.gatilhos if g.disparado is None}
    assert {"reducao_dividendos", "aumento_divida", "perda_qualidade",
            "mudanca_estrategia"} <= pendentes
    assert a.tese.valida is None  # sem fundamentos não há "válida"


def test_prioridade_das_acoes():
    ordem = sorted(m.PRIORIDADE_ACAO, key=m.PRIORIDADE_ACAO.get, reverse=True)
    assert ordem[:2] == [m.REAVALIAR_TESE, m.REDUZIR_CONCENTRACAO]
    assert ordem[-1] == m.MANTER
    assert set(m.ROTULO_ACAO) == set(m.PRIORIDADE_ACAO)


# -- seções externas e as quatro perguntas ------------------------------------------

def test_secoes_externas_na_ordem_da_tela():
    a = _analise("HGLG11", _ctx())
    assert [s.chave for s in a.secoes_externas] == list(secoes.SECOES)
    # nenhuma seção segue pendente; sem dado (nem cenário cadastrado) na
    # suíte offline todas saem SEM_DADOS
    assert all(s.estado == m.SEM_DADOS for s in a.secoes_externas)
    cen = next(s for s in a.secoes_externas if s.chave == "cenario")
    assert "Configurações → Geral → Cenário de Investimentos" in cen.resumo
    q = a.questoes
    assert q[m.Q_FUNDAMENTOS].estado == m.SEM_DADOS
    assert "nenhum dos 14 indicadores" in q[m.Q_FUNDAMENTOS].resposta
    assert q[m.Q_ADEQUACAO].estado == m.DISPONIVEL
    assert "renda imobiliária" in q[m.Q_FUNCAO].resposta


def test_provedor_que_falha_vira_sem_dados(monkeypatch):
    def _quebra(info, ctx):
        raise RuntimeError("fora do ar")
    monkeypatch.setitem(secoes.PROVEDORES, "valuation", _quebra)
    a = _analise("HGLG11", _ctx())
    assert a.valuation.estado == m.SEM_DADOS
    assert a.fundamentos.estado == m.SEM_DADOS
    assert a.fundamentos.dados["tipo"] == "fii"  # os demais seguem de pé


# -- fluxo completo --------------------------------------------------------------

def test_carteira_analisada_do_maior_para_o_menor_peso():
    ctx = _ctx()
    pesos = [a.ativo.peso_atual for a in analise.analisar_carteira(ctx)]
    assert pesos == sorted(pesos, reverse=True) and len(pesos) == 5


def test_texto_para_llm_leva_politica_carteira_e_ativo():
    ctx = _ctx()
    texto = analise.texto_para_llm(_analise("HGLG11", ctx), ctx)
    assert "Objetivo principal: Renda passiva" in texto
    assert "CARTEIRA COMPLETA" in texto
    for ticker in ("HGLG11", "XPML11", "TAEE11", "AAPL"):
        assert ticker in texto
    assert "ATIVO EM ANÁLISE: HGLG11" in texto
    assert "Papel principal: renda imobiliária." in texto
    assert m.ROTULO_ACAO[m.APORTE_COMPATIVEL] in texto
    assert "=== FUNDAMENTOS: HGLG11 ===" in texto and "[INTERPRETAÇÃO" in texto


def test_como_dict_serializa():
    d = _analise("HGLG11", _ctx()).como_dict()
    assert d["ativo"]["ticker"] == "HGLG11"
    assert d["acao"]["estado"] == m.APORTE_COMPATIVEL
