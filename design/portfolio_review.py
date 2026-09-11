"""Composição calculada com saldo não alocado e metas pendentes explícitas."""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st


def render_portfolio_review(result, *, key):
    st.subheader("Composição possível com proteção ao investidor")
    st.caption("Proposta para revisão. Os pesos se referem ao capital total; "
               "a parcela não alocada não representa um investimento ou retorno presumido.")
    left, right = st.columns(2)
    left.metric("Alocado em ativos", f"{result['allocated_weight']:.1%}")
    right.metric("Capital não alocado", f"{result['unallocated_weight']:.1%}")
    rows = [{"Ativo": row["ticker"], "Peso do capital total": f"{row['weight']:.2%}",
             "Categoria / setor": row.get("tipo") or row.get("sector_group") or row.get("setor"),
             "Score observado": row.get("type_score", row.get("entry_score", row.get("quality")))}
            for row in result["items"]]
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        st.info("O capital permanece 100% não alocado: não há candidato com evidência "
                "suficiente que comporte os limites de proteção nesta execução.")
    for reason in result.get("reasons", []):
        st.caption(reason)
    targets = result.get("category_targets") or {}
    if targets:
        with st.expander("Metas por categoria e composição obtida"):
            st.dataframe(pd.DataFrame([
                {"Categoria": kind, "Meta mínima": f"{values['minimum']:.0%}",
                 "Meta máxima": f"{values['maximum']:.0%}",
                 "Alocação obtida": f"{values['actual']:.1%}"}
                for kind, values in targets.items()]), hide_index=True, width="stretch")
    # Exportação só da composição visível e das ressalvas: nenhum dado bruto.
    payload = {"status": "proposta_para_revisao", "can_publish": False,
               "items": [{"ticker": row["ticker"], "weight": row["weight"]}
                         for row in result["items"]],
               "unallocated_weight": result["unallocated_weight"],
               "reasons": result.get("reasons", []), "category_targets": targets}
    st.download_button("Baixar composição para revisão", json.dumps(payload, ensure_ascii=False, indent=2),
                       file_name=f"{key}_composicao.json", mime="application/json", key=f"{key}_download")
