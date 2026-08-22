"""Teste no universo + encolhimento hierárquico (puro, auditoria §16 peça c)."""
from __future__ import annotations

import numpy as np
import pytest

from core.b3_evidence import A_FAVOR, CONTRA, INCONCLUSIVO
from core.b3_pooled_evidence import (
    SegmentSample,
    pooled_yearly_ics,
    shrink_segment_estimates,
    universe_evidence,
)

# ── IC agrupado no universo ──────────────────────────────────────────────────

def test_ic_do_universo_captura_ordenacao_perfeita():
    # score maior → retorno maior, em dois anos
    pares = [(2020, i, i * 0.01) for i in range(10)]
    pares += [(2021, i, i * 0.02) for i in range(10)]
    ics = pooled_yearly_ics(pares)
    assert set(ics) == {2020, 2021}
    assert ics[2020] == pytest.approx(1.0)


def test_ic_do_universo_detecta_ordenacao_invertida():
    pares = [(2020, i, -i * 0.01) for i in range(10)]
    assert pooled_yearly_ics(pares)[2020] == pytest.approx(-1.0)


def test_ano_com_poucas_empresas_e_ignorado_nao_inventado():
    pares = [(2020, i, i * 0.01) for i in range(3)]      # < 5 ativos
    assert pooled_yearly_ics(pares) == {}


def test_pares_invalidos_sao_descartados_sem_quebrar():
    pares = [(2020, 1, 0.1), ("x", 2, 0.2), (2020, None, 0.3),
             (2020, 3, float("nan")), (2020, 4, 0.4), (2020, 5, 0.5),
             (2020, 6, 0.6), (2020, 7, 0.7), (2020, 8, 0.8)]
    ics = pooled_yearly_ics(pares)
    assert 2020 in ics and np.isfinite(ics[2020])


def test_amplitude_do_universo_supera_a_de_segmento():
    """O ponto central da peça (c): 300 empresas num teste, não 3 em 78."""
    pares = [(2020, i, i * 0.01) for i in range(300)]
    ics = pooled_yearly_ics(pares)
    assert ics[2020] == pytest.approx(1.0)   # calculável — no segmento não seria


# ── veredito do universo ─────────────────────────────────────────────────────

def test_universo_com_sinal_consistente_e_evidencia_a_favor():
    ev = universe_evidence({2018: .12, 2019: .10, 2020: .14, 2021: .11,
                            2022: .13}, n_medio_ativos=320)
    assert ev.estado == A_FAVOR
    assert ev.p_value is not None and ev.p_value < 0.10
    assert "amplitude real" in ev.explicacao


def test_universo_anti_preditivo_e_evidencia_contra():
    ev = universe_evidence({2020: -.20, 2021: -.15, 2022: -.18})
    assert ev.estado == CONTRA
    assert "ao contrário" in ev.explicacao


def test_universo_ruidoso_e_inconclusivo_com_mde_declarado():
    ev = universe_evidence({2020: .10, 2021: -.08, 2022: .04})
    assert ev.estado == INCONCLUSIVO
    assert ev.efeito_minimo_detectavel is not None
    assert "Efeito mínimo detectável" in ev.explicacao


def test_universo_sem_anos_nao_inventa_veredito():
    ev = universe_evidence({})
    assert ev.estado == INCONCLUSIVO and ev.anos == 0
    assert not np.isfinite(ev.ic_medio)


# ── encolhimento hierárquico ─────────────────────────────────────────────────

def test_segmento_pequeno_e_encolhido_mais_que_o_grande():
    """3 empresas com IC 0,40 é quase certamente ruído; 40 com 0,15 não é."""
    amostras = [
        SegmentSample("pequeno", (0.40, 0.38), n_assets=3),
        SegmentSample("grande", (0.15, 0.14), n_assets=40),
    ]
    por_chave = {e.key: e for e in shrink_segment_estimates(amostras)}
    # o pequeno perde mais do seu valor bruto na direção da média
    desvio_pequeno = abs(por_chave["pequeno"].ic_encolhido - 0.39)
    desvio_grande = abs(por_chave["grande"].ic_encolhido - 0.145)
    assert desvio_pequeno > desvio_grande


def test_segmento_sem_observacao_recebe_a_media_do_universo():
    """Sem dado, a melhor inferência é o universo — não uma reprovação."""
    amostras = [SegmentSample("mudo", (), n_assets=2),
                SegmentSample("medido", (0.10, 0.12), n_assets=20)]
    por_chave = {e.key: e for e in shrink_segment_estimates(
        amostras, universe_mean=0.08)}
    mudo = por_chave["mudo"]
    assert mudo.ic_bruto is None
    assert mudo.ic_encolhido == pytest.approx(0.08)
    assert mudo.peso_proprio == 0.0
    assert "adota a estimativa do universo" in mudo.explicacao


def test_sem_dispersao_entre_segmentos_tudo_colapsa_para_a_media():
    """Se os segmentos não se distinguem do ruído, τ²→0 e todos viram a média."""
    amostras = [SegmentSample(f"s{i}", (0.10, 0.10), n_assets=10)
                for i in range(5)]
    estimativas = shrink_segment_estimates(amostras, universe_mean=0.10)
    assert all(e.peso_proprio == pytest.approx(0.0) for e in estimativas)
    assert all(e.ic_encolhido == pytest.approx(0.10) for e in estimativas)


def test_encolhido_fica_entre_o_bruto_e_a_media_do_universo():
    amostras = [
        SegmentSample("a", (0.50, 0.40, 0.45), n_assets=8),
        SegmentSample("b", (-0.20, -0.10, -0.15), n_assets=8),
        SegmentSample("c", (0.05, 0.10, 0.02), n_assets=8),
    ]
    for e in shrink_segment_estimates(amostras, universe_mean=0.10):
        assert min(e.ic_bruto, 0.10) - 1e-9 <= e.ic_encolhido <= max(e.ic_bruto, 0.10) + 1e-9
        assert 0.0 <= e.peso_proprio <= 1.0


def test_lista_vazia_nao_quebra():
    assert shrink_segment_estimates([]) == []


def test_explicacao_declara_o_peso_proprio():
    amostras = [SegmentSample("x", (0.30, 0.20), n_assets=6),
                SegmentSample("y", (0.05, 0.02), n_assets=30)]
    estimativa = shrink_segment_estimates(amostras)[0]
    assert "peso próprio" in estimativa.explicacao


# ── integração com a interface ───────────────────────────────────────────────

def test_secao_do_universo_renderiza_e_encolhe_segmentos():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string("""
import views.portfolio_b3 as view

# dois segmentos: um minusculo (ruido) e um grande (sinal real)
pares_peq = [(2020, i, i * 0.01) for i in range(3)]
pares_gra = [(2020, i, i * 0.01) for i in range(40)]
pares_gra += [(2021, i, i * 0.01) for i in range(40)]
resultados = [
    {"setor": "S", "segmento": "Pequeno", "ic_pairs": pares_peq,
     "rank_ic_values": [], "n_empresas_medio": 3.0},
    {"setor": "S", "segmento": "Grande", "ic_pairs": pares_gra,
     "rank_ic_values": [0.9, 0.85], "n_empresas_medio": 40.0},
]
view._render_evidencia_universo(resultados)
""").run(timeout=60)

    assert not app.exception
    rendered = "\n".join(item.value for item in app.markdown)
    assert "Evidência no universo" in rendered
    assert any("empirical Bayes" in exp.label for exp in app.expander)
    captions = "\n".join(item.value for item in app.caption)
    assert "mediana é de 3 empresas" in captions


def test_secao_do_universo_nao_renderiza_sem_pares():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string("""
import views.portfolio_b3 as view
view._render_evidencia_universo([{"setor": "S", "segmento": "X"}])
""").run(timeout=60)
    assert not app.exception
    assert not any("Evidência no universo" in item.value for item in app.markdown)
