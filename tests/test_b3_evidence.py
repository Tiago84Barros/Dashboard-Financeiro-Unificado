"""Estado de evidência estatística — inconclusivo ≠ reprovado (puro)."""
from __future__ import annotations

import numpy as np

from core.b3_evidence import (
    A_FAVOR,
    CONTRA,
    INCONCLUSIVO,
    SEM_AMPLITUDE,
    SEM_SIGNIFICANCIA,
    classify_evidence,
    evidence_label,
    minimum_detectable_effect,
    sinal_significante,
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


# ── Rodada de correção 4 da Task 6 (A-1): o portão do sinal ──


def test_portao_do_sinal_nao_aprova_sem_p_valor_nem_o_trata_como_contra():
    """A-1: `views/portfolio_b3.py` mantinha a QUARTA cópia do teste t —
    a única que DECIDE — com a guarda absoluta (`sd > 0`) e um
    `p_value_ic = 0.0` literal: dois anos com Rank-IC idêntico e positivo
    davam dispersão zero e a tela gravava `p = 0.0`, `t = inf`, isto é,
    certeza absoluta onde não há grau de liberdade para afirmar nada. Sob
    postos independentes isso alcança 2,7% dos segmentos de 5 ativos.

    Sem dispersão real não há p-valor, e os DOIS lados da ausência foram
    medidos antes da escolha: num modo que exige prova positiva ela não
    aprova (`sinal_significante is False`), e também não é evidência
    contra (`classify_evidence` devolve inconclusivo, não bloqueante)."""
    # importado aqui dentro de proposito: no topo, o `test*` do nome faz o
    # pytest COLETAR `teste_t_unilateral` como se fosse um teste.
    from core.b3_evidence import teste_t_unilateral

    identicos = [0.30, 0.30]
    assert teste_t_unilateral(identicos) == (None, None)
    assert sinal_significante(None) is False

    veredito = classify_evidence(ic_values=identicos, p_value=None)
    assert veredito.estado == INCONCLUSIVO and veredito.bloqueante is False
    assert veredito.estado != CONTRA


def test_sinal_significante_e_o_mesmo_alpha_de_classify_evidence():
    """O portão aprova significância DEMONSTRADA e nada além dela: o
    limiar é o mesmo `alpha` que classifica o estado, e valores que não
    são p-valor (`None`, `nan`) não aprovam por descuido de comparação —
    `nan < 0.10` é False, mas `p >= 0.10` (a forma antiga do portão)
    também era False para `nan`, e isso APROVAVA."""
    assert sinal_significante(0.0) is True
    assert sinal_significante(0.099) is True
    assert sinal_significante(0.10) is False
    assert sinal_significante(0.5) is False
    assert sinal_significante(1.0) is False
    assert sinal_significante(float("nan")) is False
    assert sinal_significante(0.04, alpha=0.01) is False

    # e concorda com o estado publicado ao lado: o que o portão aprova é
    # exatamente o que `classify_evidence` chama de evidência a favor
    for p in (0.001, 0.05, 0.099, 0.10, 0.3, 0.9):
        estado = classify_evidence(ic_values=[0.3, 0.25, 0.28], p_value=p).estado
        assert sinal_significante(p) is (estado == A_FAVOR), p
