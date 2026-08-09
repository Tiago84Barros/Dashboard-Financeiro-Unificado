"""
views/portfolio_global.py — Portfolio Global

Reune as carteiras-modelo das tres classes num unico patrimonio e mostra
composicao, concentracao e metricas agregadas. Le exclusivamente os snapshots
persistidos; nao recalcula nada contra market.*.

A logica de decisao fica em funcoes puras (estado_vazio, carregar_snapshots)
para poder ser testada sem Streamlit. Coberto por
tests/test_portfolio_global_view.py.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy.exc import OperationalError, ProgrammingError

from core.global_portfolio import concentration, metrics
from core.global_portfolio.aggregate import classes_sem_posicao, montar_posicoes
from core.global_portfolio.taxonomy import ROTULOS, nao_mapeados
from core.portfolio.registry import asset_classes, get_spec
from core.portfolio.repository import (
    load_active_snapshots,
    load_allocation_targets,
    save_allocation_targets,
)
from design.componentes import card_metrica

MSG_SEM_SNAPSHOT = (
    "Nenhum snapshot encontrado. Rode o schema 049 no Supabase e depois "
    "`python -m scripts.backfill_portfolio_snapshots --apply`."
)
MSG_SEM_ALVO = "Defina a alocação-alvo por classe para consolidar o patrimônio."


def carregar_snapshots(*, engine=None, owner_id=None) -> dict[str, dict[str, dict]]:
    """Snapshots do modelo ativo de cada classe registrada."""
    return {
        classe: load_active_snapshots(classe, engine=engine, owner_id=owner_id)
        for classe in asset_classes()
    }


def estado_vazio(snapshots: dict, alvos: dict) -> str | None:
    """Mensagem a exibir quando nao ha o que consolidar, ou None."""
    if not any(snapshots.get(c) for c in snapshots):
        return MSG_SEM_SNAPSHOT
    if not alvos:
        return MSG_SEM_ALVO
    return None


_MARCAS_TABELA_AUSENTE = ("does not exist", "no such table")


def mensagem_de_erro_ao_carregar(exc: Exception) -> str:
    """Traduz uma excecao ao carregar snapshots/alocacao para o texto a exibir.

    O schema 049 (tabelas de snapshot e de alocacao-alvo) pode ainda nao ter
    sido aplicado no Supabase: nesse caso a consulta cai numa tabela
    inexistente, o que e o primeiro-uso esperado, nao um erro de verdade.
    Postgres levanta ProgrammingError para relacao ausente; SQLite levanta
    OperationalError. Checar os dois tipos cobre os bancos usados pelo
    projeto; a checagem por substring do texto complementa (para outros
    drivers), mas nao substitui, porque grepar texto de excecao arbitraria e
    fragil. Qualquer outra excecao mantem a mensagem crua — uma falha de
    conexao real precisa continuar visivel, nao disfarcada de "sem dados".
    """
    tabela_ausente = isinstance(exc, (ProgrammingError, OperationalError))
    if not tabela_ausente:
        texto = str(exc).lower()
        tabela_ausente = any(marca in texto for marca in _MARCAS_TABELA_AUSENTE)
    if tabela_ausente:
        return MSG_SEM_SNAPSHOT
    return f"Não foi possível ler os dados do portfólio: {exc}"


def _fmt(valor: float | None, sufixo: str = "", casas: int = 2) -> str:
    if valor is None:
        return "—"
    return f"{valor:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".") + sufixo


def detalhe_cobertura(metrica) -> str:
    """Texto de rodape do card: cobertura e, se for o caso, o aviso."""
    pct = f"{metrica.cobertura * 100:.0f}%"
    if metrica.valor is None:
        return "sem dado disponível"
    if not metrica.confiavel:
        return f"⚠️ cobertura {pct} — abaixo do mínimo confiável"
    return f"cobertura {pct} · {metrica.n_ativos} ativos"


def _valor_inicial_total(total_brl: float | None) -> float:
    """Valor inicial do campo de patrimonio total: o total salvo, ou 0.0.

    Funcao pura para nao deixar essa decisao so alcancavel via st.* — o bug
    original (total salvo sumindo a cada reabertura do formulario) veio
    justamente de o widget nunca receber o total ja persistido.
    """
    return float(total_brl) if total_brl is not None else 0.0


def _editor_de_alocacao(alvos: dict, total_brl: float | None = None) -> None:
    """Formulario da alocacao-alvo por classe."""
    with st.expander("⚖️ Alocação-alvo por classe", expanded=not alvos):
        with st.form("form_alocacao_global"):
            entradas: dict[str, float] = {}
            colunas = st.columns(len(asset_classes()))
            for coluna, classe in zip(colunas, asset_classes()):
                with coluna:
                    entradas[classe] = st.number_input(
                        get_spec(classe).label,
                        min_value=0.0, max_value=100.0, step=1.0,
                        value=float(alvos.get(classe, 0.0) * 100.0),
                        key=f"alvo_{classe}",
                    )
            total = st.number_input(
                "Patrimônio total em R$ (opcional)",
                min_value=0.0, step=1000.0,
                value=_valor_inicial_total(total_brl),
                help="Se informado, a tabela mostra o valor por ativo. Não é usado nos percentuais.",
            )
            if st.form_submit_button("Salvar alocação"):
                try:
                    save_allocation_targets(entradas, total_brl=total or None)
                    st.success("Alocação-alvo salva.")
                    st.rerun()
                except (ValueError, KeyError) as exc:
                    st.error(f"Não foi possível salvar: {exc}")


def _cards_de_concentracao(resumo: dict) -> None:
    st.markdown("#### Concentração")
    colunas = st.columns(4)
    cartoes = [
        ("Posições efetivas", resumo["symbol"], "#5B8DEF"),
        ("Setores efetivos", resumo["sector"], "#38BDF8"),
        ("Países efetivos", resumo["country"], "#34D399"),
        ("Classes efetivas", resumo["asset_class"], "#FBBF24"),
    ]
    for coluna, (rotulo, dados, cor) in zip(colunas, cartoes):
        maior = dados["maior_nome"] or "—"
        if rotulo == "Setores efetivos":
            maior = ROTULOS.get(dados["maior_nome"], maior)
        with coluna:
            card_metrica(
                rotulo,
                _fmt(dados["numero_efetivo"], casas=1),
                delta=f'maior: {maior} · {dados["maior_peso"] * 100:.1f}%',
                accent=cor,
                ajuda="Número de posições iguais que teria a mesma concentração (1/HHI).",
            )


def _cards_de_metricas(df: pd.DataFrame) -> None:
    st.markdown("#### Métricas do patrimônio")
    pl = metrics.valuation_agregado(df, "pe")
    pvp = metrics.valuation_agregado(df, "pvp")
    dy = metrics.dy_consolidado(df)

    colunas = st.columns(3)
    cartoes = [
        ("P/L agregado", _fmt(pl.valor), pl, "#5B8DEF"),
        ("P/VP agregado", _fmt(pvp.valor), pvp, "#38BDF8"),
        ("Dividend yield", _fmt(dy.valor, "%"), dy, "#34D399"),
    ]
    for coluna, (rotulo, valor, metrica, cor) in zip(colunas, cartoes):
        with coluna:
            card_metrica(rotulo, valor, delta=detalhe_cobertura(metrica),
                         positivo=None if metrica.confiavel else False,
                         accent=cor)

    st.caption(
        "O P/L e o P/VP agregados usam **earnings yield ponderado**, invertido ao final. "
        "A média aritmética de múltiplos é matematicamente incorreta e distorce para cima "
        "quando há empresa de lucro pequeno."
    )


def _qualidade(df: pd.DataFrame) -> None:
    st.markdown("#### Qualidade por classe")
    st.caption(
        "Não existe um número único de qualidade para o patrimônio: score B3, score "
        "americano e score FII vêm de metodologias e escalas diferentes, e agregá-los "
        "produziria um valor sem significado."
    )
    por_classe = metrics.qualidade_por_classe(df)
    if not por_classe:
        st.info("Sem score disponível nas posições.")
        return
    colunas = st.columns(len(por_classe))
    for coluna, classe in zip(colunas, sorted(por_classe)):
        metrica = por_classe[classe]
        with coluna:
            card_metrica(get_spec(classe).label, _fmt(metrica.valor, casas=1),
                         delta=detalhe_cobertura(metrica), accent="#A78BFA")


def _tabelas(df: pd.DataFrame) -> None:
    st.markdown("#### Composição")
    aba_ativos, aba_setor, aba_pais = st.tabs(["Por ativo", "Por setor", "Por país"])

    with aba_ativos:
        visao = df[["symbol", "name", "asset_class", "sector", "weight_global",
                    "valor_brl"]].copy()
        visao["sector"] = visao["sector"].map(lambda s: ROTULOS.get(s, s))
        visao["weight_global"] = (visao["weight_global"] * 100).round(2)
        visao.columns = ["Ativo", "Nome", "Classe", "Setor", "Peso %", "Valor R$"]
        st.dataframe(visao, use_container_width=True, hide_index=True)

    with aba_setor:
        setores = concentration.por_dimensao(df, "sector")
        setores["sector"] = setores["sector"].map(lambda s: ROTULOS.get(s, s))
        setores["peso"] = (setores["peso"] * 100).round(2)
        setores.columns = ["Setor", "Peso %", "Ativos"]
        st.dataframe(setores, use_container_width=True, hide_index=True)

    with aba_pais:
        paises = concentration.por_dimensao(df, "country")
        paises["peso"] = (paises["peso"] * 100).round(2)
        paises.columns = ["País", "Peso %", "Ativos"]
        st.dataframe(paises, use_container_width=True, hide_index=True)


def render() -> None:
    st.markdown("## 🌐 Portfólio Global")
    st.caption("As três carteiras lidas como um único patrimônio.")

    try:
        snapshots = carregar_snapshots()
        alocacao = load_allocation_targets()
    except Exception as exc:  # noqa: BLE001 - fronteira de isolamento da rota
        mensagem = mensagem_de_erro_ao_carregar(exc)
        if mensagem == MSG_SEM_SNAPSHOT:
            st.info(mensagem)
        else:
            st.error(mensagem)
        return

    alvos = alocacao.get("targets") or {}
    _editor_de_alocacao(alvos, alocacao.get("total_brl"))

    aviso = estado_vazio(snapshots, alvos)
    if aviso:
        st.info(aviso)
        return

    df = montar_posicoes(snapshots, alvos, total_brl=alocacao.get("total_brl"))
    if df.empty:
        st.info(MSG_SEM_SNAPSHOT)
        return

    sem_mapa = nao_mapeados(df.to_dict(orient="records"))
    if sem_mapa:
        st.warning(
            "Setores sem mapeamento canônico (contabilizados como Outros): "
            + ", ".join(f"{get_spec(c).label}/{s}" for c, s in sem_mapa)
        )

    sem_posicao = classes_sem_posicao(snapshots, alvos)
    if sem_posicao:
        st.warning(
            "Classes com alvo definido mas sem posições capturadas: "
            + ", ".join(f"{get_spec(c).label} ({a * 100:.0f}%)" for c, a in sem_posicao)
        )

    _cards_de_concentracao(concentration.resumo(df))
    _cards_de_metricas(df)
    _qualidade(df)
    _tabelas(df)
