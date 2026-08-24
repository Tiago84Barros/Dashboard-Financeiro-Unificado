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

import logging
from datetime import date

import pandas as pd
import streamlit as st

from core import transaction_costs
from core.global_portfolio import (
    advisor,
    concentration,
    correlation,
    factors,
    metrics,
    risk,
    roles,
    signals,
)
from core.global_portfolio.aggregate import classes_sem_posicao, montar_posicoes
from core.global_portfolio.returns import Cobertura, retornos_mensais
from core.global_portfolio.taxonomy import ROTULOS, nao_mapeados
from core.market_companies import us_logo_url
from core.portfolio.registry import asset_classes, get_spec
from core.portfolio.repository import (
    load_active_snapshots,
    load_allocation_targets,
    save_allocation_targets,
)
from core.rebalancing import CalendarRebalance
from design.componentes import card_metrica
from design.market_companies import render_company_logo

logger = logging.getLogger(__name__)

MSG_SEM_SNAPSHOT = (
    "Nenhum snapshot encontrado. Rode o schema 049 no Supabase e depois "
    "`python -m scripts.backfill_portfolio_snapshots --apply`."
)
MSG_SEM_ALVO = "Defina a alocação-alvo por classe para consolidar o patrimônio."
MSG_FALHA_AO_CARREGAR = (
    "Não foi possível conectar ao banco de dados. Tente novamente em instantes."
)

# Chave de session_state que carrega a confirmacao de "alocacao salva" atraves
# do st.rerun() disparado logo apos salvar (ver _editor_de_alocacao).
_FLAG_ALOCACAO_SALVA = "portfolio_global_alocacao_salva"

# Tamanhos de Top-N exibidos na concentracao acumulada (ordem de exibicao).
_TOP_NS_CANDIDATOS: tuple[int, ...] = (1, 3, 5, 10)

# Fallback deliberado de core/market_companies.py::translate_us_sector quando
# a fonte (SEC/SIC) nao tras setor determinavel para a empresa. Nao e uma
# lacuna do nosso mapa — e o proprio dado de origem dizendo "nao sei" — entao
# fica fora do aviso de "sem mapeamento canonico" (ver _setores_sem_mapa).
_US_SETOR_INDETERMINADO = "Outros setores"


def _setores_sem_mapa(df: pd.DataFrame) -> list[tuple[str, str]]:
    """Pares (classe, setor bruto) que nao mapearam para um setor canonico.

    `nao_mapeados` espera o setor BRUTO, como a fonte escreveu (contrato da
    funcao, ver core/global_portfolio/taxonomy.py) — mas `df` (de
    montar_posicoes) carrega as duas versoes: `sector_raw` (texto original) e
    `sector` (ja canonico). Passar `sector` faria a funcao tentar re-traduzir
    um valor que ja é canonico (ex.: "consumer") e nao acharia correspondencia
    em nenhum vocabulario de origem — acusando quase todo ativo como "sem
    mapeamento". Por isso remontamos os registros com `sector_raw` no lugar
    de `sector` antes de chamar.

    Tambem filtra o fallback deliberado do modulo EUA (_US_SETOR_INDETERMINADO):
    ele já é, por definição, "sem setor na fonte" — listá-lo sob um aviso de
    "sem mapeamento canônico" sugeriria um problema no nosso mapa que não
    existe.
    """
    registros = (
        df[["asset_class", "sector_raw"]]
        .rename(columns={"sector_raw": "sector"})
        .to_dict(orient="records")
    )
    achados = nao_mapeados(registros)
    return [
        (classe, setor) for classe, setor in achados
        if not (classe == "us" and setor == _US_SETOR_INDETERMINADO)
    ]


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
    inexistente, o que e o primeiro-uso esperado, nao um erro de verdade — a
    pessoa usuaria ve a orientacao de backfill.

    Qualquer outra falha — incluindo conexao recusada ou autenticacao
    invalida, que tambem chegam como OperationalError — precisa continuar
    visivel para quem opera o app, mas NAO como texto cru de excecao Python
    na tela (driver, host, porta, link do SQLAlchemy): isso e detalhe de
    implementacao que nao ajuda a pessoa usuaria final e foi documentado como
    achado A-013. O chamador (`render`) e responsavel por logar o `exc`
    original por inteiro via `logging.exception` antes de chamar esta funcao;
    aqui so decidimos qual mensagem amigavel mostrar.
    """
    if _e_erro_de_tabela_ausente(exc):
        return MSG_SEM_SNAPSHOT
    return MSG_FALHA_AO_CARREGAR


def _fmt(valor: float | None, sufixo: str = "", casas: int = 2) -> str:
    if valor is None:
        return "—"
    return f"{valor:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".") + sufixo


def fracao_para_percentual(fracao: float | None) -> float | None:
    """Converte uma fracao (contrato de MetricaAgregada.valor, ex.: dy_consolidado)
    para pontos percentuais de exibicao.

    core/global_portfolio/metrics.py::dy_consolidado devolve o DY como fracao
    (0,1307), nao como percentual (13,07) — o mesmo contrato que weight_global
    ja usa e que esta tela ja multiplica por 100 em varios outros cartoes
    (concentracao, top-N, peso por ativo). O card de DY historicamente
    esqueceu essa conversao e exibia "0,13%" em vez de "13,07%"; esta funcao
    torna a conversao explicita e testavel no ponto de formatacao, sem tocar
    o valor armazenado.
    """
    if fracao is None:
        return None
    return fracao * 100.0


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
        ("Dividend yield", _fmt(fracao_para_percentual(dy.valor), "%"), dy, "#34D399"),
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


def texto_de_apoio_do_ativo(nome: str | None, classe_label: str, setor_label: str,
                            valor_brl: float | None) -> str:
    """Texto de apoio do card de um ativo: nome, classe, setor e, se houver, valor.

    valor_brl fica de fora quando None em vez de aparecer como travessão —
    ele so existe quando o usuario informou o patrimonio total no editor de
    alocacao (ver _valor_inicial_total); sem isso a ausencia e falta de
    entrada, nao um zero.
    """
    texto = f"{nome or '—'} · {classe_label} · {setor_label}"
    if valor_brl is not None:
        texto += f" · R$ {_fmt(valor_brl, casas=0)}"
    return texto


_B3_LOGO_CDN = "https://raw.githubusercontent.com/thefintz/icones-b3/main/icones"


def _logo_url_ativo(asset_class: str | None, symbol: str) -> str:
    """URL pública do logo do ticker, por classe de origem.

    B3 e FII compartilham o mesmo CDN (ambos negociados na B3, mesma
    convenção de ticker já usada em `views/empresas_b3.py::_logo_url`); EUA
    usa o provedor de `core.market_companies.us_logo_url`, já usado em
    `views/empresas_americanas.py`. `render_company_logo` sempre cai no
    placeholder com a inicial quando a URL não responde — nunca quebra a
    página por logo ausente (achado A-012).
    """
    tk = (symbol or "").strip().upper()
    if asset_class == "us":
        return us_logo_url(tk)
    tk = tk.replace(".SA", "")
    return f"{_B3_LOGO_CDN}/{tk}.png"


def _card_ativo(linha: dict) -> None:
    """Um cartão de ativo: logo à esquerda, KPI de peso à direita — mesmo
    padrão logo+card usado em `empresas_b3.py`/`empresas_americanas.py`.
    """
    symbol = linha.get("symbol", "—")
    asset_class = linha.get("asset_class")
    classe_label = rotulo_maior("asset_class", asset_class)
    setor_label = rotulo_maior("sector", linha.get("sector"))
    col_logo, col_info = st.columns([1, 4], gap="small")
    with col_logo:
        render_company_logo(symbol, _logo_url_ativo(asset_class, symbol), size=34)
    with col_info:
        card_metrica(
            symbol,
            _fmt(linha.get("weight_global", 0.0) * 100, "%", casas=2),
            delta=texto_de_apoio_do_ativo(
                linha.get("name"), classe_label, setor_label, linha.get("valor_brl"),
            ),
            accent="#5B8DEF",
        )


def _cards_de_ativos(df: pd.DataFrame, *, n_colunas: int = 4) -> None:
    """Grade de cartões 'Por ativo', na ordem de peso global (já vem de
    montar_posicoes ordenado decrescente) — um cartão por ativo em vez de
    linha de tabela, seguindo o padrão de card_metrica do restante do app.
    """
    linhas = df.to_dict(orient="records")
    for inicio in range(0, len(linhas), n_colunas):
        colunas = st.columns(n_colunas, gap="small")
        for coluna, linha in zip(colunas, linhas[inicio:inicio + n_colunas]):
            with coluna:
                _card_ativo(linha)


def _cards_de_ativos_por_classe(df: pd.DataFrame, *, n_colunas: int = 4) -> None:
    """Grade 'Por ativo' separada em seções por classe de origem (B3, FIIs,
    Empresas Americanas) — cada classe já é tipo e origem ao mesmo tempo no
    registry (ver `core.portfolio.registry.SPECS`). Seções na ordem do maior
    peso agregado para o menor.
    """
    if df.empty:
        st.info("Nenhum ativo para exibir.")
        return
    peso_por_classe = (
        df.groupby("asset_class")["weight_global"].sum().sort_values(ascending=False)
    )
    for classe in peso_por_classe.index:
        sub = df[df["asset_class"] == classe]
        label = rotulo_maior("asset_class", classe)
        pct = peso_por_classe[classe] * 100
        n = len(sub)
        ativo_noun = "ativo" if n == 1 else "ativos"
        st.markdown(
            f"##### {label} · {pct:.1f}% do patrimônio · {n} {ativo_noun}"
        )
        _cards_de_ativos(sub, n_colunas=n_colunas)


def _tabelas(df: pd.DataFrame) -> None:
    st.markdown("#### Composição")
    aba_ativos, aba_setor, aba_pais = st.tabs(["Por ativo", "Por setor", "Por país"])

    with aba_ativos:
        _cards_de_ativos_por_classe(df)

    with aba_setor:
        setores = concentration.por_dimensao(df, "sector")
        setores["sector"] = setores["sector"].map(lambda s: ROTULOS.get(s, s))
        setores["peso"] = (setores["peso"] * 100).round(2)
        setores.columns = ["Setor", "Peso %", "Ativos"]
        st.dataframe(setores, width="stretch", hide_index=True)

    with aba_pais:
        paises = concentration.por_dimensao(df, "country")
        paises["peso"] = (paises["peso"] * 100).round(2)
        paises.columns = ["País", "Peso %", "Ativos"]
        st.dataframe(paises, width="stretch", hide_index=True)


def aviso_de_cobertura(cob: Cobertura) -> str | None:
    """Mensagem que nomeia os ativos sem série de preços mensal e o quanto do
    patrimônio fica de fora dos painéis de correlação, fatores e risco.

    Hoje isso e toda a classe `us` (sem preço no Supabase — ver
    core/global_portfolio/returns.py); mas a função não assume isso, so lê
    `cob`. Os três painéis calculam sobre a fatia coberta apenas — um deles
    falar do "patrimônio" sem dizer que na verdade descreve 62% dele mentiria
    por omissão, o mesmo risco que _setores_sem_mapa e classes_sem_posicao já
    nomeiam nesta tela. `None` quando a cobertura é total (nada a avisar).
    """
    if (cob.peso_coberto >= 1.0
            and not cob.simbolos_sem_fx and not cob.simbolos_fx_stale
            and not cob.meses_removidos_intersecao):
        return None
    pct_coberto = cob.peso_coberto * 100.0
    pct_fora = 100.0 - pct_coberto
    nomes = (
        ", ".join(cob.simbolos_sem_serie)
        if cob.simbolos_sem_serie else "nenhum símbolo identificado"
    )
    motivos: list[str] = []
    if cob.simbolos_sem_fx:
        motivos.append("câmbio ausente: " + ", ".join(cob.simbolos_sem_fx))
    if cob.simbolos_fx_stale:
        motivos.append("câmbio defasado: " + ", ".join(cob.simbolos_fx_stale))
    detalhe = f" Motivos cambiais — {'; '.join(motivos)}." if motivos else ""
    # Cobertura e janela sao eixos diferentes: 100% do patrimonio pode estar
    # coberto e ainda assim faltarem meses. Sem esta frase o usuario leria
    # "100%" e suporia serie completa.
    janela = ""
    if cob.periodo_comum:
        janela = f" Janela comum: {cob.periodo_comum[0]} a {cob.periodo_comum[1]}."
    if cob.meses_removidos_intersecao:
        janela += (
            f" {len(cob.meses_removidos_intersecao)} meses removidos por falta de "
            f"preço ou câmbio em algum ativo: "
            f"{', '.join(cob.meses_removidos_intersecao)}."
        )
    return (
        f"Correlação, fatores e risco cobrem {pct_coberto:.0f}% do patrimônio "
        f"({pct_fora:.0f}% de fora, sem série mensal válida): {nomes}."
        f"{detalhe}{janela}"
    )


def detalhe_fx(cob: Cobertura) -> str | None:
    """Rastreabilidade da conversão usada nos retornos e no risco."""
    if not cob.fx_evidence:
        return None
    partes: list[str] = []
    for evidencia in cob.fx_evidence:
        data = (evidencia.last_observed_at or "não informada")[:10]
        stale = (
            f" · {evidencia.stale_months} mês(es) sem taxa dentro da tolerância"
            if evidencia.stale_months else ""
        )
        partes.append(
            f"{evidencia.currency}→{evidencia.base_currency} · convenção "
            f"{evidencia.convention} · fonte {evidencia.source} · última observação "
            f"{data} · defasagem máxima {evidencia.max_age_days} dias{stale}"
        )
    return "Câmbio point-in-time: " + " | ".join(partes)


def _tabela_de_pares_redundantes(pares: list[tuple[str, str, float]]) -> pd.DataFrame:
    """Tabela de exibição dos pares redundantes.

    A ordem já vem pronta de `correlation.pares_redundantes` (mais
    correlacionado primeiro, empate por símbolo) — esta função só formata
    para exibição, não reordena.
    """
    return pd.DataFrame(
        [(a, b, round(c, 2)) for a, b, c in pares],
        columns=["Ativo 1", "Ativo 2", "Correlação"],
    )


def _tabela_de_exposicoes(exposicoes: list) -> pd.DataFrame:
    """Tabela de exibição das exposições a fatores, com marca de significância.

    Um fator não significativo continua na tabela — omiti-lo esconderia que
    a estimativa é fraca, o mesmo raciocínio de `fatores_excluidos` em
    core/global_portfolio/factors.py. A marca vai numa coluna de texto
    própria (não só cor) para não depender de CSS nem sumir num leitor de
    tela ou numa exportação da tabela.
    """
    return pd.DataFrame(
        [
            {
                "Fator": factors.ROTULOS_FATOR.get(e.fator, e.fator),
                "Beta": round(e.beta, 3),
                "Erro-padrão": round(e.erro_padrao, 3),
                "R²": round(e.r2, 2),
                "Observações": e.n_obs,
                "Significativo": "✅ Sim" if e.significativo else "⚠️ Não",
            }
            for e in exposicoes
        ]
    )


def _painel_correlacao(ret: pd.DataFrame, pesos: dict) -> None:
    st.markdown("#### Correlação e diversificação")
    if ret.empty or ret.shape[1] < 2:
        st.info(
            "Menos de dois ativos com série de preços mensal suficiente — "
            "sem base para calcular correlação entre eles."
        )
        return

    colunas = st.columns(3)
    cartoes = [
        ("Correlação média", correlation.correlacao_media(ret), "#5B8DEF",
         "Média das correlações par a par entre os ativos com série de preços."),
        ("Razão de diversificação", correlation.razao_diversificacao(ret, pesos), "#38BDF8",
         "Soma ponderada das volatilidades individuais sobre a volatilidade da "
         "carteira. 1,0 = nenhuma diversificação real."),
        ("Apostas efetivas", correlation.apostas_efetivas(ret, pesos), "#34D399",
         "Número efetivo de apostas independentes na carteira (PCA da "
         "covariância ponderada pelos pesos)."),
    ]
    for coluna, (rotulo, valor, cor, ajuda) in zip(colunas, cartoes):
        with coluna:
            card_metrica(rotulo, _fmt(valor, casas=2), accent=cor, ajuda=ajuda)

    st.markdown("###### Pares redundantes (correlação ≥ {:.2f})".format(correlation.LIMIAR_REDUNDANCIA))
    pares = correlation.pares_redundantes(ret)
    if not pares:
        st.caption("Nenhum par de ativos acima do limiar de redundância.")
    else:
        st.dataframe(_tabela_de_pares_redundantes(pares), width="stretch", hide_index=True)


def _painel_fatores(ret: pd.DataFrame, pesos: dict) -> None:
    st.markdown("#### Exposição a fatores de risco")
    if ret.empty:
        st.info("Sem série de preços mensal suficiente para estimar exposição a fatores.")
        return

    serie_fatores = factors.series_de_fatores()
    if serie_fatores.empty:
        st.info("Sem série dos fatores de referência (proxies via ETF na B3).")
        return

    resultado = factors.exposicao_do_portfolio(ret, pesos, serie_fatores)
    if not resultado.exposicoes:
        st.info(
            "Amostra insuficiente para estimar exposição a fatores "
            f"(mínimo de {factors.MIN_OBS_REGRESSAO} meses em comum)."
        )
        return

    st.dataframe(_tabela_de_exposicoes(resultado.exposicoes), width="stretch", hide_index=True)
    st.caption(
        "Beta estimado por regressão múltipla contra ETFs-proxy na B3. "
        "⚠️ marca um fator cujo beta não supera ~2 erros-padrão de zero — "
        "não é evidência de exposição real, é ruído de amostra."
    )
    if resultado.fatores_excluidos:
        nomes = ", ".join(
            factors.ROTULOS_FATOR.get(f, f) for f in resultado.fatores_excluidos
        )
        st.caption(
            f"Fora da regressão por falta de dado em comum: {nomes}. "
            "Ausente da tabela não é o mesmo que irrelevante — é a série não "
            "ter sustentado a estimativa."
        )


def _painel_risco(ret: pd.DataFrame, pesos: dict) -> None:
    st.markdown("#### Risco do patrimônio")
    if ret.empty:
        st.info("Sem série de preços mensal suficiente para estimar risco.")
        return

    r = risk.metricas_de_risco(ret, pesos)
    if r is None:
        st.info(
            "Série mensal curta demais para estimar risco com confiança "
            f"(mínimo de {risk.MIN_OBS} meses)."
        )
        return

    # A cauda e que sustenta VaR e CVaR, nao o total de meses. Com poucos meses
    # ela encolhe ate um unico ponto, e ai as duas metricas sao o MESMO numero
    # (o pior mes) exibido em dois cartoes. Isso e declarado, nao escondido.
    plural = "meses" if r.n_cauda != 1 else "mês"
    ajuda_cvar = (
        f"Perda média dos {r.n_cauda} {plural} que ultrapassam o VaR 95% — "
        "a cauda além do corte."
    )
    if r.n_cauda <= 1:
        ajuda_cvar += (
            " Com esta janela a cauda tem uma observação só: este número É o "
            "pior mês da série, não uma média sobre vários."
        )
    ajuda_var = (
        "Perda mensal no percentil histórico de 5%, sem supor distribuição "
        "normal."
    )
    ajuda_var += (
        f" A série tem {r.n_obs} meses: o corte é interpolado entre os piores, "
        "não observado."
    ) if r.n_obs < 20 else " Aproximadamente 1 a cada 20 meses."

    colunas = st.columns(4)
    cartoes = [
        ("Volatilidade anual", r.vol_anual, "#5B8DEF",
         "Desvio-padrão dos retornos mensais, anualizado (×√12)."),
        ("VaR 95%", r.var_95, "#F97316", ajuda_var),
        ("CVaR 95%", r.cvar_95, "#FC5C7D", ajuda_cvar),
        ("Drawdown máximo", r.drawdown_max, "#A78BFA",
         "Maior queda do pico ao vale na série de retornos acumulados mensais "
         "— quedas dentro do mês não aparecem."),
    ]
    for coluna, (rotulo, valor, cor, ajuda) in zip(colunas, cartoes):
        with coluna:
            card_metrica(rotulo, _fmt(valor * 100, "%", casas=1), accent=cor, ajuda=ajuda)
    st.caption(
        f"Baseado em {r.n_obs} meses de retornos do patrimônio consolidado; "
        f"VaR e CVaR repousam sobre {r.n_cauda} {plural} de cauda."
    )


# Rotulos de exibicao dos limiares de roles.LIMIARES: so texto, a mesma
# necessidade que ROTULOS_PAPEL ja atende para as chaves de papel — nao
# reinterpreta nem deriva nada do valor, so nomeia a chave para leitura.
_ROTULOS_LIMIAR: dict[str, str] = {
    "payout_instavel": "desvio relativo máximo do payout (renda)",
    "cagr_minimo": "CAGR mínimo de LPA (crescimento)",
    "vol_baixa": "volatilidade anualizada máxima (baixa volatilidade)",
    "correlacao_baixa": "correlação média máxima (diversificação)",
    "papel_dominante": "% mínimo em recebíveis (proteção contra inflação, FII)",
    "tijolo_dominante": "% mínimo em imóveis (reserva de valor, FII)",
}


def _texto_de_limiares() -> str:
    """Frase que declara os limiares como escolhas heurísticas e lista os
    valores em uso — para que quem le julgue o corte em vez de tomá-lo como
    fato sobre os ativos (regra do plano de Fase 3a)."""
    partes = [
        f"{_ROTULOS_LIMIAR.get(chave, chave)} = {valor:g}"
        for chave, valor in roles.LIMIARES.items()
    ]
    return (
        "Limiares heurísticos, ajustáveis e não fatos sobre os ativos — "
        + "; ".join(partes) + "."
    )


def _resumo_de_papeis(entradas: list[roles.PapelDoAtivo]) -> dict:
    """Agregados do painel: quantos ativos cumprem cada papel e quantos não
    cumprem papel algum.

    Todo papel de `roles.PAPEIS` aparece no resultado, mesmo com contagem
    zero, quando ha pelo menos um ativo classificado — um papel que nenhum
    ativo cumpre (ex.: "renda" na carteira real) e informacao, nao ausencia
    de informacao, e sumir do resumo esconderia justamente esse caso. Lista
    vazia devolve resumo vazio: sem ativo classificado nao ha o que contar.
    """
    if not entradas:
        return {"por_papel": {}, "sem_papel": 0}

    contagem = {papel: 0 for papel in roles.PAPEIS}
    sem_papel = 0
    for entrada in entradas:
        if not entrada.papeis:
            sem_papel += 1
        for papel in entrada.papeis:
            contagem[papel] += 1
    return {"por_papel": contagem, "sem_papel": sem_papel}


def _tabela_de_papeis_do_ativo(entrada: roles.PapelDoAtivo) -> pd.DataFrame:
    """Uma linha por papel para um ativo: cumpre (com a evidência numérica),
    indeterminado (sem dado suficiente) ou não cumpre (regra avaliada e
    negada).

    "Indeterminado" e "não cumpre" recebem rótulos de status diferentes de
    propósito — são coisas diferentes ("não sabemos" vs. "sabemos e não
    cumpre"), e uma tabela que os tratasse igual diria ao leitor algo que os
    dados não sustentam.
    """
    evidencia_por_papel = {e.papel: e for e in entrada.evidencias}
    linhas = []
    for papel in roles.PAPEIS:
        rotulo = roles.ROTULOS_PAPEL[papel]
        if papel in entrada.papeis:
            evidencia = evidencia_por_papel.get(papel)
            linhas.append({
                "Papel": rotulo,
                "Status": "✅ Cumpre",
                "Evidência": evidencia.texto if evidencia else "—",
            })
        elif papel in entrada.indeterminados:
            linhas.append({
                "Papel": rotulo,
                "Status": "❔ Indeterminado",
                "Evidência": "sem dado suficiente para avaliar",
            })
        else:
            linhas.append({
                "Papel": rotulo,
                "Status": "— Não cumpre",
                "Evidência": "—",
            })
    return pd.DataFrame(linhas)


def _painel_papeis(df: pd.DataFrame, ret: pd.DataFrame) -> None:
    """Painel 'Papel estratégico': para que serve cada ativo do patrimônio.

    So formata o que `roles.classificar` ja decidiu — nenhum calculo entra
    aqui. `correlacao_media_por_ativo` reduz a matriz de correlacao ja
    calculada pelo painel de correlacao (Task 1 da Fase 3a); nao chama
    `retornos_mensais` de novo, reaproveita o `ret` que `render()` ja tem.
    """
    st.markdown("#### Papel estratégico por ativo")

    if df.empty:
        st.info("Sem posições para classificar por papel estratégico.")
        return

    correlacoes = correlation.correlacao_media_por_ativo(ret)
    entradas = roles.classificar(df, retornos=ret, correlacoes=correlacoes)
    if not entradas:
        st.info("Sem posições para classificar por papel estratégico.")
        return

    st.caption(_texto_de_limiares())

    resumo = _resumo_de_papeis(entradas)

    card_metrica(
        "Sem papel algum",
        resumo["sem_papel"],
        positivo=(resumo["sem_papel"] == 0),
        accent="#FC5C7D" if resumo["sem_papel"] else "#00C896",
        ajuda="Ativos para os quais nenhuma regra teve evidência suficiente — "
              "o número acionável deste painel.",
    )

    itens_por_papel = list(resumo["por_papel"].items())
    n_colunas = 4
    for inicio in range(0, len(itens_por_papel), n_colunas):
        colunas = st.columns(n_colunas)
        fatia = itens_por_papel[inicio:inicio + n_colunas]
        for coluna, (papel, contagem) in zip(colunas, fatia):
            with coluna:
                card_metrica(roles.ROTULOS_PAPEL[papel], contagem, accent="#5B8DEF")

    sem_papel_symbols = [e.symbol for e in entradas if not e.papeis]
    if sem_papel_symbols:
        st.warning(
            f"⚠️ {len(sem_papel_symbols)} ativo(s) sem nenhum papel identificado: "
            + ", ".join(sem_papel_symbols)
        )

    por_symbol = {e.symbol: e for e in entradas}
    for _, linha in df.iterrows():
        symbol = linha["symbol"]
        entrada = por_symbol.get(symbol)
        if entrada is None:
            continue
        titulo = (
            f"🔴 {symbol} — nenhum papel identificado" if not entrada.papeis
            else symbol
        )
        with st.expander(titulo):
            st.dataframe(_tabela_de_papeis_do_ativo(entrada),
                        width="stretch", hide_index=True)
            st.caption(entrada.justificativa)


# ---------------------------------------------------------------------------
# Fase 3b, Task 6: painel "Recomendações do motor de movimentação"
# ---------------------------------------------------------------------------

# Rotulo e cor de exibicao de cada acao de core.global_portfolio.advisor.Acao —
# so texto/estilo, nao reinterpreta nem deriva nada da decisao do motor.
_ROTULO_ACAO: dict[str, str] = {
    "aumentar": "Aumentar",
    "reduzir": "Reduzir",
    "vender": "Vender",
    "manter": "Manter",
    "indeterminado": "Indeterminado",
}

_ACCENT_ACAO: dict[str, str] = {
    "aumentar": "#00C896",
    "reduzir": "#F97316",
    "vender": "#FC5C7D",
    "manter": "#9CA3AF",
    "indeterminado": "#9CA3AF",
}

# Ordem de exibicao dos cartoes de resumo — nao a ordem de ACOES_VALIDAS
# (um frozenset, sem ordem estavel), e sim a leitura natural do fluxo de
# decisao (compra -> venda -> nada -> sem dado).
_ORDEM_RESUMO_ACOES: tuple[str, ...] = ("aumentar", "reduzir", "vender", "manter", "indeterminado")


def texto_de_custo(acao: advisor.Acao) -> str:
    """Texto de exibicao do custo de uma recomendacao.

    Nunca um numero cru quando a classe nao esta calibrada: Task 3 fez
    `calibrado=False` produzir `math.nan` de proposito, precisamente para
    nao ser confundido com um custo apurado — este texto intercepta o NaN
    ANTES de qualquer formatacao numerica (nunca chega a `_fmt`) e devolve
    "não calibrado" por extenso. Tambem nunca um "R$ 0,00" para o ativo
    indeterminado: ali `custo_estimado` e 0.0 so porque o motor nunca
    tentou calcular (Task 4), nao porque o custo de fato seja zero.

    A ressalva de IR fica NA MESMA string do numero, nunca numa nota a
    parte: o motor (Task 4) sempre chama `custo_venda` com `lucro=0.0`
    porque esta camada pura nao rastreia preco medio/lote — o IR relatado
    e sempre 0 por construcao, entao o numero mostrado e so a friccao de
    negociacao. Isso vale para toda recomendacao cujo peso sugerido caia
    abaixo do atual (venda ou reducao parcial), mesmo quando a acao final
    virou "manter" pelo guard de custo — o numero exibido ali tambem veio
    de `custo_venda`.
    """
    if acao.acao == "indeterminado":
        return "custo: não aplicável (sem sinal para este ativo)"
    if not acao.custo_calibrado:
        return "custo: não calibrado (classe sem parâmetros — ver Custo por classe)"
    texto = f"custo: R$ {_fmt(acao.custo_estimado, casas=2)}"
    if acao.peso_sugerido < acao.peso_atual:
        texto += " — fricção de negociação; não inclui IR (sem rastreio de custo de aquisição)"
    return texto


def _resumo_de_acoes(acoes: list[advisor.Acao]) -> dict[str, int]:
    """Contagem de recomendacoes por acao.

    Toda chave de `advisor.ACOES_VALIDAS` aparece, mesmo com contagem zero
    — mesma razao de `_resumo_de_papeis`: uma acao que nenhuma recomendacao
    recebeu hoje (ex.: nenhuma venda) e informacao, nao ausencia dela.
    """
    contagem = {a: 0 for a in advisor.ACOES_VALIDAS}
    for acao in acoes:
        contagem[acao.acao] = contagem.get(acao.acao, 0) + 1
    return contagem


def _linha_de_componentes(acao: advisor.Acao) -> pd.DataFrame:
    """Tabela de decomposicao numerica do score: um sinal por linha, ordenada
    por nome para exibicao deterministica."""
    linhas = [{"Sinal": nome, "Valor": round(valor, 3)}
              for nome, valor in sorted(acao.componentes.items())]
    return pd.DataFrame(linhas, columns=["Sinal", "Valor"])


def _texto_de_limiares_motor() -> str:
    """Ressalva do motor de movimentacao — mesmo tom e lugar de
    `_texto_de_limiares` (papel estrategico, Fase 3a): os limiares abaixo
    sao escolhas de desenho do motor, nao fatos sobre os ativos, e ficam
    listados para quem le julgar o corte em vez de toma-lo como dado.
    """
    partes = [
        f"limite por ativo = {signals.LIMITE_ATIVO_DEFAULT:.0%}",
        f"limite por setor = {signals.LIMITE_SETOR_DEFAULT:.0%}",
        f"limite por país = {signals.LIMITE_PAIS_DEFAULT:.0%}",
        f"correlação de redundância ≥ {correlation.LIMIAR_REDUNDANCIA:g}",
        f"tilt de fator extremo = {signals.LIMIAR_TILT_FATOR_EXTREMO:g}",
        f"sensibilidade do score = {advisor.SENSIBILIDADE_SCORE_DEFAULT:g}",
        f"peso mínimo antes de virar venda = {advisor.LIMIAR_VENDA_DEFAULT:.2%}",
    ]
    return (
        "Limiares heurísticos do motor de movimentação, ajustáveis e não fatos "
        "sobre os ativos — " + "; ".join(partes) + "."
    )


def _custos_por_classe() -> dict[str, transaction_costs.CostConfig]:
    """Mapa classe -> CostConfig que o motor usa para descontar custo.

    Só a B3 tem parâmetro calibrado hoje (`brasil_pf_default`). EUA e FII
    ficam explicitamente `nao_calibrado`: a ressalva registrada no plano da
    Fase 3b é explícita — não inventamos alíquota, spread nem isenção para
    essas classes só para a tela mostrar um número que parece apurado e não
    é (ver docstring de `core/transaction_costs.py`).
    """
    return {
        transaction_costs.CLASSE_B3: transaction_costs.CostConfig.brasil_pf_default(),
        transaction_costs.CLASSE_US: transaction_costs.CostConfig.nao_calibrado(
            transaction_costs.CLASSE_US),
        transaction_costs.CLASSE_FII: transaction_costs.CostConfig.nao_calibrado(
            transaction_costs.CLASSE_FII),
    }


def _gerar_recomendacoes(df: pd.DataFrame, ret: pd.DataFrame, pesos: dict,
                         alvos: dict, total_brl: float | None, *,
                         loader=None) -> list[advisor.Acao]:
    """Monta os sinais (Fase 3b Task 2) a partir dos analisadores de verdade
    e chama o motor (Task 4). Função pura, sem Streamlit — a fronteira de
    isolamento contra falha do motor fica em `_painel_recomendacoes`, que a
    chama dentro de um try/except (mesmo padrão de
    `dashboard_geral._carteira_modelo_us`).

    Sem série mensal suficiente (menos de dois ativos com retorno, ou
    `df`/`ret` vazios) não há como calcular risco, correlação ou fatores —
    devolve lista vazia em vez de propagar exceção ou inventar sinal.

    `loader` só existe para o teste poder injetar uma série de fatores
    sintética sem tocar o banco — produção sempre usa o default de
    `factors.series_de_fatores` (mesmo padrão de `_painel_fatores`).
    """
    if df is None or df.empty or ret is None or ret.empty or ret.shape[1] < 2:
        return []

    pares = correlation.pares_redundantes(ret)
    contribuicoes = risk.contribuicao_marginal(ret, pesos)
    correlacoes_media = correlation.correlacao_media_por_ativo(ret)
    papeis = roles.classificar(df, retornos=ret, correlacoes=correlacoes_media)

    serie_fatores = factors.series_de_fatores(loader=loader)
    exposicao_portfolio = None
    exposicoes_ativos: dict = {}
    if not serie_fatores.empty:
        exposicao_portfolio = factors.exposicao_do_portfolio(ret, pesos, serie_fatores)
        if exposicao_portfolio.exposicoes:
            exposicoes_ativos = {
                s: factors.betas_do_ativo(ret[s], serie_fatores) for s in ret.columns
            }

    sinais = signals.gerar_sinais(
        df, contribuicoes=contribuicoes, pares_redundantes=pares, papeis=papeis,
        alvos=alvos, exposicao_portfolio=exposicao_portfolio,
        exposicoes_ativos=exposicoes_ativos,
    )

    return advisor.recomendar(
        df, sinais, alvos=alvos, politica=CalendarRebalance(),
        custos=_custos_por_classe(), patrimonio_total=float(total_brl or 0.0),
        data_atual=date.today(),
    )


def _painel_recomendacoes(df: pd.DataFrame, ret: pd.DataFrame, pesos: dict,
                          alvos: dict, total_brl: float | None) -> None:
    """Painel 'Recomendações do motor de movimentação' (Fase 3b, Task 6).

    Cartões CSS (`card_metrica`), nunca informação solta — mesma regra do
    resto da tela. Uma falha do motor (`_gerar_recomendacoes`) nunca derruba
    a seção: cai no aviso e o restante do Portfólio Global continua válido,
    mesmo padrão de fronteira de isolamento que
    `dashboard_geral._carteira_modelo_us` já usa entre módulos.
    """
    st.markdown("#### Recomendações do motor de movimentação")

    try:
        acoes = _gerar_recomendacoes(df, ret, pesos, alvos, total_brl)
    except Exception:  # noqa: BLE001 - fronteira de isolamento do motor de recomendacao
        st.warning(
            "⚠️ Não foi possível gerar as recomendações do motor de movimentação. "
            "As demais seções do Portfólio Global continuam válidas."
        )
        return

    if not acoes:
        st.info(
            "Sem série mensal suficiente para o motor combinar sinais e "
            "recomendar movimentos."
        )
        return

    st.caption(_texto_de_limiares_motor())

    resumo = _resumo_de_acoes(acoes)
    colunas = st.columns(len(_ORDEM_RESUMO_ACOES))
    for coluna, chave in zip(colunas, _ORDEM_RESUMO_ACOES):
        with coluna:
            card_metrica(_ROTULO_ACAO[chave], resumo[chave], accent=_ACCENT_ACAO[chave])

    # Ponto 1 do brief: custo nao calibrado precisa ser visivel sem que o
    # usuario precise abrir cada recomendacao — um cartao de contagem, mais
    # o aviso que nomeia os ativos (mesmo padrao de sem_papel_symbols em
    # _painel_papeis).
    nao_calibrados = [a for a in acoes if not a.custo_calibrado]
    card_metrica(
        "Recomendações com custo não calibrado",
        len(nao_calibrados),
        positivo=(len(nao_calibrados) == 0),
        accent="#FC5C7D" if nao_calibrados else "#00C896",
        ajuda="Classe sem parâmetros de custo calibrados (core.transaction_costs): "
              "o motor recusa mexer até o custo real ser conhecido.",
    )
    if nao_calibrados:
        st.warning(
            f"⚠️ {len(nao_calibrados)} recomendação(ões) com custo não calibrado, "
            "mantidas em vez de executadas: "
            + ", ".join(a.symbol for a in nao_calibrados)
        )

    for acao in acoes:
        titulo = f"{acao.symbol} — {_ROTULO_ACAO.get(acao.acao, acao.acao)}"
        with st.expander(titulo):
            card_metrica(
                "Peso atual → sugerido",
                f"{acao.peso_atual * 100:.2f}% → {acao.peso_sugerido * 100:.2f}%",
                delta=texto_de_custo(acao),
                accent=_ACCENT_ACAO.get(acao.acao, "#9CA3AF"),
            )
            if acao.analisadores:
                st.caption("Analisadores que dispararam: " + ", ".join(sorted(acao.analisadores)))
            else:
                st.caption("Nenhum analisador produziu sinal para este ativo.")
            if acao.componentes:
                st.dataframe(_linha_de_componentes(acao), use_container_width=True, hide_index=True)


def render() -> None:
    st.markdown("## 🌐 Portfólio Global")
    st.caption("As três carteiras lidas como um único patrimônio.")

    try:
        snapshots = carregar_snapshots()
        alocacao = load_allocation_targets()
    except Exception as exc:  # noqa: BLE001 - fronteira de isolamento da rota
        # O detalhe tecnico completo (driver, host, porta) vai para o log,
        # nao para a tela — ver achado A-013.
        logger.exception("Falha ao carregar snapshots/alocacao do portfolio global")
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

    sem_mapa = _setores_sem_mapa(df)
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

    st.markdown("#### Correlação, fatores e risco")
    # Uma unica chamada a retornos_mensais por render: o quadro (ret, cob) e
    # reaproveitado pelos tres paineis abaixo, em vez de buscar preco tres vezes.
    ret, cob = retornos_mensais(df)
    aviso = aviso_de_cobertura(cob)
    if aviso:
        st.warning(aviso)
    fx_info = detalhe_fx(cob)
    if fx_info:
        st.caption(fx_info)
    pesos = dict(zip(df["symbol"], df["weight_global"]))
    _painel_correlacao(ret, pesos)
    _painel_fatores(ret, pesos)
    _painel_risco(ret, pesos)
    _painel_papeis(df, ret)
    _painel_recomendacoes(df, ret, pesos, alvos, alocacao.get("total_brl"))
