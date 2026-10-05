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

Apresentação
------------
A tela está organizada em faixas, na ordem em que se lê: **estado da medição**
(quando, que idade, quantas seções), **o número do app**, **confiança por
seção**, **rigor dos três motores** e **como ler**. O estilo saiu dos
``style=`` espalhados por cada ``<div>`` e virou folha única (``_CONF_CSS``)
com classes e tokens ``--app-*``, que acompanham tema claro e escuro.

Cada função monta e devolve HTML; quem desenha é ``render_corpo``. Isso deixa
o que esta tela promete (não medido continua nomeado, aviso de medição velha
só aparece quando ela está velha, texto do banco entra escapado) testável sem
subir o Streamlit.
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

#: Folha unica da tela. Cor por token ``--app-*``, nunca literal: a paleta
#: clara redefine os mesmos nomes e a tela acompanha os dois temas sozinha
#: (``memoria: tema-claro-so-alcanca-o-que-passa-por-token``).
_CONF_CSS = """
<style>
.conf-estado {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 7px;
    margin: 2px 0 4px;
}
.conf-chip {
    --conf-tom: var(--app-muted, #8A99AE);
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 4px 10px;
    border: 1px solid color-mix(in srgb, var(--conf-tom) 30%, transparent);
    border-radius: 999px;
    background: color-mix(in srgb, var(--conf-tom) 10%, transparent);
    color: var(--app-text, #E2E8F0);
    font-size: .66rem;
    font-weight: 700;
    white-space: nowrap;
}
.conf-chip b { color: var(--conf-tom); font-weight: 820; }
.conf-chip-nome {
    color: var(--app-muted, #8A99AE);
    font-size: .6rem;
    font-weight: 700;
    letter-spacing: .06em;
    text-transform: uppercase;
}
.conf-aviso {
    display: flex;
    align-items: flex-start;
    gap: 9px;
    margin: 12px 0 0;
    padding: 10px 14px;
    border: 1px solid color-mix(in srgb, var(--app-warning, #F6C90E) 34%, transparent);
    border-left: 3px solid var(--app-warning, #F6C90E);
    border-radius: 10px;
    background: color-mix(in srgb, var(--app-warning, #F6C90E) 9%, transparent);
    color: var(--app-text, #E2E8F0);
    font-size: .76rem;
    line-height: 1.45;
}
.conf-hero {
    --conf-tom: var(--app-primary, #00C896);
    display: grid;
    grid-template-columns: minmax(0, auto) minmax(0, 1fr);
    align-items: center;
    gap: 22px;
    margin: 16px 0 6px;
    padding: 20px 24px;
    border: 1px solid var(--app-border, rgba(148,163,184,.14));
    border-left: 3px solid var(--conf-tom);
    border-radius: 14px;
    background: linear-gradient(135deg,
        color-mix(in srgb, var(--conf-tom) 10%, transparent),
        var(--app-surface-raised, #171D2B) 58%);
}
.conf-hero-rotulo {
    color: var(--app-muted, #8A99AE);
    font-size: .62rem;
    font-weight: 760;
    letter-spacing: .09em;
    text-transform: uppercase;
}
.conf-hero-valor {
    color: var(--conf-tom);
    font-size: 3rem;
    font-variant-numeric: tabular-nums;
    font-weight: 860;
    letter-spacing: -.03em;
    line-height: 1;
}
.conf-hero-faixa {
    margin-top: 2px;
    color: var(--conf-tom);
    font-size: .76rem;
    font-weight: 780;
}
.conf-hero-texto {
    color: var(--app-muted, #8A99AE);
    font-size: .78rem;
    line-height: 1.5;
}
.conf-hero-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 10px;
}
.conf-barra {
    position: relative;
    height: 6px;
    margin-top: 12px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--app-muted, #8A99AE) 20%, transparent);
    overflow: hidden;
}
.conf-barra span {
    display: block;
    height: 100%;
    border-radius: 999px;
    background: var(--conf-tom);
}
.conf-secao {
    --conf-tom: var(--app-info, #4A9EFF);
    display: flex;
    align-items: center;
    gap: 10px;
    margin: 24px 0 12px;
    padding: 8px 13px;
    border: 1px solid var(--app-border, rgba(148,163,184,.12));
    border-left: 3px solid var(--conf-tom);
    border-radius: 10px;
    background: linear-gradient(90deg,
        color-mix(in srgb, var(--conf-tom) 7%, transparent),
        var(--app-surface, #121722) 45%);
}
.conf-secao-titulo {
    flex: 0 0 auto;
    color: var(--app-text, #F1F5F9);
    font-size: .78rem;
    font-weight: 820;
}
.conf-secao-texto {
    flex: 1 1 auto;
    min-width: 0;
    color: var(--app-muted, #8A99AE);
    font-size: .71rem;
    line-height: 1.45;
}
.conf-secao-selo {
    flex: 0 0 auto;
    padding: 3px 9px;
    border: 1px solid color-mix(in srgb, var(--conf-tom) 28%, transparent);
    border-radius: 999px;
    background: color-mix(in srgb, var(--conf-tom) 9%, transparent);
    color: var(--app-text, #DCE7F5);
    font-size: .61rem;
    font-weight: 720;
    white-space: nowrap;
}
.conf-cards {
    /* Grade propria, e nao duas ``st.columns``: com colunas, o card alto de
       uma seção empurrava o par ao lado e as duas pilhas saíam desencontradas.
       ``auto-fit`` ainda desce para uma coluna sozinho no telefone. */
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(330px, 1fr));
    gap: 14px;
}
.conf-card {
    --conf-tom: var(--app-subtle, #64748B);
    display: flex;
    flex-direction: column;
    min-width: 0;
    padding: 16px 18px;
    border: 1px solid var(--app-border, rgba(148,163,184,.14));
    border-left: 3px solid var(--conf-tom);
    border-radius: 12px;
    background: var(--app-surface-raised, #171D2B);
}
.conf-card-topo {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 10px;
}
.conf-card-nome {
    color: var(--app-text, #F1F5F9);
    font-size: 1rem;
    font-weight: 780;
    letter-spacing: -.01em;
}
.conf-card-pct {
    color: var(--conf-tom);
    font-size: 1.35rem;
    font-variant-numeric: tabular-nums;
    font-weight: 840;
    line-height: 1;
}
.conf-card-faixa {
    margin-top: 3px;
    color: var(--conf-tom);
    font-size: .68rem;
    font-weight: 740;
    letter-spacing: .04em;
    text-transform: uppercase;
}
.conf-comp {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 12px;
    padding: 7px 0;
    border-bottom: 1px solid var(--app-border, rgba(148,163,184,.12));
}
.conf-comp:last-of-type { border-bottom: none; }
.conf-comp-nome {
    flex: 1 1 auto;
    min-width: 0;
    color: var(--app-text, #E2E8F0);
    font-size: .8rem;
    font-weight: 640;
}
.conf-comp-evidencia {
    display: block;
    margin-top: 2px;
    color: var(--app-muted, #8A99AE);
    font-size: .71rem;
    font-weight: 400;
    line-height: 1.35;
}
.conf-comp-valor {
    flex: 0 0 auto;
    color: var(--conf-tom);
    font-size: .86rem;
    font-variant-numeric: tabular-nums;
    font-weight: 780;
}
.conf-comp-ausente {
    flex: 0 0 auto;
    color: var(--app-subtle, #64748B);
    font-size: .74rem;
    font-style: italic;
}
.conf-card-rodape {
    margin-top: 10px;
    padding-top: 9px;
    border-top: 1px solid var(--app-border, rgba(148,163,184,.12));
    color: var(--app-muted, #8A99AE);
    font-size: .71rem;
    line-height: 1.4;
}
.conf-card-rodape div + div { margin-top: 4px; }
.conf-rigor {
    overflow-x: auto;
    border: 1px solid var(--app-border, rgba(148,163,184,.14));
    border-radius: 12px;
    background: var(--app-surface-raised, #171D2B);
}
.conf-rigor table { width: 100%; border-collapse: collapse; }
.conf-rigor th {
    padding: 11px 12px;
    color: var(--app-muted, #8A99AE);
    font-size: .64rem;
    font-weight: 780;
    letter-spacing: .06em;
    text-align: center;
    text-transform: uppercase;
}
.conf-rigor th.conf-rigor-dim { text-align: left; }
.conf-rigor td {
    padding: 10px 12px;
    border-top: 1px solid var(--app-border, rgba(148,163,184,.12));
    text-align: center;
    vertical-align: top;
}
.conf-rigor td.conf-rigor-dim {
    color: var(--app-text, #E2E8F0);
    font-size: .8rem;
    font-weight: 700;
    text-align: left;
}
.conf-rigor-simbolo {
    font-size: 1.05rem;
    font-weight: 800;
    line-height: 1.1;
}
.conf-rigor-detalhe {
    margin-top: 2px;
    color: var(--app-muted, #8A99AE);
    font-size: .69rem;
    line-height: 1.3;
}
.conf-legenda {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
    gap: 12px;
}
.conf-legenda-item {
    --conf-tom: var(--app-primary, #00C896);
    padding: 12px 14px;
    border: 1px solid var(--app-border, rgba(148,163,184,.14));
    border-left: 3px solid var(--conf-tom);
    border-radius: 11px;
    background: var(--app-surface, #121722);
}
.conf-legenda-faixa {
    color: var(--conf-tom);
    font-size: .74rem;
    font-weight: 820;
}
.conf-legenda-texto {
    margin-top: 4px;
    color: var(--app-muted, #8A99AE);
    font-size: .73rem;
    line-height: 1.45;
}
@media (max-width: 760px) {
    .conf-hero { grid-template-columns: 1fr; gap: 14px; }
    .conf-secao { align-items: flex-start; flex-direction: column; gap: 6px; }
}
</style>
"""


def _pct(valor: float | None) -> str:
    return "—" if valor is None else f"{valor:.0f}%"


def _chip(nome: str, valor: str, cor: str) -> str:
    rotulo = f'<span class="conf-chip-nome">{html.escape(nome)}</span>' if nome else ""
    return (f'<span class="conf-chip" style="--conf-tom:{cor}">'
            f"{rotulo}<b>{html.escape(valor)}</b></span>")


def _faixa_secao(titulo: str, texto: str, selo: str, cor: str) -> str:
    return (f'<section class="conf-secao" style="--conf-tom:{cor}">'
            f'<span class="conf-secao-titulo">{html.escape(titulo)}</span>'
            f'<span class="conf-secao-texto">{html.escape(texto)}</span>'
            f'<span class="conf-secao-selo">{html.escape(selo)}</span></section>')


def _card(sec: ConfiancaSecao) -> str:
    """Todo o card sai num único bloco HTML. Abrir a div num st.markdown e
    fechá-la em outro produz moldura vazia com o conteúdo fora da borda."""
    cor = _COR.get(sec.faixa, "var(--app-subtle)")
    rotulo = _ROTULO_FAIXA.get(sec.faixa, sec.faixa)
    faixa_rotulo = rotulo if sec.faixa == "Nao medido" else f"Confiança {rotulo}"
    linhas = []
    for c in sec.componentes:
        if c.medido:
            valor = f'<span class="conf-comp-valor">{c.pct:.0f}%</span>'
        else:
            # Não medido é cinza e nomeado. Exibi-lo como 0% acusaria um defeito
            # que não foi observado; omiti-lo fingiria cobertura que não houve.
            valor = '<span class="conf-comp-ausente">não medido</span>'
        linhas.append(
            '<div class="conf-comp"><span class="conf-comp-nome">'
            f'{html.escape(c.nome)}'
            f'<span class="conf-comp-evidencia">{html.escape(c.evidencia)}</span>'
            f'</span>{valor}</div>'
        )
    rodape = []
    cobertura = sec.cobertura_da_medicao
    if cobertura <= 0:
        # Sem nenhum componente medido nao existe percentual a declarar: dizer
        # "apoiado em 0% do peso" sugere uma medicao que deu zero.
        rodape.append('<div>Nenhum componente desta seção pôde ser medido.</div>')
    elif cobertura < 1.0:
        rodape.append(
            f'<div>Percentual apoiado em {cobertura * 100:.0f}% '
            'do peso avaliado — o restante não pôde ser medido.</div>')
    rodape += [f'<div>⚠ {html.escape(n)}</div>' for n in sec.notas]
    rodape_html = (f'<div class="conf-card-rodape">{"".join(rodape)}</div>'
                   if rodape else "")
    return (
        f'<article class="conf-card" style="--conf-tom:{cor}">'
        '<div class="conf-card-topo">'
        f'<span class="conf-card-nome">{html.escape(sec.secao)}</span>'
        f'<span class="conf-card-pct">{_pct(sec.pct)}</span></div>'
        f'<div class="conf-card-faixa">{faixa_rotulo}</div>'
        f'<div class="conf-comps">{"".join(linhas)}</div>'
        + rodape_html + "</article>"
    )


def _cards_html(secoes) -> str:
    """Os cards das seções numa grade só.

    Saem num ``st.markdown`` único de propósito: era um laço alternando duas
    ``st.columns``, e cada coluna empilhava os seus na própria altura — bastava
    uma seção com muitas notas para a fileira sair desencontrada.
    """
    return ('<section class="conf-cards">'
            + "".join(_card(s) for s in secoes) + "</section>")


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


def _tabela_rigor_html(dados: dict | None) -> str:
    """Por que as três notas não são comparáveis entre si.

    A casca visual é a mesma nas três abas, e isso sugere que 80 no FII vale o
    mesmo que 80 nos EUA. Não vale: cada motor venceu um conjunto diferente de
    condições. Até 28/08/2026 cada um declarava só as perguntas que respondia,
    e o que menos perguntava marcava a melhor nota de metodologia.

    Recebe os dados em vez de medi-los: a tabela vem do mesmo snapshot das
    seções, e medir aqui reabriria o caminho que o botão fechou.
    """
    if not dados:
        return ""
    motores = dados.get("motores") or {}
    if not motores:
        return ""
    cabecalho = "".join(f"<th>{html.escape(c)}</th>" for c in motores)
    linhas = []
    for dim in dados["dimensoes"]:
        celulas = []
        for classe in motores:
            item = motores[classe].get(dim)
            ok, detalhe = (None, "não declarada") if item is None else item
            simbolo, cor = _SIMBOLO[ok]
            celulas.append(
                f'<td><div class="conf-rigor-simbolo" style="color:{cor}">{simbolo}</div>'
                f'<div class="conf-rigor-detalhe">{html.escape(_resumo(detalhe))}'
                "</div></td>")
        linhas.append('<tr><td class="conf-rigor-dim">'
                      f'{html.escape(dim)}</td>' + "".join(celulas) + "</tr>")
    return (
        _faixa_secao(
            "Rigor dos três motores",
            "As notas de FII, Empresas B3 e Empresas Americanas saem de motores "
            "independentes e não são comparáveis entre si: 80 num não é o 80 do "
            "outro. Abaixo, as mesmas perguntas feitas aos três.",
            "✓ vencida · ✗ reprovada · — não apurada",
            "var(--app-accent, #9333EA)",
        )
        + '<div class="conf-rigor"><table>'
        f'<tr><th class="conf-rigor-dim"></th>{cabecalho}</tr>'
        + "".join(linhas) + "</table></div>"
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


def _carimbo_html(medido_em, secoes) -> str:
    """Quando a medição foi feita, e o aviso quando ela envelheceu.

    A idade é requisito, não enfeite: um snapshot de três semanas marcando
    "Alta" soa como rigor e já pode ser falso. O texto sai de ``medido_em``,
    nunca de uma frase fixa — frase fixa envelhece invertida. O aviso só existe
    quando ``vencida()`` diz que existe.
    """
    quando = medido_em.astimezone()
    velha = confianca_snapshot.vencida(medido_em)
    tom = "var(--app-warning)" if velha else "var(--app-primary)"
    medidas = sum(1 for s in secoes if s.pct is not None)
    chips = [
        _chip("medido em", quando.strftime("%d/%m/%Y %H:%M"), tom),
        _chip("idade", confianca_snapshot.rotulo_idade(medido_em), tom),
        _chip("validade", f"{confianca_snapshot.VALIDADE_DIAS} dias",
              "var(--app-muted)"),
        _chip("seções medidas", f"{medidas} de {len(secoes)}",
              "var(--app-muted)"),
    ]
    aviso = ""
    if velha:
        aviso = (
            '<div class="conf-aviso"><span>⚠</span><span>'
            f'Medição de mais de {confianca_snapshot.VALIDADE_DIAS} dias. '
            "Vitrines, safras e ingestões mudaram desde então; recalcule antes "
            "de usar estes números para decidir.</span></div>")
    return '<div class="conf-estado">' + "".join(chips) + "</div>" + aviso


def _hero_html(geral: float | None, secoes) -> str:
    """O número do app, com a distribuição que o sustenta ao lado.

    A manchete sozinha não diz se o 71% vem de sete seções parecidas ou de duas
    altas carregando três baixas. Os chips por faixa respondem isso sem exigir
    que se leia a grade inteira.
    """
    faixa = ("Alta" if (geral or 0) >= FAIXA_ALTA else
             "Media" if (geral or 0) >= FAIXA_MEDIA else "Baixa")
    if geral is None:
        faixa = "Nao medido"
    cor = _COR[faixa]
    contagem = {f: sum(1 for s in secoes if s.faixa == f)
                for f in ("Alta", "Media", "Baixa", "Nao medido")}
    chips = "".join(
        _chip(_ROTULO_FAIXA[f], f"{n} {'seção' if n == 1 else 'seções'}", _COR[f])
        for f, n in contagem.items() if n
    )
    largura = max(0.0, min(100.0, geral or 0.0))
    return (
        f'<section class="conf-hero" style="--conf-tom:{cor}">'
        "<div>"
        '<div class="conf-hero-rotulo">Confiança geral do aplicativo</div>'
        f'<div class="conf-hero-valor">{_pct(geral)}</div>'
        f'<div class="conf-hero-faixa">Confiança {_ROTULO_FAIXA[faixa]}</div>'
        "</div>"
        '<div><div class="conf-hero-texto">Média das seções, ponderada pelo '
        "quanto de cada uma foi efetivamente medido. O que não pôde ser medido "
        "não entra como zero — sai da média e continua nomeado no card da "
        "seção.</div>"
        f'<div class="conf-barra"><span style="width:{largura:.0f}%"></span></div>'
        f'<div class="conf-hero-chips">{chips}</div></div></section>'
    )


def _legenda_html() -> str:
    faixas = [
        ("Alta", f"≥ {FAIXA_ALTA:.0f}%",
         "A seção sustenta decisão nos limites que ela própria declara."),
        ("Media", f"{FAIXA_MEDIA:.0f}–{FAIXA_ALTA:.0f}%",
         "Serve para estudar; confira a evidência do componente mais baixo "
         "antes de agir."),
        ("Baixa", f"< {FAIXA_MEDIA:.0f}%", "Trate como exploratório."),
    ]
    itens = "".join(
        f'<div class="conf-legenda-item" style="--conf-tom:{_COR[f]}">'
        f'<div class="conf-legenda-faixa">{_ROTULO_FAIXA[f]} · {regua}</div>'
        f'<div class="conf-legenda-texto">{html.escape(texto)}</div></div>'
        for f, regua, texto in faixas
    )
    return (
        _faixa_secao(
            "Como ler",
            "A régua das três faixas e o que cada uma autoriza a fazer com o "
            "número.",
            "Apoio analítico",
            "var(--app-info, #4A9EFF)",
        )
        + f'<div class="conf-legenda">{itens}</div>'
    )


def render_corpo() -> None:
    """O conteúdo da tela, sem título nem legenda.

    Quem embute isto numa aba (``views/configuracoes.py``) já desenhou o
    próprio cabeçalho; repetir ``st.title`` dentro da aba duplicaria o título
    da página.
    """
    st.markdown(_CONF_CSS, unsafe_allow_html=True)
    snap = confianca_snapshot.carregar_ultimo()

    if snap is None:
        if st.button("🔄 Medir agora", key="confianca_recalcular", type="primary"):
            try:
                _medir_e_gravar()
            except confianca_snapshot.TabelaAusente as exc:
                # Falhar calado aqui seria o pior desfecho possível: o usuário
                # pagaria a medicão inteira e voltaria para a mesma tela vazia.
                st.error(str(exc))
                return
            st.rerun()
        st.info(
            "Nenhuma medição gravada ainda. Medir consulta o banco em todas as "
            "seções e leva alguns minutos, por isso não acontece sozinho ao "
            "abrir esta aba — clique em **Medir agora** quando quiser o número."
        )
        return

    secoes, rigor, medido_em = snap
    estado, acao = st.columns([1, 0.18], vertical_alignment="center")
    estado.markdown(_carimbo_html(medido_em, secoes), unsafe_allow_html=True)
    if acao.button("🔄 Recalcular", key="confianca_recalcular",
                   use_container_width=True,
                   help="Remede as sete seções e os três motores. Consulta o "
                        "banco e leva alguns minutos."):
        try:
            _medir_e_gravar()
        except confianca_snapshot.TabelaAusente as exc:
            st.error(str(exc))
            return
        st.rerun()

    st.markdown(_hero_html(confianca_global(secoes), secoes),
                unsafe_allow_html=True)
    st.markdown(
        _faixa_secao(
            "Confiança por seção",
            "Cada card abre os componentes que formam a nota e a evidência de "
            "cada um. O que não pôde ser medido aparece nomeado, nunca como "
            "zero.",
            f"{len(secoes)} seções",
            "var(--app-primary, #00C896)",
        ) + _cards_html(secoes),
        unsafe_allow_html=True,
    )

    rigor_html = _tabela_rigor_html(rigor)
    if rigor_html:
        st.markdown(rigor_html, unsafe_allow_html=True)
        st.caption(
            "Uma pergunta **não apurada** não conta como vencida nem como "
            "reprovada — ela sai da média e continua escrita. Foi o contrário "
            "disso que inflou a nota do motor de FIIs: enquanto ele declarava "
            "uma pergunta e os outros dois declaravam duas, medir menos rendia "
            "nota maior."
        )

    st.markdown(_legenda_html(), unsafe_allow_html=True)
    st.caption(
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
