"""Estado de evidência estatística — inconclusivo ≠ reprovado (puro)."""
from __future__ import annotations

import numpy as np
import pytest

from core.b3_evidence import (
    A_FAVOR, CONTRA, INCONCLUSIVO, SEM_AMPLITUDE, SEM_SIGNIFICANCIA,
    classify_evidence, evidence_label, minimum_detectable_effect,
)


def test_sem_amplitude_e_inconclusivo_e_nao_bloqueia():
    """O caso da B3: segmento com 3 empresas nem chega a ter Rank-IC."""
    v = classify_evidence(ic_values=[])
    assert v.estado == INCONCLUSIVO and v.motivo == SEM_AMPLITUDE
    assert v.bloqueante is False
    assert "não é evidência contra" in v.explicacao


def test_um_ano_medido_ainda_e_sem_amplitude():
    v = classify_evidence(ic_values=[0.30], min_anos=2)
    assert v.motivo == SEM_AMPLITUDE and v.anos_medidos == 1
    assert v.bloqueante is False


def test_sinal_anti_preditivo_e_evidencia_contra_e_bloqueia():
    """Único caso em que a estatística tem base para reprovar."""
    v = classify_evidence(ic_values=[-0.20, -0.15, -0.18], p_value=0.02)
    assert v.estado == CONTRA
    assert v.bloqueante is True
    assert "ordenou ao contrário" in v.explicacao


def test_sinal_positivo_e_significante_e_evidencia_a_favor():
    v = classify_evidence(ic_values=[0.22, 0.18, 0.25, 0.20], p_value=0.01)
    assert v.estado == A_FAVOR and v.bloqueante is False


def test_positivo_sem_significancia_e_inconclusivo_nao_reprovado():
    """O erro que a auditoria §16 apontou: não rejeitar H0 vira 'reprovado'."""
    v = classify_evidence(ic_values=[0.10, -0.02, 0.14], p_value=0.30)
    assert v.estado == INCONCLUSIVO and v.motivo == SEM_SIGNIFICANCIA
    assert v.bloqueante is False
    assert "não prova ausência de habilidade" in v.explicacao


def test_mde_expoe_o_poder_do_teste():
    """Amostra pequena só enxerga efeito grande — é o dado que faltava."""
    poucos = minimum_detectable_effect([0.05, -0.05, 0.10])
    muitos = minimum_detectable_effect([0.05, -0.05, 0.10] * 6)
    assert poucos is not None and muitos is not None
    assert poucos > muitos           # menos dados ⇒ exige efeito maior
    assert poucos > 0.05             # com 3 anos, IC pequeno é invisível


def test_mde_indefinido_sem_dispersao_ou_amostra():
    assert minimum_detectable_effect([]) is None
    assert minimum_detectable_effect([0.2]) is None
    assert minimum_detectable_effect([0.2, 0.2, 0.2]) is None   # desvio zero


def test_mde_aparece_na_explicacao_do_inconclusivo():
    v = classify_evidence(ic_values=[0.02, -0.01, 0.05], p_value=0.44)
    assert v.efeito_minimo_detectavel is not None
    assert "seria detectável" in v.explicacao


def test_rotulos_distinguem_os_dois_tipos_de_inconclusao():
    sem_amplitude = classify_evidence(ic_values=[])
    sem_signif = classify_evidence(ic_values=[0.05, 0.01, 0.03], p_value=0.40)
    assert evidence_label(sem_amplitude) == "Inconclusivo (sem amplitude)"
    assert evidence_label(sem_signif) == "Inconclusivo (sem significância)"
    assert evidence_label(classify_evidence(
        ic_values=[-0.3, -0.2], p_value=0.03)) == "Evidência contra"


def test_valores_nao_finitos_sao_descartados():
    v = classify_evidence(ic_values=[0.2, float("nan"), np.inf, 0.3],
                          p_value=0.05)
    assert v.anos_medidos == 2
    assert v.estado == A_FAVOR


def test_limiar_de_evidencia_contra_e_parametrizavel():
    valores = [-0.03, -0.02, -0.04]
    assert classify_evidence(ic_values=valores).estado == INCONCLUSIVO
    assert classify_evidence(ic_values=valores, ic_contra=-0.01).estado == CONTRA


def test_apenas_evidencia_contra_bloqueia():
    casos = [
        classify_evidence(ic_values=[]),
        classify_evidence(ic_values=[0.1, 0.05], p_value=0.4),
        classify_evidence(ic_values=[0.3, 0.25], p_value=0.01),
    ]
    assert [c.bloqueante for c in casos] == [False, False, False]
    assert classify_evidence(ic_values=[-0.3, -0.4], p_value=0.01).bloqueante is True
