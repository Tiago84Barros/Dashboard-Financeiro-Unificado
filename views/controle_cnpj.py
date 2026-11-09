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
from core.pj.plano_contas import GRUPOS_DRE, NOME_GRUPO, PLANO_PADRAO, PlanoDeContas
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

_SECOES = ["📊  Visão geral", "🧾  Lançamentos", "📅  A pagar e receber",
           "📈  DRE", "⚖️  Balanço", "🧮  Impostos"]


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
    # Resumo do ano corrente, de janeiro até hoje, sem seletor de mês: a visão
    # geral responde "como vai o ano"; o mês a mês fica na DRE e no gráfico.
    hoje = _date.today()
    ano = hoje.year
    d = rel.dre(lancs, [(ano, m) for m in range(1, hoje.month + 1)])
    periodo = (f"{_MESES[1]}–{_rotulo_mes(ano, hoje.month)}" if hoje.month > 1
               else _rotulo_mes(ano, 1))
    saldo = repo.saldo_calculado(empresa, lancs, hoje)
    k1, k2, k3, k4 = st.columns(4, gap="small")
    with k1:
        card_metrica("Receita bruta no ano", fmt_moeda(d["grupos"]["receita_bruta"]), periodo)
    with k2:
        margem = f"margem {d['margem']:.0%}" if d["margem"] is not None else "sem receita"
        card_metrica("Resultado no ano", fmt_moeda(d["resultado"]), margem,
                     positivo=d["resultado"] >= 0)
    with k3:
        card_metrica("Saldo em conta", fmt_moeda(saldo) if saldo is not None else "—",
                     "calculado hoje" if saldo is not None else "importe um extrato com saldo")
    with k4:
        card_metrica("Sem categoria no ano", d["sem_categoria_n"],
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
    st.plotly_chart(_fig_serie(rel.serie_mensal(lancs, _date(ano, hoje.month, 1))),
                    width="stretch")


# ─────────────────────────────────────────────────────────────────────────────
# 🧾 Lançamentos
# ─────────────────────────────────────────────────────────────────────────────

def _form_novo_lancamento(prefixo: str, plano: PlanoDeContas = PLANO_PADRAO,
                          previsto_padrao: bool = False) -> None:
    with st.form(f"{prefixo}_form_novo", clear_on_submit=True):
        c1, c2, c3 = st.columns([1, 1, 1], gap="small")
        sentido = c1.radio("Tipo", ["Saída", "Entrada"], horizontal=True, key=f"{prefixo}_sentido")
        data = c2.date_input("Data / vencimento", value=_date.today(), format="DD/MM/YYYY",
                             key=f"{prefixo}_data")
        valor = c3.number_input("Valor (R$)", min_value=0.0, step=0.01, format="%.2f",
                                key=f"{prefixo}_valor")
        c4, c5 = st.columns([2, 1], gap="small")
        descricao = c4.text_input("Descrição", key=f"{prefixo}_desc")
        nome_cat = c5.selectbox("Categoria", plano.nomes(), key=f"{prefixo}_cat")
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
        categoria=plano.codigo_por_nome()[nome_cat],
        status="previsto" if situacao_tx == "Previsto" else "pago",
        repeticoes=int(repeticoes))
    if ok:
        st.session_state["cnpj_msg"] = "Lançamento salvo."
        st.rerun()
    st.error(msg)


# O que cada grupo faz com a categoria, para quem escolhe onde ela entra.
_EFEITO_DO_GRUPO = {
    "receita_bruta": "Soma no faturamento e entra na base do DAS.",
    "deducoes": "Abate da receita bruta na DRE (como o DAS).",
    "custos": "Custo do serviço ou da mercadoria: entra antes do lucro bruto.",
    "despesas_operacionais": "Despesa do dia a dia da empresa.",
    "pessoal": "Gente: entra em Pessoal na DRE, mas não na folha do Fator R "
               "(que conta só pró-labore, salários e encargos).",
    "financeiro": "Juros, rendimentos e tarifas financeiras.",
    "fora_do_resultado": "Mexe no caixa, mas não no lucro. No Balanço aparece em "
                         "'Outras movimentações fora do resultado'.",
}


def _gerenciar_categorias(plano: PlanoDeContas) -> None:
    with st.expander("🏷️ Categorias da empresa"):
        st.caption("Além das categorias do app, crie as suas. Cada uma entra numa linha da DRE, "
                   "e é a linha que decide onde o valor soma: na receita (e no DAS), nas "
                   "despesas ou fora do resultado.")
        with st.form("cnpj_form_nova_categoria", clear_on_submit=True):
            c1, c2 = st.columns([3, 2], gap="small")
            nome = c1.text_input("Nome da categoria", max_chars=60,
                                 placeholder="ex.: Comissões de vendedores")
            grupo = c2.selectbox("Linha da DRE", [g for g, _ in GRUPOS_DRE],
                                 index=3, format_func=lambda g: NOME_GRUPO[g])
            st.caption(" · ".join(f"**{NOME_GRUPO[g]}**: {txt}"
                                  for g, txt in _EFEITO_DO_GRUPO.items()))
            criar = st.form_submit_button("Criar categoria", type="primary")
        if criar:
            ok, msg = repo.criar_categoria(nome, grupo)
            if ok:
                st.session_state["cnpj_msg"] = f"Categoria \"{' '.join(nome.split())}\" criada."
                st.rerun()
            st.error(msg)

        proprias = repo.listar_categorias()
        if not proprias:
            st.caption("Nenhuma categoria criada ainda.")
            return
        st.markdown(_tabela(
            [[html.escape(c["nome"]), html.escape(NOME_GRUPO.get(c["grupo"], c["grupo"])),
              str(c["usos"])] for c in proprias],
            ["Categoria", "Linha da DRE", "Lançamentos"], {2}), unsafe_allow_html=True)
        por_codigo = {c["codigo"]: c for c in proprias}
        escolhida = st.selectbox("Categoria criada", list(por_codigo), key="cnpj_cat_sel",
                                 format_func=lambda k: por_codigo[k]["nome"])
        c1, c2, c3 = st.columns([3, 1, 1], gap="small")
        novo = c1.text_input("Novo nome", key=f"cnpj_cat_novo_{escolhida}",
                             value=por_codigo[escolhida]["nome"], max_chars=60)
        if c2.button("Renomear", key="cnpj_cat_renomear", width="stretch"):
            ok, msg = repo.renomear_categoria(escolhida, novo)
            if ok:
                st.session_state["cnpj_msg"] = "Categoria renomeada."
                st.rerun()
            st.error(msg)
        if c3.button("Excluir", key="cnpj_cat_excluir", width="stretch",
                     disabled=bool(por_codigo[escolhida]["usos"]),
                     help="Só categoria sem lançamento. Troque a categoria dos lançamentos "
                          "em 'Corrigir categoria' antes."):
            ok, msg = repo.excluir_categoria(escolhida)
            if ok:
                st.session_state["cnpj_msg"] = "Categoria excluída."
                st.rerun()
            st.error(msg)
        st.caption("A linha da DRE não muda depois de criada: mudar levaria os valores já "
                   "lançados de uma linha para outra sem aviso. Para isso, crie outra "
                   "categoria e corrija os lançamentos.")


# Primeira opção do seletor de período da aba Lançamentos: todos os meses juntos.
_TODOS_OS_MESES = "Todos os meses"


def _filtros_lancamentos(do_periodo: list[dict],
                         plano: PlanoDeContas = PLANO_PADRAO) -> list[dict]:
    """Filtro por categoria e busca por descrição sobre os lançamentos do período."""
    # Opções fixas (todo o plano de contas), e não só as do período: trocar de
    # mês não pode apagar o filtro que o usuário já escolheu.
    sem = "Sem categoria"
    opcoes = [sem, *plano.nomes()]
    f1, f2 = st.columns([2, 3], gap="small")
    escolhidas = f1.multiselect("Categoria", opcoes, key="cnpj_lanc_filtro_cat",
                                placeholder="Todas")
    busca = f2.text_input("Buscar na descrição", key="cnpj_lanc_busca",
                          placeholder="cliente, fornecedor, CPF/CNPJ…")
    por_nome = plano.codigo_por_nome()
    codigos = [None if n == sem else por_nome[n] for n in escolhidas if n == sem or n in por_nome]
    visiveis = rel.filtrar_lancamentos(do_periodo, codigos, busca)
    if escolhidas or busca.strip():
        entradas = sum(x["valor"] for x in visiveis if x["valor"] > 0)
        saidas = sum(x["valor"] for x in visiveis if x["valor"] < 0)
        st.caption(escapar_cifrao(
            f"{len(visiveis)} de {len(do_periodo)} lançamento(s) · entradas {fmt_moeda(entradas)} · "
            f"saídas {fmt_moeda(saidas)}"))
    return visiveis


def _tab_lancamentos(lancs: list[dict], plano: PlanoDeContas = PLANO_PADRAO) -> None:
    por_rotulo = {_TODOS_OS_MESES: None,
                  **{_rotulo_mes(*p): p for p in _periodos_disponiveis(lancs)}}
    periodo = por_rotulo[st.selectbox("Mês", list(por_rotulo), index=1, key="cnpj_lanc_mes")]
    if periodo is None:
        do_periodo, vazio = list(lancs), "Nenhum lançamento registrado ainda."
    else:
        ano, mes = periodo
        do_periodo = [x for x in lancs if x["data"].year == ano and x["data"].month == mes]
        vazio = f"Nenhum lançamento em {_rotulo_mes(ano, mes)}."
    visiveis = do_periodo
    if not do_periodo:
        estado_vazio(vazio, "🧾")
    else:
        visiveis = _filtros_lancamentos(do_periodo, plano)
    if do_periodo and not visiveis:
        estado_vazio("Nenhum lançamento do período passa nos filtros.", "🔎")
    elif do_periodo:
        linhas = [[
            _fmt_data(x["data"]), html.escape(x["descricao"]), html.escape(plano.rotulo(x["categoria"])),
            _valor_html(x["valor"]),
            "previsto" if x["status"] == "previsto" else "pago",
            "extrato" if x["origem"] == "extrato" else ("manual · conciliado" if x["chave"] else "manual"),
        ] for x in sorted(visiveis, key=lambda x: x["data"], reverse=True)]
        st.markdown(_tabela(linhas, ["Data", "Descrição", "Categoria", "Valor", "Situação", "Origem"],
                            {3}), unsafe_allow_html=True)

    pendentes = [x for x in do_periodo if not x["categoria"]][:40]
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
                    "Categoria", ["Sem categoria", *plano.nomes()], key=f"cnpj_cls_{x['id']}",
                    label_visibility="collapsed")
            salvar = st.form_submit_button("Salvar categorias", type="primary")
        if salvar:
            erros = [m for i, nome in escolhas.items() if nome != "Sem categoria"
                     for ok, m in [repo.classificar(i, plano.codigo_por_nome()[nome])] if not ok]
            if erros:
                st.error(erros[0])
            else:
                st.session_state["cnpj_msg"] = "Categorias salvas."
                st.rerun()

    if visiveis:
        with st.expander("✏️ Corrigir categoria"):
            st.caption("Para qualquer lançamento do período, inclusive os já classificados: um "
                       "aporte de sócio aceito como receita, por exemplo. Corrigir aqui muda a "
                       "DRE e o DAS estimado. A lista segue os filtros acima.")
            por_id = {x["id"]: x for x in visiveis}
            escolhido = st.selectbox(
                "Lançamento", list(por_id), key="cnpj_corrigir_sel",
                format_func=lambda i: (f"{_fmt_data(por_id[i]['data'])} · {por_id[i]['descricao']} · "
                                       f"{fmt_moeda(por_id[i]['valor'])} · {plano.rotulo(por_id[i]['categoria'])}"))
            opcoes = ["Sem categoria", *plano.nomes()]
            atual = plano.rotulo(por_id[escolhido]["categoria"])
            nova = st.selectbox("Nova categoria", opcoes, index=opcoes.index(atual),
                                key=f"cnpj_corrigir_cat_{escolhido}")
            lembrar = st.checkbox("Usar esta categoria para os próximos extratos desta contraparte",
                                  value=True, key="cnpj_corrigir_lembrar")
            if st.button("Salvar correção", key="cnpj_corrigir_btn"):
                ok, msg = repo.classificar(escolhido, plano.codigo_por_nome().get(nova), lembrar=lembrar)
                if ok:
                    st.session_state["cnpj_msg"] = "Categoria corrigida."
                    st.rerun()
                st.error(msg)

    _gerenciar_categorias(plano)

    with st.expander("➕ Novo lançamento manual"):
        st.caption("Para o que não passa pelo extrato ou ainda não aconteceu. Quando o extrato "
                   "trouxer o mesmo valor perto da data, a importação concilia em vez de duplicar.")
        _form_novo_lancamento("cnpj_lanc", plano)

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

def _tab_previstos(lancs: list[dict], empresa: dict | None,
                   plano: PlanoDeContas = PLANO_PADRAO) -> None:
    hoje = _date.today()
    previstos = [{"id": x["id"], "descricao": x["descricao"], "valor": x["valor"],
                  "vencimento": x["data"], "categoria": plano.rotulo(x["categoria"]),
                  "parcela": x["parcela"], "total_parcelas": x["total_parcelas"]}
                 for x in lancs if x["status"] == "previsto"]
    with st.expander("➕ Nova conta a pagar ou a receber", expanded=not previstos):
        _form_novo_lancamento("cnpj_prev", plano, previsto_padrao=True)
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


def _tab_dre(lancs: list[dict], plano: PlanoDeContas = PLANO_PADRAO) -> None:
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
                for c in plano.do_grupo(grupo) if d["categorias"].get(c.codigo)]

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
# ⚖️ Balanço
# ─────────────────────────────────────────────────────────────────────────────

def _datas_do_balanco(lancs: list[dict]) -> dict[str, _date]:
    """Hoje e o último dia de cada mês já fechado com movimento."""
    hoje = _date.today()
    datas = {f"Hoje ({_fmt_data(hoje)})": hoje}
    for ano, mes in _periodos_disponiveis(lancs):
        if (ano, mes) >= (hoje.year, hoje.month):
            continue
        fim = (_date(ano + (mes == 12), mes % 12 + 1, 1) - timedelta(days=1))
        datas[f"Fim de {_rotulo_mes(ano, mes)}"] = fim
    return datas


def _tab_balanco(lancs: list[dict], empresa: dict | None) -> None:
    datas = _datas_do_balanco(lancs)
    rotulo_data = st.selectbox("Data do balanço", list(datas), key="cnpj_bal_data")
    data = datas[rotulo_data]
    hoje = _date.today()
    b = rel.balanco(lancs, (empresa or {}).get("saldo_inicial"),
                    (empresa or {}).get("data_saldo_inicial"), data,
                    contas_em_aberto=data >= hoje)
    if b is None:
        estado_vazio("Sem saldo de abertura não há caixa, e sem caixa não há balanço. Importe "
                     "um extrato com saldo em Configurações › Atualização de dados › 🏢 CNPJ.",
                     "⚖️")
        return

    k1, k2, k3 = st.columns(3, gap="small")
    with k1:
        card_metrica("Ativo", fmt_moeda(b["total_ativo"]), "o que a empresa tem",
                     accent="var(--app-info)")
    with k2:
        card_metrica("Passivo", fmt_moeda(b["total_passivo"]), "o que a empresa deve",
                     accent="var(--app-danger)")
    with k3:
        card_metrica("Patrimônio líquido", fmt_moeda(b["total_pl"]), "ativo − passivo",
                     positivo=b["total_pl"] >= 0)

    # No balanço a cor não diz "entrou" ou "saiu": passivo positivo é dívida,
    # e verde nele leria como coisa boa. Só o valor negativo ganha cor.
    def valor(v: float) -> str:
        cor = "var(--app-danger)" if v < 0 else "var(--app-text)"
        return f'<span style="color:{cor};">{html.escape(fmt_moeda(v))}</span>'

    def linhas(itens: list[tuple[str, float]]) -> list[list[str]]:
        return [[f'<span style="padding-left:12px;">{html.escape(nome)}</span>', valor(v)]
                for nome, v in itens if v]

    def total(nome: str, v: float) -> list[str]:
        return [f"<b>{html.escape(nome)}</b>", f"<b>{valor(v)}</b>"]

    def titulo(nome: str) -> list[str]:
        return [f'<span style="font-size:0.68rem;text-transform:uppercase;letter-spacing:0.05em;'
                f'color:var(--app-muted);font-weight:700;">{html.escape(nome)}</span>', ""]

    c1, c2 = st.columns(2, gap="medium")
    with c1:
        st.markdown(_tabela([*linhas(b["ativo"]), total("Total do ativo", b["total_ativo"])],
                            ["Ativo", "Valor"], {1}), unsafe_allow_html=True)
    with c2:
        st.markdown(_tabela(
            [*linhas(b["passivo"]), total("Total do passivo", b["total_passivo"]),
             titulo("Patrimônio líquido"),
             *linhas(b["patrimonio_liquido"]), total("Total do patrimônio líquido", b["total_pl"]),
             total("Passivo + patrimônio líquido", b["total_passivo"] + b["total_pl"])],
            ["Passivo", "Valor"], {1}), unsafe_allow_html=True)

    notas = ["Balanço gerencial, montado com o que o app tem: o caixa conferido com o banco, "
             "as contas a pagar e a receber e as categorias de empréstimo, aplicação, aporte e "
             "distribuição. Imobilizado, estoque, tributo a recolher e o que nunca passou pela "
             "conta não aparecem; o balanço oficial é o do contador."]
    if not b["contas_em_aberto"]:
        notas.append("Em data passada, contas a pagar e a receber ficam de fora: o app não sabe "
                     "o que estava em aberto naquele dia.")
    if any(nome == "Empréstimos e financiamentos" and v < 0 for nome, v in b["passivo"]):
        notas.append("Empréstimos negativos: há pagamento de empréstimo tomado antes do "
                     "primeiro extrato, que o app não viu entrar.")
    if any(nome == "Movimentos sem categoria" and v for nome, v in b["patrimonio_liquido"]):
        notas.append("Movimentos sem categoria entram no caixa, mas o app não sabe de que lado "
                     "pô-los; classifique-os na aba Lançamentos.")
    st.caption(" ".join(notas))


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
    historico = rel.meses_desde_o_inicio(anteriores, lancs)
    est = estimar_das(anexo_atual, [s["receita_simples"] for s in historico],
                      atual["receita_simples"],
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
            "considerada é a Receita bruta menos as devoluções a clientes, no mês em que o "
            "dinheiro passou pela conta.")


# ─────────────────────────────────────────────────────────────────────────────
# Tela
# ─────────────────────────────────────────────────────────────────────────────

def render() -> None:
    from core.user_context import require_user
    require_user()
    try:
        empresa = repo.obter_empresa()
        lancs = repo.listar_lancamentos()
        plano = repo.plano_de_contas()
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
        _tab_lancamentos(lancs, plano)
    elif secao == _SECOES[2]:
        _tab_previstos(lancs, empresa, plano)
    elif secao == _SECOES[3]:
        _tab_dre(lancs, plano)
    elif secao == _SECOES[4]:
        _tab_balanco(lancs, empresa)
    elif secao == _SECOES[5]:
        _tab_impostos(lancs, empresa)
    else:
        _tab_visao_geral(lancs, empresa)

