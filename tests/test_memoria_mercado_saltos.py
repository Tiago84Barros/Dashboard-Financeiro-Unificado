"""Salto de preço bruto: o índice não soma desdobramento, o evento não o mede.

Até a metodologia 1.0.0 o índice equiponderado da B3 era a média crua dos
retornos diários do COTAHIST. Medido no armazém em 28/09/2026: +77 % em 20
pregões a partir de 01/02/2016, p99 de +720 % em 20 pregões. E o retorno do
ativo trazia o desdobramento junto -- WEGE3 caía 50,7 % em 28/04/2021 no dia em
que a especificação virou ``ON  EB  NM``.
"""
from __future__ import annotations

import pytest

from core.memoria_mercado import benchmark as bmk
from core.memoria_mercado.retornos import MOTIVO_SALTO, medir_evento
from core.memoria_mercado.serie import (
    SALTO_SUSPEITO,
    neutralizar_eventos_societarios,
)
from scripts import construir_memoria_mercado as construtor
from tests.apoio_memoria import dias_uteis, serie


def _com_desdobramento(simbolo, dias, i_salto, fator=0.5, **kw):
    """Série cujo preço bruto cai para ``fator`` do nível em ``dias[i_salto]``."""
    return serie(simbolo, dias, choques={dias[i_salto]: fator - 1.0}, **kw)


def _retorno_no_dia(indice, d):
    """O índice nasce no segundo pregão: posição não é data."""
    i = indice.indice_do_pregao(d)
    assert indice.datas[i] == d
    return indice.fechamentos[i] / indice.fechamentos[i - 1] - 1


# ── índice ────────────────────────────────────────────────────────────────────

def test_indice_exclui_o_salto_de_um_papel_e_nao_o_soma_como_mercado():
    dias = dias_uteis(60)
    painel = [serie(f"A{i:02d}", dias) for i in range(24)]
    # Um único papel com grupamento de 10:1 (+900 %) no pregão 30.
    painel.append(serie("GRP", dias, choques={dias[30]: 9.0}))

    sem_filtro = bmk.indice_equiponderado(painel, limiar_salto=None)
    com_filtro = bmk.indice_equiponderado(painel)
    so_limpos = bmk.indice_equiponderado(painel[:24])

    salto_sem = _retorno_no_dia(sem_filtro, dias[30])
    salto_com = _retorno_no_dia(com_filtro, dias[30])
    limpo = _retorno_no_dia(so_limpos, dias[30])
    assert salto_sem > 0.30                 # 1/25 de +900 % = +36 % "de mercado"
    assert salto_com == pytest.approx(limpo)


def test_filtro_exclui_e_nao_apara():
    """Aparar em 35 % ainda somaria 35/25 = 1,4 % que não aconteceu."""
    dias = dias_uteis(10)
    painel = [serie(f"A{i:02d}", dias, ruido=0.0) for i in range(24)]
    painel.append(serie("GRP", dias, ruido=0.0, choques={dias[5]: 9.0}))
    idx = bmk.indice_equiponderado(painel)
    assert _retorno_no_dia(idx, dias[5]) == pytest.approx(0.0)


# ── neutralização por marcador ────────────────────────────────────────────────

def test_salto_marcado_vira_retorno_zero_e_o_trecho_final_fica_intacto():
    dias = dias_uteis(30)
    bruta = _com_desdobramento("WEGE3", dias, 15)
    ajustada = neutralizar_eventos_societarios(bruta, [dias[15]])

    assert ajustada.ajustes == (dias[15],)
    assert ajustada.fechamentos[15] == pytest.approx(ajustada.fechamentos[14]
                                                     * bruta.fechamentos[15]
                                                     / bruta.fechamentos[14]
                                                     / 0.5, rel=0.02)
    assert abs(ajustada.fechamentos[15] / ajustada.fechamentos[14] - 1) < 0.02
    assert ajustada.fechamentos[15:] == bruta.fechamentos[15:]
    assert ajustada.saltos() == ()
    # Volume anterior em unidade nova: metade do preço, o dobro de papéis.
    assert ajustada.volumes[0] == pytest.approx(bruta.volumes[0] * 2, rel=0.02)


def test_marcador_sem_salto_nao_mexe_e_salto_sem_marcador_nao_e_ajustado():
    dias = dias_uteis(30)
    bruta = _com_desdobramento("X", dias, 15)
    # Dividendo marcado em outro dia (sem salto) e nenhum marcador no salto.
    saida = neutralizar_eventos_societarios(bruta, [dias[5]])
    assert saida is bruta
    assert saida.saltos() == (15,)


def test_marcador_novo_e_o_que_aparece_e_nao_o_que_persiste():
    linhas = [("d1", "ON      NM"), ("d2", "ON  EB  NM"), ("d3", "ON  EB  NM"),
              ("d4", "ON  EDB NM"), ("d5", "CI  ER"), ("d6", "CI  01")]
    assert construtor.datas_com_marcador_novo(linhas) == ("d2", "d4", "d5")


# ── medição do evento ─────────────────────────────────────────────────────────

def _painel_e_indice(dias):
    painel = [serie(f"A{i:02d}", dias) for i in range(24)]
    return bmk.indice_equiponderado(painel)


def test_janela_que_atravessa_salto_sem_marcador_nao_e_medida():
    dias = dias_uteis(400)
    idx = _painel_e_indice(dias)
    ativo = _com_desdobramento("MGLU3", dias, 210, fator=10.0)

    ev = medir_evento(chave="k", simbolo="MGLU3", tipo_evento="resultado",
                      data_evento=dias[200], ativo=ativo, indice=idx,
                      limiar_salto=SALTO_SUSPEITO)
    assert ev.janelas[1].retorno_anormal is not None
    assert ev.janelas[5].retorno_anormal is not None
    for h in (20, 60):
        assert ev.janelas[h].retorno_ativo is None
        assert ev.janelas[h].retorno_anormal is None
        assert ev.janelas[h].motivo_ausencia == MOTIVO_SALTO
    assert ev.drawdown is None
    assert ev.persistencia is None
    assert any(dias[210].strftime("%d/%m/%Y") in x for x in ev.limitacoes)
    # A volatilidade perde só o pregão do salto, não a janela.
    assert ev.volatilidade_pos is not None and ev.volatilidade_pos < 0.5


def test_sem_limiar_o_comportamento_antigo_fica_para_os_eua():
    dias = dias_uteis(400)
    idx = _painel_e_indice(dias)
    ativo = _com_desdobramento("X", dias, 210, fator=0.5)
    ev = medir_evento(chave="k", simbolo="X", tipo_evento="resultado",
                      data_evento=dias[200], ativo=ativo, indice=idx)
    assert ev.janelas[20].retorno_ativo < -0.4
    assert ev.drawdown is not None


def test_deriva_pre_evento_que_atravessa_salto_nao_e_medida():
    dias = dias_uteis(400)
    ativo = _com_desdobramento("X", dias, 195, fator=0.5)
    ev = medir_evento(chave="k", simbolo="X", tipo_evento="resultado",
                      data_evento=dias[200], ativo=ativo,
                      indice=_painel_e_indice(dias), limiar_salto=SALTO_SUSPEITO)
    assert ev.deriva_pre_evento is None
    assert ev.janelas[60].retorno_ativo is not None


def test_ajuste_perto_do_evento_sai_nas_limitacoes():
    dias = dias_uteis(400)
    bruta = _com_desdobramento("WEGE3", dias, 210)
    ajustada = neutralizar_eventos_societarios(bruta, [dias[210]])
    ev = medir_evento(chave="k", simbolo="WEGE3", tipo_evento="resultado",
                      data_evento=dias[200], ativo=ajustada,
                      indice=_painel_e_indice(dias), limiar_salto=SALTO_SUSPEITO)
    assert abs(ev.janelas[20].retorno_ativo) < 0.1
    assert ev.drawdown is not None
    assert any("retroajustado" in x and dias[210].strftime("%d/%m/%Y") in x
               for x in ev.limitacoes)


def test_beta_ignora_o_pregao_do_salto():
    dias = dias_uteis(400)
    idx = _painel_e_indice(dias)
    limpo = serie("X", dias)
    sujo = serie("X", dias, choques={dias[150]: 9.0})
    b_limpo = bmk.estimar_beta(limpo, idx, 200)
    b_sujo = bmk.estimar_beta(sujo, idx, 200)
    b_filtrado = bmk.estimar_beta(sujo, idx, 200, limiar_salto=SALTO_SUSPEITO)
    assert b_filtrado.n == b_limpo.n - 1
    assert b_filtrado.beta == pytest.approx(b_limpo.beta, abs=0.05)
    assert b_sujo is None or abs(b_sujo.beta - b_limpo.beta) > 0.05


# ── o construtor ──────────────────────────────────────────────────────────────

class _Resultado:
    def __init__(self, linhas):
        self._linhas = linhas

    def mappings(self):
        return iter(self._linhas)

    def keys(self):
        return list(self._linhas[0]) if self._linhas else []


class _Conexao:
    def __init__(self, linhas):
        self._linhas = linhas

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        return _Resultado(self._linhas)


class _Engine:
    def __init__(self, linhas):
        self._linhas = linhas

    def begin(self):
        return _Conexao(self._linhas)


def test_construtor_b3_retroajusta_o_marcado_e_relata_antes_e_depois():
    dias = dias_uteis(400)
    linhas = []
    for k in range(25):
        s = serie(f"A{k:02d}", dias, deriva=0.0001 * k)
        for i, d in enumerate(dias):
            espec = "ON      NM"
            if k == 0 and i >= 250:
                espec = "ON  EB  NM"         # bonificação a partir do 250
            linhas.append({"simbolo": s.simbolo, "data": d,
                           "fechamento": s.fechamentos[i]
                           * (0.5 if (k == 0 and i >= 250) else 1.0),
                           "volume": 1_000.0, "especificacao": espec})
    eventos = [{"chave": f"k{k}", "simbolo": f"A{k:02d}",
                "tipo_evento": "resultado", "data": dias[240].isoformat()}
               for k in range(25)]

    saida = construtor.construir(_Engine(linhas), mercado="b3", eventos=eventos)
    rel = saida["relatorio"]
    assert rel["precos_retroajustados"] == 1
    assert rel["janelas_nao_medidas_por_salto"] == 0
    a00 = next(ev for ev in saida["medidos"] if ev.simbolo == "A00")
    assert abs(a00.janelas[20].retorno_ativo) < 0.1
    assert set(rel["distribuicao_indice"]) == {"antes_sem_filtro", "depois"}
    assert set(rel["distribuicao_indice"]["depois"]) == {"20d", "60d"}
    assert "p1" in rel["distribuicao_indice"]["depois"]["20d"]


def test_script_simula_por_padrao_e_so_grava_com_apply():
    import inspect
    fonte = inspect.getsource(construtor.main)
    assert '"--apply"' in fonte
    assert "if not args.apply:" in fonte
