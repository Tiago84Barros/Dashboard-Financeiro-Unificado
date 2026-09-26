"""
views/configuracoes_estrategia.py
Configurações → Geral → 🎯 Estratégia de Investimentos.

O corpo do bloco: estado, progresso, entrevista guiada, formulário de revisão
e as ações do ciclo de vida (iniciar, continuar, concluir, editar, descartar).
O cabeçalho e a moldura ficam em ``views/configuracoes_geral.py``, como os
demais blocos da aba.

Regras e SQL moram em ``core/estrategia``; a entrevista, em
``core/llm_estrategia``. Aqui só se lê, mostra e encaminha.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from core import llm_estrategia as entrevista
from core.estrategia import politica as pol
from core.estrategia import repositorio as repo
from core.utils import escapar_cifrao

_MODO = "cfg_estrategia_modo"
_FLASH = "cfg_estrategia_flash"
_CONFIRMA_DESCARTE = "cfg_estrategia_confirma_descarte"
# Posto pelo botão da aba Investimentos → Inteligência dos Ativos
# (``views.inteligencia_ativos.VEIO_DA_ANALISE``).
_VEIO_DA_ANALISE = "cfg_estrategia_veio_da_analise"
_MODO_CHAT = "💬 Entrevista"
_MODO_FORM = "📝 Revisar respostas"

_ICONE_STATUS = {
    pol.NOT_STARTED: "⚪",
    pol.IN_PROGRESS: "🟡",
    pol.COMPLETED: "🟢",
    pol.NEEDS_REVIEW: "🟠",
}
_NAO_INFORMADO = "— não informado —"


def render() -> None:
    _mostrar_flash()
    if st.session_state.pop(_VEIO_DA_ANALISE, False):
        st.info("Você veio da **Inteligência dos Ativos**. Conclua a "
                "estratégia para liberar a análise; o que já foi respondido "
                "continua salvo.")
    try:
        estado = repo.carregar()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Não foi possível ler a estratégia: {exc}")
        return
    if estado.tabela_ausente:
        st.warning(
            "A tabela da estratégia ainda não existe neste banco. Rode a "
            f"migration 076 (`{repo.MIGRATION}`) no Supabase.")
        return

    _render_situacao(estado)

    if estado.rascunho is not None:
        _render_rascunho(estado)
    elif estado.vigente is not None:
        _render_vigente(estado.vigente)
    else:
        st.caption("Uma entrevista curta, uma pergunta por vez. Dá para parar "
                   "e continuar depois; nada vale para a análise até você "
                   "concluir.")
        if st.button("▶️ Iniciar configuração", key="cfg_estrategia_iniciar",
                     type="primary"):
            _iniciar()

    atual = estado.rascunho or estado.vigente
    if atual is not None:
        with st.expander("Como a IA vai ler sua estratégia"):
            if estado.rascunho is not None:
                st.caption("Isto é o rascunho. A análise lê a versão concluída.")
            st.code(pol.texto_da_politica(atual.politica, versao=atual.version,
                                          status=atual.status), language=None)


# -- situação ------------------------------------------------------------------

def texto_situacao(estado: repo.Estado) -> tuple[str, float, str]:
    """(linha de status, fração do progresso, rótulo do progresso). Pura."""
    status = estado.status
    linha = f"{_ICONE_STATUS.get(status, '')} **{pol.ROTULO_STATUS[status]}**"
    if estado.vigente is not None:
        v = estado.vigente
        linha += f" · versão {v.version}"
        if v.completed_at:
            linha += f" · concluída em {v.completed_at:%d/%m/%Y}"
    if estado.rascunho is not None and estado.vigente is not None:
        linha += (f" · editando a versão {estado.rascunho.version} "
                  "(a atual continua valendo até você concluir)")
    base = estado.rascunho or estado.vigente
    pct = base.progresso.pct if base is not None else 0.0
    return linha, pct / 100, f"Configuração da estratégia: {pct:.0f}% concluída"


def _render_situacao(estado: repo.Estado) -> None:
    linha, fracao, rotulo = texto_situacao(estado)
    st.markdown(linha)
    st.progress(fracao, text=rotulo)
    base = estado.rascunho or estado.vigente
    if base is not None:
        prog = base.progresso
        st.caption(
            f"Campos mínimos: {prog.obrigatorios_ok} de "
            f"{prog.obrigatorios_total} · complementares: "
            f"{prog.complementares_ok} de {prog.complementares_total}")


def _motivos_revisao(registro: repo.Registro) -> list[str]:
    motivos = pol.erros_de_conclusao(registro.politica)
    if registro.schema_version != pol.SCHEMA_VERSION:
        motivos.append("O conjunto de perguntas mudou desde a conclusão.")
    if not motivos:
        motivos.append(f"Concluída há mais de {pol.REVISAO_DIAS} dias: "
                       "confirme se ainda vale.")
    return motivos


# -- política vigente, sem edição aberta ---------------------------------------

def _render_vigente(vigente: repo.Registro) -> None:
    if vigente.status == pol.NEEDS_REVIEW:
        st.warning("Esta estratégia precisa de revisão antes de voltar a "
                   "servir de premissa:\n\n"
                   + "\n".join(f"- {m}" for m in _motivos_revisao(vigente)))
    _render_alertas(vigente.politica)
    st.dataframe(tabela_respostas(vigente.politica), hide_index=True,
                 width="stretch")
    if st.button("✏️ Editar estratégia", key="cfg_estrategia_editar",
                 type="primary"):
        _iniciar()


def tabela_respostas(politica: dict) -> pd.DataFrame:
    linhas = []
    for campo in pol.CAMPOS:
        if not pol.aplicavel(politica, campo.chave):
            continue
        ok = pol.preenchido(politica, campo.chave)
        item = politica.get(campo.chave) or {}
        linhas.append({
            "Grupo": campo.grupo,
            "Campo": campo.rotulo + (" *" if campo.obrigatorio else ""),
            "Resposta": (pol.formatar(campo.chave, item.get("value"))
                         if ok else "não informado"),
            "Origem": ({"entrevista": "Entrevista", "manual": "Formulário"}
                       .get(item.get("source"), "—") if ok else "—"),
        })
    return pd.DataFrame(linhas)


def _render_alertas(politica: dict) -> None:
    avisos = pol.alertas(politica)
    if avisos:
        st.info("Pontos de atenção (não impedem concluir):\n\n"
                + "\n".join(f"- {a}" for a in avisos))


# -- rascunho -----------------------------------------------------------------

def _render_rascunho(estado: repo.Estado) -> None:
    rascunho = estado.rascunho
    modo = st.radio("Como continuar", [_MODO_CHAT, _MODO_FORM], key=_MODO,
                    horizontal=True, label_visibility="collapsed")
    if modo == _MODO_CHAT:
        _render_entrevista(rascunho)
    else:
        _render_formulario(rascunho)

    _render_alertas(rascunho.politica)
    prog = rascunho.progresso
    if not prog.minimos_completos:
        st.caption("Para concluir falta: " + ", ".join(
            pol.POR_CHAVE[c].rotulo for c in prog.faltantes) + ".")

    col_ok, col_descarte = st.columns([1, 1])
    with col_ok:
        if st.button("✅ Concluir estratégia", key="cfg_estrategia_concluir",
                     type="primary", disabled=not prog.minimos_completos,
                     width="stretch"):
            ok, erros = repo.concluir(rascunho.id)
            if ok:
                _flash("success", f"Estratégia concluída (versão "
                                  f"{rascunho.version}). A análise "
                                  "inteligente dos seus ativos já está "
                                  "disponível em Investimentos → "
                                  "Inteligência dos Ativos.")
                st.rerun()
            st.error("Não foi possível concluir:\n\n"
                     + "\n".join(f"- {e}" for e in erros))
    with col_descarte:
        rotulo = ("Descartar esta edição" if estado.vigente
                  else "Descartar rascunho")
        confirmado = st.checkbox(f"{rotulo} (não há como desfazer)",
                                 key=_CONFIRMA_DESCARTE)
        if st.button(f"🗑️ {rotulo}", key="cfg_estrategia_descartar",
                     disabled=not confirmado, width="stretch"):
            repo.descartar_rascunho(rascunho.id)
            st.session_state.pop(_CONFIRMA_DESCARTE, None)
            _flash("info", "Rascunho descartado."
                   + (" A versão concluída continua valendo."
                      if estado.vigente else ""))
            st.rerun()


# -- entrevista ----------------------------------------------------------------

def _render_entrevista(rascunho: repo.Registro) -> None:
    disponivel = entrevista.llm_disponivel()
    if disponivel:
        st.caption("Provedores de IA: "
                   + ", ".join(entrevista.provedores_disponiveis())
                   + ". A IA registra só o que você disser; nada é suposto.")
    else:
        st.info("Nenhum provedor de IA configurado. Você ainda pode preencher "
                f"tudo em **{_MODO_FORM}**.")

    historico = list(rascunho.entrevista)
    if not historico:
        historico = [_msg("assistant", entrevista.abertura(rascunho.politica))]
    for m in historico:
        with st.chat_message(m["role"]):
            st.markdown(escapar_cifrao(m["content"]))

    turnos = sum(1 for m in historico if m["role"] == "user")
    if turnos >= entrevista.MAX_TURNOS:
        st.caption(f"A entrevista chegou ao limite de {entrevista.MAX_TURNOS} "
                   f"respostas. Ajuste o que faltar em **{_MODO_FORM}**.")
        return
    if not disponivel:
        return

    resposta = st.chat_input("Sua resposta", key="cfg_estrategia_resposta")
    if not resposta:
        return
    with st.spinner("Lendo sua resposta..."):
        etapa = entrevista.proxima_etapa(
            rascunho.politica, historico, resposta, contexto=_contexto())
    fala = etapa.pergunta or (
        "Tenho o que preciso para montar sua estratégia. Revise as respostas "
        "se quiser e clique em **Concluir estratégia**.")
    if etapa.proposta:
        fala += "\n\nSugestão de divisão: " + pol.formatar(
            "asset_class_targets", etapa.proposta) + ". Confirma?"
    historico += [_msg("user", resposta), _msg("assistant", fala)]
    try:
        repo.salvar_rascunho(rascunho.id, etapa.politica, historico)
    except Exception as exc:  # noqa: BLE001
        st.error(f"A resposta não foi gravada: {exc}")
        return
    if etapa.aviso:
        _flash("warning", etapa.aviso)
    elif etapa.aplicados:
        _flash("success", "Registrado: " + ", ".join(
            pol.POR_CHAVE[c].rotulo for c in etapa.aplicados) + ".")
    st.rerun()


def _msg(papel: str, conteudo: str) -> dict:
    return {"role": papel, "content": conteudo,
            "at": repo._agora().isoformat()}


def _contexto() -> str:
    """O que o app já sabe, para a IA perguntar melhor. Nunca vira resposta.

    Só percentuais da carteira, nunca valores em reais. Dado de demonstração
    (MOCK_MODE) fica de fora: pergunta guiada por carteira inventada é
    pergunta errada.
    """
    partes = []
    try:
        from core.investimentos import get_carteira
        carteira = get_carteira()
        if carteira.get("data_source") == "real" and carteira.get("por_classe"):
            partes.append("Composição atual da carteira (% do valor de mercado): "
                          + "; ".join(f"{c['nome']} {c['pct_carteira']:.0f}%"
                                      for c in carteira["por_classe"]))
    except Exception:  # noqa: BLE001
        pass
    try:
        from core.metas import get_metas
        metas = get_metas()
        if metas.get("data_source") == "real" and metas.get("metas"):
            partes.append("Metas financeiras cadastradas: " + "; ".join(
                f"{m['nome']} ({m['tipo_label']}, prazo {m['prazo_fmt']})"
                for m in metas["metas"]))
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(partes)


# -- formulário de revisão -----------------------------------------------------

def _render_formulario(rascunho: repo.Registro) -> None:
    politica = rascunho.politica
    brutos: dict[str, object] = {}
    with st.form(key=f"cfg_estrategia_form_{rascunho.id}", border=False):
        grupo = None
        for campo in pol.CAMPOS:
            if campo.grupo != grupo:
                grupo = campo.grupo
                st.markdown(f"**{grupo}**")
            brutos[campo.chave] = _widget(campo, politica, rascunho.id)
        enviado = st.form_submit_button("💾 Salvar respostas", type="primary")
    if not enviado:
        return

    mudancas, remocoes = diferencas(politica, brutos)
    nova, erros = pol.aplicar(politica, mudancas, fonte="manual")
    for chave in remocoes:
        nova = pol.remover(nova, chave)
    if erros:
        st.error("Não foi salvo:\n\n" + "\n".join(f"- {e}" for e in erros.values()))
        return
    if not mudancas and not remocoes:
        st.info("Nenhuma resposta mudou.")
        return
    try:
        repo.salvar_rascunho(rascunho.id, nova, rascunho.entrevista)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Não foi possível salvar: {exc}")
        return
    _flash("success", f"{len(mudancas) + len(remocoes)} resposta(s) atualizada(s).")
    st.rerun()


def diferencas(politica: dict, brutos: dict) -> tuple[dict, list[str]]:
    """(campos que mudaram, campos esvaziados). Pura.

    Só o que MUDOU é regravado. Salvar o formulário sem tocar num campo não
    troca a procedência dele de "entrevista" para "formulário", e um widget
    vazio nunca grava um valor que ninguém escolheu.
    """
    mudancas, remocoes = {}, []
    for chave, bruto in brutos.items():
        atual = pol.valor(politica, chave)
        if bruto is None:
            if atual is not None:
                remocoes.append(chave)
            continue
        normal, erro = pol.normalizar(chave, bruto)
        if erro or normal != atual:
            mudancas[chave] = bruto
    return mudancas, remocoes


def _widget(campo: pol.Campo, politica: dict, rid: str):
    atual = pol.valor(politica, campo.chave)
    chave = f"cfg_estrategia_{rid}_{campo.chave}"
    rotulo = campo.rotulo + (" *" if campo.obrigatorio else "")

    if campo.tipo == "escolha":
        opcoes = [None] + [k for k, _ in campo.opcoes]
        rotulos = dict(campo.opcoes)
        return st.selectbox(
            rotulo, opcoes, key=chave,
            index=opcoes.index(atual) if atual in opcoes else 0,
            format_func=lambda k: _NAO_INFORMADO if k is None else rotulos[k])

    if campo.tipo == "multi":
        rotulos = dict(campo.opcoes)
        escolha = st.multiselect(
            rotulo, [k for k, _ in campo.opcoes], key=chave,
            default=[k for k in (atual or []) if k in rotulos],
            format_func=rotulos.get, placeholder=_NAO_INFORMADO)
        # Lista vazia é "não informado", salvo se já foi respondido "nenhum".
        return escolha if escolha or atual == [] else None

    if campo.tipo == "numero":
        return st.number_input(
            f"{rotulo} ({campo.unidade})" if campo.unidade else rotulo,
            key=chave, value=None if atual is None else float(atual),
            min_value=None if campo.minimo is None else float(campo.minimo),
            max_value=None if campo.maximo is None else float(campo.maximo),
            step=1.0, placeholder=_NAO_INFORMADO)

    if campo.tipo == "bool":
        opcoes = [None, True, False]
        return st.selectbox(
            rotulo, opcoes, key=chave,
            index=opcoes.index(atual) if atual in opcoes else 0,
            format_func=lambda v: (_NAO_INFORMADO if v is None
                                   else "Sim" if v else "Não"))

    if campo.tipo == "texto":
        texto = st.text_input(rotulo, key=chave, value=atual or "",
                              max_chars=pol.MAX_TEXTO)
        return texto.strip() or None

    # alocacao / limites: um número por classe, vazio = não informado
    st.caption(rotulo + (" — as quatro classes precisam somar 100%"
                         if campo.tipo == "alocacao" else
                         " — só as classes que têm limite"))
    colunas = st.columns(len(pol.CLASSES))
    valores = {}
    for col, (classe, nome) in zip(colunas, pol.CLASSES):
        with col:
            v = st.number_input(
                f"{nome} (%)", key=f"{chave}_{classe}",
                value=_float((atual or {}).get(classe)), min_value=0.0,
                max_value=100.0, step=5.0, placeholder="—")
        if v is not None:
            valores[classe] = v
    return valores or None


# -- apoio ---------------------------------------------------------------------

def _float(v):
    return None if v is None else float(v)


def _iniciar() -> None:
    try:
        repo.iniciar()
    except repo.TabelaAusente:
        st.error(f"Rode a migration 076 (`{repo.MIGRATION}`) no Supabase.")
        return
    except Exception as exc:  # noqa: BLE001
        st.error(f"Não foi possível iniciar: {exc}")
        return
    st.session_state[_MODO] = _MODO_CHAT
    st.rerun()


def _flash(tipo: str, texto: str) -> None:
    st.session_state[_FLASH] = (tipo, texto)


def _mostrar_flash() -> None:
    item = st.session_state.pop(_FLASH, None)
    if item:
        getattr(st, item[0], st.info)(item[1])
