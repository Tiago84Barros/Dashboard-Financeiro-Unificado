# -*- coding: utf-8 -*-
"""Confiança por seção: o que não foi medido não pode virar 100%.

Esta é a propriedade central do módulo, e ela existe porque o erro contrário
já aconteceu: uma faixa de validação que rejeitava valor absurdo gravava NULL
e matava a checagem que o lia (A-124). Aqui o análogo seria assumir perfeição
num componente que ninguém olhou.
"""
import pytest

from core.confianca_secao import (
    FAIXA_ALTA,
    FAIXA_MEDIA,
    Componente,
    ConfiancaSecao,
    _frescor_pct,
    confianca_global,
)


def _sec(*comps, secao="S", notas=()):
    return ConfiancaSecao(secao, tuple(comps), notas=notas)


def test_nao_medido_sai_da_media_em_vez_de_virar_cem():
    """Se o não medido contasse 100, a seção daria 75% em vez de 50%."""
    s = _sec(Componente("a", 50.0, 0.5, ""), Componente("b", None, 0.5, ""))
    assert s.pct == pytest.approx(50.0)
    assert s.cobertura_da_medicao == pytest.approx(0.5)
    assert [c.nome for c in s.nao_medidos] == ["b"]


def test_nao_medido_tambem_nao_vira_zero():
    """Zero acusaria defeito não observado; a resposta é 'não medido'."""
    s = _sec(Componente("a", None, 1.0, ""))
    assert s.pct is None
    assert s.faixa == "Nao medido"


def test_pesos_sao_renormalizados_entre_os_medidos():
    s = _sec(Componente("a", 100.0, 0.30, ""),
             Componente("b", 0.0, 0.10, ""),
             Componente("c", None, 0.60, ""))
    assert s.pct == pytest.approx(75.0)


def test_faixas():
    assert _sec(Componente("a", FAIXA_ALTA, 1.0, "")).faixa == "Alta"
    assert _sec(Componente("a", FAIXA_MEDIA, 1.0, "")).faixa == "Media"
    assert _sec(Componente("a", FAIXA_MEDIA - 0.1, 1.0, "")).faixa == "Baixa"


def test_frescor_decai_e_satura_nas_pontas():
    assert _frescor_pct(0, 3, 30) == 100.0
    assert _frescor_pct(3, 3, 30) == 100.0
    assert _frescor_pct(30, 3, 30) == 0.0
    assert _frescor_pct(999, 3, 30) == 0.0
    assert _frescor_pct(None, 3, 30) is None
    meio = _frescor_pct(16, 3, 30)
    assert 45.0 < meio < 55.0


def test_confianca_global_pondera_pela_medicao_efetiva():
    """Seção apoiada em pouca medição pesa menos na manchete: a de 100%
    mediu só 20% do peso, então não deve arrastar o número para cima."""
    forte = _sec(Componente("a", 50.0, 1.0, ""), secao="forte")
    fraca = _sec(Componente("a", 100.0, 0.2, ""),
                 Componente("b", None, 0.8, ""), secao="fraca")
    g = confianca_global([forte, fraca])
    assert g == pytest.approx((50.0 * 1.0 + 100.0 * 0.2) / 1.2)
    assert g < 62.0


def test_global_sem_nada_medido_e_none_nao_zero():
    assert confianca_global([_sec(Componente("a", None, 1.0, ""))]) is None
