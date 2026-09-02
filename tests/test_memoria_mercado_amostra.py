"""Amostra histórica: tamanho, estatísticas, persistência e reversão.

Cenários pedidos cobertos aqui: **amostra suficiente e insuficiente**, **evento
sem equivalente histórico**, **reversão**, **impacto persistente** e a mistura
com **benchmark ausente** (que faz a amostra cair para retorno bruto e dizer
que caiu).
"""
from __future__ import annotations

from core.memoria_mercado import amostra as am
from core.memoria_mercado.retornos import (
    PERSISTENTE,
    REVERSAO,
    REVERSAO_PARCIAL,
)
from tests.apoio_memoria import dias_uteis, evento, indice_plano, painel


def resumo(eventos, horizonte=20, tipo="resultado"):
    return am.resumir(eventos, tipo_evento=tipo, horizonte=horizonte)


# ── tamanho da amostra ────────────────────────────────────────────────────────

def test_evento_sem_equivalente_historico_nao_publica_nada():
    """Cenário pedido. Amostra vazia não vira faixa nem estatística zerada."""
    a = resumo([])
    assert a.n_eventos == 0
    assert a.principal is None
    assert not a.publicavel
    assert not a.robusta
    assert any("nenhum evento historico comparavel" in x for x in a.limitacoes)


def test_amostra_insuficiente_fica_abaixo_do_piso_e_diz_o_tamanho():
    a = resumo(painel(5))
    assert a.n_eventos == 5
    assert not a.publicavel
    assert not a.robusta
    # `experimental` marca a faixa do meio -- publicavel mas nao robusta. Abaixo
    # do piso de publicacao nao ha faixa nenhuma para marcar, e quem carrega o
    # rotulo dali para frente e a `Estimativa`.
    assert not a.experimental
    assert any(f"abaixo do minimo de {am.N_MINIMO_EXPERIMENTAL}" in x
               for x in a.limitacoes)


def test_amostra_suficiente_publica_marcada_como_experimental():
    a = resumo(painel(am.N_MINIMO_EXPERIMENTAL))
    assert a.n_eventos == am.N_MINIMO_EXPERIMENTAL
    assert a.publicavel
    assert a.experimental and not a.robusta
    assert any(f"abaixo do piso robusto de {am.N_MINIMO_ROBUSTO}" in x
               for x in a.limitacoes)


def test_amostra_robusta_deixa_de_ser_experimental():
    a = resumo(painel(am.N_MINIMO_ROBUSTO))
    assert a.robusta and not a.experimental
    assert not any("experimental" in x for x in a.limitacoes)


def test_eventos_sem_o_horizonte_medido_ficam_de_fora_e_a_exclusao_e_declarada():
    """`memoria: foto-truncada-vira-evidencia`: um evento recente sem 60
    pregões de futuro não é uma reação nula em 60 pregões."""
    completos = painel(10)
    dias_curtos = dias_uteis(232)
    truncado = evento("NOVO", reacao=-0.06, dias=dias_curtos, offset=200,
                      indice=indice_plano(dias_curtos))

    a = resumo(completos + [truncado], horizonte=60)
    assert a.n_eventos == 10
    assert "NOVO" not in a.simbolos
    assert any("1 de 11 eventos sem o horizonte de 60 pregoes" in x
               for x in a.limitacoes)


# ── estatísticas ──────────────────────────────────────────────────────────────

def test_estatisticas_cobrem_media_mediana_desvio_percentis_e_intervalo():
    a = resumo(painel(20, reacao=-0.06, dispersao=0.05))
    st = a.principal
    assert st.n == 20
    assert st.minimo <= st.p10 <= st.p25 <= st.mediana <= st.p75 <= st.p90 <= st.maximo
    assert st.desvio > 0
    assert st.intervalo_historico == (st.minimo, st.maximo)
    assert -0.10 < st.mediana < -0.02

    # Volatilidade, volume, drawdown e tempos também saem resumidos.
    assert a.volatilidade is not None and a.volatilidade.n == 20
    assert a.razao_volume is not None
    assert a.drawdown is not None and a.drawdown.mediana < 0
    assert a.pregoes_ate_o_pior is not None
    assert a.periodo is not None and a.periodo[0] < a.periodo[1]


def test_probabilidade_de_movimento_relevante_conta_o_que_passou_do_limiar():
    a = resumo(painel(20, reacao=-0.06, dispersao=0.01))
    # Reações concentradas perto de -6%: praticamente todas passam de 3%.
    assert a.prob_movimento_relevante(0.03) > 0.9
    # E praticamente nenhuma passa de 30%.
    assert a.prob_movimento_relevante(0.30) == 0.0
    assert a.fracao_negativa == 1.0


def test_sem_benchmark_a_amostra_cai_para_retorno_bruto_e_avisa():
    """Cenário pedido combinado: benchmark ausente muda a base da amostra, e a
    troca precisa aparecer -- retorno bruto mistura evento com mercado."""
    a = resumo(painel(12, com_indice=False))
    assert a.n_com_retorno_anormal == 0
    assert a.usa_retorno_bruto
    assert a.principal is a.bruto
    assert any("retorno BRUTO" in x for x in a.limitacoes)


def test_cobertura_parcial_de_anormal_ainda_usa_anormal_acima_do_piso():
    dias = dias_uteis(1200)
    idx = indice_plano(dias)
    com = [evento(f"C{i:02d}", reacao=-0.06, dias=dias, offset=200 + i * 40,
                  indice=idx, chave=f"c{i}") for i in range(8)]
    sem = [evento(f"S{i:02d}", reacao=-0.06, dias=dias, offset=200 + i * 40,
                  indice=None, chave=f"s{i}") for i in range(3)]

    a = resumo(com + sem)
    assert a.n_com_retorno_anormal == 8
    assert 8 / 11 >= am.COBERTURA_MINIMA_ANORMAL
    assert not a.usa_retorno_bruto


# ── persistência e reversão ───────────────────────────────────────────────────

def test_impacto_persistente_e_classificado_como_persistente():
    """Cenário pedido: a queda de 10% ainda está lá em 60 pregões."""
    eventos = painel(10, reacao=-0.10, dispersao=0.0, recuperacao=0.0)
    assert all(e.persistencia == PERSISTENTE for e in eventos)

    a = resumo(eventos, horizonte=60)
    assert a.n_persistentes == 10
    assert a.fracao_persistente == 1.0
    assert a.n_reversoes == 0


def test_reversao_completa_e_classificada_como_reversao():
    """Cenário pedido: cai 10% no curto prazo e termina acima do nível de t=0."""
    eventos = painel(10, reacao=-0.10, dispersao=0.0, recuperacao=0.25)
    assert all(e.persistencia == REVERSAO for e in eventos)

    a = resumo(eventos, horizonte=60)
    assert a.n_reversoes == 10
    assert a.fracao_persistente == 0.0
    assert a.n_recuperaram == 10


def test_reversao_parcial_fica_entre_as_duas():
    eventos = painel(6, reacao=-0.10, dispersao=0.0, recuperacao=0.078)
    assert all(e.persistencia == REVERSAO_PARCIAL for e in eventos)


def test_drawdown_e_tempo_ate_o_pior_ponto_sao_medidos():
    ev = evento("ATV", reacao=-0.12, dias=None, offset=200)
    assert ev.drawdown < -0.10
    assert ev.pregoes_ate_o_pior is not None and ev.pregoes_ate_o_pior >= 1
    # Não recuperou dentro da janela de acompanhamento: isso é um fato medido,
    # e o tempo de recuperação sai como piso, não como dado faltante.
    assert ev.recuperacao_observada is False
    assert ev.pregoes_ate_recuperar is None
    assert any("nao havia recuperado" in x for x in ev.limitacoes)


def test_evento_que_so_sobe_tem_drawdown_zero_medido_nao_ausente():
    ev = evento("ATV", reacao=0.10, dias=None, offset=200, ruido=0.0,
                deriva=0.002)
    assert ev.drawdown == 0.0
    assert ev.recuperacao_observada is True


def test_resumo_e_deterministico_na_ordem_de_entrada():
    eventos = painel(12)
    a = resumo(eventos)
    b = resumo(list(reversed(eventos)))
    assert a.simbolos == b.simbolos
    assert a.principal.mediana == b.principal.mediana
    assert a.periodo == b.periodo
