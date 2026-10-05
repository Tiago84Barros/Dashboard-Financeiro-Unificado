"""Painéis que confrontam os ativos da carteira com o universo do banco.

O que aparece aqui é o mesmo que Empresas B3, Seleção de FIIs e Empresas
Americanas mostram — nota, cobertura e posição relativa —, aplicado apenas aos
ativos que o usuário tem. Ativo sem nota aparece como SEM NOTA, nunca como
nota mediana: "não apurado" e "mediano" são coisas diferentes, e confundi-las
já produziu aprovação confiante em cima de dado ausente neste projeto.
"""
from __future__ import annotations

from html import escape

import streamlit as st

from design.lacunas import aviso_lacuna, detalhe_tecnico

_BADGE_CORES = {
    "sucesso": "var(--app-primary)",
    "info": "var(--app-info)",
    "neutro": "var(--app-muted)",
    "alerta": "var(--app-warning)",
    "erro": "var(--app-danger)",
}

_STATUS_FII = {
    "validated": ("Validado", "sucesso"),
    "diligence_only": ("Só diligência", "alerta"),
}


def _cor(badge: str) -> str:
    return _BADGE_CORES.get(badge, _BADGE_CORES["neutro"])


def _pct(value, casas: int = 0) -> str:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "—"
    if value != value:
        return "—"
    return f"{value:.{casas}%}"


def _cobertura_txt(value) -> str:
    """Cobertura chega em pontos (B3/EUA) ou em fração (FII)."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "—"
    if value != value:
        return "—"
    return f"{value:.0f}%" if value > 1 else f"{value:.0%}"


def _linha_html(linha: dict, extras: list[str]) -> str:
    """Card CSS em UM único bloco — moldura e conteúdo nunca se separam."""
    score = linha.get("score")
    cor = _cor(linha.get("badge", "neutro"))
    nota = "—" if score is None else f"{float(score):.1f}"
    detalhe = " · ".join(escape(e) for e in extras if e)
    subtitulo = escape(str(linha.get("nome") or linha.get("setor") or
                           linha.get("tipo") or ""))
    return (
        f'<div style="background:var(--app-surface);border:1px solid var(--app-border);'
        f'border-left:3px solid {cor};border-radius:10px;padding:12px 14px;'
        f'margin-bottom:8px;">'
        f'<div style="display:flex;justify-content:space-between;'
        f'align-items:baseline;gap:12px;">'
        f'<div style="font-size:0.95rem;font-weight:800;color:var(--app-text);">'
        f'{escape(str(linha.get("ticker", "")))}'
        f'<span style="font-size:0.68rem;font-weight:600;color:var(--app-subtle);'
        f'margin-left:8px;">{subtitulo}</span></div>'
        f'<div style="font-size:1.15rem;font-weight:800;color:{cor};">{nota}'
        f'<span style="font-size:0.62rem;color:var(--app-subtle);font-weight:600;">/100</span>'
        f'</div></div>'
        f'<div style="font-size:0.70rem;color:var(--app-muted);margin-top:5px;'
        f'line-height:1.4;">{detalhe}</div>'
        f'</div>'
    )


def _rodape_ausentes(ausentes, explicacao: str, classe: str = "") -> None:
    """Quais ativos ficaram sem nota continua na tela (é o resultado por
    ativo); a causa técnica vai para o log de lacunas."""
    if not ausentes:
        return
    st.caption("Sem nota apurada: " + ", ".join(ausentes) + ".")
    aviso_lacuna("Sem nota apurada: " + ", ".join(ausentes) + ". " + explicacao,
                 codigo="tela.carteira.db_sem_nota", entidade=classe or None)


def render_db_analysis(classe: str, dados: dict) -> None:
    """Desenha o confronto com o universo do banco para uma classe."""
    if not dados:
        return
    titulo = {
        "acoes": "🏦 Estas ações contra o universo da B3",
        "fiis": "🏢 Estes FIIs contra os pares do mesmo tipo",
        "exterior": "🌎 Estas posições contra o universo de ações dos EUA",
    }.get(classe, "📊 Comparação com o universo do banco")
    st.markdown(f"##### {titulo}")

    if dados.get("erro"):
        st.warning("A comparação com os pares não está disponível no momento. "
                   "Os cards acima continuam válidos.")
        aviso_lacuna(str(dados["erro"]) + " Os cards acima continuam válidos; o que "
                     "falta é a comparação com os pares.",
                     codigo="tela.carteira.db_comparacao_indisponivel", entidade=classe)
        return

    linhas = dados.get("linhas") or []
    if not linhas and not dados.get("ausentes"):
        st.info("Nenhum ativo desta classe para comparar.")
        return

    for linha in linhas:
        extras: list[str] = []
        if linha.get("classificacao"):
            extras.append(str(linha["classificacao"]))
        if linha.get("percentil") is not None:
            extras.append(f"acima de {_pct(linha['percentil'])} do universo")
        ticker = str(linha.get("ticker") or "") or None
        if linha.get("cobertura") is not None:
            detalhe_tecnico(f"cobertura {_cobertura_txt(linha['cobertura'])}",
                            codigo="carteira.db_cobertura", entidade=ticker)
        if linha.get("confianca") is not None:
            detalhe_tecnico(f"confiança {_pct(linha['confianca'])}",
                            codigo="carteira.db_confianca", entidade=ticker)
        if linha.get("pares_tipo"):
            detalhe_tecnico(
                f"{linha['pares_tipo']} pares do tipo {linha.get('tipo') or '—'}",
                codigo="carteira.db_pares_do_tipo", entidade=ticker)
        status = linha.get("status_publicacao")
        if status:
            rotulo, _badge = _STATUS_FII.get(status, (status, "neutro"))
            extras.append(rotulo)
        if linha.get("faltantes"):
            aviso_lacuna("faltam críticas: " + ", ".join(linha["faltantes"][:4]),
                         codigo="tela.carteira.db_faltam_criticas", entidade=ticker)
        st.markdown(_linha_html(linha, extras), unsafe_allow_html=True)

    explicacao = {
        "acoes": "O ticker não tem linha em public.multiplos — sem corte "
                 "transversal não há nota, e ausência de nota não é nota média.",
        "fiis": "O fundo não está no snapshot de seleção de FIIs.",
        "exterior": "O módulo americano cobre AÇÕES: ETF, BDR e fundo de índice "
                    "não têm demonstração de companhia e por isso não são "
                    "pontuados. Não é falha de ingestão, é o escopo do módulo.",
    }.get(classe, "O ativo não está no universo consultado.")
    _rodape_ausentes(dados.get("ausentes"), explicacao, classe)

    if classe == "acoes" and not dados.get("crescimento_apurado", True):
        aviso_lacuna(
            "O histórico não veio nesta sessão: a trilha de crescimento ficou "
            "sem cobertura e a nota encolheu para o neutro — é perda de "
            "convicção, não penalidade.",
            codigo="tela.carteira.db_sem_historico_crescimento")
    elif classe == "fiis" and not dados.get("validacao_aplicavel", False):
        detalhe_tecnico("A metodologia de FIIs está sem validação point-in-time "
                        "aprovada nesta sessão: as notas servem para diligência, "
                        "não como recomendação publicada.",
                        codigo="carteira.db_fii_sem_validacao_pit")


def render_db_macro(dados: dict) -> None:
    """Conjuntura que precifica o título público — não há score de Tesouro."""
    if not dados:
        return
    st.markdown("##### 🏦 Conjuntura no banco")
    if dados.get("erro"):
        st.warning("A conjuntura não está disponível no momento.")
        aviso_lacuna(str(dados["erro"]), codigo="tela.carteira.db_macro_indisponivel")
        return
    atual = dados.get("atual") or {}
    campos = (("selic", "Selic"), ("ipca", "IPCA"),
              ("juros_real_ex_ante", "Juro real ex post"), ("cambio", "Câmbio"))
    for col, (chave, rotulo) in zip(st.columns(len(campos)), campos):
        valor = atual.get(chave)
        texto = "—" if valor is None else f"{float(valor):.2f}"
        with col:
            st.markdown(
                f'<div style="background:var(--app-surface);border:1px solid var(--app-border);'
                f'border-radius:10px;padding:14px 14px 12px;">'
                f'<div style="font-size:0.58rem;font-weight:800;'
                f'text-transform:uppercase;letter-spacing:0.12em;color:var(--app-subtle);'
                f'margin-bottom:6px;">{escape(rotulo)}</div>'
                f'<div style="font-size:1.35rem;font-weight:800;color:var(--app-text);">'
                f'{texto}</div>'
                f'<div style="font-size:0.68rem;color:var(--app-subtle);">'
                f'ano {escape(str(atual.get("ano", "—")))}</div>'
                f'</div>', unsafe_allow_html=True)
    anos = dados.get("anos") or []
    if len(anos) > 1:
        with st.expander("Série recente"):
            st.dataframe(
                [{"Ano": a["ano"], "Selic": a["selic"], "IPCA": a["ipca"],
                  "Juro real ex post": a["juros_real_ex_ante"],
                  "Câmbio": a["cambio"]} for a in anos],
                width="stretch", hide_index=True)
    st.caption(
        "Não existe nota de Tesouro Direto: o emissor é único e não há corte "
        "transversal para ranquear."
    )
    detalhe_tecnico(
        f"Fonte: {dados.get('fonte', 'public.macro')}. As unidades são as "
        "gravadas na tabela — confira a escala antes de comparar com a taxa "
        "contratada no seu extrato.",
        codigo="carteira.db_macro_fonte")


# ══════════════════════════════════════════════════════════════════════════════
# Carregamento cacheado. A chave é a tupla ORDENADA de tickers: o resultado não
# depende da ordem em que a carteira lista os ativos, e chavear pela ordem faria
# o mesmo conjunto ser recalculado a cada troca de aba.
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=3600, show_spinner=False)
def carregar_db(classe: str, chaves: tuple) -> dict:
    from core.portfolio_db_analysis import (
        analise_acoes_db,
        analise_exterior_db,
        analise_fiis_db,
    )

    fn = {"acoes": analise_acoes_db, "fiis": analise_fiis_db,
          "exterior": analise_exterior_db}.get(classe)
    return fn(chaves) if fn else {}


@st.cache_data(ttl=3600, show_spinner=False)
def carregar_macro() -> dict:
    from core.portfolio_db_analysis import analise_tesouro_db

    return analise_tesouro_db()


@st.cache_data(ttl=1800, show_spinner=False)
def carregar_documentos(classe: str, chaves: tuple) -> dict:
    """Evidência documental da classe (CVM/IPE ou notícias), cacheada.

    Meia hora de TTL: é leitura de acervo já publicado, e o dossiê pode ser
    gerado várias vezes seguidas enquanto o usuário refaz a pergunta.
    """
    from core.carteira_documentos import documentos_da_classe

    engine = None
    try:
        from core.database import get_engine

        engine = get_engine()
    except Exception:  # noqa: BLE001 - ausência vira erro declarado lá dentro
        engine = None
    return documentos_da_classe(classe, chaves, engine=engine)
