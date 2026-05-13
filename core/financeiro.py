"""
core/financeiro.py
Camada de serviço financeiro — abstrai a fonte de dados entre mock e banco real.

USE_MOCK é controlado por settings.MOCK_MODE (lido do .env).
  MOCK_MODE=true  → retorna dados de core/mock_data.py (Fases 1–3)
  MOCK_MODE=false → executa queries SQL via core/database.py (Fase 4+)

Padrão de uso nas páginas:
    from core.financeiro import get_visao_geral
    dados = get_visao_geral()
    patrimonio = dados["patrimonio"]["total"]

Fase 4: implementar as funções _*_real() com SQLAlchemy.
"""
import streamlit as st

from core.config import settings


@st.cache_data(ttl=300)
def get_visao_geral() -> dict:
    """
    Retorna o dicionário completo de dados para o Dashboard Geral.

    Chaves retornadas:
        mes_referencia    str
        patrimonio        dict  (total, investido, saldo_bancario, delta_mes_pct, saude_score)
        fluxo_mes         dict  (receitas, despesas, economia, taxa_poupanca_pct, ...)
        historico_mensal  list  (6 meses: mes, receitas, despesas, patrimonio)
        categorias_despesa list (nome, gasto, orcamento, pct_usado)
        portfolio         dict  (rentabilidade_mes_pct, dividendos_mes, ...)
        classes_ativo     list  (nome, valor, pct_carteira, rentab_mes_pct, cor)
        alertas           list  (tipo, icone, titulo, descricao, acao, modulo)
        proximos_passos   list  (numero, urgencia, titulo, descricao, modulo)
    """
    if settings.MOCK_MODE:
        return _visao_geral_mock()
    return _visao_geral_real()


def _visao_geral_mock() -> dict:
    """Constrói a visão geral a partir de core/mock_data.py."""
    import core.mock_data as m

    return {
        "mes_referencia":   m.MES_REFERENCIA,
        "patrimonio":       m.PATRIMONIO,
        "fluxo_mes":        m.FLUXO_MES,
        "historico_mensal": m.HISTORICO_MENSAL,
        "categorias_despesa": m.CATEGORIAS_DESPESA,
        "portfolio":        m.PORTFOLIO,
        "classes_ativo":    m.CLASSES_ATIVO,
        "alertas":          m.ALERTAS_DASHBOARD,
        "proximos_passos":  m.PROXIMOS_PASSOS,
    }


def _visao_geral_real() -> dict:
    """
    Placeholder para queries SQL reais (Fase 4).
    Requer DATABASE_URL configurado no .env e banco com schema correto.
    """
    raise NotImplementedError(
        "Integração com banco de dados não implementada. "
        "Configure DATABASE_URL no .env e aguarde a Fase 4."
    )


# ── Helpers de cálculo (sem dependência de fonte de dados) ────────────────────

def calcular_saude_score(
    taxa_poupanca: float,
    meses_reserva: float,
    categorias_no_limite: int,
    total_categorias: int,
    rentabilidade_positiva: bool,
) -> int:
    """
    Calcula o score de saúde financeira (0–100).

    Componentes:
      40 pts → taxa de poupança ≥ 30% (proporcional)
      30 pts → reserva de emergência ≥ 6 meses (proporcional)
      20 pts → orçamento respeitado (proporção de categorias OK)
      10 pts → investimentos com rentabilidade positiva
    """
    score = 0.0

    # Taxa de poupança (meta: 30%)
    score += min(taxa_poupanca / 30.0, 1.0) * 40

    # Meses de reserva (meta: 6 meses)
    score += min(meses_reserva / 6.0, 1.0) * 30

    # Orçamento respeitado (categorias abaixo de 90% do limite)
    if total_categorias > 0:
        score += (categorias_no_limite / total_categorias) * 20

    # Rentabilidade positiva
    if rentabilidade_positiva:
        score += 10

    return round(score)
