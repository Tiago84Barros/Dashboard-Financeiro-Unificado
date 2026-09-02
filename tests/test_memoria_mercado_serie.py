"""Série, portão de densidade, benchmark e retorno anormal.

Cobre quatro dos cenários pedidos na entrega: **benchmark ausente**, **retorno
anormal**, **dados incompletos** e a fronteira do portão de densidade -- que é
a que decide, no armazém real, quais horizontes de ação da B3 podem sequer ser
medidos.
"""
from __future__ import annotations

from datetime import date, timedelta

from core.memoria_mercado import benchmark as bmk
from core.memoria_mercado.retornos import (
    MOTIVO_ESPARSA,
    MOTIVO_FORA_DA_SERIE,
    medir_evento,
)
from core.memoria_mercado.serie import (
    DENSIDADE_MINIMA,
    TOLERANCIA_PREGOES,
    SeriePrecos,
)
from tests.apoio_memoria import (
    dias_esparsos,
    dias_uteis,
    evento,
    indice_plano,
    serie,
)

# ── construção da série ───────────────────────────────────────────────────────

def test_de_pares_ordena_deduplica_e_descarta_preco_nao_positivo():
    pares = [
        (date(2024, 1, 3), 10.0),
        (date(2024, 1, 2), 9.0),
        (date(2024, 1, 3), 11.0),   # duplicata: vence a última
        (date(2024, 1, 4), 0.0),    # preço zero não é preço
        (date(2024, 1, 5), -1.0),
    ]
    s = SeriePrecos.de_pares("X", pares)
    assert s.datas == (date(2024, 1, 2), date(2024, 1, 3))
    assert s.fechamentos == (9.0, 11.0)


def test_indice_do_pregao_cai_no_primeiro_pregao_a_partir_da_data():
    dias = dias_uteis(10)
    s = serie("X", dias)
    # Um sábado: o evento não tem pregão próprio e cai no seguinte.
    sabado = dias[4] + timedelta(days=1)
    assert s.indice_do_pregao(sabado) == 5
    assert s.indice_do_pregao(dias[0] - timedelta(days=30)) == 0
    assert s.indice_do_pregao(dias[-1] + timedelta(days=30)) is None


# ── portão de densidade ───────────────────────────────────────────────────────

def test_serie_diaria_mede_todos_os_horizontes_inclusive_na_sexta_feira():
    """Regressão do falso negativo de sexta-feira.

    Sem a folga de um pregão no denominador, uma janela de 1 pregão iniciada
    numa sexta ocupa 3 dias corridos, "esperaria" 2,07 pregões e sairia com
    densidade 0,48 -- reprovada. O efeito seria perder o horizonte de 1 pregão
    de todo evento de sexta-feira, cerca de um quinto da amostra, sem nenhum
    erro aparecer.
    """
    dias = dias_uteis(300)
    s = serie("X", dias)
    sextas = [i for i, d in enumerate(dias[:200]) if d.weekday() == 4]
    assert sextas, "o calendário sintético precisa conter sextas-feiras"

    for i in sextas:
        assert s.densidade(i, 1) >= DENSIDADE_MINIMA
        assert s.retorno(i, 1) is not None

    for h in (1, 5, 20, 60):
        assert all(s.retorno(i, h) is not None for i in range(200))


def test_folga_do_portao_nao_salva_serie_esparsa():
    """A folga é constante e some nas janelas longas; a série da B3 continua
    reprovada em todos os horizontes."""
    s = serie("Y", dias_esparsos(300, passo=15))
    for h in (1, 5, 20, 60):
        assert s.densidade(100, h) < DENSIDADE_MINIMA
        assert s.retorno(100, h) is None
    assert TOLERANCIA_PREGOES == 1.0


def test_janela_curta_demais_para_o_denominador_nao_reprova_por_construcao():
    dias = dias_uteis(50)
    s = serie("X", dias)
    # Janela de 1 pregão entre dois dias consecutivos: o denominador ajustado
    # fica <= 0 e a densidade é máxima por definição, não desconhecida.
    seg_a_ter = [i for i, d in enumerate(dias[:40]) if d.weekday() in (0, 1, 2, 3)]
    assert all(s.densidade(i, 1) == 1.0 for i in seg_a_ter)


# ── benchmark ─────────────────────────────────────────────────────────────────

def test_benchmark_ausente_nao_inventa_retorno_anormal():
    """Cenário pedido: sem índice, o retorno anormal é `None` e a limitação é
    escrita. Este é o estado REAL do armazém local hoje (SPY e QQQ têm 9 linhas
    cada), então é o caminho quente, não a exceção."""
    ev = evento("PETR4", reacao=-0.08, indice=None)

    for h in (1, 5, 20, 60):
        assert ev.janelas[h].retorno_ativo is not None
        assert ev.janelas[h].retorno_anormal is None
        assert ev.janelas[h].modelo_anormal is None
    assert not ev.tem_retorno_anormal
    assert ev.benchmark is None
    assert any("sem indice de referencia" in x for x in ev.limitacoes)


def test_retorno_anormal_separa_o_movimento_do_mercado():
    """O ativo cai 8% no pregão seguinte; o mercado cai 5%. O que pertence ao
    evento são os 3 pontos restantes."""
    dias = dias_uteis(400)
    idx = serie("IDX", dias, choques={dias[201]: -0.05}, ruido=0.0)
    ativo = serie("ABEV3", dias, choques={dias[201]: -0.08}, ruido=0.0)

    ev = medir_evento(chave="k", simbolo="ABEV3", tipo_evento="resultado",
                      data_evento=dias[200], ativo=ativo, indice=idx)

    assert abs(ev.retorno(1) - (-0.08)) < 1e-12
    assert abs(ev.janelas[1].retorno_benchmark - (-0.05)) < 1e-12
    assert abs(ev.retorno_anormal(1) - (-0.03)) < 1e-9
    assert ev.janelas[1].modelo_anormal == bmk.MODELO_DIFERENCA


def test_modelo_de_mercado_estima_beta_e_degrada_declarando_a_degradacao():
    dias = dias_uteis(400)
    idx = indice_plano(dias)
    ev = evento("ATV", reacao=-0.08, dias=dias, indice=idx,
                modelo=bmk.MODELO_MERCADO)
    assert ev.beta is not None
    assert ev.beta.n >= bmk.PREGOES_MINIMOS_ESTIMACAO
    assert ev.janelas[1].modelo_anormal == bmk.MODELO_MERCADO

    # Sem histórico anterior suficiente não há beta: o modelo degrada para a
    # diferença simples e diz que degradou, em vez de devolver `None` mudo.
    curto = dias_uteis(120)
    idx_curto = indice_plano(curto)
    ev2 = evento("ATV", reacao=-0.08, dias=curto, offset=20, indice=idx_curto,
                 modelo=bmk.MODELO_MERCADO)
    assert ev2.beta is None
    assert ev2.janelas[1].modelo_anormal == bmk.MODELO_DIFERENCA
    assert any("beta" in x.lower() for x in ev2.limitacoes)


def test_indice_sintetico_viaja_marcado_ate_o_evento():
    dias = dias_uteis(400)
    painel = [serie(f"A{i:02d}", dias, deriva=0.0002 * i) for i in range(25)]
    idx = bmk.indice_equiponderado(painel, nome="b3_equiponderado",
                                   minimo_ativos=20)
    assert idx.fonte == bmk.FONTE_SINTETICA

    ev = evento("A00", reacao=-0.05, dias=dias, indice=idx)
    assert ev.benchmark_sintetico is True
    assert any("sintetico" in x for x in ev.limitacoes)


def test_indice_equiponderado_recusa_painel_estreito():
    dias = dias_uteis(100)
    idx = bmk.indice_equiponderado([serie(f"A{i}", dias) for i in range(5)],
                                   minimo_ativos=20)
    assert idx.vazia


# ── dados incompletos ─────────────────────────────────────────────────────────

def test_horizonte_alem_do_fim_da_serie_sai_nao_medido_e_os_outros_ficam():
    """Cenário pedido: um evento com 20 pregões de futuro ainda informa 1, 5 e
    20. Ele não vira "reação nula em 60 pregões"."""
    dias = dias_uteis(232)
    ev = evento("ATV", reacao=-0.06, dias=dias, offset=200,
                indice=indice_plano(dias))

    assert ev.horizontes_medidos == (1, 5, 20)
    assert ev.janelas[60].retorno_ativo is None
    assert ev.janelas[60].motivo_ausencia == MOTIVO_FORA_DA_SERIE
    assert any("horizontes nao medidos: 60" in x for x in ev.limitacoes)


def test_serie_esparsa_nao_produz_horizonte_algum_e_diz_por_que():
    dias = dias_esparsos(400, passo=15)
    ev = evento("B3ACAO", reacao=-0.06, dias=dias, offset=200)

    assert ev.horizontes_medidos == ()
    assert {ev.janelas[h].motivo_ausencia for h in (1, 5, 20)} == {MOTIVO_ESPARSA}


def test_serie_sem_volume_nao_finge_razao_de_volume():
    ev = evento("ATV", reacao=-0.05, volumes=False)
    assert ev.razao_volume is None
    assert ev.volume_medio_pre is None
    assert any("sem volume" in x for x in ev.limitacoes)


def test_volume_e_volatilidade_sao_medidos_como_razao_pos_sobre_pre():
    ev = evento("ATV", reacao=-0.05, volume_pos=3_000_000.0)
    assert ev.razao_volume is not None and ev.razao_volume > 2.0
    assert ev.volatilidade_pre is not None and ev.volatilidade_pos is not None
    assert ev.razao_volatilidade is not None


def test_evento_fora_do_calendario_do_ativo_devolve_none():
    dias = dias_uteis(50)
    ativo = serie("X", dias)
    assert medir_evento(chave="k", simbolo="X", tipo_evento="t",
                        data_evento=dias[-1] + timedelta(days=365),
                        ativo=ativo) is None
