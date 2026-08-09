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

# Chave de session_state que carrega a confirmacao de "alocacao salva" atraves
# do st.rerun() disparado logo apos salvar (ver _editor_de_alocacao).
_FLAG_ALOCACAO_SALVA = "portfolio_global_alocacao_salva"

# Tamanhos de Top-N exibidos na concentracao acumulada (ordem de exibicao).
_TOP_NS_CANDIDATOS: tuple[int, ...] = (1, 3, 5, 10)


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


# undefined_table no Postgres (SQLSTATE); ver
# https://www.postgresql.org/docs/current/errcodes-appendix.html
_PGCODE_TABELA_AUSENTE = "42P01"

# SQLite nao tem codigo de erro estruturado: "no such table" e o unico sinal.
# "does not exist" sozinho e ambiguo demais (aparece em erros de coluna, de
# funcao etc.); exigir "relation" junto restringe ao caso de tabela.
_MARCAS_TABELA_AUSENTE = ("no such table",)


def _e_erro_de_tabela_ausente(exc: Exception) -> bool:
    """True quando a excecao representa uma tabela/relacao inexistente.

    Nao usa o tipo da excecao SQLAlchemy (ProgrammingError/OperationalError)
    como sinal: no psycopg2, OperationalError e a categoria de falha de
    CONEXAO/autenticacao, nao de relacao ausente — um Supabase fora do ar ou
    uma credencial errada tambem levantam OperationalError, e confundir os
    dois faz a tela mentir "rode o schema" quando o problema e outro. O sinal
    correto vem do erro do driver: pgcode 42P01 (undefined_table) no
    Postgres, ou a mensagem "no such table" no SQLite (que nao tem pgcode).
    """
    orig = getattr(exc, "orig", None)
    pgcode = getattr(orig, "pgcode", None)
    if pgcode == _PGCODE_TABELA_AUSENTE:
        return True
    texto = str(exc).lower()
    if any(marca in texto for marca in _MARCAS_TABELA_AUSENTE):
        return True
    if "does not exist" in texto and "relation" in texto:
        return True
    return False


def mensagem_de_erro_ao_carregar(exc: Exception) -> str:
    """Traduz uma excecao ao carregar snapshots/alocacao para o texto a exibir.

    O schema 049 (tabelas de snapshot e de alocacao-alvo) pode ainda nao ter
    sido aplicado no Supabase: nesse caso a consulta cai numa tabela
    inexistente, o que e o primeiro-uso esperado, nao um erro de verdade.
    Qualquer outra falha — incluindo conexao recusada ou autenticacao
    invalida, que tambem chegam como OperationalError — mantem a mensagem
    crua: uma falha de conexao real precisa continuar visivel, nao
    disfarcada de "sem dados".
    """
    if _e_erro_de_tabela_ausente(exc):
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
    # A confirmacao precisa sobreviver ao st.rerun() disparado apos salvar —
    # por isso vem de session_state, exibida ANTES do expander: salvar altera
    # `alvos`, o que recolhe o expander (expanded=not alvos) no rerun
    # seguinte, e uma mensagem dentro dele ficaria escondida.
    if st.session_state.pop(_FLAG_ALOCACAO_SALVA, False):
        st.success("Alocação-alvo salva.")
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
                    st.session_state[_FLAG_ALOCACAO_SALVA] = True
                    st.rerun()
                except (ValueError, KeyError) as exc:
                    st.error(f"Não foi possível salvar: {exc}")


def rotulo_maior(dimensao: str, chave: str | None) -> str:
    """Traduz a chave do maior item de uma dimensao de concentracao.

    Setor usa o mapa ROTULOS; classe de ativo usa o label do registry
    (`get_spec`) — a mesma fonte que qualquer outra tela usa para nomear
    b3/us/fii, em vez da chave crua. Pais e moeda ja chegam prontos para
    exibicao ('BR', 'USD') e nao passam por traducao nenhuma.

    Funcao pura para poder testar a decisao de rotulagem sem Streamlit,
    seguindo o padrao de estado_vazio/detalhe_cobertura.
    """
    if chave is None:
        return "—"
    if dimensao == "sector":
        return ROTULOS.get(chave, chave)
    if dimensao == "asset_class":
        try:
            return get_spec(chave).label
        except KeyError:
            return chave
    return chave


def top_ns_a_exibir(n_posicoes: int) -> list[int]:
    """Quais cartoes Top-N mostrar dado o numero de posicoes da carteira.

    `concentration.top_n(df, n)` satura no total (100%) quando `n` e maior ou
    igual ao numero de posicoes — mostrar dois cartoes saturados no mesmo
    valor so repete informacao (ex.: "Top 10" identico a "Top 5" numa
    carteira de 4 ativos). Por isso um tamanho so aparece quando e
    estritamente menor que o numero de posicoes. "Top 1" e sempre exibido,
    mesmo saturado, para a secao nunca ficar vazia (carteira de 1 ativo).
    """
    exibir = [n for n in _TOP_NS_CANDIDATOS if n < n_posicoes]
    if 1 not in exibir:
        exibir = [1] + exibir
    return exibir


def _cards_de_concentracao(df: pd.DataFrame, resumo: dict) -> None:
    st.markdown("#### Concentração")
    colunas = st.columns(5)
    cartoes = [
        ("Posições efetivas", "symbol", resumo["symbol"], "#5B8DEF"),
        ("Setores efetivos", "sector", resumo["sector"], "#38BDF8"),
        ("Países efetivos", "country", resumo["country"], "#34D399"),
        ("Classes efetivas", "asset_class", resumo["asset_class"], "#FBBF24"),
    ]
    for coluna, (rotulo, dimensao, dados, cor) in zip(colunas[:4], cartoes):
        maior = rotulo_maior(dimensao, dados["maior_nome"])
        with coluna:
            card_metrica(
                rotulo,
                _fmt(dados["numero_efetivo"], casas=1),
                delta=f'maior: {maior} · {dados["maior_peso"] * 100:.1f}%',
                accent=cor,
                ajuda="Número de posições iguais que teria a mesma concentração (1/HHI).",
            )
    with colunas[4]:
        card_metrica(
            "Desigualdade (Gini)",
            _fmt(concentration.gini(df["weight_global"]), casas=2),
            accent="#F472B6",
            ajuda=(
                "0 = todas as posições têm o mesmo peso; perto de 1 = "
                "patrimônio concentrado em poucas posições."
            ),
        )


def _cards_de_top_n(df: pd.DataFrame) -> None:
    """Participação acumulada das maiores posições (spec §6.3)."""
    rotulos = {1: "Top 1", 3: "Top 3", 5: "Top 5", 10: "Top 10"}
    ns = top_ns_a_exibir(len(df))
    colunas = st.columns(len(ns))
    for coluna, n in zip(colunas, ns):
        with coluna:
            card_metrica(
                rotulos[n],
                _fmt(concentration.top_n(df, n) * 100, "%", casas=1),
                accent="#F97316",
                ajuda=f"Participação somada das {n} maior(es) posição(ões) do patrimônio.",
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
                         delta=detalhe_cobertura(metrica),
                         positivo=None if metrica.confiavel else False,
                         accent="#A78BFA")


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

    _cards_de_concentracao(df, concentration.resumo(df))
    _cards_de_top_n(df)
    _cards_de_metricas(df)
    _qualidade(df)
    _tabelas(df)
