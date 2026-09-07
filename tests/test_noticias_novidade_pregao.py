"""A-148: a novidade decai por pregão, não por hora corrida.

Medido no motor real, antes da correção:

    sabado 03:00 lido na segunda 12:00  ->  57 h  ->  novidade 0,25
    (mesma faixa de uma noticia de quase uma semana; pregoes decorridos: 0)

Depois:

    sabado 03:00 lido na segunda 12:00  ->  0 pregoes  ->  novidade 1,00

O erro tinha sinal, e é isso que o teste central defende: ele rebaixava
sistematicamente notícia de fim de semana e de madrugada -- justamente quando
banco central, regulador e conselho de administração publicam o que não querem
no meio do pregão.
"""
from datetime import datetime, timedelta, timezone

from core import pregao
from core.noticias import relevancia
from core.noticias.modelos import Noticia


def _utc(a, m, d, h=12, mi=0):
    return datetime(a, m, d, h, mi, tzinfo=timezone.utc)


def _noticia(publicado_em):
    return Noticia(id_dedup="x", hash_conteudo="h", titulo="t",
                   url="https://exemplo.test/x", publicado_em=publicado_em)


def test_noticia_de_fim_de_semana_nao_nasce_velha():
    """O caso que motivou o A-148."""
    sabado = _utc(2026, 9, 5, 3)
    segunda = _utc(2026, 9, 7, 12)
    assert (segunda - sabado).total_seconds() / 3600.0 > 50
    assert pregao.pregoes_encerrados_entre(sabado, segunda) == 0
    assert relevancia._novidade(_noticia(sabado), segunda) == 1.0


def test_a_mesma_idade_em_horas_da_notas_diferentes_e_isso_e_o_ponto():
    """57 horas de sexta valem menos que 57 horas de sábado.

    Sob a régua antiga as duas caíam no mesmo patamar, o que era exatamente o
    defeito: horas iguais, oportunidades de precificação opostas.
    """
    fim_de_semana = relevancia._novidade(
        _noticia(_utc(2026, 9, 5, 3)), _utc(2026, 9, 7, 12))
    meio_de_semana = relevancia._novidade(
        _noticia(_utc(2026, 9, 1, 3)), _utc(2026, 9, 3, 12))
    assert fim_de_semana > meio_de_semana


def test_madrugada_de_dia_util_ainda_e_o_mesmo_pregao():
    """03:00 de terça é notícia da terça, não notícia da segunda."""
    madrugada = _utc(2026, 9, 8, 6)          # 03:00 em Sao Paulo
    manha = _utc(2026, 9, 8, 16)             # 13:00, pregao em curso
    assert relevancia._novidade(_noticia(madrugada), manha) == 1.0


def test_a_novidade_so_pode_decair_com_o_tempo():
    """Notícia não rejuvenesce ao ser reavaliada mais tarde.

    Vale para qualquer instante, inclusive atravessando fins de semana -- que é
    onde uma implementação ingênua de calendário costuma inverter o sinal.
    """
    saida = _utc(2026, 9, 4, 14)
    anterior = 1.1
    for h in range(0, 24 * 21, 5):
        v = relevancia._novidade(_noticia(saida), saida + timedelta(hours=h))
        assert v <= anterior + 1e-9, f"rejuvenesceu em {h} h"
        anterior = v


def test_noticia_antiga_continua_no_piso():
    """A correção não pode ressuscitar notícia velha -- só a de fim de semana.

    É o par obrigatório do teste central: mostrar que o caso oposto não se
    moveu. Sem ele, "sábado voltou para 1,00" poderia ser um teto solto.
    """
    velha = _utc(2026, 8, 10, 14)
    assert relevancia._novidade(_noticia(velha), _utc(2026, 9, 5, 14)) == 0.05


def test_sem_data_de_publicacao_a_novidade_e_none_e_nao_zero():
    """"Não sei quando saiu" não é "acabou de sair" nem "é velha"."""
    assert relevancia._novidade(_noticia(None), _utc(2026, 9, 7)) is None


def test_o_desconto_por_atraso_continua_em_horas():
    """Ele mede outra grandeza, e uma régua de sessões não a enxergaria.

    Atraso é quanto esta matéria chegou depois da primeira sobre o mesmo fato.
    Isso se decide dentro de um pregão -- contar em sessões daria zero para
    todos os casos que o desconto existe para separar.
    """
    saida = _utc(2026, 9, 7, 13)
    agora = _utc(2026, 9, 7, 16)
    sem = relevancia._novidade(_noticia(saida), agora)
    atrasada = relevancia._novidade(_noticia(saida), agora,
                                    primeiro_em=saida - timedelta(hours=8))
    assert sem == 1.0
    assert atrasada < sem


def test_os_patamares_nao_mudaram_so_a_unidade():
    """Trocar régua e patamares junto tornaria a mudança impossível de atribuir."""
    valores = {v for _, v in relevancia.DECAIMENTO_POR_PREGAO}
    valores.add(relevancia.DECAIMENTO_MINIMO)
    assert valores == {1.0, 0.85, 0.55, 0.25, 0.05}


def test_cada_faixa_de_pregoes_cai_no_patamar_declarado():
    esperado = {0: 1.0, 1: 0.85, 2: 0.55, 3: 0.55,
                4: 0.25, 5: 0.25, 6: 0.05, 40: 0.05}
    for n, alvo in esperado.items():
        base = relevancia.DECAIMENTO_MINIMO
        for limite, valor in relevancia.DECAIMENTO_POR_PREGAO:
            if n <= limite:
                base = valor
                break
        assert base == alvo, f"{n} pregoes"


def test_a_versao_da_metodologia_subiu_com_a_escala():
    """Mudar a escala sem trocar a safra desliga a comparação em silêncio."""
    from core.noticias import armazenamento
    assert armazenamento.VERSAO_METODOLOGIA == "1.2.0"
