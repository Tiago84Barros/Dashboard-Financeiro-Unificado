"""
data_pipeline/jobs/update_b3_fundamentals.py
Atualiza demonstrações financeiras e múltiplos B3 (anuais e trimestrais).

Tabelas atualizadas:
  - public."Demonstracoes_Financeiras"     (DRE anual)
  - public."Demonstracoes_Financeiras_TRI" (DRE trimestral)
  - public.multiplos                       (múltiplos anuais)
  - public.multiplos_TRI                   (múltiplos trimestrais)

Fontes:
  - yfinance: financials, balance_sheet, cashflow (anual e trimestral)
  - Fundamentus + Status Invest: múltiplos correntes via reconciliação
"""
from __future__ import annotations

import logging
import time
from datetime import date

logger = logging.getLogger(__name__)

TABLE_DEMO      = '"Demonstracoes_Financeiras"'
TABLE_DEMO_TRI  = '"Demonstracoes_Financeiras_TRI"'
TABLE_MULT      = "multiplos"
TABLE_MULT_TRI  = '"multiplos_TRI"'
SOURCE_NAME     = "B3 / yfinance + Fundamentus"
JOB_NAME        = "update_b3_fundamentals"


def run() -> dict:
    result = {
        "status":           "success",
        "table_name":       "Demonstracoes_Financeiras, multiplos",
        "source_name":      SOURCE_NAME,
        "job_name":         JOB_NAME,
        "records_inserted": 0,
        "records_updated":  0,
        "records_failed":   0,
        "error_message":    None,
    }

    try:
        import yfinance as yf
        import pandas as pd
    except ImportError as e:
        result["status"] = "failed"
        result["error_message"] = f"Dependência não instalada: {e}"
        return result

    from data_pipeline.utils.db_utils import get_pipeline_engine
    from sqlalchemy import text, inspect as sa_inspect

    engine = get_pipeline_engine()
    if engine is None:
        result["status"] = "failed"
        result["error_message"] = "Banco não conectado"
        return result

    # Reutiliza lógica do backfill existente
    try:
        from scripts.backfill_b3_fundamentals import (
            yf_annual_rows,
            current_web_row,
            collect_changes_for_table,
            apply_changes,
            fetch_existing,
            table_columns,
            clean_ticker,
            DEMO_COLS,
            MULT_COLS,
        )
    except ImportError as e:
        result["status"] = "failed"
        result["error_message"] = f"Não foi possível importar backfill_b3_fundamentals: {e}"
        return result

    # Busca tickers da tabela setores
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT DISTINCT ticker FROM public.setores WHERE ticker IS NOT NULL ORDER BY ticker")
            ).fetchall()
            tickers = [r[0].strip().upper().replace(".SA", "") for r in rows if r[0]]
    except Exception as exc:
        result["status"] = "failed"
        result["error_message"] = f"Erro ao listar tickers de setores: {exc}"
        return result

    if not tickers:
        result["status"] = "skipped"
        result["error_message"] = "Nenhum ticker encontrado na tabela setores"
        return result

    logger.info("update_b3_fundamentals: %d tickers encontrados", len(tickers))

    # Verifica quais tabelas existem e suas colunas
    with engine.connect() as conn:
        existing_tables = set(sa_inspect(conn).get_table_names(schema="public"))

    tables_present = {
        TABLE_DEMO:     TABLE_DEMO.strip('"')     in existing_tables,
        TABLE_DEMO_TRI: TABLE_DEMO_TRI.strip('"') in existing_tables,
        TABLE_MULT:     TABLE_MULT                in existing_tables,
        TABLE_MULT_TRI: TABLE_MULT_TRI            in existing_tables,
    }

    if not any(tables_present.values()):
        result["status"] = "skipped"
        result["error_message"] = "Nenhuma tabela de fundamentais encontrada no banco"
        return result

    # Carrega colunas das tabelas presentes
    cols_by_table: dict[str, set] = {}
    with engine.connect() as conn:
        for tbl, present in tables_present.items():
            if present:
                cols_by_table[tbl] = table_columns(conn, tbl)

    first_errors: list[str] = []

    for tk in tickers:
        try:
            # ── Dados anuais via yfinance ──────────────────────────────────────
            annual = yf_annual_rows(tk, years=8)

            with engine.begin() as conn:
                changes = []

                if tables_present[TABLE_DEMO]:
                    demo_gen = {dt: {k: v for k, v in row.items() if k in DEMO_COLS}
                                for dt, row in annual.items()}
                    demo_ex  = fetch_existing(conn, TABLE_DEMO, tk)
                    changes += collect_changes_for_table(
                        TABLE_DEMO, tk, demo_gen, demo_ex,
                        cols_by_table[TABLE_DEMO], True, "yfinance-anual",
                    )

                if tables_present[TABLE_MULT]:
                    mult_gen = {dt: {k: v for k, v in row.items() if k in MULT_COLS}
                                for dt, row in annual.items()}
                    mult_ex  = fetch_existing(conn, TABLE_MULT, tk)
                    changes += collect_changes_for_table(
                        TABLE_MULT, tk, mult_gen, mult_ex,
                        cols_by_table[TABLE_MULT], True, "yfinance-anual",
                    )
                    # Múltiplos correntes via Fundamentus/Status Invest
                    web = current_web_row(tk)
                    if web:
                        latest_dt = max(mult_ex.keys(), default=date.today())
                        changes += collect_changes_for_table(
                            TABLE_MULT, tk, {latest_dt: web}, mult_ex,
                            cols_by_table[TABLE_MULT], True, "web-atual",
                        )

                # ── Dados trimestrais via yfinance ─────────────────────────────
                if tables_present[TABLE_DEMO_TRI] or tables_present[TABLE_MULT_TRI]:
                    quarterly = _yf_quarterly_rows(tk)

                    if tables_present[TABLE_DEMO_TRI]:
                        demo_tri_gen = {dt: {k: v for k, v in row.items() if k in DEMO_COLS}
                                        for dt, row in quarterly.items()}
                        demo_tri_ex  = fetch_existing(conn, TABLE_DEMO_TRI, tk)
                        changes += collect_changes_for_table(
                            TABLE_DEMO_TRI, tk, demo_tri_gen, demo_tri_ex,
                            cols_by_table[TABLE_DEMO_TRI], True, "yfinance-tri",
                        )

                    if tables_present[TABLE_MULT_TRI]:
                        mult_tri_gen = {dt: {k: v for k, v in row.items() if k in MULT_COLS}
                                        for dt, row in quarterly.items()}
                        mult_tri_ex  = fetch_existing(conn, TABLE_MULT_TRI, tk)
                        changes += collect_changes_for_table(
                            TABLE_MULT_TRI, tk, mult_tri_gen, mult_tri_ex,
                            cols_by_table[TABLE_MULT_TRI], True, "yfinance-tri",
                        )

                if changes:
                    apply_changes(conn, changes, cols_by_table)
                    inserts  = sum(1 for c in changes if c.action == "insert")
                    updates  = sum(1 for c in changes if c.action == "update")
                    result["records_inserted"] += inserts
                    result["records_updated"]  += updates

            time.sleep(0.5)

        except Exception as exc:
            logger.warning("update_b3_fundamentals: erro em %s: %s", tk, exc)
            result["records_failed"] += 1
            if len(first_errors) < 5:
                first_errors.append(f"{tk}: {type(exc).__name__}: {exc}")

    if result["records_failed"] > 0 and result["records_inserted"] == 0 and result["records_updated"] == 0:
        result["status"] = "failed"
        detalhe = " | ".join(first_errors)
        result["error_message"] = f"{result['records_failed']} tickers falharam" + (f" — {detalhe}" if detalhe else "")
    elif result["records_failed"] > 0:
        result["status"] = "partial_success"
        if first_errors:
            result["error_message"] = "Falhas parciais: " + " | ".join(first_errors)

    return result


# ── Dados trimestrais ─────────────────────────────────────────────────────────

def _yf_quarterly_rows(ticker: str) -> dict[date, dict[str, float]]:
    """Retorna linhas trimestrais via yfinance quarterly_financials/balance_sheet/cashflow."""
    import math
    import pandas as pd
    import yfinance as yf
    from scripts.backfill_b3_fundamentals import (
        stmt_col, first_value, safe_div, finite,
        annual_dividends, annual_prices,
    )

    tkr = yf.Ticker(f"{ticker}.SA")

    def _get(attr: str) -> pd.DataFrame:
        try:
            return getattr(tkr, attr) or pd.DataFrame()
        except Exception:
            return pd.DataFrame()

    fin = _get("quarterly_financials")
    bs  = _get("quarterly_balance_sheet")
    cf  = _get("quarterly_cashflow")

    all_dates = sorted({
        pd.Timestamp(c).tz_localize(None) if getattr(pd.Timestamp(c), "tz", None) else pd.Timestamp(c)
        for frame in (fin, bs, cf)
        for c in getattr(frame, "columns", [])
        if not pd.isna(pd.Timestamp(c))
    })

    rows: dict[date, dict[str, float]] = {}
    for dt in all_dates:
        f = stmt_col(fin, dt)
        b = stmt_col(bs,  dt)
        c = stmt_col(cf,  dt)

        revenue      = first_value(f, ("Total Revenue", "Operating Revenue"))
        ebit         = first_value(f, ("EBIT", "Operating Income"))
        net_income   = first_value(f, ("Net Income", "Net Income Common Stockholders"))
        equity       = first_value(b, ("Stockholders Equity", "Total Equity Gross Minority Interest"))
        assets       = first_value(b, ("Total Assets",))
        debt         = first_value(b, ("Total Debt", "Long Term Debt And Capital Lease Obligation"))
        cash         = first_value(b, ("Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"))
        current_a    = first_value(b, ("Current Assets", "Total Current Assets"))
        current_l    = first_value(b, ("Current Liabilities", "Total Current Liabilities"))
        fco          = first_value(c, ("Operating Cash Flow", "Total Cash From Operating Activities"))
        fci          = first_value(c, ("Investing Cash Flow", "Total Cashflows From Investing Activities"))
        fcf_raw      = first_value(c, ("Free Cash Flow",))
        if fcf_raw is None and fco is not None:
            capex  = first_value(c, ("Capital Expenditure", "Capital Expenditures"))
            fcf_raw = fco + capex if capex is not None else None

        net_debt     = (debt - cash) if debt is not None and cash is not None else None
        inv_capital  = (equity + debt - (cash or 0.0)) if equity is not None and debt is not None else None

        row = {
            "Receita_Liquida":           revenue,
            "EBIT":                      ebit,
            "Lucro_Liquido":             net_income,
            "Patrimonio_Liquido":        equity,
            "Divida_Liquida":            net_debt,
            "Divida_Total":              debt,
            "Ativo_Total":               assets,
            "FCO":                       fco,
            "FCI":                       fci,
            "FCF":                       fcf_raw,
            "Fluxo_Caixa_Operacional":   fco,
            "Fluxo_Caixa_Investimento":  fci,
            "Fluxo_Caixa_Livre":         fcf_raw,
            "Margem_Liquida":            safe_div(net_income, revenue),
            "Margem_Operacional":        safe_div(ebit, revenue),
            "ROE":                       safe_div(net_income, equity),
            "ROA":                       safe_div(net_income, assets),
            "ROIC":                      safe_div(ebit, inv_capital),
            "Endividamento_Total":       safe_div(debt, equity),
            "Liquidez_Corrente":         safe_div(current_a, current_l),
        }
        rows[dt.date()] = {k: float(v) for k, v in row.items() if finite(v)}

    return rows
