"""Transparência do portão da Inteligência dos Ativos nas telas de criação de
carteira (Empresas B3, Empresas Americanas, Seleção de FIIs). O log vem de
``veredito.filtrar_selecao`` ou ``veredito.reotimizar_sem_vetados``."""
from __future__ import annotations

import streamlit as st

TITULO = "🧭 Inteligência dos Ativos — vetos e substituições"


def tem_conteudo(log: dict | None) -> bool:
    return bool(log) and any(log.values())


def render(log: dict, *, como_substitui: str, grupo: str = "segmento") -> None:
    """Lista vetados, substituições, vagas vazias, avaliações indisponíveis e
    vetados que restaram. ``como_substitui`` diz, na legenda, quem entra no
    lugar do vetado nesta tela."""
    st.caption(
        "A carteira criada é compra: nome que a Inteligência dos Ativos "
        "manda avaliar troca ou não aportar (alerta eliminatório, qualidade "
        f"frágil) não entra. {como_substitui} Nada aqui altera score; "
        "avaliação indisponível não veta, mas é listada."
    )
    for v in log.get("vetados", ()):
        st.markdown(f"❌ **{v['tk']}** ({v['segmento']}) — {v['limite']}: "
                    f"{v['motivo']}")
    for s in log.get("substituicoes", ()):
        st.markdown(f"🔁 **{s['entra']}** entra no lugar de **{s['sai']}** "
                    f"({s['segmento']})")
    for s in log.get("vagas_vazias", ()):
        st.markdown(f"⬜ Vaga de **{s['sai']}** ({s['segmento']}) ficou "
                    f"vazia: nenhum substituto do mesmo {grupo} passou.")
    for s in log.get("indisponiveis", ()):
        st.markdown(f"⚠️ **{s['tk']}** ({s['segmento']}): avaliação "
                    f"indisponível ({s['erro']}); entrou sem o portão.")
    for v in log.get("persistentes", ()):
        st.warning(f"**{v['tk']}** ({v['segmento']}) continua na carteira com "
                   f"limite {v['limite']} ({v['motivo']}): o otimizador não "
                   "fechou a carteira sem ele no número máximo de tentativas. "
                   "Não aporte nele sem rever o alerta.")
