"""
pages/macro.py
Cenário macroeconômico: SELIC, IPCA, câmbio e índices de mercado.

Status: Fase 5.x — integração com API BCB/yfinance pendente.

Seções:
  1. Indicadores de referência (valores atualizados manualmente)
  2. Benchmarks cadastrados no banco (tabela `benchmarks`)
  3. O que está por vir (roadmap)

Dados:
  Hardcoded (referência manual) + tabela `benchmarks` (seed 008).
  Próxima fase: API BCB/SGS para SELIC/IPCA e yfinance para índices.
"""
import streamlit as st

from core.config import settings
from design.componentes import (
    badge_status,
    card_metrica,
    container_pagina,
    em_construcao,
    secao_titulo,
)

# ── Dados de referência (atualização manual) ──────────────────────────────────
# Valores aproximados para o cenário macroeconômico em 2026.
# Serão substituídos por dados em tempo real na integração com BCB/yfinance.
_MACRO_REF = {
    "selic":      13.25,     # % a.a.  (meta Copom)
    "cdi":        13.15,     # % a.a.
    "ipca_12m":    5.20,     # % (acumulado 12 meses)
    "igpm_12m":    4.80,     # % (acumulado 12 meses)
    "usd_brl":     5.85,     # R$ / USD
    "eur_brl":     6.42,     # R$ / EUR
    "ibovespa":  128_450,    # pontos
    "sp500":       5_870,    # pontos
    "ultima_atualizacao": "Mai/2026 (referência manual)",
}


def render() -> None:
    container_pagina(
        "Cenário Macroeconômico",
        "SELIC, IPCA, câmbio e índices de mercado",
        "🌐",
    )

    # ── Badges ────────────────────────────────────────────────────────────────
    col_b1, col_b2, *_ = st.columns([1, 2, 4])
    with col_b1:
        badge_status("Referência manual", "alerta")
    with col_b2:
        badge_status(_MACRO_REF["ultima_atualizacao"], "neutro")

    st.info(
        "**Dados de referência** — Os valores abaixo são estimativas para o cenário "
        "de mai/2026 e precisam de atualização manual. A integração automática com "
        "API BCB/SGS e yfinance está planejada para uma fase futura.",
        icon="ℹ️",
    )

    st.markdown("<br>", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 1 — Taxas de juros e inflação
    # ══════════════════════════════════════════════════════════════════════════
    secao_titulo("Taxas de Juros e Inflação", "📊")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        card_metrica(
            "SELIC",
            f"{_MACRO_REF['selic']:.2f}% a.a.",
            ajuda="Taxa básica de juros (meta Copom). Fonte: Banco Central do Brasil.",
        )
    with c2:
        card_metrica(
            "CDI",
            f"{_MACRO_REF['cdi']:.2f}% a.a.",
            ajuda="Certificado de Depósito Interbancário. Referência para renda fixa.",
        )
    with c3:
        card_metrica(
            "IPCA (12m)",
            f"{_MACRO_REF['ipca_12m']:.2f}%",
            positivo=_MACRO_REF["ipca_12m"] < 4.5,
            ajuda="Índice Nacional de Preços ao Consumidor Amplo. Meta: 3,0% ± 1,5 p.p.",
        )
    with c4:
        card_metrica(
            "IGP-M (12m)",
            f"{_MACRO_REF['igpm_12m']:.2f}%",
            ajuda="Índice Geral de Preços do Mercado. Referência para contratos de aluguel.",
        )

    # Juro real implícito
    juro_real = round((1 + _MACRO_REF["selic"] / 100) / (1 + _MACRO_REF["ipca_12m"] / 100) * 100 - 100, 2)
    st.markdown(
        f'<div style="color:#9CA3AF;font-size:0.85rem;margin-top:4px">'
        f'Juro real implícito (SELIC / IPCA): '
        f'<b style="color:{"#00C896" if juro_real > 0 else "#FC5C7D"}">'
        f'{juro_real:+.2f}% a.a.</b>'
        f'</div>',
        unsafe_allow_html=True,
    )

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 2 — Câmbio
    # ══════════════════════════════════════════════════════════════════════════
    secao_titulo("Câmbio", "💱")

    c1, c2, *_ = st.columns([1, 1, 3])
    with c1:
        card_metrica(
            "USD / BRL",
            f"R$ {_MACRO_REF['usd_brl']:.2f}".replace(".", ","),
            ajuda="Dólar americano. Fonte: yfinance (USDBRL=X).",
        )
    with c2:
        card_metrica(
            "EUR / BRL",
            f"R$ {_MACRO_REF['eur_brl']:.2f}".replace(".", ","),
            ajuda="Euro. Fonte: yfinance (EURBRL=X).",
        )

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 3 — Índices de mercado
    # ══════════════════════════════════════════════════════════════════════════
    secao_titulo("Índices de Mercado", "📈")

    c1, c2, *_ = st.columns([1, 1, 3])
    with c1:
        card_metrica(
            "IBOVESPA",
            f"{_MACRO_REF['ibovespa']:,}".replace(",", "."),
            ajuda="Índice Bovespa (pontos). Principal índice da B3.",
        )
    with c2:
        card_metrica(
            "S&P 500",
            f"{_MACRO_REF['sp500']:,}".replace(",", "."),
            ajuda="Standard & Poor's 500 (pontos). Principal índice da NYSE/NASDAQ.",
        )

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 4 — Benchmarks cadastrados no banco
    # ══════════════════════════════════════════════════════════════════════════
    secao_titulo("Benchmarks no Banco", "🗄️", "Tabela `benchmarks` (seed 008)")

    if settings.has_database:
        _render_benchmarks_db()
    else:
        st.caption(
            "Configure `SUPABASE_UNIFICADO_URL` para visualizar os benchmarks "
            "cadastrados no banco."
        )

    st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # SEÇÃO 5 — Roadmap
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown("""
### Integração Automática — Fase 5.x

| Indicador | API | Frequência |
|-----------|-----|-----------|
| SELIC, CDI, IPCA, IPCA-15 | BCB/SGS | Mensal |
| IBOVESPA histórico | yfinance `^BVSP` | Diário |
| S&P 500, NASDAQ | yfinance `^GSPC`, `^IXIC` | Diário |
| USD/BRL, EUR/BRL | yfinance `USDBRL=X` | Diário |
| IFIX (FIIs) | yfinance `IFIX.SA` | Diário |
| CDI acumulado | BCB/SGS série 12 | Mensal |

*Gráfico temporal com todos os indicadores sobrepostos.*
    """)

    em_construcao(
        "Fase 5.x",
        "SELIC, IPCA, câmbio e índices em tempo real via API BCB/yfinance.",
    )


def _render_benchmarks_db() -> None:
    """Tenta exibir benchmarks da tabela `benchmarks`."""
    try:
        import pandas as pd
        from sqlalchemy import text

        from core.database import get_engine

        engine = get_engine()
        if engine is None:
            st.caption("Banco não conectado.")
            return

        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT code, name, type, frequency, description FROM benchmarks ORDER BY type, code")
            ).fetchall()

        if not rows:
            st.caption(
                "Nenhum benchmark cadastrado. Execute o script `008_seed_reference_data.sql` "
                "no SQL Editor do Supabase."
            )
            return

        df = pd.DataFrame([
            {
                "Código":      r.code,
                "Nome":        r.name,
                "Tipo":        r.type or "—",
                "Frequência":  r.frequency or "—",
                "Descrição":   (r.description or "")[:60],
            }
            for r in rows
        ])

        st.dataframe(df, hide_index=True, width="stretch")
        st.caption(
            f"{len(rows)} benchmark(s) cadastrado(s) · "
            "Histórico de cotações pendente de importação via API BCB/yfinance."
        )

    except Exception as exc:
        st.caption(f"Erro ao consultar benchmarks: {exc}")
