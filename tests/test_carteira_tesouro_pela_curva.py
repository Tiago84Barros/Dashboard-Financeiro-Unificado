"""O Tesouro da carteira é marcado pela curva oficial, não pelo preço da foto.

A foto "Posição" da B3 carrega, para os títulos públicos, um PU de dias antes
da data do relatório. Em 26/10/2026 os prefixados e os IPCA+ caíram de 2 a 10%
num pregão, e a foto do dia seguinte continuou com o PU da semana anterior: a
carteira passou a afirmar um ganho que o próprio painel do Tesouro, marcado
pela curva, não reconhecia — e a distância crescia com o prazo do título.

A curva oficial (`tesouro_market_rates`) é o preço do emissor. Quem já tem o
título vende ao Tesouro pela ponta de **recompra**, a mesma que a Evolução e o
painel do Tesouro já usam. Os números abaixo são sintéticos.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from core import investimentos, tesouro_curva
from core.investimentos import _montar_carteira_snapshot


def _row(ticker, *, qty, vm, invested, tipo="tesouro", report=date(2026, 10, 27)):
    return SimpleNamespace(
        ticker=ticker, quantity=qty, market_value=vm, invested_value=invested,
        asset_type=tipo, asset_name=ticker, sector="other", currency="BRL",
        country="BR", report_date=report, live_price=None, live_timestamp=None,
        usd_brl_rate=None, pp_quantity=0, pp_total_invested=0,
        pp_average_price=0, b3_avg_price=0, b3_quantity=0, b3_report_date=None,
        is_loaned=False, market_price=None,
    )


def _pos(rows, ticker, curva):
    carteira = _montar_carteira_snapshot(rows, curva_tesouro=curva)
    return carteira, next(p for p in carteira["posicoes"] if p["ticker"] == ticker)


def test_curva_mais_nova_que_a_foto_remarca_o_titulo():
    # Foto de 27/10 com o PU de antes do choque (866,67); curva de 28/10 a 850.
    rows = [_row("TPRE2030", qty=3.0, vm=2600.0, invested=2000.0)]
    curva = {"TPRE2030": (850.0, date(2026, 10, 28))}

    carteira, p = _pos(rows, "TPRE2030", curva)

    assert p["preco_atual"] == 850.0
    assert p["valor_mercado"] == 2550.0
    assert p["rentab_pct"] == 27.5
    assert p["cotacao_fonte"] == "curva_tesouro"
    assert p["data_referencia"] == date(2026, 10, 28)
    # A foto continua dizendo de quando é a quantidade.
    assert p["snapshot_report_date"] == date(2026, 10, 27)
    assert carteira["total_mercado"] == 2550.0
    assert carteira["rentabilidade_total_pct"] == 27.5


def test_curva_do_mesmo_dia_da_foto_tambem_vale():
    # A foto da B3 é de 27/10 mas o PU dela é de antes; a curva de 27/10 é o
    # preço do próprio dia.
    rows = [_row("TIPCA2035", qty=2.0, vm=7000.0, invested=6000.0)]
    curva = {"TIPCA2035": (3300.0, date(2026, 10, 27))}

    _, p = _pos(rows, "TIPCA2035", curva)

    assert p["valor_mercado"] == 6600.0
    assert p["cotacao_fonte"] == "curva_tesouro"


def test_curva_mais_velha_que_a_foto_nao_desfaz_a_foto():
    # Job da curva parado: a foto é o número mais recente que existe.
    rows = [_row("TPRE2030", qty=3.0, vm=2600.0, invested=2000.0)]
    curva = {"TPRE2030": (850.0, date(2026, 10, 20))}

    _, p = _pos(rows, "TPRE2030", curva)

    assert p["valor_mercado"] == 2600.0
    assert p["cotacao_fonte"] == "snapshot"
    assert p["data_referencia"] == date(2026, 10, 27)


def test_quantidade_zerada_pela_corretora_fica_com_o_valor_da_foto():
    # A XP já entregou título com quantidade arredondada para zero e valor
    # cheio. Quantidade vezes PU daria zero.
    rows = [_row("TSELIC2029", qty=0.0, vm=5400.0, invested=5000.0)]
    curva = {"TSELIC2029": (18000.0, date(2026, 10, 28))}

    _, p = _pos(rows, "TSELIC2029", curva)

    assert p["valor_mercado"] == 5400.0
    assert p["cotacao_fonte"] == "snapshot"


def test_papel_fora_da_curva_e_acao_nao_mudam():
    rows = [
        _row("CDB-BANCO-X", qty=1.0, vm=1100.0, invested=1000.0, tipo="fixed_income"),
        _row("PETR4", qty=10.0, vm=380.0, invested=300.0, tipo="stock"),
    ]
    # Mesmo que alguém ponha um ticker de ação no dicionário, ação não é
    # título público.
    curva = {"PETR4": (50.0, date(2026, 10, 28))}

    carteira = _montar_carteira_snapshot(rows, curva_tesouro=curva)
    por = {p["ticker"]: p for p in carteira["posicoes"]}

    assert por["CDB-BANCO-X"]["valor_mercado"] == 1100.0
    assert por["PETR4"]["valor_mercado"] == 380.0
    assert por["PETR4"]["cotacao_fonte"] == "snapshot"


def test_sem_curva_a_montagem_e_a_de_antes():
    rows = [_row("TPRE2030", qty=3.0, vm=2600.0, invested=2000.0)]

    carteira = _montar_carteira_snapshot(rows)

    assert carteira["posicoes"][0]["valor_mercado"] == 2600.0


# ── leitura da curva ─────────────────────────────────────────────────────────

class _Savepoint:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        self.conn.savepoints += 1
        return self

    def __exit__(self, *exc):
        return False


class _Conexao:
    def __init__(self, linhas=None, erro=None):
        self.linhas, self.erro = linhas or [], erro
        self.savepoints = 0
        self.params = None

    def begin_nested(self):
        return _Savepoint(self)

    def execute(self, stmt, params=None):
        if self.erro:
            raise self.erro
        self.params = params
        return SimpleNamespace(fetchall=lambda: list(self.linhas))


def test_pu_de_recompra_le_a_ponta_de_venda_dentro_de_savepoint():
    conn = _Conexao(linhas=[
        SimpleNamespace(security_key="TPRE2030", base_date=date(2026, 10, 28), pu=850.0),
    ])

    pus = tesouro_curva.pu_de_recompra(conn, ["TPRE2030", "TPRE2030", "TIPCA2035"])

    assert pus == {"TPRE2030": (850.0, date(2026, 10, 28))}
    assert conn.savepoints == 1
    assert sorted(conn.params["chaves"]) == ["TIPCA2035", "TPRE2030"]


def test_pu_de_recompra_sem_tabela_devolve_vazio():
    # Tabela ausente não pode derrubar a carteira: o SAVEPOINT impede que a
    # transação do chamador fique abortada, e a função devolve vazio.
    conn = _Conexao(erro=RuntimeError("relation tesouro_market_rates does not exist"))

    assert tesouro_curva.pu_de_recompra(conn, ["TPRE2030"]) == {}
    assert tesouro_curva.pu_de_recompra(conn, []) == {}


def test_sql_da_curva_usa_a_ponta_de_recompra():
    sql = " ".join(tesouro_curva.SQL_PU_DE_RECOMPRA.split())

    assert "COALESCE(sell_pu, base_pu)" in sql
    assert "DISTINCT ON (security_key)" in sql
    assert "base_date DESC" in sql


def test_carteira_consolidada_passa_a_curva_para_a_montagem(monkeypatch):
    rows = [_row("TPRE2030", qty=3.0, vm=2600.0, invested=2000.0),
            _row("PETR4", qty=10.0, vm=380.0, invested=300.0, tipo="stock")]
    pedidas = []

    def _curva(conn, chaves):
        pedidas.append(sorted(chaves))
        return {"TPRE2030": (850.0, date(2026, 10, 28))}

    class _Conn:
        def execute(self, stmt, params=None):
            return SimpleNamespace(fetchall=lambda: [], scalar=lambda: True)

    monkeypatch.setattr(tesouro_curva, "pu_de_recompra", _curva)
    monkeypatch.setattr(investimentos, "_calcular_custos_transacoes", lambda conn, owner: {})
    monkeypatch.setattr(investimentos, "cambio_medio_de_aquisicao", lambda conn, owner: {})
    monkeypatch.setattr(investimentos, "listar_precos_manuais", lambda owner, conn: {})
    monkeypatch.setattr(investimentos.cotacao_viva, "buscar", lambda simbolos: {})

    carteira = investimentos._carteira_snapshot_consolidada(_Conn(), "u1", rows)

    # Só os títulos públicos vão à curva.
    assert pedidas == [["TPRE2030"]]
    p = next(p for p in carteira["posicoes"] if p["ticker"] == "TPRE2030")
    assert p["valor_mercado"] == 2550.0
    assert p["cotacao_fonte"] == "curva_tesouro"


def test_card_rotula_a_cotacao_pela_curva():
    from views.investimentos import _card_ativo

    pos = {
        "ticker": "TPRE2030", "nome": "Tesouro Prefixado 2030", "classe": "Tesouro Direto",
        "setor": "Tesouro Direto", "moeda": "BRL", "pais": "BR", "quantidade": 3.0,
        "preco_medio": 666.67, "total_investido": 2000.0, "custo_fonte": "snapshot",
        "cotacao_fonte": "curva_tesouro", "preco_atual": 850.0, "valor_mercado": 2550.0,
        "diferenca_reais": 550.0, "rentab_pct": 27.5, "rentab_moeda": "BRL",
        "pct_carteira": 100.0, "cor": "#000000", "data_referencia": date(2026, 10, 28),
    }

    html = _card_ativo(pos, 0.0)

    assert "curva do Tesouro" in html
    assert "snapshot" not in html.split("Cotação", 1)[1].split("</", 1)[0]


class _ConnEscalar:
    def __init__(self, valor):
        self.valor = valor

    def execute(self, *_a, **_k):
        return SimpleNamespace(scalar=lambda: self.valor)


@pytest.mark.parametrize("dias, esperado", [(2, 100.0), (15, 0.0), (30, 0.0)])
def test_grau_de_confianca_mede_a_idade_da_curva(dias, esperado):
    from datetime import timedelta

    from core.confianca_secao import _curva_do_tesouro

    comp = _curva_do_tesouro(_ConnEscalar(date.today() - timedelta(days=dias)))

    assert comp.nome == "Curva do Tesouro"
    assert comp.pct == pytest.approx(esperado)


def test_grau_de_confianca_sem_curva_vale_zero_e_nao_some():
    """Sem curva o número da tela continua de pé, só que velho: zero, não None."""
    from core.confianca_secao import _curva_do_tesouro

    comp = _curva_do_tesouro(_ConnEscalar(None))

    assert comp.pct == 0.0
    assert "foto da B3" in comp.evidencia


def test_grau_de_confianca_falha_na_leitura_vira_nao_medido():
    from core.confianca_secao import confianca_investimentos

    class _Quebrado:
        def connect(self):
            raise RuntimeError("sem banco")

    secao = confianca_investimentos(_Quebrado())

    curva = next(c for c in secao.componentes if c.nome == "Curva do Tesouro")
    assert curva.pct is None
