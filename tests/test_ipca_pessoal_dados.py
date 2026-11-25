"""Leitura do IPCA Pessoal: mock determinístico, falha propagada e série publicada."""
from __future__ import annotations

from datetime import date

from core import ipca_pessoal_dados as dados
from core.ipca_grupos import INDICE_GERAL, SerieIpcaGrupos
from core.ipca_pessoal import (
    ORIGEM_ITENS,
    ORIGEM_OFICIAL,
    calcular_ipca_pessoal,
    somar_meses,
)
from core.previstos import sql_realizado

FIM = date(2026, 10, 1)


def _serie():
    var = {}
    for k in range(24):
        m = somar_meses(FIM, -k)
        var.update({(m, INDICE_GERAL): 0.4, (m, "Habitação"): 0.3,
                    (m, "Alimentação e bebidas"): 0.5, (m, "Transportes"): 0.2})
    return SerieIpcaGrupos(variacao=var)


def test_mock_da_um_resultado_completo():
    res = calcular_ipca_pessoal(dados.lancamentos_mock(2026, 10), _serie(), 2026, 10)
    assert res.ok
    cats = {c.categoria: c for c in res.categorias}
    assert cats["Moradia"].origem == ORIGEM_ITENS
    assert cats["Moradia"].meses_proprios == 12
    assert cats["Alimentação"].origem == ORIGEM_OFICIAL
    assert cats["Transporte"].origem == ORIGEM_OFICIAL


def test_mock_e_deterministico():
    assert dados.lancamentos_mock(2026, 10) == dados.lancamentos_mock(2026, 10)


def test_falha_de_leitura_vira_none(monkeypatch):
    monkeypatch.setattr(dados, "carregar_lancamentos_ipca", lambda ano, mes: None)
    assert dados.ipca_pessoal_do_mes(2026, 10) is None


def test_conta_do_mes_usa_a_serie_publicada(monkeypatch):
    monkeypatch.setattr(dados, "carregar_lancamentos_ipca",
                        lambda ano, mes: dados.lancamentos_mock(ano, mes))
    monkeypatch.setattr(dados, "ler_ipca_grupos_publicado", _serie)
    res = dados.ipca_pessoal_do_mes(2026, 10)
    assert res.ok and res.fim == FIM


def test_sql_le_so_realizado_e_saida():
    assert sql_realizado("t", "ac") in dados._SQL
    assert "t.amount < 0" in dados._SQL


def test_sem_owner_a_leitura_levanta_em_vez_de_historico_vazio(monkeypatch):
    monkeypatch.setattr(dados.settings, "MOCK_MODE", False)
    monkeypatch.setattr(dados.settings, "OWNER_USER_ID", "")
    import pytest
    with pytest.raises(RuntimeError, match="OWNER_USER_ID"):
        dados.carregar_lancamentos_ipca(2026, 10)
