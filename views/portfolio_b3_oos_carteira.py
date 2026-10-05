"""
views/portfolio_b3_oos_carteira.py — o fora da amostra da CARTEIRA, por perfil.

Só renderiza: a medição sai de `scripts/medir_oos_carteira_b3.py` para
`data/oos_carteira_b3.json` (auditoria B3-03), e a leitura/validade vem de
`core.b3_oos_carteira`. Fica fora de `views/portfolio_b3.py` pelo mesmo motivo
de `portfolio_b3_safras.py`: a aba já passa de 5 mil linhas.

O bloco existe porque o Rank-IC de `data/vantagem_oos.json` mede o RANKING na
configuração padrão, bruto e sem portão -- não a carteira que cada perfil
entrega depois de aprovação, líder+maior, orçamento por segmento, tetos e
giro. Este bloco mostra a carteira, líquida, contra pesos iguais, Selic e
Ibovespa, e diz com todas as letras quando o intervalo cruza o zero.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core import b3_oos_carteira as oos


def _pp(v) -> str:
    return "—" if v is None else f"{float(v) * 100:+.1f} pp"


def _ic(r: dict) -> str:
    lo, hi = (r.get("ic95") or [None, None])
    if lo is None or hi is None:
        return "—"
    return f"[{lo * 100:+.1f}; {hi * 100:+.1f}]"


def _linha_resumo(rotulo: str, r: dict) -> dict:
    return {
        "Comparação": rotulo,
        "Excesso médio/safra": _pp(r.get("media")),
        "IC 95%": _ic(r),
        "Safras": r.get("n_safras", 0),
        "Anos negativos": ", ".join(str(a) for a in r.get("safras_negativas") or []) or "—",
        "Cruza o zero?": "sim" if r.get("ic_cruza_zero") else "não",
    }


def _perfil_ativo() -> str | None:
    try:
        from core.b3_portfolio_presets import PRESETS, identificar_perfil
        atual = {chave: st.session_state.get(chave)
                 for p in PRESETS.values() for chave in p.valores}
        return identificar_perfil(atual)
    except Exception:  # noqa: BLE001 -- destaque é cosmético
        return None


def render_oos_carteira() -> None:
    """Bloco 'A carteira de cada perfil, fora da amostra'."""
    from core.b3_methodology import SCORE_VERSION
    from core.b3_portfolio_presets import VERSION as PRESETS_VERSION

    st.markdown("<hr style='margin:24px 0;border-color:var(--app-border);'>",
                unsafe_allow_html=True)
    st.markdown(
        '<div style="font-weight:700;font-size:1.05rem;color:var(--app-text);'
        'margin-bottom:8px;">🧪 A carteira de cada perfil, fora da amostra</div>',
        unsafe_allow_html=True,
    )

    dados = oos.carregar()
    if not dados or not dados.get("perfis"):
        st.info("Ainda não há medição da carteira por perfil. Rode "
                "`python scripts/medir_oos_carteira_b3.py` com o armazém local "
                "ligado para gerar `data/oos_carteira_b3.json`.")
        return

    motivos = oos.vencida(dados, SCORE_VERSION, PRESETS_VERSION)
    if motivos:
        st.warning("Medição VENCIDA — feita com outra versão do código ("
                   + "; ".join(motivos) + "). Os números abaixo não valem para a "
                   "carteira de hoje até a medição ser refeita.")

    st.caption(
        f"Safra a safra ({dados.get('janela') or '—'}), a carteira que a aba "
        "montaria em abril com os dados disponíveis até março (aprovação dos "
        "segmentos, líder + maior participação, orçamento por segmento, cap e "
        "tetos), mantida por 12 meses e paga o giro do rebalanceamento. Medida "
        f"em {str(dados.get('medido_em', ''))[:10]}, metodologia "
        f"{dados.get('versao_metodologia')}."
    )

    ativo = _perfil_ativo()
    from core.b3_portfolio_presets import NOMES_ANTIGOS
    for nome, medido in dados["perfis"].items():
        nome = NOMES_ANTIGOS.get(nome, nome)
        variantes = medido.get("variantes") or {}
        base = variantes.get(oos.SEM_PORTAO) or {}
        if not base:
            continue
        titulo = f"{nome}" + (" — perfil em uso" if nome == ativo else "")
        with st.expander(titulo, expanded=(nome == ativo)):
            principal = base.get("vs_equal_weight") or {}
            leitura = oos.leitura_honesta(principal)
            (st.warning if principal.get("ic_cruza_zero") else st.success)(leitura)

            st.dataframe(pd.DataFrame([
                _linha_resumo("Pesos iguais (líquido — principal)", principal),
                _linha_resumo("Pesos iguais (bruto)", base.get("vs_equal_weight_bruto") or {}),
                _linha_resumo("Pesos iguais (líquido, com IR)",
                              base.get("vs_equal_weight_com_ir") or {}),
                _linha_resumo("Selic", base.get("vs_selic") or {}),
                _linha_resumo("Ibovespa (BOVA11)", base.get("vs_ibovespa") or {}),
            ]), hide_index=True, width="stretch")

            giro, custo = base.get("giro_medio"), base.get("custo_medio")
            if giro is not None and custo is not None:
                st.caption(f"Giro médio de {giro:.0%} por rebalanceamento, custo médio "
                           f"de {custo * 100:.2f}% da carteira por ano.")

            adv = variantes.get(oos.PORTAO_VETA_O_MELHOR) or {}
            fav = variantes.get(oos.PORTAO_VETA_O_PIOR) or {}
            adv_b = adv.get("vs_equal_weight_bruto") or {}
            fav_b = fav.get("vs_equal_weight_bruto") or {}
            sem_b = base.get("vs_equal_weight_bruto") or {}
            adv_l = adv.get("vs_equal_weight") or {}
            fav_l = fav.get("vs_equal_weight") or {}
            if adv_b and fav_b:
                # O veto é escolhido pelo BRUTO, e só no bruto a banda é limite
                # estrito; o líquido das variantes é o daqueles cenários.
                st.caption(
                    "Banda do portão de LLM (não medido diretamente — ver abaixo), "
                    "para um portão que vete ATÉ UM nome da carteira por safra. "
                    "Limites estritos, no BRUTO: pior veto possível "
                    f"{_pp(adv_b.get('media'))} IC {_ic(adv_b)}; melhor "
                    f"{_pp(fav_b.get('media'))} IC {_ic(fav_b)}; sem portão "
                    f"{_pp(sem_b.get('media'))}. No líquido, os mesmos cenários dão "
                    f"{_pp(adv_l.get('media'))} e {_pp(fav_l.get('media'))} (sem "
                    f"portão {_pp(principal.get('media'))}) -- escolhidos pelo bruto, "
                    "não são limites estritos do líquido. Portão que vete mais de "
                    "um nome pode sair da banda.")
                for rotulo, var in (("Pior veto", adv), ("Melhor veto", fav)):
                    vetos = [f"{s['safra']} {s['veto']}" for s in var.get("safras") or []
                             if s.get("veto") and not s.get("sem_carteira")]
                    if vetos:
                        st.caption(f"{rotulo} por safra (sai → entra): " + "; ".join(vetos) + ".")

            marcadas = sorted(set(base.get("safras_inviaveis_no_cap") or [])
                              | set(base.get("safras_com_revisao") or []))
            if marcadas:
                st.caption("Safras em que a tela pediria revisão em vez de carteira "
                           "(poucos ativos para o cap ou tetos que não fecham) — "
                           "medidas mesmo assim, com os pesos possíveis: "
                           + ", ".join(str(a) for a in marcadas) + ".")
            sem = base.get("safras_sem_carteira") or []
            if sem:
                st.caption("Safras sem segmento aprovado (investidor em caixa, "
                           "fora da média): " + ", ".join(str(a) for a in sem) + ".")

            linhas = [s for s in base.get("safras") or [] if not s.get("sem_carteira")]
            if linhas:
                tab = pd.DataFrame([{
                    "Safra": s["safra"],
                    "Segmentos aprovados": f"{s['segmentos_aprovados']}/{s['segmentos_avaliados']}",
                    "Ativos": s["n_ativos"],
                    "Carteira líquida": _pp(s.get("liquido")).replace(" pp", ""),
                    "Pesos iguais": _pp(s.get("ew")).replace(" pp", ""),
                    "Selic": _pp(s.get("selic")).replace(" pp", ""),
                    "Ibovespa": _pp(s.get("ibov")).replace(" pp", ""),
                    "Excesso s/ pesos iguais": _pp(s.get("excesso_ew")),
                    "Maiores posições": s.get("maiores", ""),
                } for s in linhas])
                st.dataframe(tab, hide_index=True, width="stretch")

    portao = dados.get("portao_llm") or oos.PORTAO_LLM
    with st.expander("O que esta medição NÃO cobre"):
        st.markdown(f"**Portão de LLM — fora da medição.** {portao.get('por_que', '')} "
                    f"{portao.get('como_medido', '')}")
        for nota in dados.get("fora_do_pit") or oos.FORA_DO_PIT:
            st.markdown(f"- {nota}")
        custos = dados.get("custos") or {}
        if custos.get("benchmarks"):
            st.caption(f"Custos: {custos.get('cobrado', '')}. Benchmarks "
                       f"{custos['benchmarks']}. IR: {custos.get('ir', '')}.")


__all__ = ["render_oos_carteira"]
