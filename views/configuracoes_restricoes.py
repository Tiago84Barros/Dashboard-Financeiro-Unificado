"""
views/configuracoes_restricoes.py
Aba "Restrições" de Configurações — só o administrador vê.

Desde 05/10/2026 nenhuma tela de uso mostra limitação técnica, fonte fora do
ar, procedência ou versão de metodologia: tudo isso é registrado em silêncio
(``design/lacunas.py``) e aparece aqui, para quem administra o app decidir o
que fazer com cada item. Três naturezas:

  Restrição       — falta dado, fonte, histórico ou cálculo (fila do corretor)
  Erro            — exceção que chegou à fronteira de uma tela
  Detalhe técnico — procedência, metodologia, data de vitrine, amostra

A decisão (Pendente / Em análise / Aceita / Resolvida) vale para o corretor
diário de lacunas, que lê o mesmo log: "Aceita" tira o item da fila dele.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core.lacunas import painel

_FILTRO_NAT = "cfg_restr_natureza"
_FILTRO_STATUS = "cfg_restr_status"
_FILTRO_TELA = "cfg_restr_tela"
_FILTRO_BUSCA = "cfg_restr_busca"
_ESCOLHA = "cfg_restr_escolha"
_TODAS = "Todas"


@st.cache_data(ttl=300, show_spinner=False)
def _carregar() -> painel.Painel:
    return painel.carregar()


def _limpar_cache() -> None:
    # Sem Streamlit (alguns testes) o decorador e identidade e nao tem .clear.
    limpar = getattr(_carregar, "clear", None)
    if callable(limpar):
        limpar()


def _data_curta(valor: str | None) -> str:
    if not valor:
        return "—"
    try:
        return pd.Timestamp(valor).tz_convert("America/Sao_Paulo").strftime("%d/%m/%Y %H:%M")
    except (ValueError, TypeError):
        return str(valor)[:16]


def render() -> None:
    from core.user_context import is_admin

    if not is_admin():
        return
    render_painel(_carregar())


def render_painel(dados: painel.Painel) -> None:
    """Desenha o painel a partir do que ja foi lido (separado para teste)."""
    itens = dados.itens

    ativos = [i for i in itens if i["status"] not in ("legitima", "resolvida")]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Restrições pendentes",
              sum(1 for i in ativos if i["natureza"] == "Restrição"))
    c2.metric("Erros pendentes", sum(1 for i in ativos if i["natureza"] == "Erro"))
    c3.metric("Detalhes técnicos",
              sum(1 for i in itens if i["natureza"] == "Detalhe técnico"))
    c4.metric("Reincidentes",
              sum(1 for i in itens if i.get("reincidente") and i["status"] != "resolvida"),
              help="Voltaram a aparecer depois de marcadas como resolvidas.")

    fontes = " · ".join(f"{k}: {v}" for k, v in dados.fontes.items())
    topo, botao = st.columns([5, 1])
    topo.caption(f"Fontes do log — {fontes}. Lido em {_data_curta(dados.gerado_em)}; "
                 "cache de 5 minutos.")
    if botao.button("Recarregar", key="cfg_restr_recarregar", use_container_width=True):
        _limpar_cache()
        st.rerun()

    if not itens:
        st.info("Nenhuma restrição registrada até agora.")
        return

    f1, f2, f3 = st.columns([2, 2, 2])
    naturezas = f1.multiselect("Natureza", painel.NATUREZAS,
                               default=["Restrição", "Erro"], key=_FILTRO_NAT)
    status_sel = f2.multiselect(
        "Situação", list(painel.STATUS_ROTULO),
        default=["aberta", "incerta", "em_pr"], key=_FILTRO_STATUS,
        format_func=painel.STATUS_ROTULO.get)
    telas = sorted({i["tela"] for i in itens})
    tela = f3.selectbox("Tela", [_TODAS, *telas], key=_FILTRO_TELA)
    busca = st.text_input("Buscar no texto ou no código", key=_FILTRO_BUSCA).strip().lower()

    vistos = [
        i for i in itens
        if (not naturezas or i["natureza"] in naturezas)
        and (not status_sel or i["status"] in status_sel)
        and (tela == _TODAS or i["tela"] == tela)
        and (not busca or busca in (i.get("ultima_mensagem") or "").lower()
             or busca in (i.get("codigo") or "").lower())
    ]
    st.caption(f"{len(vistos)} de {len(itens)} registros.")
    if not vistos:
        return

    tabela = pd.DataFrame([{
        "Tela": i["tela"],
        "Natureza": i["natureza"],
        "Situação": painel.STATUS_ROTULO.get(i["status"], i["status"]),
        "Mensagem": i.get("ultima_mensagem") or "",
        "Ativo": i.get("entidade") or "",
        "Vezes (14d)": i.get("ocorrencias_14d", 0),
        "Última vez": _data_curta(i.get("ultima_vez")),
    } for i in vistos])
    st.dataframe(tabela, hide_index=True, use_container_width=True,
                 column_config={"Mensagem": st.column_config.TextColumn(width="large")})

    st.markdown("#### Decidir um item")
    por_chave = {i["impressao"]: i for i in vistos}
    escolhido = st.selectbox(
        "Item", list(por_chave), key=_ESCOLHA,
        format_func=lambda k: (f"{por_chave[k]['tela']} — "
                               f"{(por_chave[k].get('ultima_mensagem') or '')[:90]}"))
    item = por_chave[escolhido]
    _render_detalhe(item)
    _render_decisao(item)


def _render_detalhe(item: dict) -> None:
    linhas = [
        f"**Mensagem:** {item.get('ultima_mensagem') or '—'}",
        f"**Natureza:** {item['natureza']} · **Situação:** "
        f"{painel.STATUS_ROTULO.get(item['status'], item['status'])}"
        + (" · **reincidente**" if item.get("reincidente") else ""),
        f"**Onde:** {item['tela']} (`{item.get('modulo') or '—'}`)",
        f"**Código:** `{item.get('codigo') or '(sem código)'}`"
        + (f" · **Ativo:** {item['entidade']}" if item.get("entidade") else ""),
        f"**Primeira vez:** {_data_curta(item.get('primeira_vez'))} · "
        f"**Última vez:** {_data_curta(item.get('ultima_vez'))} · "
        f"**Ocorrências:** {item.get('ocorrencias', 0)} "
        f"({item.get('ocorrencias_14d', 0)} em 14 dias)",
    ]
    if item.get("contexto"):
        linhas.append("**Contexto:** " + ", ".join(
            f"{k}={v}" for k, v in sorted(item["contexto"].items())))
    if item.get("pr_url"):
        linhas.append(f"**PR:** {item['pr_url']}")
    if item.get("nota_triagem"):
        linhas.append(f"**Nota:** {item['nota_triagem']}")
    st.markdown("  \n".join(linhas))


def _render_decisao(item: dict) -> None:
    chave = item["impressao"][:12]
    opcoes = list(painel.STATUS_DECIDIVEIS)
    atual = item["status"] if item["status"] in opcoes else "aberta"
    explicacao = {
        "aberta": "Pendente — precisa de correção; fica na fila do corretor diário.",
        "incerta": "Em análise — volta à fila com prioridade reduzida.",
        "legitima": "Aceita — limitação conhecida, nada a corrigir; sai da fila.",
        "resolvida": "Resolvida — corrigida; reabre sozinha se voltar a aparecer.",
    }
    with st.form(f"cfg_restr_form_{chave}"):
        novo = st.radio("Decisão", opcoes, index=opcoes.index(atual),
                        format_func=explicacao.get, key=f"cfg_restr_status_{chave}")
        obs = st.text_area("Observação (opcional)", key=f"cfg_restr_obs_{chave}",
                           max_chars=400)
        enviar = st.form_submit_button("Registrar decisão")
    if not enviar:
        return
    resultado = painel.decidir(item["impressao"], novo, obs)
    if "ok" not in resultado.values():
        st.error("A decisão não foi gravada: "
                 + ("; ".join(f"{k}: {v}" for k, v in resultado.items()) or "nenhum destino"))
        return
    _limpar_cache()
    st.success("Decisão registrada — "
               + "; ".join(f"{k}: {v}" for k, v in resultado.items()))
