"""O limiar de movimento relevante, por classe e por volatilidade.

O que estes testes protegem é uma afirmação, não uma função: *o mesmo movimento
não significa a mesma coisa em ativos diferentes*. O código anterior dizia que
significava -- 3% para tudo -- e por isso o primeiro teste do arquivo é a
comparação entre um FII e uma ação volátil sobre a mesma pergunta.
"""
from __future__ import annotations

import pytest

from core.calibracao import limiar as lim

# Séries sintéticas com desvio conhecido. Alternar dois valores em torno de zero
# dá desvio ~= amplitude, o que torna a asserção legível sem depender de numpy.
FII_CALMO = [0.006, -0.006] * 80          # ~0,6% ao dia
ACAO_AGITADA = [0.045, -0.045] * 80       # ~4,5% ao dia


def test_o_mesmo_movimento_nao_e_relevante_nos_dois_ativos():
    """O defeito que este módulo existe para corrigir, medido nos dois lados.

    Com o limiar único de 3%: no FII isso era ~5 desvios (o motor nunca falaria)
    e na ação volátil era menos de um desvio (o motor falaria todo dia).
    """
    fii = lim.calcular(classe=lim.CLASSE_FII, retornos_diarios=FII_CALMO)
    acao = lim.calcular(classe=lim.CLASSE_ACAO_B3, retornos_diarios=ACAO_AGITADA)

    assert fii.valor < 0.03 < acao.valor
    assert fii.estimado and acao.estimado
    # E os dois continuam sendo "o mesmo número de desvios", que é a definição.
    assert fii.valor == pytest.approx(fii.k * fii.sigma_diario, rel=1e-6)


def test_sem_historico_sai_o_prior_da_classe_marcado_como_nao_estimado():
    resultado = lim.calcular(classe=lim.CLASSE_FII, retornos_diarios=[0.01] * 5)
    assert resultado.estimado is False
    assert resultado.origem == lim.ORIGEM_PRIOR
    assert resultado.sigma_diario is None
    assert any("volatilidade nao estimada" in m for m in resultado.limitacoes)


def test_serie_constante_nao_vira_sigma_zero():
    """Papel que não negocia tem série parada, e isso não é estabilidade.

    Sigma zero produziria limiar zero e qualquer variação viraria movimento
    relevante. ``desvio_diario`` devolve ``None`` -- não medido -- e o limiar
    cai no prior.
    """
    sigma, n = lim.desvio_diario([0.0] * 200)
    assert sigma is None and n == 200

    resultado = lim.calcular(classe=lim.CLASSE_ACAO_B3,
                             retornos_diarios=[0.0] * 200)
    assert resultado.estimado is False


def test_piso_impede_ruido_de_papel_ilíquido_virar_evento():
    """Sigma minúsculo por falta de negócio, não por estabilidade real."""
    resultado = lim.calcular(classe=lim.CLASSE_ACAO_B3, sigma_diario=0.002,
                             n_observacoes=252)
    parametros = lim.PARAMETROS[lim.CLASSE_ACAO_B3]
    assert resultado.valor == parametros.piso
    assert resultado.origem == lim.ORIGEM_PISO
    assert any("piso da classe" in m for m in resultado.limitacoes)


def test_teto_impede_o_motor_de_calar_no_ativo_em_colapso():
    resultado = lim.calcular(classe=lim.CLASSE_ACAO_B3, sigma_diario=0.15,
                             n_observacoes=252)
    parametros = lim.PARAMETROS[lim.CLASSE_ACAO_B3]
    assert resultado.valor == parametros.teto
    assert resultado.origem == lim.ORIGEM_TETO
    assert any("calaria o motor" in m for m in resultado.limitacoes)


def test_horizonte_maior_exige_movimento_maior_e_declara_a_aproximacao():
    um = lim.calcular(classe=lim.CLASSE_ACAO_US, sigma_diario=0.02,
                      n_observacoes=252, horizonte_pregoes=1)
    cinco = lim.calcular(classe=lim.CLASSE_ACAO_US, sigma_diario=0.02,
                         n_observacoes=252, horizonte_pregoes=5)
    assert cinco.valor > um.valor
    assert lim.AVISO_RAIZ in cinco.limitacoes
    assert lim.AVISO_RAIZ not in um.limitacoes


def test_classe_desconhecida_nao_reconhecida_declara_a_limitacao():
    resultado = lim.calcular(classe="cripto", sigma_diario=0.03,
                             n_observacoes=252)
    assert resultado.classe == lim.CLASSE_DESCONHECIDA
    assert any("nao reconhecida" in m for m in resultado.limitacoes)


@pytest.mark.parametrize("simbolo,mercado,esperado", [
    ("PETR4", None, lim.CLASSE_ACAO_B3),
    ("AAPL", None, lim.CLASSE_ACAO_US),
    ("HGLG11", None, lim.CLASSE_DESCONHECIDA),   # 11 é unit e é FII
    ("HGLG11", "fii", lim.CLASSE_FII),
    ("PETR4", "b3", lim.CLASSE_ACAO_B3),
    ("", None, lim.CLASSE_DESCONHECIDA),
])
def test_classificar(simbolo, mercado, esperado):
    """O ``11`` ambíguo devolve desconhecida em vez de chutar com confiança."""
    assert lim.classificar(simbolo, mercado=mercado) == esperado


def test_conversao_para_pontos_acontece_uma_vez_so():
    resultado = lim.calcular(classe=lim.CLASSE_FII, sigma_diario=0.01,
                             n_observacoes=252)
    assert resultado.em_pontos == pytest.approx(resultado.valor * 100)


def test_ponte_usa_o_limiar_e_declara_quando_nao_tem():
    """A travessia até ``core.noticias.impacto`` carrega a procedência junto."""
    from core.memoria_mercado import amostra as am
    from core.memoria_mercado import ponte_noticias as pn
    from tests.apoio_memoria import painel

    amostra = am.resumir(painel(30, reacao=-0.06, dispersao=0.03),
                         tipo_evento="resultado", horizonte=20)
    calculado = lim.calcular(classe=lim.CLASSE_FII, retornos_diarios=FII_CALMO)

    com = pn.para_base_historica(amostra, limiar=calculado)
    assert com.limiar_relevante == pytest.approx(calculado.em_pontos)

    sem = pn.para_base_historica(amostra)
    assert sem.limiar_relevante == pytest.approx(
        pn.LIMIAR_RELEVANTE_PADRAO * 100)
    assert any("prior da classe desconhecida" in m
               for m in pn.descrever(amostra, sem))
