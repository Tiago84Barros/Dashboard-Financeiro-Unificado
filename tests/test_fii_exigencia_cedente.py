"""A exigência do próprio usuário cede — em degraus, e nunca além da casa.

O defeito que estes testes prendem: os controles da tela são portões duros, e
nenhuma das cessões existentes consegue aumentar o conjunto de candidatos que
eles deixaram. Varrendo 216 combinações realistas contra o snapshot publicado,
36 devolviam ZERO candidatos (carteira vazia, contra a regra permanente de
nunca zerar) e 126 devolviam menos de cinco — "carteira" que não diversifica.
"""
from __future__ import annotations

import pytest

from core.fii_exigencia_cedente import (
    EIXOS,
    PISO_DE_DIVERSIFICACAO,
    ajustar_politica_de_carteira,
    ceder_exigencia_ate_diversificar,
    escada,
    nota_de_cessao,
    nota_de_concentracao,
)
from core.fii_integrated_model import IntegratedEligibilityPolicy
from core.fii_portfolio_v4 import PortfolioPolicy

_BASE = {
    "tipo": "tijolo", "liquidez_diaria": 2e6, "pvp": .95,
    "history_months": 40, "max_drawdown": -.22,
    "dy_12m": .10, "income_recurrence": .90,
    "tenant_concentration": .10, "lease_expiry_concentration_24m": .10,
}

CASA = IntegratedEligibilityPolicy()


def _linha(ticker: str, **mudancas) -> dict:
    return {**_BASE, "ticker": ticker, **mudancas}


def _universo(n: int = 8) -> list[dict]:
    """Fundos que passam no padrão da casa e falham em qualquer aperto."""
    return [_linha(f"FII{i:02d}11") for i in range(n)]


# --- a escada só desfaz aperto -------------------------------------------

def test_no_padrao_da_casa_a_escada_esta_vazia():
    # O caminho comum não paga nada e não muda nada: é o que garante que o
    # backtest point-in-time e qualquer chamador já validado sigam idênticos.
    assert escada(CASA) == []


def test_usuario_mais_frouxo_que_a_casa_nao_gera_degrau():
    # Voltar "em direção ao padrão" apertaria quem já está mais frouxo. A
    # escada afrouxa ou não faz nada; apertar nunca é uma opção dela.
    frouxo = IntegratedEligibilityPolicy(
        min_daily_liquidity=5e5, min_recurrent_dy_12m=.04,
        min_history_months=6, max_drawdown=.60)
    assert escada(frouxo) == []


def test_nenhum_degrau_passa_do_padrao_da_casa():
    apertado = IntegratedEligibilityPolicy(
        min_daily_liquidity=10e6, min_recurrent_dy_12m=.12,
        min_history_months=60, max_drawdown=.15,
        require_pvp_below_one=True, require_multi_region=True,
        require_min_properties=True, require_multicategory=True)
    for politica, _ in escada(apertado):
        assert politica.min_daily_liquidity >= CASA.min_daily_liquidity
        assert politica.min_recurrent_dy_12m >= CASA.min_recurrent_dy_12m
        assert politica.min_history_months >= CASA.min_history_months
        assert politica.max_drawdown <= CASA.max_drawdown
    ultimo, _ = escada(apertado)[-1]
    assert ultimo == CASA, "o teto da escada é exatamente o nível validado"


def test_a_escada_cede_em_degraus_e_na_ordem_do_risco():
    apertado = IntegratedEligibilityPolicy(
        min_daily_liquidity=10e6, max_drawdown=.15,
        require_pvp_below_one=True)
    primeiro, _ = escada(apertado)[0]
    # A preferência (P/VP<1) sai antes de qualquer portão de risco: desligá-la
    # não admite fundo mais arriscado, só para de recusar quem não a atende.
    assert primeiro.require_pvp_below_one is False
    assert primeiro.min_daily_liquidity == 10e6
    assert primeiro.max_drawdown == pytest.approx(.15)
    # E a liquidez volta antes do drawdown, que é o degrau mais caro.
    liquidez = next(i for i, (p, _) in enumerate(escada(apertado))
                    if p.min_daily_liquidity < 10e6)
    drawdown = next(i for i, (p, _) in enumerate(escada(apertado))
                    if p.max_drawdown > .15)
    assert liquidez < drawdown
    # Gradativamente: o primeiro degrau da liquidez não salta para o padrão.
    politica, _ = escada(apertado)[liquidez]
    assert CASA.min_daily_liquidity < politica.min_daily_liquidity < 10e6


def test_todo_eixo_da_escada_existe_na_politica():
    # Um eixo com nome errado sairia da escada em silêncio: `getattr` acharia
    # o atributo da política do usuário e o da casa iguais, `_mais_estrito`
    # daria False e o degrau simplesmente nunca apareceria.
    for eixo in EIXOS:
        assert hasattr(CASA, eixo.atributo), eixo.atributo


# --- a escada entrega carteira onde antes não havia nenhuma ---------------

def test_universo_zerado_pelo_usuario_volta_a_ter_candidatos():
    # O caso medido na tela (liquidez 5 M/dia + drawdown 15%): zero
    # candidatos, `status=blocked`, carteira publicada vazia.
    apertado = IntegratedEligibilityPolicy(
        min_daily_liquidity=5e6, max_drawdown=.15)
    universo = ceder_exigencia_ate_diversificar(_universo(8), apertado)
    assert universo.candidatos_no_pedido == 0
    assert universo.candidatos >= PISO_DE_DIVERSIFICACAO
    assert universo.cedeu and universo.piso_alcancado


def test_cede_o_minimo_e_para_no_primeiro_degrau_que_basta():
    # Oito fundos com 2 M/dia; o usuário pede 3 M. Basta a liquidez voltar um
    # degrau — nenhum outro eixo pode ser tocado depois disso.
    apertado = IntegratedEligibilityPolicy(min_daily_liquidity=3e6)
    universo = ceder_exigencia_ate_diversificar(_universo(8), apertado)
    assert universo.piso_alcancado
    assert len(universo.degraus) == 1
    assert "liquidez" in universo.degraus[0]
    assert universo.politica.max_drawdown == CASA.max_drawdown
    assert universo.politica.min_recurrent_dy_12m == pytest.approx(.08)


def test_universo_suficiente_nao_cede_nada():
    universo = ceder_exigencia_ate_diversificar(_universo(8), CASA)
    assert not universo.cedeu
    assert universo.politica == CASA
    assert universo.candidatos == universo.candidatos_no_pedido == 8


def test_universo_pobre_de_verdade_entrega_o_que_existe_sem_zerar():
    # Três fundos no mundo inteiro. Nenhum degrau alcança o piso, e mesmo
    # assim a criação de portfólio não pode terminar vazia.
    apertado = IntegratedEligibilityPolicy(min_daily_liquidity=5e6)
    universo = ceder_exigencia_ate_diversificar(_universo(3), apertado)
    assert not universo.piso_alcancado
    assert universo.candidatos == 3
    # A escada foi até o fim procurando o quarto fundo e não o achou, mas o
    # nível que ela ENTREGA é o primeiro que rendeu os três: descer até o
    # padrão da casa sem ganhar ativo seria pagar proteção por nada.
    assert universo.politica.min_daily_liquidity == pytest.approx(2e6)
    assert universo.politica.min_daily_liquidity > CASA.min_daily_liquidity


def test_conta_a_fila_da_concessao_como_candidato():
    # Reprovado só em portão de proteção continua sendo material de carteira:
    # o orquestrador o readmite. Contar apenas os estritos faria a escada
    # ceder exigência que ela não precisava ceder.
    linhas = _universo(1) + [
        _linha(f"PROT{i}11", income_recurrence=.20) for i in range(6)]
    universo = ceder_exigencia_ate_diversificar(linhas, CASA)
    assert len(universo.elegiveis) == 1
    assert len(universo.relatorio["concession_candidates"]) == 6
    assert universo.candidatos == 7 and not universo.cedeu


# --- a cessão é visível, e a política de carteira a acompanha -------------

def test_a_cessao_aparece_declarada():
    apertado = IntegratedEligibilityPolicy(min_daily_liquidity=3e6)
    universo = ceder_exigencia_ate_diversificar(_universo(8), apertado)
    nota = nota_de_cessao(universo)
    assert "padrão da casa" in nota
    assert "não é ausência de risco" in nota
    assert universo.degraus[0] in nota


def test_carteira_abaixo_do_piso_declara_a_concentracao():
    apertado = IntegratedEligibilityPolicy(min_daily_liquidity=5e6)
    universo = ceder_exigencia_ate_diversificar(_universo(3), apertado)
    nota = nota_de_concentracao(universo, entregues=3)
    assert "abaixo do piso" in nota
    assert "concentração" in nota
    assert "nem no padrão da casa" in nota


def test_sem_cessao_e_com_piso_alcancado_as_notas_ficam_vazias():
    universo = ceder_exigencia_ate_diversificar(_universo(8), CASA)
    assert nota_de_cessao(universo) == ""
    assert nota_de_concentracao(universo, entregues=8) == ""


def test_a_concentracao_se_mede_nos_ativos_entregues_nao_nos_candidatos():
    # Candidato não é ativo: o otimizador pode entregar menos do que recebeu.
    # Oito candidatos e duas posições continua sendo carteira concentrada, e
    # medir pelo universo esconderia exatamente esse caso.
    universo = ceder_exigencia_ate_diversificar(_universo(8), CASA)
    nota = nota_de_concentracao(universo, entregues=2)
    assert "2 ativo(s), abaixo do piso" in nota
    # Sem cessão, a causa é outra: os controles já estão no nível validado.
    assert "já estão no padrão da casa" in nota


def test_politica_de_carteira_segue_a_liquidez_cedida():
    # Ceder liquidez na elegibilidade e não aqui readmitiria o fundo para
    # barrá-lo adiante no teto de posição ilíquida do MILP: a cessão pagaria
    # o preço em proteção sem entregar o ativo.
    cedida = IntegratedEligibilityPolicy(min_daily_liquidity=1e6)
    ajustada = ajustar_politica_de_carteira(
        PortfolioPolicy(min_daily_liquidity=5e6), cedida)
    assert ajustada.min_daily_liquidity == pytest.approx(1e6)
    assert ajustada.max_assets == PortfolioPolicy().max_assets


def test_politica_de_carteira_nao_afrouxa_sozinha():
    intacta = PortfolioPolicy(min_daily_liquidity=1e6)
    assert ajustar_politica_de_carteira(
        intacta, IntegratedEligibilityPolicy(min_daily_liquidity=5e6)) is intacta


# --- contra o universo publicado, que é o que a tela lê ------------------

# Os ajustes que o usuário reproduziu na tela. Cada um é alcançável com um
# arrasto de controle na barra lateral, e o primeiro entregava UM FII.
CONTROLES_DA_TELA = {
    "liquidez 5 M/dia": dict(min_daily_liquidity=5e6),
    "liquidez 10 M/dia": dict(min_daily_liquidity=10e6),
    "drawdown máx 15%": dict(max_drawdown=.15),
    "renda recorrente 12%": dict(min_recurrent_dy_12m=.12),
    "liq 5 M + drawdown 15%": dict(min_daily_liquidity=5e6, max_drawdown=.15),
    "tudo no máximo": dict(
        min_daily_liquidity=10e6, min_recurrent_dy_12m=.12,
        min_history_months=60, max_drawdown=.15, require_pvp_below_one=True),
}


@pytest.fixture(scope="module")
def universo_publicado() -> list[dict]:
    """O snapshot que a tela lê — a fonte que a decisão usa, não um proxy."""
    from core.market_read import load_fii_methodology_inputs

    quadro = load_fii_methodology_inputs()
    if quadro.empty:
        pytest.skip("vitrine de FIIs indisponível neste ambiente")
    return quadro.to_dict("records")


@pytest.mark.parametrize("nome", list(CONTROLES_DA_TELA))
def test_nenhum_ajuste_da_tela_deixa_o_universo_abaixo_do_piso(
        universo_publicado, nome):
    """O defeito relatado, na fonte que a tela lê.

    Antes desta escada, seis destes ajustes deixavam menos de cinco
    candidatos e dois deixavam ZERO — carteira publicada vazia. Varrendo as
    216 combinações dos controles, eram 36 zeradas e 126 abaixo do piso.
    """
    politica = IntegratedEligibilityPolicy(**CONTROLES_DA_TELA[nome])
    do_padrao = ceder_exigencia_ate_diversificar(universo_publicado, CASA)
    if do_padrao.candidatos < PISO_DE_DIVERSIFICACAO:
        pytest.skip("nem o padrão da casa sustenta o piso nesta safra")

    universo = ceder_exigencia_ate_diversificar(universo_publicado, politica)

    assert universo.candidatos >= PISO_DE_DIVERSIFICACAO, (
        f"{nome}: {universo.candidatos} candidato(s) mesmo depois da escada")
    assert universo.candidatos >= universo.candidatos_no_pedido, (
        "a escada nunca pode reduzir o universo que o usuário já tinha")


def test_a_escada_nao_toca_o_caminho_do_padrao_da_casa(universo_publicado):
    """O backtest PIT e o portão de publicação rodam no padrão da casa.

    Se a escada cedesse alguma coisa aqui, ela mudaria a safra validada sem
    que ninguém tivesse pedido — e a metodologia publicada passaria a ser
    outra que não a medida.
    """
    universo = ceder_exigencia_ate_diversificar(universo_publicado, CASA)
    assert not universo.cedeu
    assert universo.politica == CASA
    assert universo.degraus == ()
