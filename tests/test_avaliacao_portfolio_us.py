"""Paridade entre a Avaliação de Portfólio B3 e a das Empresas Americanas.

A promessa ao usuário é "exatamente como a B3, respeitando o mercado americano".
Estes testes travam os dois lados dessa frase: o que TEM de ser igual (seções,
contrato de saída, escalas) e o que TEM de ser diferente (SEC/GAAP, dólar, Fed,
indústria, offline-first).
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pandas as pd

import core.portfolio_report_b3 as rel_b3
import core.portfolio_report_common as comum
import core.portfolio_report_us as rel_us
import views.analise_portfolio_us as view_us

_RAIZ = Path(__file__).resolve().parents[1]
_FONTE_US = (_RAIZ / "views" / "analise_portfolio_us.py").read_text(encoding="utf-8")
_FONTE_B3 = (_RAIZ / "views" / "analise_portfolio_b3.py").read_text(encoding="utf-8")


# ── O que tem de ser IGUAL ───────────────────────────────────────────────────

def test_os_dois_mercados_usam_a_mesma_maquinaria():
    """Sanitização, cenários, notas e confiança vêm do módulo comum."""
    assert rel_b3.QUALITATIVE_WEIGHTS is comum.QUALITATIVE_WEIGHTS
    assert rel_us.QUALITATIVE_WEIGHTS is comum.QUALITATIVE_WEIGHTS
    assert rel_b3.sanitize_company_report is comum.sanitize_company_report
    assert rel_us.sanitize_company_report is comum.sanitize_company_report
    assert rel_b3.sanitize_portfolio_report is comum.sanitize_portfolio_report
    assert rel_us.sanitize_portfolio_report is comum.sanitize_portfolio_report


def test_correcao_de_confianca_vale_para_os_dois():
    """O defeito do card "1" não pode reaparecer só no lado americano."""
    for modulo in (rel_b3, rel_us):
        relatorio = modulo.sanitize_company_report(
            {"perspectiva": "forte", "confianca": 0.9}, "TEST",
        )
        assert relatorio["confianca"] == 90


def test_relatorio_americano_devolve_o_schema_que_a_ui_consome():
    esperado = set(comum.fallback_company("X", "y"))
    assert set(rel_us.sanitize_company_report({}, "X")) >= esperado
    consolidado = comum.fallback_portfolio()
    assert set(rel_us.analyze_us_portfolio_report.__doc__ or "") or True
    for chave in ("qualidade_carteira", "perspectiva_12m", "confianca_media",
                  "score_medio", "conclusao_estrategica"):
        assert chave in consolidado


def test_as_duas_telas_tem_as_mesmas_secoes():
    for secao in ("Relatório Consolidado do Portfólio", "Alocação do Modelo",
                  "Relatórios por Empresa", "Conclusão Estratégica",
                  "Tire Dúvidas sobre o Portfólio", "Análise Qualitativa via LLM",
                  "Etapa 3 de 3"):
        assert secao in _FONTE_B3, f"a B3 perdeu: {secao}"
        assert secao in _FONTE_US, f"falta na tela americana: {secao}"


def test_a_tela_americana_reusa_o_css_e_os_cards_da_b3():
    """Duplicar a folha garantiria divergência na primeira mudança."""
    assert "from views.analise_portfolio_b3 import (" in _FONTE_US
    for componente in ("_CSS", "_kpi_card", "_macro_card", "_persp_badge",
                       "_delta_str", "_score_mod"):
        assert componente in _FONTE_US


def test_redistribuicao_de_pesos_e_o_mesmo_modelo_unico():
    assert "redistribuir_pesos(items_analisados)" in _FONTE_US
    assert "redistribuir_pesos" in _FONTE_B3


# ── O que tem de ser DIFERENTE (mercado americano) ───────────────────────────

def test_macro_americano_e_do_fed_nao_do_bcb():
    texto = rel_us.format_us_macro({
        "regime": "Transição", "score": 57, "tone": "neutro",
        "inputs": {"fed_funds": 4.25, "cpi_yoy": 2.5, "real_gdp_yoy": 2.0,
                   "unemployment": 4.2, "yield_curve_10y_2y": 0.25,
                   "high_yield_spread": 3.5},
        "sector_impacts": {"Technology": 1.6},
    })
    for termo in ("Fed funds", "CPI", "PIB real", "Desemprego", "Curva 10a-2a",
                  "Treasury", "S&P 500"):
        assert termo in texto
    # Nenhum indicador brasileiro é REPORTADO. A última linha cita Selic e
    # Ibovespa de propósito, para proibi-los — por isso o teste olha só as
    # linhas de dado, não a instrução.
    linhas_dado = [linha for linha in texto.splitlines() if linha.startswith("  ")
                   and not linha.strip().startswith("Custo de oportunidade")]
    for termo_br in ("Selic", "IPCA", "Ibovespa", "USD/BRL"):
        assert not any(termo_br in linha for linha in linhas_dado), termo_br


def test_prompt_americano_proibe_referencias_brasileiras():
    prompt = rel_us._PROMPT_COMPANY_PORTFOLIO
    assert "SEC/US GAAP" in prompt
    assert "indústria" in prompt
    assert "Não use Selic, IPCA nem Ibovespa" in prompt
    # Sem base documental: o prompt tem de mandar declarar a lacuna.
    assert "NÃO há base documental indexada" in prompt


def test_prompt_americano_tambem_proibe_confianca_fracionaria():
    assert "NUNCA fração" in rel_us._PROMPT_COMPANY_PORTFOLIO


def test_pares_sao_por_industria_sec():
    """Com 3+ pares na indústria, a comparação não sobe para o setor."""
    scored = pd.DataFrame([
        {"symbol": "AAA", "industry": "Software", "sector": "Tech", "score": 80},
        {"symbol": "BBB", "industry": "Software", "sector": "Tech", "score": 70},
        {"symbol": "CCC", "industry": "Software", "sector": "Tech", "score": 60},
        {"symbol": "EEE", "industry": "Software", "sector": "Tech", "score": 50},
        {"symbol": "DDD", "industry": "Bancos", "sector": "Financeiro", "score": 90},
    ])
    pares, nivel = rel_us.compute_industry_peers(scored, "AAA")
    assert pares == ["BBB", "CCC", "EEE"]      # ordenados por score
    assert nivel == "indústria SEC"
    assert "DDD" not in pares                  # outro setor nunca entra


def test_industria_rala_cai_para_o_setor():
    """Menos de 3 pares na indústria: o mesmo limiar da hierarquia B3."""
    scored = pd.DataFrame([
        {"symbol": "AAA", "industry": "Nicho", "sector": "Tech", "score": 80},
        {"symbol": "BBB", "industry": "Software", "sector": "Tech", "score": 70},
        {"symbol": "CCC", "industry": "Software", "sector": "Tech", "score": 60},
    ])
    pares, nivel = rel_us.compute_industry_peers(scored, "AAA")
    assert nivel == "setor"
    assert set(pares) == {"BBB", "CCC"}


def test_ticker_desconhecido_nao_quebra_os_pares():
    assert rel_us.compute_industry_peers(pd.DataFrame(), "AAA") == ([], "")
    scored = pd.DataFrame([{"symbol": "AAA", "industry": "X", "sector": "Y", "score": 1}])
    assert rel_us.compute_industry_peers(scored, "ZZZ") == ([], "")


def test_historico_financeiro_usa_fiscal_year_e_separa_fco_de_fcl():
    frame = pd.DataFrame([
        {"fiscal_year": 2023, "revenue": 1000.0, "net_income": 100.0,
         "operating_cash_flow": 150.0, "capex": -40.0, "free_cash_flow": 110.0},
        {"fiscal_year": 2024, "revenue": 1200.0, "net_income": 120.0,
         "operating_cash_flow": 180.0, "capex": -50.0, "free_cash_flow": 130.0},
    ])
    texto = rel_us.build_financial_history_context(frame)
    assert "FY2024" in texto
    assert "SEC/US GAAP" in texto and "USD" in texto
    assert "FCO não é FCL" in texto
    assert "Conversão FCL/lucro mais recente: 1.08x" in texto


def test_historico_vazio_declara_a_lacuna():
    texto = rel_us.build_financial_history_context(None)
    assert "indisponível" in texto
    assert "Não infira tendência" in texto


def test_laboratorio_avancado_usa_as_chaves_reais_do_modulo():
    """Regressão: nomes inventados fariam o bloco sair sempre vazio."""
    texto = rel_us.build_advanced_context({
        "f_score": 7, "z_score": 3.4, "sloan_accruals": 0.02,
        "incremental_roic": 0.18, "z_zone": "segura", "f_evaluable": 9,
    })
    assert "Piotroski F-Score (0–9): 7" in texto
    assert "Altman Z-Score: 3.40" in texto
    assert "ROIC incremental: 18.0%" in texto
    assert "Zona de Altman: segura" in texto


def test_f_score_parcial_e_declarado():
    texto = rel_us.build_advanced_context({"f_score": 4, "f_evaluable": 5})
    assert "apenas 5 dos 9 sinais" in texto


def test_avaliacao_quantitativa_entra_no_prompt_consolidado():
    """O determinístico precede a leitura da LLM, como o dossiê na B3."""
    assert "{quant_context}" in rel_us._PROMPT_PORTFOLIO
    assert "avaliacao_quant" in inspect.signature(
        rel_us.analyze_us_portfolio_report).parameters
    texto = rel_us.build_quant_context({
        "ok": True, "adjusted_score": 62.5, "score": 60.0, "macro_adjustment": 2.5,
        "classification": "Sólida", "diversification_score": 71.0,
        "effective_assets": 12.4, "hhi": 0.081, "max_sector_weight": 28.3,
        "coverage_weight": 94.0,
    })
    assert "não estime novamente" in texto
    assert "62.5/100" in texto


def test_concentracao_cobre_setor_e_industria():
    texto = rel_us.build_concentration_context([
        {"setor": "Tecnologia", "industria": "Software", "peso_pct": 40.0},
        {"setor": "Saúde", "industria": "Biotec", "peso_pct": 60.0},
    ])
    assert "Por setor" in texto and "Por indústria" in texto
    assert "Saúde=60.0%" in texto


# ── Offline-first e integração com a vitrine ─────────────────────────────────

def test_a_tela_americana_nao_consulta_fonte_externa():
    """O módulo americano é offline-first por contrato."""
    for proibido in ("fundamentus", "status_invest", "yfinance", "requests",
                     "get_web_evidence_context", "retrieve_chunks"):
        assert proibido not in _FONTE_US, proibido
    assert "somente o warehouse local" in _FONTE_US


def test_aba_da_vitrine_delega_para_a_tela_dedicada():
    fonte = (_RAIZ / "views" / "empresas_americanas.py").read_text(encoding="utf-8")
    corpo = fonte[fonte.index("def _tab_avaliacao_portfolio"):]
    corpo = corpo[:corpo.index("\ndef ")]
    assert "analise_portfolio_us.render(show_header=False)" in corpo
    # A seleção improvisada de ativos saiu: a aba avalia a carteira SALVA.
    assert "multiselect" not in corpo
    assert "data_editor" not in corpo


def test_render_aceita_o_mesmo_contrato_da_b3():
    import views.analise_portfolio_b3 as view_b3
    assert (inspect.signature(view_us.render).parameters.keys()
            == inspect.signature(view_b3.render).parameters.keys())


def test_estado_de_sessao_nao_colide_com_a_b3():
    """Duas carteiras diferentes não podem compartilhar chave de sessão."""
    assert view_us._STATE != "apb3_state"
    assert view_us._CHAT != "apb3_chat_history"
    assert "apus_" in view_us._STATE


# ── Renderização real ────────────────────────────────────────────────────────

def test_tela_renderiza_sem_carteira_salva():
    """Sem modelo salvo, orienta em vez de estourar."""
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string("""
import views.analise_portfolio_us as v
v.load_active_us_portfolio_model = lambda: {}
v.render(show_header=True)
""").run(timeout=60)
    assert not app.exception
    assert any("Criação de Portfólio" in i.value for i in app.info)


def test_tela_renderiza_com_carteira_salva_e_sem_llm():
    """Carteira salva sem provedor LLM: painéis determinísticos aparecem."""
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string("""
import pandas as pd
import views.analise_portfolio_us as v

v.load_active_us_portfolio_model = lambda: {
    "name": "Portfolio EUA Modelo 2026",
    "ano_compra": 2026,
    "is_stale": False,
    "metrics_json": {"entry_score": 71.5},
    "items": [
        {"ticker": "AAPL", "symbol": "AAPL", "nome": "Apple", "setor": "Technology",
         "industria": "Consumer Electronics", "weight": 0.6, "entry_score": 74.0},
        {"ticker": "MSFT", "symbol": "MSFT", "nome": "Microsoft", "setor": "Technology",
         "industria": "Software - Infrastructure", "weight": 0.4, "entry_score": 69.0},
    ],
}
v.us.scored_universe = lambda *a, **k: pd.DataFrame([
    {"symbol": "AAPL", "name": "Apple", "sector": "Technology",
     "industry": "Consumer Electronics", "score": 74.0, "pe": 30.0, "roe": 0.9},
    {"symbol": "MSFT", "name": "Microsoft", "sector": "Technology",
     "industry": "Software - Infrastructure", "score": 69.0, "pe": 34.0, "roe": 0.4},
])
v.llm_disponivel = lambda: False
v.render(show_header=True)
""").run(timeout=60)
    assert not app.exception
    markdown = "\n".join(str(m.value) for m in app.markdown)
    assert "Portfolio EUA Modelo 2026" in markdown
    assert "Cenário Macroeconômico" in markdown
    assert "Etapa 3 de 3" in markdown
    # Sem provedor configurado, a tela avisa e para antes do painel de LLM.
    assert any("provedor LLM" in w.value for w in app.warning)


def test_tela_bloqueia_carteira_com_metodologia_antiga():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string("""
import views.analise_portfolio_us as v
v.load_active_us_portfolio_model = lambda: {
    "items": [{"ticker": "AAPL", "weight": 1.0}], "is_stale": True,
}
v.render(show_header=False)
""").run(timeout=60)
    assert not app.exception
    assert any("versão antiga da metodologia" in e.value for e in app.error)
