"""Série diária do patrimônio da carteira e as métricas de risco dela (INV-A4).

Até aqui a tela de Investimentos não tinha uma única medida de risco da
carteira: nem volatilidade, nem drawdown, nem VaR — e a política do usuário
declara uma queda máxima aceitável (``max_drawdown_tolerance_pct``) que nada
comparava com coisa alguma.

**Como a série é montada.** Retorno diário por posição, no critério de
*holdings*: o retorno do dia ``t`` é o das quantidades que estavam na carteira
no fechamento de ``t-1``, marcadas no preço de ``t``, mais o provento pago em
``t``. É o mesmo número do TWR clássico ``(V_t − F_t) / V_{t-1} − 1`` com o
fluxo ``F_t = Σ Δq·P_t − proventos`` neutralizado no preço de fechamento — a
função calcula pelos fluxos, explicitamente. A consequência que protege o
resto: **erro de quantidade só muda o peso de um ativo, nunca cria retorno**.
Um aporte que a reconstrução não viu vira um salto de quantidade, e salto de
quantidade é fluxo, não ganho.

**Quantidade ao longo do tempo.** As fotos de posição
(``portfolio_position_snapshots``, mensais da XP e as da Área do Investidor da
B3) são as âncoras. Entre duas fotos, os eventos (liquidação da Movimentação
da B3, Negociação, compras da Nomad) só valem quando FECHAM com a foto seguinte;
quando não fecham, a quantidade da foto anterior segue até a próxima e o
degrau entra como fluxo. Medido em 04/10/2026: a reconstrução para trás a
partir da posição de hoje errava BBAS3, DIRR3 e GMAT3 em todas as fotos de
abril a setembro — as liquidações de GMAT3, PETR3, BBAS3 e CSMG3 são aluguel
de ações (``is_loaned``), não negociação, e as compras de DIRR3 e a venda de
BBAS3 do início de outubro ainda não estão na Movimentação.

**Preço.** ``asset_quotes`` (Supabase) — diário desde 14/04/2026 para a renda
variável da B3, BOVA11 e o câmbio; ETFs americanos convertidos pelo USDBRL do
dia. O job grava o fechamento ajustado do yfinance do dia do download e não
reajusta o passado (janela de 5 dias por execução): a série se comporta como
preço SEM ajuste, com a queda do provento antecipada em alguns pregões. Por
isso o provento entra como renda, na data de pagamento. O Tesouro Selic entra com o preço modelado pelo CDI (PU de
hoje descontado pelo CDI diário) e é declarado como modelado. IPCA+,
prefixado, CDB e fundos ficam fora — não há PU diário deles no banco.

**Ativo sem preço nunca vira retorno zero.** No dia em que um ativo não tem
preço (ou tem um salto acima de 35%, o mesmo corte das séries do armazém), ele
sai daquele dia, e a cobertura diária diz quanto do valor mediu.
"""
from __future__ import annotations

import logging
import math
import re
from bisect import bisect_left, bisect_right
from datetime import date, timedelta
from statistics import median
from typing import Iterable, Sequence

from core.config import settings
from core.user_context import user_cache_data

logger = logging.getLogger(__name__)

_DIAS_ANO = 252
_PREGOES_MES = 21
# Retorno diário acima disto é retroajuste ou erro de cadastro, não mercado:
# o mesmo corte que o armazém usa nas séries da B3 (/b3/detalhe).
_SALTO_MAX = 0.35
# Amostra mínima para volatilidade, VaR diário, Sharpe e beta (um mês de
# pregões) e para o VaR mensal por janelas de 21 pregões (três meses).
_MIN_DIAS = 20
_MIN_DIAS_MES = 63
# Janela lida do Supabase: o último ano. A cota de egress está excedida
# (restrição prevista para 14/10/2026); ~25 ativos × 252 pregões cabem em
# poucas centenas de KB, e o resultado fica 6 h em cache.
_JANELA_DIAS = 365
_FOLGA_ANCORA_DIAS = 60
# Preço do exterior e câmbio repetem no máximo até 4 dias corridos: cobre o
# feriado americano sem pregão na B3 e vice-versa, nunca uma semana parada.
_FFILL_DIAS = 4
# Cotação mais velha que isto no último pregão da série = ativo parado.
_COTACAO_PARADA_DIAS = 7
_LIQUIDACAO_PREGOES = 2
_TOL_QTD = 0.01

BENCHMARK = "BOVA11"
_FX = "USDBRL"
_RX_TESOURO_SELIC = re.compile(r"TESOURO\s+SELIC\s+(\d{4})", re.IGNORECASE)

FONTE_OBSERVADA = "cotação diária"
FONTE_MODELADA = "modelado pelo CDI"


# ─────────────────────────────────────────────────────────────────────────────
# Quantidade ao longo do tempo
# ─────────────────────────────────────────────────────────────────────────────

def _soma_eventos(eventos: Sequence[tuple[date, float]], depois: date | None,
                  ate: date | None) -> float:
    return sum(dq for d, dq in eventos
               if (depois is None or d > depois) and (ate is None or d <= ate))


def serie_quantidade(dias: Sequence[date], ancoras: dict[date, float],
                     fontes: Sequence[Sequence[tuple[date, float]]] = (),
                     tol: float = _TOL_QTD) -> tuple[list[float | None], dict]:
    """Quantidade no fechamento de cada dia, ancorada nas fotos de posição.

    ``ancoras``: {data da foto: quantidade} (fim do dia). ``fontes``: listas de
    eventos ``(data, delta)`` em ordem de preferência. Entre duas âncoras vale
    a primeira fonte cujos eventos levam uma foto à outra; sem nenhuma, a
    quantidade da foto anterior segue até a seguinte (degrau). Antes da
    primeira âncora, volta-se a partir dela pela primeira fonte com eventos;
    quantidade negativa ali vira ``None`` (desconhecida), nunca zero.

    Devolve as quantidades e ``{"conciliados", "degraus"}`` — contagem de
    intervalos com mudança de quantidade.
    """
    info = {"conciliados": 0, "degraus": 0}
    if not ancoras:
        return [None] * len(dias), info
    marcos = sorted(ancoras.items())
    datas = [d for d, _ in marcos]

    # Fonte de cada intervalo (i → i+1): índice da fonte, ou None = degrau.
    escolha: list[int | None] = []
    for (a, qa), (b, qb) in zip(marcos[:-1], marcos[1:]):
        fonte = None
        for k, evs in enumerate(fontes):
            if abs(qa + _soma_eventos(evs, a, b) - qb) <= tol:
                fonte = k
                break
        escolha.append(fonte)
        mudou = abs(qb - qa) > tol or any(_soma_eventos(evs, a, b) for evs in fontes)
        if mudou:
            info["conciliados" if fonte is not None else "degraus"] += 1

    antes = next((evs for evs in fontes if evs), ())
    saida: list[float | None] = []
    for d in dias:
        i = bisect_right(datas, d) - 1
        if i < 0:
            q = marcos[0][1] - _soma_eventos(antes, d, marcos[0][0])
            saida.append(q if q >= -tol else None)
            continue
        a, qa = marcos[i]
        if i >= len(escolha):
            # Depois da última foto: segue pelos eventos da fonte preferida.
            saida.append(qa + _soma_eventos(antes, a, d))
            continue
        k = escolha[i]
        saida.append(qa if k is None else qa + _soma_eventos(fontes[k], a, d))
    return [None if q is None else max(q, 0.0) for q in saida], info


def recuar_pregoes(d: date, dias: Sequence[date], n: int) -> date:
    """O ``n``-ésimo pregão antes de ``d``; fora do calendário, dias úteis."""
    k = bisect_left(dias, d)
    if dias and dias[0] <= d <= dias[-1] + timedelta(days=_FFILL_DIAS) and k >= n:
        return dias[k - n]
    out = d
    while n > 0:
        out -= timedelta(days=1)
        if out.weekday() < 5:
            n -= 1
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Retorno diário (TWR) e cobertura
# ─────────────────────────────────────────────────────────────────────────────

def retornos_diarios(dias: Sequence[date],
                     quantidades: dict[str, Sequence[float | None]],
                     precos: dict[str, Sequence[float | None]],
                     proventos: dict[str, dict[date, float]] | None = None,
                     *, salto_max: float = _SALTO_MAX) -> list[dict]:
    """Retorno de cada dia pelos fluxos, só com os ativos medíveis no dia.

    Para o dia ``t`` entram os ativos com quantidade em ``t-1`` e preço válido
    em ``t-1`` e em ``t`` (sem salto acima de ``salto_max``). Sobre eles:
    ``V_ant = Σ q_{t-1}·P_{t-1}``, ``V = Σ q_t·P_t`` e o fluxo
    ``F = Σ (q_t − q_{t-1})·P_t − proventos_t``; ``r = (V − F)/V_ant − 1``.

    ``cobertura`` é ``V_ant`` sobre o valor de tudo que estava na carteira em
    ``t-1`` (preço mais recente conhecido para quem ficou fora). Dia sem nenhum
    ativo medível devolve ``retorno=None`` — não zero.
    """
    proventos = proventos or {}
    saida: list[dict] = []
    # Referência de valor de quem ficou fora no dia: o último preço visto; antes
    # do primeiro, o primeiro da série — ativo carregado antes de ter cotação
    # (SPY só tem preço desde 12/05/2026) precisa pesar na cobertura, não sumir.
    ultimo: dict[str, float] = {}
    for tk, ps in precos.items():
        primeiro = next((p for p in ps if p is not None and p > 0), None)
        if primeiro is not None:
            ultimo[tk] = primeiro
    for i in range(1, len(dias)):
        v_ant = v = fluxo = universo = 0.0
        n = 0
        saltos: list[str] = []
        for tk, qs in quantidades.items():
            ps = precos.get(tk)
            q0 = qs[i - 1]
            if not q0 or ps is None:
                continue
            p0, p1 = ps[i - 1], ps[i]
            ref = p0 if p0 is not None and p0 > 0 else ultimo.get(tk)
            if ref:
                universo += q0 * ref
            if p0 is None or p1 is None or p0 <= 0 or p1 <= 0:
                continue
            if abs(p1 / p0 - 1.0) > salto_max:
                saltos.append(tk)
                continue
            q1 = qs[i] if qs[i] is not None else q0
            v_ant += q0 * p0
            v += q1 * p1
            fluxo += (q1 - q0) * p1 - proventos.get(tk, {}).get(dias[i], 0.0)
            n += 1
        for tk, ps in precos.items():
            if ps[i] is not None and ps[i] > 0:
                ultimo[tk] = ps[i]
        saida.append({
            "data": dias[i],
            "retorno": (v - fluxo) / v_ant - 1.0 if v_ant > 0 else None,
            "valor_medido": v_ant,
            "cobertura": v_ant / universo if universo > 0 else None,
            "n_ativos": n,
            "saltos": saltos,
        })
    return saida


# ─────────────────────────────────────────────────────────────────────────────
# Métricas
# ─────────────────────────────────────────────────────────────────────────────

def _validos(retornos: Iterable[float | None]) -> list[float]:
    return [r for r in retornos if r is not None and math.isfinite(r)]


def twr(retornos: Iterable[float | None]) -> float | None:
    rs = _validos(retornos)
    if not rs:
        return None
    acc = 1.0
    for r in rs:
        acc *= 1.0 + r
    return acc - 1.0


def volatilidade_anual(retornos: Iterable[float | None]) -> float | None:
    rs = _validos(retornos)
    if len(rs) < 2:
        return None
    m = sum(rs) / len(rs)
    var = sum((r - m) ** 2 for r in rs) / (len(rs) - 1)
    return math.sqrt(var) * math.sqrt(_DIAS_ANO)


def drawdown(retornos: Sequence[float | None]) -> dict:
    """Queda máxima e atual do índice de TWR, a partir de 1 antes do 1º dia.

    Devolve frações positivas (0,12 = queda de 12%) e as posições do pico e
    do vale da queda máxima na sequência recebida.
    """
    indice = 1.0
    pico, i_pico = 1.0, -1
    maior, i_pico_max, i_vale = 0.0, -1, -1
    for i, r in enumerate(retornos):
        if r is not None and math.isfinite(r):
            indice *= 1.0 + r
        if indice > pico:
            pico, i_pico = indice, i
        queda = 1.0 - indice / pico
        if queda > maior:
            maior, i_pico_max, i_vale = queda, i_pico, i
    return {"max": maior, "atual": 1.0 - indice / pico,
            "i_pico": i_pico_max, "i_vale": i_vale}


def _quantil_inferior(valores: list[float], nivel: float) -> float:
    """Estatística de ordem: a perda observada no corte, sem interpolar."""
    ordenados = sorted(valores)
    k = int(math.floor((1.0 - nivel) * (len(ordenados) - 1)))
    return ordenados[k]


def var_historico(retornos: Iterable[float | None], nivel: float = 0.95) -> float | None:
    """VaR histórico como perda positiva: 0,02 = perder 2% ou mais em 5% dos dias."""
    rs = _validos(retornos)
    if len(rs) < _MIN_DIAS:
        return None
    return max(-_quantil_inferior(rs, nivel), 0.0)


def retornos_em_janela(retornos: Sequence[float | None], janela: int = _PREGOES_MES) -> list[float]:
    """Retornos compostos de janelas móveis (sobrepostas) de ``janela`` dias."""
    rs = _validos(retornos)
    if len(rs) < janela:
        return []
    out = []
    for i in range(len(rs) - janela + 1):
        acc = 1.0
        for r in rs[i:i + janela]:
            acc *= 1.0 + r
        out.append(acc - 1.0)
    return out


def var_mensal(retornos: Sequence[float | None], nivel: float = 0.95) -> float | None:
    """VaR de 21 pregões pelas janelas móveis; ``None`` com menos de 63 dias.

    As janelas se sobrepõem — com seis meses de série são ~100 janelas, mas só
    ~6 independentes. É estimativa de amostra curta, e a tela diz isso.
    """
    if len(_validos(retornos)) < _MIN_DIAS_MES:
        return None
    janelas = retornos_em_janela(retornos)
    return max(-_quantil_inferior(janelas, nivel), 0.0) if janelas else None


def sharpe(retornos: Sequence[float | None], cdi_dia: Sequence[float | None]) -> float | None:
    """Sharpe anualizado do excesso diário sobre o CDI do mesmo dia (fração)."""
    exc = [r - c for r, c in zip(retornos, cdi_dia)
           if r is not None and c is not None and math.isfinite(r)]
    if len(exc) < _MIN_DIAS:
        return None
    m = sum(exc) / len(exc)
    dp = math.sqrt(sum((x - m) ** 2 for x in exc) / (len(exc) - 1))
    return m / dp * math.sqrt(_DIAS_ANO) if dp > 0 else None


def beta(retornos: Sequence[float | None], referencia: Sequence[float | None]) -> tuple[float | None, int]:
    pares = [(r, b) for r, b in zip(retornos, referencia) if r is not None and b is not None]
    if len(pares) < _MIN_DIAS:
        return None, len(pares)
    mr = sum(r for r, _ in pares) / len(pares)
    mb = sum(b for _, b in pares) / len(pares)
    cov = sum((r - mr) * (b - mb) for r, b in pares)
    var = sum((b - mb) ** 2 for _, b in pares)
    return (cov / var if var > 0 else None), len(pares)


def alerta_drawdown(max_dd: float | None, atual_dd: float | None,
                    tolerancia_pct: float | None) -> dict:
    """Compara as quedas com a queda máxima aceitável declarada na política."""
    if tolerancia_pct is None:
        return {"nivel": "sem_tolerancia",
                "texto": "A política não declara a queda máxima aceitável "
                         "(Inteligência dos Ativos → estratégia)."}
    if max_dd is None:
        return {"nivel": "sem_dado", "texto": "Série curta demais para medir a queda."}
    tol = float(tolerancia_pct) / 100.0
    if atual_dd is not None and atual_dd > tol:
        return {"nivel": "atual_excede",
                "texto": f"A carteira está {_pt(atual_dd)} abaixo do pico, acima da "
                         f"queda máxima aceitável de {_pt(tol, 0)} declarada na política."}
    if max_dd > tol:
        return {"nivel": "excedido",
                "texto": f"A queda máxima do período ({_pt(max_dd)}) passou da "
                         f"tolerância de {_pt(tol, 0)} declarada na política."}
    return {"nivel": "dentro",
            "texto": f"Queda máxima de {_pt(max_dd)}, dentro da tolerância de "
                     f"{_pt(tol, 0)} declarada na política."}


def _pt(v: float, casas: int = 1) -> str:
    """Percentual com vírgula, como a tela mostra (0,083 → '8,3%')."""
    return f"{v * 100:.{casas}f}%".replace(".", ",")


# ─────────────────────────────────────────────────────────────────────────────
# Preços
# ─────────────────────────────────────────────────────────────────────────────

def alinhar_precos(dias: Sequence[date], cotacoes: dict[date, float],
                   ffill_dias: int = 0) -> list[float | None]:
    """Preço de cada pregão; repete o último até ``ffill_dias`` corridos."""
    datas = sorted(cotacoes)
    out: list[float | None] = []
    for d in dias:
        if d in cotacoes:
            out.append(cotacoes[d])
            continue
        if ffill_dias:
            i = bisect_right(datas, d) - 1
            if i >= 0 and (d - datas[i]).days <= ffill_dias:
                out.append(cotacoes[datas[i]])
                continue
        out.append(None)
    return out


def preco_modelado_cdi(dias: Sequence[date], preco_hoje: float, hoje: date,
                       cdi: dict[date, float]) -> list[float | None]:
    """PU de cada dia = PU de hoje ÷ CDI acumulado de ``d`` (inclusive) a hoje.

    Série 12 do BCB: a taxa de ``d`` remunera de ``d`` até o próximo dia útil,
    então ``P(t+1) = P(t)·(1 + cdi_t)`` e o retorno do dia é o CDI da véspera.
    """
    datas = sorted(k for k in cdi if k < hoje)
    # sufixo[i] = Π (1 + cdi) de datas[i] até a última antes de hoje.
    sufixo = [1.0] * (len(datas) + 1)
    for i in range(len(datas) - 1, -1, -1):
        sufixo[i] = sufixo[i + 1] * (1.0 + cdi[datas[i]] / 100.0)
    return [preco_hoje / sufixo[bisect_left(datas, d)] for d in dias]


def cdi_por_dia(dias: Sequence[date], cdi: dict[date, float]) -> list[float | None]:
    """CDI que remunerou cada retorno: o do pregão anterior (fração)."""
    return [(cdi[dias[i - 1]] / 100.0) if dias[i - 1] in cdi else None
            for i in range(1, len(dias))]


# ─────────────────────────────────────────────────────────────────────────────
# Contexto da LLM
# ─────────────────────────────────────────────────────────────────────────────

def _pct(v: float | None, casas: int = 1) -> str:
    return "ausente" if v is None else f"{v * 100:.{casas}f}%"


def bloco_risco_para_prompt(risco: dict | None) -> str:
    """Bloco "RISCO DA CARTEIRA" para o chat da Visão Geral; só percentuais."""
    if not risco:
        return ""
    if not risco.get("disponivel"):
        return ("RISCO DA CARTEIRA (série diária): indisponível — "
                + str(risco.get("motivo") or "sem motivo informado") + ".")
    cob = risco.get("cobertura") or {}
    tol = risco.get("tolerancia_pct")
    linhas = [
        f"RISCO DA CARTEIRA (série {risco.get('frequencia', 'diária')} de "
        f"{risco['inicio']:%d/%m/%Y} a {risco['fim']:%d/%m/%Y}, {risco['n_dias']} pregões):",
        f"- Cobertura: {_pct(cob.get('pct_observado'))} do patrimônio com cotação diária"
        f" + {_pct(cob.get('pct_modelado'))} modelado pelo CDI (Tesouro Selic);"
        f" {_pct(cob.get('pct_fora'))} fora da série ("
        + (", ".join(sorted({e['motivo_curto'] for e in risco.get('excluidos') or []}))
           or "nada") + ").",
        f"- TWR do período: {_pct(risco.get('twr'), 2)} | CDI no mesmo período: "
        f"{_pct(risco.get('cdi_periodo'), 2)}",
        f"- Volatilidade anualizada: {_pct(risco.get('vol_anual'))}",
        f"- Queda máxima (drawdown): {_pct(risco.get('max_drawdown'))} | "
        f"queda atual desde o pico: {_pct(risco.get('drawdown_atual'))}",
        f"- VaR histórico 95%: 1 dia {_pct(risco.get('var_1d'), 2)} | 1 mês (21 pregões) "
        f"{_pct(risco.get('var_1m'), 2)}",
        "- Sharpe contra o CDI: " + ("ausente" if risco.get("sharpe") is None
                                     else f"{risco['sharpe']:.2f}"),
        f"- Beta contra o {BENCHMARK} (proxy do Ibovespa): "
        + ("ausente" if risco.get("beta") is None else f"{risco['beta']:.2f}"),
        "- Queda máxima aceitável na política: "
        + ("não declarada" if tol is None else f"{tol:.0f}%")
        + f" → {(risco.get('alerta') or {}).get('texto', '')}",
        "- Leitura: medida sobre a parte coberta; o que está fora não entra "
        "no risco, e série de meses não estima cauda de anos. Proventos só os "
        "da B3: os ETFs americanos entram sem dividendos (não registrados).",
    ]
    return "\n".join(linhas)


REGRA_RISCO = ("- Pergunta sobre risco, volatilidade, queda ou perda possível: "
               "cite os números do bloco RISCO DA CARTEIRA com o período e a "
               "cobertura; não estime risco por outro caminho.")


# ─────────────────────────────────────────────────────────────────────────────
# Carga (I/O)
# ─────────────────────────────────────────────────────────────────────────────

_SQL_COTACOES = """
    SELECT UPPER(TRIM(a.ticker)) AS ticker, q.timestamp::date AS data, q.close AS fechamento
    FROM asset_quotes q
    JOIN assets a ON a.id = q.asset_id
    WHERE UPPER(TRIM(a.ticker)) = ANY(:tickers)
      AND q.timestamp >= :ini
      AND q.close IS NOT NULL
    ORDER BY q.timestamp
"""

_SQL_FOTOS = """
    SELECT s.report_date AS data, UPPER(TRIM(a.ticker)) AS ticker,
           COALESCE(s.currency, a.currency, 'BRL') AS moeda,
           COALESCE(s.institution, '') AS instituicao,
           SUM(s.quantity) AS quantidade
    FROM portfolio_position_snapshots s
    JOIN assets a ON a.id = s.asset_id
    WHERE s.user_id = :uid
      AND s.report_date BETWEEN :ini AND :fim
    GROUP BY 1, 2, 3, 4
"""

_SQL_EVENTOS = """
    SELECT event_date AS data, ticker, movement AS movimento,
           direction AS sentido, quantity AS quantidade, total_value AS valor
    FROM investment_movement_events
    WHERE user_id = :uid
      AND event_date BETWEEN :ini AND :fim
"""

_SQL_NEGOCIACOES = """
    SELECT t.transaction_date::date AS data, UPPER(TRIM(a.ticker)) AS ticker,
           t.type AS tipo, t.quantity AS quantidade
    FROM investment_transactions t
    JOIN assets a ON a.id = t.asset_id
    WHERE t.user_id = :uid
      AND t.type IN ('buy', 'sell')
      AND t.transaction_date::date BETWEEN :ini AND :fim
"""

_MOTIVO_CURTO = {
    "tesouro": "Tesouro IPCA+/prefixado sem PU diário",
    "renda_fixa": "CDB/renda fixa sem PU diário",
    "fundo": "fundos e FIP sem cota diária",
    "sem_cotacao": "ativos sem cotação diária",
    "parada": "cotação parada",
    "quantidade": "quantidade não reconstruída",
    "outro": "classe sem série de preço",
}
_MOTIVO = {
    "tesouro": "Tesouro IPCA+ e prefixado: o banco guarda só o PU de hoje, e o "
               "preço deles não segue o CDI — modelar inventaria a marcação.",
    "renda_fixa": "CDB e renda fixa privada: indexador e PU diário não estão "
                  "registrados.",
    "fundo": "Fundo ou FIP: a cota diária não está registrada (FIP é private "
             "equity, sem cotação).",
    "sem_cotacao": "Sem cotação diária em asset_quotes no período.",
    "parada": "A cotação parou antes do fim da série.",
    "quantidade": "A quantidade histórica não fecha com as fotos de posição.",
    "outro": "Classe sem série de preço diária.",
}


def _categoria(p: dict, classes_rv: set[str]) -> str:
    tk = str(p.get("ticker") or "").upper()
    classe = str(p.get("classe") or "")
    if p.get("moeda") == "USD":
        return "usd"
    if p.get("moeda") == "BRL" and classe in classes_rv:
        return "brl"
    if tk.startswith("TSELIC") or "SELIC" in tk:
        return "selic"
    if classe.startswith("Tesouro"):
        return "tesouro"
    if classe.startswith("Fundo") or classe == "FIP":
        return "fundo"
    if classe.startswith("Renda Fixa") or tk.startswith("CDB"):
        return "renda_fixa"
    return "outro"


def _num(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


@user_cache_data(ttl=21600)
def get_risco_carteira() -> dict:
    """Série diária da carteira e as métricas de risco, com cobertura declarada."""
    if settings.MOCK_MODE:
        return {"data_source": "mock", "disponivel": False,
                "motivo": "Risco da carteira não é simulado em modo mock"}
    try:
        return _risco_carteira_real()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[carteira_risco] indisponível (%s).", type(exc).__name__)
        return {"data_source": "error", "disponivel": False,
                "motivo": "Não foi possível montar a série diária da carteira"}


def _ler(engine, sql: str, params: dict) -> list:
    """Cada leitura na sua conexão: uma tabela ausente não derruba as outras."""
    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            return conn.execute(text(sql), params).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.info("[carteira_risco] leitura indisponível: %s", type(exc).__name__)
        return []


def _tolerancia_politica(engine, owner: str) -> tuple[float | None, str | None]:
    try:
        from core.estrategia import politica as pol
        from core.estrategia import repositorio as repo

        estado = repo.carregar(engine=engine, owner_id=owner)
        reg = estado.vigente
        if reg is None:
            return None, None
        v = pol.valores(reg.politica).get("max_drawdown_tolerance_pct")
        return (float(v) if v is not None else None), reg.status
    except Exception as exc:  # noqa: BLE001
        logger.info("[carteira_risco] política indisponível: %s", type(exc).__name__)
        return None, None


def _risco_carteira_real() -> dict:
    from core.database import get_engine
    from core.investimentos import (
        _CLASSES_RV_B3,
        _SQL_RENTAB_PROVENTOS_B3,
        _base_ticker,
        _cdi_diario_cache,
        get_carteira,
    )
    from core.movimentacao_b3 import (
        RECIBO,
        SEM_CAIXA,
        TROCA_DE_CODIGO,
        _sinal,
        interpretar,
    )

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Engine indisponível.")
    owner = settings.OWNER_USER_ID
    if not owner:
        raise RuntimeError("OWNER_USER_ID não configurado.")

    hoje = date.today()
    ini = hoje - timedelta(days=_JANELA_DIAS)
    carteira = get_carteira()
    total = _num(carteira.get("total_mercado"))

    # Posição de hoje por ticker-base (BBAS3F soma em BBAS3).
    posicao: dict[str, dict] = {}
    for p in carteira.get("posicoes") or []:
        tk = _base_ticker(p.get("ticker") or "")
        if not tk:
            continue
        cur = posicao.setdefault(tk, {"ticker": tk, "classe": p.get("classe"),
                                      "moeda": p.get("moeda"), "quantidade": 0.0,
                                      "valor": 0.0,
                                      "categoria": _categoria(p, _CLASSES_RV_B3)})
        cur["quantidade"] += _num(p.get("quantidade"))
        cur["valor"] += _num(p.get("valor_mercado"))

    mercado = [tk for tk, p in posicao.items() if p["categoria"] in ("brl", "usd")]
    cot_rows = _ler(engine, _SQL_COTACOES,
                    {"tickers": sorted(set(mercado) | {BENCHMARK, _FX}), "ini": ini})
    cotacoes: dict[str, dict[date, float]] = {}
    for r in cot_rows:
        if _num(r.fechamento) > 0:
            cotacoes.setdefault(r.ticker, {})[r.data] = _num(r.fechamento)

    # Calendário da B3: os pregões do BOVA11; sem ele, a união da renda variável BRL.
    dias = sorted(cotacoes.get(BENCHMARK, {}))
    if len(dias) < _MIN_DIAS:
        dias = sorted({d for tk in mercado if posicao[tk]["categoria"] == "brl"
                       for d in cotacoes.get(tk, {})})
    dias = [d for d in dias if d.weekday() < 5]
    if len(dias) < _MIN_DIAS + 1:
        return {"data_source": "real", "disponivel": False,
                "motivo": f"Menos de {_MIN_DIAS} pregões com cotação diária no banco"}

    # Âncoras: fotos de posição (dedup por instituição: a foto de 31/08 trouxe o
    # Tesouro duas vezes, pela XP e pelo Tesouro Direto) mais a posição de hoje.
    fotos = _ler(engine, _SQL_FOTOS, {"uid": owner, "ini": dias[0] - timedelta(days=_FOLGA_ANCORA_DIAS),
                                      "fim": hoje})
    por_inst: dict[tuple, float] = {}
    datas_familia: dict[str, set] = {"BRL": set(), "USD": set()}
    for r in fotos:
        fam = "USD" if str(r.moeda).upper() == "USD" else "BRL"
        datas_familia[fam].add(r.data)
        k = (fam, r.data, _base_ticker(r.ticker), r.instituicao)
        por_inst[k] = por_inst.get(k, 0.0) + _num(r.quantidade)
    foto_qtd: dict[tuple, float] = {}
    for (fam, d, tk, _inst), q in por_inst.items():
        foto_qtd[(fam, d, tk)] = max(foto_qtd.get((fam, d, tk), 0.0), q)

    # Eventos: liquidação da Movimentação (deslocada 2 pregões e sem deslocar —
    # vale a que fechar), ajustes de quantidade sem caixa, Negociação.
    ev_rows = _ler(engine, _SQL_EVENTOS, {"uid": owner, "ini": dias[0] - timedelta(days=_FOLGA_ANCORA_DIAS),
                                          "fim": hoje})
    liq: dict[str, list] = {}
    liq_d2: dict[str, list] = {}
    eventos_interp = []
    for r in ev_rows:
        mov = str(r.movimento or "").strip().lower()
        tk = _base_ticker(str(r.ticker or ""))
        m = _RX_TESOURO_SELIC.search(str(r.ticker or ""))
        if m:
            tk = f"TSELIC{m.group(1)}"
        sinal = _sinal(str(r.sentido or ""))
        q = abs(_num(r.quantidade))
        if not sinal:
            continue
        if mov == "transferência - liquidação" or (m and mov in ("compra", "venda")):
            liq.setdefault(tk, []).append((r.data, sinal * q))
            liq_d2.setdefault(tk, []).append(
                (recuar_pregoes(r.data, dias, _LIQUIDACAO_PREGOES) if not m else r.data,
                 sinal * q))
        elif mov in SEM_CAIXA or mov == RECIBO or mov in TROCA_DE_CODIGO:
            eventos_interp.append({"data": r.data, "ticker": tk, "movimento": mov,
                                   "sentido": r.sentido, "quantidade": r.quantidade,
                                   "valor": r.valor})
    ajustes: dict[str, list] = {}
    for a in interpretar(eventos_interp)["ajustes"]:
        if a["delta_qtd"]:
            ajustes.setdefault(a["ticker"], []).append((a["data"], a["delta_qtd"]))
    negoc: dict[str, list] = {}
    for r in _ler(engine, _SQL_NEGOCIACOES, {"uid": owner, "ini": dias[0] - timedelta(days=_FOLGA_ANCORA_DIAS),
                                             "fim": hoje}):
        tk = _base_ticker(r.ticker)
        negoc.setdefault(tk, []).append(
            (r.data, _num(r.quantidade) * (1.0 if r.tipo == "buy" else -1.0)))

    # CDI: Sharpe, a comparação do período e o preço do Tesouro Selic.
    cdi_info = _cdi_diario_cache(dias[0].isoformat(), hoje.isoformat())
    cdi = cdi_info.get("serie") or {}
    cdi_ok = bool(cdi) and not cdi_info.get("motivo")

    fx = alinhar_precos(dias, cotacoes.get(_FX, {}), _FFILL_DIAS)
    quantidades: dict[str, list] = {}
    precos: dict[str, list] = {}
    fonte_de: dict[str, str] = {}
    excluidos: list[dict] = []
    degraus: list[str] = []
    n_conc = n_deg = 0
    ultimo = dias[-1]

    def _excluir(p: dict, cat: str) -> None:
        excluidos.append({"ticker": p["ticker"], "classe": p["classe"], "valor": p["valor"],
                          "motivo": _MOTIVO[cat], "motivo_curto": _MOTIVO_CURTO[cat]})

    for tk, p in sorted(posicao.items()):
        cat = p["categoria"]
        if cat in ("brl", "usd"):
            cot = cotacoes.get(tk, {})
            if not cot:
                _excluir(p, "sem_cotacao")
                continue
            if (ultimo - max(cot)).days > _COTACAO_PARADA_DIAS:
                _excluir(p, "parada")
                continue
            if cat == "usd":
                pu = alinhar_precos(dias, cot, _FFILL_DIAS)
                ps = [a * b if a is not None and b is not None else None for a, b in zip(pu, fx)]
                fontes = [negoc.get(tk, [])]
            else:
                ps = alinhar_precos(dias, cot)
                extra = ajustes.get(tk, [])
                fontes = [liq_d2.get(tk, []) + extra, liq.get(tk, []) + extra, negoc.get(tk, [])]
            fonte_de[tk] = FONTE_OBSERVADA
        elif cat == "selic":
            if not cdi_ok or p["quantidade"] <= 0:
                _excluir(p, "outro")
                continue
            ps = preco_modelado_cdi(dias, p["valor"] / p["quantidade"], hoje, cdi)
            fontes = [liq.get(tk, [])]
            fonte_de[tk] = FONTE_MODELADA
            cat = "brl"
        else:
            _excluir(p, cat)
            continue

        fam = "USD" if cat == "usd" else "BRL"
        ancoras = {d: foto_qtd.get((fam, d, tk), 0.0) for d in datas_familia[fam] if d < hoje}
        ancoras[hoje] = p["quantidade"]
        qs, info = serie_quantidade(dias, ancoras, fontes)
        if not any(q for q in qs):
            fonte_de.pop(tk, None)
            _excluir(p, "quantidade")
            continue
        n_conc += info["conciliados"]
        n_deg += info["degraus"]
        if info["degraus"]:
            degraus.append(tk)
        quantidades[tk] = qs
        precos[tk] = ps

    # Proventos (BRL, deduplicados como na rentabilidade) caem no 1º pregão
    # na data de pagamento ou depois dela.
    proventos: dict[str, dict[date, float]] = {}
    for r in _ler(engine, _SQL_RENTAB_PROVENTOS_B3, {"uid": owner}):
        tk = _base_ticker(r.ticker)
        if tk not in quantidades or r.data is None or r.data <= dias[0] or r.data > ultimo:
            continue
        d = dias[bisect_left(dias, r.data)]
        proventos.setdefault(tk, {})[d] = proventos.get(tk, {}).get(d, 0.0) + _num(r.valor)

    serie = retornos_diarios(dias, quantidades, precos, proventos)
    rets = [s["retorno"] for s in serie]
    bova = alinhar_precos(dias, cotacoes.get(BENCHMARK, {}))
    ref = [(b1 / b0 - 1.0) if b0 and b1 and abs(b1 / b0 - 1.0) <= _SALTO_MAX else None
           for b0, b1 in zip(bova[:-1], bova[1:])]
    validos = [s for s in serie if s["retorno"] is not None]
    if len(validos) < _MIN_DIAS:
        return {"data_source": "real", "disponivel": False,
                "motivo": f"Menos de {_MIN_DIAS} pregões com retorno medido",
                "excluidos": excluidos}

    dd = drawdown(rets)
    b, n_beta = beta(rets, ref)
    tol, status_pol = _tolerancia_politica(engine, owner)
    observado = sum(posicao[tk]["valor"] for tk, f in fonte_de.items() if f == FONTE_OBSERVADA)
    modelado = sum(posicao[tk]["valor"] for tk, f in fonte_de.items() if f == FONTE_MODELADA)
    coberturas = [s["cobertura"] for s in validos if s["cobertura"] is not None]
    indice, acc = [], 1.0
    pico = 1.0
    for s in serie:
        if s["retorno"] is not None:
            acc *= 1.0 + s["retorno"]
        pico = max(pico, acc)
        indice.append({"data": s["data"], "indice": acc, "drawdown": 1.0 - acc / pico,
                       "cobertura": s["cobertura"]})

    inicio, fim = dias[0], ultimo
    cdi_periodo = None
    if cdi_ok:
        from core.rentabilidade import fator_cdi
        cdi_periodo = fator_cdi(cdi, inicio, fim) - 1.0

    return {
        "data_source": "real",
        "disponivel": True,
        "motivo": None,
        "frequencia": "diária",
        "inicio": inicio,
        "fim": fim,
        "n_dias": len(validos),
        "twr": twr(rets),
        "cdi_periodo": cdi_periodo,
        "vol_anual": volatilidade_anual(rets) if len(validos) >= _MIN_DIAS else None,
        "max_drawdown": dd["max"],
        "drawdown_atual": dd["atual"],
        "pico_data": serie[dd["i_pico"]]["data"] if dd["i_pico"] >= 0 else inicio,
        "vale_data": serie[dd["i_vale"]]["data"] if dd["i_vale"] >= 0 else None,
        "var_1d": var_historico(rets),
        "var_1m": var_mensal(rets),
        "n_janelas_1m": len(retornos_em_janela(rets)),
        "sharpe": sharpe(rets, cdi_por_dia(dias, cdi)) if cdi_ok else None,
        "beta": b,
        "n_beta": n_beta,
        "cdi_fonte": cdi_info.get("fonte"),
        "cdi_motivo": cdi_info.get("motivo"),
        "tolerancia_pct": tol,
        "politica_status": status_pol,
        "alerta": alerta_drawdown(dd["max"], dd["atual"], tol),
        "cobertura": {
            "total": total,
            "observado": observado,
            "modelado": modelado,
            "fora": max(total - observado - modelado, 0.0),
            "pct_observado": observado / total if total else None,
            "pct_modelado": modelado / total if total else None,
            "pct_fora": max(total - observado - modelado, 0.0) / total if total else None,
            "diaria_min": min(coberturas) if coberturas else None,
            "diaria_mediana": median(coberturas) if coberturas else None,
        },
        "incluidos": sorted(({"ticker": tk, "classe": posicao[tk]["classe"],
                              "valor": posicao[tk]["valor"], "fonte": f}
                             for tk, f in fonte_de.items()),
                            key=lambda x: (-x["valor"], x["ticker"])),
        "excluidos": sorted(excluidos, key=lambda x: (-x["valor"], x["ticker"])),
        "quantidade": {"conciliados": n_conc, "degraus": n_deg, "com_degrau": sorted(degraus)},
        "saltos": sorted({tk for s in serie for tk in s["saltos"]}),
        "proventos_no_periodo": sum(v for m in proventos.values() for v in m.values()),
        "serie": indice,
    }
