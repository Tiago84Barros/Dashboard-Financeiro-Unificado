import pandas as pd

import core.llm_b3 as llm
import core.llm_context_b3 as ctxmod
import core.portfolio_chat_charts as charts

# ── parse_chart_directives ────────────────────────────────────────────────────

def test_parse_chart_directives_extracts_and_strips_block():
    resposta = (
        "**Resumo** WEGE3 lidera em ROE.\n\n"
        "```charts\n"
        '[{"tipo":"comparison","metrica":"ROE","tickers":["WEGE3","ROMI3"]}]\n'
        "```"
    )
    texto, directives = llm.parse_chart_directives(resposta)
    assert "```charts" not in texto
    assert texto.strip().startswith("**Resumo**")
    assert directives == [
        {"tipo": "comparison", "metrica": "ROE", "tickers": ["WEGE3", "ROMI3"]}
    ]


def test_parse_chart_directives_no_block_returns_text_unchanged():
    texto, directives = llm.parse_chart_directives("Sem gráfico aqui.")
    assert texto == "Sem gráfico aqui."
    assert directives == []


def test_parse_chart_directives_ignores_invalid_json():
    texto, directives = llm.parse_chart_directives("ok ```charts\n{quebrado}\n```")
    assert directives == []


# ── detect_intent ─────────────────────────────────────────────────────────────

def test_detect_intent_outside_portfolio_triggers_universe_and_creation_compare():
    blocks = ctxmod.detect_intent("Existe alguma empresa melhor fora da carteira?")
    assert "compare_outside" in blocks
    assert "universe" in blocks


def test_detect_intent_sector_and_ranking():
    assert "sector" in ctxmod.detect_intent("A carteira está concentrada por setor?")
    assert "fundamentals" in ctxmod.detect_intent("Quais ações têm maior ROE e menor dívida?")


def test_detect_intent_selection_logic():
    assert "creation" in ctxmod.detect_intent("Por que essas ações foram escolhidas?")


# ── gráficos de preço/DRE (novos tipos) ───────────────────────────────────────

def test_chart_registry_tem_financials_e_performance():
    for t in ("financials", "dre", "receita_lucro", "performance", "desempenho"):
        assert t in charts.CHART_REGISTRY


def test_parse_directiva_financials():
    _, directives = llm.parse_chart_directives(
        'Veja o histórico.\n```charts\n'
        '[{"tipo":"financials","tickers":["EUCA4"],"titulo":"Receita x Lucro"}]\n```'
    )
    assert directives == [
        {"tipo": "financials", "tickers": ["EUCA4"], "titulo": "Receita x Lucro"}
    ]


def test_schema_menciona_preco_e_dre_disponiveis():
    schema = ctxmod.get_available_database_schema()
    assert "historical_prices" in schema
    # não deve mais afirmar indisponibilidade de séries de preço
    assert "performance" in schema and "financials" in schema


def test_detect_intent_concorrentes():
    assert "peers" in ctxmod.detect_intent("Quais os 3 maiores concorrentes da EUCA4?")
    assert "peers" in ctxmod.detect_intent("Compare WEGE3 vs seus pares")


# ── fallback determinístico de gráficos ───────────────────────────────────────

def test_infer_directives_receita_lucro():
    d = charts.infer_chart_directives(
        "Me dê um gráfico da relação receita x lucro líquido da EUCA4", ["EUCA4"], {})
    assert d == [{"tipo": "financials", "tickers": ["EUCA4"]}]


def test_infer_directives_desempenho_preco():
    d = charts.infer_chart_directives("Mostre o gráfico de desempenho dos preços dela", ["EUCA4"])
    assert d and d[0]["tipo"] == "performance" and d[0]["tickers"] == ["EUCA4"]


def test_infer_directives_concorrentes_usa_peers_map():
    d = charts.infer_chart_directives(
        "gráfico entre as 3 empresas mais concorrentes do segmento da EUCA4",
        ["EUCA4"], {"EUCA4": ["SUZB3", "KLBN11", "DXCO3", "RANI3"]})
    assert d and d[0]["tipo"] == "comparison"
    assert d[0]["tickers"] == ["EUCA4", "SUZB3", "KLBN11", "DXCO3"]   # base + top 3


def test_infer_directives_sem_pedido_de_grafico():
    assert charts.infer_chart_directives("Qual o ROE da EUCA4?", ["EUCA4"], {}) == []


# ── orquestrador ──────────────────────────────────────────────────────────────

def test_build_context_always_includes_schema_and_base():
    model = {"items": [{"ticker": "WEGE3", "setor": "Bens Industriais", "weight": 1.0}]}
    ctx, meta = ctxmod.build_llm_context_for_portfolio_chat(
        user_question="DY médio da carteira?",
        base_context="BASE_CONTEXT_AQUI",
        model=model,
        weights={"WEGE3": 1.0},
        macro_hist={},
        portfolio_tickers=["WEGE3"],
        cobertura_docs={},
    )
    assert "BASE_CONTEXT_AQUI" in ctx
    assert "DADOS DISPONÍVEIS NO BANCO" in ctx
    assert meta["portfolio_tickers"] == ["WEGE3"]


def test_company_fundamentals_context_uses_db(monkeypatch):
    mock = pd.DataFrame({
        "Ticker": ["ROMI3"], "P/L": [8.0], "P/VP": [1.1], "DY": [0.05],
        "ROE": [0.12], "ROIC": [0.10], "Margem_Liquida": [0.08],
        "Endividamento_Total": [0.9], "SETOR": ["Bens Industriais"],
    })
    monkeypatch.setattr(ctxmod._db, "load_multiplos_todos", lambda: mock)
    monkeypatch.setattr(ctxmod._db, "load_setores", lambda: pd.DataFrame())
    if hasattr(ctxmod._universe_with_sector, "clear"):
        ctxmod._universe_with_sector.clear()
    block = ctxmod.get_company_fundamentals_context(["ROMI3"])
    assert "ROMI3" in block
    assert "P/L=8.00" in block


# ── chart dispatcher ──────────────────────────────────────────────────────────

def test_render_charts_dispatcher_draws_valid_and_skips_unknown(monkeypatch):
    drawn_titles = []
    monkeypatch.setattr(charts.st, "plotly_chart", lambda *a, **k: drawn_titles.append(k.get("key")))
    monkeypatch.setattr(charts.st, "caption", lambda *a, **k: None)

    mock = pd.DataFrame({
        "Ticker": ["WEGE3", "ROMI3", "ITUB4"],
        "ROE": [0.28, 0.12, 0.20], "P/L": [30.0, 8.0, 9.0], "DY": [0.01, 0.05, 0.06],
    })
    monkeypatch.setattr(charts, "_multiplos_todos", lambda: mock)

    meta = {
        "model": {"items": [{"ticker": "WEGE3", "setor": "Ind", "weight": 1.0}]},
        "weights": {"WEGE3": 1.0},
        "portfolio_tickers": ["WEGE3"],
    }
    directives = [
        {"tipo": "comparison", "metrica": "ROE", "tickers": ["WEGE3", "ROMI3"]},
        {"tipo": "ranking", "metrica": "P/L"},
        {"tipo": "sector_allocation"},
        {"tipo": "tipo_inexistente", "metrica": "ROE"},   # ignorado
    ]
    n = charts.render_charts_from_directives(directives, meta)
    assert n == 3
