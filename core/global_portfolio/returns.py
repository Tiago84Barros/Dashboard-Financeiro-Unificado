"""Retornos mensais em moeda-base, com cobertura cambial explicita.

A serie mensal dos EUA agora e publicada no Supabase (market_us.prices_monthly,
via scripts/publish_us_prices_monthly.py) e lida no mesmo formato do analogo da
B3 por core.us_read.load_precos_mensais_us. Por isso `us` entra em
_CLASSES_COM_PRECO junto de `b3` e `fii`: os tres tem preco e concorrem a
cobertura pelo mesmo caminho. O loader padrao busca cada classe na sua fonte
(b3/fii em core.market_read, us em core.us_read) e junta os quadros alinhando
pelo indice de data — um simbolo so e pedido a fonte da sua propria classe.

Ativos em moeda diferente da base só entram quando existe uma taxa point-in-time
válida. Para USD em base BRL a convenção é ``BRL_per_USD`` e o retorno é:
``(1 + r_local) * (1 + r_fx) - 1``. Ausência ou defasagem cambial nunca vira
taxa 1 nem retorno 0%.

Ordem de descarte (a ordem importa e já foi defeito):

1. piso ``MIN_OBS`` POR COLUNA — um ativo com histórico curto sai sozinho,
   com motivo próprio;
2. interseção mensal comum entre os SOBREVIVENTES do passo 1;
3. piso ``MIN_OBS`` de novo, agora sobre a janela comum.

Fazer a interseção antes do piso por coluna (como já esteve nesta função) faz
um único ativo recém-comprado zerar o painel inteiro: um IPO, um FII novo ou a
série dos EUA recém-publicada bastavam para `peso_coberto` cair a 0,0. Isso é o
caso NORMAL da carteira, não uma falha, e contraria o gate G5 ("falha de um
adapter degrada de forma visível, não zera o patrimônio").

Unidades e convenções: retorno mensal em fração (0,01 = 1%), moeda-base BRL,
frequência mensal (fim de mês), fonte de preço = vitrines `market`/`market_us`,
fonte de câmbio = `asset_quotes`. Ausência permanece ausência — nunca 0%.

Coberto por tests/test_global_returns.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import partial

import numpy as np
import pandas as pd
from sqlalchemy import bindparam, text

MIN_OBS = 18                      # mesmo piso de core/b3_correlation_diversification
_CLASSES_COM_PRECO = ("b3", "fii", "us")
DEFAULT_BASE_CURRENCY = "BRL"
DEFAULT_MAX_FX_AGE_DAYS = 7

# Variantes aceitas do par cambial. A mesma taxa aparece gravada de formas
# diferentes conforme o ingestor (`USDBRL`, o sufixo do yfinance `USDBRL=X`, e
# a forma com barra). Casar só com a primeira devolvia {} e todo ativo em USD
# saía com o motivo errado ("sem câmbio" em vez de "câmbio existe, não achamos").
# `BRLUSD` está deliberadamente FORA: é a convenção invertida (USD por BRL) e
# consumi-la em silêncio inverteria a conversão.
FX_TICKERS_POR_MOEDA: dict[str, tuple[str, ...]] = {
    "USD": ("USDBRL", "USDBRL=X", "USD/BRL"),
}
_FX_CONVENTIONS = {("USD", "BRL"): "BRL_per_USD"}

# Motivos de exclusão por símbolo. São fatos distintos e o usuário age
# diferente em cada um: `sem_preco` manda olhar a ingestão; `historico_
# insuficiente` é dado íntegro e curto (nada a corrigir, só esperar);
# `sem_fx`/`fx_stale` apontam a série cambial; `fora_da_janela_comum` diz que o
# ativo tinha histórico suficiente e foi a janela COMUM que não sustentou.
# `classe_sem_preco` é ausência ESTRUTURAL (caixa, renda fixa: não existe série
# mensal de preço a ingerir) e não pede ação nenhuma — separá-lo de `sem_preco`
# é o que permite ao usuário distinguir "normal" de "a ingestão falhou".
MOTIVO_SEM_PRECO = "sem_preco"
MOTIVO_CLASSE_SEM_PRECO = "classe_sem_preco"
MOTIVO_HISTORICO_INSUFICIENTE = "historico_insuficiente"
MOTIVO_SEM_FX = "sem_fx"
MOTIVO_FX_STALE = "fx_stale"
MOTIVO_FORA_DA_JANELA = "fora_da_janela_comum"


@dataclass(frozen=True)
class FxEvidence:
    """Proveniência e cobertura da série cambial usada na conversão.

    ``stale_months`` conta apenas meses CANDIDATOS A RETORNO — o primeiro mês
    do índice de preços nunca gera retorno e não pode inflar o numerador — em
    que a taxa (ou a taxa do mês anterior) faltou dentro da tolerância.
    ``meses_candidatos`` é o denominador explícito desse numerador.
    """

    currency: str
    base_currency: str
    convention: str
    source: str
    last_observed_at: str | None
    max_age_days: int
    stale_months: int
    meses_candidatos: int = 0


@dataclass(frozen=True)
class Cobertura:
    """Quanto do patrimonio a serie de precos alcanca, e por que o resto não.

    Invariante garantido por ``retornos_mensais``:
    ``retornos.empty == (periodo_comum is None) == (meses == 0)``. Campos de
    janela descrevem o quadro PUBLICADO; quando nada é publicado eles ficam
    neutros, senão a tela exibiria um período de uma série que não existe.
    """

    simbolos_com_serie: tuple[str, ...]
    simbolos_sem_serie: tuple[str, ...]
    peso_coberto: float
    meses: int
    base_currency: str = DEFAULT_BASE_CURRENCY
    simbolos_sem_fx: tuple[str, ...] = ()
    simbolos_fx_stale: tuple[str, ...] = ()
    fx_evidence: tuple[FxEvidence, ...] = ()
    meses_removidos_intersecao: tuple[str, ...] = ()
    periodo_comum: tuple[str, str] | None = None
    # (simbolo, motivo), ordenado por simbolo. Cobre exatamente os simbolos de
    # `simbolos_sem_serie`; um simbolo publicado nunca aparece aqui.
    motivos: tuple[tuple[str, str], ...] = ()
    # Meses com observação em ALGUM sobrevivente que ficaram fora da janela
    # comum por truncamento de ponta (o ativo mais curto define o início).
    # `meses_removidos_intersecao` só enxerga buracos DENTRO da janela.
    meses_truncados_nas_pontas: int = 0
    contigua: bool = True
    maior_intervalo_meses: int = 0

    def simbolos_por_motivo(self) -> dict[str, tuple[str, ...]]:
        """Agrupa os símbolos excluídos por motivo, em ordem determinística."""
        agrupado: dict[str, list[str]] = {}
        for simbolo, motivo in self.motivos:
            agrupado.setdefault(motivo, []).append(simbolo)
        return {motivo: tuple(sorted(simbolos))
                for motivo, simbolos in sorted(agrupado.items())}

    def motivo_de(self, simbolo: str) -> str | None:
        """Motivo de exclusão de um símbolo, ou ``None`` se ele foi publicado."""
        return dict(self.motivos).get(str(simbolo).upper())


def _cobertura_sem_serie(base_currency: str,
                         motivos: dict[str, str]) -> Cobertura:
    """Cobertura de saída antecipada: nada publicado, motivo declarado."""
    return Cobertura(
        simbolos_com_serie=(),
        simbolos_sem_serie=tuple(sorted(motivos)),
        peso_coberto=0.0,
        meses=0,
        base_currency=base_currency,
        motivos=tuple(sorted(motivos.items())),
    )


def passos_em_meses(index) -> np.ndarray:
    """Distância em meses de calendário entre observações consecutivas.

    Usado para descrever contiguidade sem depender do número de dias (meses
    têm 28 a 31 dias; a diferença em dias não distingue "mês seguinte" de
    "mesmo mês").
    """
    idx = pd.DatetimeIndex(index)
    if len(idx) < 2:
        return np.empty(0, dtype=int)
    ordinal = idx.year.to_numpy(dtype=int) * 12 + idx.month.to_numpy(dtype=int)
    return np.diff(ordinal).astype(int)


def contiguidade(index) -> tuple[bool, int]:
    """(é contígua, maior intervalo em meses) para um índice mensal."""
    passos = passos_em_meses(index)
    if passos.size == 0:
        return True, 0
    maior = int(passos.max())
    return maior <= 1, maior


def _default_loader(tickers: tuple[str, ...],
                    *, classes_por_simbolo: dict[str, str] | None = None) -> pd.DataFrame:
    """Busca precos mensais em cada fonte pela classe do simbolo e junta por data.

    `classes_por_simbolo` chega via functools.partial (montado em
    retornos_mensais a partir de df_posicoes) para nao alterar a assinatura
    que os testes injetam: um loader customizado continua recebendo so
    `tickers`. Cada simbolo e pedido a UMA fonte - a da sua propria classe -
    nunca as duas com a lista inteira, o que so infla a consulta sem mudar o
    resultado. O join e por `pd.concat(..., axis=1)`, que alinha pelo indice
    (mes) em vez de empilhar por posicao; ambas as fontes devolvem o mesmo
    formato (DatetimeIndex mensal, colunas = simbolos em maiusculas), entao o
    alinhamento e direto.
    """
    from core.market_read import load_precos_mensais
    from core.us_read import load_precos_mensais_us

    classes = classes_por_simbolo or {}
    tickers_us = tuple(sorted(t for t in tickers if classes.get(t) == "us"))
    tickers_outros = tuple(sorted(t for t in tickers if classes.get(t) != "us"))

    partes = []
    if tickers_outros:
        partes.append(load_precos_mensais(tickers_outros))
    if tickers_us:
        partes.append(load_precos_mensais_us(tickers_us))
    partes = [p for p in partes if isinstance(p, pd.DataFrame) and not p.empty]
    if not partes:
        return pd.DataFrame()
    return pd.concat(partes, axis=1)


def _default_fx_loader(currencies: tuple[str, ...]) -> dict[str, pd.Series]:
    """Lê taxas históricas point-in-time do ``asset_quotes``.

    O casamento do ticker é sobre FX_TICKERS_POR_MOEDA, normalizado
    (``UPPER(TRIM(...))``), porque o mesmo par está gravado sob nomes
    diferentes conforme o ingestor. A tabela não guarda o provedor por linha;
    a proveniência declarada é o(s) ticker(s) interno(s) que de fato casaram,
    para que uma mistura de gravações fique visível em vez de virar um rótulo
    único inventado.
    """
    if not currencies:
        return {}
    from core.database import get_engine

    try:
        engine = get_engine()
    except Exception:
        return {}
    if engine is None:
        return {}
    saida: dict[str, pd.Series] = {}
    query = text(
        "SELECT UPPER(TRIM(a.ticker)) AS ticker, aq.timestamp, aq.close "
        "FROM asset_quotes aq JOIN assets a ON a.id = aq.asset_id "
        "WHERE UPPER(TRIM(a.ticker)) IN :tickers AND aq.close > 0 "
        "ORDER BY aq.timestamp"
    ).bindparams(bindparam("tickers", expanding=True))
    try:
        with engine.connect() as conn:
            for currency in currencies:
                variantes = FX_TICKERS_POR_MOEDA.get(currency)
                if not variantes:
                    continue
                frame = pd.read_sql_query(
                    query, conn, params={"tickers": [v.upper() for v in variantes]})
                if frame.empty:
                    continue
                idx = pd.DatetimeIndex(pd.to_datetime(
                    frame["timestamp"], errors="coerce", utc=True))
                serie = pd.Series(
                    pd.to_numeric(frame["close"], errors="coerce").to_numpy(),
                    index=idx.tz_convert(None),
                    name=currency,
                ).dropna()
                serie = serie[serie > 0].groupby(level=0).last().sort_index()
                if serie.empty:
                    continue
                casados = "+".join(sorted({str(t) for t in frame["ticker"]}))
                serie.attrs.update({
                    "source": f"asset_quotes:{casados}",
                    "convention": _FX_CONVENTIONS.get(
                        (currency, DEFAULT_BASE_CURRENCY), "unsupported"),
                })
                saida[currency] = serie
    except Exception:
        return {}
    return saida


def _normalizar_precos(precos: pd.DataFrame) -> pd.DataFrame:
    """Normaliza índice/colunas e trata preço fora de faixa como AUSÊNCIA.

    Preço só existe positivo e finito. Um `0`, um negativo ou um `inf` vindos
    da fonte não são preço: viram NaN e contam como ausência (nunca como
    observação válida). Sem isso `inf` atravessava `notna()` e chegava a
    produzir painel com `max = inf` — o caminho do câmbio já filtrava
    `serie > 0`, e a assimetria era o indício.
    """
    base = precos.copy()
    idx = pd.to_datetime(base.index, errors="coerce", utc=True)
    base.index = idx.tz_convert(None)
    base = base.loc[~base.index.isna()].sort_index().groupby(level=0).last()
    base.columns = [str(c).strip().upper() for c in base.columns]
    numerico = base.apply(pd.to_numeric, errors="coerce")
    if numerico.empty:
        return numerico.astype(float)
    valores = numerico.to_numpy(dtype=float)
    # NaN > 0 já é False, então a mesma máscara cobre ausência, zero, negativo
    # e não-finito.
    valido = np.isfinite(valores) & (valores > 0)
    return numerico.astype(float).where(
        pd.DataFrame(valido, index=numerico.index, columns=numerico.columns))


def _alinhar_fx(fx: pd.Series, datas: pd.DatetimeIndex, max_age_days: int,
                ) -> tuple[pd.Series, pd.Timestamp | None]:
    """Última taxa conhecida em ou antes da data, limitada por defasagem.

    Devolve também o timestamp da última observação cambial EFETIVAMENTE
    consumida. O máximo bruto da série serviria de proveniência falsa: uma
    cotação posterior ao último mês do painel (ou fora da tolerância) nunca
    entrou em nenhum retorno e não pode ser exibida como "última observação".
    """
    serie = pd.to_numeric(fx, errors="coerce")
    idx = pd.to_datetime(serie.index, errors="coerce", utc=True)
    serie.index = idx.tz_convert(None)
    serie = serie.loc[~serie.index.isna()].astype(float)
    # Câmbio só existe quando é finito e estritamente positivo. Em particular,
    # ``inf > 0`` é True: filtrar só pelo sinal o deixava atravessar o
    # alinhamento e fazia a observação seguinte aparentar retorno de -100%.
    # Qualquer domínio inválido é ausência, para que o mês e seu retorno
    # dependente saiam da janela comum.
    serie = serie[np.isfinite(serie.to_numpy()) & (serie > 0)]
    serie = serie.sort_index().groupby(level=0).last()
    if serie.empty:
        return pd.Series(index=datas, dtype=float), None
    esquerda = pd.DataFrame(index=pd.DatetimeIndex(datas).sort_values())
    direita = serie.rename("fx").to_frame()
    direita["fx_observed_at"] = direita.index
    casada = pd.merge_asof(
        esquerda, direita, left_index=True, right_index=True,
        direction="backward", tolerance=pd.Timedelta(days=max_age_days),
    )
    alinhada = casada["fx"].reindex(datas)
    usados = casada["fx_observed_at"].dropna()
    ultimo = pd.Timestamp(usados.max()) if not usados.empty else None
    return alinhada, ultimo


def _currency_for_row(row: dict) -> str:
    explicit = str(row.get("currency") or "").strip().upper()
    if explicit:
        return explicit
    from core.portfolio.registry import get_spec
    try:
        return get_spec(str(row.get("asset_class") or "")).currency.upper()
    except KeyError:
        return ""


def _retornos_cambiais(moedas: tuple[str, ...], series_fx: dict,
                       datas: pd.DatetimeIndex, base_currency: str,
                       max_fx_age_days: int,
                       ) -> tuple[dict[str, pd.Series], dict[str, FxEvidence]]:
    """Retorno cambial mensal e evidência POR MOEDA (não por símbolo).

    O alinhamento depende só do índice de datas e da tolerância, iguais para
    todos os símbolos da mesma moeda — calcular dentro do laço por símbolo,
    além de repetir trabalho, sobrescrevia a evidência e deixava só o último
    ativo em USD visível na tela.
    """
    retornos_fx: dict[str, pd.Series] = {}
    evidencias: dict[str, FxEvidence] = {}
    # O primeiro mês do índice nunca gera retorno: ele é o denominador da
    # primeira variação, não uma observação. Contá-lo inflaria `stale_months`.
    candidatos = max(len(datas) - 1, 0)
    for moeda in moedas:
        convention = _FX_CONVENTIONS.get((moeda, base_currency))
        fx = series_fx.get(moeda)
        if convention is None or not isinstance(fx, pd.Series) or fx.empty:
            continue
        alinhada, ultimo = _alinhar_fx(fx, datas, max_fx_age_days)
        retorno_fx = alinhada.pct_change(fill_method=None)
        sem_taxa = int(retorno_fx.iloc[1:].isna().sum()) if candidatos else 0
        retornos_fx[moeda] = retorno_fx
        evidencias[moeda] = FxEvidence(
            currency=moeda,
            base_currency=base_currency,
            convention=str(fx.attrs.get("convention") or convention),
            source=str(fx.attrs.get("source") or "unknown"),
            last_observed_at=(ultimo.isoformat() if ultimo is not None else None),
            max_age_days=int(max_fx_age_days),
            stale_months=sem_taxa,
            meses_candidatos=candidatos,
        )
    return retornos_fx, evidencias


def retornos_mensais(df_posicoes: pd.DataFrame,
                     *, loader=None, fx_loader=None,
                     base_currency: str = DEFAULT_BASE_CURRENCY,
                     max_fx_age_days: int = DEFAULT_MAX_FX_AGE_DAYS,
                     ) -> tuple[pd.DataFrame, Cobertura]:
    """Retornos mensais na moeda-base, mais cobertura de preços e câmbio.

    ``fx_loader`` recebe as moedas estrangeiras ordenadas e devolve
    ``{moeda: Series}``, com índice igual ao timestamp observado e atributos
    ``source``/``convention``. A taxa é sempre unidades da moeda-base por uma
    unidade da moeda de origem.
    """
    base_currency = str(base_currency or "").strip().upper()
    if base_currency != DEFAULT_BASE_CURRENCY:
        raise ValueError("moeda-base suportada nesta versão: BRL")
    if max_fx_age_days < 0:
        raise ValueError("max_fx_age_days deve ser não negativo")
    if df_posicoes is None or df_posicoes.empty:
        return pd.DataFrame(), Cobertura((), (), 0.0, 0, base_currency)

    linhas = df_posicoes.to_dict(orient="records")
    peso_total = sum(float(linha.get("weight_global") or 0.0) for linha in linhas)

    mapa_classes = {str(linha["symbol"]).upper(): str(linha.get("asset_class") or "").lower()
                    for linha in linhas}
    mapa_moedas = {str(linha["symbol"]).upper(): _currency_for_row(linha) for linha in linhas}
    loader = loader or partial(_default_loader, classes_por_simbolo=mapa_classes)

    candidatos = sorted({
        str(linha["symbol"]).upper() for linha in linhas
        if str(linha.get("asset_class") or "").lower() in _CLASSES_COM_PRECO
    })
    # Classe sem série de preços (renda fixa, caixa): ausência estrutural, não
    # defeito de ingestão — por isso motivo próprio. Até 24/08/2026 esta linha
    # gravava MOTIVO_SEM_PRECO, o mesmo das linhas abaixo, e o comentário
    # prometia uma distinção que o código não fazia: um CDB (que nunca terá
    # série) e uma NVDA faltando na ingestão saíam com o mesmo rótulo.
    motivos: dict[str, str] = {
        str(linha["symbol"]).upper(): MOTIVO_CLASSE_SEM_PRECO for linha in linhas
        if str(linha.get("asset_class") or "").lower() not in _CLASSES_COM_PRECO
    }

    precos = loader(tuple(candidatos)) if candidatos else pd.DataFrame()
    if not isinstance(precos, pd.DataFrame) or precos.empty:
        for simbolo in candidatos:
            motivos[simbolo] = MOTIVO_SEM_PRECO
        return pd.DataFrame(), _cobertura_sem_serie(base_currency, motivos)

    precos = _normalizar_precos(precos)
    for simbolo in candidatos:
        if simbolo not in precos.columns:
            motivos[simbolo] = MOTIVO_SEM_PRECO

    moedas_fx = tuple(sorted({moeda for moeda in mapa_moedas.values()
                              if moeda and moeda != base_currency}))
    fx_loader = fx_loader or _default_fx_loader
    series_fx = fx_loader(moedas_fx) if moedas_fx else {}
    if not isinstance(series_fx, dict):
        series_fx = {}
    retornos_fx, evidencias = _retornos_cambiais(
        moedas_fx, series_fx, precos.index, base_currency, max_fx_age_days)

    # fill_method=None: um preco ausente vira NaN no retorno, nunca um 0%
    # fabricado (o default 'pad' do pandas preenche o preco antes de
    # diferenciar e transforma o gap em "calmaria" que nao existiu).
    retornos_locais = precos.sort_index().pct_change(fill_method=None)
    retornos = pd.DataFrame(index=retornos_locais.index)
    sem_fx: set[str] = set()
    fx_stale: set[str] = set()

    for symbol in retornos_locais.columns:
        moeda = mapa_moedas.get(symbol, "")
        local = retornos_locais[symbol]
        if moeda == base_currency:
            retornos[symbol] = local
            continue

        retorno_fx = retornos_fx.get(moeda)
        if retorno_fx is None:
            retornos[symbol] = pd.Series(index=retornos.index, dtype=float)
            sem_fx.add(symbol)
            continue

        # Retorno total em BRL = retorno local + câmbio + interação.
        retornos[symbol] = (1.0 + local) * (1.0 + retorno_fx) - 1.0
        # Stale conta só onde o ativo TERIA retorno: um mês sem taxa fora do
        # histórico do papel não é defasagem sofrida por ele.
        if bool((local.notna() & retorno_fx.isna()).any()):
            fx_stale.add(symbol)

    # ------------------------------------------------------------------
    # 1) Piso MIN_OBS POR COLUNA.
    # Um ativo com histórico curto sai sozinho e é nomeado. Aplicar a
    # interseção antes disto fazia um ativo de 12 meses zerar um painel de
    # 120 — e produzia a descontinuidade absurda "0 observações: painel vivo;
    # 1 observação: painel morto".
    # ------------------------------------------------------------------
    observacoes = {
        c: int(np.isfinite(retornos[c].to_numpy(dtype=float)).sum())
        for c in retornos.columns
    }
    sobreviventes = sorted(c for c, n in observacoes.items() if n >= MIN_OBS)
    for coluna in retornos.columns:
        if coluna in sobreviventes or coluna in motivos:
            continue
        if coluna in sem_fx:
            motivos[coluna] = MOTIVO_SEM_FX
        elif coluna in fx_stale:
            # A defasagem cambial é o que derrubou a coluna abaixo do piso;
            # chamar isso de "histórico insuficiente" mandaria o usuário
            # procurar defeito no lugar errado.
            motivos[coluna] = MOTIVO_FX_STALE
        else:
            motivos[coluna] = MOTIVO_HISTORICO_INSUFICIENTE

    # ------------------------------------------------------------------
    # 2) Janela mensal COMUM entre os sobreviventes. Um mês só entra se TODOS
    # eles tiverem retorno nele (preço e câmbio). A alternativa — deixar o NaN
    # passar — faz risk.retorno_do_portfolio renormalizar os pesos mês a mês:
    # uma carteira 40% B3 / 60% EUA viraria 100% B3 nos meses sem FX, sem que
    # nada na tela mudasse. Aqui o mês sai e é declarado.
    #
    # Segunda consequencia, descoberta depois e igualmente importante: como o
    # quadro publicado nao tem buraco, `_covariancia_confiavel` monta a matriz
    # sobre casos completos e ela sai positiva semidefinida. Afrouxar esta
    # janela devolve a covariancia par a par, que pode ter autovalor negativo —
    # e ai a contribuicao marginal de risco continua fechando na identidade de
    # Euler (o teste central de `risk.py`) exibindo parcela errada: medido em
    # 23/08/2026, um ativo aparecia protegendo a carteira com -26,2% do risco
    # onde a matriz corrigida dizia +3,5%. Ver
    # `tests/test_global_covariancia_psd.py`.
    # ------------------------------------------------------------------
    retornos = retornos[sobreviventes] if sobreviventes else pd.DataFrame()
    meses_removidos: tuple[str, ...] = ()
    truncados = 0
    if sobreviventes:
        finito = np.isfinite(retornos.to_numpy(dtype=float))
        valida = finito.all(axis=1)
        alguma = finito.any(axis=1)
        if valida.any():
            datas_validas = retornos.index[valida]
            inicio, fim = datas_validas[0], datas_validas[-1]
            # Em pandas recentes a comparação de DatetimeIndex já devolve
            # ndarray; a conversão adicional não existe nessa API.
            dentro = (retornos.index >= inicio) & (retornos.index <= fim)
            meses_removidos = tuple(
                d.date().isoformat() for d in retornos.index[dentro & ~valida])
            # Truncamento de ponta: meses com observação em algum sobrevivente
            # que a janela comum cortou nas bordas. `meses_removidos` só
            # enxerga buracos internos, então sem este número o corte de 56
            # meses de um ativo longo fica invisível.
            truncados = int((~dentro & alguma).sum())
            retornos = retornos.loc[datas_validas]
        else:
            retornos = retornos.iloc[0:0]

    # ------------------------------------------------------------------
    # 3) Piso MIN_OBS sobre a janela comum. Serie curta nao sustenta
    # correlacao. Esvaziar aqui é legítimo: os ativos JÁ passaram no piso
    # individual e foi a sobreposição entre eles que não sustentou (históricos
    # disjuntos, por exemplo). Nenhum ativo é sacrificado para "salvar" o
    # painel — isso seria seleção oportunista de amostra (G6).
    # ------------------------------------------------------------------
    if len(retornos) < MIN_OBS:
        for coluna in sobreviventes:
            motivos.setdefault(coluna, MOTIVO_FORA_DA_JANELA)
        retornos = pd.DataFrame()
        # Invariante: sem quadro publicado não há período, buraco nem
        # truncamento a declarar. Antes esses campos sobreviviam ao
        # esvaziamento e a tela mostrava janela de uma série inexistente.
        meses_removidos = ()
        truncados = 0

    periodo_comum: tuple[str, str] | None = None
    if not retornos.empty:
        periodo_comum = (retornos.index[0].date().isoformat(),
                         retornos.index[-1].date().isoformat())
    contigua, maior_intervalo = contiguidade(retornos.index)

    publicados = set(retornos.columns)
    do_portfolio = set(candidatos) | set(motivos)
    motivos = {s: m for s, m in motivos.items()
               if s in do_portfolio and s not in publicados}
    peso_ok = sum(float(linha.get("weight_global") or 0.0) for linha in linhas
                  if str(linha["symbol"]).upper() in publicados)

    return retornos, Cobertura(
        simbolos_com_serie=tuple(retornos.columns),
        simbolos_sem_serie=tuple(sorted(motivos)),
        peso_coberto=(peso_ok / peso_total) if peso_total > 0 else 0.0,
        meses=len(retornos),
        base_currency=base_currency,
        simbolos_sem_fx=tuple(sorted(sem_fx)),
        simbolos_fx_stale=tuple(sorted(fx_stale)),
        fx_evidence=tuple(evidencias[k] for k in sorted(evidencias)),
        meses_removidos_intersecao=meses_removidos,
        periodo_comum=periodo_comum,
        motivos=tuple(sorted(motivos.items())),
        meses_truncados_nas_pontas=truncados,
        contigua=contigua,
        maior_intervalo_meses=maior_intervalo,
    )
