"""
core/metas.py
Camada de serviço de metas financeiras (financial_goals).

Padrão: MOCK_MODE=true → mock, false → real com fallback.

Tabela:
  financial_goals — id, user_id, name, type, target_amount,
                    current_amount, deadline, active

Tipos válidos: emergency_fund | travel | purchase | debt_payment | other

API pública:
  get_metas()                               → dict
  atualizar_progresso(goal_id, novo_valor)  → tuple[bool, str]
  inserir_meta(nome, tipo, alvo, atual, prazo) → tuple[bool, str]

Schema do dict retornado por get_metas():
  data_source    str
  total_metas    int
  metas_ativas   int
  metas_concl    int
  metas_atras    int
  total_atual    float
  total_alvo     float
  pct_geral      float
  metas          list   Ver _META_SCHEMA abaixo

_META_SCHEMA:
  id, nome, tipo, tipo_label, icone, atual, alvo, pct,
  prazo (date | None), prazo_fmt, aporte, status, status_badge
"""
import logging
from datetime import date as _date
from typing import Optional

import streamlit as st

from core.config import settings

logger = logging.getLogger(__name__)

_TIPO_LABEL: dict[str, str] = {
    "emergency_fund": "Reserva de Emergência",
    "travel":         "Viagem",
    "purchase":       "Compra",
    "debt_payment":   "Pag. de Dívida",
    "other":          "Outro",
}

_TIPO_ICONE: dict[str, str] = {
    "emergency_fund": "🛡️",
    "travel":         "✈️",
    "purchase":       "🛒",
    "debt_payment":   "💳",
    "other":          "🎯",
}

_MESES_PT = {
    1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr",
    5: "Mai", 6: "Jun", 7: "Jul", 8: "Ago",
    9: "Set", 10: "Out", 11: "Nov", 12: "Dez",
}

_STATUS_BADGE: dict[str, tuple] = {
    "concluida":    ("Concluída",    "sucesso"),
    "em_andamento": ("Em andamento", "info"),
    "atrasada":     ("Atrasada",     "erro"),
}

# ── Mock ──────────────────────────────────────────────────────────────────────
_MOCK_METAS_RAW = [
    ("1", "Reserva de Emergência", "emergency_fund", 19_500.00, 30_000.00, _date(2026, 12, 31)),
    ("2", "Viagem — Europa",       "travel",          6_000.00, 15_000.00, _date(2027,  7, 31)),
    ("3", "Troca do Carro",        "purchase",        5_500.00, 25_000.00, _date(2027, 12, 31)),
    ("4", "Fundo de Investimento", "other",          75_150.00,100_000.00, _date(2027,  6, 30)),
]

# ── SQL ───────────────────────────────────────────────────────────────────────
_SQL_METAS = """
    SELECT
        id::text,
        name,
        type,
        target_amount,
        current_amount,
        deadline
    FROM  financial_goals
    WHERE user_id = :uid
      AND active  = true
    ORDER BY deadline NULLS LAST, target_amount DESC
"""

_SQL_UPDATE_PROGRESSO = """
    UPDATE financial_goals
    SET    current_amount = :novo_valor
    WHERE  id       = :goal_id::uuid
      AND  user_id  = :uid::uuid
"""

_SQL_INSERT_META = """
    INSERT INTO financial_goals
        (user_id, name, type, target_amount, current_amount, deadline)
    VALUES
        (:uid::uuid, :nome, :tipo, :alvo, :atual, :prazo)
"""


# ─────────────────────────────────────────────────────────────────────────────
# API pública — leitura
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def get_metas() -> dict:
    """
    Retorna dict com resumo + lista de metas.
    TTL=60s — reflete inserções/atualizações recentes.
    """
    if settings.MOCK_MODE:
        d = _metas_mock()
        d["data_source"] = "mock"
        return d

    try:
        d = _metas_real()
        d["data_source"] = "real"
        return d
    except Exception as exc:
        logger.warning(
            "[metas] Banco real falhou (%s: %s) — usando mock.",
            type(exc).__name__,
            str(exc)[:120],
        )
        d = _metas_mock()
        d["data_source"] = "mock_fallback"
        return d


# ─────────────────────────────────────────────────────────────────────────────
# API pública — escrita
# ─────────────────────────────────────────────────────────────────────────────

def atualizar_progresso(goal_id: str, novo_valor: float) -> tuple:
    """Atualiza current_amount de uma meta específica."""
    if settings.MOCK_MODE:
        return False, "Modo mock ativo — UPDATE não executado. Defina MOCK_MODE=false para persistir dados."

    try:
        from sqlalchemy import text

        from core.database import get_engine

        engine = get_engine()
        if engine is None:
            return False, "Banco não configurado."

        owner = settings.OWNER_USER_ID
        if not owner:
            return False, "OWNER_USER_ID não configurado."

        with engine.begin() as conn:
            conn.execute(
                text(_SQL_UPDATE_PROGRESSO),
                {"goal_id": goal_id, "novo_valor": novo_valor, "uid": owner},
            )

        get_metas.clear()
        return True, ""

    except Exception as exc:
        logger.error("[metas] Falha ao atualizar progresso: %s", exc)
        return False, str(exc)


def inserir_meta(
    nome: str,
    tipo: str,
    alvo: float,
    atual: float,
    prazo: Optional[_date],
) -> tuple:
    """Insere nova meta em financial_goals."""
    if settings.MOCK_MODE:
        return False, "Modo mock ativo — INSERT não executado."

    try:
        from sqlalchemy import text

        from core.database import get_engine

        engine = get_engine()
        if engine is None:
            return False, "Banco não configurado."

        owner = settings.OWNER_USER_ID
        if not owner:
            return False, "OWNER_USER_ID não configurado."

        with engine.begin() as conn:
            conn.execute(
                text(_SQL_INSERT_META),
                {
                    "uid":   owner,
                    "nome":  nome.strip(),
                    "tipo":  tipo,
                    "alvo":  alvo,
                    "atual": atual,
                    "prazo": prazo,
                },
            )

        get_metas.clear()
        return True, ""

    except Exception as exc:
        logger.error("[metas] Falha ao inserir meta: %s", exc)
        return False, str(exc)


# ─────────────────────────────────────────────────────────────────────────────
# MOCK
# ─────────────────────────────────────────────────────────────────────────────

def _metas_mock() -> dict:
    raw = [
        {"id": id_, "nome": n, "tipo": t, "atual": a, "alvo": v, "prazo": p}
        for id_, n, t, a, v, p in _MOCK_METAS_RAW
    ]
    return _montar_dict(raw)


# ─────────────────────────────────────────────────────────────────────────────
# REAL
# ─────────────────────────────────────────────────────────────────────────────

def _metas_real() -> dict:
    from sqlalchemy import text

    from core.database import get_engine

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Engine indisponível.")

    owner = settings.OWNER_USER_ID
    if not owner:
        raise RuntimeError("OWNER_USER_ID não configurado.")

    with engine.connect() as conn:
        rows = conn.execute(text(_SQL_METAS), {"uid": owner}).fetchall()

    raw = [
        {
            "id":    r.id,
            "nome":  r.name,
            "tipo":  r.type or "other",
            "atual": float(r.current_amount) if r.current_amount is not None else 0.0,
            "alvo":  float(r.target_amount),
            "prazo": r.deadline,
        }
        for r in rows
    ]
    return _montar_dict(raw)


# ─────────────────────────────────────────────────────────────────────────────
# Helper compartilhado
# ─────────────────────────────────────────────────────────────────────────────

def _montar_dict(raw: list) -> dict:
    hoje = _date.today()
    metas = []

    for m in raw:
        atual = float(m["atual"])
        alvo  = float(m["alvo"])
        pct   = round(atual / alvo * 100, 1) if alvo > 0 else 0.0
        prazo = m.get("prazo")

        # Status
        if pct >= 100:
            status = "concluida"
        elif prazo and isinstance(prazo, _date) and prazo < hoje and pct < 100:
            status = "atrasada"
        else:
            status = "em_andamento"

        # Prazo formatado
        if prazo and isinstance(prazo, _date):
            prazo_fmt = f"{_MESES_PT[prazo.month]} {prazo.year}"
        else:
            prazo_fmt = "Sem prazo"

        # Aporte mensal sugerido
        faltando = max(0.0, alvo - atual)
        aporte = 0.0
        if prazo and isinstance(prazo, _date) and prazo > hoje:
            meses_rest = (prazo.year - hoje.year) * 12 + (prazo.month - hoje.month)
            aporte = round(faltando / meses_rest, 2) if meses_rest > 0 else faltando

        tipo = m.get("tipo", "other")
        metas.append({
            "id":           m["id"],
            "nome":         m["nome"],
            "tipo":         tipo,
            "tipo_label":   _TIPO_LABEL.get(tipo, tipo.replace("_", " ").title()),
            "icone":        _TIPO_ICONE.get(tipo, "🎯"),
            "atual":        round(atual, 2),
            "alvo":         round(alvo, 2),
            "pct":          pct,
            "prazo":        prazo,
            "prazo_fmt":    prazo_fmt,
            "aporte":       aporte,
            "status":       status,
            "status_badge": _STATUS_BADGE.get(status, ("Em andamento", "info")),
        })

    total_atual  = sum(m["atual"] for m in metas)
    total_alvo   = sum(m["alvo"]  for m in metas)
    pct_geral    = round(total_atual / total_alvo * 100, 1) if total_alvo > 0 else 0.0

    return {
        "metas":        metas,
        "total_metas":  len(metas),
        "metas_ativas": sum(1 for m in metas if m["status"] == "em_andamento"),
        "metas_concl":  sum(1 for m in metas if m["status"] == "concluida"),
        "metas_atras":  sum(1 for m in metas if m["status"] == "atrasada"),
        "total_atual":  round(total_atual, 2),
        "total_alvo":   round(total_alvo, 2),
        "pct_geral":    pct_geral,
        # data_source injetado pelo caller
    }
