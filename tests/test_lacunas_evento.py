"""Evento de lacuna: a impressao digital identifica o FATO, nao o texto.

A mensagem que a pessoa ve carrega numeros que mudam a cada execucao ("faltam
12 meses", "faltam 13 meses"). Se o texto entrasse na chave, a mesma lacuna
viraria uma linha nova por dia e a frequencia -- que e o que prioriza a
correcao -- nunca passaria de 1.

    memoria: chave-de-texto-nao-e-identidade-do-fato
"""
from datetime import datetime, timezone

import pytest

from core.lacunas.evento import (
    MAX_MENSAGEM,
    Lacuna,
    construir_lacuna,
    filtrar_contexto,
    impressao_digital,
    normalizar,
    sanitizar,
)


def test_impressao_ignora_numeros_da_mensagem_quando_ha_codigo():
    a = impressao_digital("motor", "core/x.py:f", "fii.vpa", "HGLG11", "faltam 12 meses")
    b = impressao_digital("motor", "core/x.py:f", "fii.vpa", "HGLG11", "faltam 13 meses")
    assert a == b
    assert len(a) == 40


def test_impressao_muda_com_codigo_entidade_fonte_e_modulo():
    base = ("motor", "core/x.py:f", "fii.vpa", "HGLG11", "m")
    ref = impressao_digital(*base)
    assert impressao_digital("tela", *base[1:]) != ref
    assert impressao_digital("motor", "core/y.py:f", *base[2:]) != ref
    assert impressao_digital("motor", "core/x.py:f", "fii.dy", "HGLG11", "m") != ref
    assert impressao_digital("motor", "core/x.py:f", "fii.vpa", "KNRI11", "m") != ref


def test_entidade_nao_diferencia_caixa():
    assert (impressao_digital("motor", "m", "c", "petr4", "x")
            == impressao_digital("motor", "m", "c", "PETR4", "x"))


def test_sem_codigo_a_mensagem_normalizada_vira_a_chave():
    a = impressao_digital("llm", "core/llm_b3.py:f", "", None, "Sem dados de 2024-05-01, faltam 12")
    b = impressao_digital("llm", "core/llm_b3.py:f", "", None, "sem dados de 2025-01-31,  faltam 7")
    c = impressao_digital("llm", "core/llm_b3.py:f", "", None, "sem dividendos")
    assert a == b
    assert a != c


def test_normalizar_troca_datas_e_numeros():
    assert normalizar("Em 31/08/2026 faltavam 1.234,5  MESES") == "em <data> faltavam # meses"
    assert normalizar("desde 2026-07-15") == "desde <data>"


@pytest.mark.parametrize("sujo, proibido", [
    ("falhou em postgresql://u:senha@db.x.supabase.co:5432/p agora", "senha"),
    ("veja https://exemplo.com/a?b=1", "exemplo.com"),
    ("contato fulano@gmail.com", "gmail"),
    ("posição de R$ 1.234,56 em PETR4", "1.234"),
    ("custo US$ 99.10 hoje", "99.10"),
])
def test_sanitizar_remove_segredos_e_valores(sujo, proibido):
    limpo = sanitizar(sujo)
    assert proibido not in limpo


def test_sanitizar_preserva_o_resto():
    assert sanitizar("sem histórico de VPA para HGLG11") == "sem histórico de VPA para HGLG11"


def test_filtrar_contexto_usa_lista_branca():
    ctx = filtrar_contexto({"tabela": "fii_metrics", "n_faltantes": 12,
                            "senha": "x", "user_id": "abc"})
    assert ctx == {"tabela": "fii_metrics", "n_faltantes": 12}
    assert filtrar_contexto(None) == {}


def test_filtrar_contexto_converte_valor_nao_primitivo_em_texto_curto():
    ctx = filtrar_contexto({"periodo": ["2024", "2025"] * 100})
    assert isinstance(ctx["periodo"], str)
    assert len(ctx["periodo"]) <= 100


def test_construir_lacuna_monta_evento_sanitizado_e_truncado():
    agora = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    lac = construir_lacuna(fonte="motor", codigo=" fii.vpa ", mensagem="x" * 900 + " a@b.com",
                           modulo="core/x.py:f", entidade=" hglg11 ",
                           contexto={"tabela": "t", "outra": 1}, agora=agora)
    assert isinstance(lac, Lacuna)
    assert lac.codigo == "fii.vpa"
    assert lac.entidade == "HGLG11"
    assert len(lac.mensagem) == MAX_MENSAGEM
    assert lac.contexto == {"tabela": "t"}
    assert lac.ts == "2026-09-28T12:00:00+00:00"
    assert lac.impressao == impressao_digital("motor", "core/x.py:f", "fii.vpa", "HGLG11", "")


def test_construir_lacuna_recusa_fonte_desconhecida():
    with pytest.raises(ValueError):
        construir_lacuna(fonte="outra", codigo="c", mensagem="m", modulo="m")
