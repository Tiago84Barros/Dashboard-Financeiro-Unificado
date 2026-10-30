"""Relatório de analista por ativo (B3, EUA e FII), 29/10/2026.

Pedido do usuário: as seções de mercado "se tornem analistas profissionais".
O relatório sai da mesma avaliação por regras da Inteligência dos Ativos; os
gatilhos são as regras eliminatórias dela, com o número de hoje; a faixa de
preço é observada no histórico do próprio ativo, não alvo. Dados sintéticos.
"""
import json
from dataclasses import replace

import pytest

from core.inteligencia_ativos import avaliacao as av
from core.inteligencia_ativos import fontes_fundamentos as ff
from core.inteligencia_ativos import fundamentos as f
from core.inteligencia_ativos import informacoes as inf
from core.inteligencia_ativos import relatorio_analista as ra
from core.inteligencia_ativos import valuation as val
from core.inteligencia_ativos import veredito as vd
from tests.test_inteligencia_avaliacao import (  # noqa: F401
    BOA,
    _com,
    _fund,
    _linha,
    _noticia,
    an,
)
from tests.test_inteligencia_limitacoes import _com_pares
from tests.test_inteligencia_veredito import _leitores

HIST_PL = [8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0]
HIST_DY = [4.0, 5.0, 6.0, 7.0, 8.0]


def _gatilho(rel, indicador):
    return next(g for g in rel.gatilhos if g.indicador == indicador)


def _valuation(*linhas):
    return val.Valuation(f.ACAO, "BRL", tuple(linhas))


# -- faixas pelo histórico ------------------------------------------------------------

def test_faixa_pelo_pl_e_pelo_dy_segue_o_interquartil():
    pl = _linha("p_l", "P/L", 10.0, HIST_PL, referencia="12 meses")
    dy = _linha("dividend_yield", "Dividend yield", 6.0, HIST_DY,
                unidade=f.PCT)
    faixas, lim = ra.faixas_implicitas(_valuation(pl, dy), av.GERAL)
    assert not lim
    por = {fx.chave: fx for fx in faixas}
    est = val.estatistica(HIST_PL)
    assert por["p_l"].variacao_min == round((est.p25 / 10.0 - 1) * 100, 1)
    assert por["p_l"].variacao_max == round((est.p75 / 10.0 - 1) * 100, 1)
    assert por["p_l"].atual == "10,0x" and por["p_l"].referencia == "12 meses"
    assert por["p_l"].periodo == "8 observações"
    # DY maior é preço menor: o p75 do DY dá o extremo baixo do preço.
    est = val.estatistica(HIST_DY)
    assert por["dividend_yield"].variacao_min == round((6.0 / est.p75 - 1) * 100, 1)
    assert por["dividend_yield"].variacao_max == round((6.0 / est.p25 - 1) * 100, 1)
    for fx in faixas:
        assert fx.variacao_min < fx.variacao_max
        assert "não alvo" in fx.premissa


def test_sem_historico_vira_limitacao_e_ev_ebit_nao_gera_faixa():
    v = _valuation(_linha("p_vp", "P/VP", 2.0),
                   _linha("ev_ebit", "EV/EBIT", 7.0, [6.0, 7.0, 8.0]))
    faixas, lim = ra.faixas_implicitas(v, av.GERAL)
    assert faixas == ()
    assert lim == ("P/VP: sem faixa de preço — sem histórico do próprio "
                   "ativo.",)


def test_pl_abaixo_de_3_nao_gera_faixa():
    v = _valuation(_linha("p_l", "P/L", 2.5, HIST_PL))
    faixas, lim = ra.faixas_implicitas(v, av.GERAL)
    assert faixas == () and "lucro não recorrente" in lim[0]


def test_ciclica_avisa_que_o_lucro_pode_ser_de_pico():
    v = _valuation(_linha("p_l", "P/L", 10.0, HIST_PL),
                   _linha("p_vp", "P/VP", 1.0, [0.8, 1.0, 1.2]))
    faixas, _ = ra.faixas_implicitas(v, av.CICLICA)
    por = {fx.chave: fx for fx in faixas}
    assert "cíclica" in por["p_l"].premissa
    assert "cíclica" not in por["p_vp"].premissa


def test_fii_usa_cota_e_rendimento():
    v = val.Valuation(f.FII, "BRL", (
        _linha("p_vp", "P/VP", 0.9, [0.8, 0.9, 1.0, 1.1]),
        _linha("cap_rate", "Cap rate", 8.0, [7.0, 8.0, 9.0], unidade=f.PCT)))
    faixas, _ = ra.faixas_implicitas(v, av.FII)
    assert [fx.chave for fx in faixas] == ["p_vp"]
    assert "valor patrimonial da cota" in faixas[0].premissa


# -- gatilhos: as regras eliminatórias, com o número de hoje ----------------------------

def test_gatilho_dispara_junto_com_a_regra_que_elimina(an):  # noqa: F811
    casos = [
        _com(an["WEGE3"], _fund(**BOA)),
        _com(an["WEGE3"], _fund(**{**BOA, "divida_liquida_ebitda": 5.0})),
        _com(an["WEGE3"], _fund(**{**BOA, "cobertura_juros": 0.8})),
        _com(an["WEGE3"], _fund(**{**BOA, "payout": 120.0,
                                    "fluxo_caixa_livre": -10.0})),
        _com(an["WEGE3"], _fund(**BOA),
             noticias=[_noticia("Fraude contábil na empresa", "fraude")]),
        _com(an["WEGE3"], _fund(**BOA), noticias=[_noticia(
            "Empresa sai da recuperação judicial", "recuperacao_judicial")]),
        _com(an["TAEE11"], _fund(**{**BOA, "payout": 120.0,
                                     "fluxo_caixa_livre": -10.0})),
        _com(an["HGLG11"], _fund(f.FII, vacancia_financeira=35.0,
                                 inadimplencia=2.0)),
        _com(an["HGLG11"], _fund(f.FII, vacancia_financeira=3.0,
                                 inadimplencia=12.0)),
    ]
    for a in casos:
        rel = ra.relatorio_dos_insumos(a)
        eliminou = bool(av.avaliar(a).criticos)
        assert any(g.disparado and g.elimina for g in rel.gatilhos) == eliminou
        assert (rel.limite == vd.TROCAR) == eliminou


def test_alavancagem_usa_a_regua_do_setor(an):  # noqa: F811
    geral = ra.relatorio_dos_insumos(
        _com(an["WEGE3"], _fund(**{**BOA, "divida_liquida_ebitda": 5.0})))
    g = _gatilho(geral, "Dívida líquida/EBITDA")
    assert g.disparado and "4,50x" in g.regra and g.atual.startswith("5,00x")
    regulada = ra.relatorio_dos_insumos(
        _com(an["TAEE11"], _fund(**{**BOA, "divida_liquida_ebitda": 5.0})))
    g = _gatilho(regulada, "Dívida líquida/EBITDA")
    assert g.disparado is False and "setor regulado" in g.regra


def test_ebitda_negativo_dispara(an):  # noqa: F811
    dados = ff.dados_acao_b3(None, None, {"ebitda_negativo": True,
                                          "alavancagem_ref": "brapi"})
    fund = f.montar(f.ACAO, {**{k: f.Dado(v, "teste", "2025")
                                for k, v in BOA.items()
                                if k != "divida_liquida_ebitda"},
                             **dados}).como_dict()
    g = _gatilho(ra.relatorio_dos_insumos(_com(an["WEGE3"], fund)),
                 "Dívida líquida/EBITDA")
    assert g.disparado and "negativo" in g.atual


def test_dividendo_sem_caixa_no_setor_regulado_nao_elimina(an):  # noqa: F811
    rel = ra.relatorio_dos_insumos(_com(an["TAEE11"], _fund(
        **{**BOA, "payout": 120.0, "fluxo_caixa_livre": -10.0})))
    g = _gatilho(rel, "Dividendo sem lucro nem caixa")
    assert g.disparado and not g.elimina
    assert rel.limite != vd.TROCAR


def test_sem_dado_nao_e_disparo(an):  # noqa: F811
    rel = ra.relatorio_dos_insumos(an["WEGE3"])
    for nome in ("Dívida líquida/EBITDA", "Cobertura de juros",
                 "Patrimônio líquido", "Queda do preço em 12 meses"):
        g = _gatilho(rel, nome)
        assert g.disparado is None and g.atual == "sem dado"
    # Seção de notícias que não leu nada não é "nenhum fato".
    g = _gatilho(rel, "Fraude ou recuperação judicial")
    assert g.disparado is None and g.atual == "sem dado"
    calmo = ra.relatorio_dos_insumos(_com(an["WEGE3"], noticias=[
        _noticia("Resultado do trimestre")]))
    g = _gatilho(calmo, "Fraude ou recuperação judicial")
    assert g.disparado is False and g.atual == "nenhum fato em 60 dias"


def test_financeira_nao_tem_regra_de_balanco(an):  # noqa: F811
    rel = ra.relatorio_dos_insumos(_com(an["BBAS3"], _fund(**BOA)))
    nomes = {g.indicador for g in rel.gatilhos}
    assert "Dívida líquida/EBITDA" not in nomes
    assert "Cobertura de juros" not in nomes
    assert any("Banco e seguradora" in li for li in rel.limitacoes)


def test_incorporadora_le_divida_bruta_contra_o_teto(an):  # noqa: F811
    base = an["WEGE3"]
    a = replace(base, ativo=replace(base.ativo, setor="Construção Civil",
                                    subclasse=None))
    fund = _fund(**BOA)
    teto = av.teto_divida_patrimonio(a, f.Fundamentos.de_dict(fund), None)
    assert teto is not None
    a = _com(a, _fund(**{**BOA, "divida_bruta_patrimonio":
                         av.FATOR_CRITICO_DIVIDA_PL * teto[0] + 0.5}))
    rel = ra.relatorio_dos_insumos(a)
    assert av.avaliar(a).perfil == av.INCORPORADORA
    assert _gatilho(rel, "Dívida bruta/patrimônio").disparado
    # A razão sobre o EBITDA só informa na incorporadora.
    assert _gatilho(rel, "EBITDA de 12 meses").disparado is False
    assert rel.limite == vd.TROCAR


def test_fii_confere_vacancia_e_inadimplencia(an):  # noqa: F811
    rel = ra.relatorio_dos_insumos(_com(an["HGLG11"], _fund(
        f.FII, vacancia_fisica=40.0, inadimplencia=2.0)))
    assert _gatilho(rel, "Vacância física").disparado
    assert _gatilho(rel, "Inadimplência").disparado is False
    assert "Cobertura de juros" not in {g.indicador for g in rel.gatilhos}


def test_patrimonio_negativo_dispara(an):  # noqa: F811
    neg = val.LinhaValuation("p_vp", "P/VP", f.X, False,
                             motivo=val.NAO_SE_APLICA["patrimonio_negativo"])
    rel = ra.relatorio_dos_insumos(_com(an["WEGE3"], _fund(**BOA),
                                        linhas=[neg]))
    assert _gatilho(rel, "Patrimônio líquido").disparado
    assert any("P/VP: sem faixa" in li for li in rel.limitacoes)


def test_queda_forte_pede_revisao_sem_eliminar(an):  # noqa: F811
    rel = ra.relatorio_dos_insumos(
        _com_pares(an["WEGE3"], -40.0, (-38.0, -35.0, -42.0)))
    g = _gatilho(rel, "Queda do preço em 12 meses")
    assert g.disparado and not g.elimina and g.atual == "retorno de -40,0%"


# -- relatório inteiro ------------------------------------------------------------------

def test_tese_riscos_e_eliminatorio_primeiro(an):  # noqa: F811
    rel = ra.relatorio_dos_insumos(
        _com(an["WEGE3"], _fund(**{**BOA, "divida_liquida_ebitda": 5.0})))
    assert rel.riscos[0].startswith("Eliminatório: alavancagem de 5,00x")
    assert any("ROE de 22,0%" in t for t in rel.tese)
    assert rel.limite == vd.TROCAR and "eliminatório" in rel.motivo_limite


def test_sem_dado_vira_limitacao_declarada(an):  # noqa: F811
    rel = ra.relatorio_dos_insumos(an["WEGE3"])
    assert rel.tese == () and rel.riscos == () and rel.faixas == ()
    titulos = {sec.titulo for sec in (an["WEGE3"].fundamentos,
                                      an["WEGE3"].valuation,
                                      an["WEGE3"].pares,
                                      an["WEGE3"].noticias)}
    for t in titulos:
        assert any(li.startswith(f"{t}:") for li in rel.limitacoes)
    assert [le.dimensao for le in rel.leituras] == [
        av.ROTULO_DIMENSAO[k] for k in (av.QUALIDADE, av.VALUATION,
                                        av.MERCADO)]


def test_indicadores_ausentes_sao_nomeados(an):  # noqa: F811
    rel = ra.relatorio_dos_insumos(_com(an["WEGE3"], _fund(roe=20.0)))
    ausentes = next(li for li in rel.limitacoes
                    if li.startswith("Indicadores sem dado"))
    assert "ROIC" in ausentes


def test_noticias_de_impacto_mais_recentes_primeiro():
    def nt(titulo, data, nivel):
        return inf.Noticia(headline=titulo, date=data, source="Fonte",
                           url=None, impact_level=nivel, affected_dimension=(),
                           summary=None, categoria="resultado", motivo="m")
    nots = inf.Noticias(itens=(nt("velha", "2026-09-01", inf.HIGH),
                               nt("baixa", "2026-10-20", inf.LOW),
                               nt("nova", "2026-10-15", inf.MEDIUM)))
    saida = ra.noticias_de_impacto(nots)
    assert len(saida) == 2
    assert saida[0].startswith("15/10/2026 · Médio") and "nova" in saida[0]
    assert "velha (Fonte)" in saida[1]


def test_montar_pelos_provedores_e_o_mesmo_relatorio(an):  # noqa: F811
    a = _com(an["WEGE3"], _fund(**{**BOA, "divida_liquida_ebitda": 5.0}),
             linhas=[_linha("p_l", "P/L", 10.0, HIST_PL)])
    a = replace(a, ativo=replace(a.ativo, subclasse=None))
    obtido = ra.montar_relatorio(a.ativo.ticker, a.ativo.nome, a.ativo.setor,
                                 **_leitores(a))
    # O estado das seções é o do fixture (pendente, com dado injetado); o
    # dos provedores sai do dado. O resto do relatório tem de coincidir.
    titulos = tuple(f"{sec.titulo}:" for sec in (a.fundamentos, a.valuation,
                                                  a.pares, a.noticias))

    def sem_estado(r):
        return replace(r, limitacoes=tuple(
            li for li in r.limitacoes if not li.startswith(titulos)))
    assert sem_estado(obtido) == sem_estado(ra.relatorio_dos_insumos(a))
    assert obtido.gatilhos == ra.relatorio_dos_insumos(a).gatilhos


def test_texto_para_a_llm_carrega_limite_gatilho_e_aviso(an):  # noqa: F811
    rel = ra.relatorio_dos_insumos(_com(
        an["WEGE3"], _fund(**{**BOA, "divida_liquida_ebitda": 5.0}),
        linhas=[_linha("p_l", "P/L", 10.0, HIST_PL)]))
    t = ra.texto(rel)
    assert "LIMITE da recomendação: avaliar troca" in t
    assert "Gatilho Dívida líquida/EBITDA" in t and "[DISPARADO]" in t
    assert "Faixa pelo P/L" in t and "não alvo" in t
    assert "sem previsão de retorno" in t
    assert "não recomendação" not in t and "decisão é sua" not in t


def test_relatorio_diz_que_nao_preve_retorno(an):  # noqa: F811
    """As regras do relatório nunca foram testadas contra o retorno: a tela diz
    em uma frase, o que a medição gravada diz do score vai para as limitações
    (Restrições) e o texto da LLM leva as duas. Falha se a limitação voltar a
    ser texto fixo em vez de sair de ``data/vantagem_oos.json``."""
    rel = ra.relatorio_dos_insumos(_com(an["WEGE3"], _fund(**BOA)))
    assert "sem previsão de retorno" in rel.aviso
    assert "não foram testadas como previsoras" in rel.aviso
    assert "Rank-IC" not in rel.aviso
    lim = ra.limitacao_previsao("b3")
    assert lim in rel.limitacoes
    t = ra.texto(rel)
    assert f"Limitação: {lim}" in t and rel.aviso in t


def _medicao(tmp_path, motor, **campos):
    from core import vantagem_oos as vo
    reg = {"motor": motor, "medido_em": "2026-10-05",
           "versao_metodologia": vo.versao_corrente(motor),
           "formato": vo.FORMATO_COEFICIENTE, "n_periodos": 10,
           "media": 0.068, "ic_low": -0.0137, "ic_high": 0.1447}
    reg.update(campos)
    caminho = tmp_path / "vantagem_oos.json"
    caminho.write_text(json.dumps({motor: reg}), encoding="utf-8")
    return caminho


def test_limitacao_previsao_sai_da_medicao_gravada(tmp_path):
    """Cada veredito do portão vira a frase certa, com o número e a data da
    medição. Falha se o texto perder o número, inventar vantagem sem medição
    desta versão ou dizer "cruza o zero" quando o intervalo é todo negativo."""
    reprovado = ra.limitacao_previsao("b3", _medicao(tmp_path, "b3"))
    assert "não demonstrou vantagem" in reprovado
    assert "05/10/2026" in reprovado and "0,068" in reprovado
    assert "−0,014 a 0,145" in reprovado and "cruza o zero" in reprovado

    negativo = ra.limitacao_previsao(
        "b3", _medicao(tmp_path, "b3", media=-0.05, ic_low=-0.09, ic_high=-0.0004))
    assert "inteiro abaixo do zero" in negativo and "−0,0004" in negativo

    aprovado = ra.limitacao_previsao(
        "b3", _medicao(tmp_path, "b3", ic_low=0.02, ic_high=0.11))
    assert "demonstrou vantagem fora da amostra" in aprovado
    assert "não demonstrou" not in aprovado and "acima do zero" in aprovado

    velha = ra.limitacao_previsao(
        "b3", _medicao(tmp_path, "b3", versao_metodologia="0.0.1"))
    assert "metodologia 0.0.1" in velha and "não há evidência" in velha
    assert "0,068" not in velha

    ausente = ra.limitacao_previsao("fii", tmp_path / "nao_existe.json")
    assert "não tem medição" in ausente and "não há evidência" in ausente


def test_limitacao_previsao_us_diz_que_ordena_sem_superar(tmp_path):
    """Nos EUA o excesso reprova mas o Rank-IC é significativo: a frase diz as
    duas coisas. Falha se omitir a ordenação (o score pareceria inútil) ou o
    excesso (pareceria que bate a carteira igualmente ponderada)."""
    from core import vantagem_oos as vo
    caminho = _medicao(tmp_path, "us", formato=vo.FORMATO_PERCENTUAL,
                       media=-0.0405, ic_low=-0.1384, ic_high=0.0510,
                       extras={"rank_ic_medio": 0.1069, "rank_ic_t": 3.96})
    us = ra.limitacao_previsao("us", caminho)
    assert "ações americanas" in us and "−4,0%" in us and "5,1%" in us
    assert "cruza o zero" in us
    assert "Rank-IC médio 0,107, t = 3,96" in us
    assert "não superou a igualmente ponderada" in us


@pytest.mark.parametrize("palavra", ["preço-alvo de", "compre", "venda já"])
def test_texto_nao_vira_recomendacao(an, palavra):  # noqa: F811
    rel = ra.relatorio_dos_insumos(_com(an["WEGE3"], _fund(**BOA)))
    assert palavra not in ra.texto(rel).lower()


def test_fii_sem_dado_ainda_mostra_os_gatilhos_do_fundo(an):  # noqa: F811
    rel = ra.relatorio_dos_insumos(an["HGLG11"])
    nomes = {g.indicador for g in rel.gatilhos}
    assert {"Vacância", "Inadimplência"} <= nomes
    assert "Dívida líquida/EBITDA" not in nomes
    assert not rel.disparados
