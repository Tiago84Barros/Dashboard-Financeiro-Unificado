"""
core/b3_safras.py — a carteira de cada safra e o que ela rendeu.

O motor de scoring ja monta `lids_por_ano` e `pesos_por_ano` por segmento,
point-in-time (score com lag=1, dados ate N-1). Este modulo agrega isso
entre segmentos com o mesmo orcamento que a tela usa e mede o retorno da
janela de vigencia de cada safra.

`carteiras_por_safra` recebe a lista de resultados JA FILTRADA pela
chamadora. E assim que a medicao do vies de universo funciona: a mesma
funcao roda com os segmentos aprovados e com todos, e a distancia entre as
duas curvas e o tamanho do vies.

Regras de medicao do retorno (rodada de correcao 1, revisao de codigo):

- **Lacuna de preco e simetrica.** Um ticker sem preco valido na janela
  rende ZERO tanto na estrategia (peso vai para `peso_ausente`) quanto no
  equal-weight (entra na media com 0.0 em vez de ser omitido). Omitir do
  EW faria o benchmark virar "a media dos sobreviventes", que e mais forte
  que o mercado que ele deveria descrever.
- **A ancora da ponta final vem do mercado, nao do papel.** `corte` e a
  ultima data de `df_precos` (a serie inteira, nao a coluna do ticker)
  dentro da janela, limitada a `carteira.fim`. Cada ticker so conta se sua
  primeira cotacao estiver a no maximo `TOLERANCIA_DIAS` do inicio da
  janela e a ultima a no maximo `TOLERANCIA_DIAS` do corte -- do contrario
  um papel que parou de negociar em maio entraria com o retorno de um mes
  rotulado como o retorno da safra inteira (vies sistematico para cima em
  quem foi deslistado). A serie e mensal; 45 dias da uma folga de um mes e
  nao mais que isso.
- **A Selic da safra parcial para no mesmo corte.** Assim a comparacao
  cobre os mesmos meses dos dois lados -- sem isso uma safra com 5 meses
  de bolsa seria comparada com 12 meses de CDI.
- **O fallback da taxa Selic e reportado.** Ano sem entrada em
  `selic_por_ano` (ou com valor `None`) usa `taxa_selic_aa` e entra em
  `selic_anos_estimados`, para a tela poder avisar que aquele trecho nao
  veio do dado observado.

Regras de medicao do retorno (rodada de correcao 2, revisao de codigo):

- **As duas pontas da janela vem do mercado, nao so a final.** Se o feed
  de precos comeca depois do inicio civil da safra (atraso de ingestao, o
  mesmo `df_precos` inteiro para todo mundo -- nao delisting de um papel
  so), ancorar a tolerancia da ponta inicial no calendario reprovaria TODO
  ticker por um problema de cobertura de dado. `_janela_de_mercado`
  devolve `(inicio_mercado, corte)`, os dois lidos da serie inteira; os
  dois alimentam a tolerancia de `_preco_nas_pontas` E o `pd.date_range`
  da Selic -- antes so o `corte` vinha do mercado e o `inicio` cru, essa
  assimetria inflava a Selic quando a primeira cotacao real caia depois do
  dia 1 do mes (o caso comum: dado mensal com data no fim do mes deixava o
  mes parcial inicial de fora do preco mas dentro da Selic).
- **Linha 100% sem cotacao nao e pregao.** `_janela_de_mercado` descarta
  linhas onde nenhum ticker tem preco (`dropna(how="all")`) antes de olhar
  o indice. Sem isso, um `df_precos` vindo de `reindex`/`asfreq` sobre o
  calendario cheio faria o corte parar no ultimo dia do calendario, nao no
  ultimo dia com dado de verdade -- reabrindo por outra porta o mesmo
  descasamento Selic-vs-bolsa que o corte de mercado existe para fechar.
- **A cobertura do universo chega na tabela, nao so no dict interno.**
  `tabela_de_safras` tinha `n_universo_total`/`n_universo_com_preco`
  calculados em `retorno_da_safra` e nunca expostos -- a Task 5 so
  consome a tabela. Agora a coluna "Universo com preço" traz `n/total`
  (nao percentual: 3/4 e 300/400 sao coberturas bem diferentes que um "75%"
  sozinho esconde) e `tabela.attrs["selic_anos_estimados_por_safra"]`
  traz o dict `{safra: [anos]}` inteiro, nao so o ultimo calculado.

Modulo puro: sem streamlit, sem banco. Coberto por tests/test_b3_safras.py.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from core.b3_vigencia import ano_base_do_score, janela_de_vigencia, safra_completa

# A serie de precos e mensal: 45 dias cobre uma folga de um mes de atraso
# na cotacao e nao mais que isso -- ver docstring do modulo.
TOLERANCIA_DIAS = 45

COLUNAS_TABELA = [
    "Safra", "Exercício-base", "Janela", "Completa", "Segmentos", "Ativos",
    "Maiores posições", "Estratégia (%)", "Equal-weight (%)", "Selic (%)",
    "Excesso s/ Selic (pp)", "Peso sem preço (%)", "Universo com preço",
]


@dataclass(frozen=True)
class SafraCarteira:
    """A carteira que o motor teria montado numa safra, e seu universo."""
    safra: int
    ano_base: int
    inicio: pd.Timestamp
    fim: pd.Timestamp
    completa: bool
    pesos: dict[str, float]
    universo: tuple[str, ...]
    segmentos: int


def _pesos_internos_do_segmento(lids: list[str], brutos: dict) -> dict[str, float]:
    """Peso relativo de cada lider dentro do orcamento do segmento.

    Cai para peso igual entre os lideres quando `pesos_por_ano` esta
    ausente, incompleto (lider sem entrada) ou tem valor invalido (soma
    <=0 ou NaN). Sem essa checagem um lider sem entrada silenciosamente
    virava peso 0 e mesmo assim contava como ativo na tabela, e um NaN
    propagava ate `round(nan*100, 1)` sem nenhum aviso.
    """
    valores = {tk: float(brutos.get(tk, float("nan"))) for tk in lids}
    total = sum(valores.values())
    completo_e_valido = (
        all(tk in brutos for tk in lids)
        and np.isfinite(total) and total > 0
        and all(np.isfinite(v) for v in valores.values())
    )
    if not completo_e_valido:
        return {tk: 1.0 / len(lids) for tk in lids}
    return {tk: v / total for tk, v in valores.items()}


def carteiras_por_safra(resultados: list[dict], *,
                        hoje: pd.Timestamp | None = None
                        ) -> list[SafraCarteira]:
    """Agrega os lideres por segmento na carteira consolidada de cada safra.

    Orcamento igual por segmento que tem lider na safra; dentro dele, os
    pesos do score. Ticker em mais de um segmento soma os orcamentos --
    mesma regra de views/portfolio_b3.py:3593-3624.
    """
    anos: set[int] = set()
    for res in resultados or []:
        anos.update(int(a) for a in (res.get("lids_por_ano") or {}))

    saida: list[SafraCarteira] = []
    for safra in sorted(anos):
        contribuintes = [
            res for res in resultados
            if (res.get("lids_por_ano") or {}).get(safra)
        ]
        if not contribuintes:
            continue
        orcamento = 1.0 / len(contribuintes)

        pesos: dict[str, float] = {}
        universo: list[str] = []
        for res in contribuintes:
            lids = [str(t).upper() for t in (res.get("lids_por_ano") or {})[safra]]
            if not lids:
                continue
            brutos = {
                str(tk).upper(): v
                for tk, v in ((res.get("pesos_por_ano") or {}).get(safra) or {}).items()
            }
            internos = _pesos_internos_do_segmento(lids, brutos)
            for tk in lids:
                fatia = orcamento * internos[tk]
                if fatia <= 0:
                    # sem peso real: nao vira ativo fantasma em `Ativos`.
                    continue
                pesos[tk] = pesos.get(tk, 0.0) + fatia
            universo.extend(str(t).upper() for t in (res.get("tickers") or []))

        inicio, fim = janela_de_vigencia(safra)
        saida.append(SafraCarteira(
            safra=safra,
            ano_base=ano_base_do_score(safra),
            inicio=inicio,
            fim=fim,
            completa=safra_completa(safra, hoje=hoje),
            pesos=pesos,
            # dict.fromkeys preserva a ordem de insercao; sorted seria uma
            # ordem alfabetica que apaga a estrutura por segmento.
            universo=tuple(dict.fromkeys(universo)),
            segmentos=len(contribuintes),
        ))
    return saida


def _janela_de_mercado(df_precos: pd.DataFrame, inicio: pd.Timestamp,
                       fim: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Primeira e ultima data da serie inteira (nao do papel) dentro da
    janela -- as duas pontas da comparacao vem do mercado, nao do
    calendario da safra.

    `dropna(how="all")` descarta linha onde NENHUM ticker tem cotacao
    antes de olhar o indice: senao um `df_precos` vindo de
    `reindex`/`asfreq` sobre o calendario cheio faria o corte parar no
    ultimo dia do calendario, nao no ultimo dia com dado de verdade.

    `inicio_mercado = max(inicio, primeira data dentro da janela)` e
    `corte = min(fim, ultima data dentro da janela)`: com dado completo os
    dois colapsam em `inicio`/`fim`; numa safra parcial, ou com o feed
    comecando depois do inicio civil, eles se afastam.
    """
    validas = df_precos.dropna(how="all")
    dentro = validas.index[(validas.index >= inicio) & (validas.index <= fim)]
    if len(dentro) == 0:
        return inicio, fim
    return max(inicio, dentro.min()), min(fim, dentro.max())


def _preco_nas_pontas(serie: pd.Series, inicio_mercado: pd.Timestamp,
                      corte: pd.Timestamp) -> tuple[float, float] | None:
    """Primeiro e ultimo preco valido DENTRO da janela, com as pontas perto
    do inicio e do corte de mercado da safra (tolerancia de
    `TOLERANCIA_DIAS`).

    Sem o piso de proximidade, um papel que negociou em abril e maio e
    parou entregaria o retorno de um mes rotulado como o retorno da safra
    inteira -- e em quem foi deslistado esse vies e sistematicamente para
    cima. A tolerancia da ponta inicial usa `inicio_mercado` (nao o
    inicio civil): se o feed inteiro so comeca depois do inicio da safra,
    isso e cobertura de dado, nao liquidez do papel, e nao pode reprovar
    todo mundo.
    """
    dentro = serie[(serie.index >= inicio_mercado) & (serie.index <= corte)].dropna()
    dentro = dentro[dentro > 0]
    if len(dentro) < 2:
        return None
    d0, d1 = dentro.index[0], dentro.index[-1]
    tolerancia = pd.Timedelta(days=TOLERANCIA_DIAS)
    if (d0 - inicio_mercado) > tolerancia or (corte - d1) > tolerancia:
        return None
    return float(dentro.iloc[0]), float(dentro.iloc[-1])


def _retorno_selic(inicio: pd.Timestamp, fim: pd.Timestamp,
                   selic_por_ano: dict[int, float],
                   taxa_selic_aa: float) -> tuple[float, list[int]]:
    """Composto mes a mes, com a taxa do ano de cada mes -- a janela cruza
    dois anos civis e usar a taxa de um so deles distorce o benchmark.

    Devolve tambem os anos que cairam no fallback `taxa_selic_aa` (ano
    ausente do dict OU com valor `None` explicito) -- sem isso o numero sai
    igual ao de um ano observado e ninguem sabe que parte veio do default.
    """
    acumulado = 1.0
    anos_estimados: list[int] = []
    for mes in pd.date_range(inicio, fim, freq="MS"):
        ano = int(mes.year)
        valor = selic_por_ano.get(ano)
        if ano in selic_por_ano and valor is not None:
            taxa_aa = float(valor)
        else:
            taxa_aa = float(taxa_selic_aa or 0.0)
            anos_estimados.append(ano)
        acumulado *= (1.0 + taxa_aa) ** (1.0 / 12.0)
    return acumulado - 1.0, sorted(set(anos_estimados))


def retorno_da_safra(carteira: SafraCarteira, df_precos: pd.DataFrame, *,
                     selic_por_ano: dict[int, float],
                     taxa_selic_aa: float) -> dict:
    """Retorno buy-and-hold da janela de vigencia, contra Selic e equal-weight.

    Ticker sem duas cotacoes validas e proximas das pontas da janela
    (ver `_preco_nas_pontas`) rende ZERO nos dois lados da comparacao:
    seu peso entra em `peso_ausente` na estrategia, e entra com 0.0 na
    media do equal-weight -- nao e omitido dela. Omitir puniria so a
    estrategia e deixaria o benchmark mais forte que o mercado real.

    A Selic e composta so ate o `corte` de mercado da safra e a partir do
    `inicio_mercado` (ver `_janela_de_mercado`), que se afastam do
    `inicio`/`fim` civis numa safra parcial ou com feed atrasado -- do
    contrario a linha compararia 5 meses de bolsa com 12 de CDI.
    """
    inicio, fim = carteira.inicio, carteira.fim
    inicio_mercado, corte = _janela_de_mercado(df_precos, inicio, fim)

    retorno_est = 0.0
    peso_ausente = 0.0
    for tk, peso in carteira.pesos.items():
        pontas = (_preco_nas_pontas(df_precos[tk], inicio_mercado, corte)
                  if tk in df_precos.columns else None)
        if pontas is None:
            peso_ausente += float(peso)
            continue
        p0, p1 = pontas
        retorno_est += float(peso) * (p1 / p0 - 1.0)

    retornos_ew: list[float] = []
    n_com_preco = 0
    for tk in carteira.universo:
        pontas = (_preco_nas_pontas(df_precos[tk], inicio_mercado, corte)
                  if tk in df_precos.columns else None)
        if pontas is not None:
            p0, p1 = pontas
            retornos_ew.append(p1 / p0 - 1.0)
            n_com_preco += 1
        else:
            retornos_ew.append(0.0)  # simetrico a estrategia: lacuna rende 0
    retorno_ew = float(np.mean(retornos_ew)) if retornos_ew else float("nan")

    retorno_selic, selic_anos_estimados = _retorno_selic(
        inicio_mercado, corte, selic_por_ano or {}, taxa_selic_aa)
    return {
        "retorno_estrategia": retorno_est,
        "retorno_equal_weight": retorno_ew,
        "retorno_selic": retorno_selic,
        "excesso_selic": retorno_est - retorno_selic,
        "excesso_equal_weight": (retorno_est - retorno_ew
                                 if np.isfinite(retorno_ew) else float("nan")),
        "peso_ausente": peso_ausente,
        "n_universo_total": len(carteira.universo),
        "n_universo_com_preco": n_com_preco,
        "selic_anos_estimados": selic_anos_estimados,
    }


def tabela_de_safras(resultados: list[dict], df_precos: pd.DataFrame, *,
                     selic_por_ano: dict[int, float],
                     taxa_selic_aa: float,
                     hoje: pd.Timestamp | None = None) -> pd.DataFrame:
    """Uma linha por safra. `attrs['safras_completas']` lista as que podem
    entrar em media -- a safra vigente tem janela aberta e fica de fora.

    Sem safras, devolve um DataFrame vazio mas com as colunas declaradas
    (`COLUNAS_TABELA`) -- `pd.DataFrame([])` sem colunas faz qualquer leitor
    de `tabela["Safra"]` estourar `KeyError` em vez de achar um quadro
    vazio bem-formado.

    `attrs["selic_anos_estimados_por_safra"]` leva o dict `{safra: [anos]}`
    inteiro -- sem isso so a ultima safra calculada sobreviveria fora do
    loop, e a tela nao teria como avisar por safra qual trecho da Selic
    veio do fallback.
    """
    linhas: list[dict] = []
    completas: list[int] = []
    selic_estimados_por_safra: dict[int, list[int]] = {}
    for carteira in carteiras_por_safra(resultados, hoje=hoje):
        metricas = retorno_da_safra(carteira, df_precos,
                                    selic_por_ano=selic_por_ano,
                                    taxa_selic_aa=taxa_selic_aa)
        maiores = sorted(carteira.pesos.items(),
                         key=lambda kv: (-kv[1], kv[0]))[:5]
        linhas.append({
            "Safra": carteira.safra,
            "Exercício-base": carteira.ano_base,
            "Janela": f"{carteira.inicio:%m/%Y} a {carteira.fim:%m/%Y}",
            "Completa": carteira.completa,
            "Segmentos": carteira.segmentos,
            "Ativos": len(carteira.pesos),
            "Maiores posições": ", ".join(f"{tk} {p:.0%}" for tk, p in maiores),
            "Estratégia (%)": round(metricas["retorno_estrategia"] * 100, 1),
            "Equal-weight (%)": round(metricas["retorno_equal_weight"] * 100, 1),
            "Selic (%)": round(metricas["retorno_selic"] * 100, 1),
            "Excesso s/ Selic (pp)": round(metricas["excesso_selic"] * 100, 1),
            "Peso sem preço (%)": round(metricas["peso_ausente"] * 100, 1),
            "Universo com preço": (
                f"{metricas['n_universo_com_preco']}/{metricas['n_universo_total']}"
            ),
        })
        if carteira.completa:
            completas.append(carteira.safra)
        selic_estimados_por_safra[carteira.safra] = metricas["selic_anos_estimados"]

    tabela = pd.DataFrame(linhas, columns=COLUNAS_TABELA)
    tabela.attrs["safras_completas"] = completas
    tabela.attrs["selic_anos_estimados_por_safra"] = selic_estimados_por_safra
    return tabela
