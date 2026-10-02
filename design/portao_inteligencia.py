"""Transparência do portão da Inteligência dos Ativos nas telas de criação de
carteira (Empresas B3, Empresas Americanas, Seleção de FIIs). O log vem de
``veredito.filtrar_selecao`` ou ``veredito.reotimizar_sem_vetados``."""
from __future__ import annotations

import streamlit as st

TITULO = "🧭 Inteligência dos Ativos — vetos e substituições"
TITULO_SALVO = "🧭 Inteligência dos Ativos na criação desta carteira"

# Quem entra no lugar do vetado e o grupo da vaga, por tela. A criação e a
# leitura da carteira salva usam o mesmo texto.
COMO_SUBSTITUI = {
    "b3": "O substituto é o próximo do ranking do MESMO segmento e herda o peso.",
    "us": ("O substituto é o próximo do ranking da MESMA indústria que também "
           "passa no piso de qualidade; os pesos são reotimizados."),
    "fii": ("O otimizador remonta a carteira sem o vetado, sob as mesmas "
            "restrições; quem entra é escolha dele, não herança de vaga."),
}
GRUPO = {"b3": "segmento", "us": "indústria", "fii": "tipo"}


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


def render_salvo(log: dict | None, *, tela: str,
                 titulo: str = TITULO_SALVO) -> None:
    """O log do portão gravado no resumo da carteira salva (``tela`` é
    ``"b3"``, ``"us"`` ou ``"fii"``). ``None`` é carteira salva antes do
    registro: diz que não se sabe, em vez de sugerir que nada foi vetado."""
    aberto = bool(log and (log.get("vetados") or log.get("persistentes")))
    with st.expander(titulo, expanded=aberto):
        render_conteudo_salvo(log, tela=tela)


def render_conteudo_salvo(log: dict | None, *, tela: str) -> None:
    """O corpo de ``render_salvo`` sem o expander, para quem já está dentro
    de um (o Streamlit não aninha expanders)."""
    if log is None:
        st.caption(
            "Esta carteira foi salva antes de o log do portão ser gravado: "
            "não dá para saber o que foi vetado ou substituído na criação. "
            "Refaça e salve a seleção para registrá-lo.")
    elif not tem_conteudo(log):
        st.caption(
            "Na criação, nenhum nome foi vetado nem substituído e todas as "
            "avaliações estavam disponíveis.")
    else:
        render(log, como_substitui=COMO_SUBSTITUI[tela], grupo=GRUPO[tela])
