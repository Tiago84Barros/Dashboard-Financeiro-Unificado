"""Médias descritivas, não múltiplos contábeis consolidados, de posições long-only."""
import math
from typing import NamedTuple

METRICS = {'dy': 'DY', 'pl': 'P/L', 'pvp': 'P/VP', 'psr': 'PSR',
           'p_ebit': 'P/EBIT', 'ev_ebitda': 'EV/EBITDA', 'ev_ebit': 'EV/EBIT'}


class MetricSpec(NamedTuple):
    """Como um indicador é rotulado e qual valor dele é agregável.

    ``floor=None`` aceita qualquer número finito — é o caso das medidas que
    podem ser legitimamente negativas (ROE, margem, crescimento); rejeitá-las
    daria média enviesada para cima justamente onde o dado é ruim.
    ``floor=0`` com ``strict=True`` exige valor positivo: múltiplo zero ou
    negativo não é "barato", é indefinido, e entrar na média inverte a leitura.
    """
    label: str
    unit: str
    floor: float | None
    strict: bool


_X = 'x'
_PCT = '%'

SPECS: dict[str, MetricSpec] = {
    # Múltiplos — só positivos.
    'pl':        MetricSpec('P/L', _X, 0., True),
    'pvp':       MetricSpec('P/VP', _X, 0., True),
    'psr':       MetricSpec('PSR', _X, 0., True),
    'p_ebit':    MetricSpec('P/EBIT', _X, 0., True),
    'ev_ebitda': MetricSpec('EV/EBITDA', _X, 0., True),
    'ev_ebit':   MetricSpec('EV/EBIT', _X, 0., True),
    'p_fcf':     MetricSpec('P/FCF', _X, 0., True),
    'p_s':       MetricSpec('P/S', _X, 0., True),
    'pe':        MetricSpec('P/L', _X, 0., True),
    # Percentuais que só fazem sentido não negativos.
    'dy':              MetricSpec('DY', _PCT, 0., False),
    'vacancia_media':  MetricSpec('Vacância física', _PCT, 0., False),
    'vacancia_financ': MetricSpec('Vacância financeira', _PCT, 0., False),
    # Percentuais que podem ser negativos sem deixar de ser observação válida.
    'roe':          MetricSpec('ROE', _PCT, None, False),
    'roic':         MetricSpec('ROIC', _PCT, None, False),
    'marg_liq':     MetricSpec('Margem líquida', _PCT, None, False),
    'cresc_rec_5a': MetricSpec('Cresc. receita 5a', _PCT, None, False),
    'net_margin':        MetricSpec('Margem líquida', _PCT, None, False),
    'operating_margin':  MetricSpec('Margem operacional', _PCT, None, False),
    'fcf_yield':         MetricSpec('FCF yield', _PCT, None, False),
    'earnings_yield':    MetricSpec('Earnings yield', _PCT, None, False),
    'shareholder_yield': MetricSpec('Retorno ao acionista', _PCT, None, False),
    # Não negativo: dividendo desembolsado é saída de caixa, e o sinal do EDGAR
    # já é tratado em us_metrics. Um DY negativo aqui seria erro de fonte.
    'dividend_yield':    MetricSpec('Dividend yield', _PCT, 0., False),
    # Razões não negativas.
    'div_brut_patrim': MetricSpec('Dív. bruta/Patrim.', _X, 0., False),
    'liq_corr':        MetricSpec('Liquidez corrente', _X, 0., False),
    'current_ratio':   MetricSpec('Liquidez corrente', _X, 0., False),
    'net_debt_ebitda': MetricSpec('Dív. líquida/EBITDA', _X, None, False),
}

# Indicadores agregáveis por classe. Cada classe usa os nomes de campo que o
# provedor daquela classe já entrega — não há tradução implícita entre elas, e
# um múltiplo de companhia nunca entra na mesma média que o de um FII.
METRICS_BY_CLASS: dict[str, tuple[str, ...]] = {
    'acoes': ('dy', 'pl', 'pvp', 'psr', 'ev_ebitda', 'ev_ebit',
              'roe', 'roic', 'marg_liq', 'cresc_rec_5a', 'div_brut_patrim'),
    'fiis': ('dy', 'pvp', 'vacancia_media', 'vacancia_financ'),
    # `dividend_yield` do exterior é derivado do EDGAR e se refere ao último
    # exercício fechado; ele NÃO entra na mesma média que o 'dy' de ações e FIIs
    # brasileiros, que é de 12 meses corridos. Por isso o nome do campo é outro:
    # a separação por classe é justamente o que impede a média indevida.
    'exterior': ('pe', 'p_s', 'ev_ebitda', 'ev_ebit', 'p_fcf', 'roe',
                 'net_margin', 'fcf_yield', 'shareholder_yield',
                 'dividend_yield'),
}

# Indicadores do universo dos EUA que a fonte entrega como fração decimal
# (0,12 = 12%). Convertê-los na leitura evita publicar "0,12%" de margem.
_FRACAO_PARA_PCT = {'roe', 'roic', 'roa', 'net_margin', 'operating_margin',
                    'gross_margin', 'fcf_yield', 'earnings_yield',
                    'shareholder_yield', 'payout_ratio',
                    'dividend_yield'}

CLASS_LABELS = {'acoes': 'Ações', 'fiis': 'FIIs',
                'tesouro': 'Tesouro Direto', 'exterior': 'Exterior'}


def _number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def metric_spec(metric: str) -> MetricSpec:
    """Spec do indicador; desconhecido cai na regra conservadora de múltiplo."""
    return SPECS.get(metric, MetricSpec(METRICS.get(metric, metric), _X, 0., True))


def _agregavel(metric: str, value):
    """Devolve o valor quando ele pode entrar na média; None quando não pode."""
    value = _number(value)
    if value is None:
        return None
    spec = metric_spec(metric)
    if spec.floor is None:
        return value
    if spec.strict and value <= spec.floor:
        return None
    if not spec.strict and value < spec.floor:
        return None
    return value


def para_percentual(metric: str, value):
    """Converte fração decimal em ponto percentual só onde a fonte usa fração."""
    value = _number(value)
    if value is None:
        return None
    return value * 100 if metric in _FRACAO_PARA_PCT else value


def aggregate_valuations(positions, fundamentals, metrics=None):
    """Σ(valor BRL × indicador)/Σ(valor BRL válido), por indicador.

    DY entra em pontos percentuais (8 = 8%); múltiplos em vezes.
    Não imputa ausências. Cada indicador tem seu critério de validade em
    ``SPECS``: múltiplo precisa ser positivo, margem pode ser negativa.
    Lotes do mesmo ticker acumulam exposição, mas contam como um ativo.
    Cobertura é medida sobre o valor de mercado positivo das posições
    recebidas — quem passa a carteira inteira mede sobre a carteira; quem
    passa uma classe mede sobre aquela classe.
    """
    weights = {}
    for pos in positions or ():
        weight = _number(pos.get('valor_mercado'))
        if weight is not None and weight > 0:
            ticker = str(pos.get('ticker') or '').strip().upper()
            weights[ticker] = weights.get(ticker, 0.) + weight
    total = sum(weights.values())
    result = {}
    for metric in (METRICS if metrics is None else metrics):
        numerator = covered = 0.
        count = 0
        for ticker, weight in weights.items():
            value = _agregavel(metric, (fundamentals or {}).get(ticker, {}).get(metric))
            if value is None:
                continue
            numerator += weight * value
            covered += weight
            count += 1
        result[metric] = {'value': numerator / covered if covered else None,
                          'coverage': covered / total if total else 0., 'assets': count}
    return result


def aggregate_tesouro(positions, ano_atual):
    """Consolida o que é agregável em renda fixa pública: prazo, retorno, indexador.

    Não há múltiplo de valuation aqui — título público não tem lucro nem
    patrimônio, e inventar um seria pior que declarar a ausência. O que se
    pode somar é a exposição por indexador e o prazo médio até o vencimento,
    ponderados pelo valor de mercado. O retorno é mercado sobre custo, o mesmo
    da tabela por título; não é marcação a mercado isolada nem retorno anual.
    """
    from core.tesouro_analysis import retorno_mercado_sobre_custo, tesouro_meta

    total_mv = total_vi = 0.
    prazo_peso = prazo_soma = 0.
    sem_ano = 0.
    por_indexador: dict = {}
    tickers = set()
    for pos in positions or ():
        mv = _number(pos.get('valor_mercado')) or 0.
        vi = _number(pos.get('total_investido')) or 0.
        if mv <= 0:
            continue
        total_mv += mv
        total_vi += vi
        ticker = str(pos.get('ticker') or '').strip().upper()
        tickers.add(ticker)
        meta = tesouro_meta(ticker)
        por_indexador[meta.tipo] = por_indexador.get(meta.tipo, 0.) + mv
        if meta.ano_referencia:
            prazo_soma += mv * (meta.ano_referencia - ano_atual)
            prazo_peso += mv
        else:
            sem_ano += mv
    retorno = retorno_mercado_sobre_custo(total_mv, total_vi) if total_vi > 0 else None
    return {
        'valor_mercado': total_mv,
        'custo': total_vi,
        'resultado': total_mv - total_vi,
        'retorno_pct': retorno * 100 if retorno is not None else None,
        'prazo_medio_anos': prazo_soma / prazo_peso if prazo_peso else None,
        'cobertura_prazo': prazo_peso / total_mv if total_mv else 0.,
        'valor_sem_ano': sem_ano,
        'por_indexador': ({k: v / total_mv for k, v in sorted(
            por_indexador.items(), key=lambda item: -item[1])} if total_mv else {}),
        'titulos': len(tickers),
    }


def load_valuation_fundamentals(stocks, fiis):
    """Reutiliza os provedores da Análise; falha de uma classe não apaga a outra."""
    from core.data_reconciliacao import batch_fund_fmt
    from core.fundamentus import batch_fiis
    data, unavailable = {}, []
    for tickers, loader, name in [(stocks, batch_fund_fmt, 'Ações B3'),
                                   (fiis, batch_fiis, 'FIIs')]:
        if not tickers:
            continue
        try:
            data.update(loader(tickers))
        except Exception:
            # Não expor detalhes de conexão nem dados privados na interface.
            unavailable.append(name)
    return data, unavailable
