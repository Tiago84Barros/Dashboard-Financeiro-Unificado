"""Componentes visuais compartilhados pelas vitrines B3 e EUA."""
from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from design.componentes import rolar_para_topo


MARKET_COMPANIES_CSS = """
<style>
.b3-sector-hdr {
    font-size:0.92rem;font-weight:800;text-transform:uppercase;
    letter-spacing:.10em;color:#E8FBF4;
    background:linear-gradient(90deg,rgba(0,200,150,.18),rgba(0,200,150,.02));
    border-left:4px solid #00C896;border-radius:0 8px 8px 0;
    padding:10px 14px;margin:30px 0 14px;
    display:flex;align-items:center;gap:10px;
}
.b3-sector-count {
    font-size:0.66rem;font-weight:700;color:#00C896;letter-spacing:.04em;
    background:rgba(0,200,150,.14);border:1px solid rgba(0,200,150,.32);
    border-radius:999px;padding:2px 9px;
}
.b3-sector-hdr-none {
    color:#9CA3AF;
    background:linear-gradient(90deg,rgba(148,163,184,.12),rgba(148,163,184,.02));
    border-left-color:#5A6678;
}
.b3-sector-hdr-none .b3-sector-count {
    color:#9CA3AF;background:rgba(148,163,184,.12);border-color:rgba(148,163,184,.30);
}
.b3-card {
    background:#12151E;border:1px solid #1E2533;border-radius:12px;
    padding:14px 14px 10px;height:100%;min-height:100px;
    transition:border-color .2s,transform .2s;
}
.b3-card:hover { border-color:rgba(0,200,150,.35); }
.b3-card-selected { border-color:rgba(0,200,150,.55); }
.b3-card-logo-wrap {
    width:42px;height:42px;border-radius:9px;background:rgba(255,255,255,.06);
    display:flex;align-items:center;justify-content:center;position:relative;flex-shrink:0;
    color:#718096;font-size:.80rem;font-weight:800;overflow:hidden;
}
.b3-card-logo { width:36px;height:36px;border-radius:8px;object-fit:contain;
                background:rgba(255,255,255,.06);padding:3px;position:absolute;inset:3px; }
.b3-card-ticker { font-size:0.88rem;font-weight:800;color:#E2E8F0; }
.b3-card-nome   { font-size:0.70rem;color:#718096;margin-top:1px;
                  overflow:hidden;text-overflow:ellipsis;white-space:nowrap; }
.b3-card-tag    { font-size:0.62rem;color:#4A5568;line-height:1.35;
                  min-height:1.7em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap; }
</style>
"""


NAV_ITEMS = (
    ("🏢", "Empresas por Setor"),
    ("🔍", "Análise de Empresa"),
    ("🔬", "Análise Avançada"),
    ("🚀", "Criação de Portfólio"),
    ("🧠", "Avaliação de Portfólio"),
)


def render_market_css() -> None:
    st.markdown(MARKET_COMPANIES_CSS, unsafe_allow_html=True)


def render_market_tabs(*, state_key: str, key_prefix: str) -> int:
    """Trilho de abas das vitrines B3/EUA. Trocar de aba sempre volta ao topo.

    O Streamlit preserva a rolagem entre reruns, e as abas com chat (Avaliação
    de Portfólio) ainda puxam o foco para o rodapé quando o ``st.chat_input``
    monta — a aba abria no meio da conversa com a LLM, não no relatório.
    """
    active = int(st.session_state.get(state_key, 0))
    rolar_flag = f"_{state_key}_rolar"
    widths = [2, 2, 2.5, 2.5, 2.5]
    for idx, (col, (icon, label)) in enumerate(zip(st.columns(widths), NAV_ITEMS)):
        with col:
            if st.button(
                f"{icon} {label}", use_container_width=True,
                type="primary" if active == idx else "secondary",
                key=f"{key_prefix}_tab{idx}",
            ):
                st.session_state[state_key] = idx
                st.session_state[rolar_flag] = True
                st.rerun()
    st.markdown("<hr style='margin:4px 0 16px;border-color:#1E2533;'>",
                unsafe_allow_html=True)
    # pop: vale para o rerun da troca. Mantê-lo jogaria o usuário de volta ao
    # topo a cada interação dentro da aba.
    if st.session_state.pop(rolar_flag, False):
        rolar_para_topo()
    return active


def render_company_search(*, label: str, placeholder: str, key: str) -> str:
    return st.text_input(label, key=key, placeholder=placeholder).strip()


def render_sector_grid(
    companies: pd.DataFrame, *, key_prefix: str, selected_ticker: str | None,
    selected_state_key: str, active_state_key: str, page_size: int = 160,
    unclassified_label: str = "Sem classificação setorial",
) -> None:
    """Renderiza setores + quatro cards por linha e navega ao clicar em Analisar."""
    if companies is None or companies.empty:
        st.info("Nenhuma empresa encontrada para o filtro atual.")
        return
    df = companies.copy()
    for col in ("ticker", "company_name", "sector", "industry", "card_tag", "logo_url"):
        if col not in df:
            df[col] = ""
    df["_sector_sort"] = df["sector"].fillna("").astype(str).str.strip()
    df["_sector_sort"] = df["_sector_sort"].where(df["_sector_sort"] != "", "￿")
    df = df.sort_values(["_sector_sort", "industry", "ticker"], kind="stable")

    limit_key = f"{key_prefix}_visible_limit"
    visible_limit = int(st.session_state.get(limit_key, page_size))
    visible = df.head(visible_limit)
    total_counts = df.groupby("sector", dropna=False)["ticker"].size().to_dict()

    for sector, group in visible.groupby("sector", sort=False, dropna=False):
        sector_text = str(sector).strip() if pd.notna(sector) else ""
        classified = bool(sector_text)
        title = sector_text if classified else unclassified_label
        css_class = "b3-sector-hdr" if classified else "b3-sector-hdr b3-sector-hdr-none"
        total = int(total_counts.get(sector, len(group)))
        noun = "empresa" if total == 1 else "empresas"
        st.markdown(
            f'<div class="{css_class}">{html.escape(title)}'
            f'<span class="b3-sector-count">{total} {noun}</span></div>',
            unsafe_allow_html=True,
        )
        group = group.reset_index(drop=True)
        for start in range(0, len(group), 4):
            cols = st.columns(4, gap="small")
            for offset, (_, row) in enumerate(group.iloc[start:start + 4].iterrows()):
                ticker = str(row["ticker"])
                name = str(row["company_name"] or ticker)[:28]
                tag = str(row["card_tag"] or row["industry"] or "—")
                logo = str(row["logo_url"] or "")
                initial = html.escape((name or ticker or "?")[:1].upper())
                selected = ticker == (selected_ticker or "")
                selected_class = " b3-card-selected" if selected else ""
                with cols[offset]:
                    st.markdown(
                        f'<div class="b3-card{selected_class}">'
                        f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">'
                        f'<div class="b3-card-logo-wrap"><span>{initial}</span>'
                        + (f'<img src="{html.escape(logo, quote=True)}" class="b3-card-logo" '
                           f'onerror="this.style.display=\'none\'">' if logo else "")
                        + f'</div><div style="overflow:hidden;">'
                        f'<div class="b3-card-ticker">{html.escape(ticker)}</div>'
                        f'<div class="b3-card-nome">{html.escape(name)}</div>'
                        f'</div></div><div class="b3-card-tag">{html.escape(tag)}</div></div>',
                        unsafe_allow_html=True,
                    )
                    if st.button(
                        "Analisar", key=f"{key_prefix}_analyze_{ticker}_{start}_{offset}",
                        use_container_width=True,
                    ):
                        st.session_state[selected_state_key] = ticker
                        st.session_state[active_state_key] = 1
                        # Mesma troca de aba do trilho — mesma volta ao topo.
                        st.session_state[f"_{active_state_key}_rolar"] = True
                        st.rerun()

    if len(df) > visible_limit:
        remaining = len(df) - visible_limit
        if st.button(
            f"Mostrar mais empresas ({remaining} restantes)",
            key=f"{key_prefix}_show_more", use_container_width=True,
        ):
            st.session_state[limit_key] = visible_limit + page_size
            st.rerun()
