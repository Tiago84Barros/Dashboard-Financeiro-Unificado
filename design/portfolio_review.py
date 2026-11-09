"""Composição calculada com saldo não alocado e metas pendentes explícitas.

A composição sai em cards CSS, não em ``st.metric`` + ``st.dataframe``: a
tabela de quatro colunas espalhava dez linhas de texto pela largura toda e
deixava peso, setor e score sem hierarquia nenhuma. A tabela continua
disponível num expander para quem quer ordenar ou copiar.

Só monta HTML. Nenhuma conta de alocação mora aqui -- pesos, saldo e motivos
vêm prontos de ``core.portfolio_best_effort``/``core.portfolio_review_routes``.
Cada bloco sai em **uma** string para um único ``st.markdown``: abrir a
``<div>`` num bloco e fechar em outro já produziu moldura vazia neste projeto,
e linha em branco no meio fecha o bloco HTML do Streamlit.
"""
from __future__ import annotations

import json
import math
from html import escape

import pandas as pd
import streamlit as st

from design.componentes import cor_token
from design.lacunas import detalhe_tecnico

# Cores semânticas passam por ``cor_token`` para acompanhar o tema claro.
_POSITIVO = cor_token("#00C896")
_INFO = cor_token("#4A9EFF")
_ALERTA = cor_token("#F6C90E")
_NEGATIVO = cor_token("#FC5C7D")
_NEUTRO = cor_token("#9CA3AF")

# Classes com prefixo ``pr-`` para não colidir com o resto do app. Fundo,
# borda e texto só por token: literal cravado não é alcançado pelo tema claro
# (`memoria: tema-claro-so-alcanca-o-que-passa-por-token`).
_CSS = (
    "<style>"
    ".pr-wrap{--pr-bg:var(--app-surface,#121722);--pr-raised:var(--app-surface-raised,#171D2B);"
    "--pr-bd:var(--app-border,rgba(148,163,184,.14));--pr-bd2:var(--app-border-strong,rgba(148,163,184,.24));"
    "--pr-tx:var(--app-text,#F8FAFC);--pr-mu:var(--app-muted,#94A3B8);--pr-sb:var(--app-subtle,#64748B);"
    "margin:4px 0 14px 0;}"
    ".pr-resumo{background-color:var(--pr-bg);border:1px solid var(--pr-bd);border-radius:14px;"
    "padding:18px 20px;box-shadow:var(--app-shadow,0 12px 34px rgba(0,0,0,.22));}"
    ".pr-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px;}"
    ".pr-kpi{border-left:3px solid var(--pr-ac);padding:2px 0 2px 12px;}"
    ".pr-kpi-rot{font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--pr-mu);}"
    ".pr-kpi-val{font-size:1.75rem;font-weight:800;color:var(--pr-tx);line-height:1.15;"
    "font-variant-numeric:tabular-nums;letter-spacing:-.01em;margin-top:4px;}"
    ".pr-kpi-sub{font-size:.72rem;color:var(--pr-sb);margin-top:2px;}"
    ".pr-barra{display:flex;height:8px;border-radius:999px;overflow:hidden;margin-top:16px;"
    "background-color:var(--pr-raised);}"
    ".pr-barra span{display:block;height:100%;}"
    ".pr-legenda{display:flex;gap:16px;flex-wrap:wrap;font-size:.7rem;color:var(--pr-mu);margin-top:8px;}"
    ".pr-legenda i{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:6px;vertical-align:middle;}"
    ".pr-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px;margin-top:14px;}"
    ".pr-card{background-color:var(--pr-bg);border:1px solid var(--pr-bd);border-radius:12px;"
    "padding:14px 16px;display:flex;flex-direction:column;gap:10px;transition:border-color .15s ease,transform .15s ease;}"
    ".pr-card:hover{border-color:var(--pr-bd2);transform:translateY(-1px);}"
    ".pr-topo{display:flex;justify-content:space-between;align-items:center;gap:8px;}"
    ".pr-ticker{font-size:1.05rem;font-weight:800;color:var(--pr-tx);letter-spacing:.01em;}"
    ".pr-rank{font-size:.66rem;font-weight:700;color:var(--pr-sb);font-variant-numeric:tabular-nums;}"
    ".pr-chip{align-self:flex-start;font-size:.68rem;font-weight:600;color:var(--pr-mu);"
    "background-color:var(--pr-raised);border:1px solid var(--pr-bd);border-radius:999px;padding:2px 9px;"
    "max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}"
    ".pr-linha{display:flex;justify-content:space-between;align-items:baseline;gap:8px;}"
    ".pr-rot{font-size:.64rem;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:var(--pr-sb);}"
    ".pr-peso{font-size:1.35rem;font-weight:800;color:var(--pr-tx);font-variant-numeric:tabular-nums;}"
    ".pr-score{font-size:.9rem;font-weight:700;font-variant-numeric:tabular-nums;}"
    ".pr-medidor{height:5px;border-radius:999px;background-color:var(--pr-raised);overflow:hidden;margin-top:-4px;}"
    ".pr-medidor span{display:block;height:100%;border-radius:999px;}"
    "</style>"
)


def _t(texto: object) -> str:
    """Escapa e achata — linha em branco fecha o bloco HTML do Streamlit."""
    return escape(" ".join(str(texto).split()), quote=True)


def _numero(valor: object) -> float | None:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if math.isfinite(numero) else None


def _categoria(row: dict) -> str:
    return row.get("tipo") or row.get("sector_group") or row.get("setor") or "Não classificado"


def _score(row: dict) -> object:
    return row.get("type_score", row.get("entry_score", row.get("quality")))


def _cor_score(score: float | None) -> str:
    # Faixas só para leitura visual do score 0–100; não reclassificam o ativo.
    if score is None:
        return _NEUTRO
    if score >= 60:
        return _POSITIVO
    if score >= 45:
        return _INFO
    return _ALERTA


def _kpi(rotulo: str, valor: str, sub: str, cor: str) -> str:
    return (f'<div class="pr-kpi" style="--pr-ac:{cor};">'
            f'<div class="pr-kpi-rot">{_t(rotulo)}</div>'
            f'<div class="pr-kpi-val">{_t(valor)}</div>'
            f'<div class="pr-kpi-sub">{_t(sub)}</div></div>')


def _html_resumo(result: dict) -> str:
    alocado = float(result.get("allocated_weight") or 0.0)
    livre = float(result.get("unallocated_weight") or 0.0)
    itens = result.get("items") or []
    pesos = [float(row["weight"]) for row in itens]
    scores = [s for s in (_numero(_score(row)) for row in itens) if s is not None]
    n = len(itens)
    sub_alocado = (f"{n} ativo{'s' if n != 1 else ''} · maior peso {max(pesos):.2%}"
                   if pesos else "nenhum ativo comporta os limites")
    sub_livre = "não presume investimento nem retorno"
    kpis = [_kpi("Alocado em ativos", f"{alocado:.1%}", sub_alocado, _POSITIVO),
            _kpi("Capital não alocado", f"{livre:.1%}", sub_livre, _NEUTRO)]
    if scores:
        kpis.append(_kpi("Score médio observado", f"{sum(scores) / len(scores):.1f}",
                         f"de {min(scores):.1f} a {max(scores):.1f}", _INFO))
    largura_a = max(0.0, min(alocado, 1.0)) * 100
    largura_l = max(0.0, min(livre, 1.0)) * 100
    barra = (f'<div class="pr-barra" role="img" aria-label="{alocado:.1%} alocado, {livre:.1%} não alocado">'
             f'<span style="width:{largura_a:.2f}%;background-color:{_POSITIVO};"></span>'
             f'<span style="width:{largura_l:.2f}%;background-color:{_NEUTRO};opacity:.35;"></span></div>'
             f'<div class="pr-legenda"><span><i style="background-color:{_POSITIVO};"></i>Alocado</span>'
             f'<span><i style="background-color:{_NEUTRO};opacity:.5;"></i>Não alocado</span></div>')
    return f'<div class="pr-resumo"><div class="pr-kpis">{"".join(kpis)}</div>{barra}</div>'


def _html_card(posicao: int, row: dict) -> str:
    score = _numero(_score(row))
    cor = _cor_score(score)
    texto_score = f"{score:.1f}" if score is not None else "—"
    preenchido = max(0.0, min(score or 0.0, 100.0))
    return (f'<div class="pr-card">'
            f'<div class="pr-topo"><span class="pr-ticker">{_t(row["ticker"])}</span>'
            f'<span class="pr-rank">#{posicao}</span></div>'
            f'<span class="pr-chip" title="{_t(_categoria(row))}">{_t(_categoria(row))}</span>'
            f'<div class="pr-linha"><span class="pr-rot">Peso do capital</span>'
            f'<span class="pr-peso">{float(row["weight"]):.2%}</span></div>'
            f'<div class="pr-linha"><span class="pr-rot">Score observado</span>'
            f'<span class="pr-score" style="color:{cor};">{_t(texto_score)}</span></div>'
            f'<div class="pr-medidor"><span style="width:{preenchido:.1f}%;background-color:{cor};"></span></div>'
            f'</div>')


def _ordenados(itens: list[dict]) -> list[dict]:
    return sorted(itens, key=lambda row: (-float(row["weight"]),
                                          -(_numero(_score(row)) or -math.inf),
                                          str(row["ticker"])))


def render_portfolio_review(result, *, key):
    st.subheader("Composição possível com proteção ao investidor")
    st.caption("Proposta para revisão. Os pesos se referem ao capital total; "
               "a parcela não alocada não representa um investimento ou retorno presumido.")
    itens = _ordenados(list(result["items"]))
    cards = "".join(_html_card(i, row) for i, row in enumerate(itens, start=1))
    grade = f'<div class="pr-grid">{cards}</div>' if cards else ""
    st.markdown(f'{_CSS}<div class="pr-wrap">{_html_resumo(result)}{grade}</div>',
                unsafe_allow_html=True)
    if itens:
        with st.expander("Ver composição em tabela"):
            st.dataframe(pd.DataFrame([
                {"Ativo": row["ticker"], "Peso do capital total": f"{row['weight']:.2%}",
                 "Categoria / setor": _categoria(row), "Score observado": _score(row)}
                for row in itens]), hide_index=True, width="stretch")
    else:
        st.info("O capital permanece 100% não alocado: não há candidato com evidência "
                "suficiente que comporte os limites de proteção nesta execução.")
    for reason in result.get("reasons", []):
        detalhe_tecnico(str(reason), codigo="carteira.composicao_motivo")
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
