"""
views/configuracoes_cenario.py
🌎 Cenário de Investimentos, mostrado em Investimentos → Inteligência dos
Ativos ("Meu cenário", no fim da aba liberada). Até 27/09/2026 morava em
Configurações → Geral. Renderize uma vez só por página: as chaves dos widgets
são fixas por versão.

O corpo do bloco: situação do cenário salvo, sinais de revisão e o formulário
dos 12 itens. Cabeçalho e moldura ficam em ``views/configuracoes_geral.py``.

O cenário só muda aqui, e de dois jeitos: o usuário edita e salva, ou pede
"sugerir a partir dos dados publicados", confere o que veio no formulário e
salva. A sugestão sozinha não grava nada. Nenhuma LLM chama esta tela.

Regras em ``core/cenario``. Coberto por tests/test_cenario_investimentos.py.
"""
from __future__ import annotations

import datetime as dt
from html import escape

import streamlit as st

from core.cenario import modelo as mod
from design.lacunas import falha_de_acao

_FLASH = "cfg_cenario_flash"
_ORIGEM = "cfg_cenario_origem"
_SUGESTAO = "cfg_cenario_sugestao"
_VAZIO = ""


def chave_widget(versao: int, chave: str, campo: str) -> str:
    """A versão entra na chave: salvou, o formulário renasce do gravado."""
    return f"cfg_cenario_v{versao}_{chave}_{campo}"


def cartao_situacao(c: mod.Cenario, sinais: tuple, hoje: dt.date) -> str:
    """Situação do cenário salvo, num ``st.markdown`` só. Puro."""
    if c.vazio:
        corpo = ('<div style="font-weight:700;color:var(--app-text)">Nenhum '
                 'cenário cadastrado</div><div style="font-size:0.84rem;'
                 'color:var(--app-muted);margin-top:4px">Sem cenário, as '
                 'análises dos ativos usam só a estratégia, os fundamentos e '
                 'os dados de mercado.</div>')
        borda = "border"
    else:
        velhos = c.envelhecidos(hoje)
        corpo = ('<div style="font-weight:700;color:var(--app-text)">'
                 f'Versão {c.versao} · {len(c.preenchidos)} de '
                 f'{len(mod.CHAVES)} itens preenchidos</div>'
                 '<div style="font-size:0.82rem;color:var(--app-muted);'
                 f'margin-top:2px">Salvo em {escape((c.salvo_em or "—")[:10])}'
                 f' · {escape(mod.ORIGENS.get(c.origem or "", "—"))}</div>')
        if velhos:
            corpo += ('<div style="font-size:0.84rem;color:var(--app-warning);'
                      f'margin-top:6px">Revistos há mais de '
                      f'{mod.ENVELHECE_DIAS} dias: '
                      f'{escape(", ".join(mod.ROTULO[k] for k in velhos))}.'
                      '</div>')
        borda = "border"
    if sinais:
        itens = "".join(
            '<li style="margin:2px 0;font-size:0.84rem;color:var(--app-text)">'
            f'{escape(s.texto)}</li>' for s in sinais)
        corpo += ('<div style="margin-top:10px;font-weight:700;'
                  f'color:var(--app-warning)">{escape(mod.FRASE_REVISAO)}</div>'
                  '<ul style="margin:4px 0 0 0;padding-left:18px">'
                  f'{itens}</ul><div style="font-size:0.8rem;'
                  'color:var(--app-subtle);margin-top:4px">O cenário não foi '
                  'alterado. Ele vale até você mudá-lo.</div>')
        borda = "warning"
    return ('<div style="background:var(--app-surface);border:1px solid '
            f'var(--app-{borda});border-radius:12px;padding:12px 14px;'
            f'margin:4px 0 12px 0">{corpo}</div>')


def _mostrar_flash() -> None:
    flash = st.session_state.pop(_FLASH, None)
    if flash:
        tipo, texto = flash
        (st.success if tipo == "ok" else st.info)(texto)


def _carregar_sugestoes(versao: int) -> None:
    """Callback do botão: põe as sugestões nos campos, antes dos widgets."""
    from core.cenario import referencias
    sugestoes = referencias.sugestoes()
    for chave, valores in sugestoes.items():
        st.session_state[chave_widget(versao, chave, "valor")] = \
            valores["current_value"]
        st.session_state[chave_widget(versao, chave, "fonte")] = \
            valores["source"]
    st.session_state[_ORIGEM] = mod.ATUALIZACAO_SOLICITADA
    st.session_state[_SUGESTAO] = sorted(sugestoes)


def render() -> None:
    from core.cenario import divergencia, referencias
    from core.cenario import repositorio as repo

    _mostrar_flash()
    st.caption(
        "A estratégia diz o que você pretende alcançar; o cenário diz em que "
        "ambiente econômico você acredita estar investindo. O cenário entra "
        "como premissa adicional na análise de cada ativo e não substitui os "
        "fundamentos nem a estratégia. A IA lê o cenário, mas nunca o altera: "
        "se os fatos o contradisserem, ela avisa que pode ser hora de revisá-lo.")
    try:
        cenario = repo.carregar()
    except Exception as exc:  # noqa: BLE001
        falha_de_acao("Não foi possível ler o cenário.", exc)
        return
    hoje = dt.date.today()
    sinais = divergencia.sinais(cenario, referencias.referencias())
    st.markdown(cartao_situacao(cenario, sinais, hoje), unsafe_allow_html=True)

    v = cenario.versao
    st.button("🔄 Sugerir valores a partir dos dados publicados",
              key=f"cfg_cenario_sugerir_v{v}", on_click=_carregar_sugestoes,
              args=(v,),
              help="Preenche juros, inflação, câmbio e economia internacional "
                   "com os insumos macro publicados. Nada é salvo até você "
                   "conferir e clicar em Salvar.")
    sugeridos = st.session_state.get(_SUGESTAO)
    if sugeridos:
        st.info("Sugestões carregadas em: "
                + ", ".join(mod.ROTULO[k] for k in sugeridos)
                + ". Nada foi salvo. Confira a data de referência de cada "
                  "fonte, escolha direção e confiança e clique em Salvar.")

    direcoes = [_VAZIO] + [k for k, _ in mod.DIRECOES]
    confiancas = [_VAZIO] + [k for k, _ in mod.CONFIANCAS]
    with st.form(f"cfg_cenario_form_v{v}"):
        entradas = {}
        for chave, rotulo, ajuda in mod.CAMPOS:
            it = cenario.item(chave)
            st.markdown(f"**{rotulo}**")
            st.caption(ajuda + (f" · revisto em {it.last_updated}"
                                if it.last_updated else ""))
            c1, c2, c3, c4 = st.columns([3, 1.3, 1.1, 2.4])
            valor = c1.text_input(
                "Valor atual", value=it.current_value,
                key=chave_widget(v, chave, "valor"), max_chars=mod.MAX_VALOR)
            direcao = c2.selectbox(
                "Direção esperada", direcoes,
                index=direcoes.index(it.expected_direction)
                if it.expected_direction in direcoes else 0,
                format_func=lambda k: mod.ROTULO_DIRECAO.get(k, "—"),
                key=chave_widget(v, chave, "direcao"))
            confianca = c3.selectbox(
                "Confiança", confiancas,
                index=confiancas.index(it.confidence)
                if it.confidence in confiancas else 0,
                format_func=lambda k: mod.ROTULO_CONFIANCA.get(k, "—"),
                key=chave_widget(v, chave, "confianca"))
            fonte = c4.text_input(
                "Fonte", value=it.source, key=chave_widget(v, chave, "fonte"),
                max_chars=mod.MAX_FONTE,
                placeholder="Ex.: Focus/BCB, minha leitura")
            entradas[chave] = {"current_value": valor,
                               "expected_direction": direcao,
                               "confidence": confianca, "source": fonte}
        salvar = st.form_submit_button("💾 Salvar cenário", type="primary")

    if not salvar:
        return
    origem = st.session_state.get(_ORIGEM, mod.MANUAL)
    try:
        novo, alterados, erros = repo.salvar(
            entradas, versao_esperada=v, origem=origem)
    except mod.ConflitoDeVersao as exc:
        st.error(str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        falha_de_acao("O cenário não foi salvo.", exc)
        return
    if erros:
        st.error("O cenário não foi salvo:\n\n"
                 + "\n".join(f"- {e}" for e in erros))
        return
    if not alterados:
        st.info("Nada mudou: o cenário salvo continua o mesmo.")
        return
    st.session_state.pop(_ORIGEM, None)
    st.session_state.pop(_SUGESTAO, None)
    st.session_state[_FLASH] = (
        "ok", f"Cenário salvo (versão {novo.versao}). Itens alterados: "
              + ", ".join(mod.ROTULO[k] for k in alterados) + ".")
    st.rerun()
