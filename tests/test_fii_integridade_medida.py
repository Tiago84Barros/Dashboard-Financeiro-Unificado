# -*- coding: utf-8 -*-
"""A-132 em producao: a fita de preco da B3 nao mora onde a tela roda.

O check de provento implausivel precisa do preco NEGOCIADO no dia do evento, e
a unica serie que o serve e `market.fii_b3_security_history` -- 243 MB que
foram retirados do Supabase de proposito. A consequencia era permanente e nao
parecia: a consulta ao vivo levantava `ProgrammingError` em toda execucao
publicada, o componente de peso 0,35 saia da media e a tela imprimia "nao
medido: ProgrammingError". A frase soa transitoria para um defeito que nunca
passa, e nomeia o passo errado -- nao ha nada a reparar no banco.

A saida e a de `core/us_survivorship.py`: quem tem o armazem mede e grava, a
tela le. Estes testes guardam as tres decisoes que fazem a leitura ser honesta:

* medicao ausente ou vencida NAO vira zero nem vira nota -- ela declara que
  saiu ([[medicao-que-pune-a-evidencia]], [[aviso-que-envelhece-invertido]]);
* o numerador vem do universo investivel DESTA base, nao da contagem gravada
  no armazem ([[medicao-comparou-safras-diferentes]]);
* o `try` da consulta ao vivo cobre a consulta e so ela
  ([[guarda-que-cobre-metade-da-funcao]]).
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from core import confianca_secao as cs
from core import fii_integridade as fi
from core.universo_decisao import Universo

MEDICAO_BOA = {
    "medido_em": date.today().isoformat(),
    "fonte": "armazem local: market.fii_b3_security_history",
    "limiar_provento_sobre_preco": 0.30,
    "janela_preco_epoca_dias": 10,
    "eventos_total": 21408,
    "eventos_julgados": 15798,
    "tickers_flag": ["BBFI11", "FAMB11", "HGPO11", "KNRE11", "PRSN11", "TSNC11"],
}


def _universo(investivel: int = 433) -> Universo:
    return Universo(modulo="fii", nominal=investivel, investivel=investivel,
                    apto=353, exemplos_descartados=[], notas=[])


class _ConnFalso:
    """Consulta ao vivo quebrada (o caso de producao) + universo local."""

    def __init__(self, presentes: int = 6, ao_vivo=None):
        self.presentes = presentes
        self.ao_vivo = ao_vivo

    def execute(self, sql, params=None):
        if params is None:                      # e a consulta do A-132
            if self.ao_vivo is None:
                raise RuntimeError("relation fii_b3_security_history does not exist")
            return _Resultado(self.ao_vivo)
        return _Escalar(self.presentes)         # flagrados_no_universo


class _Resultado:
    def __init__(self, linha):
        self._linha = linha

    def mappings(self):
        return self

    def one(self):
        return self._linha


class _Escalar:
    def __init__(self, valor):
        self._valor = valor

    def scalar(self):
        return self._valor


@pytest.fixture
def medicao(tmp_path, monkeypatch):
    """Grava uma medicao no disco e faz `carregar_medicao` ler dali."""
    def _grava(dados):
        destino = tmp_path / "fii_integridade.json"
        destino.write_text(json.dumps(dados), encoding="utf-8")
        monkeypatch.setattr(cs, "carregar_medicao",
                            lambda: fi.carregar_medicao(destino))
        return destino
    return _grava


# ── ausencia ────────────────────────────────────────────────────────────────

def test_sem_medicao_gravada_o_motivo_publicado_e_o_permanente(monkeypatch):
    """Nao medido continua nao medido -- mas dizendo o que de fato falta.

    O texto antigo ("nao medido: ProgrammingError") mandava procurar um erro
    de banco que nao existe. O que falta e uma medicao, e ela se produz com um
    comando nomeado.
    """
    monkeypatch.setattr(cs, "carregar_medicao", lambda: None)
    comp = cs._integridade_fii(_ConnFalso(), _universo())
    assert comp.pct is None
    assert comp.peso == 0.35
    assert "armazem local" in comp.evidencia
    assert "scripts/medir_integridade_fii.py" in comp.evidencia
    assert "ProgrammingError" not in comp.evidencia


# ── vencimento ──────────────────────────────────────────────────────────────

def test_medicao_vencida_sai_da_media_em_vez_de_ser_publicada(medicao):
    """Numero velho apresentado como atual e pior do que a ausencia.

    Eventos de renda novos chegam todo mes; uma lista de acusados apurada ha
    muito tempo nao julgou nenhum deles. Vencida, a medicao nao e publicada
    com desconto -- ela declara que saiu.
    """
    velha = dict(MEDICAO_BOA)
    velha["medido_em"] = (
        date.today() - timedelta(days=fi.VALIDADE_DIAS + 1)).isoformat()
    medicao(velha)
    comp = cs._integridade_fii(_ConnFalso(), _universo())
    assert comp.pct is None
    assert velha["medido_em"] in comp.evidencia
    assert str(fi.VALIDADE_DIAS) in comp.evidencia


def test_medicao_no_ultimo_dia_de_validade_ainda_vale(medicao):
    """O teste oposto do de cima: o corte nao pode engolir o caso valido."""
    limite = dict(MEDICAO_BOA)
    limite["medido_em"] = (
        date.today() - timedelta(days=fi.VALIDADE_DIAS)).isoformat()
    medicao(limite)
    comp = cs._integridade_fii(_ConnFalso(), _universo())
    assert comp.pct is not None


# ── safra do numerador ──────────────────────────────────────────────────────

def test_numerador_vem_desta_base_e_nao_da_lista_gravada(medicao):
    """Seis acusados no armazem, dois no universo de hoje: valem os dois.

    O denominador e `Universo.investivel`, contado em `market.fiis` da base
    que a tela le. Usar `len(tickers_flag)` como numerador poria armazem sobre
    producao na mesma fracao.
    """
    medicao(MEDICAO_BOA)
    comp = cs._integridade_fii(_ConnFalso(presentes=2), _universo(100))
    assert comp.pct == pytest.approx(98.0)      # (100 - 2) / 100
    comp6 = cs._integridade_fii(_ConnFalso(presentes=6), _universo(100))
    assert comp6.pct == pytest.approx(94.0)


def test_flagrados_no_universo_consulta_a_tabela_da_producao():
    """Quem responde "esta no universo?" e `market.fiis`, com o filtro de preco."""
    vistos = {}

    class _C:
        def execute(self, sql, params):
            vistos["sql"] = str(sql)
            vistos["params"] = params
            return _Escalar(3)

    assert fi.flagrados_no_universo(_C(), ["AAAA11", "BBBB11"]) == 3
    assert "market.fiis" in vistos["sql"]
    assert "price > 0" in vistos["sql"]
    assert vistos["params"]["t"] == ["AAAA11", "BBBB11"]


def test_lista_vazia_nao_vai_ao_banco():
    """Nenhum acusado e resultado legitimo, nao motivo de consulta."""
    class _Explode:
        def execute(self, *a, **k):
            raise AssertionError("nao deveria consultar")

    assert fi.flagrados_no_universo(_Explode(), []) == 0


# ── a fronteira do try ──────────────────────────────────────────────────────

def test_consulta_ao_vivo_que_funciona_tem_precedencia(medicao):
    """Onde a fita da B3 existe, e ela que manda -- o arquivo e o substituto."""
    medicao(MEDICAO_BOA)
    conn = _ConnFalso(presentes=6,
                      ao_vivo={"total": 1000, "julgados": 900, "tickers_flag": 1})
    comp = cs._integridade_fii(conn, _universo(100))
    assert comp.pct == pytest.approx(99.0)      # 1 acusado ao vivo, nao 6
    assert "medido no armazem" not in comp.evidencia


def test_o_caminho_gravado_declara_a_data_da_medicao(medicao):
    """Quem le a nota precisa saber de quando ela e, sem abrir o repositorio."""
    medicao(MEDICAO_BOA)
    comp = cs._integridade_fii(_ConnFalso(presentes=6), _universo(433))
    assert MEDICAO_BOA["medido_em"] in comp.evidencia
    assert "cobre" in comp.evidencia          # a cobertura do check segue junto


# ── contrato do artefato ────────────────────────────────────────────────────

def test_medicao_sem_evento_julgado_nao_passa_no_contrato():
    """`eventos_julgados` zerado nao sustenta nota nenhuma.

    `tickers_flag` vazio, sim: e "ninguem foi acusado". Sao coisas opostas.
    """
    # Sem acusado NENHUM, para que so a clausula dos julgados possa reprovar.
    vazia = dict(MEDICAO_BOA, eventos_julgados=0, tickers_flag=[])
    assert fi.medicao_coerente(vazia) is False
    assert fi.medicao_coerente(dict(MEDICAO_BOA, tickers_flag=[])) is True


def test_mais_acusados_do_que_eventos_julgados_e_incoerente():
    assert fi.medicao_coerente(
        dict(MEDICAO_BOA, eventos_julgados=3)) is False


def test_arquivo_incoerente_no_disco_nao_e_carregado(tmp_path):
    """O contrato vale na LEITURA, nao so na hora de gravar.

    O arquivo pode ter sido escrito por uma versao antiga do medidor, editado
    a mao ou truncado. Sem esta conferencia, um JSON valido e sem sentido
    viraria nota publicada.
    """
    ruim = tmp_path / "y.json"
    ruim.write_text(
        json.dumps(dict(MEDICAO_BOA, eventos_julgados=0, tickers_flag=[])),
        encoding="utf-8")
    assert fi.carregar_medicao(ruim) is None


def test_arquivo_ilegivel_vira_ausencia_e_nao_excecao(tmp_path):
    ruim = tmp_path / "x.json"
    ruim.write_text("{nao e json", encoding="utf-8")
    assert fi.carregar_medicao(ruim) is None
    assert fi.carregar_medicao(tmp_path / "nao_existe.json") is None


def test_o_artefato_versionado_passa_no_proprio_contrato():
    """O arquivo que vai para producao, conferido contra a regra que o le."""
    dados = json.loads(fi.CAMINHO_MEDICAO.read_text(encoding="utf-8"))
    assert fi.medicao_coerente(dados), dados
    assert dados["limiar_provento_sobre_preco"] == fi.LIMIAR_PROVENTO_SOBRE_PRECO
    assert dados["janela_preco_epoca_dias"] == fi.JANELA_PRECO_EPOCA_DIAS
