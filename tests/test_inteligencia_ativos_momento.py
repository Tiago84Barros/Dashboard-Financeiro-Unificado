"""Inteligência dos Ativos: a decisão é sensível ao momento (pedido de
08/11/2026). "Por uma queda momentânea, uma sugestão de substituição ou
mesmo de redução deve ser vista com cuidado, pois pode representar uma perda
para o investidor."

Queda sem piora nos fundamentos não vira venda; queda com piora vira venda
em etapas, salvo alerta eliminatório do balanço."""
from dataclasses import replace

import pytest

from core.inteligencia_ativos import avaliacao as av
from core.inteligencia_ativos import momento as mo
from core.inteligencia_ativos import recomendacao as rec
from core.inteligencia_ativos import resumida as rs
from core.inteligencia_ativos import secoes
from core.inteligencia_ativos import veredito as vd
from tests.test_inteligencia_ativos_adequacao import _analise as _analisar
from tests.test_inteligencia_ativos_adequacao import _ctx
from tests.test_inteligencia_ativos_resumida import _sug
from tests.test_inteligencia_recomendacao import (
    ATIVO,
    CARTEIRA,
    FRAGIL,
    SUPERIOR,
    _caro,
    _com,
    _com_par,
    _fund,
    _peso,
    _posicao,
    _solida,
)


@pytest.fixture(scope="module")
def an():
    """As análises da carteira de ``test_inteligencia_recomendacao``, com as
    seções vazias: quem precisa de dado injeta."""
    with pytest.MonkeyPatch.context() as mp:
        for chave in secoes.SECOES:
            mp.setitem(secoes.PROVEDORES, chave, secoes._pendente(chave))
        ctx = _ctx(CARTEIRA)
        return {pp["ticker"]: _analisar(pp["ticker"], ctx)
                for pp in CARTEIRA["posicoes"]}


def _mom(resultado=None, ret=None, med=None, n=0, barato=False,
         piora=(), balanco=False, sem_leitura=False):
    return mo.Momento(resultado, ret, med, n, barato, tuple(piora), balanco,
                      sem_leitura)


# -- a leitura do momento --------------------------------------------------------

def test_tipos_do_momento():
    assert _mom().tipo == mo.SEM_QUEDA
    assert _mom(resultado=-5.0, ret=-5.0).tipo == mo.SEM_QUEDA
    assert _mom(resultado=-12.0).tipo == mo.QUEDA_DE_PRECO
    assert _mom(ret=-25.0).tipo == mo.QUEDA_DE_PRECO
    # preço barato só conta como queda se o preço de fato caiu
    assert _mom(barato=True).tipo == mo.SEM_QUEDA
    assert _mom(barato=True, ret=-3.0).tipo == mo.QUEDA_DE_PRECO
    assert _mom(resultado=-12.0, piora=("qualidade frágil",)).tipo == \
        mo.QUEDA_COM_DETERIORACAO
    assert _mom(resultado=-12.0, sem_leitura=True).tipo == mo.QUEDA_SEM_LEITURA


def test_segmento_caiu_junto_ou_caiu_mais():
    junto = _mom(ret=-25.0, med=-22.0, n=4)
    assert junto.segmento_caiu_junto is True
    assert "o segmento caiu junto" in junto.linha()
    sozinho = _mom(ret=-25.0, med=3.0, n=4)
    assert sozinho.segmento_caiu_junto is False
    assert "caiu mais que o segmento" in sozinho.linha()
    assert _mom(ret=-25.0).segmento_caiu_junto is None


def test_linha_separa_fato_de_conclusao():
    assert _mom().linha() is None
    preco = _mom(resultado=-15.0).linha()
    assert "15,0% abaixo do valor investido" in preco
    assert "não é motivo de venda" in preco
    piora = _mom(resultado=-15.0, piora=("qualidade frágil",)).linha()
    assert "Há piora nos fundamentos (qualidade frágil)" in piora


def test_resultado_da_posicao():
    info = replace(_INFO, valor_mercado=85.0, valor_investido=100.0)
    assert mo.resultado_da_posicao(info) == pytest.approx(-15.0)
    assert mo.resultado_da_posicao(replace(info, valor_investido=None)) is None
    assert mo.resultado_da_posicao(replace(info, valor_investido=0.0)) is None


_INFO = vd.info_ativo("WEGE3")


def test_medir_le_retorno_dos_pares_e_a_piora(an):
    a = _com_par(_peso(an["WEGE3"], 2.5), {**ATIVO, "retorno_12m": -30.0},
                 {**SUPERIOR, "retorno_12m": -28.0})
    a = _posicao(a, 70.0, 100.0)
    m = mo.medir(a, av.avaliar(a), quebra=("aumento da dívida",))
    assert m.resultado_pct == pytest.approx(-30.0)
    assert m.retorno_12m == pytest.approx(-30.0)
    assert m.segmento_caiu_junto is True
    assert "gatilho da tese: aumento da dívida" in m.deterioracao
    assert m.tipo == mo.QUEDA_COM_DETERIORACAO


def test_alerta_so_do_noticiario_nao_e_chamado_de_fundamento(an):
    a = _posicao(_peso(an["WEGE3"], 2.5), 70.0, 100.0)
    noticia = av.Avaliacao(av.GERAL, (
        av.Dimensao(av.QUALIDADE, av.FRAGIL),
        av.Dimensao(av.VALUATION, av.JUSTO),
        av.Dimensao(av.MERCADO, av.NEGATIVO)),
        (av.Alerta("recuperação judicial na manchete", True, noticia=True),))
    m = mo.medir(a, noticia)
    assert not m.alerta_do_balanco
    assert m.deterioracao == ("alerta no noticiário, a confirmar: "
                              "recuperação judicial na manchete",)


# -- o ajuste da decisão ------------------------------------------------------------

def _sit(pesado=False):
    return rs._Situacao(
        atual=5.0, alvo=2.5, dif=2.5, acima=pesado, abaixo=False,
        concentracao=(), excesso_teto=None, severa=False, fora=None,
        quebra=(), est=rec.Esticamento(0, True, ()), confronto=None)


_REDUZIR = rs._Rascunho(rs.REDUZIR_PARCIAL,
                        "reduzir cerca de 20% da posição: preço esticado",
                        "Realizar parte trava o ganho.", proporcao=0.2)
_VENDER = rs._Rascunho(rs.VENDER, "vender: tese quebrada e qualidade frágil",
                       "Gatilho disparado.", proporcao=1.0)
_SUBSTITUIR = rs._Rascunho(rs.SUBSTITUIR, "substituir por RAPT4: qualidade "
                           "frágil", "Qualidade frágil.", proporcao=1.0)


def test_sem_queda_a_decisao_fica():
    for r in (_REDUZIR, _VENDER, _SUBSTITUIR):
        assert rs._ajustar_ao_momento(r, _sit(), _mom()) is r


def test_queda_de_preco_nao_vende():
    queda = _mom(resultado=-15.0)
    r = rs._ajustar_ao_momento(_REDUZIR, _sit(pesado=False), queda)
    assert r.codigo == rs.MANTER and r.proporcao is None
    assert "não vender na queda" in r.detalhe
    assert "15,0% abaixo do valor investido" in r.motivo
    assert r.teto == rec.MODERADA
    assert any("a venda parcial volta a valer" in c for c in r.condicoes)
    r = rs._ajustar_ao_momento(_REDUZIR, _sit(pesado=True), queda)
    assert r.codigo == rs.REBALANCEAR and "pelos aportes" in r.detalhe


def test_queda_com_piora_vende_em_etapas():
    queda = _mom(resultado=-15.0, piora=("qualidade frágil",))
    r = rs._ajustar_ao_momento(_VENDER, _sit(), queda)
    assert r.codigo == rs.REDUZIR_PARCIAL
    assert r.proporcao == pytest.approx(rs.FRACAO_MAX_REDUCAO)
    assert r.detalhe.startswith("vender em etapas: reduzir cerca de 1/3 agora")
    assert any("vender o restante" in c for c in r.condicoes)
    # Reduzir parte já é em etapas; substituir fica, com o aviso.
    assert rs._ajustar_ao_momento(_REDUZIR, _sit(), queda) is _REDUZIR
    sub = rs._ajustar_ao_momento(_SUBSTITUIR, _sit(), queda)
    assert sub.codigo == rs.SUBSTITUIR and "vende na queda" in sub.motivo


def test_queda_sem_leitura_tambem_vende_em_etapas():
    r = rs._ajustar_ao_momento(_VENDER, _sit(),
                               _mom(resultado=-15.0, sem_leitura=True))
    assert r.codigo == rs.REDUZIR_PARCIAL


def test_alerta_do_balanco_mantem_a_venda_inteira():
    queda = _mom(resultado=-40.0, piora=("alerta eliminatório: dívida",),
                 balanco=True)
    assert rs._ajustar_ao_momento(_VENDER, _sit(), queda) is _VENDER


def test_decisao_que_nao_vende_nao_muda():
    queda = _mom(resultado=-15.0)
    for codigo in (rs.MANTER, rs.COMPRAR, rs.REBALANCEAR, rs.ACOMPANHAR):
        r = rs._Rascunho(codigo, "x", "y")
        assert rs._ajustar_ao_momento(r, _sit(), queda) is r


# -- o caminho inteiro, pela decisão ---------------------------------------------------

def test_empresa_solida_em_prejuizo_nao_realiza_preco_esticado(an):
    """Sem queda, "muito esticado" realiza 20%. Com a posição 15% abaixo do
    investido e nenhum fundamento pior, não há ganho a realizar."""
    base = _solida(an, 2.5, _caro(95.0))
    assert rs.decisao(base, _sug(2.5)).codigo == rs.REDUZIR_PARCIAL
    d = rs.decisao(_posicao(base, 85.0, 100.0), _sug(2.5))
    assert d.codigo == rs.MANTER and not d.movimenta and d.custos is None
    assert d.momento is not None and d.momento.tipo == mo.QUEDA_DE_PRECO
    assert d.contexto[0].startswith("Momento:")


def test_fragil_com_substituto_em_queda_avisa_mas_troca(an):
    a = _com_par(_peso(_com(an["WEGE3"], _fund(**FRAGIL)), 2.5),
                 {**ATIVO, "retorno_12m": -30.0},
                 {**SUPERIOR, "retorno_12m": -5.0})
    d = rs.decisao(a, _sug(2.5), na_carteira=("WEGE3",))
    assert d.codigo == rs.SUBSTITUIR
    assert "vende na queda" in d.motivo
    assert d.momento.tipo == mo.QUEDA_COM_DETERIORACAO


def test_momento_chega_a_llm_pelo_veredito(an):
    base = _posicao(_solida(an, 2.5, _caro(95.0)), 85.0, 100.0)
    d = rs.decisao(base, _sug(2.5))
    v = vd.veredito_de_decisao("WEGE3", d)
    texto = vd.texto_ticker(v, "WEGE3")
    assert "Momento:" in texto and "não é motivo de venda" in texto
    assert v.limite != vd.REDUZIR



def test_momento_entra_nos_motivos_da_substituicao():
    from core.inteligencia_ativos import confronto_modelo as cf
    from tests import test_inteligencia_ativos_confronto_modelo as tc
    v = tc._vered("PETR4", "reduzir_parcial", tc._av(av.FRAGIL),
                  proporcao=1 / 3)
    v.recomendacao.momento = _mom(resultado=-20.0, piora=("qualidade frágil",))
    c = cf.montar(tc._ref(), {"PETR4": v}, {"VALE3": tc._av(av.FORTE)},
                  alocacao_estrategia=tc.ESTRATEGIA,
                  analises={"PETR4": tc._analise("PETR4", tc.PETR, pares=(
                      ("VALE3", tc.VALE_MELHOR),))})
    x = c.substituicao_de("PETR4")
    assert x.modo == cf.TROCA and "33% da posição de PETR4" in x.texto
    assert any(m.startswith("Momento: A posição está 20,0% abaixo")
               for m in x.motivos)


def test_tela_mostra_o_momento_numa_secao_propria(an):
    from views import inteligencia_ativos_resumida as tela
    base = _posicao(_solida(an, 2.5, _caro(95.0)), 85.0, 100.0)
    d = rs.decisao(base, _sug(2.5))
    html = tela.cartao_recomendacao(d)
    assert "Momento: o ativo está em queda" in html
    assert html.count("15,0% abaixo do valor investido") == 1
    sem = tela.cartao_recomendacao(rs.decisao(_solida(an, 2.5), _sug(2.5)))
    assert "Momento:" not in sem


def test_caiu_menos_que_o_segmento_nao_vira_caiu_mais():
    """Codex P2 (#698): −20% contra −40% dos pares é cair menos, não mais."""
    melhor = _mom(ret=-20.0, med=-40.0, n=4)
    assert melhor.segmento_caiu_junto is False
    assert "caiu menos que o segmento" in melhor.linha()
    assert "caiu mais" not in melhor.linha()
    pior = _mom(ret=-30.0, med=-5.0, n=4)
    assert "caiu mais que o segmento" in pior.linha()


def test_perda_de_exatamente_dez_por_cento_conta_como_queda():
    """Codex P2 (#698): 90 contra 100 dá −9,999…% em ponto flutuante; o
    limite é inclusivo e tem de valer com valores reais da posição."""
    info = replace(_INFO, valor_mercado=90.0, valor_investido=100.0)
    m = _mom(resultado=mo.resultado_da_posicao(info))
    assert m.em_prejuizo_relevante and m.tipo == mo.QUEDA_DE_PRECO
    assert _mom(ret=(80.0 / 100.0 - 1.0) * 100.0).queda_forte_12m




def test_limites_do_confronto_tambem_sao_inclusivos():
    """Gêmeo do achado do Codex: 5 pp e 3 pp são "ou mais" também quando a
    subtração em ponto flutuante fica um fio abaixo (12% − 7% = 4,999…;
    10% − 7% = 2,999…)."""
    from core.inteligencia_ativos import confronto_modelo as cf
    from core.inteligencia_ativos import referencia_modelo as rm
    real, alvo = 0.12 * 100, 0.07 * 100
    assert real - alvo < 5.0
    k = cf.DivergenciaClasse("b3", "Empresas B3", alvo, real, None,
                             cf.SEM_AVALIACAO, "")
    assert k.acentuada
    linha = rm.LinhaAtivo("PETR4", "Petrobras", "b3", peso_modelo=0.07 * 100,
                          peso_real=0.10 * 100, situacao=rm.NOS_DOIS)
    assert linha.diferenca < 3.0
    ref = rm.ReferenciaModelo(disponivel=True, ativos={"PETR4": linha})
    c = cf.montar(ref, {}, {})
    assert c.da_carteira("PETR4") is not None
