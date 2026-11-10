"""migrate_app2 segue a regra dos importadores: quantidade ausente fica ausente.

Gêmeo esquecido da correção do #678: o script de migração do App2 gravava
``quantity = 1`` e ``amount_per_unit = total`` em todo provento, porque o App2
só guarda o total.
"""
from __future__ import annotations

from types import SimpleNamespace

from data_pipeline.importers.investments import common
from migration import migrate_app2_investimentos as m

_REGISTROS = [
    {"id": 1, "asset_id": 10, "type": "rendimento", "amount": 35.2, "date": "2026-06-12"},
    {"id": 2, "asset_id": 10, "type": "jcp", "amount": 16.4, "date": "2026-06-30",
     "quantity": 53},
]
_MAPA = {10: "asset-KNCR11"}


class _Conn:
    """Grava os parâmetros de cada INSERT; o dialeto decide o portão da 083."""

    def __init__(self, dialeto: str = "sqlite"):
        self.dialect = SimpleNamespace(name=dialeto)
        self.inserts: list[dict] = []

    def execute(self, _sql, params=None):
        self.inserts.append(params)


def _texto(sql):
    return sql


def test_sem_quantidade_grava_vazio_e_com_quantidade_calcula():
    conn = _Conn()
    ins, pul, erros = m.migrate_dividends(conn, _texto, True, _REGISTROS, _MAPA, "uid")

    assert (ins, pul, erros) == (2, 0, [])
    sem, com = conn.inserts
    assert (sem["qty"], sem["apu"]) == (None, None)
    assert (com["qty"], com["apu"]) == (53.0, round(16.4 / 53, 6))
    assert [p["total"] for p in conn.inserts] == ["35.20", "16.40"]


def test_regra_e_a_mesma_dos_importadores():
    conn = _Conn()
    m.migrate_dividends(conn, _texto, True, _REGISTROS, _MAPA, "uid")
    for reg, params in zip(_REGISTROS, conn.inserts):
        assert (params["qty"], params["apu"]) == common.quantidade_e_valor_unitario(
            reg.get("quantity"), reg["amount"])


def test_sem_a_083_recusa_tudo_antes_de_qualquer_insert(monkeypatch):
    monkeypatch.setattr(common, "migracao_083_aplicada", lambda conn: False)
    conn = _Conn("postgresql")

    ins, pul, erros = m.migrate_dividends(conn, _texto, True, _REGISTROS, _MAPA, "uid")

    assert (ins, pul) == (0, 2)
    assert conn.inserts == []
    assert len(erros) == 1 and "083" in erros[0] and "inventar" in erros[0]


def test_dry_run_nao_consulta_o_portao(monkeypatch):
    def _nao_chamar(conn):
        raise AssertionError("dry run não lê o schema")

    monkeypatch.setattr(common, "migracao_083_aplicada", _nao_chamar)
    conn = _Conn("postgresql")
    ins, _pul, erros = m.migrate_dividends(conn, _texto, False, _REGISTROS, _MAPA, "uid")
    assert (ins, erros, conn.inserts) == (2, [], [])
