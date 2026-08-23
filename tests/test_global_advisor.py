"""O motor de movimentacao: sinais -> Acao, com as duas guardas responsaveis."""
import math
import random
from datetime import date

import pandas as pd
import pytest

from core.global_portfolio.advisor import recomendar
from core.global_portfolio.signals import Sinal
from core.portfolio_constraints import project_capped_simplex
from core.rebalancing import CalendarRebalance, ThresholdRebalance
from core.transaction_costs import CLASSE_B3, CLASSE_FII, CLASSE_US, CostConfig


def _linha(symbol, classe="b3", peso=0.1, sector="financials", country="BR"):
    return {"asset_class": classe, "symbol": symbol, "sector": sector,
            "country": country, "currency": "BRL", "weight_global": peso}


def _sinal(nome, symbol, valor, analisador):
    direcao = "aumentar" if valor > 0 else ("reduzir" if valor < 0 else "neutro")
    return Sinal(nome=nome, symbol=symbol, valor=valor, direcao=direcao,
                analisador=analisador, texto=f"{nome}:{symbol}:{valor}")


# CalendarRebalance com intervalo 0 sempre dispara (delta em dias >= 0), e
# serve de "sempre rebalanceia hoje" real (nao fake) para testes que nao
# querem exercitar o gate de politica.
_SEMPRE_REBALANCEAR = CalendarRebalance(intervalo_dias=0)
_HOJE = date(2026, 8, 16)
_ONTEM = date(2026, 8, 15)


# ---------------------------------------------------------------------------
# Regra 1a: movimento menor que o custo vira manter
# ---------------------------------------------------------------------------

def test_movimento_menor_que_custo_vira_manter():
    # Replica o exemplo do proprio plano: realocacao de 0,3% cujo custo fixo
    # (corretagem) supera o valor movimentado. Patrimonio pequeno + corretagem
    # fixa real fazem o cenario acontecer sem inventar nada.
    df = pd.DataFrame([_linha("A", peso=0.50), _linha("B", peso=0.50)])
    sinais = [_sinal("qualidade", "A", 0.05, "metrics")]  # score fraco -> delta pequeno
    cfg = CostConfig(classe=CLASSE_B3, corretagem_fixa=20.0, spread_bps_large=10.0,
                     spread_bps_small=10.0, ir_rate=0.0, isencao_mes=1e12)
    resultado = recomendar(
        df, sinais, alvos={}, politica=_SEMPRE_REBALANCEAR,
        custos={CLASSE_B3: cfg}, patrimonio_total=1_000.0,
        data_atual=_HOJE, sensibilidade=1.0,
    )
    por_symbol = {a.symbol: a for a in resultado}
    assert por_symbol["A"].acao == "manter"
    assert por_symbol["A"].custo_calibrado is True
    assert por_symbol["A"].custo_estimado > 0
    # o alvo (peso_sugerido) continua visivel mesmo com a execucao adiada
    assert por_symbol["A"].peso_sugerido != por_symbol["A"].peso_atual


def test_movimento_maior_que_custo_e_recomendado():
    df = pd.DataFrame([_linha("A", peso=0.50), _linha("B", peso=0.50)])
    sinais = [_sinal("qualidade", "A", 0.9, "metrics")]  # score forte -> delta grande
    cfg = CostConfig(classe=CLASSE_B3, corretagem_fixa=0.0, spread_bps_large=10.0,
                     spread_bps_small=10.0, ir_rate=0.0, isencao_mes=1e12)
    resultado = recomendar(
        df, sinais, alvos={}, politica=_SEMPRE_REBALANCEAR,
        custos={CLASSE_B3: cfg}, patrimonio_total=1_000_000.0,
        data_atual=_HOJE, sensibilidade=1.0,
    )
    por_symbol = {a.symbol: a for a in resultado}
    assert por_symbol["A"].acao == "aumentar"
    assert por_symbol["A"].custo_calibrado is True
    assert por_symbol["A"].custo_estimado > 0
    assert por_symbol["A"].peso_sugerido > por_symbol["A"].peso_atual


# ---------------------------------------------------------------------------
# Regra 1b: custo nao calibrado nunca vira aprovacao por acidente (nan)
# ---------------------------------------------------------------------------

def test_custo_nao_calibrado_vira_manter_nunca_aprova_por_acidente():
    df = pd.DataFrame([_linha("USA", classe="us", peso=0.50), _linha("USB", classe="us", peso=0.50)])
    sinais = [_sinal("qualidade", "USA", 0.9, "metrics")]  # score bem forte
    resultado = recomendar(
        df, sinais, alvos={}, politica=_SEMPRE_REBALANCEAR,
        custos={},  # mapa vazio -> custo_por_classe cai em nao_calibrado
        patrimonio_total=1_000_000.0, data_atual=_HOJE, sensibilidade=1.0,
    )
    acao_usa = next(a for a in resultado if a.symbol == "USA")
    assert acao_usa.acao == "manter"
    assert acao_usa.custo_calibrado is False
    assert math.isnan(acao_usa.custo_estimado)


def test_custo_calibrado_pelo_usuario_para_classe_us_produz_recomendacao_real():
    df = pd.DataFrame([_linha("USA", classe="us", peso=0.50), _linha("USB", classe="us", peso=0.50)])
    sinais = [_sinal("qualidade", "USA", 0.9, "metrics")]
    cfg_us = CostConfig(classe=CLASSE_US, corretagem_fixa=0.0, spread_bps_large=5.0,
                        spread_bps_small=5.0, ir_rate=0.0, isencao_mes=1e12, calibrado=True)
    resultado = recomendar(
        df, sinais, alvos={}, politica=_SEMPRE_REBALANCEAR,
        custos={CLASSE_US: cfg_us}, patrimonio_total=1_000_000.0,
        data_atual=_HOJE, sensibilidade=1.0,
    )
    acao_usa = next(a for a in resultado if a.symbol == "USA")
    assert acao_usa.acao == "aumentar"
    assert acao_usa.custo_calibrado is True
    assert acao_usa.custo_estimado > 0


# ---------------------------------------------------------------------------
# Regra 2: sinal sobre dado ausente nunca vira acao
# ---------------------------------------------------------------------------

def test_ativo_sem_nenhum_sinal_fica_indeterminado():
    df = pd.DataFrame([_linha("COM_SINAL", peso=0.5), _linha("SEM_SINAL", peso=0.5)])
    sinais = [_sinal("qualidade", "COM_SINAL", 0.8, "metrics")]
    resultado = recomendar(
        df, sinais, alvos={}, politica=_SEMPRE_REBALANCEAR,
        custos={CLASSE_B3: CostConfig.brasil_pf_default()},
        patrimonio_total=1_000_000.0, data_atual=_HOJE, sensibilidade=1.0,
    )
    sem_sinal = next(a for a in resultado if a.symbol == "SEM_SINAL")
    assert sem_sinal.acao == "indeterminado"
    assert sem_sinal.score is None
    assert sem_sinal.componentes == {}
    assert sem_sinal.analisadores == frozenset()


def test_indeterminado_nunca_e_aumentar_reduzir_ou_vender():
    # Mesmo com outros ativos se movendo bastante (o que redistribui a fatia
    # do simplex), o ativo sem sinal nunca recebe um rotulo de acao.
    df = pd.DataFrame([_linha("X", peso=0.33), _linha("Y", peso=0.33), _linha("SEM_SINAL", peso=0.34)])
    sinais = [_sinal("qualidade", "X", 1.0, "metrics"), _sinal("qualidade", "Y", -1.0, "metrics")]
    resultado = recomendar(
        df, sinais, alvos={}, politica=_SEMPRE_REBALANCEAR,
        custos={CLASSE_B3: CostConfig.brasil_pf_default()},
        patrimonio_total=1_000_000.0, data_atual=_HOJE, sensibilidade=1.0,
    )
    sem_sinal = next(a for a in resultado if a.symbol == "SEM_SINAL")
    assert sem_sinal.acao == "indeterminado"


# ---------------------------------------------------------------------------
# Sinais neutros: nao inventar recomendacao do nada
# ---------------------------------------------------------------------------

def test_todos_os_sinais_neutros_nao_recomenda_nada():
    df = pd.DataFrame([_linha("A", peso=0.5), _linha("B", peso=0.5)])
    sinais = [_sinal("qualidade", "A", 0.0, "metrics"), _sinal("qualidade", "B", 0.0, "metrics")]
    resultado = recomendar(
        df, sinais, alvos={}, politica=_SEMPRE_REBALANCEAR,
        custos={CLASSE_B3: CostConfig.brasil_pf_default()},
        patrimonio_total=1_000_000.0, data_atual=_HOJE, sensibilidade=1.0,
    )
    assert all(a.acao == "manter" for a in resultado)
    assert all(a.score == 0.0 for a in resultado)


# ---------------------------------------------------------------------------
# Politica de rebalanceamento: gate real, nao fake
# ---------------------------------------------------------------------------

def test_politica_nao_manda_rebalancear_forca_manter_mesmo_com_delta():
    df = pd.DataFrame([_linha("A", peso=0.5), _linha("B", peso=0.5)])
    sinais = [_sinal("qualidade", "A", 0.9, "metrics")]
    politica = ThresholdRebalance(banda_abs=0.99, rebal_inicial=False)  # banda absurda: nunca dispara
    resultado = recomendar(
        df, sinais, alvos={}, politica=politica,
        custos={CLASSE_B3: CostConfig.brasil_pf_default()},
        patrimonio_total=1_000_000.0, data_atual=_HOJE, ultimo_rebal=_ONTEM,
        sensibilidade=1.0,
    )
    acao_a = next(a for a in resultado if a.symbol == "A")
    assert acao_a.acao == "manter"
    # o alvo continua exposto mesmo com a execucao represada pela politica
    assert acao_a.peso_sugerido != acao_a.peso_atual


def test_politica_manda_rebalancear_libera_movimento():
    df = pd.DataFrame([_linha("A", peso=0.5), _linha("B", peso=0.5)])
    sinais = [_sinal("qualidade", "A", 0.9, "metrics")]
    politica = ThresholdRebalance(banda_abs=0.001, rebal_inicial=False)  # banda minima: dispara facil
    resultado = recomendar(
        df, sinais, alvos={}, politica=politica,
        custos={CLASSE_B3: CostConfig.brasil_pf_default()},
        patrimonio_total=1_000_000.0, data_atual=_HOJE, ultimo_rebal=_ONTEM,
        sensibilidade=1.0,
    )
    acao_a = next(a for a in resultado if a.symbol == "A")
    assert acao_a.acao == "aumentar"


# ---------------------------------------------------------------------------
# Seam real com portfolio_constraints: teto por ativo de fato limita
# ---------------------------------------------------------------------------

def test_projecao_respeita_teto_por_ativo_de_verdade():
    df = pd.DataFrame([_linha("A", peso=0.20), _linha("B", peso=0.20),
                       _linha("C", peso=0.20), _linha("D", peso=0.20), _linha("E", peso=0.20)])
    sinais = [_sinal("qualidade", "A", 1.0, "metrics")]  # tenta empurrar A para cima
    resultado = recomendar(
        df, sinais, alvos={}, politica=_SEMPRE_REBALANCEAR,
        custos={CLASSE_B3: CostConfig.brasil_pf_default()},
        patrimonio_total=1_000_000.0, data_atual=_HOJE, sensibilidade=1.0,
        cap_ativo=0.25,
    )
    acao_a = next(a for a in resultado if a.symbol == "A")
    assert acao_a.peso_sugerido <= 0.25 + 1e-9


def test_pesos_sugeridos_somam_um_apos_projecao():
    df = pd.DataFrame([_linha("A", peso=0.5), _linha("B", peso=0.3), _linha("C", peso=0.2)])
    sinais = [_sinal("qualidade", "A", 0.7, "metrics"), _sinal("qualidade", "C", -0.5, "metrics")]
    resultado = recomendar(
        df, sinais, alvos={}, politica=_SEMPRE_REBALANCEAR,
        custos={CLASSE_B3: CostConfig.brasil_pf_default()},
        patrimonio_total=1_000_000.0, data_atual=_HOJE, sensibilidade=1.0,
    )
    total = sum(a.peso_sugerido for a in resultado)
    assert total == pytest.approx(1.0, abs=1e-9)


def test_projecao_usada_e_a_funcao_real_do_modulo_de_constraints():
    # Cross-check direto: o peso_sugerido de um ativo indeterminado (pinado
    # no bruto = peso_atual) bate com o que project_capped_simplex real
    # devolveria para o mesmo vetor de entrada -- prova que o motor chama a
    # funcao de verdade, nao uma reimplementacao paralela.
    df = pd.DataFrame([_linha("A", peso=0.6), _linha("SEM_SINAL", peso=0.4)])
    sinais = [_sinal("qualidade", "A", 0.5, "metrics")]
    resultado = recomendar(
        df, sinais, alvos={}, politica=_SEMPRE_REBALANCEAR,
        custos={CLASSE_B3: CostConfig.brasil_pf_default()},
        patrimonio_total=1_000_000.0, data_atual=_HOJE, sensibilidade=1.0,
        cap_ativo=0.9,
    )
    bruto = {"A": 0.6 * (1.0 + 1.0 * 0.5), "SEM_SINAL": 0.4}
    esperado = project_capped_simplex(bruto, 0.9)
    por_symbol = {a.symbol: a.peso_sugerido for a in resultado}
    assert por_symbol["A"] == pytest.approx(esperado["A"])
    assert por_symbol["SEM_SINAL"] == pytest.approx(esperado["SEM_SINAL"])


# ---------------------------------------------------------------------------
# Teto por classe via alvos
# ---------------------------------------------------------------------------

def test_alvo_de_classe_funciona_como_teto_superior():
    df = pd.DataFrame([
        _linha("B3A", classe="b3", peso=0.45), _linha("B3B", classe="b3", peso=0.45),
        _linha("FIIA", classe="fii", peso=0.10),
    ])
    sinais = [_sinal("qualidade", "B3A", 0.9, "metrics"), _sinal("qualidade", "B3B", 0.9, "metrics")]
    alvos = {"b3": 0.5, "fii": 0.5}
    resultado = recomendar(
        df, sinais, alvos=alvos, politica=_SEMPRE_REBALANCEAR,
        custos={CLASSE_B3: CostConfig.brasil_pf_default(), CLASSE_FII: CostConfig(classe=CLASSE_FII)},
        patrimonio_total=1_000_000.0, data_atual=_HOJE, sensibilidade=1.0,
    )
    peso_b3_total = sum(a.peso_sugerido for a in resultado if a.symbol.startswith("B3"))
    assert peso_b3_total <= 0.5 + 1e-6


# ---------------------------------------------------------------------------
# vender vs reduzir
# ---------------------------------------------------------------------------

def test_score_muito_negativo_com_peso_projetado_quase_zero_vira_vender():
    df = pd.DataFrame([_linha("RUIM", peso=0.05), _linha("BOM", peso=0.95)])
    sinais = [_sinal("qualidade", "RUIM", -1.0, "metrics")]
    resultado = recomendar(
        df, sinais, alvos={}, politica=_SEMPRE_REBALANCEAR,
        custos={CLASSE_B3: CostConfig.brasil_pf_default()},
        patrimonio_total=1_000_000.0, data_atual=_HOJE, sensibilidade=1.0,
    )
    acao_ruim = next(a for a in resultado if a.symbol == "RUIM")
    assert acao_ruim.acao == "vender"
    assert acao_ruim.peso_sugerido < 0.001


# ---------------------------------------------------------------------------
# Determinismo: mesma entrada, ordem embaralhada -> mesma saida
# ---------------------------------------------------------------------------

def test_determinismo_sob_ordem_embaralhada():
    linhas = [_linha("A", peso=0.30), _linha("B", peso=0.25), _linha("C", peso=0.20),
             _linha("D", peso=0.15), _linha("E", peso=0.10)]
    sinais = [
        _sinal("qualidade", "A", 0.8, "metrics"),
        _sinal("valuation", "A", -0.3, "metrics"),
        _sinal("estouro_limite", "B", -0.6, "concentration"),
        _sinal("redundancia", "C", -0.4, "correlation"),
        _sinal("qualidade", "D", 0.5, "metrics"),
        # E fica sem sinal -> indeterminado
    ]

    kwargs = dict(
        alvos={"b3": 1.0}, politica=_SEMPRE_REBALANCEAR,
        custos={CLASSE_B3: CostConfig.brasil_pf_default()},
        patrimonio_total=1_000_000.0, data_atual=_HOJE, sensibilidade=1.0,
        cap_ativo=0.5,
    )

    base = recomendar(pd.DataFrame(linhas), list(sinais), **kwargs)

    rng = random.Random(42)
    for _ in range(5):
        linhas_embaralhadas = linhas[:]
        sinais_embaralhados = sinais[:]
        rng.shuffle(linhas_embaralhadas)
        rng.shuffle(sinais_embaralhados)
        variante = recomendar(pd.DataFrame(linhas_embaralhadas), sinais_embaralhados, **kwargs)
        assert variante == base


# ---------------------------------------------------------------------------
# Quadro vazio
# ---------------------------------------------------------------------------

def test_quadro_vazio_devolve_lista_vazia():
    assert recomendar(pd.DataFrame(), [], alvos={}, politica=_SEMPRE_REBALANCEAR,
                      custos={}, patrimonio_total=1.0, data_atual=_HOJE) == []
    assert recomendar(None, [], alvos={}, politica=_SEMPRE_REBALANCEAR,
                      custos={}, patrimonio_total=1.0, data_atual=_HOJE) == []


# ---------------------------------------------------------------------------
# Vocabulario de acao e Acao e frozen (imutavel)
# ---------------------------------------------------------------------------

def test_acao_e_dataclass_imutavel():
    df = pd.DataFrame([_linha("A", peso=1.0)])
    resultado = recomendar(
        df, [], alvos={}, politica=_SEMPRE_REBALANCEAR,
        custos={CLASSE_B3: CostConfig.brasil_pf_default()},
        patrimonio_total=1_000_000.0, data_atual=_HOJE,
    )
    with pytest.raises(Exception):
        resultado[0].acao = "aumentar"


def test_todas_as_acoes_pertencem_ao_vocabulario_valido():
    from core.global_portfolio.advisor import ACOES_VALIDAS
    df = pd.DataFrame([_linha("A", peso=0.5), _linha("B", peso=0.5)])
    sinais = [_sinal("qualidade", "A", 0.9, "metrics")]
    resultado = recomendar(
        df, sinais, alvos={}, politica=_SEMPRE_REBALANCEAR,
        custos={CLASSE_B3: CostConfig.brasil_pf_default()},
        patrimonio_total=1_000_000.0, data_atual=_HOJE, sensibilidade=1.0,
    )
    assert all(a.acao in ACOES_VALIDAS for a in resultado)
