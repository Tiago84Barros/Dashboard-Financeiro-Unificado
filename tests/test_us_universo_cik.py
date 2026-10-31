# -*- coding: utf-8 -*-
"""EUA-A (auditoria 04/10/2026): título de dívida e preferencial no ranking,
grandes empresas sem vínculo e falha de vínculo sem rastro.

DUK ficava 'unresolved' (company_id nulo) enquanto DUKB, nota da própria Duke,
aparecia elegível com nota 65,6; MCHPP (preferencial) estava em decision_grade.
"""
from __future__ import annotations

from contextlib import contextmanager

from core.us_instrumento import (
    MOTIVO_CLASSE_ADICIONAL,
    MOTIVO_OUTRO_TICKER_DA_EMISSORA,
    motivo_exclusao_ativo,
    ticker_principal_da_emissora,
)
from data_pipeline.us import enrichment
from data_pipeline.us.edgar import EdgarProvider


# ── (a) filtro de instrumento ────────────────────────────────────────────────
def test_right_e_unit_com_hifen_sao_excluidos():
    for sym in ("AIIA-RI", "XYZ-R", "IMPX-U", "LHC-U", "ABC-UN", "SES-WT"):
        assert motivo_exclusao_ativo(sym, "common", "Services"), sym


def test_classe_a_b_v_continua_acao_ordinaria():
    for sym in ("HVT-A", "TAP-A", "MKC-V", "BRK-B"):
        assert motivo_exclusao_ativo(sym, "common", "Services") is None, sym


def test_nota_da_duke_sai_quando_a_duke_esta_vinculada():
    # DUK e DUKB no mesmo company_id: o vínculo por CIK é o que habilita a regra.
    assert motivo_exclusao_ativo(
        "DUKB", "common", "Electric Services", ("DUK", "DUKB"), name="Duke Energy"
    ) == MOTIVO_CLASSE_ADICIONAL.format(base="DUK")
    assert motivo_exclusao_ativo(
        "MCHPP", "common", "Semiconductors", ("MCHP", "MCHPP"), name="Microchip"
    ) == MOTIVO_CLASSE_ADICIONAL.format(base="MCHP")


def test_principal_da_emissora_e_o_de_maior_giro():
    # DTE Energy: nenhuma nota é prefixo da ação; só o giro as separa.
    giro = {"DTB": 2e6, "DTE": 3e8, "DTG": 1e6, "DTK": None}
    assert ticker_principal_da_emissora(giro) == "DTE"
    assert MOTIVO_OUTRO_TICKER_DA_EMISSORA.format(base="DTE")


def test_empate_ou_sem_giro_e_deterministico():
    assert ticker_principal_da_emissora({"AAB": None, "AA": None}) == "AA"
    assert ticker_principal_da_emissora({}) is None


# ── (b)/(c) vínculo por CIK e rastro em ingestion_errors ─────────────────────
class _Res:
    def __init__(self, rows=(), rowcount=0):
        self._rows, self.rowcount = list(rows), rowcount

    def fetchall(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class _Conn:
    def __init__(self, respostas):
        self.respostas, self.chamadas = list(respostas), []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.chamadas.append((sql, params))
        for chave, resp in self.respostas:
            if chave in sql:
                return resp
        return _Res()


class _Eng:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def begin(self):
        yield self.conn


def test_vinculo_so_toca_ativo_sem_company_e_usa_companhia_existente():
    conn = _Conn([("UPDATE market_us.assets", _Res(rowcount=2))])
    r = enrichment.link_assets_by_cik(_Eng(conn), {"DUK": "0001326160", "MU": "723125"})
    assert r == {"linked": 2, "map_size": 2}
    upd = next(s for s, _ in conn.chamadas if s.strip().startswith("UPDATE"))
    assert "a.company_id IS NULL" in upd and "market_us.companies" in upd


def test_mapa_vazio_nao_toca_o_banco():
    conn = _Conn([])
    assert enrichment.link_assets_by_cik(_Eng(conn), {})["linked"] == 0
    assert conn.chamadas == []


def test_nao_vinculado_vira_erro_tipado_e_nao_repete():
    conn = _Conn([
        ("analysis_status='unresolved'", _Res([("MU",), ("ZZZZ",), ("JA",)])),
        ("FROM market_us.companies", _Res([("0000000001",)])),
        ("resolved = FALSE AND error_type", _Res([("JA", enrichment.ERRO_CIK_SEM_EMPRESA)])),
    ])
    mapa = {"MU": "0000723125", "JA": "0000000009"}
    r = enrichment.log_unlinked_assets(_Eng(conn), mapa)
    ins = [p for s, p in conn.chamadas if s.strip().startswith("INSERT")]
    assert r["logged"] == 2 and len(ins) == 1
    tipos = {x["s"]: x["t"] for x in ins[0]}
    assert tipos == {"MU": enrichment.ERRO_CIK_SEM_EMPRESA,
                     "ZZZZ": enrichment.ERRO_SEM_CIK_SEC}
    assert any("resolved = TRUE" in s for s, _ in conn.chamadas)


def test_mapa_da_sec_une_as_duas_listagens(monkeypatch):
    p = EdgarProvider(user_agent="teste teste@exemplo.com")
    p._ticker_map = {"DUK": "0001326160", "ANTIGO": "0000000007"}
    monkeypatch.setattr(p, "get_universe", lambda ex: [
        {"symbol": "DUK", "cik": "0001326160"}, {"symbol": "MU", "cik": "0000723125"}])
    assert p.sec_ticker_cik_map() == {"DUK": "0001326160", "ANTIGO": "0000000007",
                                      "MU": "0000723125"}
