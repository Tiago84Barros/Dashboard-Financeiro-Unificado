"""Janela incremental do `daily` dos EUA.

Por que estes testes existem: o `daily` rebaixava `period="max"` a cada
execução e reescrevia o histórico inteiro de cada símbolo. Medido em
07/09/2026, ~13 s por símbolo contra ~0,4 s da janela — quatro horas para
atualizar 1.100 empresas. Uma rotina diária que leva quatro horas não termina,
e foi assim que a vitrine dos EUA ficou parada em 20/08 e a Criação de
Portfólio bloqueou o universo inteiro por negociabilidade não verificada.

A lentidão não aparece como erro; aparece como série parada dias depois. Então
o que se verifica aqui é a janela pedida ao provedor, não o tempo de parede.

A janela tem um custo: o Yahoo retroajusta o histórico inteiro a cada split e
a cada provento, e ela só reescreve a última semana. Até 18/11/2026 isso deixou
grupamentos sem ajuste no armazém (AEHL 0,51 contra 8,16) e tirou do retorno
total cada provento pago depois da carga inicial (KO 0,6%, VZ 1,6%). Os testes
de "retroajuste" fixam a regra que fecha esses dois buracos.
"""
from contextlib import contextmanager
from datetime import date

import pytest

from data_pipeline.us import ingest


class _ProviderFake:
    """Registra a janela pedida em cada chamada.

    ``precos`` são as barras que o Yahoo devolve hoje; por padrão, uma só.
    """

    def __init__(self, splits_por_janela=None, precos=None):
        self.chamadas = []
        self._splits = splits_por_janela or {}
        self._precos = precos or [
            {"date": "2026-09-04", "close": 10.0, "adjClose": 10.0, "volume": 100}]

    def get_prices_daily(self, symbol, start=None, end=None):
        self.chamadas.append(("prices", symbol, start))
        return [dict(p) for p in self._precos
                if start is None or p["date"] >= start]

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
def guardado(monkeypatch):
    """{data ISO: adjusted_close} que o armazém devolve para a janela."""
    banco = {}

    def _ler(conn, sym, desde):
        return {date.fromisoformat(d): v for d, v in banco.items() if d >= desde}

    monkeypatch.setattr(ingest.repo, "ajustados_guardados", _ler)
    return banco


@pytest.fixture
def repo_mudo(monkeypatch, guardado):
    """Neutraliza a gravação: o que se mede aqui é a janela, não o banco."""
    monkeypatch.setattr(ingest.repo, "upsert_prices_daily",
                        lambda conn, sym, rows: len(rows))
    monkeypatch.setattr(ingest.repo, "upsert_dividends",
                        lambda conn, sym, rows: len(rows))
    monkeypatch.setattr(ingest.repo, "upsert_splits",
                        lambda conn, sym, rows: len(rows))


def _barras(*pares):
    return [{"date": d, "close": v, "adjClose": v, "volume": 100} for d, v in pares]


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


def test_sem_retroajuste_nao_refaz_nada(repo_mudo, guardado):
    guardado.update({"2026-08-28": 10.0, "2026-08-31": 10.1, "2026-09-01": 10.2,
                     "2026-09-02": 10.3, "2026-09-03": 10.4})
    prov = _ProviderFake(precos=_barras(
        ("2026-08-28", 10.0), ("2026-08-31", 10.1), ("2026-09-01", 10.2),
        ("2026-09-02", 10.3), ("2026-09-03", 10.4), ("2026-09-04", 10.5)))
    saida = ingest.ingest_prices_only(prov, _EngineFake(), ["KO"],
                                      desde={"KO": date(2026, 9, 3)})
    assert saida["historico_refeito"] == 0
    assert [s for t, _, s in prov.chamadas if t == "prices"] == ["2026-08-27"]


def test_split_na_janela_sem_retroajuste_nao_refaz(repo_mudo, guardado):
    """O split sozinho não decide: refazer antes do Yahoo ajustar grava sem ajuste.

    Foi assim que os grupamentos de julho a outubro ficaram no armazém: o
    histórico era refeito no dia do split, o Yahoo ajustava dias depois, e
    então o split já tinha saído da janela.
    """
    guardado.update({"2026-08-28": 10.0, "2026-08-31": 10.0, "2026-09-01": 10.0})
    prov = _ProviderFake(
        splits_por_janela={"2026-08-28": [
            {"date": "2026-09-01", "numerator": 4.0, "denominator": 1.0}]},
        precos=_barras(("2026-08-28", 10.0), ("2026-08-31", 10.0),
                       ("2026-09-01", 10.0), ("2026-09-04", 2.5)))
    saida = ingest.ingest_prices_only(prov, _EngineFake(), ["KO"],
                                      desde={"KO": date(2026, 9, 4)})
    assert saida["historico_refeito"] == 0


def test_retroajuste_de_split_refaz_o_historico(repo_mudo, guardado):
    """Grupamento 1:20 que o Yahoo já ajustou: o guardado está 20x abaixo."""
    guardado.update({"2026-08-28": 0.40, "2026-08-31": 0.41, "2026-09-01": 8.1,
                     "2026-09-02": 8.2, "2026-09-03": 8.3})
    prov = _ProviderFake(precos=_barras(
        ("2026-08-28", 8.0), ("2026-08-31", 8.2), ("2026-09-01", 8.1),
        ("2026-09-02", 8.2), ("2026-09-03", 8.3), ("2026-09-04", 8.4)))
    saida = ingest.ingest_prices_only(prov, _EngineFake(), ["AEHL"],
                                      desde={"AEHL": date(2026, 9, 4)})
    assert saida["historico_refeito"] == 1
    assert [s for t, _, s in prov.chamadas if t == "prices"] == ["2026-08-28", None]
    # provento e split acompanham a descida completa (mesma chave de memoização)
    assert {s for t, _, s in prov.chamadas if t != "prices"} == {None}


def test_retroajuste_de_provento_refaz_o_historico(repo_mudo, guardado):
    """Provento de 1,5%: o Yahoo baixa todo o ajustado anterior à data ex."""
    guardado.update({"2026-08-28": 40.6, "2026-08-31": 40.6, "2026-09-01": 40.0,
                     "2026-09-02": 40.1, "2026-09-03": 40.2})
    prov = _ProviderFake(precos=_barras(
        ("2026-08-28", 40.0), ("2026-08-31", 40.0), ("2026-09-01", 40.0),
        ("2026-09-02", 40.1), ("2026-09-03", 40.2), ("2026-09-04", 40.3)))
    saida = ingest.ingest_prices_only(prov, _EngineFake(), ["VZ"],
                                      desde={"VZ": date(2026, 9, 4)})
    assert saida["historico_refeito"] == 1


def test_revisao_da_barra_mais_nova_nao_refaz(repo_mudo, guardado):
    """O Yahoo revisa o fechamento recente; é para isso que existe a sobreposição."""
    guardado.update({"2026-08-28": 10.0, "2026-08-31": 10.0, "2026-09-01": 10.0,
                     "2026-09-03": 11.0, "2026-09-04": 12.0})
    prov = _ProviderFake(precos=_barras(
        ("2026-08-28", 10.0), ("2026-08-31", 10.0), ("2026-09-01", 10.0),
        ("2026-09-03", 11.5), ("2026-09-04", 12.4)))
    saida = ingest.ingest_prices_only(prov, _EngineFake(), ["KO"],
                                      desde={"KO": date(2026, 9, 4)})
    assert saida["historico_refeito"] == 0


def test_split_vigiado_alarga_a_janela_ate_antes_dele(repo_mudo, guardado):
    """O retroajuste tardio só é visto se a barra antes do split estiver na janela."""
    guardado.update({"2026-07-01": 0.5, "2026-07-02": 0.5, "2026-09-01": 9.0,
                     "2026-09-02": 9.0, "2026-09-03": 9.0})
    prov = _ProviderFake(precos=_barras(
        ("2026-07-01", 10.0), ("2026-07-02", 10.0), ("2026-09-01", 9.0),
        ("2026-09-02", 9.0), ("2026-09-03", 9.0), ("2026-09-04", 9.0)))
    saida = ingest.ingest_prices_only(
        prov, _EngineFake(), ["SKIN"], desde={"SKIN": date(2026, 9, 4)},
        splits_vigiados={"SKIN": date(2026, 7, 15)})
    precos = [s for t, _, s in prov.chamadas if t == "prices"]
    assert precos[0] == "2026-06-15"
    assert saida["historico_refeito"] == 1


def test_sem_split_vigiado_o_passado_velho_fica_fora_da_janela(repo_mudo, guardado):
    """Contraprova: sem o alargamento, a janela curta não vê o erro de julho."""
    guardado.update({"2026-07-01": 0.5, "2026-07-02": 0.5, "2026-09-01": 9.0,
                     "2026-09-02": 9.0, "2026-09-03": 9.0})
    prov = _ProviderFake(precos=_barras(
        ("2026-07-01", 10.0), ("2026-07-02", 10.0), ("2026-09-01", 9.0),
        ("2026-09-02", 9.0), ("2026-09-03", 9.0), ("2026-09-04", 9.0)))
    saida = ingest.ingest_prices_only(prov, _EngineFake(), ["SKIN"],
                                      desde={"SKIN": date(2026, 9, 4)})
    assert saida["historico_refeito"] == 0


# ── Comparação pura ───────────────────────────────────────────────────────

def test_passado_retroajustado_devolve_a_primeira_data_divergente():
    guardado = {date(2026, 9, 1): 10.0, date(2026, 9, 2): 5.0,
                date(2026, 9, 3): 9.0, date(2026, 9, 4): 9.0, date(2026, 9, 5): 9.0}
    baixado = _barras(("2026-09-01", 10.0), ("2026-09-02", 10.0),
                      ("2026-09-03", 18.0), ("2026-09-04", 9.0))
    assert ingest.passado_retroajustado(guardado, baixado) == date(2026, 9, 2)


def test_passado_retroajustado_ignora_nulo_zero_e_ruido():
    guardado = {date(2026, 9, 1): 0.0, date(2026, 9, 2): 10.0,
                date(2026, 9, 3): 10.0, date(2026, 9, 4): 1.0, date(2026, 9, 5): 1.0}
    baixado = [{"date": "2026-09-01", "adjClose": 3.0},
               {"date": "2026-09-02", "adjClose": None, "close": 10.0000001},
               {"date": "2026-09-03", "adjClose": float("nan")}]
    assert ingest.passado_retroajustado(guardado, baixado) is None
    assert ingest.passado_retroajustado({}, baixado) is None


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
