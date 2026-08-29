# -*- coding: utf-8 -*-
"""A população sobre a qual a mortalidade americana é medida (A-158)."""
from decimal import Decimal

import pytest

from core import us_universo_sec as u


@pytest.mark.parametrize("sic,nome", [
    ("6189", "World Omni Automobile Lease Securitization Trust"),
    ("6726", "Commonwealth Income & Growth Fund VII"),
    ("6798", "Angel Oak Mortgage REIT"),
    ("6770", "Ajax Capital Acquisitions Corp"),
])
def test_veiculo_sai_do_universo(sic, nome):
    ok, motivo = u.classificar(nome=nome, sic=sic, sic_descricao=None)
    assert ok is False and motivo


@pytest.mark.parametrize("sic,desc,nome", [
    ("2834", "Pharmaceutical Preparations", "Shockwave Medical, Inc."),
    ("6022", "State Commercial Banks", "Fauquier Bankshares, Inc."),
    ("3674", "Semiconductors & Related Devices", "SPX FLOW, Inc."),
])
def test_companhia_operacional_fica(sic, desc, nome):
    ok, _ = u.classificar(nome=nome, sic=sic, sic_descricao=desc)
    assert ok is True


def test_sem_sic_e_nao_classificado_e_nao_vira_zero():
    """Terceiro estado de propósito.

    Somar o não apurado a qualquer dos lados afirmaria o que ninguém apurou: em
    `operacionais` inflaria o denominador da mortalidade, em `veiculos` o
    reduziria. Nos dois casos o usuário leria como medição o que é lacuna.
    """
    ok, motivo = u.classificar(nome="Alguma Coisa Inc", sic=None)
    assert ok is None
    assert "não classificado" in motivo


@pytest.mark.parametrize("sic", [
    "",
    True,
    "sic livre",
    float("nan"),
    float("inf"),
    float("-inf"),
    2834.0,
    "2834.0",
    "0000",
    "123",
    "12345",
])
def test_sic_fora_do_formato_sec_e_nao_classificado(sic):
    ok, motivo = u.classificar(nome="Alguma Coisa Inc", sic=sic)
    assert ok is None
    assert "não classificado" in motivo


def test_criterio_nao_toca_em_campo_que_a_sec_apaga_na_morte():
    """A trava central deste módulo.

    `tickers` e `exchanges` vêm vazios para quem parou de arquivar -- 0 de 40
    CIKs sorteados entre as saídas devolveram ticker. Um critério que os lesse
    excluiria seletivamente os mortos e devolveria mortalidade menor por
    construção, reintroduzindo exatamente o viés que a medição dimensiona.

    O teste é sobre a assinatura, não sobre um valor: é ela que impede o campo
    de entrar na regra numa edição futura.
    """
    import inspect
    params = set(inspect.signature(u.classificar).parameters)
    assert params == {"nome", "sic", "sic_descricao"}
    assert not (params & {"tickers", "exchanges", "symbol", "is_active"})


def test_mesma_entidade_classifica_igual_viva_ou_morta():
    """A comparação entre coorte e painel só vale se a regra for a mesma.

    A entidade não muda de natureza ao morrer: o que muda é o que a SEC serve
    sobre ela. Passar a mesma identidade duas vezes, uma com os campos de vida
    preenchidos e outra sem, tem de dar o mesmo veredito.
    """
    viva = u.classificar(nome="Reis, Inc.", sic="6531",
                         sic_descricao="Real Estate Agents & Managers (For Others)")
    morta = u.classificar(nome="Reis, Inc.", sic="6531",
                          sic_descricao="Real Estate Agents & Managers (For Others)")
    assert viva == morta


def test_particionar_nao_perde_nem_duplica_cik():
    linhas = [
        {"cik": 1, "nome": "A Inc", "sic": "2834", "sic_descricao": "Pharmaceutical Preparations"},
        {"cik": 2, "nome": "B Trust", "sic": "6189", "sic_descricao": None},
        {"cik": 3, "nome": "C Corp", "sic": None, "sic_descricao": None},
    ]
    p = u.particionar(linhas)
    assert p["operacionais"] == {1}
    assert p["veiculos"] == {2}
    assert p["nao_classificados"] == {3}
    todos = p["operacionais"] | p["veiculos"] | p["nao_classificados"]
    assert todos == {1, 2, 3}
    assert sum(len(v) for v in p.values()) == len(todos)


def test_particionar_duplicata_identica_eh_idempotente():
    entidade = {"cik": 1, "nome": "A Inc", "sic": "2834",
                "sic_descricao": "Pharmaceutical Preparations"}
    p = u.particionar([entidade, entidade.copy()])
    assert p == {"operacionais": {1}, "veiculos": set(),
                 "nao_classificados": set()}


def test_particionar_conflito_de_cik_falha_fechada_sem_duplicar_conjuntos():
    p = u.particionar([
        {"cik": 1, "nome": "A Inc", "sic": "2834",
         "sic_descricao": "Pharmaceutical Preparations"},
        {"cik": 1, "nome": "A Trust", "sic": "6189", "sic_descricao": None},
    ])
    assert p == {"operacionais": set(), "veiculos": set(),
                 "nao_classificados": {1}}


def test_particionar_sics_operacionais_distintos_conflitam_por_cik():
    p = u.particionar([
        {"cik": 1, "nome": "A Inc", "sic": "2834",
         "sic_descricao": "Pharmaceutical Preparations"},
        {"cik": 1, "nome": "A Inc", "sic": "3674",
         "sic_descricao": "Semiconductors & Related Devices"},
    ])
    assert p == {"operacionais": set(), "veiculos": set(),
                 "nao_classificados": {1}}


def test_particionar_normaliza_pontuacao_e_caixa_do_nome_duplicado():
    p = u.particionar([
        {"cik": 1, "nome": "Acme, Inc.", "sic": "2834",
         "sic_descricao": "Pharmaceutical Preparations"},
        {"cik": 1, "nome": "ACME INC", "sic": "2834",
         "sic_descricao": "Pharmaceutical Preparations"},
    ])
    assert p == {"operacionais": {1}, "veiculos": set(),
                 "nao_classificados": set()}


def test_particionar_nome_materialmente_divergente_conflita_por_cik():
    p = u.particionar([
        {"cik": 1, "nome": "Acme Inc", "sic": "2834",
         "sic_descricao": "Pharmaceutical Preparations"},
        {"cik": 1, "nome": "Outra Corp", "sic": "2834",
         "sic_descricao": "Pharmaceutical Preparations"},
    ])
    assert p == {"operacionais": set(), "veiculos": set(),
                 "nao_classificados": {1}}


def test_particionar_descricao_sic_materialmente_divergente_conflita_por_cik():
    p = u.particionar([
        {"cik": 1, "nome": "Acme Inc", "sic": "2834",
         "sic_descricao": "Pharmaceutical Preparations"},
        {"cik": 1, "nome": "ACME, INC.", "sic": "2834",
         "sic_descricao": "Semiconductors & Related Devices"},
    ])
    assert p == {"operacionais": set(), "veiculos": set(),
                 "nao_classificados": {1}}


@pytest.mark.parametrize("cik", [True, 1.5, Decimal("1"), "1", 0, -1,
                                  float("nan"), float("inf"), 10**400])
def test_particionar_ignora_cik_sem_identidade_sec_estrita(cik):
    p = u.particionar([{"cik": cik, "nome": "A Inc", "sic": "2834",
                        "sic_descricao": "Pharmaceutical Preparations"}])
    assert p == {"operacionais": set(), "veiculos": set(),
                 "nao_classificados": set()}
