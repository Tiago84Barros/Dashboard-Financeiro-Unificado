"""update_bcb: coleta vazia do BCB/SGS reprova; REST fora do ar cai no SOAP.

Em 03/10/2026 o ``api.bcb.gov.br`` sumiu do DNS: as sete séries voltavam com
0 pontos e o workflow terminava verde, porque o orquestrador somava a falha às
dos outros jobs e saía ``partial_success``.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
import requests

from core import bcb_sgs, rentabilidade
from data_pipeline import orchestrator as orq
from data_pipeline.jobs import update_bcb
from scripts import seed_macro_bcb

import run_data_updates

SERIES = list(seed_macro_bcb.SERIES)


class _Conexao:
    def __init__(self):
        self.sql: list[str] = []

    def execute(self, stmt, params=None):
        self.sql.append(str(stmt))


class _Engine:
    def __init__(self):
        self.conexao = _Conexao()

    def begin(self):
        engine = self

        class _Ctx:
            def __enter__(self):
                return engine.conexao

            def __exit__(self, *exc):
                return False

        return _Ctx()


@pytest.fixture
def engine(monkeypatch):
    eng = _Engine()
    monkeypatch.setattr("data_pipeline.utils.db_utils.get_pipeline_engine", lambda: eng)
    return eng


def _macro_anual() -> pd.DataFrame:
    linha = {"ano": 2025, "data": pd.Timestamp("2025-12-31")}
    linha.update({c: 1.0 for c in seed_macro_bcb.MACRO_COLUMNS})
    return pd.DataFrame([linha])


# ── avaliar_contagens ───────────────────────────────────────────────────────

def test_todas_as_series_vazias_e_falha():
    status, msg = update_bcb.avaliar_contagens({s: 0 for s in SERIES})
    assert status == "failed"
    assert "todas as 7 séries" in msg


def test_sem_serie_nenhuma_tambem_e_falha():
    assert update_bcb.avaliar_contagens({})[0] == "failed"


def test_algumas_vazias_e_parcial_com_as_series_nomeadas():
    contagens = {s: 10 for s in SERIES} | {"pib": 0, "icc": 0}
    status, msg = update_bcb.avaliar_contagens(contagens)
    assert status == "partial_success"
    assert msg == "Séries sem pontos: icc, pib"


def test_todas_com_pontos_e_sucesso():
    assert update_bcb.avaliar_contagens({s: 5 for s in SERIES}) == ("success", None)


# ── run() ───────────────────────────────────────────────────────────────────

def test_resposta_vazia_do_sgs_reprova_como_fatal_sem_gravar(engine, monkeypatch):
    """REST e SOAP fora do ar: cada série vem vazia."""
    monkeypatch.setattr(seed_macro_bcb, "_fetch_sgs_series",
                        lambda name, code, start, end: pd.DataFrame(columns=[name]))
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)

    r = update_bcb.run()

    assert r["status"] == "failed"
    assert r["fatal"] is True
    assert r["series_pontos"] == {s: 0 for s in SERIES}
    assert "0 pontos em todas" in r["error_message"]
    assert engine.conexao.sql == []  # nada gravado


def test_coleta_parcial_grava_e_avisa(engine, monkeypatch):
    contagens = {s: 12 for s in SERIES} | {"divida_publica": 0}
    monkeypatch.setattr(seed_macro_bcb, "_fetch_macro",
                        lambda start, end: (_macro_anual(), contagens))

    r = update_bcb.run()

    assert r["status"] == "partial_success"
    assert not r.get("fatal")
    assert r["error_message"] == "Séries sem pontos: divida_publica"
    assert r["records_inserted"] == 1
    assert any("INSERT INTO public.macro" in s for s in engine.conexao.sql)


def test_coleta_completa_e_sucesso(engine, monkeypatch):
    monkeypatch.setattr(seed_macro_bcb, "_fetch_macro",
                        lambda start, end: (_macro_anual(), {s: 12 for s in SERIES}))
    r = update_bcb.run()
    assert r["status"] == "success"
    assert r["error_message"] is None


def test_upsert_nao_apaga_coluna_que_nao_veio():
    """Série ausente numa coleta parcial chega NULL e não pode zerar o gravado."""
    conn = _Conexao()
    seed_macro_bcb._upsert_macro(conn, _macro_anual(), apply=True)
    upsert = next(s for s in conn.sql if "ON CONFLICT" in s)
    for col in ("data", *seed_macro_bcb.MACRO_COLUMNS):
        assert f"{col} = COALESCE(EXCLUDED.{col}, public.macro.{col})" in upsert


# ── código de saída do run_data_updates ─────────────────────────────────────

def _resultado(*jobs):
    ok = sum(1 for j in jobs if j["status"] in ("success", "partial_success"))
    falha = sum(1 for j in jobs if j["status"] == "failed")
    status = "success" if not falha else ("partial_success" if ok else "failed")
    return {"status": status, "results": list(jobs)}


def test_job_fatal_reprova_mesmo_com_pipeline_parcial():
    resultado = _resultado(
        {"job_name": "update_fx_rates", "status": "success"},
        {"job_name": "update_bcb", "status": "failed", "fatal": True},
    )
    assert resultado["status"] == "partial_success"
    assert run_data_updates.codigo_saida(resultado) == 1


def test_falha_comum_em_pipeline_parcial_continua_passando():
    resultado = _resultado(
        {"job_name": "update_fx_rates", "status": "success"},
        {"job_name": "update_noticias", "status": "failed"},
    )
    assert run_data_updates.codigo_saida(resultado) == 0


def test_pipeline_so_com_sucesso_passa():
    assert run_data_updates.codigo_saida(
        _resultado({"job_name": "update_bcb", "status": "success"})) == 0


def test_cmd_run_sai_1_com_bcb_vazio(monkeypatch, capsys):
    """De ponta a ponta pelo CLI que o workflow chama."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    resultado = _resultado(
        {"job_name": "update_fx_rates", "status": "success"},
        {"job_name": "update_bcb", "status": "failed", "fatal": True,
         "error_message": "BCB/SGS devolveu 0 pontos em todas as 7 séries"},
    )
    monkeypatch.setattr(orq, "run_updates", lambda **k: resultado)
    args = run_data_updates.argparse.Namespace(source=None, force=False, json=False)
    assert run_data_updates.cmd_run(args) == 1
    assert "::error title=update_bcb sem dados::" in capsys.readouterr().out


# ── reserva SOAP (core.bcb_sgs) ─────────────────────────────────────────────

# Recorte real de getValoresSeriesXML: o XML das séries vem escapado.
_SOAP_DIARIA = (
    '<soapenv:Envelope><soapenv:Body><ns1:getValoresSeriesXMLResponse>'
    '<getValoresSeriesXMLReturn>&lt;?xml version="1.0" encoding="ISO-8859-1"?&gt;'
    "&lt;SERIES&gt;&lt;SERIE ID='432'&gt;"
    "&lt;ITEM&gt;&lt;DATA&gt;1/1/2010&lt;/DATA&gt;&lt;VALOR&gt;8.75&lt;/VALOR&gt;"
    "&lt;BLOQUEADO&gt;false&lt;/BLOQUEADO&gt;&lt;/ITEM&gt;"
    "&lt;ITEM&gt;&lt;DATA&gt;15/10/2026&lt;/DATA&gt;&lt;VALOR&gt;12,5&lt;/VALOR&gt;"
    "&lt;BLOQUEADO&gt;false&lt;/BLOQUEADO&gt;&lt;/ITEM&gt;"
    "&lt;ITEM&gt;&lt;DATA&gt;16/10/2026&lt;/DATA&gt;&lt;VALOR&gt;&lt;/VALOR&gt;"
    "&lt;BLOQUEADO&gt;false&lt;/BLOQUEADO&gt;&lt;/ITEM&gt;"
    "&lt;/SERIE&gt;&lt;/SERIES&gt;</getValoresSeriesXMLReturn>"
    "</ns1:getValoresSeriesXMLResponse></soapenv:Body></soapenv:Envelope>"
)
_SOAP_MENSAL = (
    "&lt;SERIE ID='433'&gt;&lt;ITEM&gt;&lt;DATA&gt;1/2026&lt;/DATA&gt;"
    "&lt;VALOR&gt;0.33&lt;/VALOR&gt;&lt;/ITEM&gt;"
    "&lt;ITEM&gt;&lt;DATA&gt;12/2025&lt;/DATA&gt;&lt;VALOR&gt;0.52&lt;/VALOR&gt;&lt;/ITEM&gt;"
)


def test_parse_soap_diaria_com_virgula_e_valor_vazio():
    assert bcb_sgs.parse_sgs_xml(_SOAP_DIARIA) == {
        date(2010, 1, 1): 8.75, date(2026, 10, 15): 12.5}


def test_parse_soap_mensal_vira_dia_1_como_na_rest():
    assert bcb_sgs.parse_sgs_xml(_SOAP_MENSAL) == {
        date(2026, 1, 1): 0.33, date(2025, 12, 1): 0.52}


class _Resposta:
    def __init__(self, texto, status=200):
        self.text, self.status_code, self.ok = texto, status, status < 400


def test_soap_serie_inexistente_devolve_motivo(monkeypatch):
    falha = ("<soapenv:Fault><faultcode>soapenv:Server</faultcode>"
             "<faultstring>Series 999999 not found</faultstring></soapenv:Fault>")
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resposta(falha, 500))
    serie, motivo = bcb_sgs.baixar_sgs_soap(999999, date(2026, 1, 1), date(2026, 2, 1))
    assert serie == {}
    assert "Series 999999 not found" in motivo


def test_soap_sem_rede_devolve_motivo(monkeypatch):
    def cai(*a, **k):
        raise requests.exceptions.ConnectionError("dns")
    monkeypatch.setattr(requests, "post", cai)
    assert bcb_sgs.baixar_sgs_soap(432, date(2026, 1, 1), date(2026, 2, 1)) == (
        {}, "o SOAP do SGS não respondeu (ConnectionError)")


def test_rest_sem_dns_cai_no_soap_sem_insistir(monkeypatch):
    """NXDOMAIN encerra a REST na 1ª tentativa e a série inteira vem do SOAP."""
    tentativas = []

    def sem_dns(*a, **k):
        tentativas.append(a)
        raise requests.exceptions.ConnectionError("NameResolutionError")

    monkeypatch.setattr(requests, "get", sem_dns)
    monkeypatch.setattr(seed_macro_bcb.time, "sleep", lambda s: None)
    monkeypatch.setattr(bcb_sgs, "baixar_sgs_soap",
                        lambda code, ini, fim, **k: ({date(2026, 10, 1): 15.0}, None))
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)

    df = seed_macro_bcb._fetch_sgs_series("selic", 432, date(2010, 1, 1), date(2026, 10, 3))

    assert len(tentativas) == 1
    assert df["selic"].to_dict() == {pd.Timestamp("2026-10-01"): 15.0}


def test_trecho_perdido_na_rest_e_completado_pelo_soap(monkeypatch):
    """A REST traz um trecho e perde outro: o SOAP completa, a REST prevalece."""
    class _Rest:
        status_code, text = 200, "x"

        def raise_for_status(self):
            pass

        def json(self):
            return [{"data": "01/10/2026", "valor": "15,0"}]

    respostas = iter([_Rest()])  # 1º trecho de 8 anos responde; os demais, 503
    monkeypatch.setattr(requests, "get",
                        lambda *a, **k: next(respostas, _Resposta("", 503)))
    monkeypatch.setattr(seed_macro_bcb.time, "sleep", lambda s: None)
    monkeypatch.setattr(bcb_sgs, "baixar_sgs_soap", lambda code, ini, fim, **k: (
        {date(2026, 10, 1): 99.0, date(2012, 5, 2): 9.0}, None))
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)

    df = seed_macro_bcb._fetch_sgs_series("selic", 432, date(2010, 1, 1), date(2026, 10, 3))

    assert df["selic"].to_dict() == {pd.Timestamp("2012-05-02"): 9.0,
                                     pd.Timestamp("2026-10-01"): 15.0}


def test_cdi_cai_no_soap_quando_a_rest_falha(monkeypatch):
    def sem_dns(*a, **k):
        raise requests.exceptions.ConnectionError("NameResolutionError")

    monkeypatch.setattr(requests, "get", sem_dns)
    monkeypatch.setattr(bcb_sgs, "baixar_sgs_soap",
                        lambda code, ini, fim, **k: ({date(2026, 10, 1): 0.055}, None)
                        if code == 12 else ({}, "série errada"))
    assert rentabilidade.baixar_cdi_bcb(date(2026, 9, 1), date(2026, 10, 3)) == (
        {date(2026, 10, 1): 0.055}, None)


def test_cdi_com_rest_e_soap_fora_nomeia_os_dois(monkeypatch):
    def sem_dns(*a, **k):
        raise requests.exceptions.ConnectionError("dns")

    monkeypatch.setattr(requests, "get", sem_dns)
    monkeypatch.setattr(bcb_sgs, "baixar_sgs_soap",
                        lambda *a, **k: ({}, "o SOAP do SGS respondeu HTTP 502"))
    serie, motivo = rentabilidade.baixar_cdi_bcb(date(2026, 9, 1), date(2026, 10, 3))
    assert serie == {}
    assert motivo == "o BCB não respondeu (ConnectionError); o SOAP do SGS respondeu HTTP 502"
