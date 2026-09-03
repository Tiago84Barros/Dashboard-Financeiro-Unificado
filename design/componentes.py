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


def rolar_para_topo() -> None:
    """Reposiciona a página no topo.

    O Streamlit preserva a posição de rolagem entre reruns e o ``st.chat_input``
    no fim das abas com IA recebe foco ao montar — o navegador então rola o
    rodapé para dentro da tela. Sem isto, "Análises" e "Cartão de Crédito"
    abrem no fim da página, longe do conteúdo que o usuário pediu.

    O reposicionamento é repetido por alguns frames de propósito: este iframe
    carrega antes do restante da aba, e uma única chamada seria desfeita pelo
    foco que chega depois.
    """
    components.html(
        """
        <script>
        (function () {
            const doc = window.parent && window.parent.document;
            if (!doc) { return; }
            function aoTopo() {
                const alvos = [
                    doc.querySelector('section.stMain'),
                    doc.querySelector('[data-testid="stMain"]'),
                    doc.querySelector('section.main'),
                    doc.scrollingElement,
                ];
                for (const alvo of alvos) {
                    if (alvo && typeof alvo.scrollTo === 'function') {
                        alvo.scrollTo({top: 0, behavior: 'auto'});
                    }
                }
            }
            aoTopo();
            requestAnimationFrame(aoTopo);
            [60, 160, 320, 600].forEach(function (ms) { setTimeout(aoTopo, ms); });
        })();
        </script>
        """,
        height=0,
    )


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
    cor_delta = "#00C896" if positivo is True else "#FC5C7D" if positivo is False else "#9CA3AF"
    accent = accent or ("#00C896" if positivo is True
                        else "#FC5C7D" if positivo is False else "#4A9EFF")
    delta_html = (
        f'<div class="app-kpi-delta" style="color:{cor_delta}">{_linha(str(delta))}</div>'
        if delta is not None and str(delta) != "" else ""
    )
    ajuda_attr = (f' title="{_linha(str(ajuda), aspas=True)}"'
                  if ajuda is not None and str(ajuda) != "" else "")
    st.markdown(
        f'<div class="app-kpi-card"{ajuda_attr} style="--app-kpi-accent:{accent}">'
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
    st.caption(f"Cobertura da recomendacao: {u.resumo()}.{nota}")


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
        mensagem_aviso("Vitrine fora do prazo de publicação", dados["texto"])
    elif dados.get("idade") is None or dados.get("idade", 0) < 0:
        st.caption(f"Frescor dos dados: {dados['texto']}")
    else:
        st.caption(f"Vitrine publicada em {dados['as_of']} "
                   f"(alvo de atualização: {dados['alvo']} dia(s)).")


def badge_status(texto: str, tipo: str = "info") -> None:
    """
    Badge colorido inline.
    tipo: 'sucesso' | 'alerta' | 'erro' | 'info' | 'neutro'
    """
    paleta = {
        "sucesso": ("#00C896", "rgba(0,200,150,0.12)"),
        "alerta":  ("#F6C90E", "rgba(246,201,14,0.12)"),
        "erro":    ("#FC5C7D", "rgba(252,92,125,0.12)"),
        "info":    ("#4A9EFF", "rgba(74,158,255,0.12)"),
        "neutro":  ("#9CA3AF", "rgba(156,163,175,0.12)"),
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
    cor_valor: str = "#F7FAFC",
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
            "sucesso": "#00C896", "alerta": "#F6C90E",
            "erro": "#FC5C7D", "info": "#4A9EFF", "neutro": "#9CA3AF",
        }
        c = paleta.get(tipo_badge, "#4A9EFF")
        badge_html = f'<span style="color:{c};font-size:0.75rem;font-weight:600;margin-left:8px">{badge}</span>'

    st.markdown(
        f"""<div style="display:flex;justify-content:space-between;
            align-items:center;padding:6px 0;border-bottom:1px solid #1E2533;">
            <span style="color:#9CA3AF;font-size:0.88rem">{label}</span>
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
            f'<span style="font-size:0.88rem;color:#CBD5E0">{label}</span>',
            unsafe_allow_html=True,
        )
    with col_pct:
        st.markdown(
            f'<span style="font-size:0.88rem;font-weight:600;'
            f'color:#00C896;float:right">{pct_display}</span>',
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
        "sucesso": "#00C896",
        "alerta":  "#F6C90E",
        "erro":    "#FC5C7D",
        "info":    "#4A9EFF",
    }
    paleta_fundo = {
        "sucesso": "rgba(0,200,150,0.06)",
        "alerta":  "rgba(246,201,14,0.06)",
        "erro":    "rgba(252,92,125,0.06)",
        "info":    "rgba(74,158,255,0.06)",
    }
    borda = paleta_borda.get(tipo, "#4A9EFF")
    fundo = paleta_fundo.get(tipo, "rgba(74,158,255,0.06)")
    modulo_html = (
        f'<div style="font-size:0.72rem;color:#4A5568;margin-top:4px">📁 {modulo}</div>'
        if modulo else ""
    )
    st.markdown(
        f"""<div style="
            background:{fundo};
            border-left:3px solid {borda};
            border-radius:0 8px 8px 0;
            padding:10px 14px;
            margin-bottom:8px;
        ">
            <div style="font-size:0.92rem;font-weight:600;color:#E2E8F0">
                {icone} {titulo}
            </div>
            <div style="font-size:0.80rem;color:#9CA3AF;margin-top:3px">
                {descricao}
            </div>
            {modulo_html}
        </div>""",
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
        "alta":  ("#FC5C7D", "Alta"),
        "media": ("#F6C90E", "Média"),
        "baixa": ("#4A9EFF", "Baixa"),
    }
    cor, label_urgencia = cores_urgencia.get(urgencia, cores_urgencia["media"])
    modulo_html = (
        f'<span style="color:#4A5568;font-size:0.72rem">→ {modulo}</span>'
        if modulo else ""
    )
    st.markdown(
        f"""<div style="
            display:flex;
            gap:14px;
            align-items:flex-start;
            padding:10px 14px;
            background:#1A1F2E;
            border:1px solid #2D3748;
            border-radius:10px;
            margin-bottom:8px;
        ">
            <div style="
                min-width:32px;height:32px;
                background:{cor};
                color:#0E1117;
                border-radius:50%;
                display:flex;align-items:center;justify-content:center;
                font-weight:800;font-size:0.9rem;
                flex-shrink:0;margin-top:2px;
            ">{numero}</div>
            <div>
                <div style="font-size:0.92rem;font-weight:600;color:#E2E8F0">
                    {titulo}
                    <span style="
                        font-size:0.68rem;font-weight:600;
                        color:{cor};margin-left:8px;
                        vertical-align:middle;
                    ">{label_urgencia}</span>
                </div>
                <div style="font-size:0.80rem;color:#9CA3AF;margin-top:3px">
                    {descricao}
                </div>
                {modulo_html}
            </div>
        </div>""",
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
        cor, classificacao = "#00C896", "Ótimo"
    elif score >= 60:
        cor, classificacao = "#4A9EFF", "Bom"
    elif score >= 40:
        cor, classificacao = "#F6C90E", "Atenção"
    else:
        cor, classificacao = "#FC5C7D", "Crítico"

    st.markdown(
        f"""<div style="
            text-align:center;
            background:#1A1F2E;
            border:1px solid #2D3748;
            border-radius:12px;
            padding:20px 16px;
        ">
            <div style="font-size:0.72rem;font-weight:600;text-transform:uppercase;
                        letter-spacing:0.08em;color:#718096;margin-bottom:8px">
                {label}
            </div>
            <div style="font-size:3rem;font-weight:800;color:{cor};line-height:1">
                {score}
            </div>
            <div style="font-size:0.85rem;font-weight:600;color:{cor};margin-top:4px">
                {classificacao}
            </div>
            <div style="
                background:#2D3748;border-radius:4px;height:6px;
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
