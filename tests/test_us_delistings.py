# -*- coding: utf-8 -*-
"""Testes da derivacao de saidas do universo americano.

Cada teste aqui existe por causa de um defeito concreto que ja custou caro no
projeto: atraso de arquivamento lido como morte, ano truncado lido como extincao
em massa, e data de saida antecipada para antes da evidencia existir.
"""
from __future__ import annotations

from datetime import date

from core.us_delistings import (
    COBERTURA_MINIMA,
    derivar_saidas,
    resumo,
)


def _universo(n: int, base: int = 1000) -> set[int]:
    return set(range(base, base + n))


def test_saida_simples_com_data_no_primeiro_ano_de_ausencia():
    por_ano = {
        2010: {1, 2, 3},
        2011: {1, 2, 3},
        2012: {1, 2},
        2013: {1, 2},
    }
    diag = derivar_saidas(por_ano)
    assert [s.cik for s in diag.saidas] == [3]
    s = diag.saidas[0]
    assert s.ultimo_ano_com_relatorio == 2011
    # A data e o fim de 2012, nao de 2011: em 2011 a empresa ainda arquivava, e
    # so o fechamento de 2012 sem arquivamento constitui evidencia da saida.
    assert s.ano_da_ausencia == 2012
    assert s.data_saida == date(2012, 12, 31)


def test_atraso_de_arquivamento_nao_vira_deslistagem():
    """Empresa que pula um ano e volta continua viva."""
    por_ano = {
        2010: {1, 2},
        2011: {1},        # 2 nao arquivou
        2012: {1, 2},     # e voltou
        2013: {1, 2},
    }
    assert derivar_saidas(por_ano).saidas == []


def test_presenca_no_ultimo_ano_nao_gera_saida():
    por_ano = {2010: {1}, 2011: {1}, 2012: {1}}
    assert derivar_saidas(por_ano).saidas == []


def test_ano_truncado_e_descartado_em_vez_de_matar_o_mercado():
    """Um trimestre perdido encolhe o ano; sem o piso, todos 'morreriam' nele."""
    cheio = _universo(1000)
    por_ano = {
        2010: cheio,
        2011: cheio,
        2012: set(list(cheio)[:100]),   # 10% -- indice truncado
        2013: cheio,
    }
    diag = derivar_saidas(por_ano)
    assert 2012 in diag.anos_descartados
    assert 2012 not in diag.anos_comparaveis
    assert diag.saidas == []


def test_piso_e_relativo_ao_maior_ano_nao_ao_vizinho():
    """Dois anos truncados em sequencia nao podem se validar um ao outro."""
    cheio = _universo(1000)
    truncado = set(list(cheio)[:500])          # 50% do maior -- abaixo do piso
    quase = set(list(cheio)[:350])             # 70% do vizinho, 35% do maior
    por_ano = {2010: cheio, 2011: truncado, 2012: quase, 2013: cheio}
    diag = derivar_saidas(por_ano)
    assert set(diag.anos_descartados) == {2011, 2012}
    assert diag.saidas == []


def test_encolhimento_real_do_universo_e_aceito():
    """O numero de arquivadores caiu de verdade entre 2010 e 2025; o piso
    precisa acomodar isso, senao descarta o mercado inteiro como 'truncado'."""
    por_ano = {
        2010: _universo(1000),
        2011: _universo(900),
        2012: _universo(800),
        2013: _universo(700),   # 70% do maior -- acima do piso de 60%
    }
    diag = derivar_saidas(por_ano)
    assert diag.anos_descartados == {}
    assert diag.anos_comparaveis == [2010, 2011, 2012, 2013]
    assert COBERTURA_MINIMA <= 0.7


def test_janela_curta_nao_declara_saida_e_diz_por_que():
    diag = derivar_saidas({2010: {1, 2, 3}})
    assert diag.saidas == []
    assert not diag.ok
    assert "comparavel" in diag.motivo


def test_ausencia_de_saidas_acusa_o_indice_e_nao_o_mercado():
    """Zero saidas num mercado real e assinatura de amostra sobrevivente."""
    por_ano = {2010: {1, 2}, 2011: {1, 2}, 2012: {1, 2}}
    diag = derivar_saidas(por_ano)
    assert diag.saidas == []
    assert "impossivel" in diag.motivo


def test_resumo_conta_saidas_por_ano():
    # O lastro de 100 sobreviventes existe para o piso de cobertura nao entrar
    # em cena: com quatro empresas no total, perder tres e "ano truncado", e o
    # teste mediria o piso em vez da contagem.
    vivas = _universo(100, base=10_000)
    por_ano = {
        2010: {1, 2, 3, 4} | vivas,
        2011: {1, 2, 3} | vivas,        # 4 sai em 2011
        2012: {1} | vivas,              # 2 e 3 saem em 2012
        2013: {1} | vivas,
    }
    rel = resumo(derivar_saidas(por_ano))
    assert rel["total_saidas"] == 3
    assert rel["saidas_por_ano"] == {"2011": 1, "2012": 2}
    assert rel["fonte"] == "sec_full_index"
    # O motivo gravado descreve o que foi OBSERVADO (ausencia de relatorio), nao
    # a causa economica: ausencia nao separa falencia de aquisicao.
    assert rel["motivo_gravado"] == "ausencia_de_relatorio_anual"


def test_saida_exige_ausencia_em_todos_os_anos_posteriores():
    """Reaparecer depois de dois anos ainda desmente a saida."""
    por_ano = {2010: {1, 2}, 2011: {1}, 2012: {1}, 2013: {1, 2}}
    assert derivar_saidas(por_ano).saidas == []
