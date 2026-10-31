"""
core/tesouro_historico.py — a trajetória da marcação da **posição**, dia a dia.

Por que não é a série do título
-------------------------------
A oscilação que interessa a quem tem o papel não é a da taxa publicada: é a da
razão entre o preço do dia e o preço que o *lote dele* teria naquele dia pela
taxa que ele contratou. Dois investidores com o mesmo título e taxas
contratadas diferentes têm marcações diferentes no mesmo dia, e um pode estar
em ágio enquanto o outro está em deságio.

A honestidade da série está em **não** marcar lote que ainda não existia. Em
cada data entram só os lotes com aplicação até ali, com os pesos daquele dia.
Aplicar a carteira de hoje à taxa de dois anos atrás desenharia uma curva
bonita e contrafactual — a marcação de uma posição que não havia sido comprada.
A série começa, portanto, na primeira aplicação, e o número de lotes viaja em
cada ponto para que a tela possa dizer quando a composição mudou.

Função pura, sem banco e sem pandas: recebe os lotes e as linhas da curva já
decimalizadas por `core.tesouro_curva`, devolve lista de dicionários.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Sequence

from core.tesouro_mtm import LoteTesouro, dias_uteis, mtm_por_taxa


def _data(valor: Any) -> date | None:
    if isinstance(valor, date):
        return valor
    try:
        return valor.date()  # datetime/Timestamp
    except Exception:
        return None


def _numero(valor: Any) -> float | None:
    if valor is None:
        return None
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return None if numero != numero else numero  # NaN do pandas


def serie_mtm_posicao(
    lotes: Sequence[LoteTesouro],
    *,
    vencimento: date,
    cotacoes: Iterable[dict],
) -> list[dict]:
    """Marcação da posição em cada data-base da curva, em ordem cronológica.

    Cada ponto traz:

    ``data``
        a data-base da cotação;
    ``mtm_pct``
        a marcação da posição viva naquele dia, ponderada pelo valor de
        mercado de cada lote — média simples daria o mesmo peso a uma
        aplicação de R$ 30 e a uma de R$ 30.000;
    ``taxa_mercado`` / ``taxa_contratada``
        a taxa de recompra do dia e a média contratada da posição viva, em
        decimal. São o *por quê* da marcação: ela é positiva exatamente quando
        a contratada está acima da de mercado;
    ``valor_bruto`` / ``valor_curva`` / ``investido``
        o valor de mercado daquele dia, o valor que a posição teria pela taxa
        contratada (a "curva do lote") e o custo dos lotes vivos. A distância
        entre os dois primeiros **é** a marcação em reais: desenhados juntos,
        eles mostram o ágio sem precisar de porcentagem;
    ``lotes``
        quantos lotes compunham a posição — é o que permite distinguir queda de
        marcação de entrada de lote novo com taxa diferente.

    Datas sem taxa, sem PU ou já no vencimento saem fora: ponto sem preço não é
    ponto em zero.
    """
    vivos_por_data: list[dict] = []
    if not lotes or vencimento is None:
        return vivos_por_data

    linhas = []
    for cotacao in cotacoes:
        base = _data(cotacao.get("base_date"))
        taxa = _numero(cotacao.get("sell_rate_dec"))
        pu = _numero(cotacao.get("sell_pu"))
        if base is None or taxa is None or pu is None or pu <= 0:
            continue
        linhas.append((base, taxa, pu))
    linhas.sort(key=lambda t: t[0])

    for base, taxa_mercado, pu in linhas:
        du = dias_uteis(base, vencimento)
        if du <= 0:
            continue
        vivos = [lote for lote in lotes
                 if lote.data_aplicacao is not None
                 and lote.data_aplicacao <= base
                 and lote.taxa_contratada is not None]
        if not vivos:
            continue

        bruto = 0.0
        curva = 0.0
        investido = 0.0
        contratada_ponderada = 0.0
        for lote in vivos:
            mtm = mtm_por_taxa(lote.taxa_contratada.valor, taxa_mercado, du)
            if mtm is None or mtm <= -1.0:
                continue
            valor = pu * lote.quantidade
            bruto += valor
            curva += valor / (1.0 + mtm)
            investido += lote.valor_investido
            contratada_ponderada += lote.taxa_contratada.valor * valor

        if bruto <= 0 or curva <= 0:
            continue
        vivos_por_data.append({
            "data": base,
            "mtm_pct": bruto / curva - 1.0,
            "taxa_mercado": taxa_mercado,
            "taxa_contratada": contratada_ponderada / bruto,
            "valor_bruto": bruto,
            "valor_curva": curva,
            "investido": investido,
            "lotes": len(vivos),
        })
    return vivos_por_data


def extremos_da_serie(serie: Sequence[dict]) -> dict:
    """Mínimo, máximo e último ponto da marcação — o alcance da oscilação.

    A tela precisa dizer de quanto a marcação já oscilou *nesta* posição; sem
    isso, um ágio de 1,2% hoje não informa se é muito ou pouco. Devolve dicionário
    vazio quando a série não tem ponto algum, para que a ausência não vire zero.
    """
    pontos = [p for p in serie if p.get("mtm_pct") is not None]
    if not pontos:
        return {}
    minimo = min(pontos, key=lambda p: p["mtm_pct"])
    maximo = max(pontos, key=lambda p: p["mtm_pct"])
    return {
        "inicio": pontos[0]["data"],
        "fim": pontos[-1]["data"],
        "atual": pontos[-1]["mtm_pct"],
        "minimo": minimo["mtm_pct"],
        "data_minimo": minimo["data"],
        "maximo": maximo["mtm_pct"],
        "data_maximo": maximo["data"],
        "pontos": len(pontos),
    }
