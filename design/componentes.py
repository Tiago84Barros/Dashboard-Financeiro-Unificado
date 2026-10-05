"""
design/componentes.py
Componentes reutilizáveis de interface para o Dashboard Financeiro Unificado.

Uso padrão em cada página:
    from design.componentes import container_pagina, card_metrica, estado_vazio
    def render():
        container_pagina("Título", "Subtítulo opcional")
        col1, col2 = st.columns(2)
        with col1:
            card_metrica("Saldo", "R$ 12.300,00", delta="+5,2%", positivo=True)
"""
from html import escape

import streamlit as st
import streamlit.components.v1 as components

from design.lacunas import aviso_lacuna, detalhe_tecnico


def _linha(texto: object, *, aspas: bool = False) -> str:
    """Escapa **e** achata o texto que vai para dentro de uma tag.

    ``escape`` já cobria ``<``, ``&`` e ``"``. Faltava o espaço em branco: o
    markdown do Streamlit fecha o bloco HTML na primeira linha em branco, e daí
    em diante a própria tag aparece na tela como texto. Uma mensagem de erro de
    banco em ``ajuda`` -- ela vem com quebras e um parágrafo vazio -- bastava
    para imprimir ``" style="--app-kpi-accent:...>`` no meio do card.

    Vale a mesma lição de ``str(delta)``: converter no componente, e não pedir
    que os chamadores lembrem de formatar.
    """
    return escape(" ".join(str(texto).split()), quote=aspas)

# ══════════════════════════════════════════════════════════════════
# Marca da sidebar
# ══════════════════════════════════════════════════════════════════

def _iniciais(nome: str) -> str:
    """Até duas iniciais para o avatar; e-mail vira a primeira letra."""
    partes = nome.split("@")[0].replace(".", " ").replace("_", " ").split()
    if not partes:
        return "?"
    if len(partes) == 1:
        return partes[0][:2].upper()
    return (partes[0][0] + partes[-1][0]).upper()


def marca_sidebar_html(usuario: str = "") -> str:
    """HTML do topo da sidebar: quem está logado e o card da marca.

    O nome vem da sessão (cadastro do próprio usuário), então passa por
    ``_linha`` como qualquer texto externo. Sem usuário -- modo sintético --
    o bloco de identidade simplesmente não aparece.
    """
    partes: list[str] = []
    nome = " ".join(str(usuario or "").split())
    if nome:
        partes.append(
            '<div class="app-user">'
            f'<div class="app-user-avatar" aria-hidden="true">{_linha(_iniciais(nome))}</div>'
            '<div class="app-user-text">'
            '<div class="app-user-label">Conectado como</div>'
            f'<div class="app-user-name" title="{_linha(nome, aspas=True)}">{_linha(nome)}</div>'
            '</div>'
            '<span class="app-user-status" aria-hidden="true"></span>'
            '</div>'
        )
    partes.append(
        '<div class="app-brand">'
        '<div class="app-brand-glow" aria-hidden="true"></div>'
        '<div class="app-brand-head">'
        '<div class="app-brand-mark" aria-hidden="true">📊</div>'
        '<span class="app-brand-badge">Unificado</span>'
        '</div>'
        '<div class="app-brand-title">Dashboard Financeiro</div>'
        '<div class="app-brand-subtitle">Visão unificada do seu caixa e dos investimentos</div>'
        '<div class="app-brand-tags">'
        '<span>Caixa</span><span>Carteira</span><span>Mercado</span>'
        '</div>'
        '</div>'
    )
    return "".join(partes)


# ══════════════════════════════════════════════════════════════════
# Estrutura de página
# ══════════════════════════════════════════════════════════════════

def container_pagina(
    titulo: str,
    subtitulo: str = "",
    icone: str = "",
    metadados: list[tuple[str, str]] | None = None,
    eyebrow: str = "Dashboard Financeiro",
) -> None:
    """
    Cabeçalho padrão de página com título, ícone e subtítulo opcionals.
    Deve ser a primeira chamada em cada render().
    """
    meta_html = "".join(
        '<span class="app-page-meta">'
        f'<small>{_linha(str(label))}</small>{_linha(str(valor))}'
        "</span>"
        for label, valor in (metadados or [])
        if valor not in (None, "")
    )
    icon_html = (
        f'<span class="app-page-icon" aria-hidden="true">{_linha(icone)}</span>'
        if icone else ""
    )
    subtitle_html = (
        f'<p class="app-page-subtitle">{_linha(subtitulo)}</p>'
        if subtitulo else ""
    )
    meta_group = (
        f'<div class="app-page-meta-group" aria-label="Contexto da página">{meta_html}</div>'
        if meta_html else ""
    )
    st.markdown(
        '<section class="app-page-hero">'
        '<div class="app-page-copy">'
        f'<div class="app-page-eyebrow">{_linha(eyebrow)}</div>'
        '<div class="app-page-title-row">'
        f'{icon_html}<h1>{_linha(titulo)}</h1>'
        "</div>"
        f"{subtitle_html}</div>{meta_group}</section>",
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════
# Sub-navegação de seções (abas)
# ══════════════════════════════════════════════════════════════════

# Prefixo obrigatório da key. O CSS em design/tema.py estiliza a
# sub-navegação por [class*="st-key-appnav_"]; sem o prefixo o widget
# renderiza com o visual cru do Streamlit.
NAV_KEY_PREFIX = "appnav_"


CLASSE_TRANSICAO = "app-em-transicao"
_ROTA_RENDERIZADA = "_app_rota_renderizada"
_SEQ_TRANSICAO = "_app_transicao_seq"

# Um script só para as duas metades do problema, medidas no DOM em 29/09/2026:
#
# 1. Fantasma. Entre a troca de rota e o fim do novo run, o Streamlit mantém
#    montados os elementos do run anterior com `data-stale="true"` -- 120 deles,
#    numa página de porte médio -- e eles continuam visíveis por baixo do título
#    da página nova. O tema claro ainda subia a opacidade de `.33` para `.88`,
#    quase opaco. A classe no `body` liga a regra do tema que os esconde; o vigia
#    a retira quando não sobra nenhum velho, e um teto garante que a tela nunca
#    fique presa escondida se algo der errado no meio.
# 2. Rolagem. O Streamlit preserva a rolagem entre reruns, e o `st.tabs` troca de
#    aba só no cliente (nenhum rerun): sem ouvinte de clique, a aba nova abre na
#    altura em que a anterior estava -- 2389 px, na medição. Daí o ouvinte em
#    capture, que alcança tanto a aba quanto o menu da barra lateral antes do
#    servidor responder. O reposicionamento é repetido por alguns frames de
#    propósito: este iframe carrega antes do restante da página, e o `chat_input`
#    do fim das abas com IA recebe foco ao montar, o que rolaria o rodapé de volta
#    para dentro da tela.
_JS_TRANSICAO = """
<script>
/* marca __MARCA__ -- muda a cada run para o iframe recarregar e o script rodar */
(function () {
    const janela = window.parent;
    const doc = janela && janela.document;
    if (!doc || !doc.body) { return; }
    const CLASSE = '__CLASSE__';
    const MIN_MS = 900;      /* piso antes de soltar, para o stale chegar */
    const TETO_MS = 25000;   /* teto de seguranca: a tela nunca fica presa */
    const PASSO_MS = 100;
    const est = janela.__app4Transicao || (janela.__app4Transicao = {});

    function principal() {
        return doc.querySelector('section.stMain')
            || doc.querySelector('[data-testid="stMain"]')
            || doc.querySelector('section.main');
    }
    function aoTopo() {
        const alvos = [principal(), doc.scrollingElement, doc.documentElement];
        for (const alvo of alvos) {
            if (alvo && typeof alvo.scrollTo === 'function') {
                alvo.scrollTo({top: 0, behavior: 'auto'});
            }
        }
    }
    function insistirNoTopo() {
        aoTopo();
        requestAnimationFrame(aoTopo);
        [60, 160, 320, 600].forEach(function (ms) { janela.setTimeout(aoTopo, ms); });
    }
    /* Os temporizadores moram na janela de cima porque este iframe e' recarregado
       a cada run: um `setInterval` daqui morreria com ele no meio da transicao. */
    function vigiar() {
        if (est.vigia) { janela.clearInterval(est.vigia); }
        est.vigia = janela.setInterval(function () {
            if (!doc.body.classList.contains(CLASSE)) {
                janela.clearInterval(est.vigia); est.vigia = null; return;
            }
            const area = principal();
            const velhos = area ? area.querySelectorAll('[data-stale="true"]').length : 0;
            if (velhos > 0) { est.viuVelhos = true; }
            const idade = Date.now() - (est.inicio || 0);
            const acabou = velhos === 0 && (est.viuVelhos || idade > MIN_MS);
            if (acabou || idade > TETO_MS) {
                doc.body.classList.remove(CLASSE);
                janela.clearInterval(est.vigia); est.vigia = null;
                aoTopo();
                janela.setTimeout(aoTopo, 80);
            }
        }, PASSO_MS);
    }
    function ligar() {
        est.inicio = Date.now();
        est.viuVelhos = false;
        doc.body.classList.add(CLASSE);
        insistirNoTopo();
        vigiar();
    }
    function aoClicar(ev) {
        /* Nao `instanceof Element`: o no' clicado vem do documento de cima e
           `Element` aqui e' o construtor DESTE iframe -- entre realms o teste da'
           falso e o ouvinte saia' sem fazer nada. Medido em 29/09/2026: o clique
           na aba nao rolava a pagina por causa disso. Vale o que o no' sabe
           fazer, nao de que realm ele veio. */
        const alvo = ev && ev.target && typeof ev.target.closest === 'function'
            ? ev.target : null;
        if (!alvo) { return; }
        const secao = alvo.closest('[data-testid="stSidebar"] [role="radiogroup"] label,'
            + ' [data-testid="stSidebarNav"] a');
        if (secao) { ligar(); return; }
        /* Aba: o corpo das duas ja' esta' no DOM, nao ha' fantasma a esconder --
           so' falta abrir no topo. */
        if (alvo.closest('[role="tab"], [data-baseweb="tab"]')) { insistirNoTopo(); }
    }
    if (est.ouvinte) { doc.removeEventListener('click', est.ouvinte, true); }
    est.ouvinte = aoClicar;
    doc.addEventListener('click', aoClicar, true);

    if (__ATIVA__) {
        ligar();
    } else if (doc.body.classList.contains(CLASSE)) {
        /* Transicao aberta por um run anterior: o vigia dela morreu com o iframe
           daquele run, e sem reassumir aqui a tela ficaria escondida. */
        est.inicio = est.inicio || Date.now();
        vigiar();
    }
})();
</script>
"""


def _injetar_transicao(*, ativa: bool, marca: str) -> None:
    html = (
        _JS_TRANSICAO
        .replace("__CLASSE__", CLASSE_TRANSICAO)
        .replace("__ATIVA__", "true" if ativa else "false")
        .replace("__MARCA__", marca)
    )
    components.html(html, height=0)


def transicao_de_pagina(rota: str) -> None:
    """Instala a transição de página: sem fantasma e sempre no topo.

    Chamar uma vez por rerun, **sem condição**, antes de renderizar a rota. Sem
    condição de propósito: um elemento que só existe em alguns runs desloca os
    `st.tabs` das views, e o Streamlit devolve a seleção para a primeira aba
    quando o grupo de abas muda de posição (medido em 24/09/2026).

    O que o script faz depende de ``rota``: quando ela difere da última
    renderizada, a transição é ligada aqui mesmo -- é o que cobre troca de seção
    por qualquer caminho, inclusive sem clique. O ouvinte de clique, esse é
    instalado sempre.
    """
    sessao = getattr(st, "session_state", None)
    if sessao is None:  # dublê de testes sem session_state
        _injetar_transicao(ativa=True, marca=str(rota))
        return
    trocou = sessao.get(_ROTA_RENDERIZADA) != rota
    sessao[_ROTA_RENDERIZADA] = rota
    seq = int(sessao.get(_SEQ_TRANSICAO, 0)) + 1
    sessao[_SEQ_TRANSICAO] = seq
    _injetar_transicao(ativa=trocou, marca=f"{seq}")


def rolar_para_topo() -> None:
    """Reposiciona a página no topo e esconde o render anterior.

    Usada por quem tem sub-navegação própria (``abas_secao``, Seleção de FIIs,
    Empresas): a troca de aba dispara rerun, e no rerun valem as duas metades do
    defeito -- a aba nova nascia na altura da anterior e o conteúdo da anterior
    seguia na tela até o novo terminar de carregar. É a mesma transição de
    ``transicao_de_pagina``, sempre ligada.
    """
    _injetar_transicao(ativa=True, marca="topo")


def abas_secao(
    opcoes: list[str],
    *,
    key: str,
    default: str | None = None,
    rolar_ao_trocar: bool = True,
    label: str = "Seção",
) -> str:
    """
    Sub-navegação padrão do app, com a mesma aparência das abas nativas
    (``st.tabs``) usadas em Investimentos.

    Por que não ``st.tabs`` direto: ele não expõe ``key`` nem ``on_change``.
    Sem ``key``, qualquer widget interno que dispare rerun (um filtro numa
    tabela, por exemplo) devolve o usuário à primeira aba; sem ``on_change``,
    não há como reposicionar a página no topo ao trocar de seção. O
    ``segmented_control`` tem os dois e recebe o visual de aba via CSS.

    Args:
        opcoes:          Rótulos das seções, na ordem de exibição.
        key:             Sufixo da chave em session_state (o prefixo é fixo).
        default:         Seção inicial; ``opcoes[0]`` quando omitido.
        rolar_ao_trocar: Rola para o topo ao mudar de seção.
        label:           Rótulo acessível (visualmente colapsado).

    Returns:
        O rótulo da seção ativa — sempre um item de ``opcoes``.
    """
    if not opcoes:
        raise ValueError("abas_secao exige pelo menos uma opção.")

    widget_key = f"{NAV_KEY_PREFIX}{key}"
    flag_key = f"_{widget_key}_rolar"

    def _marcar_troca() -> None:
        st.session_state[flag_key] = True

    # Seção que deixa de existir (aba retirada entre um deploy e outro) fica
    # guardada em session_state e o widget seria criado com um valor fora das
    # opções. Descartar aqui devolve o usuário ao default em vez de quebrar a
    # página de quem estava com a aba antiga aberta.
    if st.session_state.get(widget_key) not in (None, *opcoes):
        del st.session_state[widget_key]

    escolhida = st.segmented_control(
        label,
        opcoes,
        key=widget_key,
        default=default or opcoes[0],
        label_visibility="collapsed",
        on_change=_marcar_troca if rolar_ao_trocar else None,
    ) or (default or opcoes[0])

    # pop e não get: a rolagem vale para o rerun da troca, não para os
    # seguintes — senão qualquer interação dentro da aba jogaria o usuário
    # de volta ao topo.
    if st.session_state.pop(flag_key, False):
        rolar_para_topo()

    return escolhida


def secao_titulo(titulo: str, icone: str = "", subtitulo: str = "") -> None:
    """Cabeçalho de seção dentro de uma página."""
    icon_html = (
        f'<span class="app-section-icon" aria-hidden="true">{_linha(icone)}</span>'
        if icone else ""
    )
    subtitle_html = (
        f'<div class="app-section-subtitle">{_linha(subtitulo)}</div>'
        if subtitulo else ""
    )
    st.markdown(
        '<div class="app-section-heading">'
        f'{icon_html}<div><div class="app-section-title">{_linha(titulo)}</div>'
        f"{subtitle_html}</div></div>",
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════
# Indicadores / KPIs
# ══════════════════════════════════════════════════════════════════

# O Plotly não resolve `var(--…)`, então as telas guardam as cores semânticas
# como literais. Ao virarem HTML elas passam por aqui e acompanham o tema.
_TOKEN_POR_COR = {
    "#00C896": "var(--app-primary)",
    "#4A9EFF": "var(--app-info)",
    "#FC5C7D": "var(--app-danger)",
    "#F6C90E": "var(--app-warning)",
    "#F97316": "var(--app-alert)",
    "#9CA3AF": "var(--app-muted)",
    "#4A5568": "var(--app-subtle)",
    "#E2E8F0": "var(--app-text)",
}


def cor_token(cor: str) -> str:
    """Traduz a cor semântica literal para o token equivalente do tema."""
    return _TOKEN_POR_COR.get(str(cor).upper(), cor)


def card_metrica(
    titulo: object,
    valor: object,
    delta: object | None = None,
    positivo: bool | None = None,
    ajuda: object | None = None,
    accent: str | None = None,
) -> None:
    """
    Card de KPI em CSS (não usa st.metric) — visual coeso com o restante do app.

    Args:
        titulo:   Rótulo do indicador (ex: "Patrimônio Total")
        valor:    Valor do indicador. Aceita número e converte — ver nota abaixo.
        delta:    Variação (ex: "+5,2%") ou None para omitir
        positivo: True → verde, False → vermelho, None → neutro (cor do delta)
        ajuda:    Tooltip de ajuda (via atributo title do card)
        accent:   Cor da borda-esquerda; default deriva de `positivo` (neutro=azul)

    Os parâmetros de texto são tipados como ``object`` e convertidos aqui de
    propósito. A assinatura anterior pedia ``str`` e a maioria das chamadas
    respeitava, mas 19 delas em quatro telas passam ``int(...)`` ou ``len(...)``
    — contagem é o caso natural de um KPI. Enquanto a renderização era f-string
    isso funcionava por acidente; ao passar a escapar HTML, ``html.escape``
    chamou ``.replace`` num inteiro e derrubou a aba inteira em produção
    (31/07/2026, "Empresas B3": *'int' object has no attribute 'replace'*).

    Converter no componente, e não pedir que 19 chamadores lembrem de formatar,
    é o que impede a falha de voltar pela vigésima chamada.
    """
    cor_delta = "var(--app-primary)" if positivo is True else "var(--app-danger)" if positivo is False else "var(--app-muted)"
    accent = accent or ("var(--app-primary)" if positivo is True
                        else "var(--app-danger)" if positivo is False else "var(--app-info)")
    delta_html = (
        f'<div class="app-kpi-delta" style="color:{cor_token(cor_delta)}">{_linha(str(delta))}</div>'
        if delta is not None and str(delta) != "" else ""
    )
    ajuda_attr = (f' title="{_linha(str(ajuda), aspas=True)}"'
                  if ajuda is not None and str(ajuda) != "" else "")
    st.markdown(
        f'<div class="app-kpi-card"{ajuda_attr} style="--app-kpi-accent:{cor_token(accent)}">'
        f'<div class="app-kpi-label">{_linha(str(titulo))}</div>'
        f'<div class="app-kpi-value">{_linha(str(valor))}</div>'
        f'{delta_html}</div>',
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════
# Badges e status
# ══════════════════════════════════════════════════════════════════

# Os três motores de score (B3, FIIs, Empresas Americanas) usam o mesmo cartão,
# a mesma faixa 0–100 e o mesmo vocabulário de badge, mas são metodologias
# independentes com rigor diferente: cada nota é um percentil DENTRO do próprio
# universo comparável. Um 72 na B3 e um 72 em FIIs não dizem a mesma coisa, e a
# casca visual comum sugere o contrário. Ver `docs/objetivo_analista_profissional.md`.
AVISO_ESCALA_NAO_COMPARAVEL = (
    "Nota relativa ao universo comparável desta aba, em escala própria. "
    "Não é comparável às notas de outras abas (B3, FIIs e Empresas Americanas "
    "usam metodologias independentes)."
)


def aviso_escala_do_score() -> None:
    """Declara que a nota não vale fora da aba onde foi calculada."""
    st.caption(AVISO_ESCALA_NAO_COMPARAVEL)


# ── A-154: cobertura declarada na tela onde a recomendacao aparece ──────────
# `core.universo_decisao` ja media as tres populacoes (nominal, investivel,
# apto) e o preco pago pelo descarte. Como em A-152, o unico consumidor era o
# relatorio de confianca, que o usuario nao le: quem via o ranking dos EUA nao
# tinha como saber que ele fala por 874 dos 2.831 ativos negociaveis. Filtro
# silencioso e pior que filtro nenhum -- o ativo some da tela e o usuario nao
# sabe que sumiu.

_UNIVERSO_FN = {
    "b3": "universo_b3",
    "fii": "universo_fii",
    "us": "universo_us",
}


@st.cache_data(ttl=900, show_spinner=False)
def _universo_cacheado(modulo: str):
    """Cache curto: `universo_b3` recalcula o indice de confianca inteiro, e
    isso nao pode rodar a cada interacao de widget da aba."""
    import core.universo_decisao as ud
    return getattr(ud, _UNIVERSO_FN[modulo])()


def aviso_cobertura_do_universo(modulo: str) -> None:
    """Declara por quantos ativos a nota desta aba fala, e o que ficou de fora.

    Falha em silencio de proposito: cobertura e contexto da recomendacao, nao a
    recomendacao. Fonte fora do ar nao pode derrubar a tela que o usuario abriu
    para ver o ranking.
    """
    try:
        u = _universo_cacheado(modulo)
    except Exception:  # noqa: BLE001 - contexto nao derruba a tela
        return
    if not u.investivel:
        return
    # `.capitalize()` minusculiza o RESTO: "DY", "P/VP" e "B3" viravam
    # "dy", "p/vp" e "b3" no meio da nota do gate.
    nota = f" {u.notas[0][:1].upper()}{u.notas[0][1:]}." if u.notas else ""
    detalhe_tecnico(f"Cobertura da recomendacao: {u.resumo()}.{nota}",
                    codigo=f"componentes.cobertura_universo.{modulo}")


# ── Selo de frescor: de quando e o dado que esta tela esta mostrando ────────
# A tela de FIIs declarava a idade da vitrine desde o PR #190; EUA e B3 nao
# declaravam nada. As tres leem vitrine publicada a partir do armazem local e as
# tres podem estar lendo dado de semanas atras -- um ranking sobre preco velho
# tem a mesma aparencia de um sobre preco de ontem.


@st.cache_data(ttl=300, show_spinner=False)
def _carimbo_cacheado(modulo: str):
    """Cache curto: o carimbo da B3 sai de `market_health_summary`, que varre a
    tabela de metricas, e isso nao pode rodar a cada interacao de widget."""
    from core.frescor import carimbo_do_modulo
    return carimbo_do_modulo(modulo)


def frescor_da_vitrine(modulo: str) -> dict | None:
    """Selo do modulo, ou ``None`` se nao deu para medir.

    Falha em silencio de proposito, como `aviso_cobertura_do_universo`: frescor
    e contexto do ranking, nao o ranking. Banco fora do ar nao pode derrubar a
    tela que o usuario abriu para ver a recomendacao.
    """
    try:
        from core.frescor import selo
        return selo(modulo, _carimbo_cacheado(modulo))
    except Exception:  # noqa: BLE001 - contexto nao derruba a tela
        return None


def selo_de_frescor(modulo: str, dados: dict | None = None) -> None:
    """Declara a idade da vitrine: discreto no prazo, alto quando vence.

    A gradacao e o ponto. Um card de alerta em todo carregamento treina a pessoa
    a nao ler o card -- e ai o aviso que importa some junto com os outros.
    """
    dados = dados if dados is not None else frescor_da_vitrine(modulo)
    if not dados:
        return
    if dados.get("vencida"):
        aviso_lacuna(f"Vitrine fora do prazo de publicação: {dados['texto']}",
                     codigo=f"tela.componentes.vitrine_vencida.{modulo}")
    elif dados.get("idade") is None or dados.get("idade", 0) < 0:
        detalhe_tecnico(f"Frescor dos dados: {dados['texto']}",
                        codigo=f"componentes.frescor_vitrine.{modulo}")
    else:
        detalhe_tecnico(
            f"Vitrine publicada em {dados['as_of']} "
            f"(alvo de atualização: {dados['alvo']} dia(s)).",
            codigo=f"componentes.frescor_vitrine.{modulo}")


# ── Atualidade trimestral da B3 (B3-02) ─────────────────────────────────────
# O selo acima responde "quando a vitrine foi publicada". Não responde "de
# qual trimestre é o dado": em 28/09/2026 a vitrine foi publicada no prazo
# carregando o TTM do 2026T1, com o 2026T2 já no banco.


@st.cache_data(ttl=3600, show_spinner=False)
def _atualidade_b3_cacheada(hoje_iso: str) -> dict | None:
    """Cache de uma hora, chaveado pelo dia: a medição lê cerca de 200 kB do
    Supabase (ver `core.b3_atualidade_trimestral.medir_banco`), e o egress está
    acima da cota desde setembro."""
    from datetime import date

    from core.b3_atualidade_trimestral import medir_banco
    from core.database import get_engine
    eng = get_engine()
    if eng is None:
        return None
    with eng.connect() as conn:
        return medir_banco(conn, date.fromisoformat(hoje_iso))


def aviso_atualidade_trimestral_b3() -> None:
    """Avisa quando a base da B3 está um trimestre (ou mais) atrás.

    Alto quando há atraso; discreto quando está em dia. Falha em silêncio pelo
    mesmo motivo do selo de frescor: é contexto do ranking, não o ranking.
    """
    try:
        from datetime import date

        from core.b3_atualidade_trimestral import texto_do_score
        medida = _atualidade_b3_cacheada(date.today().isoformat())
    except Exception:  # noqa: BLE001 - contexto nao derruba a tela
        return
    if not medida:
        return
    universo = medida.get("universo") or {}
    aviso_score = texto_do_score(medida.get("score") or {})
    if universo.get("atrasada") or aviso_score:
        partes = [universo.get("texto")] if universo.get("atrasada") else []
        if aviso_score:
            partes.append(aviso_score)
        aviso_lacuna("Fundamentos com trimestre defasado: " + " ".join(partes),
                     codigo="tela.componentes.b3_trimestre_defasado")
    elif universo.get("texto"):
        detalhe_tecnico(str(universo["texto"]),
                        codigo="componentes.b3_atualidade_trimestral")


def _tinta(token: str, pct: int = 12) -> str:
    """Fundo derivado do proprio token, para acompanhar a troca de tema."""
    return f"color-mix(in srgb, var({token}) {pct}%, transparent)"


def badge_status(texto: str, tipo: str = "info") -> None:
    """
    Badge colorido inline.
    tipo: 'sucesso' | 'alerta' | 'erro' | 'info' | 'neutro'
    """
    paleta = {
        "sucesso": ("var(--app-primary)", "rgba(0,200,150,0.12)"),
        "alerta":  ("var(--app-warning)", "rgba(246,201,14,0.12)"),
        "erro":    ("var(--app-danger)", "rgba(252,92,125,0.12)"),
        "info":    ("var(--app-info)", "rgba(74,158,255,0.12)"),
        "neutro":  ("var(--app-muted)", "rgba(156,163,175,0.12)"),
    }
    cor_texto, cor_fundo = paleta.get(tipo, paleta["info"])
    st.markdown(
        f'<span class="app-status-badge" style="--badge-color:{cor_texto};'
        f'--badge-bg:{cor_fundo}">{_linha(texto)}</span>',
        unsafe_allow_html=True,
    )


def indicador_linha(
    label: str,
    valor: str,
    cor_valor: str = "var(--app-text)",
    badge: str | None = None,
    tipo_badge: str = "info",
) -> None:
    """
    Linha de indicador: 'Label ........... Valor  [badge]'
    Útil para listas de resumo dentro de cards.
    """
    badge_html = ""
    if badge:
        paleta = {
            "sucesso": "var(--app-primary)", "alerta": "var(--app-warning)",
            "erro": "var(--app-danger)", "info": "var(--app-info)", "neutro": "var(--app-muted)",
        }
        c = paleta.get(tipo_badge, "var(--app-info)")
        badge_html = f'<span style="color:{c};font-size:0.75rem;font-weight:600;margin-left:8px">{badge}</span>'

    st.markdown(
        f"""<div style="display:flex;justify-content:space-between;
            align-items:center;padding:6px 0;border-bottom:1px solid var(--app-border);">
            <span style="color:var(--app-muted);font-size:0.88rem">{label}</span>
            <span style="color:{cor_valor};font-weight:600;font-size:0.92rem">
                {valor}{badge_html}
            </span>
        </div>""",
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════
# Estados de feedback
# ══════════════════════════════════════════════════════════════════

def estado_vazio(mensagem: str = "Nenhum dado disponível.", icone: str = "📭") -> None:
    """Estado vazio para seções sem dados."""
    st.markdown(
        f"""<div class="empty-state">
            <div class="empty-icon">{icone}</div>
            <div class="empty-text">{mensagem}</div>
        </div>""",
        unsafe_allow_html=True,
    )


def mensagem_erro(titulo: str, detalhe: str = "") -> None:
    """Mensagem de erro formatada."""
    corpo = f"**{titulo}**"
    if detalhe:
        corpo += f"\n\n{detalhe}"
    st.error(corpo)


def mensagem_aviso(titulo: str, detalhe: str = "") -> None:
    """Mensagem de aviso/atenção formatada."""
    corpo = f"**{titulo}**"
    if detalhe:
        corpo += f"\n\n{detalhe}"
    st.warning(corpo)


def em_construcao(fase: str, descricao: str = "") -> None:
    """
    Placeholder padrão para módulos ainda não implementados.
    Substitui o st.info() genérico dos stubs.
    """
    st.info(
        f"**Módulo em construção** — aguarda {fase}."
        + (f"\n\n{descricao}" if descricao else ""),
        icon="🔧",
    )


# ══════════════════════════════════════════════════════════════════
# Barra de progresso com rótulos
# ══════════════════════════════════════════════════════════════════

def barra_progresso(
    label: str,
    valor_atual: float,
    valor_total: float,
    fmt_valor: str | None = None,
    fmt_total: str | None = None,
) -> None:
    """
    Barra de progresso com label, valores e percentual.

    Args:
        label:       Nome do item (ex: "Reserva de Emergência")
        valor_atual: Valor acumulado
        valor_total: Meta/total
        fmt_valor:   String formatada do valor atual (ou None para exibir raw)
        fmt_total:   String formatada do valor total
    """
    pct = min(valor_atual / valor_total, 1.0) if valor_total > 0 else 0
    pct_display = f"{pct * 100:.1f}%".replace(".", ",")

    v_str = fmt_valor or f"{valor_atual:.2f}".replace(".", ",")
    t_str = fmt_total or f"{valor_total:.2f}".replace(".", ",")

    col_label, col_pct = st.columns([3, 1])
    with col_label:
        st.markdown(
            f'<span style="font-size:0.88rem;color:var(--app-muted)">{label}</span>',
            unsafe_allow_html=True,
        )
    with col_pct:
        st.markdown(
            f'<span style="font-size:0.88rem;font-weight:600;'
            f'color:var(--app-primary);float:right">{pct_display}</span>',
            unsafe_allow_html=True,
        )

    st.progress(pct)
    st.caption(f"{v_str} de {t_str}")


# ══════════════════════════════════════════════════════════════════
# Alertas compactos (para dashboard — versão resumida)
# ══════════════════════════════════════════════════════════════════

def card_alerta_resumo(
    tipo: str,
    icone: str,
    titulo: str,
    descricao: str,
    modulo: str = "",
) -> None:
    """
    Card compacto de alerta para o Dashboard Geral.
    Diferente do card completo de pages/alertas.py — mais denso e sem ação.

    tipo: 'sucesso' | 'alerta' | 'erro' | 'info'
    """
    paleta_borda = {
        "sucesso": "var(--app-primary)",
        "alerta":  "var(--app-warning)",
        "erro":    "var(--app-danger)",
        "info":    "var(--app-info)",
    }
    paleta_fundo = {
        "sucesso": "rgba(0,200,150,0.06)",
        "alerta":  "rgba(246,201,14,0.06)",
        "erro":    "rgba(252,92,125,0.06)",
        "info":    "rgba(74,158,255,0.06)",
    }
    borda = paleta_borda.get(tipo, "var(--app-info)")
    fundo = paleta_fundo.get(tipo, "rgba(74,158,255,0.06)")
    modulo_html = (
        f'<div style="font-size:0.72rem;color:var(--app-subtle);margin-top:4px">📁 {modulo}</div>'
        if modulo else ""
    )
    # Marcação numa linha só, sem recuo: com `modulo` vazio a linha de
    # `{modulo_html}` virava linha em branco, o Markdown fechava ali o bloco de
    # HTML e o `</div>` seguinte, recuado, saía na tela como bloco de código.
    st.markdown(
        f'<div style="background:{fundo};border-left:3px solid {borda};'
        f'border-radius:0 8px 8px 0;padding:10px 14px;margin-bottom:8px;">'
        f'<div style="font-size:0.92rem;font-weight:600;color:var(--app-text)">'
        f'{icone} {titulo}</div>'
        f'<div style="font-size:0.80rem;color:var(--app-muted);margin-top:3px">'
        f'{descricao}</div>'
        f'{modulo_html}</div>',
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════
# Próximos passos (ações recomendadas numeradas)
# ══════════════════════════════════════════════════════════════════

def card_proximo_passo(
    numero: int,
    titulo: str,
    descricao: str,
    urgencia: str = "media",
    modulo: str = "",
) -> None:
    """
    Card de próximo passo financeiro com número, urgência e módulo-destino.

    urgencia: 'alta' | 'media' | 'baixa'
    """
    cores_urgencia = {
        "alta":  ("var(--app-danger)", "Alta"),
        "media": ("var(--app-warning)", "Média"),
        "baixa": ("var(--app-info)", "Baixa"),
    }
    cor, label_urgencia = cores_urgencia.get(urgencia, cores_urgencia["media"])
    modulo_html = (
        f'<span style="color:var(--app-subtle);font-size:0.72rem">→ {modulo}</span>'
        if modulo else ""
    )
    st.markdown(
        # Uma linha só, sem recuo: ver a nota em `card_alerta_resumo`.
        f'<div style="display:flex;gap:14px;align-items:flex-start;'
        f'padding:10px 14px;background:var(--app-surface);'
        f'border:1px solid var(--app-border);border-radius:10px;'
        f'margin-bottom:8px;">'
        f'<div style="min-width:32px;height:32px;background:{cor};'
        f'color:var(--app-on-accent);border-radius:50%;display:flex;'
        f'align-items:center;justify-content:center;font-weight:800;'
        f'font-size:0.9rem;flex-shrink:0;margin-top:2px;">{numero}</div>'
        f'<div><div style="font-size:0.92rem;font-weight:600;'
        f'color:var(--app-text)">{titulo}'
        f'<span style="font-size:0.68rem;font-weight:600;color:{cor};'
        f'margin-left:8px;vertical-align:middle;">{label_urgencia}</span></div>'
        f'<div style="font-size:0.80rem;color:var(--app-muted);margin-top:3px">'
        f'{descricao}</div>'
        f'{modulo_html}</div></div>',
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════════
# Score de saúde financeira
# ══════════════════════════════════════════════════════════════════

def score_saude(score: int, label: str = "Saúde Financeira") -> None:
    """
    Exibe o score de saúde financeira (0–100) com cor e classificação.

    0–39  → Crítico  (vermelho)
    40–59 → Atenção  (amarelo)
    60–79 → Bom      (azul)
    80–100→ Ótimo    (verde)
    """
    if score >= 80:
        cor, classificacao = "var(--app-primary)", "Ótimo"
    elif score >= 60:
        cor, classificacao = "var(--app-info)", "Bom"
    elif score >= 40:
        cor, classificacao = "var(--app-warning)", "Atenção"
    else:
        cor, classificacao = "var(--app-danger)", "Crítico"

    st.markdown(
        f"""<div style="
            text-align:center;
            background:var(--app-surface);
            border:1px solid var(--app-border);
            border-radius:12px;
            padding:20px 16px;
        ">
            <div style="font-size:0.72rem;font-weight:600;text-transform:uppercase;
                        letter-spacing:0.08em;color:var(--app-subtle);margin-bottom:8px">
                {label}
            </div>
            <div style="font-size:3rem;font-weight:800;color:{cor};line-height:1">
                {score}
            </div>
            <div style="font-size:0.85rem;font-weight:600;color:{cor};margin-top:4px">
                {classificacao}
            </div>
            <div style="
                background:var(--app-border);border-radius:4px;height:6px;
                margin-top:12px;overflow:hidden;
            ">
                <div style="
                    background:{cor};width:{score}%;height:100%;
                    border-radius:4px;transition:width 0.5s;
                "></div>
            </div>
        </div>""",
        unsafe_allow_html=True,
    )
