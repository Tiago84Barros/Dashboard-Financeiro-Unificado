"""Trimestres dos EUA para o chat, publicados como arquivo.

O chat da empresa americana recebia só a contagem de demonstrativos. Agora a
ordem é armazém direto -> túnel -> arquivo publicado, e a conta é sempre a de
`resumo_para_prompt`. Sem banco: o SQL é dublê.
"""
from datetime import date, datetime, timezone

import pytest

from core import llm_context_us as ctx
from core import us_detalhe_armazem as det
from scripts import publish_detalhe_eua as pub

HOJE = date(2026, 11, 8)


def _bruto():
    """Forma de `ler_detalhe`, com tipos do banco (date, Decimal-like)."""
    trimestres = [
        {"fiscal_year": 2026, "fiscal_quarter": 4, "reference_date": date(2026, 9, 27),
         "available_at": date(2026, 10, 31), "revenue": 102_466_000_000.0,
         "gross_profit": 48_000_000_000.0, "operating_income": 32_000_000_000.0,
         "net_income": 27_466_000_000.0, "eps_diluted": 1.8512345,
         "operating_cash_flow": 30e9, "capex": -3e9, "free_cash_flow": 27e9},
        {"fiscal_year": 2025, "fiscal_quarter": 4, "reference_date": date(2025, 9, 27),
         "available_at": date(2025, 10, 31), "revenue": 94_930_000_000.0,
         "gross_profit": None, "operating_income": None, "net_income": 14_736_000_000.0,
         "eps_diluted": 0.97, "operating_cash_flow": None, "capex": None,
         "free_cash_flow": None},
    ]
    return {"AAPL": {"precos": [], "trimestres": trimestres,
                     "exercicios": [dict(trimestres[0], fiscal_quarter=None)],
                     "proventos": [{"ex_date": date(2026, 8, 11), "valor": 0.26}]},
            "SEMDADO": {"precos": [], "trimestres": [], "exercicios": [],
                        "proventos": []}}


def test_arquivo_guarda_as_linhas_cruas_sem_o_que_nao_existe():
    arq = det.para_arquivo(_bruto())
    assert "SEMDADO" not in arq
    t = arq["AAPL"]["trimestres"][0]
    assert t["reference_date"] == "2026-09-27" and t["revenue"] == 102_466_000_000
    assert t["eps_diluted"] == 1.8512
    assert "gross_profit" not in arq["AAPL"]["trimestres"][1]
    assert arq["AAPL"]["proventos"] == [{"ex_date": "2026-08-11", "valor": 0.26}]


@pytest.fixture()
def arquivo(tmp_path, monkeypatch):
    payload = {"versao": 1, "gerado_em": "2026-11-08T12:00:00+00:00",
               "base_ate": "2026-10-31", "por_ticker": det.para_arquivo(_bruto())}
    caminho = tmp_path / "detalhe_eua.json.gz"
    pub.serializar(payload, caminho)
    monkeypatch.setattr(det, "ARQUIVO", caminho)
    return caminho


def test_leitura_do_arquivo_devolve_a_forma_de_ler_detalhe(arquivo):
    detalhe, origem = det.ler_do_arquivo(["aapl", "MSFT"])
    assert set(detalhe) == {"AAPL", "MSFT"}
    assert detalhe["AAPL"]["precos"] == [] and len(detalhe["AAPL"]["trimestres"]) == 2
    assert detalhe["MSFT"]["trimestres"] == []
    assert "demonstrativos até 2026-10-31" in origem


def test_mesma_conta_do_tunel_e_preco_ausente_dito_como_tal(arquivo):
    detalhe, origem = det.ler_do_arquivo(["AAPL"])
    texto = det.resumo_para_prompt(detalhe, origem=origem, hoje=HOJE, com_precos=False)
    assert "FY2026Q4 (27/09/2026); receita 102.47 bi; lucro 27.47 bi" in texto
    assert "a/a receita +7.9%" in texto and "a/a lucro +86.4%" in texto
    assert "não vem no arquivo publicado" in texto
    assert "o armazém não tem série recente" not in texto


def test_semente_sem_base_nao_conta_como_arquivo(tmp_path, monkeypatch):
    caminho = tmp_path / "s.json.gz"
    pub.serializar({"gerado_em": "2000-01-01", "base_ate": None, "por_ticker": {}},
                   caminho)
    assert det.ler_do_arquivo(["AAPL"], caminho=caminho) is None


def _sem_tunel(monkeypatch, *, levanta=False):
    import core.armazem_remoto as remoto
    import core.us_read as us_read
    monkeypatch.setattr(us_read, "_db_is_local", lambda: False)

    def detalhe_eua(_alvo):
        if levanta:
            raise ConnectionError("conexão recusada")
        return None

    monkeypatch.setattr(remoto, "detalhe_eua", detalhe_eua)


@pytest.mark.parametrize("levanta", [False, True])
def test_sem_tunel_o_chat_le_o_arquivo(arquivo, monkeypatch, levanta):
    _sem_tunel(monkeypatch, levanta=levanta)
    captura = {}
    texto = ctx.get_warehouse_detail_context(["AAPL"], captura=captura)
    assert "arquivo publicado em 2026-11-08" in texto and "FY2026Q4" in texto
    assert captura["AAPL"]["trimestres"]


def test_sem_tunel_e_sem_arquivo_a_falta_e_nomeada(tmp_path, monkeypatch):
    _sem_tunel(monkeypatch)
    monkeypatch.setattr(det, "ARQUIVO", tmp_path / "nao_existe.json.gz")
    texto = ctx.get_warehouse_detail_context(["AAPL"])
    assert "túnel não configurado" in texto and "ainda não existe" in texto


def test_publicador_le_em_lotes_sem_preco_e_sem_teto(monkeypatch):
    chamadas = []

    def falso(engine, simbolos, *, hoje, limite, com_precos):
        chamadas.append((list(simbolos), limite, com_precos))
        return {s: _bruto()["AAPL"] for s in simbolos}

    monkeypatch.setattr(det, "ler_detalhe", falso)
    payload = pub.montar(object(), ["A", "B", "C"], hoje=HOJE, lote=2,
                         agora=datetime(2026, 11, 8, tzinfo=timezone.utc))
    assert [c[0] for c in chamadas] == [["A", "B"], ["C"]]
    assert all(c[1] is None and c[2] is False for c in chamadas)
    assert set(payload["por_ticker"]) == {"A", "B", "C"}
    assert payload["base_ate"] == "2026-10-31"
