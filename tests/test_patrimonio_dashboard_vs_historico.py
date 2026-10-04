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


def test_historico_inclui_acoes_emprestadas():
    # Dez/23 da B3 (R$ 590.520,23) so fecha somando a aba de emprestimos.
    sql = investimentos._SQL_EVOLUCAO_SNAPSHOTS.lower()

    assert "is_loaned" not in sql


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


def test_recorte_anual_pega_a_ultima_foto_de_cada_ano():
    from views.investimentos import _recorte_evolucao

    snaps = [
        {"label": "Dez/20", "mes_str": "2020-12", "valor_mercado": 610.0, "valor_investido": None},
        {"label": "Dez/25", "mes_str": "2025-12", "valor_mercado": 198.0, "valor_investido": 150.0},
        {"label": "Jan/26", "mes_str": "2026-01", "valor_mercado": 200.0, "valor_investido": 160.0},
        {"label": "Out/26", "mes_str": "2026-10", "valor_mercado": 317.0, "valor_investido": 300.0},
    ]
    anos = _recorte_evolucao(snaps, "Anos")
    assert [p["label"] for p in anos] == ["2020", "2025", "2026"]
    assert anos[-1]["valor_mercado"] == 317.0

    doze = _recorte_evolucao(snaps, "12 M")
    assert [p["label"] for p in doze] == ["Dez/25", "Jan/26", "Out/26"]

    assert _recorte_evolucao(snaps, "Tudo") == snaps
    # Sem mes_str não dá para agrupar: mostra tudo em vez de inventar.
    assert _recorte_evolucao([{"label": "x", "valor_mercado": 1.0}], "Anos") == [
        {"label": "x", "valor_mercado": 1.0}]


# ── Exterior (Nomad) nas fotos históricas ─────────────────────────────────────


class _ConexaoExterior:
    """Compra 2 SPY em 10/01 (dólar 5,00) e vende 1 em 20/02 (dólar 5,20)."""

    def __init__(self, sem_preco_em=None):
        self.sem_preco_em = sem_preco_em

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if sql == investimentos._SQL_EVOLUCAO_EXTERIOR_TX:
            return _Resultado([
                SimpleNamespace(ticker="SPY", asset_id="a1", data=date(2026, 1, 10),
                                tipo="buy", quantidade=2.0, preco=100.0),
                SimpleNamespace(ticker="SPY", asset_id="a1", data=date(2026, 2, 20),
                                tipo="sell", quantidade=1.0, preco=120.0),
            ])
        if sql == investimentos._SQL_EVOLUCAO_EXTERIOR_PRECOS:
            return _Resultado([
                SimpleNamespace(data=d, asset_id="a1",
                                close=None if d == self.sem_preco_em else 110.0)
                for d in params["datas"]
            ])
        if sql == investimentos._SQL_EVOLUCAO_EXTERIOR_FX:
            taxas = {date(2026, 1, 10): 5.0, date(2026, 2, 20): 5.2}
            return _Resultado([SimpleNamespace(data=d, close=taxas.get(d, 6.0))
                               for d in params["datas"]])
        return _Resultado([])


def test_exterior_recomposto_em_cada_data_de_foto():
    datas = [date(2025, 12, 31), date(2026, 1, 31), date(2026, 2, 28)]
    ext = investimentos._exterior_nas_datas(_ConexaoExterior(), "u1", datas)

    assert ext[date(2025, 12, 31)] == {"vm": 0.0, "vi": 0.0, "faltando": []}
    # 2 x 110 USD x 6,00; custo 2 x 100 x 5,00 (dolar da compra)
    assert ext[date(2026, 1, 31)]["vm"] == pytest.approx(1320.0)
    assert ext[date(2026, 1, 31)]["vi"] == pytest.approx(1000.0)
    # Vendeu metade: custo cai pela metade, a preco medio
    assert ext[date(2026, 2, 28)]["vm"] == pytest.approx(660.0)
    assert ext[date(2026, 2, 28)]["vi"] == pytest.approx(500.0)


def test_exterior_sem_preco_e_nomeado_e_nao_zerado_em_silencio():
    ext = investimentos._exterior_nas_datas(
        _ConexaoExterior(sem_preco_em=date(2026, 1, 31)), "u1", [date(2026, 1, 31)])

    assert ext[date(2026, 1, 31)]["vm"] == 0.0
    assert ext[date(2026, 1, 31)]["faltando"] == ["SPY"]


def test_foto_da_b3_recebe_o_exterior_da_mesma_data():
    snaps = [SimpleNamespace(mes=date(2026, 9, 30), valor_mercado=300.0,
                             valor_investido_snapshot=250.0)]
    ext = {date(2026, 9, 30): {"vm": 100.0, "vi": 80.0, "faltando": []}}
    d = investimentos._montar_evolucao_snapshot(snaps, [], None, [], ext)

    assert d["snapshots"][0]["valor_mercado"] == pytest.approx(400.0)
    assert d["snapshots"][0]["valor_investido"] == pytest.approx(330.0)
