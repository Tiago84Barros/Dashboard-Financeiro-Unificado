"""Configurações › Atualização de dados › 🏢 CNPJ: extrato da conta da empresa.

Fluxo: sobe o arquivo (OFX hoje), confere a prévia, escolhe a categoria de
cada movimento, marca o que já estava lançado à mão e grava. A leitura do
arquivo mora em ``core.pj.extratos`` e a gravação em ``core.pj.repositorio``;
aqui só se desenha e se coleta a decisão do usuário.
"""
from __future__ import annotations

import hashlib
import html

import pandas as pd
import streamlit as st

from core.config import settings
from core.pj import relatorios
from core.pj import repositorio as repo
from core.pj.extratos import extensoes_aceitas, ler_extrato
from core.pj.plano_contas import CATEGORIAS, POR_CODIGO, rotulo, sugerir_categoria
from core.utils import escapar_cifrao, fmt_moeda
from design.tema_canvas import no_claro

SEM_CATEGORIA = "Sem categoria"
_OPCOES = [SEM_CATEGORIA] + [c.nome for c in CATEGORIAS]
_CODIGO_POR_NOME = {c.nome: c.codigo for c in CATEGORIAS}


def _fmt_data(d) -> str:
    return d.strftime("%d/%m/%Y") if hasattr(d, "strftime") else "-"


def _editor_categorias(movs: list[dict]) -> list[str]:
    """Nome da categoria escolhida para cada movimento, na ordem.

    Tema escuro: ``st.data_editor``. Tema claro: um seletor por linha, porque
    o canvas do data_editor ignora as cores do tema claro (mesma decisão de
    ``views/bank_statement_upload.py``).
    """
    # Chaves dos widgets pelo movimento (FITID), nunca pela posição: trocar de
    # arquivo com a prévia aberta herdava a categoria escolhida para a linha de
    # mesmo número do arquivo anterior (achado do Codex no PR #680).
    assinatura = hashlib.sha1("".join(m["chave"] for m in movs).encode()).hexdigest()[:12]
    if not no_claro():
        df = pd.DataFrame([{
            "Data": _fmt_data(m["data"]), "Descrição": m["descricao"],
            "Valor (R$)": m["valor"], "Categoria": rotulo(m.get("categoria")),
        } for m in movs])
        editado = st.data_editor(
            df, hide_index=True, width="stretch", num_rows="fixed",
            key=f"cnpj_previa_editor_{assinatura}",
            column_config={
                "Data": st.column_config.TextColumn("Data", disabled=True),
                "Descrição": st.column_config.TextColumn("Descrição", disabled=True, width="large"),
                "Valor (R$)": st.column_config.NumberColumn("Valor (R$)", format="R$ %.2f",
                                                            disabled=True),
                "Categoria": st.column_config.SelectboxColumn("Categoria", options=_OPCOES,
                                                              required=True),
            })
        return list(editado["Categoria"])
    escolhas = []
    for m in movs:
        c1, c2, c3 = st.columns([1.1, 3.2, 2.2], gap="small")
        c1.markdown(f'<div style="padding-top:6px;font-size:0.8rem;">{_fmt_data(m["data"])}</div>',
                    unsafe_allow_html=True)
        c2.markdown(
            f'<div style="padding-top:6px;font-size:0.8rem;">{html.escape(m["descricao"])} · '
            f'<b>{html.escape(fmt_moeda(m["valor"]))}</b></div>', unsafe_allow_html=True)
        atual = rotulo(m.get("categoria"))
        escolhas.append(c3.selectbox("Categoria", _OPCOES, index=_OPCOES.index(atual),
                                     key=f"cnpj_previa_cat_{m['chave'][:24]}",
                                     label_visibility="collapsed"))
    return escolhas


def _conferencia_de_saldo(extrato, novos: list[dict], vinculos: dict[int, dict],
                          escolhidos: set[int]) -> None:
    saldo = extrato.saldo_final
    if not saldo:
        st.caption("O arquivo não trouxe saldo final; a conferência de saldo fica para o "
                   "próximo extrato que trouxer.")
        return
    empresa = repo.obter_empresa()
    if not empresa or empresa.get("saldo_inicial") is None:
        st.info(escapar_cifrao(
            f"Primeiro extrato com saldo: {fmt_moeda(saldo['valor'])} em "
            f"{_fmt_data(saldo['data'])}. Ele define o saldo de abertura da conta, e os "
            "próximos extratos serão conferidos contra ele."))
        return
    hipoteticos = relatorios.lancamentos_apos_importacao(
        repo.listar_lancamentos(), novos,
        {i: v["transaction_id"] for i, v in vinculos.items() if i in escolhidos})
    projetado = repo.saldo_calculado(empresa, hipoteticos, saldo["data"]) or 0.0
    dif = round(saldo["valor"] - projetado, 2)
    k1, k2, k3 = st.columns(3)
    k1.metric("Saldo do banco", fmt_moeda(saldo["valor"]), _fmt_data(saldo["data"]),
              delta_color="off")
    k2.metric("Saldo do app após importar", fmt_moeda(projetado))
    k3.metric("Diferença", fmt_moeda(dif))
    if abs(dif) < 0.01:
        st.success("O saldo do app fecha com o do banco.")
    else:
        st.warning(escapar_cifrao(
            f"Diferença de {fmt_moeda(dif)}: falta ou sobra lançamento até "
            f"{_fmt_data(saldo['data'])}. Causas comuns: lançamento manual pago que não "
            "aconteceu no banco, ou extrato de um período anterior ainda não importado."))


def render_upload_extrato_cnpj() -> None:
    if settings.MOCK_MODE:
        st.warning("A gravação está desabilitada neste ambiente; a prévia continua disponível.")

    ultimo = st.session_state.pop("cnpj_import_result", None)
    if ultimo:
        (st.success if ultimo.get("ok") else st.error)(ultimo.get("message", ""))

    exts = extensoes_aceitas()
    arquivo = st.file_uploader(
        f"Extrato da conta da empresa ({', '.join(e.upper() for e in exts)})",
        type=exts, key="cnpj_extrato_upload",
        help="No Cora: Extrato › Exportar › OFX. Outros formatos entram em core/pj/extratos.py.")
    if arquivo is None:
        st.caption("Selecione o arquivo para ver a prévia antes de gravar.")
        return

    extrato = ler_extrato(arquivo.getvalue(), arquivo.name)
    for aviso in extrato.avisos:
        st.warning(aviso)
    if not extrato.movimentos:
        return

    datas = [m["data"] for m in extrato.movimentos]
    st.caption(f"{extrato.formato} · {extrato.banco or 'banco não informado'} · conta "
               f"{extrato.conta or '-'} · {len(extrato.movimentos)} movimento(s) de "
               f"{_fmt_data(min(datas))} a {_fmt_data(max(datas))}")

    ctx = repo.preparar_importacao(extrato.movimentos)
    ja = ctx["ja_importadas"]
    novos_idx = [i for i, m in enumerate(extrato.movimentos) if m["chave"] not in ja]
    if ja:
        st.info(f"{len(ja)} movimento(s) deste arquivo já foram importados e serão ignorados.")
    if not novos_idx:
        # Sem movimento novo o saldo do arquivo ainda vale: pode ser o primeiro
        # saldo de movimentos importados antes sem ele, ou uma conferência
        # mais recente (achado do Codex no PR #680).
        st.success("Nenhum movimento novo neste arquivo.")
        if extrato.saldo_final:
            st.markdown("#### Conferência de saldo")
            _conferencia_de_saldo(extrato, [], {}, set())
            if st.button("Gravar o saldo do extrato", type="primary", width="stretch",
                         disabled=settings.MOCK_MODE, key="cnpj_gravar_saldo"):
                st.session_state["cnpj_import_result"] = repo.importar_extrato(
                    [], arquivo.name, extrato.saldo_final)
                st.rerun()
        return
    novos = [dict(extrato.movimentos[i]) for i in novos_idx]
    for m in novos:
        m["categoria"] = sugerir_categoria(m, ctx["regras"])

    st.markdown("#### Categorias")
    st.caption("Sugeridas pelas regras e pelo que você já classificou antes. A categoria que "
               "você escolher para uma contraparte vira regra para os próximos extratos.")
    escolhas = _editor_categorias(novos)

    # vinculos vêm indexados pela posição no arquivo; aqui, pela posição em `novos`
    vinculos = {novos_idx.index(i): v for i, v in ctx["vinculos"].items() if i in novos_idx}
    escolhidos: set[int] = set()
    if vinculos:
        st.markdown("#### 🔗 Já lançados à mão")
        st.caption("Marcado: o movimento concilia com o lançamento existente (o previsto recebe "
                   "a baixa) em vez de criar outro.")
        for k in sorted(vinculos):
            v, m = vinculos[k], novos[k]
            tipo = "previsto, vence" if v.get("previsto") else "lançado"
            txt = (f"{_fmt_data(m['data'])} · {m['descricao']} · {fmt_moeda(m['valor'])}  ↔  "
                   f"{tipo} {_fmt_data(v['data'])} · {v['descricao']}")
            if st.checkbox(escapar_cifrao(txt), value=True, key=f"cnpj_vinc_{k}_{v['transaction_id']}"):
                escolhidos.add(k)

    st.markdown("#### Conferência de saldo")
    _conferencia_de_saldo(extrato, novos, vinculos, escolhidos)

    pendentes = sum(1 for e in escolhas if e == SEM_CATEGORIA)
    if pendentes:
        st.caption(f"{pendentes} movimento(s) sem categoria entram no caixa, mas ficam fora da "
                   "DRE até você classificar (Controle CNPJ › Lançamentos).")

    if st.button("Importar extrato", type="primary", width="stretch",
                 disabled=settings.MOCK_MODE, key="cnpj_importar"):
        finais = []
        for k, m in enumerate(novos):
            codigo = _CODIGO_POR_NOME.get(escolhas[k])
            item = {**m, "categoria": codigo if codigo in POR_CODIGO else None}
            if k in escolhidos:
                item["vincular_id"] = vinculos[k]["transaction_id"]
            finais.append(item)
        st.session_state["cnpj_import_result"] = repo.importar_extrato(
            finais, arquivo.name, extrato.saldo_final)
        st.rerun()
