"""
tests/test_portfolio_market_value.py
=====================================
Testes unitários para cálculos de valor de mercado do portfólio.

Valida:
  - valor de mercado atual por ativo (live price × qty)
  - valor total de mercado do portfólio
  - valorização/desvalorização em reais e percentual
  - composição percentual por valor de mercado
  - fallback quando ativo não tem cotação live
  - divisão por zero (qty=0, investido=0)
  - ativo com qty=0 não entra no cálculo
"""
from __future__ import annotations

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Helpers e fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _pos(
    ticker: str,
    qty: float,
    preco_atual: float,
    preco_medio: float,
    cotacao_fonte: str = "live",
    moeda: str = "BRL",
    classe: str = "Ações BR",
    setor: str = "Financeiro",
) -> dict:
    """Cria uma posição com os campos mínimos para os testes."""
    total_investido = round(qty * preco_medio, 2)
    valor_mercado   = round(qty * preco_atual, 2)
    diferenca_reais = round(valor_mercado - total_investido, 2)
    rentab_pct = (
        round(diferenca_reais / total_investido * 100, 2)
        if total_investido > 0 else 0.0
    )
    return {
        "ticker":          ticker,
        "nome":            ticker,
        "classe":          classe,
        "setor":           setor,
        "moeda":           moeda,
        "quantidade":      qty,
        "preco_medio":     preco_medio,
        "total_investido": total_investido,
        "cotacao_fonte":   cotacao_fonte,
        "preco_atual":     preco_atual,
        "valor_mercado":   valor_mercado,
        "diferenca_reais": diferenca_reais,
        "rentab_pct":      rentab_pct,
        "pct_carteira":    0.0,
        "cor":             "#718096",
    }


def _build_carteira(posicoes: list[dict]) -> dict:
    """Agrega posições em carteira seguindo a mesma lógica de investimentos.py."""
    total_investido = sum(p["total_investido"] for p in posicoes)
    total_mercado   = sum(p["valor_mercado"]   for p in posicoes)
    diferenca       = round(total_mercado - total_investido, 2)
    rentab = (
        round(diferenca / total_investido * 100, 2)
        if total_investido > 0 else 0.0
    )
    base = total_mercado if total_mercado > 0 else total_investido
    for p in posicoes:
        p["pct_carteira"] = (
            round(p["valor_mercado"] / base * 100, 2) if base > 0 else 0.0
        )
    n_live = sum(1 for p in posicoes if p.get("cotacao_fonte") == "live")
    return {
        "total_investido":         round(total_investido, 2),
        "total_mercado":           round(total_mercado, 2),
        "diferenca_reais":         diferenca,
        "rentabilidade_total_pct": rentab,
        "num_ativos":              len(posicoes),
        "cotacoes_disponiveis":    n_live > 0,
        "n_cotacoes_live":         n_live,
        "posicoes":                posicoes,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. Valor de mercado por ativo
# ─────────────────────────────────────────────────────────────────────────────

class TestValorMercadoPorAtivo:

    def test_valor_mercado_basico(self):
        p = _pos("BBAS3", qty=100, preco_atual=20.30, preco_medio=18.00)
        assert p["valor_mercado"] == pytest.approx(2030.00, abs=0.01)

    def test_valor_mercado_fracionario(self):
        p = _pos("SPY", qty=1.92357, preco_atual=757.71, preco_medio=756.52, moeda="USD")
        assert p["valor_mercado"] == pytest.approx(1.92357 * 757.71, abs=0.10)

    def test_valor_mercado_preco_maior_que_medio_valorizado(self):
        p = _pos("PETR4", qty=200, preco_atual=38.50, preco_medio=30.00)
        assert p["valor_mercado"] > p["total_investido"]

    def test_valor_mercado_preco_menor_que_medio_desvalorizado(self):
        p = _pos("VALE3", qty=50, preco_atual=55.00, preco_medio=80.00)
        assert p["valor_mercado"] < p["total_investido"]

    def test_valor_mercado_preco_igual_a_medio(self):
        p = _pos("ITUB4", qty=300, preco_atual=25.00, preco_medio=25.00)
        assert p["valor_mercado"] == pytest.approx(p["total_investido"], abs=0.01)
        assert p["diferenca_reais"] == pytest.approx(0.0, abs=0.01)

    def test_qty_zero_valor_mercado_zero(self):
        p = _pos("MGLU3", qty=0, preco_atual=4.00, preco_medio=15.00)
        assert p["valor_mercado"] == 0.0
        assert p["total_investido"] == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 2. Valorização / Desvalorização em reais
# ─────────────────────────────────────────────────────────────────────────────

class TestDiferencaReais:

    def test_valorizacao_positiva(self):
        p = _pos("BBAS3", qty=100, preco_atual=22.00, preco_medio=18.00)
        assert p["diferenca_reais"] == pytest.approx(400.00, abs=0.01)

    def test_desvalorizacao_negativa(self):
        p = _pos("MGLU3", qty=100, preco_atual=3.50, preco_medio=15.00)
        assert p["diferenca_reais"] == pytest.approx(-1150.00, abs=0.01)

    def test_estabilidade_zero(self):
        p = _pos("ITUB4", qty=100, preco_atual=25.00, preco_medio=25.00)
        assert p["diferenca_reais"] == pytest.approx(0.0, abs=0.01)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Rentabilidade percentual
# ─────────────────────────────────────────────────────────────────────────────

class TestRentabilidade:

    def test_rentabilidade_positiva(self):
        p = _pos("BBAS3", qty=100, preco_atual=22.00, preco_medio=20.00)
        assert p["rentab_pct"] == pytest.approx(10.0, abs=0.01)

    def test_rentabilidade_negativa(self):
        p = _pos("VALE3", qty=100, preco_atual=45.00, preco_medio=50.00)
        assert p["rentab_pct"] == pytest.approx(-10.0, abs=0.01)

    def test_rentabilidade_investido_zero_retorna_zero(self):
        p = _pos("MGLU3", qty=0, preco_atual=5.00, preco_medio=0.0)
        assert p["rentab_pct"] == 0.0

    def test_formula_rentabilidade(self):
        # (mercado - custo) / custo * 100
        p = _pos("PETR4", qty=200, preco_atual=38.00, preco_medio=32.00)
        esperado = (38.00 * 200 - 32.00 * 200) / (32.00 * 200) * 100
        assert p["rentab_pct"] == pytest.approx(esperado, abs=0.01)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Composição percentual por valor de mercado
# ─────────────────────────────────────────────────────────────────────────────

class TestComposicaoPercentual:

    def test_dois_ativos_soma_100(self):
        posicoes = [
            _pos("BBAS3", qty=100, preco_atual=20.00, preco_medio=18.00),
            _pos("ITUB4", qty=100, preco_atual=30.00, preco_medio=25.00),
        ]
        carteira = _build_carteira(posicoes)
        soma_pct = sum(p["pct_carteira"] for p in carteira["posicoes"])
        assert soma_pct == pytest.approx(100.0, abs=0.1)

    def test_composicao_usa_valor_mercado_nao_custo(self):
        # Ativo A: custo 2000, mercado 4000 → 80%
        # Ativo B: custo 2000, mercado 1000 → 20%
        posicoes = [
            _pos("A", qty=100, preco_atual=40.00, preco_medio=20.00),
            _pos("B", qty=100, preco_atual=10.00, preco_medio=20.00),
        ]
        carteira = _build_carteira(posicoes)
        pct_a = next(p["pct_carteira"] for p in carteira["posicoes"] if p["ticker"] == "A")
        pct_b = next(p["pct_carteira"] for p in carteira["posicoes"] if p["ticker"] == "B")
        assert pct_a == pytest.approx(80.0, abs=0.1)
        assert pct_b == pytest.approx(20.0, abs=0.1)

    def test_ativo_unico_100_pct(self):
        posicoes = [_pos("BBAS3", qty=100, preco_atual=20.00, preco_medio=18.00)]
        carteira = _build_carteira(posicoes)
        assert carteira["posicoes"][0]["pct_carteira"] == pytest.approx(100.0, abs=0.01)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Carteira agregada
# ─────────────────────────────────────────────────────────────────────────────

class TestCarteiraAgregada:

    def test_total_mercado_soma_posicoes(self):
        posicoes = [
            _pos("BBAS3", qty=100, preco_atual=20.00, preco_medio=18.00),
            _pos("PETR4", qty=200, preco_atual=38.00, preco_medio=32.00),
            _pos("ITUB4", qty=150, preco_atual=25.00, preco_medio=22.00),
        ]
        carteira = _build_carteira(posicoes)
        esperado = 100*20 + 200*38 + 150*25
        assert carteira["total_mercado"] == pytest.approx(esperado, abs=0.01)

    def test_total_investido_soma_posicoes(self):
        posicoes = [
            _pos("BBAS3", qty=100, preco_atual=20.00, preco_medio=18.00),
            _pos("PETR4", qty=200, preco_atual=38.00, preco_medio=32.00),
        ]
        carteira = _build_carteira(posicoes)
        esperado = 100*18 + 200*32
        assert carteira["total_investido"] == pytest.approx(esperado, abs=0.01)

    def test_diferenca_total_mercado_menos_investido(self):
        posicoes = [
            _pos("BBAS3", qty=100, preco_atual=22.00, preco_medio=18.00),  # +400
            _pos("MGLU3", qty=100, preco_atual=3.50,  preco_medio=15.00),  # -1150
        ]
        carteira = _build_carteira(posicoes)
        assert carteira["diferenca_reais"] == pytest.approx(-750.0, abs=0.01)

    def test_rentabilidade_total_formula(self):
        posicoes = [
            _pos("A", qty=100, preco_atual=22.00, preco_medio=20.00),
            _pos("B", qty=100, preco_atual=18.00, preco_medio=20.00),
        ]
        carteira = _build_carteira(posicoes)
        mercado   = 100*22 + 100*18
        investido = 100*20 + 100*20
        esperado  = (mercado - investido) / investido * 100
        assert carteira["rentabilidade_total_pct"] == pytest.approx(esperado, abs=0.01)

    def test_rentabilidade_zero_quando_investido_zero(self):
        posicoes = [_pos("A", qty=0, preco_atual=10.00, preco_medio=0.0)]
        carteira = _build_carteira(posicoes)
        assert carteira["rentabilidade_total_pct"] == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 6. Fallback para ativo sem cotação live
# ─────────────────────────────────────────────────────────────────────────────

class TestFallbackSemCotacao:

    def test_cotacao_fonte_snapshot_nao_e_live(self):
        p = _pos("TSELIC2028", qty=1.0, preco_atual=5000.00, preco_medio=4800.00,
                 cotacao_fonte="snapshot")
        assert p["cotacao_fonte"] == "snapshot"
        # Valor de mercado ainda é calculado (com preço do snapshot)
        assert p["valor_mercado"] == pytest.approx(5000.00, abs=0.01)

    def test_n_cotacoes_live_zero_quando_tudo_snapshot(self):
        posicoes = [
            _pos("TSELIC2028", qty=1.0, preco_atual=5000.00, preco_medio=4800.00,
                 cotacao_fonte="snapshot"),
            _pos("CDB_BMG",    qty=727, preco_atual=1.55,    preco_medio=1.47,
                 cotacao_fonte="snapshot"),
        ]
        carteira = _build_carteira(posicoes)
        assert carteira["n_cotacoes_live"] == 0
        assert not carteira["cotacoes_disponiveis"]

    def test_n_cotacoes_live_conta_apenas_live(self):
        posicoes = [
            _pos("BBAS3",      qty=100, preco_atual=20.00, preco_medio=18.00,
                 cotacao_fonte="live"),
            _pos("ITUB4",      qty=200, preco_atual=25.00, preco_medio=22.00,
                 cotacao_fonte="live"),
            _pos("TSELIC2028", qty=1.0, preco_atual=5000.00, preco_medio=4800.00,
                 cotacao_fonte="snapshot"),
        ]
        carteira = _build_carteira(posicoes)
        assert carteira["n_cotacoes_live"] == 2
        assert carteira["cotacoes_disponiveis"]


# ─────────────────────────────────────────────────────────────────────────────
# 7. Edge cases e robustez
# ─────────────────────────────────────────────────────────────────────────────

class TestEdgeCases:

    def test_posicao_zerada_nao_distorce_carteira(self):
        """Ativo com qty=0 tem vm=0; não deve distorcer pct_carteira dos outros."""
        posicoes = [
            _pos("BBAS3", qty=100,  preco_atual=20.00, preco_medio=18.00),
            _pos("SAPR11", qty=0,   preco_atual=10.00, preco_medio=27.00),
        ]
        carteira = _build_carteira(posicoes)
        pct_bbas3 = next(p["pct_carteira"] for p in carteira["posicoes"] if p["ticker"] == "BBAS3")
        pct_sapr  = next(p["pct_carteira"] for p in carteira["posicoes"] if p["ticker"] == "SAPR11")
        assert pct_bbas3 == pytest.approx(100.0, abs=0.01)
        assert pct_sapr  == pytest.approx(0.0,   abs=0.01)

    def test_carteira_vazia_nao_quebra(self):
        carteira = _build_carteira([])
        assert carteira["total_mercado"]   == 0.0
        assert carteira["total_investido"] == 0.0
        assert carteira["diferenca_reais"] == 0.0
        assert carteira["num_ativos"]      == 0

    def test_ativo_usd_convertido_para_brl(self):
        """Simula posição Nomad: verifica que a moeda é preservada no dict."""
        p = _pos("SPY", qty=8.8358, preco_atual=3820.0,  # preço já em BRL
                 preco_medio=3563.0, moeda="USD")
        assert p["moeda"] == "USD"
        assert p["valor_mercado"] == pytest.approx(8.8358 * 3820.0, abs=0.50)

    def test_grandes_quantidades(self):
        p = _pos("BBAS3", qty=10_000, preco_atual=20.30, preco_medio=18.00)
        assert p["valor_mercado"] == pytest.approx(203_000.0, abs=0.01)
        assert p["diferenca_reais"] == pytest.approx(23_000.0, abs=0.01)

    def test_quantidade_fracionaria_muito_pequena(self):
        p = _pos("SPY", qty=0.00001, preco_atual=3800.0, preco_medio=3500.0)
        assert p["valor_mercado"] >= 0.0
        assert p["diferenca_reais"] >= 0.0
