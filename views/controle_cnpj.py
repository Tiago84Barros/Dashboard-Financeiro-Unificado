"""
views/controle_cnpj.py
Controle CNPJ: o financeiro da empresa, separado do pessoal.

Inspirado no Conta Azul, no que dá para fazer a partir do extrato: plano de
contas da empresa, conciliação, contas a pagar e receber, DRE gerencial e a
estimativa do DAS do Simples. Emissão de nota, cobrança por boleto e envio ao
contador ficam de fora -- dependem de certificado digital e de convênio que um
app pessoal não tem.

Cinco abas:
  📊 Visão geral       — o mês: receita, resultado, saldo em conta e conferência
  🧾 Lançamentos       — lista do mês, classificação e lançamento manual
  📅 A pagar e receber — previstos, baixa e saldo projetado
  📈 DRE               — demonstração de resultado gerencial (regime de caixa)
  🧮 Impostos          — dados da empresa e DAS estimado do Simples Nacional

O extrato entra por Configurações › Atualização de dados › 🏢 CNPJ.
"""
from __future__ import annotations

import html
from datetime import date as _date
from datetime import timedelta

import plotly.graph_objects as go
import streamlit as st

from core.pj import relatorios as rel
from core.pj import repositorio as repo
from core.pj.plano_contas import CATEGORIAS, NOME_GRUPO, rotulo
from core.pj.simples import ANEXOS_DESCRICAO, TABELAS, estimar_das
from core.previstos import MAX_REPETICOES, projetar_saldo, resumir, situacao
from core.utils import escapar_cifrao, fmt_moeda
from design.componentes import abas_secao, card_metrica, container_pagina, estado_vazio
from design.lacunas import falha_de_acao

_MESES = ["", "jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
_COR_ENTRADA = "#00C896"
_COR_SAIDA = "#FC5C7D"
_COR_LINHA = "#4A9EFF"
_COR_TEXTO = "#9CA3AF"
_NOMES = [c.nome for c in CATEGORIAS]
_CODIGO_POR_NOME = {c.nome: c.codigo for c in CATEGORIAS}

_SECOES = ["📊  Visão geral", "🧾  Lançamentos", "📅  A pagar e receber",
           "📈  DRE", "🧮  Impostos"]


def _rotulo_mes(ano: int, mes: int) -> str:
    return f"{_MESES[mes]}/{ano}"


def _fmt_data(d) -> str:
    return d.strftime("%d/%m/%Y") if hasattr(d, "strftime") else "-"


def _periodos_disponiveis(lancs: list[dict]) -> list[tuple[int, int]]:
    hoje = _date.today()
    periodos = set(rel.meses_com_movimento(lancs)) | {(hoje.year, hoje.month)}
    return sorted(periodos, reverse=True)


def _seletor_mes(lancs: list[dict], key: str) -> tuple[int, int]:
    periodos = _periodos_disponiveis(lancs)
    return st.selectbox("Mês", periodos, format_func=lambda p: _rotulo_mes(*p), key=key)


def _tabela(linhas: list[list[str]], cabecalho: list[str], alinhar_direita: set[int]) -> str:
    """Tabela HTML nos tokens do tema (st.dataframe ignora o tema claro)."""
    th = "".join(
        f'<th style="text-align:{"right" if i in alinhar_direita else "left"};padding:6px 8px;'
        f'font-size:0.68rem;text-transform:uppercase;letter-spacing:0.05em;color:var(--app-muted);'
        f'border-bottom:1px solid var(--app-border);">{html.escape(c)}</th>'
        for i, c in enumerate(cabecalho))
    corpo = "".join(
        "<tr>" + "".join(
            f'<td style="text-align:{"right" if i in alinhar_direita else "left"};padding:6px 8px;'
            f'font-size:0.82rem;color:var(--app-text);border-bottom:1px solid var(--app-border);">'
            f"{c}</td>" for i, c in enumerate(linha)) + "</tr>"
        for linha in linhas)
    return (f'<div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;">'
            f"<thead><tr>{th}</tr></thead><tbody>{corpo}</tbody></table></div>")


def _valor_html(v: float) -> str:
    cor = "var(--app-primary)" if v > 0 else "var(--app-danger)" if v < 0 else "var(--app-text)"
    return f'<span style="color:{cor};">{html.escape(fmt_moeda(v))}</span>'


# ─────────────────────────────────────────────────────────────────────────────
# 📊 Visão geral
# ─────────────────────────────────────────────────────────────────────────────

def _fig_serie(serie: list[dict]) -> go.Figure:
    x = [_rotulo_mes(s["ano"], s["mes"]) for s in serie]
    fig = go.Figure()
    fig.add_trace(go.Bar(name="Entradas", x=x, y=[s["entradas"] for s in serie],
                         marker_color=_COR_ENTRADA, opacity=0.85,
                         hovertemplate="<b>Entradas %{x}</b><br>R$ %{y:,.2f}<extra></extra>"))
    fig.add_trace(go.Bar(name="Saídas", x=x, y=[s["saidas"] for s in serie],
                         marker_color=_COR_SAIDA, opacity=0.85,
                         hovertemplate="<b>Saídas %{x}</b><br>R$ %{y:,.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(name="Resultado (DRE)", x=x, y=[s["resultado"] for s in serie],
                             mode="lines+markers", line={"color": _COR_LINHA, "width": 2.5},
                             hovertemplate="<b>Resultado %{x}</b><br>R$ %{y:,.2f}<extra></extra>"))
    fig.update_layout(
        barmode="group", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_TEXTO, height=320, margin={"t": 10, "b": 0, "l": 0, "r": 8},
        legend={"orientation": "h", "y": 1.12},
        yaxis={"gridcolor": "#1E2533", "tickformat": ",.0f", "tickprefix": "R$ "})
    return fig


def _tab_visao_geral(lancs: list[dict], empresa: dict | None) -> None:
    ano, mes = _seletor_mes(lancs, "cnpj_vg_mes")
    d = rel.dre(lancs, [(ano, mes)])
    hoje = _date.today()
    saldo = repo.saldo_calculado(empresa, lancs, hoje)
    k1, k2, k3, k4 = st.columns(4, gap="small")
    with k1:
        card_metrica("Receita bruta", fmt_moeda(d["grupos"]["receita_bruta"]), _rotulo_mes(ano, mes))
    with k2:
        margem = f"margem {d['margem']:.0%}" if d["margem"] is not None else "sem receita"
        card_metrica("Resultado", fmt_moeda(d["resultado"]), margem,
                     positivo=d["resultado"] >= 0)
    with k3:
        card_metrica("Saldo em conta", fmt_moeda(saldo) if saldo is not None else "—",
                     "calculado hoje" if saldo is not None else "importe um extrato com saldo")
    with k4:
        card_metrica("Sem categoria", d["sem_categoria_n"],
                     fmt_moeda(d["sem_categoria_total"]) if d["sem_categoria_n"] else "tudo classificado",
                     positivo=False if d["sem_categoria_n"] else None)

    saldos = repo.listar_saldos_extrato()
    if saldos and empresa and empresa.get("saldo_inicial") is not None:
        ultimo = saldos[0]
        app = repo.saldo_calculado(empresa, lancs, ultimo["data"])
        dif = round(ultimo["saldo"] - (app or 0.0), 2)
        if abs(dif) < 0.01:
            st.success(escapar_cifrao(
                f"Conferência: o saldo do app fecha com o do banco em {_fmt_data(ultimo['data'])} "
                f"({fmt_moeda(ultimo['saldo'])})."))
        else:
            st.warning(escapar_cifrao(
                f"Conferência: em {_fmt_data(ultimo['data'])} o banco tinha "
                f"{fmt_moeda(ultimo['saldo'])} e o app calcula {fmt_moeda(app)} — diferença de "
                f"{fmt_moeda(dif)}. Falta ou sobra lançamento até essa data."))
    if d["sem_categoria_n"]:
        st.caption("Movimento sem categoria entra no caixa e no saldo, mas fica fora da DRE "
                   "até ser classificado na aba Lançamentos.")

    st.markdown("#### Últimos 12 meses")
    st.plotly_chart(_fig_serie(rel.serie_mensal(lancs, _date(ano, mes, 1))), width="stretch")


# ─────────────────────────────────────────────────────────────────────────────
# 🧾 Lançamentos
# ─────────────────────────────────────────────────────────────────────────────

def _form_novo_lancamento(prefixo: str, previsto_padrao: bool = False) -> None:
    with st.form(f"{prefixo}_form_novo", clear_on_submit=True):
        c1, c2, c3 = st.columns([1, 1, 1], gap="small")
        sentido = c1.radio("Tipo", ["Saída", "Entrada"], horizontal=True, key=f"{prefixo}_sentido")
        data = c2.date_input("Data / vencimento", value=_date.today(), format="DD/MM/YYYY",
                             key=f"{prefixo}_data")
        valor = c3.number_input("Valor (R$)", min_value=0.0, step=0.01, format="%.2f",
                                key=f"{prefixo}_valor")
        c4, c5 = st.columns([2, 1], gap="small")
        descricao = c4.text_input("Descrição", key=f"{prefixo}_desc")
        nome_cat = c5.selectbox("Categoria", _NOMES, key=f"{prefixo}_cat")
        c6, c7 = st.columns(2, gap="small")
        situacao_tx = c6.radio("Situação", ["Pago", "Previsto"], horizontal=True,
                               index=1 if previsto_padrao else 0, key=f"{prefixo}_sit",
                               help="Previsto: conta a pagar ou a receber, com a data como vencimento.")
        repeticoes = c7.number_input("Repetir todo mês (vezes)", 1, MAX_REPETICOES, 1,
                                     key=f"{prefixo}_rep", help="Só para previsto.")
        enviar = st.form_submit_button("Salvar lançamento", type="primary")
    if not enviar:
        return
    ok, msg = repo.criar_lancamento(
        data, descricao or nome_cat, valor, entrada=(sentido == "Entrada"),
        categoria=_CODIGO_POR_NOME[nome_cat],
        status="previsto" if situacao_tx == "Previsto" else "pago",
        repeticoes=int(repeticoes))
    if ok:
        st.session_state["cnpj_msg"] = "Lançamento salvo."
        st.rerun()
    st.error(msg)


def _tab_lancamentos(lancs: list[dict]) -> None:
    ano, mes = _seletor_mes(lancs, "cnpj_lanc_mes")
    do_mes = [x for x in lancs if x["data"].year == ano and x["data"].month == mes]
    if not do_mes:
        estado_vazio(f"Nenhum lançamento em {_rotulo_mes(ano, mes)}.", "🧾")
    else:
        linhas = [[
            _fmt_data(x["data"]), html.escape(x["descricao"]), html.escape(rotulo(x["categoria"])),
            _valor_html(x["valor"]),
            "previsto" if x["status"] == "previsto" else "pago",
            "extrato" if x["origem"] == "extrato" else ("manual · conciliado" if x["chave"] else "manual"),
        ] for x in sorted(do_mes, key=lambda x: x["data"], reverse=True)]
        st.markdown(_tabela(linhas, ["Data", "Descrição", "Categoria", "Valor", "Situação", "Origem"],
                            {3}), unsafe_allow_html=True)

    pendentes = [x for x in do_mes if not x["categoria"]][:40]
    if pendentes:
        st.markdown(f"#### Classificar ({len(pendentes)} sem categoria)")
        st.caption("A categoria escolhida vale também para os próximos extratos da mesma "
                   "contraparte.")
        with st.form("cnpj_form_classificar"):
            escolhas = {}
            for x in pendentes:
                c1, c2 = st.columns([3, 2], gap="small")
                c1.markdown(escapar_cifrao(
                    f"{_fmt_data(x['data'])} · {x['descricao']} · **{fmt_moeda(x['valor'])}**"))
                escolhas[x["id"]] = c2.selectbox(
                    "Categoria", ["Sem categoria", *_NOMES], key=f"cnpj_cls_{x['id']}",
                    label_visibility="collapsed")
            salvar = st.form_submit_button("Salvar categorias", type="primary")
        if salvar:
            erros = [m for i, nome in escolhas.items() if nome != "Sem categoria"
                     for ok, m in [repo.classificar(i, _CODIGO_POR_NOME[nome])] if not ok]
            if erros:
                st.error(erros[0])
            else:
                st.session_state["cnpj_msg"] = "Categorias salvas."
                st.rerun()

    if do_mes:
        with st.expander("✏️ Corrigir categoria"):
            st.caption("Para qualquer lançamento do mês, inclusive os já classificados: um "
                       "aporte de sócio aceito como receita, por exemplo. Corrigir aqui muda a "
                       "DRE e o DAS estimado.")
            por_id = {x["id"]: x for x in do_mes}
            escolhido = st.selectbox(
                "Lançamento", list(por_id), key="cnpj_corrigir_sel",
                format_func=lambda i: (f"{_fmt_data(por_id[i]['data'])} · {por_id[i]['descricao']} · "
                                       f"{fmt_moeda(por_id[i]['valor'])} · {rotulo(por_id[i]['categoria'])}"))
            opcoes = ["Sem categoria", *_NOMES]
            atual = rotulo(por_id[escolhido]["categoria"])
            nova = st.selectbox("Nova categoria", opcoes, index=opcoes.index(atual),
                                key=f"cnpj_corrigir_cat_{escolhido}")
            lembrar = st.checkbox("Usar esta categoria para os próximos extratos desta contraparte",
                                  value=True, key="cnpj_corrigir_lembrar")
            if st.button("Salvar correção", key="cnpj_corrigir_btn"):
                ok, msg = repo.classificar(escolhido, _CODIGO_POR_NOME.get(nova), lembrar=lembrar)
                if ok:
                    st.session_state["cnpj_msg"] = "Categoria corrigida."
                    st.rerun()
                st.error(msg)

    with st.expander("➕ Novo lançamento manual"):
        st.caption("Para o que não passa pelo extrato ou ainda não aconteceu. Quando o extrato "
                   "trouxer o mesmo valor perto da data, a importação concilia em vez de duplicar.")
        _form_novo_lancamento("cnpj_lanc")

    manuais = [x for x in lancs if x["origem"] == "manual" and not x["chave"]]
    if manuais:
        with st.expander("🗑️ Excluir lançamento manual"):
            por_id = {x["id"]: x for x in manuais}
            escolhido = st.selectbox(
                "Lançamento", list(por_id), key="cnpj_excluir_sel",
                format_func=lambda i: (f"{_fmt_data(por_id[i]['data'])} · {por_id[i]['descricao']} · "
                                       f"{fmt_moeda(por_id[i]['valor'])}"))
            if st.button("Excluir", key="cnpj_excluir_btn"):
                ok, msg = repo.excluir(escolhido)
                if ok:
                    st.session_state["cnpj_msg"] = "Lançamento excluído."
                    st.rerun()
                st.error(msg)


# ─────────────────────────────────────────────────────────────────────────────
# 📅 A pagar e receber
# ─────────────────────────────────────────────────────────────────────────────

def _tab_previstos(lancs: list[dict], empresa: dict | None) -> None:
    hoje = _date.today()
    previstos = [{"id": x["id"], "descricao": x["descricao"], "valor": x["valor"],
                  "vencimento": x["data"], "categoria": rotulo(x["categoria"]),
                  "parcela": x["parcela"], "total_parcelas": x["total_parcelas"]}
                 for x in lancs if x["status"] == "previsto"]
    with st.expander("➕ Nova conta a pagar ou a receber", expanded=not previstos):
        _form_novo_lancamento("cnpj_prev", previsto_padrao=True)
    if not previstos:
        estado_vazio("Nenhuma conta a pagar ou a receber em aberto.", "📅")
        return

    r = resumir(previstos, hoje)
    saldo = repo.saldo_calculado(empresa, lancs, hoje)
    k1, k2, k3, k4 = st.columns(4, gap="small")
    with k1:
        card_metrica("Vencidos sem baixa", r["n_vencidos"],
                     fmt_moeda(r["total_vencido"]) if r["n_vencidos"] else "nenhum",
                     positivo=False if r["n_vencidos"] else None)
    with k2:
        card_metrica("A pagar em 30 dias", fmt_moeda(r["a_pagar"]))
    with k3:
        card_metrica("A receber em 30 dias", fmt_moeda(r["a_receber"]))
    projecao = projetar_saldo(saldo, previstos, hoje, hoje + timedelta(days=90)) if saldo is not None else []
    with k4:
        final = projecao[-1]["saldo_apos"] if projecao else saldo
        card_metrica("Saldo projetado em 90 dias", fmt_moeda(final) if final is not None else "—",
                     f"hoje {fmt_moeda(saldo)}" if saldo is not None else "importe um extrato com saldo",
                     positivo=None if final is None else final >= 0)

    linhas_fonte = projecao or sorted(previstos, key=lambda p: p["vencimento"])
    linhas = []
    for p in linhas_fonte:
        sit = situacao(p["vencimento"], hoje)
        serie = f" ({p['parcela']}/{p['total_parcelas']})" if p["total_parcelas"] > 1 else ""
        cor = "var(--app-danger)" if sit == "vencido" else "var(--app-text)"
        linha = [_fmt_data(p["vencimento"]), html.escape(p["descricao"] + serie),
                 html.escape(p["categoria"]), _valor_html(p["valor"]),
                 f'<span style="color:{cor};">{sit}</span>']
        if projecao:
            linha.append(_valor_html(p["saldo_apos"]))
        linhas.append(linha)
    cab = ["Vencimento", "Descrição", "Categoria", "Valor", "Situação"] + (["Saldo após"] if projecao else [])
    st.markdown(_tabela(linhas, cab, {3, 5}), unsafe_allow_html=True)
    if projecao:
        st.caption("Saldo da conta somado a cada previsto até 90 dias; vencido entra como pago hoje.")

    st.markdown("#### Dar baixa")
    st.caption("Ao importar o extrato, o pagamento de mesmo valor perto do vencimento dá a baixa "
               "sozinho.")
    por_id = {p["id"]: p for p in previstos}
    escolhido = st.selectbox(
        "Previsto", list(por_id), key="cnpj_baixa_sel",
        format_func=lambda i: (f"{_fmt_data(por_id[i]['vencimento'])} · {por_id[i]['descricao']} · "
                               f"{fmt_moeda(por_id[i]['valor'])}"))
    with st.form("cnpj_form_baixa"):
        c1, c2 = st.columns(2)
        data_pg = c1.date_input("Data do pagamento", value=hoje, format="DD/MM/YYYY")
        valor_pg = c2.number_input("Valor pago (R$)", min_value=0.01,
                                   value=abs(por_id[escolhido]["valor"]), step=0.01, format="%.2f",
                                   key=f"cnpj_baixa_valor_{escolhido}")
        b1, b2 = st.columns(2)
        baixar = b1.form_submit_button("Dar baixa", type="primary", width="stretch")
        excluir = b2.form_submit_button("Excluir previsto", width="stretch")
    if baixar:
        diferente = abs(valor_pg - abs(por_id[escolhido]["valor"])) >= 0.005
        ok, msg = repo.dar_baixa(escolhido, data_pg, valor_pg if diferente else None)
        if ok:
            st.session_state["cnpj_msg"] = "Baixa feita."
            st.rerun()
        st.error(msg)
    if excluir:
        ok, msg = repo.excluir(escolhido)
        if ok:
            st.session_state["cnpj_msg"] = "Previsto excluído."
            st.rerun()
        st.error(msg)


# ─────────────────────────────────────────────────────────────────────────────
# 📈 DRE
# ─────────────────────────────────────────────────────────────────────────────

def _periodos_da_escolha(escolha: str, ano: int, mes: int) -> list[tuple[int, int]]:
    if escolha == "Mês":
        return [(ano, mes)]
    if escolha == "Ano":
        return [(ano, m) for m in range(1, 13)]
    out, a, m = [], ano, mes
    for _ in range(12):
        out.append((a, m))
        a, m = (a, m - 1) if m > 1 else (a - 1, 12)
    return out


def _tab_dre(lancs: list[dict]) -> None:
    c1, c2 = st.columns([1, 2])
    with c1:
        ano, mes = _seletor_mes(lancs, "cnpj_dre_mes")
    with c2:
        escolha = st.radio("Período", ["Mês", "Ano", "Últimos 12 meses"], horizontal=True,
                           key="cnpj_dre_periodo")
    d = rel.dre(lancs, _periodos_da_escolha(escolha, ano, mes))
    g = d["grupos"]

    def cat_linhas(grupo: str) -> list[list[str]]:
        return [[f'<span style="padding-left:18px;color:var(--app-subtle);">{html.escape(c.nome)}</span>',
                 _valor_html(d["categorias"][c.codigo])]
                for c in CATEGORIAS if c.grupo == grupo and d["categorias"].get(c.codigo)]

    def total(nome: str, valor: float) -> list[str]:
        return [f"<b>{html.escape(nome)}</b>", f"<b>{_valor_html(valor)}</b>"]

    linhas = [total(NOME_GRUPO["receita_bruta"], g["receita_bruta"]), *cat_linhas("receita_bruta"),
              total("(−) " + NOME_GRUPO["deducoes"], g["deducoes"]), *cat_linhas("deducoes"),
              total("= Receita líquida", d["receita_liquida"]),
              total("(−) " + NOME_GRUPO["custos"], g["custos"]), *cat_linhas("custos"),
              total("= Lucro bruto", d["lucro_bruto"]),
              total("(−) " + NOME_GRUPO["despesas_operacionais"], g["despesas_operacionais"]),
              *cat_linhas("despesas_operacionais"),
              total("(−) " + NOME_GRUPO["pessoal"], g["pessoal"]), *cat_linhas("pessoal"),
              total("= Resultado operacional", d["resultado_operacional"]),
              total("(±) " + NOME_GRUPO["financeiro"], g["financeiro"]), *cat_linhas("financeiro"),
              total("= Resultado do período", d["resultado"])]
    st.markdown(_tabela(linhas, ["Linha", "Valor"], {1}), unsafe_allow_html=True)
    if d["margem"] is not None:
        st.caption(f"Margem do período: {d['margem']:.1%} da receita bruta. Regime de caixa: "
                   "cada lançamento conta no mês em que o dinheiro passou pela conta.")

    fora = cat_linhas("fora_do_resultado")
    if fora:
        st.markdown("#### Fora do resultado")
        st.caption("Mexem no caixa, não no lucro: aporte, distribuição de lucros, transferência, "
                   "empréstimo e aplicação.")
        st.markdown(_tabela(fora, ["Categoria", "Valor"], {1}), unsafe_allow_html=True)
    if d["sem_categoria_n"]:
        st.warning(escapar_cifrao(
            f"{d['sem_categoria_n']} lançamento(s) sem categoria somando "
            f"{fmt_moeda(d['sem_categoria_total'])} ficaram fora desta DRE. Classifique na aba "
            "Lançamentos."))


# ─────────────────────────────────────────────────────────────────────────────
# 🧮 Impostos
# ─────────────────────────────────────────────────────────────────────────────

def _tab_impostos(lancs: list[dict], empresa: dict | None) -> None:
    anexo_atual = (empresa or {}).get("anexo_simples") or repo.ANEXO_PADRAO
    with st.expander("🏢 Dados da empresa", expanded=not (empresa and empresa.get("cnpj"))):
        with st.form("cnpj_form_empresa"):
            c1, c2 = st.columns(2)
            cnpj = c1.text_input("CNPJ", value=(empresa or {}).get("cnpj") or "")
            razao = c2.text_input("Razão social", value=(empresa or {}).get("razao_social") or "")
            anexos = list(TABELAS)
            anexo = st.selectbox("Anexo do Simples Nacional", anexos, index=anexos.index(anexo_atual),
                                 format_func=lambda a: ANEXOS_DESCRICAO[a],
                                 help="Está no PGDAS-D ou com o contador. No Anexo V, o Fator R "
                                      "decide se a empresa paga pelo Anexo III.")
            salvar = st.form_submit_button("Salvar", type="primary")
        if salvar:
            ok, msg = repo.salvar_empresa(cnpj, razao, anexo)
            if ok:
                st.session_state["cnpj_msg"] = "Dados da empresa salvos."
                st.rerun()
            st.error(msg)

    ano, mes = _seletor_mes(lancs, "cnpj_imp_mes")
    serie = rel.serie_mensal(lancs, _date(ano, mes, 1), meses=13)
    anteriores, atual = serie[:-1], serie[-1]
    primeiro_com_receita = next((i for i, s in enumerate(anteriores) if s["receita"] > 0), None)
    historico = anteriores[primeiro_com_receita:] if primeiro_com_receita is not None else []
    est = estimar_das(anexo_atual, [s["receita"] for s in historico], atual["receita"],
                      folhas_12m_anteriores=[s["folha"] for s in historico])

    seguinte = (ano, mes + 1) if mes < 12 else (ano + 1, 1)
    das_pago = rel.serie_mensal(lancs, _date(*seguinte, 1), meses=1)[0]["das_pago"]
    k1, k2, k3, k4 = st.columns(4, gap="small")
    with k1:
        card_metrica("Receita do mês", fmt_moeda(est.receita_mes), _rotulo_mes(ano, mes))
    with k2:
        card_metrica("RBT12", fmt_moeda(est.rbt12),
                     "proporcional (menos de 12 meses)" if est.rbt12_proporcional else "12 meses anteriores")
    with k3:
        card_metrica("Alíquota efetiva",
                     f"{est.aliquota_efetiva:.2%}" if est.aliquota_efetiva is not None else "—",
                     f"Anexo {est.anexo_aplicado}, faixa {est.faixa}" if est.faixa else "fora do Simples")
    with k4:
        card_metrica("DAS estimado", fmt_moeda(est.das_estimado) if est.das_estimado is not None else "—",
                     f"vence em {_rotulo_mes(*seguinte)}")
    if est.aviso:
        st.error(est.aviso)
    if est.fator_r is not None:
        st.caption(f"Fator R: {est.fator_r:.1%} (folha de 12 meses ÷ RBT12). No Anexo V, de 28% "
                   "para cima a tributação é pelo Anexo III.")
    if das_pago:
        dif = round(das_pago - (est.das_estimado or 0.0), 2)
        st.caption(escapar_cifrao(
            f"DAS pago em {_rotulo_mes(*seguinte)}: {fmt_moeda(das_pago)} "
            f"(estimado {fmt_moeda(est.das_estimado)}, diferença {fmt_moeda(dif)})."))
    st.info("Estimativa. O valor oficial é o do PGDAS-D, que trata atividades mistas, ISS "
            "retido, segregação de receitas e o início de atividade com regra própria. A receita "
            "considerada é a categoria Receita bruta, no mês em que o dinheiro entrou.")


# ─────────────────────────────────────────────────────────────────────────────
# Tela
# ─────────────────────────────────────────────────────────────────────────────

def render() -> None:
    from core.user_context import require_user
    require_user()
    try:
        empresa = repo.obter_empresa()
        lancs = repo.listar_lancamentos()
    except Exception as exc:  # noqa: BLE001
        container_pagina("Controle CNPJ", "Financeiro da empresa.", "💼")
        falha_de_acao("Não foi possível ler o financeiro da empresa.", exc)
        return

    nome = (empresa or {}).get("razao_social") or "Financeiro da empresa"
    cnpj = (empresa or {}).get("cnpj")
    meta = [("CNPJ", f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}")] if cnpj else []
    container_pagina("Controle CNPJ", nome + " — separado do financeiro pessoal.", "💼",
                     metadados=meta)

    msg = st.session_state.pop("cnpj_msg", None)
    if msg:
        st.success(msg)
    if not lancs:
        st.info("Nenhum lançamento da empresa ainda. Importe o extrato em Configurações › "
                "Atualização de dados › 🏢 CNPJ, ou crie um lançamento manual na aba Lançamentos.")

    secao = abas_secao(_SECOES, key="cnpj_secao_ativa", default=_SECOES[0])
    if secao == _SECOES[1]:
        _tab_lancamentos(lancs)
    elif secao == _SECOES[2]:
        _tab_previstos(lancs, empresa)
    elif secao == _SECOES[3]:
        _tab_dre(lancs)
    elif secao == _SECOES[4]:
        _tab_impostos(lancs, empresa)
    else:
        _tab_visao_geral(lancs, empresa)

