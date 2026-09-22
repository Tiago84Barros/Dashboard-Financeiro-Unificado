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

Task 6 -- a expectativa sai como banda e fragilidade, nunca como numero:

- **`bootstrap_excesso` tem semente fixa por REPRODUTIBILIDADE, nao por
  estabilidade.** Rodar de novo com outra semente so troca o ruido de
  Monte Carlo; nao responde se a conclusao depende de uma safra
  especifica. Quem responde isso e `fragilidade_leave_one_out`. Com menos
  de duas safras a banda sai `(None, None)` -- sem dispersao observada nao
  ha intervalo, e um ponto central sozinho seria numero sem medicao.
- **O leave-one-out recalcula o p-valor em cada subamostra.**
  `classify_evidence` so pode declarar `evidencia_a_favor` quando recebe
  `p_value`; chamado sem ele (o rascunho da task), o classificador ficaria
  preso em "inconclusivo (sem significancia)" e o LOO daria ZERO safras
  que viram para QUALQUER amostra positiva -- publicando como robustez
  medida uma insensibilidade do proprio criterio. `_p_valor_unilateral`
  fecha isso, e o caso esta preso por
  `test_leave_one_out_deriva_significancia_de_cada_subamostra`.

Rodada de correcao 1 da Task 6 -- a mesma familia de defeito nos tres
achados: a tela publicava numero em verde sem evidencia medida atras.

- **Um criterio so, `veredito_do_rank_ic` (F-1).** O card "Ordena?"
  classificava SEM p-valor e o leave-one-out COM: dois criterios homonimos
  com vereditos opostos lado a lado na mesma tela. Os dois leem a mesma
  funcao agora, e `test_veredito_do_rank_ic_e_o_mesmo_que_o_leave_one_out_usa`
  compara os caminhos entre si -- a regra certa num leitor so nao cobre o
  outro leitor.
- **Piso de amostra no leave-one-out, `MIN_SAFRAS_LOO` (F-2).** Zero safras
  que viram tem duas causas OPOSTAS: evidencia robusta, ou amostra pequena
  demais para haver o que remover. So a amostra vazia era distinguida; 1, 2
  e 3 caiam no ramo que parece robustez e saiam em verde.
- **`scipy` no topo, sem fallback (F-3).** Ver o comentario do import.
- **Guarda de dispersao relativa (F-4)** e **direcao/`ddof` presos por
  teste contra `scipy.stats.ttest_1samp` (F-5).**

Rodada de correcao 2 -- o defeito maior estava na POPULACAO, um bloco
acima (ver `views/portfolio_b3_safras.py`), e aqui ficam as duas metades
que sao do motor:

- **a justificativa do piso era falsa e a medicao a desmentiu.** Ver o
  comentario de `MIN_SAFRAS_LOO`: o valor 4 continua, o motivo mudou, e
  agora ele sai de uma tabela medida e nao de uma aritmetica de
  `min_anos`.
- **contagem nao mede margem.** O leave-one-out passa a publicar a banda
  `[min_i p(sem_i), max_i p(sem_i)]`, que e o que diz quao perto de
  `alpha` a conclusao passou, e `robusto` exige veredito CONCLUSIVO --
  50,8% dos casos "Inconclusivo" saiam com "0 safra(s)" em VERDE ao lado,
  afirmando robustez da ignorancia.

Modulo puro: sem streamlit, sem banco. Coberto por tests/test_b3_safras.py.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# O teste t NAO e reimplementado aqui: vem de `core.b3_evidence`, que e a
# unica casa da distribuicao e da guarda de dispersao nos tres modulos do
# veredito. Duas copias da mesma regra nao ficam iguais -- foi exatamente o
# que aconteceu com a guarda de dispersao ate 2026-09.
from core.b3_evidence import teste_t_unilateral
from core.b3_vigencia import ano_base_do_score, janela_de_vigencia, safra_completa

# A serie de precos e mensal: 45 dias cobre uma folga de um mes de atraso
# na cotacao e nao mais que isso -- ver docstring do modulo.
TOLERANCIA_DIAS = 45

# Piso de safras para o leave-one-out publicar veredito.
#
# A justificativa da rodada 1 estava ERRADA e a medicao a desmentiu: dizia
# que a subamostra de 2 era "o limite exato do mensuravel", e ela e
# perfeitamente mensuravel -- vira veredito em ~64% dos casos. O limite
# duro e a subamostra de 1, que cai em SEM_AMPLITUDE e torna o resultado
# constante.
#
# O piso 4 sobrevive por outro motivo, esse sim medido (20.000 amostras,
# Rank-IC ~ N(mu, 0,15), taxa de virada do LOO por mu):
#
#   n=2   mu=0 41,4%  mu=0,10 33,6%  mu=0,20 45,0%  -> amplitude 0,113
#   n=3   mu=0 64,3%  mu=0,10 63,8%  mu=0,20 65,1%  -> amplitude 0,012
#   n=4   mu=0 61,4%  mu=0,10 57,6%  mu=0,20 39,8%  -> amplitude 0,216
#   n=8   mu=0 49,2%  mu=0,10 38,2%  mu=0,20  4,9%  -> amplitude 0,443
#
# Em n=3 a taxa de virada e a MESMA para sinal nulo e para sinal forte: o
# numero existe, mas nao carrega informacao nenhuma sobre a verdade. n=4 e
# o primeiro tamanho em que a medicao discrimina. Abaixo dele a tela nao
# publica o numero -- e nao publica verde.
MIN_SAFRAS_LOO = 4

# Nivel do teste, o mesmo default de `classify_evidence`. A banda de
# p-valores do leave-one-out e lida contra ele.
ALPHA_EVIDENCIA = 0.10

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
    """Fracao -> percentual, SEM arredondar; `None` (safra nao mensuravel)
    -> NaN.

    `None * 100` estouraria TypeError; e o pior seria "consertar" com
    `0.0`, que publica um numero onde nao houve medicao nenhuma.

    Nao arredonda de proposito (A-T7-02). Esta coluna nao e so exibida:
    ela e a ENTRADA de quem mede -- o Bloco 2 subtrai os dois lados para
    dimensionar o vies de universo, e o Bloco 3 reamostra o excesso. Com
    `round(..., 1)` aqui, retornos de 13,04 / 13,02 / 12,97 chegavam ao
    teste como `[3.0, 3.0, 3.0]`: dispersao real apagada, `p` nao
    calculavel e a tela afirmando "nao tem dispersao entre si" sobre um
    dado que tem. Arredondamento e do FORMATADOR
    (`st.column_config.NumberColumn(format="%.1f")`), nunca do valor.
    """
    if valor is None:
        return float("nan")
    return float(valor) * 100


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


def bootstrap_excesso(excessos: list[float], *,
                      n_reamostras: int = 10_000,
                      seed: int = 20260921
                      ) -> tuple[float | None, float | None]:
    """Intervalo de 95% do excesso medio por safra, por reamostragem.

    Semente fixa: o mesmo conjunto de safras tem que dar o mesmo intervalo
    entre execucoes. A fragilidade da conclusao NAO se mede re-semeando --
    trocar a semente so troca o ruido de Monte Carlo. Quem mede se a
    conclusao depende de uma safra especifica e `fragilidade_leave_one_out`.

    Com menos de duas safras nao ha dispersao a reamostrar e a funcao
    devolve `(None, None)`: melhor a tela mostrar "—" do que publicar um
    ponto central que a amostra nao sustenta.
    """
    valores = np.asarray([float(v) for v in (excessos or [])
                          if v is not None and np.isfinite(float(v))],
                         dtype=float)
    if len(valores) < 2:
        return (None, None)
    rng = np.random.default_rng(seed)
    medias = rng.choice(valores, size=(n_reamostras, len(valores)),
                        replace=True).mean(axis=1)
    return (float(np.percentile(medias, 2.5)),
            float(np.percentile(medias, 97.5)))


def _ic_limpos(ic_values: list[float] | None) -> list[float]:
    """Rank-ICs finitos, na ordem de entrada. Ano sem IC calculavel chega
    como `None`/`NaN` e nao e observacao."""
    return [float(v) for v in (ic_values or [])
            if v is not None and np.isfinite(float(v))]


def _p_valor_unilateral(valores: list[float] | None) -> float | None:
    """p-valor do teste t unilateral A DIREITA de `media > 0` sobre os
    Rank-ICs, com `ddof=1`.

    `classify_evidence` so tem como declarar `evidencia_a_favor` quando
    recebe `p_value` -- sem ele o classificador nunca sai de "inconclusivo
    (sem significancia)" para uma amostra positiva, por mais forte que ela
    seja. Chamar o classificador sem p-valor faria a fragilidade dar ZERO
    sempre, e zero ali seria lido como robustez: exatamente a conclusao que
    esta funcao existe para poder contradizer.

    A conta em si NAO mora aqui: e `core.b3_evidence.teste_t_unilateral`, a
    mesma funcao que o bloco "Evidencia no universo" chama. Enquanto eram
    duas copias, a guarda de dispersao divergiu (aqui relativa, la
    `desvio > 0`) e os dois cards publicavam vereditos opostos sobre os
    MESMOS Rank-ICs anuais -- p = 5,0e-61 de um lado, "Inconclusivo" do
    outro.
    """
    return teste_t_unilateral(_ic_limpos(valores))[1]


def veredito_do_rank_ic(ic_values: list[float] | None):
    """Criterio UNICO de veredito sobre o Rank-IC das safras.

    Existe para que a tela e o leave-one-out nao tenham dois criterios
    homonimos. Antes, o card "Ordena?" chamava `classify_evidence` SEM
    p-valor e o LOO chamava COM: com Rank-IC ~0,30 em 6 safras a tela
    imprimia "Inconclusivo" enquanto o motor interno concluia
    `evidencia_a_favor`, e o card de fragilidade dava selo verde a um
    veredito que a tela nao mostrava e que contradizia o card ao lado.

    Unificar no criterio sem p-valor "resolveria" a divergencia pelo lado
    errado: `evidencia_a_favor` viraria inalcancavel e o card nunca sairia
    de "Inconclusivo", por mais forte que a amostra fosse.
    """
    from core.b3_evidence import classify_evidence

    limpos = _ic_limpos(ic_values)
    return classify_evidence(ic_values=limpos,
                             p_value=_p_valor_unilateral(limpos))


def fragilidade_leave_one_out(ic_values: list[float] | None) -> dict:
    """Quantas safras precisam sair para o veredito virar, e com que margem.

    Reusa `veredito_do_rank_ic` -- o MESMO criterio que a tela publica no
    card "Ordena?", para que o veredito do LOO seja comparavel ao veredito
    publicado, e nao um segundo criterio parecido.

    Cada passo recalcula o p-valor sobre a PROPRIA subamostra: o ponto do
    leave-one-out e justamente que tirar uma safra muda a significancia.
    Reaproveitar o p-valor do conjunto inteiro (ou omiti-lo) daria sempre
    "zero safras que viram", e a tela publicaria robustez que nao foi
    medida.

    Abaixo de `MIN_SAFRAS_LOO` devolve `medido=False` e nao publica
    veredito -- ver o comentario da constante para a medicao que sustenta
    o valor.

    Alem da contagem, publica a BANDA de p-valores do leave-one-out
    (`p_banda = (min_i p(sem_i), max_i p(sem_i))`): e ela que diz a margem
    da conclusao ate `alpha`, e contagem sozinha nao diz. `robusto` exige
    as tres coisas -- medido, nenhuma virada, e veredito CONCLUSIVO.

    Sobre `banda_de_um_lado`: hoje ela e implicada pelas outras duas
    condicoes, e isso foi medido (20.000 amostras com n de 4 a 16; dos
    12.770 casos conclusivos com zero viradas, ZERO tinham a banda
    atravessando alpha). A implicacao e analitica -- se nenhuma subamostra
    virou e o estado e `a_favor`, todo `p(sem_i)` ja esta abaixo de alpha
    por definicao. Fica como condicao explicita porque o criterio de
    `classify_evidence` pode deixar de ser o p-valor, e porque a banda
    publicada e informacao real para quem le o card.
    """
    limpos = _ic_limpos(ic_values)
    completo = veredito_do_rank_ic(limpos).estado
    conclusivo = completo != "inconclusivo"
    if len(limpos) < MIN_SAFRAS_LOO:
        return {
            "estado_completo": completo,
            "estados_loo": [],
            "safras_que_viram": 0,
            "medido": False,
            "n_safras": len(limpos),
            "p_loo": [],
            "p_banda": (None, None),
            "banda_de_um_lado": None,
            "conclusivo": conclusivo,
            "robusto": False,
            "alpha": ALPHA_EVIDENCIA,
        }
    subamostras = [limpos[:i] + limpos[i + 1:] for i in range(len(limpos))]
    estados = [veredito_do_rank_ic(sub).estado for sub in subamostras]
    ps = [_p_valor_unilateral(sub) for sub in subamostras]
    viram = sum(1 for e in estados if e != completo)
    if any(p is None for p in ps):
        p_banda: tuple[float | None, float | None] = (None, None)
        um_lado: bool | None = None
    else:
        p_banda = (min(ps), max(ps))
        um_lado = (p_banda[1] < ALPHA_EVIDENCIA
                   or p_banda[0] >= ALPHA_EVIDENCIA)
    return {
        "estado_completo": completo,
        "estados_loo": estados,
        "safras_que_viram": viram,
        "medido": True,
        "n_safras": len(limpos),
        "p_loo": ps,
        "p_banda": p_banda,
        "banda_de_um_lado": um_lado,
        "conclusivo": conclusivo,
        "robusto": bool(viram == 0 and conclusivo and um_lado is True),
        "alpha": ALPHA_EVIDENCIA,
    }
