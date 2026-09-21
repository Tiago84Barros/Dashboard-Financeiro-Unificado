# -*- coding: utf-8 -*-
"""Tela: Grau de Confiança — quanto o app confia em cada seção, e por quê.

Existe porque medir confiança sem lugar para lê-la é decoração: o índice de
confiança de dados ficou meses sem consumidor (A-125) e por isso não mudava
decisão nenhuma. Esta tela é a porta de entrada do relatório.

O que ela promete é limitado de propósito: informa a qualidade do DADO que
sustenta cada seção. Não é previsão, não é recomendação e não substitui
decisão humana nem aconselhamento profissional.

Desde 21/09/2026 a tela **lê** a última medição gravada em
``confianca_snapshots`` e só remede quando o usuário pede. Ela vive dentro de
uma aba, e ``st.tabs`` executa o corpo de todas as abas em toda execução do
script: enquanto ``relatorio()`` era chamado daqui, abrir Configurações para
qualquer outra coisa pagava as sete seções e os três motores.
"""
from __future__ import annotations

import html

import streamlit as st

from core import confianca_snapshot
from core.confianca_secao import (
    FAIXA_ALTA,
    FAIXA_MEDIA,
    ConfiancaSecao,
    confianca_global,
    relatorio,
)
from core.user_context import user_cache_data

# Alta/Media/Baixa vinham de uma quarta paleta (#16a34a, #d97706, #dc2626),
# so desta tela. Sobre a pagina clara o verde e o ambar ficavam escuros
# demais para acompanhar os mesmos tres estados no resto do app; passam a
# ser os tokens semanticos, que ja mudam com o tema.
_COR = {"Alta": "var(--app-primary)", "Media": "var(--app-warning)",
        "Baixa": "var(--app-danger)", "Nao medido": "var(--app-subtle)"}

_ROTULO_FAIXA = {"Alta": "Alta", "Media": "Média", "Baixa": "Baixa",
                 "Nao medido": "Não medido"}


def _pct(valor: float | None) -> str:
    return "—" if valor is None else f"{valor:.0f}%"


def _card(sec: ConfiancaSecao) -> str:
    """Todo o card sai num único bloco HTML. Abrir a div num st.markdown e
    fechá-la em outro produz moldura vazia com o conteúdo fora da borda."""
    cor = _COR.get(sec.faixa, "var(--app-subtle)")
    linhas = []
    for c in sec.componentes:
        if c.medido:
            valor = f'<span style="color:{cor};font-weight:600">{c.pct:.0f}%</span>'
        else:
            # Não medido é cinza e nomeado. Exibi-lo como 0% acusaria um defeito
            # que não foi observado; omiti-lo fingiria cobertura que não houve.
            valor = '<span style="color:var(--app-subtle);font-style:italic">não medido</span>'
        linhas.append(
            '<div style="display:flex;justify-content:space-between;gap:12px;'
            'padding:4px 0;border-bottom:1px solid var(--app-border)">'
            f'<span style="flex:1">{html.escape(c.nome)}'
            f'<span style="color:var(--app-muted);font-size:.78rem;display:block">'
            f'{html.escape(c.evidencia)}</span></span>{valor}</div>'
        )
    notas = "".join(
        f'<div style="color:var(--app-muted);font-size:.8rem;margin-top:6px">⚠ '
        f'{html.escape(n)}</div>' for n in sec.notas)
    cobertura = ""
    if sec.cobertura_da_medicao < 1.0:
        cobertura = (
            f'<div style="color:var(--app-muted);font-size:.8rem;margin-top:6px">'
            f'Percentual apoiado em {sec.cobertura_da_medicao * 100:.0f}% do peso '
            f'avaliado — o restante não pôde ser medido.</div>')
    return (
        '<div style="border:1px solid var(--app-border-strong);border-radius:12px;'
        'padding:16px 18px;margin-bottom:14px;background:var(--app-surface-raised)">'
        '<div style="display:flex;justify-content:space-between;align-items:baseline">'
        f'<div style="font-weight:700;font-size:1.02rem">{html.escape(sec.secao)}</div>'
        f'<div style="font-weight:700;font-size:1.35rem;color:{cor}">'
        f'{_pct(sec.pct)}</div></div>'
        f'<div style="color:{cor};font-size:.82rem;margin-bottom:10px">'
        f'Confiança {_ROTULO_FAIXA.get(sec.faixa, sec.faixa)}</div>'
        + "".join(linhas) + cobertura + notas + '</div>'
    )


_SIMBOLO = {True: ("✓", "var(--app-primary)"),
            False: ("✗", "var(--app-danger)"),
            None: ("—", "var(--app-subtle)")}


@user_cache_data(ttl=900, show_spinner=False)
def _rigor() -> dict:
    """As três notas na mesma lista de perguntas (A-162).

    Cacheado porque cada motor consulta o banco; o dado muda quando uma safra
    ou um certificado é republicado, não a cada clique.
    """
    from core.validacao_motor import DIMENSOES, comparacao_de_rigor
    comp = comparacao_de_rigor()
    return {"dimensoes": list(DIMENSOES),
            "motores": {classe: {d: (None if p is None else (p.ok, p.detalhe))
                                 for d, p in dims.items()}
                        for classe, dims in comp.items()}}


def _resumo(detalhe: object, limite: int = 420) -> str:
    """Encurta o detalhe do portão SEM apagar a ressalva em silêncio.

    O corte anterior era em 150 caracteres e sem marca: a justificativa dos EUA
    reprova pelo intervalo e ressalva na sequência que o score ainda ordena o
    universo -- exatamente a metade que sumia. Truncar é aceitável; truncar sem
    o leitor perceber transforma uma meia-verdade em conclusão.
    """
    texto = str(detalhe)
    return texto if len(texto) <= limite else texto[:limite - 1].rstrip() + "…"


def _tabela_rigor(dados: dict | None) -> None:
    """Por que as três notas não são comparáveis entre si.

    A casca visual é a mesma nas três abas, e isso sugere que 80 no FII vale o
    mesmo que 80 nos EUA. Não vale: cada motor venceu um conjunto diferente de
    condições. Até 28/08/2026 cada um declarava só as perguntas que respondia,
    e o que menos perguntava marcava a melhor nota de metodologia.

    Recebe os dados em vez de medi-los: a tabela vem do mesmo snapshot das
    seções, e medir aqui reabriria o caminho que o botão fechou.
    """
    if not dados:
        return
    motores = dados.get("motores") or {}
    if not motores:
        return
    st.markdown("### Rigor dos três motores de score")
    st.caption(
        "As notas de FII, Empresas B3 e Empresas Americanas saem de motores "
        "independentes e **não são comparáveis entre si**: 80 num não é o 80 "
        "do outro. Abaixo, as mesmas perguntas feitas aos três — ✓ vencida, "
        "✗ reprovada, — não apurada."
    )
    cabecalho = "".join(
        f'<th style="text-align:center;padding:8px 10px;font-weight:600;'
        f'font-size:.82rem">{html.escape(c)}</th>' for c in motores)
    linhas = []
    for dim in dados["dimensoes"]:
        celulas = []
        for classe in motores:
            item = motores[classe].get(dim)
            ok, detalhe = (None, "não declarada") if item is None else item
            simbolo, cor = _SIMBOLO[ok]
            celulas.append(
                f'<td style="text-align:center;padding:8px 10px;vertical-align:top">'
                f'<div style="color:{cor};font-weight:700;font-size:1.1rem">{simbolo}</div>'
                f'<div style="color:var(--app-muted);font-size:.72rem;line-height:1.25">'
                f'{html.escape(_resumo(detalhe))}</div></td>')
        linhas.append(
            '<tr style="border-top:1px solid var(--app-border)">'
            f'<td style="padding:8px 10px;font-weight:600;font-size:.85rem">'
            f'{html.escape(dim)}</td>' + "".join(celulas) + '</tr>')
    st.markdown(
        '<div style="border:1px solid var(--app-border-strong);border-radius:12px;'
        'padding:8px 10px;background:var(--app-surface-raised);overflow-x:auto">'
        '<table style="width:100%;border-collapse:collapse">'
        f'<tr><th style="text-align:left;padding:8px 10px"></th>{cabecalho}</tr>'
        + "".join(linhas) + '</table></div>',
        unsafe_allow_html=True)
    st.caption(
        "Uma pergunta **não apurada** não conta como vencida nem como "
        "reprovada — ela sai da média e continua escrita. Foi o contrário disso "
        "que inflou a nota do motor de FIIs: enquanto ele declarava uma "
        "pergunta e os outros dois declaravam duas, medir menos rendia nota "
        "maior."
    )


def _medir_e_gravar() -> None:
    """A medição cara, e o único caminho que a dispara.

    Fica atrás do botão porque ``st.tabs`` não adia nada: o Streamlit executa o
    corpo de todas as abas em toda execução do script. Enquanto esta função era
    chamada direto do corpo da tela, abrir Configurações para mexer em qualquer
    outra coisa pagava as sete seções e os três motores.
    """
    with st.spinner("Medindo cada seção — isto consulta o banco e demora."):
        secoes = relatorio()
        # O rigor é cacheado por 15 min, mas um recálculo pedido à mão quer o
        # número de agora: servir o cache aqui gravaria no snapshot novo uma
        # metade velha, e o carimbo diria que as duas são da mesma hora.
        _rigor.clear()
        try:
            rigor = _rigor()
        except Exception:  # noqa: BLE001
            rigor = None
        confianca_snapshot.gravar(secoes, rigor)


def _carimbo(medido_em) -> None:
    """Quando a medição foi feita, e o aviso quando ela envelheceu.

    A idade é requisito, não enfeite: um snapshot de três semanas marcando
    "Alta" soa como rigor e já pode ser falso. O texto sai de ``medido_em``,
    nunca de uma frase fixa — frase fixa envelhece invertida.
    """
    quando = medido_em.astimezone()
    velha = confianca_snapshot.vencida(medido_em)
    cor = "var(--app-warning)" if velha else "var(--app-muted)"
    aviso = ""
    if velha:
        aviso = (
            '<div style="color:var(--app-warning);font-size:.82rem;margin-top:4px">'
            f'⚠ Medição de mais de {confianca_snapshot.VALIDADE_DIAS} dias. '
            'Vitrines, safras e ingestões mudaram desde então; recalcule antes '
            'de usar estes números para decidir.</div>')
    st.markdown(
        '<div style="border:1px solid var(--app-border);border-radius:10px;'
        'padding:10px 14px;margin-bottom:14px;background:var(--app-surface)">'
        f'<div style="color:{cor};font-size:.85rem">Medido em '
        f'<strong>{quando.strftime("%d/%m/%Y %H:%M")}</strong> '
        f'({html.escape(confianca_snapshot.rotulo_idade(medido_em))}).</div>'
        + aviso + '</div>',
        unsafe_allow_html=True,
    )


def render_corpo() -> None:
    """O conteúdo da tela, sem título nem legenda.

    Quem embute isto numa aba (``views/configuracoes.py``) já desenhou o
    próprio cabeçalho; repetir ``st.title`` dentro da aba duplicaria o título
    da página.
    """
    snap = confianca_snapshot.carregar_ultimo()

    if st.button("🔄 Recalcular agora", key="confianca_recalcular",
                 type="primary" if snap is None else "secondary"):
        try:
            _medir_e_gravar()
        except confianca_snapshot.TabelaAusente as exc:
            # Falhar calado aqui seria o pior desfecho possível: o usuário
            # pagaria a medicão inteira e voltaria para a mesma tela vazia.
            st.error(str(exc))
            return
        st.rerun()

    if snap is None:
        st.info(
            "Nenhuma medição gravada ainda. Medir consulta o banco em todas as "
            "seções e leva alguns minutos, por isso não acontece sozinho ao "
            "abrir esta aba — clique em **Recalcular agora** quando quiser o "
            "número."
        )
        return

    secoes, rigor, medido_em = snap
    _carimbo(medido_em)
    geral = confianca_global(secoes)

    cor_geral = _COR["Alta" if (geral or 0) >= FAIXA_ALTA else
                     "Media" if (geral or 0) >= FAIXA_MEDIA else "Baixa"]
    st.markdown(
        '<div style="border:1px solid var(--app-border-strong);border-radius:14px;'
        'padding:20px;margin-bottom:20px;text-align:center;'
        'background:var(--app-surface-raised)">'
        '<div style="color:var(--app-muted);font-size:.85rem;letter-spacing:.06em">'
        'CONFIANÇA GERAL DO APLICATIVO</div>'
        f'<div style="font-size:2.6rem;font-weight:800;color:{cor_geral}">'
        f'{_pct(geral)}</div>'
        '<div style="color:var(--app-muted);font-size:.8rem">média das seções, ponderada '
        'pelo quanto de cada uma foi efetivamente medido</div></div>',
        unsafe_allow_html=True,
    )

    col_esq, col_dir = st.columns(2)
    for i, sec in enumerate(secoes):
        (col_esq if i % 2 == 0 else col_dir).markdown(
            _card(sec), unsafe_allow_html=True)

    _tabela_rigor(rigor)

    st.markdown("### Como ler")
    st.markdown(
        f"- **Alta (≥ {FAIXA_ALTA:.0f}%)** — a seção sustenta decisão nos "
        "limites que ela própria declara.\n"
        f"- **Média ({FAIXA_MEDIA:.0f}–{FAIXA_ALTA:.0f}%)** — serve para "
        "estudar; confira a evidência do componente mais baixo antes de agir.\n"
        f"- **Baixa (< {FAIXA_MEDIA:.0f}%)** — trate como exploratório.\n\n"
        "**Abrangência** pesa pouco de propósito: uma seção pode ser muito "
        "confiável sobre uma fatia menor do mercado, e punir isso empurraria o "
        "app a inflar o universo com ativo ruim — o contrário do que se quer. "
        "Ativos sem dado suficiente são descartados do universo de decisão, "
        "não corrigidos no escuro."
    )


def render() -> None:
    st.title("🎯 Grau de Confiança")
    st.caption(
        "Qualidade do dado que sustenta cada seção. Apoio analítico — não é "
        "previsão, recomendação nem substituto de decisão humana."
    )
    render_corpo()
