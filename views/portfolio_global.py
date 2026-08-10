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

from core.global_portfolio import concentration, correlation, factors, metrics, risk, roles
from core.global_portfolio.aggregate import classes_sem_posicao, montar_posicoes
from core.global_portfolio.returns import Cobertura, retornos_mensais
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
                classe_label = rotulo_maior("asset_class", linha.get("asset_class"))
                setor_label = rotulo_maior("sector", linha.get("sector"))
                card_metrica(
                    linha.get("symbol", "—"),
                    _fmt(linha.get("weight_global", 0.0) * 100, "%", casas=2),
                    delta=texto_de_apoio_do_ativo(
                        linha.get("name"), classe_label, setor_label, linha.get("valor_brl"),
                    ),
                    accent="#5B8DEF",
                )


def _tabelas(df: pd.DataFrame) -> None:
    st.markdown("#### Composição")
    aba_ativos, aba_setor, aba_pais = st.tabs(["Por ativo", "Por setor", "Por país"])

    with aba_ativos:
        _cards_de_ativos(df)

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
    if cob.peso_coberto >= 1.0:
        return None
    pct_coberto = cob.peso_coberto * 100.0
    pct_fora = 100.0 - pct_coberto
    nomes = ", ".join(cob.simbolos_sem_serie) if cob.simbolos_sem_serie else "nenhum símbolo identificado"
    return (
        f"Correlação, fatores e risco cobrem {pct_coberto:.0f}% do patrimônio "
        f"({pct_fora:.0f}% de fora, sem série mensal de preços): {nomes}."
    )


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
        st.dataframe(_tabela_de_pares_redundantes(pares), use_container_width=True, hide_index=True)


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

    st.dataframe(_tabela_de_exposicoes(resultado.exposicoes), use_container_width=True, hide_index=True)
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

    colunas = st.columns(4)
    cartoes = [
        ("Volatilidade anual", r.vol_anual, "#5B8DEF",
         "Desvio-padrão dos retornos mensais, anualizado (×√12)."),
        ("VaR 95%", r.var_95, "#F97316",
         "Perda mensal no percentil histórico de 5% — 1 a cada 20 meses, sem "
         "supor distribuição normal."),
        ("CVaR 95%", r.cvar_95, "#FC5C7D",
         "Perda média nos meses que ultrapassam o VaR 95% — a cauda além do corte."),
        ("Drawdown máximo", r.drawdown_max, "#A78BFA",
         "Maior queda do pico ao vale na série de retornos acumulados."),
    ]
    for coluna, (rotulo, valor, cor, ajuda) in zip(colunas, cartoes):
        with coluna:
            card_metrica(rotulo, _fmt(valor * 100, "%", casas=1), accent=cor, ajuda=ajuda)
    st.caption(f"Baseado em {r.n_obs} meses de retornos do patrimônio consolidado.")


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
                        use_container_width=True, hide_index=True)
            st.caption(entrada.justificativa)


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
    pesos = dict(zip(df["symbol"], df["weight_global"]))
    _painel_correlacao(ret, pesos)
    _painel_fatores(ret, pesos)
    _painel_risco(ret, pesos)
    _painel_papeis(df, ret)
