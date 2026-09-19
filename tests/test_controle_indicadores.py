import pytest

from core.controle_indicadores import indicadores_caixa


@pytest.mark.parametrize("renda,despesa,aporte,saldo,status", [
    (10000, 8000, 1000, 2000, "superavit"),
    (10000, 8000, 3000, 2000, "aportes_excedem_sobra"),
    (10000, 11000, 3000, -1000, "deficit"),
    (10000, 8000, 2000, 2000, "superavit"),
    (10000, 10000, 0, 0, "equilibrio"),
    (10000, 10000, 1, 0, "aportes_excedem_sobra"),
    (0, 500, 0, -500, "deficit"),
    (0, 0, 500, 0, "aportes_excedem_sobra"),
    (10000, 8000, -3000, 2000, "superavit"),
])
def test_saldo_e_estado_separam_consumo_de_investimento(renda, despesa, aporte, saldo, status):
    result = indicadores_caixa(renda, despesa, aporte)
    assert result["saldo"] == saldo
    assert result["status"] == status
    if renda:
        assert result["comprometido_pct"] == pytest.approx(despesa / renda * 100)
        assert result["poupanca_pct"] == pytest.approx(saldo / renda * 100)
    else:
        assert result["comprometido_pct"] is None
        assert result["poupanca_pct"] is None


def test_aporte_nao_muda_saldo_poupanca_nem_comprometimento():
    before = indicadores_caixa(15000, 9000, 0)
    after = indicadores_caixa(15000, 9000, 12000)
    for key in ("saldo", "poupanca_pct", "comprometido_pct"):
        assert before[key] == after[key]


@pytest.mark.parametrize("renda,despesa,aporte", [(None, 10, 0), (10, None, 0),
    (10, 0, float("nan")), (float("inf"), 0, 0), (-1, 0, 0), (10, -1, 0)])
def test_rejeita_dados_ausentes_nao_finitos_ou_totais_negativos(renda, despesa, aporte):
    with pytest.raises(ValueError):
        indicadores_caixa(renda, despesa, aporte)


@pytest.mark.parametrize("status,papel", [
    ("deficit", "despesa"),
    ("aportes_excedem_sobra", "invest"),
    ("superavit", "receita"),
    ("equilibrio", "receita"),
])
def test_cor_do_saldo_segue_o_estado_nas_duas_telas(status, papel):
    """Vermelho só em déficit; azul quando o aporte passa da sobra; verde no resto.

    As duas telas que exibem o saldo do mês precisam usar a mesma regra — o
    usuário lê a cor antes do número.
    """
    from views import controle_financeiro as cf
    from views import dashboard_geral as dg

    esperado_cf = {"despesa": cf._COR_DESPESA, "invest": cf._COR_INVEST,
                   "receita": cf._COR_RECEITA}[papel]
    esperado_dg = {"despesa": dg._COR_NEGATIVO, "invest": dg._COR_PATRIMONIO,
                   "receita": dg._COR_FLUXO}[papel]
    assert cf._cor_saldo_caixa(status) == esperado_cf
    assert dg._cor_saldo_caixa(status) == esperado_dg
    # Azul e verde não podem colidir com o vermelho de déficit.
    if papel != "despesa":
        assert cf._cor_saldo_caixa(status) != cf._COR_DESPESA
        assert dg._cor_saldo_caixa(status) != dg._COR_NEGATIVO


def test_contexto_da_ia_nao_descreve_aporte_como_despesa():
    """O chat lê este texto como fato; se ele disser que o saldo subtrai aportes,
    a IA volta a chamar investimento de gasto mesmo com a tela corrigida."""
    from core.llm_context_financeiro import build_financas_chat_context

    contexto, meta = build_financas_chat_context(
        user_question="e o saldo?",
        dados_mes={"receitas": 27636.02, "despesas": 25577.70, "categorias": [],
                   "data_source": "real", "num_transacoes": 16},
        historico=[], hist_anual={}, gastos_categoria_anual=[], gastos_cartao={},
        evolucao={}, investido_mes=10000.0, ano_ref=2026, mes_ref=9,
    )
    assert meta["saldo_mes"] == pytest.approx(2058.32)
    assert meta["renda_comprometida_pct"] == pytest.approx(92.6, abs=0.05)
    assert "Saldo do mês (Receitas − Despesas):" in contexto
    assert "Receitas − Despesas − Investimentos" not in contexto
