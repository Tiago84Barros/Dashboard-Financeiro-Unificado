# -*- coding: utf-8 -*-
"""Guardas da prova de vida do universo de FII.

O defeito coberto aqui: `load_fiis` usa `JOIN LATERAL` interno contra
`market.fii_universe_history`, entao fundo sem foto some da tela sem aparecer
como descartado. A prova de vida fecha isso -- sem virar, na volta, evidencia
de saida.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from core import fii_saidas as fs
from core import fii_universo_vivo as vivo


class _Resultado:
    def __init__(self, linhas):
        self._linhas = linhas

    def __iter__(self):
        return iter(self._linhas)

    def scalar(self):
        return self._linhas[0][0] if self._linhas else None


class _ConnFalso:
    """Registra o SQL executado e devolve o que o teste mandar."""

    def __init__(self, respostas):
        self._respostas = list(respostas)
        self.sqls: list[str] = []
        self.params: list[dict] = []

    def execute(self, clause, params=None):
        self.sqls.append(str(clause))
        self.params.append(params or {})
        return _Resultado(self._respostas.pop(0) if self._respostas else [])


AGORA = datetime(2026, 9, 18, 23, 7, 8, tzinfo=timezone.utc)


def test_a_consulta_de_ausentes_exige_preco_vivo():
    """Sem preco nao ha o que provar: o fundo nao esta negociando."""
    assert "f.price > 0" in vivo.SQL_SEM_OBSERVACAO


def test_so_entra_quem_nao_tem_nenhuma_linha_de_universo():
    """Onde ja existe foto de listagem, ela e a evidencia melhor."""
    assert "NOT EXISTS" in vivo.SQL_SEM_OBSERVACAO
    assert "market.fii_universe_history" in vivo.SQL_SEM_OBSERVACAO


def test_a_linha_declara_que_a_evidencia_e_preco_e_nao_listagem():
    linha, = vivo.linhas_de_vida(["FIIB11"], AGORA)
    assert linha["availability_quality"] == "price_observed_proxy"
    assert linha["source"] == "market.fiis:preco_negociado"
    assert linha["active_status"] == "listed"


def test_a_observacao_e_datada_no_dia_em_que_o_preco_foi_visto():
    """Nao e a data de estreia do fundo: e o dia em que se viu negocio."""
    linha, = vivo.linhas_de_vida(["FIIB11"], AGORA)
    assert linha["reference_date"] == date(2026, 9, 18)
    assert linha["available_at"] == AGORA
    assert linha["knowledge_at"] == AGORA


def test_lista_vazia_nao_gera_linha():
    assert vivo.linhas_de_vida([], AGORA) == []


def test_sem_ausente_nao_chama_o_upsert(monkeypatch):
    """Nada a provar nao pode virar escrita vazia no banco publicado."""
    chamou = []
    monkeypatch.setattr("data_pipeline.market.repository.upsert",
                        lambda *a, **k: chamou.append(a) or 0)
    conn = _ConnFalso([[(True,)], []])  # tabela existe, nenhum ausente
    assert vivo.registrar(conn, AGORA) == 0
    assert chamou == []


def test_tabela_ausente_nao_derruba_a_ingestao(monkeypatch):
    monkeypatch.setattr("data_pipeline.market.repository.upsert",
                        lambda *a, **k: pytest.fail("nao devia gravar"))
    # Com ausente na fila: se a guarda sumir, a execucao chega ao upsert.
    conn = _ConnFalso([[(False,)], [("BTAL11",)]])
    assert vivo.registrar(conn, AGORA) == 0


def test_registrar_grava_o_que_a_consulta_achou(monkeypatch):
    gravadas = {}

    def _upsert(conn, tabela, linhas):
        gravadas["tabela"] = tabela
        gravadas["linhas"] = linhas
        return len(linhas)

    monkeypatch.setattr("data_pipeline.market.repository.upsert", _upsert)
    conn = _ConnFalso([[(True,)], [("BTAL11",), ("EURO11",)]])
    assert vivo.registrar(conn, AGORA) == 2
    assert gravadas["tabela"] == "fii_universe_history"
    assert [linha["ticker"] for linha in gravadas["linhas"]] == ["BTAL11", "EURO11"]


def test_prova_de_vida_nao_entra_na_derivacao_de_saida():
    """A volta nao vale: ausencia de preco e iliquidez, nao encerramento."""
    conn = _ConnFalso([[(date(2026, 7, 16), "HGLG11")]])
    fs.fotos_do_banco(conn)
    assert vivo.QUALIDADE in conn.params[0].values()
    assert "availability_quality" in conn.sqls[0]


def test_a_derivacao_segue_lendo_as_fotos_de_listagem():
    """Excluir a prova de vida nao pode levar junto a foto de listagem."""
    conn = _ConnFalso([[(date(2026, 7, 16), "HGLG11"), (date(2026, 7, 16), "KNRI11")]])
    assert fs.fotos_do_banco(conn) == {date(2026, 7, 16): {"HGLG11", "KNRI11"}}
    assert "'listed','active'" in conn.sqls[0]
