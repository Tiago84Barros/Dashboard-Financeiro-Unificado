"""
core/financeiro.py
Camada de serviço financeiro — abstrai a fonte de dados entre mock e banco real.

USE_MOCK é controlado por settings.MOCK_MODE (lido do .env ou de Streamlit Secrets).
  MOCK_MODE=true  → retorna dados de core/mock_data.py
  MOCK_MODE=false → executa queries SQL via core/database.py

Chave "data_source" sempre presente no dict retornado:
  "real"          → dados do banco, tudo OK
  "mock"          → MOCK_MODE=true, mock intencional
  "mock_fallback" → banco falhou, caiu no mock automaticamente

Views consultadas (Fase 4.9):
  v_net_worth, v_monthly_cashflow, v_category_spending_mtd,
  v_investment_summary, v_budget_usage_mtd, dividends

Padrão de uso nas páginas:
    from core.financeiro import get_visao_geral
    dados = get_visao_geral()
    fonte = dados["data_source"]   # "real" | "mock" | "mock_fallback"
    patrimonio = dados["patrimonio"]["total"]
"""
import logging

import streamlit as st

from core.config import settings

logger = logging.getLogger(__name__)


@st.cache_data(ttl=300)
def get_visao_geral() -> dict:
    """
    Retorna o dicionário completo de dados para o Dashboard Geral.

    Chaves retornadas:
        data_source       str   "real" | "mock" | "mock_fallback"
        mes_referencia    str
        patrimonio        dict  (total, investido, saldo_bancario, delta_mes_pct, saude_score)
        fluxo_mes         dict  (receitas, despesas, economia, taxa_poupanca_pct, ...)
        historico_mensal  list  (6 meses: mes, receitas, despesas, economia, patrimonio)
        categorias_despesa list (nome, gasto, orcamento, pct_usado)
        portfolio         dict  (rentabilidade_mes_pct, dividendos_mes, ...)
        classes_ativo     list  (nome, valor, pct_carteira, rentab_mes_pct, cor)
        alertas           list  (tipo, icone, titulo, descricao, acao, modulo)
        proximos_passos   list  (numero, urgencia, titulo, descricao, modulo)
    """
    if settings.MOCK_MODE:
        dados = _visao_geral_mock()
        dados["data_source"] = "mock"
        return dados

    # Modo real: tenta banco — fallback para mock em caso de qualquer erro
    try:
        dados = _visao_geral_real()
        dados["data_source"] = "real"
        return dados
    except Exception as exc:
        logger.warning(
            "[financeiro] Banco real falhou (%s: %s) — usando mock.",
            type(exc).__name__,
            str(exc)[:120],
        )
        dados = _visao_geral_mock()
        dados["data_source"] = "mock_fallback"
        return dados


# ─────────────────────────────────────────────────────────────────────────────
# MOCK
# ─────────────────────────────────────────────────────────────────────────────

def _visao_geral_mock() -> dict:
    """Constrói a visão geral a partir de core/mock_data.py."""
    import core.mock_data as m

    return {
        "mes_referencia":     m.MES_REFERENCIA,
        "patrimonio":         m.PATRIMONIO,
        "fluxo_mes":          m.FLUXO_MES,
        "historico_mensal":   m.HISTORICO_MENSAL,
        "categorias_despesa": m.CATEGORIAS_DESPESA,
        "portfolio":          m.PORTFOLIO,
        "classes_ativo":      m.CLASSES_ATIVO,
        "alertas":            m.ALERTAS_DASHBOARD,
        "proximos_passos":    m.PROXIMOS_PASSOS,
        # data_source injetado pelo caller (get_visao_geral)
    }


# ─────────────────────────────────────────────────────────────────────────────
# REAL — queries nas views do Supabase
# ─────────────────────────────────────────────────────────────────────────────

# Mapeamentos de classe de ativo
_CLASS_LABEL: dict[str, str] = {
    "reit":         "FII",
    "stock":        "Ações BR",
    "fixed_income": "Renda Fixa",
    "etf":          "ETF",
    "bdr":          "BDR",
    "crypto":       "Cripto",
}
_CLASS_COR: dict[str, str] = {
    "reit":         "#9B59B6",
    "stock":        "#00C896",
    "fixed_income": "#4A9EFF",
    "etf":          "#F6C90E",
    "bdr":          "#FC5C7D",
    "crypto":       "#FC5C7D",
}
_MESES_PT = {
    1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr",
    5: "Mai", 6: "Jun", 7: "Jul", 8: "Ago",
    9: "Set", 10: "Out", 11: "Nov", 12: "Dez",
}


def _visao_geral_real() -> dict:
    """
    Consulta as views do Supabase e monta o dict de visão geral.
    Retorna o mesmo schema que _visao_geral_mock().

    Qualquer exceção propaga para o caller → get_visao_geral() faz fallback.

    SEGURANÇA:
      - Nunca expõe connection strings nem credenciais.
      - Filtra todas as queries por OWNER_USER_ID.
      - Não executa DDL, DML de escrita nem SQL parametrizado com dados do usuário.
    """
    from sqlalchemy import text
    from core.database import get_engine

    # ── Pré-condições ─────────────────────────────────────────────────────
    engine = get_engine()
    if engine is None:
        raise RuntimeError(
            "Engine indisponível — configure SUPABASE_UNIFICADO_URL "
            "no .env local ou em Streamlit Secrets."
        )

    owner = settings.OWNER_USER_ID
    if not owner:
        raise RuntimeError("OWNER_USER_ID não configurado — filtro de usuário inativo.")

    with engine.connect() as conn:

        # ── 1. Patrimônio (v_net_worth) ───────────────────────────────────
        nw_row = conn.execute(
            text(
                "SELECT bank_balance, investment_total, net_worth "
                "FROM v_net_worth WHERE user_id = :uid"
            ),
            {"uid": owner},
        ).fetchone()

        if nw_row is None:
            raise RuntimeError(
                "v_net_worth retornou vazio — sem dados para este usuário. "
                "Verifique OWNER_USER_ID e as migrações."
            )

        bank_balance     = float(nw_row.bank_balance     or 0)
        investment_total = float(nw_row.investment_total or 0)
        net_worth_total  = float(nw_row.net_worth        or 0)

        # ── 2. Fluxo de caixa mensal (v_monthly_cashflow) ─────────────────
        cashflow_rows = conn.execute(
            text(
                "SELECT month_year, total_income, total_expenses_abs, net_cashflow "
                "FROM v_monthly_cashflow "
                "WHERE user_id = :uid "
                "ORDER BY month_year DESC LIMIT 7"
            ),
            {"uid": owner},
        ).fetchall()

        # ── 3. Categorias do mês (v_category_spending_mtd) ────────────────
        cat_rows = conn.execute(
            text(
                "SELECT category_name, total_spent "
                "FROM v_category_spending_mtd "
                "WHERE user_id = :uid "
                "ORDER BY total_spent DESC LIMIT 10"
            ),
            {"uid": owner},
        ).fetchall()

        # ── 4. Orçamentos do mês (v_budget_usage_mtd) ─────────────────────
        budget_rows = conn.execute(
            text(
                "SELECT category_name, amount_limit, amount_spent, usage_pct "
                "FROM v_budget_usage_mtd "
                "WHERE user_id = :uid "
                "ORDER BY usage_pct DESC NULLS LAST"
            ),
            {"uid": owner},
        ).fetchall()

        # ── 5. Resumo de investimentos (v_investment_summary) ─────────────
        inv_rows = conn.execute(
            text(
                "SELECT asset_class, asset_count, total_invested, "
                "       current_market_value, return_pct "
                "FROM v_investment_summary "
                "WHERE user_id = :uid "
                "ORDER BY total_invested DESC"
            ),
            {"uid": owner},
        ).fetchall()

        # ── 6. Dividendos (dividends) ──────────────────────────────────────
        div_row = conn.execute(
            text(
                "SELECT "
                "  COALESCE(SUM(CASE WHEN date_trunc('month', ex_date) = "
                "    date_trunc('month', CURRENT_DATE) THEN total_amount ELSE 0 END), 0) AS div_mes, "
                "  COALESCE(SUM(CASE WHEN EXTRACT(year FROM ex_date) = "
                "    EXTRACT(year FROM CURRENT_DATE) THEN total_amount ELSE 0 END), 0) AS div_ano "
                "FROM dividends WHERE user_id = :uid"
            ),
            {"uid": owner},
        ).fetchone()

    carteira_migrada = None
    proventos_migrados = None
    try:
        from core.investimentos import get_carteira
        from core.proventos import get_proventos

        carteira_migrada = get_carteira()
        proventos_migrados = get_proventos()
    except Exception as exc:
        logger.warning("[financeiro] Carteira migrada indisponivel no dashboard geral: %s", exc)

    if carteira_migrada and carteira_migrada.get("data_source") == "real":
        investment_total = float(carteira_migrada.get("total_mercado") or investment_total)
        net_worth_total = bank_balance + investment_total

    # ── Helpers ───────────────────────────────────────────────────────────
    def _f(v) -> float:
        return float(v) if v is not None else 0.0

    # ── Cashflow: index 0 = mês mais recente (já ordenado DESC) ──────────
    cf_list = [
        {
            "month_year": r.month_year,
            "income":     _f(r.total_income),
            "expenses":   _f(r.total_expenses_abs),
            "economia":   _f(r.net_cashflow),
        }
        for r in cashflow_rows
    ]

    cur_cf  = cf_list[0] if len(cf_list) > 0 else None
    prev_cf = cf_list[1] if len(cf_list) > 1 else None

    # Mês referência
    if cur_cf:
        my = cur_cf["month_year"]
        mes_ref = f"{_MESES_PT[my.month]} {my.year}"
    else:
        from datetime import date as _date
        today = _date.today()
        mes_ref = f"{_MESES_PT[today.month]} {today.year}"

    receitas  = cur_cf["income"]   if cur_cf else 0.0
    despesas  = cur_cf["expenses"] if cur_cf else 0.0
    economia  = cur_cf["economia"] if cur_cf else 0.0
    taxa_poup = (economia / receitas * 100) if receitas > 0 else 0.0

    rec_ant  = prev_cf["income"]   if prev_cf else 0.0
    desp_ant = prev_cf["expenses"] if prev_cf else 0.0
    econ_ant = prev_cf["economia"] if prev_cf else 0.0
    taxa_ant = (econ_ant / rec_ant * 100) if rec_ant > 0 else 0.0

    meses_reserva = (bank_balance / despesas) if despesas > 0 else 0.0

    # Delta patrimônio mês a mês (aproximação pelo net_cashflow do mês atual)
    base_prev       = net_worth_total - economia
    delta_mes_pct   = (economia / base_prev * 100) if base_prev > 0 else 0.0
    delta_mes_valor = economia

    # ── Histórico mensal (últimos 6, ordem cronológica) ───────────────────
    hist_6 = cf_list[:6][::-1]
    historico_mensal = [
        {
            "mes":        _MESES_PT[r["month_year"].month],
            "receitas":   r["income"],
            "despesas":   r["expenses"],
            "economia":   r["economia"],
            "patrimonio": net_worth_total if i == len(hist_6) - 1 else 0.0,
        }
        for i, r in enumerate(hist_6)
    ]

    # ── Categorias de despesa ─────────────────────────────────────────────
    maior_cat_nome  = cat_rows[0].category_name if cat_rows else "—"
    maior_cat_valor = _f(cat_rows[0].total_spent) if cat_rows else 0.0

    budget_map: dict[str, tuple] = {}
    cat_alerta_nome = maior_cat_nome
    cat_alerta_pct  = 0.0

    if budget_rows:
        # Há orçamentos configurados → usar usage_pct real
        budget_map = {
            r.category_name: (_f(r.amount_limit), _f(r.amount_spent), _f(r.usage_pct))
            for r in budget_rows
        }
        cat_alerta_nome = budget_rows[0].category_name
        cat_alerta_pct  = _f(budget_rows[0].usage_pct)

    categorias_despesa = []
    for r in cat_rows[:7]:
        nome  = r.category_name
        gasto = _f(r.total_spent)
        if nome in budget_map:
            orcamento = budget_map[nome][0]
            pct_usado = budget_map[nome][2]
        else:
            # Sem orçamento: referência = gasto × 1.2 (83 % de uso → visual limpo)
            orcamento = round(gasto * 1.2, 2)
            pct_usado = round((gasto / orcamento * 100), 1) if orcamento > 0 else 0.0
        categorias_despesa.append(
            {"nome": nome, "gasto": gasto, "orcamento": orcamento, "pct_usado": pct_usado}
        )

    cats_no_limite = sum(1 for c in categorias_despesa if c["pct_usado"] >= 90)

    # ── Score de saúde ────────────────────────────────────────────────────
    saude_score = calcular_saude_score(
        taxa_poupanca=taxa_poup,
        meses_reserva=meses_reserva,
        categorias_no_limite=cats_no_limite,
        total_categorias=len(categorias_despesa),
        rentabilidade_positiva=False,   # cotações ausentes → retorno = 0
    )

    # ── Classes de ativos ─────────────────────────────────────────────────
    classes_ativo = []
    if carteira_migrada and carteira_migrada.get("data_source") == "real":
        for c in carteira_migrada.get("por_classe", []):
            classes_ativo.append({
                "nome":           c["nome"],
                "valor":          _f(c.get("valor_mercado")),
                "pct_carteira":   _f(c.get("pct_carteira")),
                "rentab_mes_pct": _f(c.get("rentab_pct")),
                "cor":            c.get("cor", "#718096"),
            })
        num_ativos = int(carteira_migrada.get("num_ativos") or 0)
    else:
        total_val_all = sum(_f(r.current_market_value) for r in inv_rows)
        for r in inv_rows:
            cls_val  = _f(r.current_market_value)
            cls_pct  = (cls_val / total_val_all * 100) if total_val_all > 0 else 0.0
            classes_ativo.append(
                {
                    "nome":           _CLASS_LABEL.get(r.asset_class, r.asset_class.title()),
                    "valor":          cls_val,
                    "pct_carteira":   round(cls_pct, 1),
                    "rentab_mes_pct": _f(r.return_pct),
                    "cor":            _CLASS_COR.get(r.asset_class, "#718096"),
                }
            )
        num_ativos = sum(r.asset_count for r in inv_rows) if inv_rows else 0
    maior_cls  = classes_ativo[0] if classes_ativo else {"nome": "—", "pct_carteira": 0.0}

    # ── Portfolio ─────────────────────────────────────────────────────────
    dividendos_mes = _f(div_row.div_mes) if div_row else 0.0
    dividendos_ano = _f(div_row.div_ano) if div_row else 0.0
    if proventos_migrados and proventos_migrados.get("data_source") == "real":
        dividendos_ano = _f(proventos_migrados.get("total_12m", dividendos_ano))
    rentab_invest = (
        _f(carteira_migrada.get("rentabilidade_total_pct"))
        if carteira_migrada and carteira_migrada.get("data_source") == "real"
        else 0.0
    )

    portfolio = {
        "rentabilidade_mes_pct":          rentab_invest,
        "rentabilidade_ano_pct":          rentab_invest,
        "rentabilidade_mes_anterior_pct": 0.0,
        "dividendos_mes":                 dividendos_mes,
        "dividendos_ano":                 dividendos_ano,
        "maior_concentracao_nome":        maior_cls["nome"],
        "maior_concentracao_pct":         maior_cls.get("pct_carteira", 0.0),
        "meta_concentracao_pct":          25.0,
        "num_ativos":                     num_ativos,
    }

    # ── Alertas e próximos passos a partir dos dados reais ────────────────
    alertas         = _gerar_alertas_real(meses_reserva, taxa_poup, maior_cls, cat_alerta_nome, cat_alerta_pct)
    proximos_passos = _gerar_proximos_passos_real(meses_reserva, taxa_poup, maior_cls)

    return {
        "mes_referencia":     mes_ref,
        "patrimonio": {
            "total":           net_worth_total,
            "investido":       investment_total,
            "saldo_bancario":  bank_balance,
            "delta_mes_pct":   round(delta_mes_pct, 1),
            "delta_mes_valor": round(delta_mes_valor, 2),
            "saude_score":     saude_score,
        },
        "fluxo_mes": {
            "receitas":               receitas,
            "despesas":               despesas,
            "economia":               economia,
            "taxa_poupanca_pct":      round(taxa_poup, 1),
            "receitas_mes_anterior":  rec_ant,
            "despesas_mes_anterior":  desp_ant,
            "economia_mes_anterior":  econ_ant,
            "taxa_poupanca_anterior": round(taxa_ant, 1),
            "meses_reserva":          round(meses_reserva, 1),
            "meta_meses_reserva":     6.0,
            "maior_categoria":        maior_cat_nome,
            "maior_categoria_valor":  maior_cat_valor,
            "categoria_alerta":       cat_alerta_nome,
            "categoria_alerta_pct":   cat_alerta_pct,
        },
        "historico_mensal":   historico_mensal,
        "categorias_despesa": categorias_despesa,
        "portfolio":          portfolio,
        "classes_ativo":      classes_ativo,
        "alertas":            alertas,
        "proximos_passos":    proximos_passos,
        # data_source injetado pelo caller (get_visao_geral)
    }


def _gerar_alertas_real(
    meses_reserva: float,
    taxa_poupanca: float,
    maior_cls: dict,
    cat_alerta_nome: str,
    cat_alerta_pct: float,
) -> list:
    """Gera lista de alertas calculados a partir dos dados reais."""
    alertas = []

    # Reserva de emergência
    if meses_reserva < 3:
        alertas.append({
            "tipo":      "alerta",
            "icone":     "🛡️",
            "titulo":    f"Reserva de emergência em {meses_reserva:.1f}×",
            "descricao": f"Saldo cobre {meses_reserva:.1f}× as despesas mensais. Meta: 6×.",
            "acao":      "Metas",
            "modulo":    "🎯 Metas",
        })
    elif meses_reserva < 6:
        alertas.append({
            "tipo":      "info",
            "icone":     "🛡️",
            "titulo":    f"Reserva em {meses_reserva:.1f}× (meta: 6×)",
            "descricao": f"Continue aportando. Você está em {meses_reserva:.1f}× as despesas.",
            "acao":      "Metas",
            "modulo":    "🎯 Metas",
        })

    # Categoria próxima do limite (apenas se houver orçamento)
    if cat_alerta_pct >= 90:
        alertas.append({
            "tipo":      "alerta",
            "icone":     "⚠️",
            "titulo":    f"{cat_alerta_nome} próximo do limite",
            "descricao": f"{cat_alerta_pct:.0f}% do orçamento utilizado.",
            "acao":      "Controle Financeiro",
            "modulo":    "💰 Controle Financeiro",
        })

    # Concentração elevada na carteira
    if maior_cls.get("pct_carteira", 0) > 40:
        alertas.append({
            "tipo":      "info",
            "icone":     "📊",
            "titulo":    f"Alta concentração em {maior_cls['nome']}",
            "descricao": (
                f"{maior_cls['nome']} representa {maior_cls['pct_carteira']:.1f}% "
                "da carteira. Considere diversificar."
            ),
            "acao":      "Carteira",
            "modulo":    "💼 Carteira",
        })

    # Taxa de poupança boa
    if taxa_poupanca >= 30:
        alertas.append({
            "tipo":      "sucesso",
            "icone":     "🎉",
            "titulo":    "Taxa de poupança acima da meta",
            "descricao": f"Você poupou {taxa_poupanca:.1f}% da renda este mês. Meta: 30%.",
            "acao":      "Metas",
            "modulo":    "🎯 Metas",
        })

    # Garantia: ao menos um alerta
    if not alertas:
        alertas.append({
            "tipo":      "info",
            "icone":     "✅",
            "titulo":    "Finanças em dia",
            "descricao": "Nenhum alerta crítico para este mês.",
            "acao":      "",
            "modulo":    "📊 Dashboard Geral",
        })

    return alertas


def _gerar_proximos_passos_real(
    meses_reserva: float,
    taxa_poupanca: float,
    maior_cls: dict,
) -> list:
    """Gera lista de próximos passos recomendados a partir dos dados reais."""
    passos: list[dict] = []
    n = 1

    if meses_reserva < 6:
        passos.append({
            "numero":    n,
            "urgencia":  "alta" if meses_reserva < 3 else "media",
            "titulo":    "Reforçar reserva de emergência",
            "descricao": f"Meta: 6× as despesas. Atual: {meses_reserva:.1f}×.",
            "modulo":    "🎯 Metas",
        })
        n += 1

    if maior_cls.get("pct_carteira", 0) > 40:
        passos.append({
            "numero":    n,
            "urgencia":  "media",
            "titulo":    f"Diversificar além de {maior_cls['nome']}",
            "descricao": (
                f"{maior_cls['nome']} em {maior_cls['pct_carteira']:.1f}% — "
                "acima do alvo de 40%. Diluir nos próximos aportes."
            ),
            "modulo":    "💼 Carteira",
        })
        n += 1

    if taxa_poupanca < 10:
        passos.append({
            "numero":    n,
            "urgencia":  "alta",
            "titulo":    "Aumentar taxa de poupança",
            "descricao": f"Taxa atual: {taxa_poupanca:.1f}%. Meta: ≥ 30%.",
            "modulo":    "💰 Controle Financeiro",
        })
        n += 1

    # Sempre inclui: alimentar cotações
    passos.append({
        "numero":    n,
        "urgencia":  "baixa",
        "titulo":    "Alimentar cotações de mercado",
        "descricao": (
            "Cadastre preços em asset_quotes para ativar rentabilidade real "
            "e valor de mercado da carteira."
        ),
        "modulo":    "💼 Carteira",
    })

    return passos[:4]  # máximo 4 itens


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de cálculo (sem dependência de fonte de dados)
# ─────────────────────────────────────────────────────────────────────────────

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
