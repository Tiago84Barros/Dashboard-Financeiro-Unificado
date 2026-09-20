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


# ── Taxa de poupança exibida: sobra do mês + aporte, com teto na renda ────────

@pytest.mark.parametrize("renda,despesa,aporte,esperado,no_teto", [
    # Aporte cabe na sobra: ele soma à taxa sem encostar no teto.
    (10000, 8000, 1000, 30.0, False),
    # Aporte maior que a sobra (caixa de meses anteriores), ainda abaixo da renda.
    (10000, 8000, 5000, 70.0, False),
    # Aporte tão grande que sobra + aporte passa da renda: trava em 100%.
    (10000, 8000, 9000, 100.0, True),
    (10000, 0, 30000, 100.0, True),
    # Sem aporte, a taxa exibida é a própria renda não consumida.
    (10000, 8000, 0, 20.0, False),
    # Resgate (aporte negativo) reduz a taxa: saiu dinheiro da alocação.
    (10000, 8000, -1000, 10.0, False),
    # Déficit continua negativo; o teto é só superior.
    (10000, 12000, 0, -20.0, False),
])
def test_poupanca_alocada_soma_aporte_e_para_no_teto_da_renda(
    renda, despesa, aporte, esperado, no_teto
):
    """O usuário aporta com caixa que sobrou de meses anteriores, então o mês pode
    alocar mais do que sobrou nele. A taxa soma o aporte, mas não pode passar de
    100% da renda — acima disso o dinheiro não veio da renda do mês."""
    result = indicadores_caixa(renda, despesa, aporte)
    assert result["poupanca_alocada_pct"] == pytest.approx(esperado)
    assert result["poupanca_no_teto"] is no_teto


def test_poupanca_alocada_sem_renda_nao_inventa_taxa():
    result = indicadores_caixa(0, 0, 500)
    assert result["poupanca_alocada_pct"] is None
    assert result["poupanca_no_teto"] is False


def test_poupanca_alocada_nao_contamina_a_renda_nao_consumida():
    """`poupanca_pct` mede consumo e não pode enxergar o aporte; quem soma o
    aporte é a taxa exibida. Confundir as duas reescreve o indicador de consumo."""
    sem_aporte = indicadores_caixa(10000, 8000, 0)
    com_aporte = indicadores_caixa(10000, 8000, 5000)
    assert sem_aporte["poupanca_pct"] == com_aporte["poupanca_pct"]
    assert com_aporte["poupanca_alocada_pct"] > com_aporte["poupanca_pct"]


def test_telas_exibem_a_taxa_com_aporte_e_nao_a_renda_nao_consumida():
    """Três telas mostram 'Taxa de poupança' com o mesmo rótulo; se uma delas ler
    a chave antiga, o mesmo mês aparece com dois números diferentes no app."""
    import ast
    import pathlib

    for caminho, funcoes in (
        ("views/dashboard_geral.py",
         {"_render_kpi_grid", "_secao_resumo_modulos"}),
        ("views/controle_financeiro.py", {"_tab_analises"}),
    ):
        arvore = ast.parse(pathlib.Path(caminho).read_text(encoding="utf-8"))
        vistas = set()
        for no in ast.walk(arvore):
            if not (isinstance(no, ast.FunctionDef) and no.name in funcoes):
                continue
            vistas.add(no.name)
            chaves = {
                sub.slice.value
                for sub in ast.walk(no)
                if isinstance(sub, ast.Subscript)
                and isinstance(sub.slice, ast.Constant)
                and isinstance(sub.slice.value, str)
            }
            assert "poupanca_alocada_pct" in chaves, f"{caminho}:{no.name}"
            assert "poupanca_pct" not in chaves, f"{caminho}:{no.name}"
        # Função renomeada ou removida faz o laço acima rodar zero vez e o teste
        # passar sem exercitar nada; o que falta tem de aparecer como falha.
        assert vistas == funcoes, f"{caminho}: não encontradas {funcoes - vistas}"


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
