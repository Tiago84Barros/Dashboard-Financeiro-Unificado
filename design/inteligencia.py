"""Componentes visuais da Inteligência de Mercado.

Cor nunca é o único canal
-------------------------
Todo estado desta área -- qualidade do dado, frescor, situação do ativo, nível
de crise, canal do alerta -- chega à tela por **três** canais simultâneos:
ícone, texto e cor. Quem não distingue verde de vermelho lê o mesmo que os
demais, e quem lê num print em escala de cinza também.

O vocabulário não é redefinido aqui: ícone, rótulo e cor vêm de
``core.inteligencia.qualificacao.APARENCIA`` e vizinhos. Se este módulo
escolhesse os próprios ícones, a tela e a explicação da LLM passariam a falar
línguas diferentes sobre o mesmo dado.

Card sai num ``st.markdown`` só
-------------------------------
Div aberta num bloco e fechada em outro vira moldura vazia com o conteúdo caindo
fora da borda. Todo card daqui é montado como string única.
"""
from __future__ import annotations

import datetime as dt
from html import escape

import streamlit as st

from core.inteligencia import alertas as al
from core.inteligencia import painel as P
from core.inteligencia import qualificacao as qz
from core.seguranca import travas as tv

__all__ = [
    "selo_qualidade", "linha_valor", "card_valor", "grade_valores",
    "selo_frescor", "barra_frescor", "selo_provedor", "cabecalho_bloco",
    "bloco_completo", "selo_situacao", "cartao_noticia", "cartao_alerta",
    "area_tecnica", "aviso_sem_garantia",
]

AVISO_SEM_GARANTIA = (
    "Nada aqui é recomendação de compra ou venda, nem garantia de retorno. "
    "Estimativas são publicadas em faixa e podem estar erradas."
)


def _linha(texto: object, *, aspas: bool = False) -> str:
    """Escapa **e** achata. A quebra de linha aqui não é cosmética.

    ``escape`` cobre ``<``, ``&`` e ``"``. O canal que ninguém guardava era o
    espaço em branco: o markdown do Streamlit encerra o bloco HTML na primeira
    linha em branco, e a partir dali a própria tag vai para a tela como texto.
    Uma mensagem do psycopg2 -- que vem com quebras e um parágrafo em branco
    antes do ``[SQL:`` -- bastava para imprimir
    ``" style="--badge-color:#D9534F...>`` no meio do card das travas.

    Rótulo constante nunca tem quebra, então o defeito só aparece com texto que
    vem de fora: erro de banco, título de notícia, motivo de alerta. É
    exatamente o texto que este módulo existe para mostrar.
    """
    return escape(" ".join(str(texto).split()), quote=aspas)


def _selo(icone: str, rotulo: str, cor: str, ajuda: str = "") -> str:
    ajuda_attr = f' title="{_linha(ajuda, aspas=True)}"' if ajuda else ""
    return (f'<span class="app-status-badge"{ajuda_attr} '
            f'style="--badge-color:{cor};--badge-bg:rgba(0,0,0,0.06)">'
            f'<span aria-hidden="true">{_linha(icone)}</span> '
            f'{_linha(rotulo)}</span>')


def selo_qualidade(qualidade: str) -> str:
    """Fato, hipótese, estimativa ou não medido -- em ícone, texto e cor."""
    ap = qz.APARENCIA[qualidade]
    return _selo(ap["icone"], ap["rotulo"], ap["cor"], ap.get("ajuda", ""))


def selo_frescor(frescor: qz.Frescor, agora: dt.datetime | None = None) -> str:
    ap = frescor.aparencia(agora)
    return _selo(ap["icone"], ap["rotulo"], ap["cor"], frescor.descrever(agora))


def selo_situacao(situacao: str) -> str:
    ap = P.APARENCIA_SITUACAO[situacao]
    return _selo(ap["icone"], ap["rotulo"], ap["cor"])


def selo_provedor(provedor: qz.Provedor) -> str:
    cor = "#00C896" if provedor.disponivel else "#FC5C7D"
    icone = "●" if provedor.disponivel else "✕"
    rotulo = f"{provedor.nome}: {'no ar' if provedor.disponivel else 'fora do ar'}"
    return _selo(icone, rotulo, cor, provedor.descrever())


def linha_valor(valor: qz.Valor) -> str:
    """Um valor em uma linha: rótulo, número, selo de qualidade e contexto."""
    extras: list[str] = []
    if valor.confianca:
        extras.append(f"confiança {valor.confianca}")
    if valor.horizonte:
        extras.append(f"horizonte {valor.horizonte}")
    if valor.fonte:
        extras.append(f"fonte: {valor.fonte}")
    if valor.observacao:
        extras.append(valor.observacao)
    rodape = (f'<div class="app-kpi-delta" style="color:#9CA3AF">'
              f'{_linha(" · ".join(extras))}</div>' if extras else "")
    return (
        '<div class="app-kpi-card" style="--app-kpi-accent:'
        f'{valor.aparencia["cor"]}">'
        f'<div class="app-kpi-label">{_linha(valor.rotulo)}</div>'
        f'<div class="app-kpi-value">{_linha(valor.texto)}</div>'
        f'{selo_qualidade(valor.qualidade)}{rodape}</div>')


def card_valor(valor: qz.Valor) -> None:
    st.markdown(linha_valor(valor), unsafe_allow_html=True)


def grade_valores(valores, colunas: int = 3) -> None:
    """Grade de cards. Não medidos vão junto, e não no fim escondidos.

    Separar os ausentes numa gaveta faria a tela parecer completa. Eles ficam na
    mesma grade, com o selo "Não medido", porque a lacuna é informação.
    """
    valores = list(valores)
    if not valores:
        return
    for inicio in range(0, len(valores), colunas):
        faixa = valores[inicio:inicio + colunas]
        cols = st.columns(len(faixa))
        for col, valor in zip(cols, faixa):
            with col:
                card_valor(valor)


def barra_frescor(pn: P.Painel) -> None:
    """Última atualização, fontes vencidas e estado dos provedores."""
    ultima = pn.ultima_atualizacao
    texto = (ultima.strftime("%d/%m/%Y %H:%M UTC") if ultima
             else "nenhuma fonte informou data de atualização")
    selos = "".join(selo_frescor(f, pn.gerado_em) for f in pn.frescor)
    selos += "".join(selo_provedor(p) for p in pn.provedores)
    st.markdown(
        '<div class="app-kpi-card" style="--app-kpi-accent:#4A9EFF">'
        '<div class="app-kpi-label">Última atualização (fonte mais antiga)'
        '</div>'
        f'<div class="app-kpi-value">{_linha(texto)}</div>{selos}</div>',
        unsafe_allow_html=True)

    if pn.desatualizados or pn.provedores_fora:
        nomes = [f.rotulo for f in pn.desatualizados]
        nomes += [p.nome for p in pn.provedores_fora]
        st.warning(
            "**Dados desatualizados ou indisponíveis:** " + ", ".join(nomes)
            + ". A ausência de informação aqui não significa ausência de risco.")


def cabecalho_bloco(bloco: qz.Bloco, agora: dt.datetime | None = None) -> None:
    partes = [f'<span class="app-section-title">{_linha(bloco.titulo)}</span>']
    partes.append(_selo("◑", f"cobertura {bloco.cobertura:.0%}", "#4A9EFF",
                        "fração dos componentes que foi possível medir"))
    if bloco.frescor is not None:
        partes.append(selo_frescor(bloco.frescor, agora))
    st.markdown('<div class="app-section-heading">' + "".join(partes) + "</div>",
                unsafe_allow_html=True)
    if bloco.explicacao_simples:
        st.caption(bloco.explicacao_simples)


def area_tecnica(bloco: qz.Bloco, chave: str = "") -> None:
    """A área que se expande. O simples fica fora; o técnico, dentro."""
    if not bloco.detalhe_tecnico and not bloco.limitacoes:
        return
    with st.expander(f"Detalhe técnico — {bloco.titulo}", expanded=False):
        if bloco.detalhe_tecnico:
            st.markdown("**Como este resultado foi obtido**")
            for linha in bloco.detalhe_tecnico:
                st.markdown(f"- {linha}")
        if bloco.limitacoes:
            st.markdown("**Limitações declaradas**")
            for linha in bloco.limitacoes:
                st.markdown(f"- {linha}")
        if bloco.nao_medidos:
            st.markdown("**Não medido nesta execução**")
            for v in bloco.nao_medidos:
                st.markdown(f"- {v.rotulo}: {v.observacao or 'sem fonte'}")


def bloco_completo(bloco: qz.Bloco, *, colunas: int = 3,
                   agora: dt.datetime | None = None) -> None:
    cabecalho_bloco(bloco, agora)
    grade_valores(bloco.valores, colunas=colunas)
    if bloco.limitacoes:
        st.caption("Limitações: " + " · ".join(bloco.limitacoes))
    area_tecnica(bloco)


def cartao_noticia(item: P.ItemNoticia) -> None:
    """Fonte, data e hora sempre visíveis -- e o estado de verificação junto."""
    selo = selo_qualidade(item.qualidade_conteudo)
    verif = _selo("✓" if item.confirmado else "?",
                  "Confirmado" if item.confirmado else "Não confirmado",
                  "#00C896" if item.confirmado else "#E8B84B",
                  item.estado_verificacao)
    marcas = ""
    for v in item.valores():
        marcas += (f'<div class="app-kpi-delta" style="color:#9CA3AF">'
                   f'{_linha(v.descrever())}</div>')
    link = (f'<div class="app-kpi-delta"><a href="{_linha(item.url, aspas=True)}"'
            ' target="_blank" rel="noopener">abrir na fonte</a></div>'
            if item.url else "")
    st.markdown(
        '<div class="app-kpi-card" style="--app-kpi-accent:#4A9EFF">'
        f'<div class="app-kpi-value">{_linha(item.titulo)}</div>'
        f'<div class="app-kpi-label">{_linha(item.carimbo)}</div>'
        f'{selo}{verif}{marcas}{link}</div>',
        unsafe_allow_html=True)


def cartao_alerta(alerta: al.Alerta) -> None:
    ap = alerta.aparencia
    cor = {0: "#9CA3AF", 1: "#4A9EFF", 2: "#E8B84B",
           3: "#FC5C7D", 4: "#FC5C7D"}.get(alerta.nivel_codigo, "#9CA3AF")
    st.markdown(
        f'<div class="app-kpi-card" style="--app-kpi-accent:{cor}">'
        f'<div class="app-kpi-value">{_linha(alerta.titulo)}</div>'
        f'<div class="app-kpi-label">{_linha(alerta.corpo)}</div>'
        + _selo(ap["icone"], ap["rotulo"], cor, alerta.motivo_canal)
        + f'<div class="app-kpi-delta" style="color:#9CA3AF">'
          f'{_linha(alerta.motivo_canal)}</div></div>',
        unsafe_allow_html=True)


def aviso_sem_garantia() -> None:
    st.caption(AVISO_SEM_GARANTIA)


# ── Travas de circuito ───────────────────────────────────────────────────────
#: Rótulo curto de cada trava. O nome interno (``dados_vencidos``) é chave de
#: código, não frase de tela.
ROTULO_TRAVA: dict[str, str] = {
    "dados_vencidos": "Dados vencidos",
    "provedores_divergem": "Fontes divergem",
    "preco_indisponivel": "Preço indisponível",
    "modelo_fora_dos_limites": "Modelo fora dos limites",
    "llm_inventou_numero": "LLM inventou número",
    "auditoria_falhou": "Auditoria não gravou",
}

#: Três estados, três símbolos, três palavras -- nunca só três cores. Quem não
#: distingue verde de vermelho lê ``⊘``, ``✓`` e ``·``, e lê também o rótulo
#: "disparada" / "ok" / "não verificada" no ``title``.
APARENCIA_TRAVA: dict[str, tuple[str, str, str]] = {
    "disparada": ("⊘", "#D9534F", "trava disparada"),
    "ok": ("✓", "#4CAF50", "verificada, não disparou"),
    "nao_verificada": ("·", "#8A8A8A", "não verificada nesta execução"),
}


def _situacao_trava(trava) -> str:
    if trava.disparada is None:
        return "nao_verificada"
    return "disparada" if trava.disparada else "ok"


def selo_trava(trava) -> str:
    icone, cor, ajuda = APARENCIA_TRAVA[_situacao_trava(trava)]
    rotulo = ROTULO_TRAVA.get(trava.nome, trava.nome)
    detalhe = f"{ajuda}: {trava.descrever()}"
    return _selo(icone, rotulo, cor, detalhe)


def _aviso_de_trava(trava) -> str:
    """O texto da trava em markdown; o detalhe técnico, em código.

    O detalhe é a mensagem do banco, e ela vem cheia de ``[...]`` e ``(...)``.
    Solta no markdown ela vira sintaxe de link e chega truncada à tela -- o
    aviso da auditoria terminava em ``[SQL: SELECT 1 FROM public)``, perdendo
    justamente o nome da tabela que falta. Dentro de crase ela chega inteira, e
    fica visualmente separada do texto que o APP4 escreveu.
    """
    corpo = " ".join(tv.TEXTO.get(trava.nome, trava.nome).split())
    detalhe = " ".join(str(trava.detalhe).split()).replace("`", "'")
    return f"{corpo} `{detalhe}`" if detalhe else corpo


def barra_travas(estado) -> None:
    """As seis travas, o que cada uma desligou e o que ninguém verificou.

    O motor de travas existia sem porta de entrada -- avaliava e não era lido
    por tela nenhuma (``memoria: diagnostico-precisa-porta-de-entrada``). Esta
    é a porta. Ela publica as três situações separadamente porque "não
    disparou" e "não verifiquei" não são a mesma notícia, e mostrar as duas
    como silêncio seria publicar uma segurança que ninguém mediu.
    """
    if estado is None:
        return
    selos = "".join(selo_trava(t) for t in estado.travas)
    verificadas = len(estado.travas) - len(estado.nao_verificadas)
    bloqueios = estado.bloqueios
    resumo = f"{verificadas} de {len(estado.travas)} verificadas"
    if bloqueios:
        resumo += f" · {len(bloqueios)} recurso(s) bloqueado(s)"
    cor = "#D9534F" if bloqueios else "#4A9EFF"
    # Card inteiro num st.markdown só: div aberta num bloco e fechada em outro
    # vira moldura vazia (``memoria: card-css-bloco-unico-streamlit``).
    st.markdown(
        f'<div class="app-kpi-card" style="--app-kpi-accent:{cor}">'
        '<div class="app-kpi-label">Travas de segurança</div>'
        f'<div class="app-kpi-value">{_linha(resumo)}</div>{selos}</div>',
        unsafe_allow_html=True)

    for trava in estado.disparadas:
        st.warning(f"⊘ **{ROTULO_TRAVA.get(trava.nome, trava.nome)}** — "
                   f"{_aviso_de_trava(trava)}")
    if estado.nao_verificadas:
        nomes = ", ".join(ROTULO_TRAVA.get(t.nome, t.nome)
                          for t in estado.nao_verificadas)
        st.caption(
            f"· Não verificadas nesta execução: {nomes}. Não verificada não é "
            "o mesmo que em ordem -- é ausência de medição, e nada aqui "
            "autoriza tratá-la como sinal de que está tudo bem.")
