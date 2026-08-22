"""
data_pipeline/quality/updater.py
Gravação das correções — adaptador sobre core.data_healing.apply_healing.

A escrita já é segura (dry-run por padrão no preview; apply grava com backup
em `multiplos_healing_backup` e auditoria em `data_healing_audit`). Este módulo
expõe a leitura do histórico para relatórios/dashboard de forma sanitizada.
"""
from __future__ import annotations

import logging

import pandas as pd

from core.data_healing import apply_healing  # re-export (responsabilidade: gravar)

logger = logging.getLogger(__name__)

__all__ = ["apply_healing", "recent_history"]


def recent_history(limit: int = 100) -> pd.DataFrame:
    """Lê as últimas alterações de `data_healing_audit` (empresa, campo, antes,
    depois, fonte, ação, data). Vazio se a tabela não existir."""
    try:
        from sqlalchemy import text

        from core.database import get_engine
    except Exception:
        return pd.DataFrame()
    engine = get_engine()
    if engine is None:
        return pd.DataFrame()
    try:
        with engine.connect() as conn:
            exists = conn.execute(text("""
                SELECT EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema='public' AND table_name='data_healing_audit')
            """)).scalar()
            if not exists:
                return pd.DataFrame()
            return pd.read_sql_query(text("""
                SELECT run_ts, ticker, indicador, valor_antigo, valor_novo,
                       fonte, acao, n_fontes, motivo
                FROM data_healing_audit
                ORDER BY id DESC
                LIMIT :lim
            """), conn, params={"lim": int(limit)})
    except Exception as exc:
        logger.warning("recent_history: %s", exc)
        return pd.DataFrame()
