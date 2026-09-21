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

Regras de medicao do retorno (rodada de correcao 3, revisao de codigo):

- **A Selic capitaliza por dias corridos, nao por fronteira de mes.**
  Contar fronteiras de mes em `pd.date_range(..., freq="MS")` so acerta
  quando o indice de `df_precos` e fim de mes; com indice de inicio de mes
  (o fallback `yf.download(..., interval="1mo")` de
  `views/empresas_b3.py:497,504` devolve isso) o mesmo "12 meses" escondia
  quase 1 pp de diferenca real. `_dias_por_ano_civil` reparte
  `(corte - inicio_mercado)` em dias corridos por ano civil, e cada pedaco
  capitaliza `(1 + taxa_aa) ** (dias / 365.0)` -- estavel a convencao do
  indice, coberto por
  `test_selic_e_estavel_entre_convencao_fim_de_mes_e_inicio_de_mes`.
- **A linha da tabela e validada contra `COLUNAS_TABELA` no ponto em que e
  montada.** `tabela_de_safras` levanta `ValueError` se o dict da linha
  tiver uma chave a mais ou a menos que `COLUNAS_TABELA` -- fecha as duas
  direcoes do desalinhamento (coluna declarada sem chave, e chave nova sem
  coluna) no mesmo lugar onde o bug nasceria.

Regras de medicao do retorno (rodada de correcao 4, revisao de codigo):

- **Safra sem nenhum pregao observado NAO e mensuravel.** Quando
  `n_universo_com_preco == 0` (df vazio, janela 100% NaN, ou nenhum ticker
  do universo com coluna de preco), `_janela_de_mercado` recai nas pontas
  CIVIS e a Selic capitalizava a janela inteira contra uma estrategia e um
  equal-weight zerados -- a linha publicava algo como
  `Excesso s/ Selic = -12,0 pp` sem UM UNICO pregao medido, indistinguivel
  na tela de uma safra real que perdeu 12 pontos. Agora `retorno_estrategia`,
  `retorno_equal_weight`, `retorno_selic`, `excesso_selic` e
  `excesso_equal_weight` saem como `None` (e `NaN` nas colunas), e o dict
  ganha `mensuravel: bool`.

  Isso NAO contradiz a regra "ticker sem preco rende zero, nao redistribuir
  entre sobreviventes": aquela regra fala de um ticker ausente DENTRO de
  uma janela mensuravel. Uma janela em que nenhum ativo foi observado nao e
  esse caso -- nao ha o que medir. `peso_ausente` e `n_universo_*` continuam
  saindo com numero, porque sao contagem do observado, nao retorno fabricado.

  O criterio e `n_universo_com_preco > 0` (e nao "algum ticker de `pesos`
  com preco") porque `universo` e, por construcao de `carteiras_por_safra`,
  superconjunto das chaves de `pesos`: os lideres vem dos `tickers` do
  mesmo segmento. Universo vazio (`tickers=[]`) cai no mesmo caminho, em
  vez de publicar `Equal-weight (%) = NaN` sozinho numa linha que parece
  medida.

- **A marcacao e a exclusao reaproveitam a gramatica da safra parcial.**
  A coluna `"Mensurável"` (bool) fica ao lado de `"Completa"`, e
  `attrs["safras_completas"]` exige `completa AND mensuravel` -- uma safra
  de janela civil fechada mas sem pregao nenhum nao pode entrar nas medias
  da Task 5. Duas colunas booleanas, uma lista de agregaveis: a tela nao
  precisa aprender uma segunda gramatica de "nao conte esta linha".

- **`_dias_por_ano_civil` tem trava de progresso.** O laco era um `while`
  aberto: um off-by-one natural (`min(fim, fim_do_ano)` sem o `+1 day`)
  parava de avancar e a SUITE TRAVAVA em laco infinito em vez de ficar
  vermelha. Agora o laco e limitado a um pedaco por ano civil da janela e,
  se sobrar janela sem cobrir, levanta `ValueError` -- o erro aparece como
  erro.

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
    "Safra", "Exercício-base", "Janela", "Completa", "Mensurável",
    "Segmentos", "Ativos", "Maiores posições", "Estratégia (%)",
    "Equal-weight (%)", "Selic (%)", "Excesso s/ Selic (pp)",
    "Peso sem preço (%)", "Universo com preço",
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


def _proxima_virada_de_ano(cursor: pd.Timestamp,
                           fim: pd.Timestamp) -> pd.Timestamp:
    """Fim do pedaco de `[cursor, fim)` que ainda cabe no ano de `cursor`.

    O `+1 day` sobre 31/12 e o que faz o cursor SAIR do ano corrente: sem
    ele o proximo passo volta a apontar para a mesma data e o laco nao
    progride (ver `_dias_por_ano_civil`).
    """
    fim_do_ano = pd.Timestamp(year=cursor.year, month=12, day=31)
    return min(fim, fim_do_ano + pd.Timedelta(days=1))


def _dias_por_ano_civil(inicio: pd.Timestamp, fim: pd.Timestamp) -> dict[int, int]:
    """Reparte os dias corridos de `[inicio, fim)` pelo ano civil de cada um.

    A janela pode cruzar a virada do ano; cada pedaco tem que ir para o
    ano certo para a Selic compor com a taxa daquele ano.

    O laco e LIMITADO a um pedaco por ano civil da janela, e o que sobrar
    sem cobrir vira `ValueError`. Um `while` aberto transformava um
    off-by-one em `_proxima_virada_de_ano` (o classico `min(fim,
    fim_do_ano)` sem o `+1 day`) em laco infinito: a suite TRAVAVA em vez
    de ficar vermelha, e travamento nao aponta para o defeito.
    """
    dias: dict[int, int] = {}
    cursor = inicio
    for _ in range(inicio.year, fim.year + 1):
        if cursor >= fim:
            break
        proximo = _proxima_virada_de_ano(cursor, fim)
        dias[cursor.year] = dias.get(cursor.year, 0) + (proximo - cursor).days
        cursor = proximo
    if cursor < fim:
        raise ValueError(
            "_dias_por_ano_civil nao cobriu a janela inteira -- parou em "
            f"{cursor} com fim={fim}; _proxima_virada_de_ano nao progrediu"
        )
    return dias


def _retorno_selic(inicio: pd.Timestamp, fim: pd.Timestamp,
                   selic_por_ano: dict[int, float],
                   taxa_selic_aa: float) -> tuple[float, list[int]]:
    """Composto por FRACAO DE ANO em dias corridos, com a taxa do ano de
    cada pedaco -- a janela cruza dois anos civis e usar a taxa de um so
    deles distorce o benchmark.

    Contar fronteiras de mes (`pd.date_range(..., freq="MS")`, a versao
    anterior) acerta so quando o indice de precos e fim de mes, mas o
    resultado depende da convencao do quadro que chega -- e nem sempre e
    fim de mes: `yf.download(..., interval="1mo")` devolve indice de INICIO
    de mes (ver `views/empresas_b3.py:497,504`), e nesse caso 12 fronteiras
    de mes por um lado e por outro escondiam quase um ponto percentual de
    diferenca real em dias corridos por tras do mesmo "12 meses". Medir em
    `(fim - inicio).days / 365.0` e estavel a convencao do indice.

    Devolve tambem os anos que cairam no fallback `taxa_selic_aa` (ano
    ausente do dict OU com valor `None` explicito) -- sem isso o numero sai
    igual ao de um ano observado e ninguem sabe que parte veio do default.
    """
    if fim <= inicio:
        return 0.0, []
    acumulado = 1.0
    anos_estimados: list[int] = []
    for ano, dias in sorted(_dias_por_ano_civil(inicio, fim).items()):
        valor = selic_por_ano.get(ano)
        if ano in selic_por_ano and valor is not None:
            taxa_aa = float(valor)
        else:
            taxa_aa = float(taxa_selic_aa or 0.0)
            anos_estimados.append(ano)
        acumulado *= (1.0 + taxa_aa) ** (dias / 365.0)
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

    Se NENHUM ticker do universo foi observado na janela
    (`n_universo_com_preco == 0`), a safra nao e mensuravel: os cinco
    retornos saem `None` e `mensuravel` e `False`. Publicar numero ali
    seria capitalizar a Selic contra dois zeros e chamar o resultado de
    "excesso" -- um valor fabricado sem um unico pregao medido.
    """
    inicio, fim = carteira.inicio, carteira.fim
    inicio_mercado, corte = _janela_de_mercado(df_precos, inicio, fim)

    def _pontas(tk: str) -> tuple[float, float] | None:
        if tk not in df_precos.columns:
            return None
        return _preco_nas_pontas(df_precos[tk], inicio_mercado, corte)

    retorno_est = 0.0
    peso_ausente = 0.0
    for tk, peso in carteira.pesos.items():
        pontas = _pontas(tk)
        if pontas is None:
            peso_ausente += float(peso)
            continue
        p0, p1 = pontas
        retorno_est += float(peso) * (p1 / p0 - 1.0)

    retornos_ew: list[float] = []
    n_com_preco = 0
    for tk in carteira.universo:
        pontas = _pontas(tk)
        if pontas is not None:
            p0, p1 = pontas
            retornos_ew.append(p1 / p0 - 1.0)
            n_com_preco += 1
        else:
            retornos_ew.append(0.0)  # simetrico a estrategia: lacuna rende 0

    # Contagem do observado: vale tanto na safra mensuravel quanto na que
    # nao foi medida -- e o que a coluna "Universo com preço" mostra.
    cobertura = {
        "peso_ausente": peso_ausente,
        "n_universo_total": len(carteira.universo),
        "n_universo_com_preco": n_com_preco,
    }

    if n_com_preco == 0:
        return {
            "retorno_estrategia": None,
            "retorno_equal_weight": None,
            "retorno_selic": None,
            "excesso_selic": None,
            "excesso_equal_weight": None,
            "selic_anos_estimados": [],
            "mensuravel": False,
            **cobertura,
        }

    retorno_ew = float(np.mean(retornos_ew))
    retorno_selic, selic_anos_estimados = _retorno_selic(
        inicio_mercado, corte, selic_por_ano or {}, taxa_selic_aa)
    return {
        "retorno_estrategia": retorno_est,
        "retorno_equal_weight": retorno_ew,
        "retorno_selic": retorno_selic,
        "excesso_selic": retorno_est - retorno_selic,
        "excesso_equal_weight": (retorno_est - retorno_ew
                                 if np.isfinite(retorno_ew) else float("nan")),
        "selic_anos_estimados": selic_anos_estimados,
        "mensuravel": True,
        **cobertura,
    }


def _pct(valor: float | None) -> float:
    """Fracao -> percentual com 1 casa; `None` (safra nao mensuravel) -> NaN.

    `round(None * 100, 1)` estouraria TypeError; e o pior seria "consertar"
    com `0.0`, que publica um numero onde nao houve medicao nenhuma.
    """
    if valor is None:
        return float("nan")
    return round(float(valor) * 100, 1)


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

    Uma safra sem nenhum pregao observado sai com `Mensurável = False` e
    `NaN` nas colunas de retorno, e fica fora de `safras_completas` mesmo
    com a janela civil fechada -- mesma gramatica da safra vigente, para a
    tela nao ter duas maneiras diferentes de dizer "nao conte esta linha".
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
        linha = {
            "Safra": carteira.safra,
            "Exercício-base": carteira.ano_base,
            "Janela": f"{carteira.inicio:%m/%Y} a {carteira.fim:%m/%Y}",
            "Completa": carteira.completa,
            "Mensurável": bool(metricas["mensuravel"]),
            "Segmentos": carteira.segmentos,
            "Ativos": len(carteira.pesos),
            "Maiores posições": ", ".join(f"{tk} {p:.0%}" for tk, p in maiores),
            "Estratégia (%)": _pct(metricas["retorno_estrategia"]),
            "Equal-weight (%)": _pct(metricas["retorno_equal_weight"]),
            "Selic (%)": _pct(metricas["retorno_selic"]),
            "Excesso s/ Selic (pp)": _pct(metricas["excesso_selic"]),
            "Peso sem preço (%)": _pct(metricas["peso_ausente"]),
            "Universo com preço": (
                f"{metricas['n_universo_com_preco']}/{metricas['n_universo_total']}"
            ),
        }
        faltando = set(COLUNAS_TABELA) - set(linha)
        sobrando = set(linha) - set(COLUNAS_TABELA)
        if faltando or sobrando:
            raise ValueError(
                "linha da tabela desalinhada de COLUNAS_TABELA -- "
                f"faltando={faltando} sobrando={sobrando}"
            )
        linhas.append(linha)
        # Mesma exclusao da safra vigente: janela civil fechada nao basta,
        # a janela tambem precisa ter sido medida para entrar em media.
        if carteira.completa and metricas["mensuravel"]:
            completas.append(carteira.safra)
        selic_estimados_por_safra[carteira.safra] = metricas["selic_anos_estimados"]

    tabela = pd.DataFrame(linhas, columns=COLUNAS_TABELA)
    tabela.attrs["safras_completas"] = completas
    tabela.attrs["selic_anos_estimados_por_safra"] = selic_estimados_por_safra
    return tabela
