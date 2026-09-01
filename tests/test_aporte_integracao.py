"""
As duas pontas que ligam `core.aporte` ao resto do app.

1. `AporteRebalance` — a politica que impede o motor de movimentacao de
   emitir "vender" quando o usuario escolheu convergir por aporte. Sem ela,
   a tela mostraria o plano de aporte ao lado de uma ordem de venda para a
   mesma carteira: duas respostas contraditorias, nenhuma delas errada
   isoladamente.

2. `views.portfolio_global.valores_por_classe` — a conversao de peso para
   reais que alimenta o painel. Ela devolve `{}` sem patrimonio em vez de
   inventar uma base, que e a familia de defeito de
   `memoria: medir-a-fonte-que-a-decisao-le`.
"""
from datetime import date

import pandas as pd

from core import transaction_costs
from core.global_portfolio import advisor
from core.global_portfolio.signals import Sinal
from core.rebalancing import AporteRebalance, CalendarRebalance
from views.portfolio_global import valores_por_classe

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
# valores_por_classe
# ──────────────────────────────────────────────────────────────────────

def test_valores_por_classe_agrega_peso_e_multiplica_pelo_patrimonio():
    df = pd.DataFrame([
        {"symbol": "AAA", "asset_class": "b3", "weight_global": 0.30},
        {"symbol": "BBB", "asset_class": "b3", "weight_global": 0.20},
        {"symbol": "CCC", "asset_class": "fii", "weight_global": 0.50},
    ])
    assert valores_por_classe(df, 100_000.0) == {"b3": 50_000.0, "fii": 50_000.0}


def test_valores_por_classe_sem_patrimonio_devolve_vazio():
    """Sem a base em reais, derivar reais de peso seria inventar o numero."""
    df = pd.DataFrame([{"symbol": "AAA", "asset_class": "b3", "weight_global": 1.0}])
    assert valores_por_classe(df, None) == {}
    assert valores_por_classe(df, 0.0) == {}


def test_valores_por_classe_com_df_vazio_devolve_vazio():
    assert valores_por_classe(pd.DataFrame(), 100_000.0) == {}
