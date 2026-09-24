"""
As duas pontas que ligam `core.aporte` ao resto do app.

1. `AporteRebalance` — a politica que impede o motor de movimentacao de
   emitir "vender" quando o usuario escolheu convergir por aporte. Sem ela,
   a tela mostraria o plano de aporte ao lado de uma ordem de venda para a
   mesma carteira: duas respostas contraditorias, nenhuma delas errada
   isoladamente.

2. `core.global_portfolio.carteira_real.base_do_plano` — o que alimenta o
   painel. Antes era o `weight_global` das carteiras-modelo vezes o
   patrimonio: como `weight_global` e o proprio alvo, o desvio saia sempre
   0% (`memoria: medir-a-fonte-que-a-decisao-le`). Os testes abaixo provam
   que o plano agora le a carteira real e acusa desvio.
"""
from datetime import date

import pandas as pd
import pytest

from core import transaction_costs
from core.aporte import plano_de_aporte
from core.global_portfolio import advisor, carteira_real
from core.global_portfolio.signals import Sinal
from core.rebalancing import AporteRebalance, CalendarRebalance

ONTEM = date(2026, 1, 1)
HOJE = date(2026, 6, 1)


# ──────────────────────────────────────────────────────────────────────
# AporteRebalance
# ──────────────────────────────────────────────────────────────────────

def test_aporte_rebalance_nunca_dispara_negociacao_por_desvio():
    politica = AporteRebalance()
    deve, motivo = politica.deve_rebalancear(
        {"AAA": 0.90, "BBB": 0.10}, {"AAA": 0.50, "BBB": 0.50}, HOJE, ONTEM)
    assert deve is False
    assert "aporte" in motivo.lower(), "o motivo precisa dizer que a correcao vem do aporte"


def test_aporte_rebalance_permite_a_alocacao_inicial():
    """Carteira que ainda nao existe nao tem o que vender."""
    deve, _ = AporteRebalance().deve_rebalancear({}, {"AAA": 1.0}, HOJE, None)
    assert deve is True


def test_aporte_rebalance_pode_recusar_ate_a_alocacao_inicial():
    deve, _ = AporteRebalance(alocacao_inicial=False).deve_rebalancear(
        {}, {"AAA": 1.0}, HOJE, None)
    assert deve is False


def test_aporte_rebalance_enxerga_saida_de_posicao_no_motivo():
    """Mesmo sem disparar ordem, o desvio precisa ser nomeado — inclusive o de
    um ativo que saiu do alvo, que e o maior desvio possivel."""
    _, motivo = AporteRebalance().deve_rebalancear(
        {"SAIU": 0.30, "BBB": 0.70}, {"BBB": 0.70}, HOJE, ONTEM)
    assert "SAIU" in motivo


def _df_posicoes():
    return pd.DataFrame([
        {"symbol": "AAA", "asset_class": "b3", "weight_global": 0.80},
        {"symbol": "BBB", "asset_class": "b3", "weight_global": 0.20},
    ])


def _sinal(symbol: str, valor: float) -> Sinal:
    return Sinal(nome="teste", symbol=symbol, valor=valor,
                 direcao="aumentar" if valor > 0 else "reduzir",
                 analisador="metrics", texto="fixture")


def _sinais():
    return [_sinal("AAA", -1.0), _sinal("BBB", 1.0)]


def test_motor_com_aporte_rebalance_nao_emite_venda_nem_reducao():
    acoes = advisor.recomendar(
        _df_posicoes(), _sinais(), alvos={"b3": 1.0},
        politica=AporteRebalance(alocacao_inicial=False),
        custos={}, patrimonio_total=100_000.0, data_atual=HOJE, ultimo_rebal=ONTEM,
    )
    assert acoes, "o motor precisa devolver as posicoes, so que sem ordem de venda"
    assert {a.acao for a in acoes} <= {"manter", "indeterminado"}


def test_o_alvo_continua_visivel_mesmo_sem_ordem():
    """`peso_sugerido` documenta para onde o modelo aponta; so a acao muda."""
    acoes = advisor.recomendar(
        _df_posicoes(), _sinais(), alvos={"b3": 1.0},
        politica=AporteRebalance(alocacao_inicial=False),
        custos={}, patrimonio_total=100_000.0, data_atual=HOJE, ultimo_rebal=ONTEM,
    )
    aaa = next(a for a in acoes if a.symbol == "AAA")
    assert aaa.peso_sugerido < aaa.peso_atual, (
        "o modelo continua apontando que AAA esta pesado demais — a politica so "
        "impede a VENDA, nao apaga o diagnostico"
    )


def test_calendar_rebalance_continua_emitindo_ordem():
    """Guarda de nao-regressao: a politica antiga nao mudou de comportamento.

    Precisa de custo calibrado, senao a regra 1 do advisor derruba tudo para
    `manter` e o teste passaria a medir o guard de custo em vez da politica.
    """
    custos = {"b3": transaction_costs.CostConfig(corretagem_fixa=0.0)}
    acoes = advisor.recomendar(
        _df_posicoes(), _sinais(), alvos={"b3": 1.0},
        politica=CalendarRebalance(intervalo_dias=1),
        custos=custos, patrimonio_total=100_000.0, data_atual=HOJE, ultimo_rebal=ONTEM,
    )
    assert {a.acao for a in acoes} & {"aumentar", "reduzir", "vender"}


def test_aporte_rebalance_segura_a_venda_mesmo_com_custo_calibrado():
    """O contraste que da sentido ao teste acima: mesmo insumo, so muda a
    politica, e a ordem de venda desaparece."""
    custos = {"b3": transaction_costs.CostConfig(corretagem_fixa=0.0)}
    acoes = advisor.recomendar(
        _df_posicoes(), _sinais(), alvos={"b3": 1.0},
        politica=AporteRebalance(alocacao_inicial=False),
        custos=custos, patrimonio_total=100_000.0, data_atual=HOJE, ultimo_rebal=ONTEM,
    )
    assert {a.acao for a in acoes} <= {"manter", "indeterminado"}


# ──────────────────────────────────────────────────────────────────────
# Plano sobre a carteira real
# ──────────────────────────────────────────────────────────────────────

def _pos(classe, vm, moeda="BRL", pais="BR"):
    return {"classe": classe, "valor_mercado": vm, "moeda": moeda, "pais": pais}


def test_plano_le_a_carteira_real_e_acusa_desvio():
    """O defeito original: o desvio saia sempre 0%. Carteira toda em acoes
    contra alvo 50/50 com FII tem de mostrar desvio e mandar o aporte ao FII."""
    valores, alvos, fora = carteira_real.base_do_plano(
        [_pos("Ações BR", 100_000.0)], {"b3": 0.5, "fii": 0.5}, None,
    )
    plano = plano_de_aporte(valores, alvos, 10_000.0)
    assert plano.desvio_antes == pytest.approx(0.5)
    aportes = {a.symbol: a.valor_aportado for a in plano.alocacoes}
    assert aportes["fii"] == pytest.approx(10_000.0)
    assert fora == []


def test_renda_fixa_com_alvo_entra_como_classe():
    valores, alvos, fora = carteira_real.base_do_plano(
        [_pos("Ações BR", 60_000.0), _pos("Tesouro Direto", 40_000.0)],
        {"b3": 1.0}, 0.3,
    )
    assert valores == {"b3": 60_000.0, "renda_fixa": 40_000.0}
    assert alvos == pytest.approx({"b3": 0.7, "renda_fixa": 0.3})
    assert fora == []


def test_renda_fixa_sem_alvo_sai_do_plano_e_e_declarada():
    """Sem alvo, tratar RF como 0% mandaria todo aporte para longe dela."""
    valores, alvos, fora = carteira_real.base_do_plano(
        [_pos("Ações BR", 60_000.0), _pos("Renda Fixa", 40_000.0)], {"b3": 1.0}, None,
    )
    assert valores == {"b3": 60_000.0}
    assert fora == ["renda_fixa"]


def test_acao_americana_rotulada_como_acoes_br_conta_como_exterior():
    assert carteira_real.classe_global(_pos("Ações BR", 1.0, moeda="USD", pais="US")) == "us"
    assert carteira_real.classe_global(_pos("BDR", 1.0)) == "us"
    assert carteira_real.classe_global(_pos("FII", 1.0)) == "fii"
    assert carteira_real.classe_global(_pos("Cripto", 1.0)) == "outros"


def test_outros_pesa_no_patrimonio_e_nao_recebe_aporte():
    valores, alvos, _ = carteira_real.base_do_plano(
        [_pos("Ações BR", 90_000.0), _pos("Cripto", 10_000.0)], {"b3": 1.0}, None,
    )
    plano = plano_de_aporte(valores, alvos, 5_000.0)
    aportes = {a.symbol: a.valor_aportado for a in plano.alocacoes}
    assert aportes.get("outros", 0.0) == 0.0
    assert plano.desvio_antes == pytest.approx(0.1)


def test_posicao_sem_valor_nao_entra():
    assert carteira_real.valores_reais_por_classe(
        [_pos("Ações BR", 0.0), _pos("FII", None), {"classe": "FII", "valor_mercado": "x"}]
    ) == {}


def test_formulario_zero_de_renda_fixa_vira_fora_do_plano():
    from views.portfolio_global import _renda_fixa_do_formulario

    assert _renda_fixa_do_formulario(0.0) is None
    assert _renda_fixa_do_formulario(None) is None
    assert _renda_fixa_do_formulario(25.0) == pytest.approx(0.25)
