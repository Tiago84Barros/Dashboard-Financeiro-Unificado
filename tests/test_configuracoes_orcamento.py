# -*- coding: utf-8 -*-
"""Configurações → Geral → 🎯 Limites de orçamento.

A aba Orçamento saiu do Controle Financeiro em 30/09/2026 e os limites
continuaram valendo para os alertas, sem lugar para editá-los. O bloco devolve
o editor e segue as categorias retiradas do seletor. Riscos travados aqui:

1. **Categoria retirada continuar no editor.** Era o pedido de 31/10/2026: o
   que saiu do seletor de lançamento sai também do orçamento.
2. **Limite órfão.** Uma retirada que ainda tem limite dispara alerta todo mês.
   Escondê-la tiraria o único jeito de zerá-lo, então ela aparece, marcada.
3. **Leitura que falha virar R$ 0.** O editor mostraria zero onde há limite
   gravado, sem dizer que não conseguiu ler.
4. **Aporte com teto de gasto.** Categoria de investimento não é despesa.
"""
from __future__ import annotations

import pytest

from core import categorias as cat
from core import controle

_SAIDA = [
    {"id": "s1", "nome": "Lazer", "minha": False},
    {"id": "s2", "nome": "Mercado", "minha": False},
    {"id": "s3", "nome": "Restaurante", "minha": False},
    {"id": "s4", "nome": "Assinaturas", "minha": False},
    {"id": "s5", "nome": "Reserva de Despesa", "minha": False},
]


@pytest.fixture
def banco(monkeypatch):
    estado = {
        "retiradas": {"s3", "s4"},
        "vigentes": {"s1": 300.0, "s4": 100.0},
        "gravados": [],
        "falha_leitura": False,
    }

    def _consultar(sql, tipo_db):
        return [{**linha, "retirada": linha["id"] in estado["retiradas"]}
                for linha in _SAIDA]

    def _vigentes(ano, mes, *, estrito=False):
        if estado["falha_leitura"]:
            raise RuntimeError("banco fora")
        return dict(estado["vigentes"])

    def _salvar(ano, mes, limites):
        estado["gravados"].append(dict(limites))
        estado["vigentes"].update(limites)
        return True, ""

    monkeypatch.setattr(cat, "_consultar", _consultar)
    monkeypatch.setattr(controle, "get_orcamentos_vigentes", _vigentes)
    monkeypatch.setattr(controle, "salvar_orcamentos", _salvar)
    return estado


def _app():
    from views.configuracoes_geral import _render_orcamento
    _render_orcamento()


def _rodar():
    from streamlit.testing.v1 import AppTest
    return AppTest.from_function(_app).run(timeout=30)


def _rotulos(app) -> list[str]:
    return [n.label for n in app.number_input]


def test_retirada_sem_limite_sai_do_editor(banco):
    app = _rodar()
    assert not app.exception
    assert "Restaurante" not in " ".join(_rotulos(app))
    assert "Lazer" in _rotulos(app) and "Mercado" in _rotulos(app)


def test_retirada_com_limite_aparece_marcada_para_poder_zerar(banco):
    app = _rodar()
    assert "Assinaturas (retirada)" in _rotulos(app)
    campo = next(n for n in app.number_input if n.label.startswith("Assinaturas"))
    assert campo.value == 100.0


def test_categoria_de_investimento_nao_leva_teto(banco):
    app = _rodar()
    assert not any("Reserva de Despesa" in r for r in _rotulos(app))


def test_grava_so_o_que_mudou(banco):
    app = _rodar()
    next(n for n in app.number_input if n.label == "Mercado").set_value(800.0)
    next(n for n in app.number_input
         if n.label.startswith("Assinaturas")).set_value(0.0)
    app.button[0].click().run(timeout=30)
    assert not app.exception
    assert banco["gravados"] == [{"s2": 800.0, "s4": 0.0}]
    assert any("2 limite(s) gravado(s)" in s.value for s in app.success)


def test_nada_mudou_nao_grava(banco):
    app = _rodar()
    app.button[0].click().run(timeout=30)
    assert banco["gravados"] == []
    assert any("Nenhum limite mudou" in i.value for i in app.info)


def test_leitura_que_falha_e_dita_e_nao_vira_zero(banco):
    banco["falha_leitura"] = True
    app = _rodar()
    assert not app.exception
    assert not app.number_input  # sem editor em branco fingindo "sem limite"


def test_devolver_a_categoria_traz_de_volta_ao_editor(banco):
    banco["retiradas"] = {"s4"}
    app = _rodar()
    assert "Restaurante" in _rotulos(app)


def test_estrito_deixa_a_falha_subir(monkeypatch):
    """Sem ``estrito`` a falha vira ``{}`` -- o contrato antigo, que o resto
    do app usa. Com ele, o editor fica sabendo."""
    from core import config

    def _quebra():
        raise RuntimeError("banco fora")

    monkeypatch.setattr(config.settings, "MOCK_MODE", False, raising=False)
    monkeypatch.setattr("core.database.get_engine", _quebra)
    assert controle.get_orcamentos_vigentes(2026, 10) == {}
    with pytest.raises(RuntimeError):
        controle.get_orcamentos_vigentes(2026, 10, estrito=True)
