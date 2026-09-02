"""Guardas da evidência de mercado.

Dois testes aqui valem mais que os outros. O da alta do índice, porque
:func:`evidencias._faixa` usa o módulo do valor e uma queda de 15% e uma alta de
15% chegariam nele idênticas -- orientar é trabalho deste módulo. E o da janela
de referência, porque uma referência que contém o próprio choque devolve uma
razão amortecida sem erro nenhum aparecer.
"""
from __future__ import annotations

import datetime as dt
import random

import pytest

from core.eventos_extremos import mercado as mk
from core.memoria_mercado.serie import SeriePrecos

FIM = dt.date(2026, 9, 1)  # segunda-feira


def _pregoes(n: int, fim: dt.date = FIM) -> list[dt.date]:
    """``n`` dias úteis terminando em ``fim``, do mais antigo para o mais novo."""
    datas: list[dt.date] = []
    d = fim
    while len(datas) < n:
        if d.weekday() < 5:
            datas.append(d)
        d -= dt.timedelta(days=1)
    return list(reversed(datas))


def serie(simbolo: str, retornos: list[float], *, volumes: list[float] | None = None,
          fim: dt.date = FIM) -> SeriePrecos:
    datas = _pregoes(len(retornos) + 1, fim)
    preco = 100.0
    precos = [preco]
    for r in retornos:
        preco *= 1.0 + r
        precos.append(preco)
    vols = volumes if volumes is not None else [1_000_000.0] * len(datas)
    return SeriePrecos.de_pares(simbolo, list(zip(datas, precos, vols)),
                                fonte="teste")


def painel(n_ativos: int, retornos_por_ativo, *, volumes=None,
           fim: dt.date = FIM) -> list[SeriePrecos]:
    """``n_ativos`` séries; ``retornos_por_ativo(i)`` devolve a lista do ativo i."""
    return [serie(f"AT{i:03d}", retornos_por_ativo(i),
                  volumes=(volumes(i) if volumes else None), fim=fim)
            for i in range(n_ativos)]


def calmo(n: int = 400, semente: int = 7):
    def gerar(i: int) -> list[float]:
        rnd = random.Random(semente * 1000 + i)
        return [rnd.gauss(0.0, 0.008) for _ in range(n)]
    return gerar


# -- O mercado calmo é medido, não ausente ------------------------------------
def test_mercado_calmo_mede_zero_e_nao_none():
    m = mk.medir(painel(25, calmo()))
    assert m.medicoes["indices"] is not None
    assert m.medicoes["indices"] == pytest.approx(0.0, abs=0.04)
    assert m.medicoes["volatilidade"] == pytest.approx(1.0, abs=0.6)


def test_indicadores_sem_fonte_ficam_none_e_a_limitacao_os_nomeia():
    m = mk.medir(painel(25, calmo()))
    for chave in mk.SEM_FONTE_HOJE:
        assert m.medicoes[chave] is None, chave
    texto = " ".join(m.limitacoes)
    assert "spread_credito" in texto and "cambio" in texto
    assert "não são zero" in texto


# -- Queda e alta não são a mesma coisa ---------------------------------------
def test_queda_do_indice_vira_severidade():
    def gerar(i: int):
        base = calmo()(i)
        return base[:-10] + [-0.03] * 10  # ~26% em 10 pregões
    m = mk.medir(painel(25, gerar))
    assert m.medicoes["indices"] > 0.15


def test_alta_forte_do_indice_nao_e_estresse():
    """Regressão: `_faixa` usa o módulo, então orientar é trabalho daqui."""
    def gerar(i: int):
        base = calmo()(i)
        return base[:-10] + [0.03] * 10
    m = mk.medir(painel(25, gerar))
    assert m.medicoes["indices"] == pytest.approx(0.0)
    assert m.para_evidencia().valor_de("indices") == pytest.approx(0.0)


# -- A referência não pode conter o choque ------------------------------------
def test_referencia_de_volatilidade_termina_onde_a_janela_curta_comeca():
    """Referência contaminada pelo choque devolve razão amortecida, sem erro."""
    def gerar(i: int):
        rnd = random.Random(11 * 1000 + i)
        calma = [rnd.gauss(0.0, 0.005) for _ in range(390)]
        choque = [rnd.gauss(0.0, 0.040) for _ in range(10)]
        return calma + choque
    series = painel(25, gerar)
    m = mk.medir(series)
    razao = m.medicoes["volatilidade"]
    assert razao is not None and razao > 3.0, (
        "choque de 8x o desvio nos últimos 10 pregões tem que aparecer inteiro")


def test_volatilidade_sem_historico_de_referencia_nao_e_inventada():
    m = mk.medir(painel(25, lambda i: calmo(n=15)(i)))
    assert m.medicoes["volatilidade"] is None
    assert any("referência" in lim for lim in m.limitacoes)


# -- Liquidez -----------------------------------------------------------------
def test_volume_secando_vira_queda_de_liquidez():
    def vols(i: int):
        return [1_000_000.0] * 391 + [100_000.0] * 10
    m = mk.medir(painel(25, calmo(), volumes=vols))
    assert m.medicoes["liquidez"] == pytest.approx(0.9, abs=0.05)


def test_volume_estavel_nao_e_liquidez_secando():
    m = mk.medir(painel(25, calmo()))
    assert m.medicoes["liquidez"] == pytest.approx(0.0, abs=0.1)


def test_volume_ausente_nao_vira_liquidez_zero():
    def vols(i: int):
        return [None] * 401
    m = mk.medir(painel(25, calmo(), volumes=vols))
    assert m.medicoes["liquidez"] is None
    assert any("olume" in lim for lim in m.limitacoes)


# -- Correlação ---------------------------------------------------------------
def test_ativos_andando_juntos_no_fim_elevam_a_correlacao():
    def gerar(i: int):
        rnd = random.Random(3 * 1000 + i)
        calma = [rnd.gauss(0.0, 0.01) for _ in range(390)]
        comum = random.Random(999)
        junto = [comum.gauss(0.0, 0.03) for _ in range(10)]
        return calma + junto
    m = mk.medir(painel(25, gerar))
    assert m.medicoes["correlacao"] is not None
    assert m.medicoes["correlacao"] > 0.30


def test_correlacao_que_cai_nao_e_estresse():
    """Diversificação funcionando não pode entrar como severidade."""
    def gerar(i: int):
        comum = random.Random(555)
        rnd = random.Random(4 * 1000 + i)
        juntos = [comum.gauss(0.0, 0.02) for _ in range(390)]
        soltos = [rnd.gauss(0.0, 0.02) for _ in range(10)]
        return juntos + soltos
    m = mk.medir(painel(25, gerar))
    assert m.medicoes["correlacao"] == pytest.approx(0.0)


# -- Mercado fechado, série parada, painel raso -------------------------------
def test_serie_desatualizada_nao_vira_mercado_calmo():
    m = mk.medir(painel(25, calmo(), fim=dt.date(2026, 6, 1)),
                 quando=dt.date(2026, 9, 1))
    assert not m.mediu_algo
    assert any("desatualizada" in lim for lim in m.limitacoes)


def test_feriado_prolongado_nao_desliga_a_medicao():
    m = mk.medir(painel(25, calmo(), fim=dt.date(2026, 8, 28)),
                 quando=dt.date(2026, 9, 1))
    assert m.mediu_algo


def test_painel_raso_nao_produz_indice():
    m = mk.medir(painel(3, calmo()), minimo_ativos=20)
    assert not m.mediu_algo
    assert any("índice de referência" in lim for lim in m.limitacoes)


def test_sem_series_nao_explode():
    m = mk.medir([])
    assert not m.mediu_algo
    assert m.ativos == 0
    assert m.limitacoes


def test_data_anterior_a_toda_a_serie_nao_mede():
    m = mk.medir(painel(25, calmo()), quando=dt.date(2000, 1, 3))
    assert not m.mediu_algo


# -- Contrato com a evidência -------------------------------------------------
def test_para_evidencia_publica_cobertura_parcial():
    e = mk.medir(painel(25, calmo())).para_evidencia()
    assert 0.0 < e.cobertura < 1.0, "seis indicadores sem fonte têm que aparecer"
    assert e.limitacoes


def test_para_evidencia_de_painel_vazio_nao_afirma_calmaria():
    e = mk.medir([]).para_evidencia()
    assert e.cobertura == pytest.approx(0.0)
    assert e.severidade is None


def test_ordem_de_entrada_nao_muda_o_resultado():
    """Ordenação parcial já produziu resultados diferentes neste repositório."""
    series = painel(25, calmo())
    a = mk.medir(series)
    b = mk.medir(list(reversed(series)))
    assert a.medicoes == b.medicoes


def test_dicionario_de_series_e_aceito_como_lista():
    series = painel(25, calmo())
    a = mk.medir(series)
    b = mk.medir({s.simbolo: s for s in series})
    assert a.medicoes == b.medicoes


def test_queda_direcional_lisa_nao_tem_volatilidade_zero():
    """Regressão: desvio-padrão centrado remove o tombo antes de medi-lo.

    Dez pregões de -3% ao dia são -26%. Centrar na média da janela faz a média
    ser o próprio tombo e devolve volatilidade zero -- e o motor conclui
    "mercado calmo" no meio de um crash.
    """
    def gerar(i: int):
        return calmo()(i)[:-10] + [-0.03] * 10
    m = mk.medir(painel(25, gerar))
    assert m.medicoes["volatilidade"] is not None
    assert m.medicoes["volatilidade"] > 2.0, (
        "queda de 26% em 10 pregões não pode sair como volatilidade normal")
    assert m.para_evidencia().valor_de("volatilidade") > 0.0


def test_volatilidade_nao_centrada_concorda_com_a_centrada_em_ruido():
    """A correção só pode mudar o caso direcional, não o caso comum."""
    from core.memoria_mercado import benchmark as bmk
    rnd = random.Random(42)
    r = [rnd.gauss(0.0, 0.01) for _ in range(250)]
    assert mk._volatilidade_realizada(r) == pytest.approx(
        bmk.volatilidade_anualizada(r), rel=0.05)
