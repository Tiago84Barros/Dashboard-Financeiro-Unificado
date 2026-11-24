"""Grau de Confiança do Controle Financeiro mede a série do IBGE da aba IPCA Pessoal.

Arquivo ausente é defeito (``pct=0.0``), não "não medido": sem ele a aba não
calcula. O IBGE publica o mês M por volta do dia 10 de M+1, então o último mês
tem entre ~40 e ~70 dias numa série em dia.
"""
from __future__ import annotations

from datetime import date, timedelta

from core import confianca_secao as cs
from core import ipca_grupos
from core.ipca_grupos import INDICE_GERAL, SerieIpcaGrupos
from tests.test_confianca_conciliacao import _comp, _Conn, _Engine


def _serie_ate(mes):
    return SerieIpcaGrupos(variacao={(mes, INDICE_GERAL): 0.4})


def _publicar(monkeypatch, serie):
    monkeypatch.setattr(ipca_grupos, "ler_ipca_grupos_publicado", lambda: serie)


def test_serie_em_dia_tem_nota_cheia(monkeypatch):
    _publicar(monkeypatch, _serie_ate((date.today() - timedelta(days=40)).replace(day=1)))
    [c] = cs._componente_ipca_grupos([])
    assert (c.nome, c.pct, c.peso) == ("Série do IPCA por grupo", 100.0, 0.05)


def test_serie_velha_perde_a_nota(monkeypatch):
    _publicar(monkeypatch, _serie_ate((date.today() - timedelta(days=200)).replace(day=1)))
    [c] = cs._componente_ipca_grupos([])
    assert c.pct == 0.0


def test_arquivo_ausente_e_defeito_e_vira_nota(monkeypatch):
    _publicar(monkeypatch, SerieIpcaGrupos())
    notas: list[str] = []
    [c] = cs._componente_ipca_grupos(notas)
    assert c.pct == 0.0
    assert any("IPCA" in n for n in notas)


def test_secao_controle_financeiro_inclui_o_componente(monkeypatch):
    _publicar(monkeypatch, _serie_ate((date.today() - timedelta(days=40)).replace(day=1)))
    secao = cs.confianca_controle_financeiro(_Engine(_Conn(0, 0, None)))
    c = _comp(secao, "série do ipca")
    assert c is not None and c.pct == 100.0
