"""Gancho de captura: nunca propaga excecao e nunca impede o salvamento."""
import datetime as dt

import pytest

from core.portfolio import capture


def test_captura_grava_e_devolve_a_contagem(monkeypatch):
    chamadas = {}

    class FakeAdapter:
        @staticmethod
        def build_snapshots(items, *, model_id, params, as_of, loaders=None):
            chamadas["items"] = items
            return ["snap1", "snap2"]

    monkeypatch.setattr(capture, "load_adapter", lambda key: FakeAdapter)
    monkeypatch.setattr(capture, "save_snapshots", lambda snaps, **kw: len(snaps))
    monkeypatch.setattr(capture, "prune_orphans", lambda **kw: 0)
    monkeypatch.setattr(capture, "apply_retention", lambda ac, **kw: 0)

    n = capture.capture_snapshots("b3", "m01", [{"tk": "PETR4"}], {},
                                  as_of=dt.date(2026, 8, 5))
    assert n == 2
    assert chamadas["items"] == [{"tk": "PETR4"}]


def test_falha_no_adaptador_nao_propaga(monkeypatch, caplog):
    def explode(key):
        raise RuntimeError("adaptador quebrado")

    monkeypatch.setattr(capture, "load_adapter", explode)
    assert capture.capture_snapshots("b3", "m01", [{"tk": "PETR4"}], {}) == 0
    assert "snapshot" in caplog.text.lower()


def test_falha_na_gravacao_nao_propaga(monkeypatch):
    class FakeAdapter:
        @staticmethod
        def build_snapshots(items, **kw):
            return ["snap1"]

    def explode(snaps, **kw):
        raise RuntimeError("banco fora")

    monkeypatch.setattr(capture, "load_adapter", lambda key: FakeAdapter)
    monkeypatch.setattr(capture, "save_snapshots", explode)
    assert capture.capture_snapshots("b3", "m01", [{"tk": "PETR4"}], {}) == 0


def test_falha_na_retencao_nao_anula_a_gravacao(monkeypatch):
    class FakeAdapter:
        @staticmethod
        def build_snapshots(items, **kw):
            return ["snap1"]

    def explode(ac, **kw):
        raise RuntimeError("retencao quebrada")

    monkeypatch.setattr(capture, "load_adapter", lambda key: FakeAdapter)
    monkeypatch.setattr(capture, "save_snapshots", lambda snaps, **kw: len(snaps))
    monkeypatch.setattr(capture, "prune_orphans", lambda **kw: 0)
    monkeypatch.setattr(capture, "apply_retention", explode)

    assert capture.capture_snapshots("b3", "m01", [{"tk": "PETR4"}], {}) == 1


def test_lista_vazia_nao_chama_o_adaptador(monkeypatch):
    def nao_deve_ser_chamado(key):
        raise AssertionError("adaptador nao deveria ser carregado")

    monkeypatch.setattr(capture, "load_adapter", nao_deve_ser_chamado)
    assert capture.capture_snapshots("b3", "m01", [], {}) == 0


@pytest.mark.parametrize("modulo,funcao,classe", [
    ("core.b3_portfolio_model", "save_b3_portfolio_model", "b3"),
    ("core.us_portfolio_model", "save_us_portfolio_model", "us"),
    ("core.fii_portfolio_model", "save_fii_portfolio_model", "fii"),
])
def test_as_tres_funcoes_de_salvamento_chamam_a_captura(modulo, funcao, classe):
    """Regressao: a captura precisa estar ligada, senao nada e persistido."""
    import importlib
    import inspect

    fonte = inspect.getsource(getattr(importlib.import_module(modulo), funcao))
    assert "capture_snapshots" in fonte, f"{funcao} nao chama capture_snapshots"
    assert f'"{classe}"' in fonte or f"'{classe}'" in fonte
