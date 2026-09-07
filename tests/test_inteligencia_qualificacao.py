"""Guardas do vocabulário de qualificação.

Os testes que mais importam aqui são os que recusam publicação: ausente que
tenta virar fato, estimativa que tenta sair sem faixa, e frescor que tenta
dizer "atualizado" sem nunca ter sido atualizado.
"""
from __future__ import annotations

import datetime as dt

import pytest

from core.inteligencia import qualificacao as q

AGORA = dt.datetime(2026, 9, 2, 12, 0, tzinfo=dt.timezone.utc)


# -- Valor: a qualidade é uma propriedade do dado -----------------------------
def test_fato_carrega_fonte_e_carimbo():
    v = q.fato("Fechamento", 31.42, unidade=" R$", fonte="B3", medido_em=AGORA)
    assert v.qualidade == q.FATO and v.medido
    assert "B3" in v.descrever() and "Fato" in v.descrever()


def test_valor_ausente_nao_pode_ser_publicado_como_fato():
    """Ausência de dado não é ausência de risco."""
    with pytest.raises(ValueError, match="não pode ser publicado"):
        q.Valor(rotulo="Liquidez", valor=None, qualidade=q.FATO)


def test_ausente_nao_e_zero():
    a = q.ausente("Correlação sob estresse", "sem série de preços")
    z = q.fato("Correlação sob estresse", 0.0)
    assert a.medido is False and z.medido is True
    assert a.texto == "não medido" and z.texto == "0"
    assert a.numeros() == () and z.numeros() == (0.0,)


def test_estimativa_sem_faixa_precisa_declarar_o_motivo():
    """Estimativa pontual é lida como previsão, e o app não prevê."""
    with pytest.raises(ValueError, match="sem faixa"):
        q.Valor(rotulo="Impacto", valor=-0.12, qualidade=q.ESTIMATIVA)
    ok = q.Valor(rotulo="Impacto", valor=-0.12, qualidade=q.ESTIMATIVA,
                 observacao="amostra de 3 eventos não sustenta intervalo")
    assert ok.qualidade == q.ESTIMATIVA


def test_estimativa_mostra_a_faixa_e_nao_o_centro():
    v = q.estimativa("Impacto estimado", faixa=(-0.18, -0.04), unidade="",
                     confianca="baixa", horizonte="3 meses")
    assert "a " in v.texto and v.texto.startswith("-0,18")
    assert v.numeros() == (pytest.approx(-0.11), -0.18, -0.04)


def test_estimativa_sem_nada_vira_ausente():
    v = q.estimativa("Impacto estimado", observacao="sem evento comparável")
    assert v.qualidade == q.AUSENTE and not v.medido


def test_qualidade_fora_do_vocabulario_e_recusada():
    with pytest.raises(ValueError, match="vocabulário"):
        q.Valor(rotulo="X", valor=1, qualidade="provavel")


def test_toda_qualidade_tem_icone_e_rotulo_alem_da_cor():
    """Acessibilidade: cor não pode ser o único canal."""
    for chave in q.QUALIDADES:
        ap = q.APARENCIA[chave]
        assert ap["icone"].strip() and ap["rotulo"].strip()


# -- Frescor: o estado é derivado, nunca escrito ------------------------------
def test_dado_recente_esta_fresco():
    f = q.Frescor("Notícias", atualizado_em=AGORA - dt.timedelta(hours=2),
                  validade_horas=6)
    assert f.estado(AGORA) == q.FRESCO and not f.a_destacar(AGORA)


def test_dado_vencido_e_destacado_e_diz_quando_foi():
    f = q.Frescor("Notícias", atualizado_em=AGORA - dt.timedelta(hours=30),
                  validade_horas=6)
    assert f.estado(AGORA) == q.VENCIDO and f.a_destacar(AGORA)
    texto = f.descrever(AGORA)
    assert "Desatualizado" in texto and "30.0h" in texto and "validade 6h" in texto


def test_falha_da_api_e_indisponivel_e_nao_vencido():
    f = q.Frescor("Notícias", atualizado_em=AGORA, disponivel=False,
                  erro="HTTP 503 no provedor")
    assert f.estado(AGORA) == q.INDISPONIVEL and f.a_destacar(AGORA)
    assert "HTTP 503" in f.descrever(AGORA)


def test_nunca_atualizado_nao_passa_por_fresco():
    f = q.Frescor("Memória de mercado", atualizado_em=None)
    assert f.estado(AGORA) == q.NUNCA and f.a_destacar(AGORA)


def test_carimbo_ingenuo_nao_explode_contra_carimbo_ciente():
    """O banco devolve ingênuo e `core.noticias` grava ciente."""
    f = q.Frescor("Vitrine", atualizado_em=dt.datetime(2026, 9, 2, 10, 0),
                  validade_horas=6)
    assert f.estado(AGORA) == q.FRESCO


def test_frescor_no_limite_da_validade_ainda_vale():
    f = q.Frescor("X", atualizado_em=AGORA - dt.timedelta(hours=6),
                  validade_horas=6)
    assert f.estado(AGORA) == q.FRESCO


def test_todo_estado_de_frescor_tem_icone():
    for chave in q.ESTADOS_FRESCOR:
        assert q.APARENCIA_FRESCOR[chave]["icone"].strip()


# -- Provedor -----------------------------------------------------------------
def test_provedor_fora_do_ar_se_descreve():
    p = q.Provedor("newsapi", disponivel=False, detalhe="cota diária esgotada")
    assert "indisponível" in p.descrever() and "cota" in p.descrever()


def test_provedor_publica_cota_restante():
    p = q.Provedor("brapi", disponivel=True, chamadas_restantes=42)
    assert "42" in p.descrever()


# -- Bloco --------------------------------------------------------------------
def test_bloco_publica_cobertura_e_separa_o_que_nao_foi_medido():
    b = q.Bloco("Antifragilidade", valores=(
        q.fato("Liquidez", 0.22), q.ausente("Correlação", "sem série"),
        q.fato("Concentração", 0.31), q.ausente("Crédito", "sem rating")))
    assert b.cobertura == pytest.approx(0.5)
    assert {v.rotulo for v in b.nao_medidos} == {"Correlação", "Crédito"}


def test_bloco_vazio_nao_divide_por_zero():
    assert q.Bloco("Vazio").cobertura == pytest.approx(0.0)


def test_numeros_do_bloco_sao_a_ancora_da_llm():
    b = q.Bloco("X", valores=(
        q.fato("A", 10.0),
        q.estimativa("B", faixa=(-0.2, -0.05)),
        q.ausente("C", "sem fonte")))
    nums = b.numeros()
    assert 10.0 in nums and -0.2 in nums and -0.05 in nums
    assert len(nums) == 4, "o ausente não pode contribuir com número nenhum"
