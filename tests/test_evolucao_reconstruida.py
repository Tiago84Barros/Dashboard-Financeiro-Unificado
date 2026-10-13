"""Evolução Patrimonial remontada pelas negociações para quem não tem foto de custódia.

ADELAIDE (12/10/2026) tinha 494 negociações de 01/2020 a 10/2026 e só o
relatório do Tesouro como foto: a Evolução Patrimonial mostrava um ponto só,
"out/26". A carteira passa a ser remontada no fim de cada mês e avaliada pelo
fechamento bruto da B3 (COTAHIST) e pelo PU do Tesouro Transparente.
"""
from contextlib import nullcontext
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from core import database, investimentos
from core import evolucao_reconstruida as er
from scripts import publish_precos_mensais_b3 as pub

# ── publicador ───────────────────────────────────────────────────────────────

def test_payload_um_fechamento_por_mes_e_nulo_no_mes_sem_pregao():
    linhas = [
        {"ticker": "bbas3", "mes": date(2023, 10, 1), "trade_date": date(2023, 10, 31), "close": 48.1},
        {"ticker": "BBAS3", "mes": date(2023, 10, 1), "trade_date": date(2023, 10, 30), "close": 47.0},
        {"ticker": "BBAS3", "mes": date(2023, 12, 1), "trade_date": date(2023, 12, 28), "close": 52.0},
        {"ticker": "MXRF11", "mes": date(2023, 11, 1), "trade_date": date(2023, 11, 30), "close": 0},
    ]

    p = pub.montar_payload(linhas, datetime(2024, 1, 2, tzinfo=timezone.utc))

    # outubro fica com o último pregão; novembro sem pregão é nulo, não herdado
    assert p["por_ticker"] == {"BBAS3": ["2023-10", [48.1, None, 52.0]]}
    assert p["base_ate"] == "2023-12-28"
    assert p["n_tickers"] == 1


def test_fita_parada_recusa_publicar():
    p = {"base_ate": "2026-09-20"}

    assert pub.serie_velha(p, date(2026, 9, 30)) is None
    assert "2026-09-20" in pub.serie_velha(p, date(2026, 10, 12))
    assert pub.serie_velha({"base_ate": None}, date(2026, 10, 12))


# ── leitura do preço ─────────────────────────────────────────────────────────

_PRECOS = {"BBAS3": (er._indice(2023, 10), [48.0, None, None, None, 50.0]),
           "TAEE11": (er._indice(2023, 10), [35.0, 36.0, 37.0])}


def test_mes_sem_pregao_herda_ate_dois_meses_e_depois_nao():
    assert er.fechamento(_PRECOS, "BBAS3", 2023, 10) == 48.0
    assert er.fechamento(_PRECOS, "BBAS3", 2023, 12) == 48.0
    assert er.fechamento(_PRECOS, "BBAS3", 2024, 1) is None
    assert er.fechamento(_PRECOS, "BBAS3", 2024, 2) == 50.0
    assert er.fechamento(_PRECOS, "BBAS3", 2023, 9) is None   # antes da série
    assert er.fechamento(_PRECOS, "XPTO3", 2023, 10) is None


def test_meses_vao_ate_o_mes_anterior():
    assert er.meses_entre(date(2025, 11, 20), date(2026, 2, 3)) == [
        (2025, 11), (2025, 12), (2026, 1)]


# ── renda variável ───────────────────────────────────────────────────────────

def _tx(dia, tipo, qtd, preco, ticker="BBAS3", aid="a1"):
    return {"user_id": "u", "asset_id": aid, "type": tipo, "quantity": qtd,
            "unit_price": preco, "fees": 0, "transaction_date": dia, "ticker": ticker}


_ATIVOS = {"a1": {"ticker": "BBAS3", "class": "stock", "currency": "BRL"},
           "a2": {"ticker": "TAEE11", "class": "stock", "currency": "BRL"},
           "a3": {"ticker": "SPY", "class": "etf", "currency": "USD"},
           "a4": {"ticker": "LCI", "class": "fixed_income", "currency": "BRL"}}


def test_desdobro_dobra_a_quantidade_e_o_preco_bruto_fecha_a_conta():
    # 100 a 40 em out/23; desdobro 1:1 em fev/24 (BBAS3 real: 48 -> ~27).
    precos = {"BBAS3": (er._indice(2023, 10), [48.0, 49.0, 50.0, 51.0, 26.0])}
    tx = [_tx(date(2023, 10, 5), "buy", 100, 40.0)]
    ev = [{"event_date": date(2024, 2, 10), "movement": "desdobro", "ticker": "BBAS3",
           "direction": "Credito", "quantity": 100, "asset_id": "a1"}]

    pts = er.reconstruir(tx, ev, _ATIVOS, precos, [(2023, 10), (2024, 1), (2024, 2)])

    assert [p["mes"] for p in pts] == [date(2023, 10, 31), date(2024, 1, 31), date(2024, 2, 29)]
    assert [p["valor_mercado"] for p in pts] == [4800.0, 5100.0, 5200.0]
    assert all(p["valor_investido"] == 4000.0 for p in pts)


def test_sem_preco_entra_pelo_custo_e_e_nomeado():
    tx = [_tx(date(2023, 10, 5), "buy", 10, 30.0, "TAEE11", "a2"),
          _tx(date(2023, 10, 5), "buy", 1, 1000.0, "LCI", "a4"),
          _tx(date(2023, 10, 5), "buy", 2, 100.0, "SPY", "a3"),
          _tx(date(2023, 10, 5), "buy", 5, 10.0, "NOVO3", "a9")]

    (p,) = er.reconstruir(tx, [], _ATIVOS, _PRECOS, [(2023, 10)])

    # TAEE11 a mercado (350); LCI e NOVO3 pelo custo; SPY (USD) fora
    assert p["valor_mercado"] == pytest.approx(350 + 1000 + 50)
    assert p["valor_investido"] == pytest.approx(300 + 1000 + 50)
    assert p["a_custo"] == ["LCI"]          # renda fixa: custo por método
    assert p["sem_preco"] == ["NOVO3"]      # classe desconhecida: falta de preço


def test_venda_tira_do_mes_seguinte_e_excluido_nao_entra():
    tx = [_tx(date(2023, 10, 5), "buy", 10, 30.0, "TAEE11", "a2"),
          _tx(date(2023, 11, 9), "sell", 10, 36.0, "TAEE11", "a2"),
          _tx(date(2023, 10, 5), "buy", 100, 40.0)]

    pts = er.reconstruir(tx, [], _ATIVOS, _PRECOS, [(2023, 10), (2023, 11)],
                         excluir={"a1"})

    assert [p["valor_mercado"] for p in pts] == [350.0, 0.0]


# ── Tesouro ──────────────────────────────────────────────────────────────────

def test_tesouro_compra_venda_e_pu_ou_custo():
    movs = [
        {"event_date": date(2024, 1, 10), "movement": "Compra", "chave": "TIPCA2029",
         "quantity": 2, "total_value": 6000},
        {"event_date": date(2024, 1, 10), "movement": "Transferência", "chave": "TIPCA2029",
         "quantity": 2, "total_value": 0},
        {"event_date": date(2024, 3, 2), "movement": "Venda", "chave": "TIPCA2029",
         "quantity": 1, "total_value": 3300},
        {"event_date": date(2024, 2, 1), "movement": "Compra", "chave": "TSELIC2029",
         "quantity": 1, "total_value": 14000},
    ]
    pus = {("TIPCA2029", 2024, 1): 3100.0, ("TIPCA2029", 2024, 3): 3200.0}

    r = er.reconstruir_tesouro(movs, pus, [(2023, 12), (2024, 1), (2024, 2), (2024, 3)])

    assert (2023, 12) not in r
    assert r[(2024, 1)] == {"vm": 6200.0, "vi": 6000.0, "a_custo": []}
    # fev sem PU de nenhum: tudo pelo custo, nomeado
    assert r[(2024, 2)] == {"vm": 20000.0, "vi": 20000.0, "a_custo": ["TIPCA2029", "TSELIC2029"]}
    # a venda baixa metade do custo; a transferência não mexeu na posição
    assert r[(2024, 3)] == {"vm": 3200.0 + 14000.0, "vi": 17000.0, "a_custo": ["TSELIC2029"]}


# ── roteamento em _evolucao_real ─────────────────────────────────────────────

class _Res:
    def __init__(self, linhas=(), escalar=True):
        self._l, self._e = list(linhas), escalar

    def fetchall(self):
        return list(self._l)

    def fetchone(self):
        return self._l[0] if self._l else None

    def scalar(self):
        return self._e


class _Conexao:
    def __init__(self, tem_custodia):
        self.tem_custodia = tem_custodia

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def begin_nested(self):
        return nullcontext()

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if sql == investimentos._SQL_EVOLUCAO_SNAPSHOTS:
            return _Res([SimpleNamespace(mes=date.today().replace(day=1),
                                         valor_mercado=1000.0,
                                         valor_investido_snapshot=800.0)])
        if sql == investimentos._SQL_TEM_FOTO_DE_CUSTODIA:
            return _Res(escalar=self.tem_custodia)
        if sql == investimentos._SQL_POSICOES_SNAPSHOT:
            return _Res([object()])
        return _Res()


_RECONSTRUIDOS = [
    {"mes": date(2020, 1, 31), "valor_mercado": 5824.73, "valor_investido": 5873.74,
     "sem_preco": [], "a_custo": [], "tesouro_ok": True},
    {"mes": date(2020, 2, 29), "valor_mercado": 6100.0, "valor_investido": 6000.0,
     "sem_preco": ["XPTO3"], "a_custo": ["TPRE2024"], "tesouro_ok": True},
]


def _banco(monkeypatch, tem_custodia):
    engine = SimpleNamespace(connect=lambda: _Conexao(tem_custodia))
    monkeypatch.setattr(investimentos.settings, "OWNER_USER_ID", "u1")
    monkeypatch.setattr(database, "get_engine", lambda: engine)
    monkeypatch.setattr(investimentos, "_carteira_snapshot_consolidada",
                        lambda conn, owner, rows: {"total_mercado": 2450.0,
                                                   "total_investido": 2150.0})
    monkeypatch.setattr(investimentos, "_exterior_nas_datas", lambda conn, owner, datas: {})
    monkeypatch.setattr(er, "reconstruir_do_banco",
                        lambda conn, owner: [dict(p) for p in _RECONSTRUIDOS])


def test_sem_custodia_a_curva_vem_das_negociacoes(monkeypatch):
    _banco(monkeypatch, tem_custodia=False)

    d = investimentos._evolucao_real()

    s = d["snapshots"]
    assert [p["mes_str"] for p in s] == ["2020-01", "2020-02", date.today().strftime("%Y-%m")]
    assert s[0]["valor_mercado"] == 5824.73
    assert s[-1]["valor_mercado"] == 2450.0          # o ponto de hoje é a carteira atual
    assert s[1]["sem_preco"] == ["XPTO3"] and s[1]["a_custo"] == ["TPRE2024"]
    assert d["procedencia"] == "negociacoes"
    assert d["tesouro_historico_ok"] is True


def test_com_custodia_a_curva_segue_nas_fotos(monkeypatch):
    _banco(monkeypatch, tem_custodia=True)

    d = investimentos._evolucao_real()

    assert len(d["snapshots"]) == 1
    assert "procedencia" not in d


def test_reconstrucao_que_falha_mantem_as_fotos(monkeypatch):
    _banco(monkeypatch, tem_custodia=False)

    def _quebra(conn, owner):
        raise RuntimeError("sem arquivo")
    monkeypatch.setattr(er, "reconstruir_do_banco", _quebra)

    d = investimentos._evolucao_real()

    assert len(d["snapshots"]) == 1
    assert "procedencia" not in d
