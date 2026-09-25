"""O "Valor de Mercado Atual" do Histórico é o "Patrimônio Total" do Dashboard.

As duas telas respondiam "quanto vale a carteira hoje" com montagens
diferentes: o Dashboard somava o snapshot XP e as posições USD fora dele
(ETFs da Nomad), a Evolução Patrimonial montava só o snapshot. O Histórico
saía menor pela fatia do exterior.
"""
from datetime import date
from types import SimpleNamespace

import pytest

import core.database as database
import core.investimentos as investimentos

_SNAP_XP = SimpleNamespace(mes=date(2020, 1, 31), valor_mercado=900.0,
                           valor_investido_snapshot=800.0)
_ETF_NOMAD = SimpleNamespace(
    ticker="SPY", quantity=1.0, average_price=100.0, total_invested=100.0,
    current_price=110.0, current_price_timestamp=None, usd_brl_rate=5.0,
    asset_name="SPY", asset_class="etf", currency="USD", sector=None,
)
_POSICAO_XP = object()


def _carteira_so_do_snapshot(rows, tx_costs=None, precos_manuais=None):
    return {
        "posicoes": [{"ticker": "ITUB4", "classe": "Ações", "setor": "Bancos",
                      "cor": "#fff", "total_investido": 800.0,
                      "valor_mercado": 1000.0, "cotacao_fonte": "live"}],
        "total_investido": 800.0,
        "total_mercado": 1000.0,
        "num_ativos": 1,
        "rentabilidade_total_pct": 25.0,
        "por_classe": [],
        "por_setor": [],
    }


class _Resultado:
    def __init__(self, linhas):
        self._linhas = linhas

    def fetchall(self):
        return list(self._linhas)

    def fetchone(self):
        return self._linhas[0] if self._linhas else None

    def scalar(self):
        return True


class _Conexao:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "information_schema.tables" in sql:
            return _Resultado([True])
        if sql == investimentos._SQL_POSICOES_SNAPSHOT:
            return _Resultado([_POSICAO_XP])
        if sql == investimentos._SQL_POSICOES_EXTRAS_FORA_SNAPSHOT:
            return _Resultado([_ETF_NOMAD])
        if sql == investimentos._SQL_EVOLUCAO_SNAPSHOTS:
            return _Resultado([_SNAP_XP])
        return _Resultado([])


class _Engine:
    def connect(self):
        return _Conexao()


@pytest.fixture
def banco_falso(monkeypatch):
    monkeypatch.setattr(investimentos.settings, "OWNER_USER_ID", "u1")
    monkeypatch.setattr(database, "get_engine", lambda: _Engine())
    monkeypatch.setattr(investimentos, "_montar_carteira_snapshot", _carteira_so_do_snapshot)
    monkeypatch.setattr(investimentos, "cambio_medio_de_aquisicao", lambda conn, owner: {})
    monkeypatch.setattr(investimentos, "listar_precos_manuais", lambda owner, conn: {})


def test_historico_e_dashboard_dao_o_mesmo_patrimonio(banco_falso):
    carteira = investimentos._carteira_real()
    evolucao = investimentos._evolucao_real()

    # 1000 do snapshot + 1 SPY a 110 USD x 5,00
    assert carteira["total_mercado"] == pytest.approx(1550.0)
    assert evolucao["total_mercado"] == pytest.approx(carteira["total_mercado"])
    assert evolucao["total_investido"] == pytest.approx(carteira["total_investido"])
    assert evolucao["snapshots"][-1]["valor_mercado"] == pytest.approx(1550.0)


def test_foto_historica_nao_recebe_o_exterior_de_hoje(banco_falso):
    evolucao = investimentos._evolucao_real()

    assert evolucao["snapshots"][0]["mes_str"] == "2020-01"
    assert evolucao["snapshots"][0]["valor_mercado"] == pytest.approx(900.0)


# ── Foto sem custo não vira custo zero ────────────────────────────────────────


def test_sql_so_soma_custo_quando_toda_posicao_tem_custo():
    sql = " ".join(investimentos._SQL_EVOLUCAO_SNAPSHOTS.lower().split())

    assert "bool_and(coalesce(pps.invested_value, 0) > 0)" in sql
    assert "sum(coalesce(pps.invested_value, 0))" not in sql


def test_foto_sem_custo_vira_lacuna_e_nao_zero():
    snaps = [
        SimpleNamespace(mes=date(2020, 12, 31), valor_mercado=600.0,
                        valor_investido_snapshot=None),
        SimpleNamespace(mes=date(2023, 12, 31), valor_mercado=300.0,
                        valor_investido_snapshot=250.0),
    ]
    d = investimentos._montar_evolucao_snapshot(snaps, [], None, [])

    assert d["snapshots"][0]["valor_investido"] is None
    assert d["snapshots"][1]["valor_investido"] == pytest.approx(250.0)
    assert d["total_investido"] == pytest.approx(250.0)

    from views.investimentos import _fig_evolucao_patrimonial
    fig = _fig_evolucao_patrimonial(d["snapshots"])
    investido = next(t for t in fig.data if t.name == "Valor Investido")
    assert investido.y[0] is None


def test_grafico_do_dashboard_geral_aceita_custo_desconhecido():
    from views.dashboard_geral import _fig_evolucao_investimentos

    fig = _fig_evolucao_investimentos({"snapshots": [
        {"label": "Dez/20", "valor_mercado": 600.0, "valor_investido": None},
        {"label": "Dez/23", "valor_mercado": 300.0, "valor_investido": 250.0},
    ]})
    assert any(t.name == "Custo histórico" for t in fig.data)
