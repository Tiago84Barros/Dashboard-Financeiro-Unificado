"""
core/us_benchmark.py
Benchmark de mercado do módulo Empresas Americanas: mapa estável rótulo→símbolo
e a série de retorno total que a validação histórica (core.us_backtest) compara
com a estratégia.

Por que este módulo existe: o seletor "Benchmark de mercado" da tela de criação
era gravado na carteira e reexibido como métrica, mas o backtest comparava a
estratégia SÓ com o equal-weight do universo elegível. Trocar SPY por QQQ não
mudava um único número — o usuário lia "Nasdaq-100 (QQQ)" ao lado de um excesso
que nunca viu o QQQ. Aqui o rótulo vira símbolo, o símbolo vira série e a série
vira retorno realizado nas MESMAS datas de decisão do backtest.

Contrato da série (unidade, período, fonte, ausência):

* fonte: ``market_us.prices_monthly.adjusted_close`` — lida por
  ``core.us_read.load_precos_mensais_us`` (mesmo caminho do resto do módulo,
  nenhuma consulta nova);
* unidade: preço ajustado, ou seja, retorno TOTAL (dividendo reinvestido), o que
  a rubrica exige de um índice de renda variável;
* moeda: USD, a mesma do painel de scores — as duas pernas saem da mesma tabela,
  então não há conversão cambial e nenhuma é aplicada;
* período: uma observação por mês (fechamento do último pregão do mês);
* ausência: data de decisão sem preço nas duas pontas é DESCARTADA do pareamento
  e contabilizada. Nunca vira 0%, nunca é interpolada, e nunca é substituída
  pelo equal-weight.

Falha fechada: benchmark fora do mapa, sem série publicada ou com interseção
temporal abaixo do piso devolve estado de erro nomeado. Quem consome não recebe
métrica de excesso nenhuma nesses casos — omissão é mais honesta que um número
calculado contra outra coisa.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

logger = logging.getLogger("us_benchmark")

# Versão do mapa rótulo→símbolo. As carteiras salvas guardam o RÓTULO
# (core.us_portfolio_creation.Params.benchmark), então o mapa é contrato
# público: entrar, sair ou trocar o proxy de um índice muda a leitura
# retroativa de tudo que já foi gravado e exige subir esta versão.
US_BENCHMARK_MAP_VERSION = "1.0.0"

MOEDA = "USD"
FONTE_SERIE = "market_us.prices_monthly.adjusted_close (retorno total, USD, mensal)"

# Opção do seletor que declara explicitamente NÃO comparar com índice.
SEM_INDICE_ROTULO = "Pesos iguais do universo"

# Rótulos exatamente como aparecem no selectbox de views/empresas_americanas.py.
# A ordem é a da UI e faz parte do contrato (o primeiro item é o default).
US_BENCHMARKS: dict[str, str | None] = {
    "S&P 500 (SPY)": "SPY",
    "Russell 1000 (IWB)": "IWB",
    "Nasdaq-100 (QQQ)": "QQQ",
    SEM_INDICE_ROTULO: None,
}
BENCHMARK_LABELS: tuple[str, ...] = tuple(US_BENCHMARKS)

# Estados nomeados que a UI pode exibir sem interpretar exceção.
ERRO_DESCONHECIDO = "benchmark_desconhecido"
ERRO_SEM_SERIE = "benchmark_sem_serie"
ERRO_INTERSECAO_INSUFICIENTE = "benchmark_intersecao_insuficiente"

MODO_INDICE = "indice"
MODO_SEM_INDICE = "sem_indice"
MODO_ERRO = "erro"

# Piso de observações do pareamento. É o mesmo piso que o módulo já aplica à sua
# estatística de corte transversal (core.us_backtest.rank_ic exige 3 pontos):
# com 1 ou 2 janelas, "excesso anualizado" é anedota com casas decimais.
MIN_OBS_BENCHMARK = 3

# Preços mensais são observações de fechamento. O endpoint de um retorno deve
# pertencer exatamente ao mês-alvo: tolerância de zero meses. Aceitar "o
# primeiro preço depois" faria uma lacuna em junho virar retorno de 13 meses,
# mas as estatísticas do painel o anualizariam como se tivesse 12.
TOLERANCIA_ENDPOINT_MESES = 0


@dataclass(frozen=True)
class SelecaoBenchmark:
    """Rótulo da UI já resolvido para símbolo negociável.

    ``simbolo is None`` significa escolha DELIBERADA de não usar índice (a opção
    "Pesos iguais do universo"), que é diferente de escolha inválida — esta
    devolve ``None`` em ``resolver`` e vira erro nomeado.
    """

    rotulo: str
    simbolo: str | None

    @property
    def usa_indice(self) -> bool:
        return bool(self.simbolo)


def resolver(escolha: "str | SelecaoBenchmark | None") -> SelecaoBenchmark | None:
    """Rótulo da UI (ou símbolo cru) → ``SelecaoBenchmark``; ``None`` se desconhecido.

    Aceita o rótulo exato do selectbox, o mesmo rótulo com caixa/espaços
    diferentes e o símbolo cru ("SPY"), porque CLI e scripts passam símbolo
    enquanto a carteira grava rótulo. Ausência de escolha é tratada como "sem
    índice", nunca como um índice arbitrário.
    """
    if escolha is None:
        return SelecaoBenchmark(SEM_INDICE_ROTULO, None)
    if isinstance(escolha, SelecaoBenchmark):
        return escolha
    texto = str(escolha).strip()
    if not texto:
        return SelecaoBenchmark(SEM_INDICE_ROTULO, None)
    if texto in US_BENCHMARKS:
        return SelecaoBenchmark(texto, US_BENCHMARKS[texto])
    alvo = texto.casefold()
    for rotulo, simbolo in US_BENCHMARKS.items():
        if rotulo.casefold() == alvo:
            return SelecaoBenchmark(rotulo, simbolo)
    for rotulo, simbolo in US_BENCHMARKS.items():
        if simbolo and simbolo.casefold() == alvo:
            return SelecaoBenchmark(rotulo, simbolo)
    return None


def _loader_padrao(simbolos: tuple[str, ...]) -> pd.DataFrame:
    from core.us_read import load_precos_mensais_us

    return load_precos_mensais_us(simbolos)


def _higieniza_serie(bruto, simbolo: str) -> pd.Series:
    """Formato largo (datas × símbolos) ou Series → Series de preço limpa.

    Preço ≤ 0 é descartado: não existe preço não positivo de índice, e mantê-lo
    produziria retorno absurdo em vez de lacuna. Data ilegível também sai — vira
    ausência declarada, nunca um ponto no lugar errado da linha do tempo.
    """
    vazia = pd.Series(dtype="float64")
    if bruto is None:
        return vazia
    if isinstance(bruto, pd.DataFrame):
        if bruto.empty:
            return vazia
        alvo = str(simbolo).strip().upper()
        coluna = next((c for c in bruto.columns if str(c).strip().upper() == alvo), None)
        if coluna is None:
            return vazia
        serie = bruto[coluna]
    elif isinstance(bruto, pd.Series):
        serie = bruto
    else:
        return vazia
    serie = pd.Series(serie).copy()
    if len(serie) == 0:
        return vazia
    datas = pd.to_datetime(pd.Series(list(serie.index)), errors="coerce")
    # A fonte é mensal; normalizar para o fechamento do mês torna comparáveis
    # timestamps com hora/fuso sem permitir que o pareamento escorregue para o
    # mês seguinte.
    serie.index = datas.dt.to_period("M").dt.to_timestamp("M").values
    serie = pd.to_numeric(serie, errors="coerce")
    serie = serie[pd.notna(serie.index)].dropna()
    if not serie.empty:
        valores = serie.to_numpy(dtype="float64")
        serie = serie[np.isfinite(valores) & (valores > 0)]
    if serie.empty:
        return vazia
    # Duplicata de data mantém a ÚLTIMA leitura (mesma convenção de
    # load_precos_mensais_us, que agrega com aggfunc="last").
    serie = serie.sort_index()
    return serie[~serie.index.duplicated(keep="last")]


def serie_total_return(simbolo: str, *, loader=None) -> pd.Series:
    """Série mensal de preço total-return do símbolo, indexada por data.

    ``loader`` é injetável (mesmo padrão de core.global_portfolio.factors) e
    recebe uma tupla de símbolos, devolvendo o formato largo de
    ``core.us_read.load_precos_mensais_us`` (datas × símbolos). Os testes passam
    série sintética por aqui, sem banco e sem rede.

    Série vazia é resposta legítima (símbolo não publicado) — quem chama a
    transforma em erro nomeado.
    """
    if not simbolo:
        return pd.Series(dtype="float64")
    fn = loader or _loader_padrao
    try:
        bruto = fn((str(simbolo).strip().upper(),))
    except (ConnectionError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.warning("serie_total_return(%s) falhou: %s", simbolo, exc)
        return pd.Series(dtype="float64")
    return _higieniza_serie(bruto, simbolo)


def _normaliza_data(valor) -> pd.Timestamp | None:
    try:
        ts = pd.to_datetime(valor)
    except (TypeError, ValueError, OverflowError):
        return None
    if isinstance(ts, pd.Timestamp) and not pd.isna(ts):
        return ts
    return None


def horizonte_padrao(periods_per_year: int) -> int:
    """Horizonte, em meses, de um período do painel.

    O painel do backtest é anual (``periods_per_year=1`` → 12 meses de retorno
    futuro) mas o mesmo motor aceita painel mensal (12 → 1 mês). Derivar daqui
    evita que o benchmark meça 12 meses enquanto a estratégia mede 1.
    """
    try:
        ppy = int(periods_per_year)
    except (TypeError, ValueError):
        return 12
    if ppy <= 0:
        return 12
    return max(1, int(round(12 / ppy)))


def retornos_realizados(serie: pd.Series, datas, *, horizonte_meses: int) -> pd.Series:
    """Retorno do benchmark entre cada data de decisão e ``horizonte_meses`` depois.

    Convenção IDÊNTICA à frequência mensal do painel da estratégia
    (``data_pipeline.us.scoring_history.build_annual_panel``): datas e preços
    são normalizados ao fechamento de cada mês; ``p0`` deve existir no mês da
    decisão e ``p1`` deve existir exatamente no mês ``horizonte_meses`` depois
    (tolerância de zero meses). Uma lacuna não é preenchida pelo mês seguinte:
    isso converteria, por exemplo, 13 meses em retorno rotulado como 12 e
    anualizado incorretamente. Sem esse pareamento, o excesso compararia janelas
    diferentes e o número diria mais sobre o calendário do que sobre a estratégia.

    O índice do resultado são os MESMOS rótulos de data recebidos (o painel usa
    string ou date conforme a origem), para alinhar com a série de retornos da
    carteira sem reindexação implícita. Datas sem uma das pontas ficam de fora.
    """
    dados = _higieniza_serie(serie, "")
    if dados.empty:
        return pd.Series(dtype="float64")
    try:
        horizonte = int(horizonte_meses)
    except (TypeError, ValueError):
        return pd.Series(dtype="float64")
    if horizonte <= 0:
        return pd.Series(dtype="float64")
    idx = pd.DatetimeIndex(dados.index)
    valores: list[float] = []
    rotulos: list = []
    for rotulo in datas:
        ts = _normaliza_data(rotulo)
        if ts is None:
            continue
        # Decisões e preços mensais são comparados no fechamento normalizado do
        # respectivo mês. A ponta final deve estar NO mês-alvo (tolerância de
        # zero meses), nunca no primeiro mês posterior disponível.
        base = ts.to_period("M").to_timestamp("M")
        passado = idx[idx == base]
        if len(passado) != 1:
            continue
        alvo = (base + pd.DateOffset(months=horizonte)).to_period("M").to_timestamp("M")
        futuro = idx[idx == alvo]
        if len(futuro) != 1:
            continue
        p0 = float(dados.loc[passado[0]])
        p1 = float(dados.loc[futuro[0]])
        if not np.isfinite(p0) or not np.isfinite(p1) or p0 <= 0:
            continue
        retorno = p1 / p0 - 1.0
        if not np.isfinite(retorno):
            continue
        rotulos.append(rotulo)
        valores.append(retorno)
    if not rotulos:
        return pd.Series(dtype="float64")
    return pd.Series(valores, index=rotulos, dtype="float64")


def preparar(escolha, datas, *, horizonte_meses: int, loader=None,
             serie: pd.Series | None = None,
             min_obs: int = MIN_OBS_BENCHMARK) -> dict:
    """Resolve, lê e pareia o benchmark às datas de decisão do backtest.

    Devolve sempre um dicionário com ``ok`` e ``modo``:

    * ``{"ok": True, "modo": "sem_indice"}`` — o usuário escolheu não comparar
      com índice; o equal-weight do universo segue como baseline;
    * ``{"ok": True, "modo": "indice", "retornos": Series, ...}`` — pareamento
      feito, com cobertura declarada;
    * ``{"ok": False, "modo": "erro", "erro": <código>, "mensagem": ...}`` —
      benchmark desconhecido, sem série ou com interseção abaixo do piso.

    Nenhum caminho cai em equal-weight silenciosamente: o erro é nomeado e a
    ausência de ``retornos`` é o que impede a métrica de excesso de existir.
    """
    datas = list(datas or [])
    selecao = resolver(escolha)
    base = {"mapa_versao": US_BENCHMARK_MAP_VERSION, "moeda": MOEDA,
            "fonte": FONTE_SERIE, "horizonte_meses": int(horizonte_meses),
            "min_obs": int(min_obs), "datas_do_backtest": len(datas)}
    if selecao is None:
        rotulo = "" if escolha is None else str(escolha)
        return {**base, "ok": False, "modo": MODO_ERRO, "erro": ERRO_DESCONHECIDO,
                "rotulo": rotulo, "simbolo": None,
                "mensagem": (f"Benchmark '{rotulo}' não está no mapa versionado "
                             f"v{US_BENCHMARK_MAP_VERSION}. Opções: "
                             f"{', '.join(BENCHMARK_LABELS)}.")}
    base = {**base, "rotulo": selecao.rotulo, "simbolo": selecao.simbolo}
    if not selecao.usa_indice:
        return {**base, "ok": True, "modo": MODO_SEM_INDICE,
                "mensagem": ("Sem índice de mercado: a comparação é com o "
                             "equal-weight do universo elegível.")}
    # Série injetada passa pela MESMA higienização da série lida do banco:
    # senão um preço zerado ou uma data ilegível entraria por uma porta e não
    # pela outra, e o teste validaria um caminho que a produção não usa.
    dados = (_higieniza_serie(serie, selecao.simbolo) if serie is not None
             else serie_total_return(selecao.simbolo, loader=loader))
    if dados.empty:
        return {**base, "ok": False, "modo": MODO_ERRO, "erro": ERRO_SEM_SERIE,
                "n_periodos": 0,
                "mensagem": (f"Sem série mensal publicada para {selecao.simbolo} "
                             f"em market_us.prices_monthly. Publique-a antes de "
                             f"comparar a estratégia com {selecao.rotulo}.")}
    retornos = retornos_realizados(dados, datas, horizonte_meses=horizonte_meses)
    cobertas = set(retornos.index)
    faltantes = [str(d) for d in datas if d not in cobertas]
    if len(retornos) < int(min_obs):
        return {**base, "ok": False, "modo": MODO_ERRO,
                "erro": ERRO_INTERSECAO_INSUFICIENTE, "n_periodos": len(retornos),
                "datas_sem_serie": faltantes,
                "serie_inicio": str(dados.index[0].date()),
                "serie_fim": str(dados.index[-1].date()),
                "mensagem": (f"{selecao.simbolo} cobre {len(retornos)} das "
                             f"{len(datas)} datas de decisão (piso {int(min_obs)}). "
                             f"Série disponível de {dados.index[0].date()} a "
                             f"{dados.index[-1].date()}.")}
    return {**base, "ok": True, "modo": MODO_INDICE, "retornos": retornos,
            "n_periodos": len(retornos), "datas_sem_serie": faltantes,
            "cobertura": len(retornos) / len(datas) if datas else 0.0,
            "serie_inicio": str(dados.index[0].date()),
            "serie_fim": str(dados.index[-1].date())}
