"""As medidas que decidem se o motor sai do laboratório.

Metade destes testes não verifica um cálculo: verifica uma *recusa*. Precisão
que devolve ``None`` em vez de 1,0, comparação sem lado que devolve ``None`` em
vez de "perdeu", recuperação que devolve ``None`` em vez do comprimento da
série. São os pontos em que um número plausível encobriria a ausência de
evidência, que é o defeito que este repositório mais repetiu.
"""
from __future__ import annotations

import pytest

from core.calibracao import metricas as m


# ── Detecção ────────────────────────────────────────────────────────────────
def test_motor_mudo_tem_precisao_indefinida_e_nao_perfeita():
    """Nunca disparar não é acertar sempre.

    Se ``precisao`` devolvesse 1,0 aqui, o portão de promoção leria silêncio
    como excelência e promoveria um motor que nunca fala.
    """
    c = m.avaliar_deteccao([(False, True), (False, False), (False, True)])
    assert c.precisao is None
    assert c.f1 is None
    assert c.recall == 0.0            # houve dois casos e ele perdeu os dois
    assert c.taxa_nao_deteccao == 1.0


def test_falso_alarme_e_nao_deteccao_saem_separados():
    """Custos assimétricos não podem ser somados numa métrica só."""
    c = m.avaliar_deteccao(
        [(True, True)] * 8 + [(True, False)] * 2
        + [(False, True)] * 4 + [(False, False)] * 86)
    assert c.total == 100
    assert c.precisao == pytest.approx(0.8)
    assert c.recall == pytest.approx(8 / 12)
    assert c.taxa_falso_alarme == pytest.approx(2 / 88)
    assert c.taxa_nao_deteccao == pytest.approx(4 / 12)


def test_caso_nao_medido_nao_engorda_o_verdadeiro_negativo():
    """``None`` sai da conta; empurrá-lo para o negativo inflaria a precisão."""
    com_nulo = m.avaliar_deteccao([(True, True), (None, False), (True, None)])
    assert com_nulo.total == 1
    assert com_nulo.verdadeiro_negativo == 0


# ── Probabilidade ───────────────────────────────────────────────────────────
def _pares(prob: float, n: int, fracao_ocorrida: float):
    ocorreram = round(n * fracao_ocorrida)
    return [(prob, True)] * ocorreram + [(prob, False)] * (n - ocorreram)


def test_setenta_por_cento_precisa_ocorrer_em_torno_de_setenta_por_cento():
    """A frase da instrução, virada em número."""
    boa = m.avaliar_probabilidade(_pares(0.7, 100, 0.70) + _pares(0.1, 100, 0.10))
    assert boa.calibrada is True
    assert boa.erro_calibracao == pytest.approx(0.0, abs=0.01)

    # O mesmo motor prometendo 70% e entregando 30%.
    ruim = m.avaliar_probabilidade(_pares(0.7, 100, 0.30) + _pares(0.1, 100, 0.10))
    assert ruim.calibrada is False
    assert ruim.erro_calibracao > m.TOLERANCIA_CALIBRACAO


def test_amostra_pequena_nao_e_descalibracao_e_sim_ausencia_de_medida():
    """A lei do projeto: ``ok=None`` é "não medido", nunca ``False``."""
    poucos = m.avaliar_probabilidade(_pares(0.7, 5, 0.20))
    assert poucos.calibrada is None
    assert poucos.erro_calibracao is None
    assert poucos.brier is not None          # o Brier ainda é calculável
    assert all(not b.suficiente for b in poucos.baldes if b.n)


def test_baldes_publicam_o_n_para_quem_quiser_desconfiar():
    resultado = m.avaliar_probabilidade(_pares(0.9, 30, 0.9))
    ocupados = [b for b in resultado.baldes if b.n]
    assert len(ocupados) == 1 and ocupados[0].n == 30
    assert ocupados[0].suficiente is True


# ── Magnitude, direção e faixa ──────────────────────────────────────────────
def test_magnitude_sem_referencia_nao_declara_ganho():
    sozinho = m.avaliar_magnitude([(0.05, 0.04), (0.03, 0.05)])
    assert sozinho.mae == pytest.approx(0.015)
    assert sozinho.ganho_sobre_referencia is None


def test_magnitude_com_referencia_mede_quanto_do_erro_o_modelo_removeu():
    resultado = m.avaliar_magnitude(
        [(0.05, 0.04), (0.03, 0.04)],                 # erro 0,01 sempre
        referencia=[(0.0, 0.04), (0.0, 0.04)])        # referência ingênua: zero
    assert resultado.ganho_sobre_referencia == pytest.approx(0.75)


def test_vies_aparece_separado_do_erro_absoluto():
    """Errar sempre para cima e errar para os dois lados dão o mesmo MAE."""
    otimista = m.avaliar_magnitude([(0.06, 0.04), (0.06, 0.04)])
    neutro = m.avaliar_magnitude([(0.06, 0.04), (0.02, 0.04)])
    assert otimista.mae == neutro.mae == pytest.approx(0.02)
    assert otimista.vies == pytest.approx(0.02)
    assert neutro.vies == pytest.approx(0.0)


def test_estimar_zero_nao_conta_como_acerto_de_direcao():
    """Não ter opinião não é acertar; vai para ``sem_direcao``."""
    resultado = m.avaliar_direcao([(0.0, 0.0), (0.0, 0.05), (0.03, 0.02)])
    assert resultado["n"] == 1
    assert resultado["sem_direcao"] == 2
    assert resultado["acerto"] == 1.0


def test_faixa_estreita_e_faixa_larga_aparecem_com_sinais_opostos():
    estreita = m.avaliar_faixa([(-0.01, 0.01, 0.05)] * 10, alvo=0.80)
    larga = m.avaliar_faixa([(-0.90, 0.90, 0.05)] * 10, alvo=0.80)
    assert estreita["cobertura"] == 0.0 and estreita["desvio"] < 0
    assert larga["cobertura"] == 1.0 and larga["desvio"] > 0


# ── Agir contra não agir ────────────────────────────────────────────────────
def test_serie_que_nao_recupera_tem_recuperacao_indefinida():
    """``None`` e não o comprimento da série: quem não voltou, não voltou."""
    caiu = m.avaliar_politica([100.0, 120.0, 60.0, 70.0])
    assert caiu.drawdown == pytest.approx(-0.5)
    assert caiu.recuperacao_pregoes is None

    voltou = m.avaliar_politica([100.0, 120.0, 60.0, 125.0])
    assert voltou.recuperacao_pregoes == 2


def test_girar_ganha_no_bruto_e_perde_no_liquido():
    """Por isso a comparação é líquida de custo, e não bruta."""
    agir = m.avaliar_politica([100.0, 108.0], turnover=2.0, custo_por_giro=0.02)
    parado = m.avaliar_politica([100.0, 106.0])
    resultado = m.comparar(agir, parado)
    assert resultado["retorno_bruto"] > 0
    assert resultado["retorno_liquido"] < 0
    assert resultado["melhor"] == "nao_agir"
    assert resultado["turnover_extra"] == pytest.approx(2.0)


def test_comparacao_com_lado_faltando_nao_declara_vencedor():
    vazio = m.avaliar_politica([])
    parado = m.avaliar_politica([100.0, 106.0])
    assert m.comparar(vazio, parado)["melhor"] is None


# ── Estabilidade ────────────────────────────────────────────────────────────
def test_desempenho_de_um_segmento_so_e_marcado_como_concentrado():
    """A condição "funcionou apenas em um período" da instrução."""
    concentrado = m.Estabilidade({"2020": 0.30, "2021": -0.02, "2022": -0.01})
    espalhado = m.Estabilidade({"2020": 0.05, "2021": 0.04, "2022": -0.01})
    assert concentrado.concentrado is True
    assert espalhado.concentrado is False
    assert concentrado.amplitude == pytest.approx(0.32)


def test_um_segmento_medido_nao_permite_veredito():
    assert m.Estabilidade({"2020": 0.3, "2021": None}).concentrado is None
    assert m.Estabilidade({"2020": 0.3, "2021": None}).amplitude is None
