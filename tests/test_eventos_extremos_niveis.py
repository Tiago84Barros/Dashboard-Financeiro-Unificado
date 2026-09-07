"""Guardas do vocabulário dos cinco níveis.

O teste central aqui é o do radical de execução. Ele não protege contra um bug:
protege contra a evolução distraída que, daqui a um ano, acrescenta
``"vender_posicao"`` à lista de ações "porque o Nível 4 já reavalia tudo mesmo".
O requisito -- nenhuma operação executada automaticamente -- só é verificável se
alguém o verificar, e é aqui que isso acontece.
"""
from __future__ import annotations

import pytest

from core.eventos_extremos import EVENTOS_EXTREMOS_VERSAO, niveis


def test_nenhuma_acao_executa_operacao():
    """Todo verbo do vocabulário observa, mede, calcula ou propõe."""
    ofensores = [
        (acao, radical)
        for acao in niveis.ACOES
        for radical in niveis.RADICAIS_DE_EXECUCAO
        if radical in acao
    ]
    assert not ofensores, (
        "ação com radical de execução no vocabulário: "
        + "; ".join(f"{a} contém {r!r}" for a, r in ofensores)
        + ". O APP4 não executa operação automaticamente -- se a intenção é "
        "propor, renomeie para propor_*."
    )


def test_todos_os_niveis_usam_somente_acoes_do_vocabulario():
    for nivel in niveis.NIVEIS:
        desconhecidas = set(nivel.acoes) - set(niveis.ACOES)
        assert not desconhecidas, f"{nivel.chave}: {desconhecidas}"


def test_acoes_sao_cumulativas_e_crescentes():
    """Subir de nível nunca retira uma autorização que o nível abaixo tinha."""
    anterior: set[str] = set()
    for nivel in niveis.NIVEIS:
        atual = set(nivel.acoes)
        assert anterior <= atual, f"{nivel.chave} perdeu ações do nível anterior"
        anterior = atual


def test_suspensao_de_recomendacao_comeca_no_nivel_3():
    for nivel in niveis.NIVEIS:
        esperado = nivel.codigo >= niveis.NIVEL_CRISE
        assert nivel.suspende_recomendacao is esperado, nivel.chave


def test_cadencia_e_silencio_encurtam_conforme_a_gravidade():
    intervalos = [n.intervalo_reavaliacao_horas for n in niveis.NIVEIS]
    silencios = [n.silencio_horas for n in niveis.NIVEIS]
    assert intervalos == sorted(intervalos, reverse=True)
    assert silencios == sorted(silencios, reverse=True)


def test_nivel_0_nao_reavalia_de_24_em_24_horas():
    """24h corridas contra gatilho de horário fixo publica dia sim, dia não.

    O defeito já aconteceu neste projeto em outra rotina. Aqui ele fica travado
    por teste em vez de por comentário.
    """
    assert niveis.de_codigo(niveis.NIVEL_NORMAL).intervalo_reavaliacao_horas < 24.0


def test_codigo_invalido_e_erro_e_nao_vira_normal():
    for ruim in (5, -1, 99, None, "crise"):
        with pytest.raises(ValueError):
            niveis.de_codigo(ruim)


def test_chave_invalida_e_erro():
    with pytest.raises(ValueError):
        niveis.de_chave("panico")


def test_de_chave_e_de_codigo_concordam():
    for nivel in niveis.NIVEIS:
        assert niveis.de_chave(nivel.chave) is nivel
        assert niveis.de_codigo(nivel.codigo) is nivel
        assert niveis.de_chave(nivel.chave.upper()) is nivel


def test_abrangencia_local_nao_sustenta_sistemico():
    for abr in (niveis.ABRANGENCIA_ATIVO, niveis.ABRANGENCIA_SETOR,
                niveis.ABRANGENCIA_PAIS):
        assert niveis.teto_por_abrangencia(abr) == niveis.NIVEL_CRISE


def test_abrangencia_regional_e_global_sustentam_sistemico():
    for abr in niveis.ABRANGENCIAS_SISTEMICAS:
        assert niveis.teto_por_abrangencia(abr) == niveis.NIVEL_SISTEMICO


def test_abrangencia_desconhecida_recebe_o_teto_restritivo():
    """Não saber onde o evento acontece não o autoriza a ser sistêmico."""
    for ruim in (None, "", "   ", "planetario", "galactica"):
        assert niveis.teto_por_abrangencia(ruim) == niveis.NIVEL_CRISE


def test_descrever_cita_rotulo_cadencia_e_acoes():
    texto = niveis.descrever(niveis.NIVEL_CRISE)
    assert "Nível 3" in texto
    assert "2h" in texto
    assert niveis.ACAO_PROPOR_PLANO in texto


def test_versao_de_metodologia_declarada():
    assert EVENTOS_EXTREMOS_VERSAO.count(".") == 2
