"""Janela incremental do `daily` dos EUA.

Por que estes testes existem: o `daily` rebaixava `period="max"` a cada
execução e reescrevia o histórico inteiro de cada símbolo. Medido em
07/09/2026, ~13 s por símbolo contra ~0,4 s da janela — quatro horas para
atualizar 1.100 empresas. Uma rotina diária que leva quatro horas não termina,
e foi assim que a vitrine dos EUA ficou parada em 20/08 e a Criação de
Portfólio bloqueou o universo inteiro por negociabilidade não verificada.

A lentidão não aparece como erro; aparece como série parada dias depois. Então
o que se verifica aqui é a janela pedida ao provedor, não o tempo de parede.
"""
from contextlib import contextmanager
from datetime import date

import pytest

from data_pipeline.us import ingest


class _ProviderFake:
    """Registra a janela pedida em cada chamada."""

    def __init__(self, splits_por_janela=None):
        self.chamadas = []
        self._splits = splits_por_janela or {}

    def get_prices_daily(self, symbol, start=None, end=None):
        self.chamadas.append(("prices", symbol, start))
        return [{"date": "2026-09-04", "close": 10.0, "volume": 100}]

    def get_dividends(self, symbol, start=None, end=None):
        self.chamadas.append(("dividends", symbol, start))
        return []

    def get_splits(self, symbol, start=None, end=None):
        self.chamadas.append(("splits", symbol, start))
        return list(self._splits.get(start, []))


class _EngineFake:
    @contextmanager
    def begin(self):
        yield object()


@pytest.fixture
def repo_mudo(monkeypatch):
    """Neutraliza a gravação: o que se mede aqui é a janela, não o banco."""
    monkeypatch.setattr(ingest.repo, "upsert_prices_daily",
                        lambda conn, sym, rows: len(rows))
    monkeypatch.setattr(ingest.repo, "upsert_dividends",
                        lambda conn, sym, rows: len(rows))
    monkeypatch.setattr(ingest.repo, "upsert_splits",
                        lambda conn, sym, rows: len(rows))


# ── Janela ────────────────────────────────────────────────────────────────

def test_janela_recua_a_sobreposicao():
    """O Yahoo revisa a barra recente; pedir do dia seguinte congela a 1ª versão."""
    assert ingest._janela({"KO": date(2026, 9, 4)}, "KO", overlap_dias=7) == "2026-08-28"


def test_simbolo_fora_do_mapa_baixa_historico_inteiro():
    assert ingest._janela({"KO": date(2026, 9, 4)}, "PEP") is None
    assert ingest._janela(None, "KO") is None
    assert ingest._janela({}, "KO") is None


# ── Passagem incremental ──────────────────────────────────────────────────

def test_com_desde_o_provedor_recebe_janela(repo_mudo):
    prov = _ProviderFake()
    ingest.ingest_prices_only(prov, _EngineFake(), ["KO"],
                              desde={"KO": date(2026, 9, 4)})
    janelas = {start for _, _, start in prov.chamadas}
    assert janelas == {"2026-08-28"}


def test_sem_desde_continua_baixando_tudo(repo_mudo):
    """Quem nunca teve série precisa do histórico; a janela não pode virar padrão."""
    prov = _ProviderFake()
    ingest.ingest_prices_only(prov, _EngineFake(), ["KO"])
    assert {start for _, _, start in prov.chamadas} == {None}


def test_preco_provento_e_split_pedem_a_mesma_janela(repo_mudo):
    """Janelas divergentes quebram a memoização e dobram o download por símbolo."""
    prov = _ProviderFake()
    ingest.ingest_prices_only(prov, _EngineFake(), ["KO"],
                              desde={"KO": date(2026, 9, 4)})
    por_tipo = {tipo: start for tipo, _, start in prov.chamadas}
    assert set(por_tipo) == {"prices", "dividends", "splits"}
    assert len(set(por_tipo.values())) == 1


def test_split_na_janela_refaz_o_historico(repo_mudo):
    """Desdobramento retroajusta a série toda; a janela deixaria o passado velho."""
    prov = _ProviderFake(splits_por_janela={
        "2026-08-28": [{"date": "2026-09-01", "numerator": 4.0, "denominator": 1.0}]})
    saida = ingest.ingest_prices_only(prov, _EngineFake(), ["KO"],
                                      desde={"KO": date(2026, 9, 4)})
    assert saida["historico_refeito"] == 1
    precos = [start for tipo, _, start in prov.chamadas if tipo == "prices"]
    assert precos == [None]


def test_sem_split_nao_refaz_nada(repo_mudo):
    prov = _ProviderFake()
    saida = ingest.ingest_prices_only(prov, _EngineFake(), ["KO"],
                                      desde={"KO": date(2026, 9, 4)})
    assert saida["historico_refeito"] == 0


def test_falha_de_um_simbolo_nao_derruba_os_outros(repo_mudo):
    class _Explode(_ProviderFake):
        def get_prices_daily(self, symbol, start=None, end=None):
            if symbol == "RUIM":
                raise RuntimeError("yahoo fora do ar")
            return super().get_prices_daily(symbol, start, end)

    saida = ingest.ingest_prices_only(_Explode(), _EngineFake(), ["RUIM", "KO"])
    assert saida["processed"] == 2 and saida["with_prices"] == 1


# ── Contrato do provedor do yfinance ──────────────────────────────────────

def test_yfinance_serve_os_tres_de_um_download_so():
    """Um download por (símbolo, janela): 3 descidas disparam o rate-limit."""
    from data_pipeline.us.prices_yf import YFinanceProvider

    class _TickerFake:
        criados = 0

        def __init__(self, *_a, **_k):
            type(self).criados += 1

        def history(self, **_k):
            import pandas as pd
            idx = pd.to_datetime(["2026-09-04"])
            return pd.DataFrame({"Open": [1.0], "High": [1.0], "Low": [1.0],
                                 "Close": [1.0], "Adj Close": [1.0],
                                 "Volume": [10], "Dividends": [0.0],
                                 "Stock Splits": [0.0]}, index=idx)

    prov = YFinanceProvider(ticker_factory=lambda s: _TickerFake())
    prov.get_prices_daily("KO", "2026-08-28", None)
    prov.get_dividends("KO", "2026-08-28", None)
    prov.get_splits("KO", "2026-08-28", None)
    assert _TickerFake.criados == 1
