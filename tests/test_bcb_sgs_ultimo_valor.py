"""``ultimo_valor_sgs``: REST primeiro, SOAP quando o host REST some do DNS."""
from datetime import date

import requests

from core import bcb_sgs


class _Resp:
    def __init__(self, payload, ok=True):
        self._payload, self.ok = payload, ok

    def json(self):
        return self._payload


def test_rest_responde_usa_rest(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp([{"valor": "13,65"}]))
    monkeypatch.setattr(bcb_sgs, "baixar_sgs_soap",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("SOAP chamado")))
    assert bcb_sgs.ultimo_valor_sgs(4189) == 13.65


def test_rest_fora_do_dns_cai_no_soap_e_pega_o_ultimo(monkeypatch):
    def _nxdomain(*a, **k):
        raise requests.ConnectionError("NameResolutionError: api.bcb.gov.br")
    monkeypatch.setattr(requests, "get", _nxdomain)
    monkeypatch.setattr(bcb_sgs, "baixar_sgs_soap", lambda *a, **k: (
        {date(2026, 9, 1): 13.78, date(2026, 10, 1): 13.65}, None))
    assert bcb_sgs.ultimo_valor_sgs(4189) == 13.65


def test_duas_vias_falham_devolve_none(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp([], ok=False))
    monkeypatch.setattr(bcb_sgs, "baixar_sgs_soap",
                        lambda *a, **k: ({}, "o SOAP do SGS não respondeu"))
    assert bcb_sgs.ultimo_valor_sgs(13522, janela_dias=120) is None
