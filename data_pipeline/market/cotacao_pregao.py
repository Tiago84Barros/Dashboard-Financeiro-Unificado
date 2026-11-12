"""Cotação corrente por pregão: regra única de FIIs e ações da B3.

A cota gratuita da brapi acabou em 29/10/2026 e a cotação ficou na foto
daquele dia, enquanto a fita da B3 (COTAHIST) seguia diária. A regra é a
mesma da leitura do #707 (`core.market_read._observacao_vence_base`): vence a
observação de data mais nova. Nasceu em `fii.py` (#713) e foi movida para cá
quando as ações passaram a usá-la (`acoes_cotacao`); `fii.py` reexporta.

Duas séries de preço da B3 que NÃO se substituem
-------------------------------------------------
`market.historical_prices.close` é retroajustado por split; o COTAHIST é o
preço negociado, cru. Por isso o close cru NUNCA entra na série ajustada: ele
ocupa um slot próprio (`market.b3_cotacao_atual`) e o leitor escolhe entre a
última barra e o slot por `preco_vigente`. Se um desdobramento, grupamento ou
bonificação ocorreu entre a data da barra e a do pregão, o par está em escalas
diferentes: `evento_entre` detecta e a ação fica com a barra, nomeada.
"""
from __future__ import annotations

import datetime as _dt
import math
from zoneinfo import ZoneInfo

_TZ_PREGAO = ZoneInfo("America/Sao_Paulo")
# Fim do pregão regular da B3 (call de fechamento). O close de um dia é
# posterior a qualquer cotação intradiária do mesmo dia.
_HORA_FECHAMENTO = _dt.time(18, 0)
FONTE_FECHAMENTO_PREGAO = "b3_cotahist"


def instante(valor) -> _dt.datetime | None:
    """Data/hora com fuso. Texto ISO, ``datetime``, ``Timestamp`` ou ``date``.

    Sem fuso, o texto/``datetime`` é lido como UTC (é como o banco devolve
    ``timestamptz`` sem fuso e como a brapi escreve). Um ``date`` puro é o
    início do dia em São Paulo: só se sabe o dia, e o fechamento daquele
    mesmo dia é posterior a ele.
    """
    if valor is None:
        return None
    if isinstance(valor, float) and math.isnan(valor):
        return None
    try:
        import pandas as pd
        if valor is pd.NaT:
            return None
        if isinstance(valor, pd.Timestamp):
            valor = valor.to_pydatetime()
    except ImportError:  # pragma: no cover - pandas é dependência do projeto
        pass
    if isinstance(valor, _dt.datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=_dt.timezone.utc)
    if isinstance(valor, _dt.date):
        return _dt.datetime.combine(valor, _dt.time(0, 0), tzinfo=_TZ_PREGAO)
    if isinstance(valor, (int, float)):
        return _dt.datetime.fromtimestamp(float(valor), tz=_dt.timezone.utc)
    texto = str(valor).strip()
    if not texto:
        return None
    if len(texto) == 10:
        try:
            return instante(_dt.date.fromisoformat(texto))
        except ValueError:
            return None
    try:
        ts = _dt.datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=_dt.timezone.utc)


def fechamento_pregao_em(trade_date) -> _dt.datetime:
    """Instante do close de um pregão: o dia às 18h de São Paulo."""
    dia = trade_date if isinstance(trade_date, _dt.date) and not isinstance(
        trade_date, _dt.datetime) else instante(trade_date).astimezone(_TZ_PREGAO).date()
    return _dt.datetime.combine(dia, _HORA_FECHAMENTO, tzinfo=_TZ_PREGAO)


def mais_nova_vence(valor_atual, data_atual, data_nova) -> bool:
    """Regra única de cotação (leitura do #707 e escrita em `market.fiis`).

    Valor atual ausente: o novo preenche. Sem data de um dos lados, não se
    sabe qual é o mais novo e o atual fica. Senão, vence o estritamente mais
    novo -- empate mantém o que está.
    """
    if valor_atual is None or (isinstance(valor_atual, float) and math.isnan(valor_atual)):
        return True
    if data_atual is None or data_nova is None:
        return False
    return data_nova > data_atual


# ── Guarda de evento societário entre a barra e o pregão ───────────────────
# Marcadores do COTAHIST (campo ESPECI) que mudam a ESCALA do preço: grupamento
# (EG) e bonificação (EB). Provento em dinheiro -- JCP (EJ), dividendo (ED),
# amortização (ATZ) -- NÃO muda a escala da série ajustada por split e não pula.
MARCADORES_DE_ESCALA = frozenset({"EG", "EB"})
# Salto diário do preço cru acima do qual a hipótese de unidade de cotação
# quebrada pesa mais que a de movimento de mercado (calibração do saneamento
# mensal: acima de +300% sem evidência; aqui qualquer variação > 50%).
SALTO_DE_ESCALA = 0.5


def evento_entre(pregoes, data_base) -> str | None:
    """Motivo de pular o ticker, ou ``None`` se a escala é a mesma.

    ``pregoes`` são as linhas diárias do COTAHIST em ordem de data, como
    ``(trade_date, close_unitario, fator_cotacao, especificacao)``, a partir
    do último pregão até ``data_base`` (a baseline) e todos os posteriores.
    Considera só os pares cujo pregão posterior é depois da ``data_base``.
    Pula se, em algum deles: a razão dos fechamentos é um fator redondo
    (k, 1/k ou fração comum, `core.b3_precos_saneamento.e_fator_redondo`) ou o
    preço variou mais de 50%; o fator de cotação mudou; ou a especificação traz
    EG/EB. JCP e dividendo não pulam.
    """
    from core.b3_precos_saneamento import e_fator_redondo

    base = _as_date(data_base)
    linhas = [(_as_date(d), c, f, e) for d, c, f, e in pregoes]
    for i, (d, close, fator, espec) in enumerate(linhas):
        if d is None or (base is not None and d <= base):
            continue
        marcas = set(str(espec or "").upper().split()) & MARCADORES_DE_ESCALA
        if marcas:
            return f"marcador {'/'.join(sorted(marcas))} em {d:%d/%m/%Y}"
        if i == 0:
            continue
        d0, c0, f0, _ = linhas[i - 1]
        if f0 is not None and fator is not None and int(f0) != int(fator):
            return f"fator de cotação {int(f0)}→{int(fator)} em {d:%d/%m/%Y}"
        try:
            razao = float(close) / float(c0)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if e_fator_redondo(razao):
            return f"razão {razao:.4f} (fator redondo) em {d:%d/%m/%Y}"
        if abs(razao - 1.0) > SALTO_DE_ESCALA:
            return f"salto de {razao - 1.0:+.0%} em {d:%d/%m/%Y}"
    return None


def _as_date(valor) -> _dt.date | None:
    if valor is None:
        return None
    if isinstance(valor, _dt.datetime):
        return valor.date()
    if isinstance(valor, _dt.date):
        return valor
    ts = instante(valor)
    return ts.astimezone(_TZ_PREGAO).date() if ts else None


def preco_vigente(barra, slot):
    """Leitor único do "preço atual" da ação: a mais nova entre a última barra
    de `historical_prices` e o fechamento do slot (`b3_cotacao_atual`).

    ``barra`` = ``(data, close)``; ``slot`` = ``(price, price_at)``; qualquer um
    pode ser ``None``. Devolve ``(preço, instante, fonte)`` ou ``None``. Uma
    barra só tem a data (início do dia em São Paulo), então o fechamento do
    mesmo dia a vence -- é posterior à cotação intradiária.
    """
    b = None
    if barra and barra[1] is not None and not (isinstance(barra[1], float) and math.isnan(barra[1])):
        b = (float(barra[1]), instante(barra[0]), "brapi")
    s = None
    if slot and slot[0] is not None and instante(slot[1]) is not None:
        s = (float(slot[0]), instante(slot[1]), FONTE_FECHAMENTO_PREGAO)
    if b is None or s is None:
        return b or s
    return s if mais_nova_vence(b[0], b[1], s[1]) else b


# Razões de mercado do spot da brapi são calculadas sobre a cotação DELA
# (`regularMarketPrice` do mesmo quote), que não é necessariamente o close da
# última barra de `historical_prices`.
_RAZAO_COM_PRECO = {"P/L": 1, "P/VP": 1, "DY": -1}
# P/L x LPA mais longe que isto do preço de referência não é o preço do quote:
# P/L e LPA vieram de bases diferentes. TAEE3 (armazém, 12/11/2026): P/L 2,76
# (preço ÷ LPA da unit TAEE11, 4,72) x LPA 1,57 (por ação) = 4,34, 1/3 do preço,
# e o DY 7,69% virava 2,53% reescalado para 13,20. Mesmo padrão no TAEE4.
SPOT_PRECO_DESVIO_MAX = 1.5


def preco_do_spot(spot: dict) -> float | None:
    """Preço sobre o qual as razões spot foram calculadas: P/L x LPA, os dois do
    MESMO quote (P/L = preço ÷ LPA). Sem um dos dois, ``None``: o denominador não
    é presumido."""
    try:
        pl, lpa = float(spot.get("P/L")), float(spot.get("LPA"))
    except (TypeError, ValueError):
        return None
    p = pl * lpa
    return p if p > 0 else None


def rebasear_spot(spot: dict, preco_novo, preco_barra=None) -> dict:
    """Razões spot (P/L, P/VP, DY) coerentes com o preço escolhido.

    Só age quando o preço escolhido difere do da última barra (o fechamento do
    COTAHIST venceu). Então reescala P/L e P/VP por novo/antigo e DY por
    antigo/novo, onde "antigo" é o preço do PRÓPRIO spot (`preco_do_spot`), nunca
    o close da barra. Sem como recuperá-lo com segurança, as três razões saem do
    spot e o snapshot as recalcula dos numeradores (lucro, patrimônio e
    dividendos ÷ preço escolhido), como nas ações sem spot. Mesma conta do
    rescale de DY/P-VP do FII (`fii.aplicar_fechamento_pregao`).

    "Com segurança" também exige que o preço do spot seja plausível: fora de
    1/1,5 a 1,5x o close da barra (ou do preço novo, sem barra), P/L e LPA
    vieram de bases diferentes e a escala sairia errada por esse fator. As três
    razões saem, como sem preço.
    """
    try:
        novo = float(preco_novo)
        barra = float(preco_barra) if preco_barra is not None else None
    except (TypeError, ValueError):
        return spot
    if not novo > 0 or (barra is not None and abs(novo - barra) < 5e-7):
        return spot
    antigo = preco_do_spot(spot)
    out = dict(spot)
    ref = barra if barra is not None and barra > 0 else novo
    if antigo is not None and not (
            1 / SPOT_PRECO_DESVIO_MAX <= antigo / ref <= SPOT_PRECO_DESVIO_MAX):
        antigo = None
    if antigo is None:
        for k in _RAZAO_COM_PRECO:
            out.pop(k, None)
        return out
    for k, sinal in _RAZAO_COM_PRECO.items():
        v = out.get(k)
        if v is not None:
            out[k] = float(v) * (novo / antigo if sinal > 0 else antigo / novo)
    return out
