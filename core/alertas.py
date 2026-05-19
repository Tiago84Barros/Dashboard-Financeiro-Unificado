"""
core/alertas.py
Engine de alertas financeiros — regras baseadas em dados reais.

Padrão: MOCK_MODE=true → alertas mock, false → alertas calculados do banco.

Regras implementadas (Fase 5.6):
  R1 — Orçamento próximo/estourado (v_budget_usage_mtd, usage_pct >= 75)
  R2 — Meta com alto progresso (financial_goals, pct >= 80%)
  R3 — Meta com prazo próximo (deadline em até 60 dias)
  R4 — asset_quotes vazia (sem cotações)
  R5 — budgets vazia (sem orçamentos reais)
  R6 — Saldo mensal negativo (v_monthly_cashflow, mês corrente)

Schema de cada alerta:
  tipo       str   "sucesso" | "alerta" | "erro" | "info"
  icone      str   emoji
  titulo     str
  descricao  str
  modulo     str
  data       str   contexto temporal ("Este mês", "Configuração", etc.)
"""
import logging
from datetime import date as _date

import streamlit as st

from core.config import settings

logger = logging.getLogger(__name__)

# ── Mock ──────────────────────────────────────────────────────────────────────
_ALERTAS_MOCK = [
    {
        "tipo":      "alerta",
        "icone":     "⚠️",
        "titulo":    "Orçamento de Alimentação próximo do limite",
        "descricao": "Você usou 88% do orçamento de Alimentação neste mês. "
                     "Limite: R$ 1.000,00 · Gasto: R$ 880,00.",
        "modulo":    "Controle Financeiro",
        "data":      "Este mês",
    },
    {
        "tipo":      "alerta",
        "icone":     "📈",
        "titulo":    "Cotações de ativos não atualizadas",
        "descricao": "A tabela `asset_quotes` está vazia. Acesse Configurações > "
                     "Atualização de Dados para atualizar via yfinance. Rentabilidade exibida como 0%.",
        "modulo":    "Configurações",
        "data":      "Configuração",
    },
    {
        "tipo":      "sucesso",
        "icone":     "🎯",
        "titulo":    "Meta 'Reserva de Emergência' atingiu 65%",
        "descricao": "Você acumulou R$ 19.500,00 dos R$ 30.000,00 planejados. "
                     "Continue assim!",
        "modulo":    "Metas",
        "data":      "Progresso",
    },
    {
        "tipo":      "info",
        "icone":     "📂",
        "titulo":    "Orçamentos mensais não cadastrados",
        "descricao": "Sem orçamentos reais, os limites são estimados como 120% dos gastos. "
                     "Cadastre orçamentos para alertas precisos.",
        "modulo":    "Controle Financeiro",
        "data":      "Configuração",
    },
    {
        "tipo":      "sucesso",
        "icone":     "💰",
        "titulo":    "Taxa de poupança acima de 70% neste mês",
        "descricao": "Receitas: R$ 8.500,00 · Despesas: R$ 2.400,00 · "
                     "Poupança: R$ 6.100,00 (71,8%). Ótimo resultado!",
        "modulo":    "Controle Financeiro",
        "data":      "Este mês",
    },
]

# ── SQL ───────────────────────────────────────────────────────────────────────
_SQL_BUDGET_USAGE = """
    SELECT category_name, amount_limit, amount_spent, usage_pct, amount_remaining
    FROM   v_budget_usage_mtd
    WHERE  user_id  = :uid
      AND  usage_pct >= 75
    ORDER  BY usage_pct DESC
    LIMIT  5
"""

_SQL_GOALS = """
    SELECT id::text, name, type, target_amount, current_amount, deadline
    FROM   financial_goals
    WHERE  user_id = :uid
      AND  active  = true
"""

_SQL_QUOTES_COUNT  = "SELECT COUNT(*) AS cnt FROM asset_quotes"
_SQL_BUDGETS_COUNT = "SELECT COUNT(*) AS cnt FROM budgets WHERE user_id = :uid::uuid"

_SQL_CASHFLOW_MES = """
    SELECT net_cashflow, total_income, total_expenses_abs
    FROM   v_monthly_cashflow
    WHERE  user_id    = :uid::uuid
      AND  month_year = DATE_TRUNC('month', CURRENT_DATE)::DATE
"""


# ─────────────────────────────────────────────────────────────────────────────
# API pública
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=120)
def get_alertas() -> dict:
    """
    Retorna {data_source, alertas[], contagem{sucesso, alerta, erro, info}}.
    TTL=120s — alertas mudam com relativa frequência ao longo do dia.
    """
    if settings.MOCK_MODE:
        return _build_result(_ALERTAS_MOCK, "mock")

    try:
        alertas = _alertas_real()
        return _build_result(alertas, "real")
    except Exception as exc:
        logger.warning(
            "[alertas] Banco real falhou (%s: %s) — usando mock.",
            type(exc).__name__,
            str(exc)[:120],
        )
        return _build_result(_ALERTAS_MOCK, "mock_fallback")


# ─────────────────────────────────────────────────────────────────────────────
# REAL — engine de regras
# ─────────────────────────────────────────────────────────────────────────────

def _alertas_real() -> list:
    from sqlalchemy import text
    from core.database import get_engine

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Engine indisponível.")

    owner = settings.OWNER_USER_ID
    if not owner:
        raise RuntimeError("OWNER_USER_ID não configurado.")

    alertas: list = []
    hoje = _date.today()

    with engine.connect() as conn:

        # ── R1: Orçamentos próximos/estourados ────────────────────────────────
        try:
            bud_rows = conn.execute(text(_SQL_BUDGET_USAGE), {"uid": owner}).fetchall()
            for r in bud_rows:
                pct  = float(r.usage_pct or 0)
                lim  = float(r.amount_limit or 0)
                rest = float(r.amount_remaining or 0)
                tipo = "erro" if pct >= 100 else "alerta"
                icone = "🔴" if pct >= 100 else "⚠️"
                if pct >= 100:
                    descr = (
                        f"Orçamento de R$ {lim:_.2f} estourado — "
                        f"gasto R$ {float(r.amount_spent or 0):_.2f}."
                    ).replace("_", ".").replace(",", ",")
                else:
                    descr = (
                        f"Usado {pct:.0f}% do limite de R$ {lim:_.2f}. "
                        f"Restam R$ {abs(rest):_.2f}."
                    ).replace("_", ".").replace(",", ",")
                alertas.append({
                    "tipo": tipo, "icone": icone,
                    "titulo":   f"Orçamento '{r.category_name}': {pct:.0f}% usado",
                    "descricao": descr,
                    "modulo":   "Controle Financeiro",
                    "data":     "Este mês",
                })
        except Exception as e:
            logger.debug("[alertas] R1 falhou: %s", e)

        # ── R2 + R3: Metas — progresso e prazo ───────────────────────────────
        try:
            goal_rows = conn.execute(text(_SQL_GOALS), {"uid": owner}).fetchall()
            for r in goal_rows:
                atual = float(r.current_amount or 0)
                alvo  = float(r.target_amount)
                pct   = round(atual / alvo * 100, 1) if alvo > 0 else 0.0
                prazo = r.deadline
                nome  = r.name

                if pct >= 100:
                    alertas.append({
                        "tipo":      "sucesso",
                        "icone":     "🏆",
                        "titulo":    f"Meta '{nome}' concluída!",
                        "descricao": f"Você atingiu 100% da meta de R$ {alvo:_.2f}. Parabéns!".replace("_", "."),
                        "modulo":    "Metas",
                        "data":      "Concluída",
                    })
                elif pct >= 80:
                    falta = alvo - atual
                    alertas.append({
                        "tipo":      "sucesso",
                        "icone":     "🎯",
                        "titulo":    f"Meta '{nome}' atingiu {pct:.0f}%",
                        "descricao": f"Faltam R$ {falta:_.2f} para concluir.".replace("_", "."),
                        "modulo":    "Metas",
                        "data":      "Progresso",
                    })

                if prazo and isinstance(prazo, _date) and pct < 100:
                    dias = (prazo - hoje).days
                    if 0 <= dias <= 60:
                        alertas.append({
                            "tipo":      "alerta",
                            "icone":     "⏰",
                            "titulo":    f"Meta '{nome}' vence em {dias} dia(s)",
                            "descricao": f"Progresso: {pct:.1f}%. Verifique o aporte necessário para cumprir o prazo.",
                            "modulo":    "Metas",
                            "data":      f"Vence: {prazo.strftime('%d/%m/%Y')}",
                        })
                    elif dias < 0:
                        alertas.append({
                            "tipo":      "erro",
                            "icone":     "🔴",
                            "titulo":    f"Meta '{nome}' está atrasada",
                            "descricao": f"Prazo venceu há {abs(dias)} dias. Progresso: {pct:.1f}%.",
                            "modulo":    "Metas",
                            "data":      "Atrasada",
                        })
        except Exception as e:
            logger.debug("[alertas] R2/R3 falhou: %s", e)

        # ── R4: asset_quotes vazia ────────────────────────────────────────────
        try:
            quotes_cnt = conn.execute(text(_SQL_QUOTES_COUNT)).scalar()
            if not quotes_cnt:
                alertas.append({
                    "tipo":      "alerta",
                    "icone":     "📊",
                    "titulo":    "Cotações de ativos não atualizadas",
                    "descricao": "A tabela `asset_quotes` está vazia. Acesse "
                                 "Configurações > Atualização de Dados para atualizar via yfinance. "
                                 "Sem cotações, a rentabilidade é exibida como 0%.",
                    "modulo":    "Configurações",
                    "data":      "Configuração",
                })
        except Exception as e:
            logger.debug("[alertas] R4 falhou: %s", e)

        # ── R5: budgets vazia ─────────────────────────────────────────────────
        try:
            bud_cnt = conn.execute(text(_SQL_BUDGETS_COUNT), {"uid": owner}).scalar()
            if not bud_cnt:
                alertas.append({
                    "tipo":      "info",
                    "icone":     "📂",
                    "titulo":    "Orçamentos mensais não cadastrados",
                    "descricao": "Sem orçamentos, os limites são estimados como 120% dos "
                                 "gastos reais. Cadastre orçamentos no Controle Financeiro "
                                 "para alertas de limite precisos.",
                    "modulo":    "Controle Financeiro",
                    "data":      "Configuração",
                })
        except Exception as e:
            logger.debug("[alertas] R5 falhou: %s", e)

        # ── R6: Cashflow do mês corrente ──────────────────────────────────────
        try:
            cf = conn.execute(text(_SQL_CASHFLOW_MES), {"uid": owner}).fetchone()
            if cf:
                net      = float(cf.net_cashflow or 0)
                receitas = float(cf.total_income or 0)
                despesas = float(cf.total_expenses_abs or 0)
                if net < 0:
                    alertas.append({
                        "tipo":      "alerta",
                        "icone":     "📉",
                        "titulo":    "Saldo negativo no mês atual",
                        "descricao": (
                            f"Despesas (R$ {despesas:_.2f}) superaram receitas "
                            f"(R$ {receitas:_.2f}) em R$ {abs(net):_.2f}."
                        ).replace("_", "."),
                        "modulo":    "Controle Financeiro",
                        "data":      "Este mês",
                    })
                elif receitas > 0:
                    taxa = round(net / receitas * 100, 1)
                    if taxa >= 30:
                        alertas.append({
                            "tipo":      "sucesso",
                            "icone":     "💰",
                            "titulo":    f"Taxa de poupança de {taxa:.0f}% este mês!",
                            "descricao": (
                                f"Receitas: R$ {receitas:_.2f} · "
                                f"Despesas: R$ {despesas:_.2f} · "
                                f"Poupado: R$ {net:_.2f}."
                            ).replace("_", "."),
                            "modulo":    "Controle Financeiro",
                            "data":      "Este mês",
                        })
        except Exception as e:
            logger.debug("[alertas] R6 falhou: %s", e)

    # Se nenhuma regra disparou → alerta positivo
    if not alertas:
        alertas.append({
            "tipo":      "sucesso",
            "icone":     "✅",
            "titulo":    "Tudo em ordem!",
            "descricao": "Nenhum alerta detectado nos dados deste mês. Continue assim!",
            "modulo":    "Sistema",
            "data":      "Agora",
        })

    return alertas


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────

def _build_result(alertas: list, data_source: str) -> dict:
    contagem: dict[str, int] = {"sucesso": 0, "alerta": 0, "erro": 0, "info": 0}
    for a in alertas:
        t = a.get("tipo", "info")
        contagem[t] = contagem.get(t, 0) + 1
    return {
        "data_source": data_source,
        "alertas":     alertas,
        "contagem":    contagem,
    }
