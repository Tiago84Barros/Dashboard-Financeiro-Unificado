"""O calendário de pregão, e o que ele promete não fazer.

Estes testes existem porque `core/pregao.py` tem uma lacuna declarada -- não
modela feriado -- e a lacuna só é aceitável enquanto a **direção do erro** for
única. Metade daqui defende exatamente isso: sem feriados a contagem pode
superestimar sessões, e superestimar envelhece a notícia. O que o módulo nunca
pode fazer é o contrário -- fazer notícia velha parecer fresca.
"""
from datetime import datetime, timedelta, timezone

import pytest

from core import pregao


def _utc(a, m, d, h=12, mi=0):
    return datetime(a, m, d, h, mi, tzinfo=timezone.utc)


# ── o caso que motivou o módulo ──────────────────────────────────────────────
def test_fim_de_semana_nao_conta_pregao():
    """Sábado 03:00 até segunda 12:00: 57 horas corridas, zero pregões.

    É o caso do A-148 inteiro num assert. O mercado não teve chance nenhuma de
    precificar, e a notícia é tão acionável quanto no instante em que saiu.
    """
    sabado = _utc(2026, 9, 5, 3)
    segunda = _utc(2026, 9, 7, 12)
    horas = (segunda - sabado).total_seconds() / 3600.0
    assert horas > 50
    assert pregao.pregoes_encerrados_entre(sabado, segunda) == 0


def test_sessao_em_curso_nao_conta_como_pregao_inteiro():
    """Contar o dia em curso arredondaria a favor de "ela já é velha"."""
    manha = _utc(2026, 9, 7, 13)      # 10:00 em Sao Paulo, pregao abrindo
    meio = _utc(2026, 9, 7, 18)       # 15:00, pregao ainda aberto
    depois = _utc(2026, 9, 7, 21)     # 18:00, ja fechou
    assert pregao.pregoes_encerrados_entre(manha, meio) == 0
    assert pregao.pregoes_encerrados_entre(manha, depois) == 1


def test_carimbo_fora_de_ordem_devolve_zero():
    """Provedor com relógio adiantado publica no futuro. Acontece.

    Zero é a resposta certa e é a conservadora: "o mercado ainda não teve chance
    nenhuma" é literalmente o que se sabe.
    """
    agora = _utc(2026, 9, 7)
    futuro = agora + timedelta(hours=3)
    assert pregao.pregoes_encerrados_entre(futuro, agora) == 0
    assert pregao.pregoes_encerrados_entre(agora, agora) == 0


# ── a direção do erro, que é o que sustenta a lacuna dos feriados ────────────
@pytest.mark.parametrize("dias", range(1, 40))
def test_a_contagem_nunca_supera_os_dias_corridos(dias):
    """Cota superior: pregões <= dias corridos, sempre.

    Sem esta propriedade a ausência de feriados deixaria de ter direção
    conhecida, e uma lacuna sem direção não é lacuna, é defeito.
    """
    ini = _utc(2026, 1, 5, 0)
    fim = ini + timedelta(days=dias)
    assert 0 <= pregao.pregoes_encerrados_entre(ini, fim) <= dias


def test_a_contagem_e_monotona_no_tempo():
    """Esperar mais nunca deixa a notícia mais nova.

    É a garantia que o consumidor precisa: a novidade só pode decair. Um furo
    aqui deixaria uma notícia rejuvenescer sozinha ao ser reavaliada.
    """
    ini = _utc(2026, 9, 1, 3)
    anterior = 0
    for h in range(0, 24 * 20, 7):
        n = pregao.pregoes_encerrados_entre(ini, ini + timedelta(hours=h))
        assert n >= anterior
        anterior = n


def test_feriado_nao_modelado_erra_para_o_lado_conservador():
    """Documenta o custo exato da lacuna, em vez de fingir que não existe.

    7 de setembro de 2026 cai numa segunda-feira e a B3 não abre. O módulo
    conta a sessão assim mesmo -- e o efeito é envelhecer a notícia um pregão
    além do devido, nunca rejuvenescê-la.
    """
    sexta = _utc(2026, 9, 4, 21)
    terca = _utc(2026, 9, 8, 21)
    contado = pregao.pregoes_encerrados_entre(sexta, terca)
    assert contado == 2          # segunda (feriado) + terca
    assert contado >= 1          # o real; o erro e sempre para cima


# ── fusos e praças ───────────────────────────────────────────────────────────
def test_carimbo_ingenuo_e_lido_como_utc():
    """Ler como horário local produziria erro de até um pregão, em silêncio."""
    ingenuo = datetime(2026, 9, 7, 12)
    ciente = _utc(2026, 9, 7, 12)
    fim = _utc(2026, 9, 9, 12)
    assert (pregao.pregoes_encerrados_entre(ingenuo, fim)
            == pregao.pregoes_encerrados_entre(ciente, fim))


def test_horario_de_verao_nao_move_o_pregao():
    """Nova York abre às 9:30 da manhã dela o ano inteiro.

    Por isso o fuso entra por nome e não por deslocamento fixo. Em janeiro o
    deslocamento é -5 e em julho -4; a sessão é a mesma.
    """
    inverno = datetime(2026, 1, 14, 15, tzinfo=timezone.utc)   # 10:00 EST
    verao = datetime(2026, 7, 14, 14, tzinfo=timezone.utc)     # 10:00 EDT
    assert pregao.esta_aberto(inverno, pregao.NYSE)
    assert pregao.esta_aberto(verao, pregao.NYSE)


def test_esta_aberto_fecha_no_fim_de_semana_e_fora_do_horario():
    assert not pregao.esta_aberto(_utc(2026, 9, 5, 15))        # sabado
    assert not pregao.esta_aberto(_utc(2026, 9, 7, 9))         # 06:00 BRT
    assert pregao.esta_aberto(_utc(2026, 9, 7, 15))            # 12:00 BRT


def test_proximo_fechamento_pula_o_fim_de_semana():
    fecha = pregao.proximo_fechamento(_utc(2026, 9, 5, 3))
    assert fecha.astimezone(timezone.utc).date().isoformat() == "2026-09-07"


def test_proximo_fechamento_do_mesmo_dia_quando_ainda_ha_pregao():
    fecha = pregao.proximo_fechamento(_utc(2026, 9, 7, 15))    # 12:00 BRT
    assert fecha == _utc(2026, 9, 7, 20)                       # 17:00 BRT
