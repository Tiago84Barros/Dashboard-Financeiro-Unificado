"""Ferramentas que a LLM dos chats chama para buscar a série que a resposta pede.

O bloco CONTEXTO DE MERCADO dá a foto e a trajetória das séries que cabem num
prompt. A pergunta que precisa de outra coisa -- "qual foi a maior queda de
ITUB4 em cinco anos?", "compare WEGE3 e ITUB4 em três anos", "o que o BOVA11
fez depois das vezes em que o IPCA passou de 6%?" -- ficava sem resposta, ou
pior, com número de memória. Com function calling a LLM pede a série e recebe
o dado com fonte, janela e n, no mesmo formato do resto do contexto.

Três ferramentas, todas lendo o que a produção alcança:

``serie_macro``
    Séries macro e de mercado mês a mês: o painel publicado da Memória de
    Mercado (Selic, IPCA 12m, dólar 12m, Treasury, P/L mediano, BOVA11 12m,
    desde 2010), o CDI publicado e, do Supabase, dólar, SPY, BOVA11, SMAL11 e
    os títulos do Tesouro.
``preco_ativo``
    Fechamento mensal de até quatro ativos: B3 e FII em
    ``market.historical_prices``, ações americanas em
    ``market_us.prices_monthly``. Devolve retornos, máxima queda e os pontos.
``meses_com_condicao``
    Os meses do painel em que uma condição macro valeu, agrupados em
    episódios, com o que veio depois e o contraponto de todos os meses.

O egress do Supabase estourou a cota: o SQL agrega para um
ponto por mês antes de sair do banco, e cada resultado tem teto de caracteres.
Nada aqui levanta: falha vira texto que nomeia a falha, e a LLM a repete como
ausência declarada.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Iterable, Mapping, Sequence

logger = logging.getLogger(__name__)

#: Teto de cada resultado: o resultado volta ao prompt e conta tokens a cada
#: rodada seguinte.
MAX_CHARS_RESULTADO = 3500
#: Pontos listados por série; acima disso a série sai trimestral, depois anual.
MAX_PONTOS = 40
MAX_ANOS = 20
MAX_TICKERS = 4

REGRA_FERRAMENTAS = (
    "FERRAMENTAS DE DADOS: você tem ferramentas que consultam o banco do app — "
    "séries macro mês a mês desde 2010 (serie_macro), o preço mensal de qualquer "
    "ação ou FII da B3 e de ações americanas (preco_ativo) e os meses do "
    "histórico em que uma condição macro valeu, com o que veio depois "
    "(meses_com_condicao). Quando a resposta precisar de um número que o "
    "contexto não traz — a trajetória ou a maior queda de um ativo, a comparação "
    "entre ativos, o que aconteceu depois que um cenário se repetiu — CHAME a "
    "ferramenta antes de responder, em vez de dizer que o dado falta ou de "
    "estimar de memória. Não chame para o que o contexto já traz. O resultado "
    "da ferramenta é dado com fonte, e conta como dado do contexto para todas "
    "as regras acima: cite o número com a fonte, a janela e o n que vêm com ele, "
    "e a ressalva colada ao número. Ferramenta que devolve falha ou 'sem dado' "
    "é ausência declarada: diga o que faltou, nunca preencha de memória."
)


@dataclass(frozen=True)
class Ferramenta:
    nome: str
    descricao: str
    parametros: Mapping
    executar: Callable[..., str]

    def esquema(self) -> dict:
        return {"type": "function",
                "function": {"name": self.nome, "description": self.descricao,
                             "parameters": dict(self.parametros)}}


# ─────────────────────────────────────────────────────────────────────────────
# Dependências (substituídas nos testes)
# ─────────────────────────────────────────────────────────────────────────────

def _engine():
    from core.database import get_engine

    return get_engine()


def _painel() -> tuple[list[dict] | None, str]:
    from core.memoria_mercado import cenarios_macro as cmac

    carga, origem = cmac.carregar_publicado()
    return (carga or {}).get("painel"), origem


def _cdi() -> dict[date, float]:
    from core.rentabilidade import ler_cdi_publicado

    return ler_cdi_publicado()


# ─────────────────────────────────────────────────────────────────────────────
# Formatação
# ─────────────────────────────────────────────────────────────────────────────

def _br(v: float, casas: int = 2) -> str:
    texto = f"{v:,.{casas}f}"
    return texto.replace(",", "§").replace(".", ",").replace("§", ".")


def _pct(v: float | None, casas: int = 1) -> str:
    if v is None:
        return "?"
    return f"{'+' if v >= 0 else '−'}{_br(abs(100 * v), casas)}%"


def _mes_br(m: str) -> str:
    return f"{m[5:7]}/{m[:4]}"


def _limpo(exc: object, limite: int = 160) -> str:
    return " ".join(str(exc).split())[:limite]


def _cortar(texto: str) -> str:
    if len(texto) <= MAX_CHARS_RESULTADO:
        return texto
    return texto[:MAX_CHARS_RESULTADO - 60].rstrip() + "\n[resultado cortado no teto de tamanho]"


def _amostrar(pontos: list[tuple[str, float]]) -> tuple[list[tuple[str, float]], str]:
    """Mensal até :data:`MAX_PONTOS`; depois trimestral e anual. O último fica."""
    for passo, rotulo in ((1, "mensal"), (3, "trimestral"), (12, "anual")):
        escolhidos = pontos[::-1][::passo][::-1]
        if len(escolhidos) <= MAX_PONTOS:
            return escolhidos, rotulo
    return pontos[::-1][::12][::-1][-MAX_PONTOS:], "anual (só os mais recentes)"


def _anos_arg(valor, padrao: int) -> int:
    try:
        anos = int(float(valor))
    except (TypeError, ValueError):
        return padrao
    return max(1, min(MAX_ANOS, anos))


def _mes_de(d) -> str:
    if isinstance(d, datetime):
        d = d.date()
    return d.isoformat()[:7] if isinstance(d, date) else str(d)[:7]


def _ultimo_por_mes(pontos: Iterable[tuple]) -> list[tuple[str, float]]:
    """(data, valor) em qualquer frequência -> último valor de cada mês."""
    por_mes: dict[str, tuple] = {}
    for d, v in sorted(pontos, key=lambda p: str(p[0])):
        if v is None or v != v:
            continue
        por_mes[_mes_de(d)] = (d, float(v))
    return [(m, dv[1]) for m, dv in sorted(por_mes.items())]


def _ultima_data(pontos: Iterable[tuple]) -> date | None:
    datas = [d.date() if isinstance(d, datetime) else d for d, v in pontos
             if v is not None and isinstance(d, date)]
    return max(datas) if datas else None


def _quando(m: str, ultima: date | None) -> str:
    """O mês corrente é parcial: a linha diz até que dia vai o último ponto."""
    if ultima is not None and _mes_de(ultima) == m:
        return f"em {ultima:%d/%m/%Y}"
    return f"em {_mes_br(m)}"


def _desde(pontos: list[tuple[str, float]], anos: int) -> list[tuple[str, float]]:
    if not pontos:
        return pontos
    ultimo = pontos[-1][0]
    corte = f"{int(ultimo[:4]) - anos:04d}{ultimo[4:]}"
    return [p for p in pontos if p[0] > corte]


def _maior_queda(pontos: list[tuple[str, float]]) -> tuple[float, str, str] | None:
    """Maior queda de pico a vale nos fechamentos mensais: (queda, pico, vale)."""
    pior = None
    pico_v, pico_m = None, None
    for m, v in pontos:
        if pico_v is None or v > pico_v:
            pico_v, pico_m = v, m
            continue
        if pico_v > 0:
            queda = v / pico_v - 1
            if pior is None or queda < pior[0]:
                pior = (queda, pico_m, m)
    return pior


# ─────────────────────────────────────────────────────────────────────────────
# serie_macro
# ─────────────────────────────────────────────────────────────────────────────

#: chave -> (rótulo, unidade, de onde vem). Unidade: "pct" (valor em %),
#: "pp" (variação em p.p.), "fracao" (variação; sai em %), "x" (múltiplo),
#: "preco".
_PAINEL = {
    "selic": ("Selic meta (% a.a.)", "pct", "selic"),
    "direcao_selic_6m": ("variação da Selic em 6 meses (p.p.)", "pp", "selic_6m"),
    "ipca_12m": ("IPCA acumulado em 12 meses (%)", "pct", "ipca12"),
    "dolar_12m": ("variação do dólar (USDBRL médio) em 12 meses", "fracao", "usd12"),
    "treasury_10a": ("Treasury de 10 anos dos EUA (%)", "pct", "us10"),
    "pl_mediano": ("P/L mediano das 50 ações mais negociadas da B3 — NÃO é o P/L "
                   "do Ibovespa", "x", "pl"),
    "bova11_12m": ("retorno do BOVA11 em 12 meses", "fracao", "bova12"),
}
_SUPABASE = {
    "dolar": ("Dólar (USDBRL)", "preco", "cotacoes", "USDBRL"),
    "spy": ("SPY (ETF do S&P 500, US$)", "preco", "cotacoes", "SPY"),
    "bova11": ("BOVA11 (ETF do Ibovespa, R$)", "preco", "historico", "BOVA11"),
    "smal11": ("SMAL11 (ETF de small caps, R$)", "preco", "historico", "SMAL11"),
    "tesouro_ipca_2029": ("Tesouro IPCA+ 2029, taxa de compra (juro real, %)",
                          "pct", "titulos", "TIPCA2029"),
    "tesouro_ipca_2032": ("Tesouro IPCA+ 2032, taxa de compra (juro real, %)",
                          "pct", "titulos", "TIPCA2032"),
    "tesouro_ipca_2035": ("Tesouro IPCA+ 2035, taxa de compra (juro real longo, %)",
                          "pct", "titulos", "TIPCA2035"),
    "tesouro_pre_2028": ("Tesouro Prefixado 2028, taxa de compra (%)", "pct",
                         "titulos", "TPRE2028"),
    "tesouro_pre_2032": ("Tesouro Prefixado 2032, taxa de compra (%)", "pct",
                         "titulos", "TPRE2032"),
}
SERIES_MACRO = sorted([*_PAINEL, "cdi", *_SUPABASE])

_SQL_MENSAL = {
    "cotacoes": (
        "SELECT DISTINCT ON (date_trunc('month', aq.timestamp)) aq.timestamp::date AS d, "
        "aq.close::float8 AS v FROM asset_quotes aq JOIN assets a ON a.id = aq.asset_id "
        "WHERE a.ticker = :chave AND aq.close IS NOT NULL "
        "AND aq.timestamp >= current_date - make_interval(years => :anos) "
        "ORDER BY date_trunc('month', aq.timestamp), aq.timestamp DESC"),
    "historico": (
        "SELECT DISTINCT ON (date_trunc('month', date)) date AS d, "
        "COALESCE(adjusted_close, close)::float8 AS v FROM market.historical_prices "
        "WHERE ticker = :chave AND COALESCE(adjusted_close, close) IS NOT NULL "
        "AND date >= current_date - make_interval(years => :anos) "
        "ORDER BY date_trunc('month', date), date DESC"),
    "titulos": (
        "SELECT DISTINCT ON (date_trunc('month', base_date)) base_date AS d, "
        "buy_rate::float8 AS v FROM tesouro_market_rates "
        "WHERE security_key = :chave AND buy_rate IS NOT NULL "
        "AND base_date >= current_date - make_interval(years => :anos) "
        "ORDER BY date_trunc('month', base_date), base_date DESC"),
}


def _fmt(unidade: str, v: float) -> str:
    if unidade == "pct":
        return f"{_br(v)}%"
    if unidade == "pp":
        return f"{'+' if v >= 0 else '−'}{_br(abs(v))} p.p."
    if unidade == "fracao":
        return _pct(v)
    if unidade == "x":
        return f"{_br(v, 1)}x"
    return _br(v, 4 if abs(v) < 20 else 2)


def _variacao(unidade: str, de: float, para: float) -> str:
    if unidade == "preco" or unidade == "x":
        return _pct(para / de - 1) if de else "variação indefinida"
    return f"{'+' if para - de >= 0 else '−'}{_br(abs(para - de))} p.p."


def texto_serie(rotulo: str, fonte: str, unidade: str,
                pontos: list[tuple[str, float]], anos: int,
                ultima: date | None = None) -> str:
    """Resumo e pontos de uma série mensal (mês ``AAAA-MM`` -> valor)."""
    if not pontos:
        return f"{rotulo} ({fonte}): sem observações na janela de {anos} anos."
    (m0, v0), (m1, v1) = pontos[0], pontos[-1]
    i_min = min(range(len(pontos)), key=lambda i: pontos[i][1])
    i_max = max(range(len(pontos)), key=lambda i: pontos[i][1])
    percentil = 100.0 * sum(1 for _, v in pontos if v <= v1) / len(pontos)
    anos_reais = (int(m1[:4]) * 12 + int(m1[5:7]) - int(m0[:4]) * 12 - int(m0[5:7])) / 12
    linhas = [
        f"{rotulo} — fonte: {fonte}; {len(pontos)} meses de {_mes_br(m0)} a "
        f"{_mes_br(m1)} ({_br(anos_reais, 1)} anos).",
        f"Último: {_fmt(unidade, v1)} {_quando(m1, ultima)}; no início da janela "
        f"{_fmt(unidade, v0)} ({_variacao(unidade, v0, v1)} no período). "
        f"Mínimo {_fmt(unidade, pontos[i_min][1])} em {_mes_br(pontos[i_min][0])}, "
        f"máximo {_fmt(unidade, pontos[i_max][1])} em {_mes_br(pontos[i_max][0])}; "
        f"hoje no percentil {percentil:.0f} desses {_br(anos_reais, 1)} anos.",
    ]
    if anos_reais < 3:
        linhas.append("HISTÓRICO CURTO: menos de 3 anos, não é o ciclo longo.")
    amostra, freq = _amostrar(pontos)
    linhas.append(f"Pontos ({freq}): " + "; ".join(
        f"{_mes_br(m)} {_fmt(unidade, v)}" for m, v in amostra) + ".")
    return "\n".join(linhas)


def serie_macro(serie: str = "", anos=10) -> str:
    chave = str(serie or "").strip().lower()
    anos = _anos_arg(anos, 10)
    if chave in _PAINEL:
        rotulo, unidade, campo = _PAINEL[chave]
        painel, origem = _painel()
        if not painel:
            return f"{rotulo}: painel da Memória de Mercado indisponível ({origem})."
        # O mês corrente ainda está em curso: entra, e o rótulo diz.
        pontos = [(linha["mes"], float(v)) for linha in painel
                  if (v := (linha.get("estado") or {}).get(campo)) is not None]
        return texto_serie(rotulo, f"painel da Memória de Mercado, {origem}; o mês "
                           "corrente vale até a data de geração", unidade,
                           _desde(pontos, anos), anos)
    if chave == "cdi":
        from core.macro_brasil import cdi_anualizado

        cdi = _cdi()
        if not cdi:
            return "CDI: arquivo cdi_diario ausente ou ilegível."
        brutos = [(d, cdi_anualizado(t)) for d, t in cdi.items()]
        return texto_serie("CDI anualizado (trajetória efetiva da Selic, % a.a.)",
                           "BCB/SGS 12, arquivo cdi_diario, último dia útil de cada mês",
                           "pct", _desde(_ultimo_por_mes(brutos), anos), anos,
                           _ultima_data(brutos))
    if chave in _SUPABASE:
        rotulo, unidade, tabela, sql_chave = _SUPABASE[chave]
        fonte = {"cotacoes": "asset_quotes", "historico": "market.historical_prices",
                 "titulos": "tesouro_market_rates"}[tabela]
        try:
            from sqlalchemy import text

            engine = _engine()
            if engine is None:
                return f"{rotulo}: banco Supabase indisponível neste ambiente."
            with engine.connect() as conn:
                linhas = conn.execute(text(_SQL_MENSAL[tabela]),
                                      {"chave": sql_chave, "anos": anos}).all()
        except Exception as exc:  # noqa: BLE001 - ausência declarada
            return f"{rotulo}: falha na leitura do Supabase ({_limpo(exc)})."
        brutos = [(d, v) for d, v in linhas]
        return texto_serie(rotulo, f"{fonte}, Supabase, último ponto de cada mês",
                           unidade, _ultimo_por_mes(brutos), anos, _ultima_data(brutos))
    return (f"Série '{serie}' não existe. Séries disponíveis: "
            f"{', '.join(SERIES_MACRO)}.")


# ─────────────────────────────────────────────────────────────────────────────
# preco_ativo
# ─────────────────────────────────────────────────────────────────────────────

_SQL_PRECO_B3 = (
    "SELECT DISTINCT ON (ticker, date_trunc('month', date)) ticker, date AS d, "
    "COALESCE(adjusted_close, close)::float8 AS v FROM market.historical_prices "
    "WHERE ticker = ANY(:tickers) AND COALESCE(adjusted_close, close) > 0 "
    "AND date >= current_date - make_interval(years => :anos) "
    "ORDER BY ticker, date_trunc('month', date), date DESC")
_SQL_PRECO_EUA = (
    "SELECT DISTINCT ON (symbol, date_trunc('month', month_end)) symbol AS ticker, "
    "month_end AS d, COALESCE(adjusted_close, close)::float8 AS v "
    "FROM market_us.prices_monthly WHERE symbol = ANY(:tickers) "
    "AND COALESCE(adjusted_close, close) > 0 "
    "AND month_end >= current_date - make_interval(years => :anos) "
    "ORDER BY symbol, date_trunc('month', month_end), month_end DESC")
_JANELAS_RETORNO = (1, 3, 12, 36, 60)


def _normalizar_ticker(t) -> str:
    tk = str(t or "").strip().upper()
    return tk[:-3] if tk.endswith(".SA") else tk


def _retorno(pontos: list[tuple[str, float]], meses: int) -> float | None:
    if len(pontos) <= meses:
        return None
    de = pontos[-1 - meses][1]
    return pontos[-1][1] / de - 1 if de else None


def texto_preco(ticker: str, fonte: str, pontos: list[tuple[str, float]], anos: int,
                ultima: date | None = None) -> str:
    if not pontos:
        return f"{ticker}: sem preço no banco nos últimos {anos} anos."
    (m0, v0), (m1, v1) = pontos[0], pontos[-1]
    linhas = [f"{ticker} — fonte: {fonte}; fechamento do último pregão de cada mês "
              "(ajustado por proventos quando a fonte traz o ajuste); "
              f"{len(pontos)} meses de {_mes_br(m0)} a {_mes_br(m1)}."]
    rets = [f"{m}m {_pct(r)}" for m in _JANELAS_RETORNO
            if (r := _retorno(pontos, m)) is not None]
    linhas.append(f"Último: {_br(v1)} {_quando(m1, ultima)}. Retorno até esse ponto: "
                  + (", ".join(rets) if rets else "série curta demais") + "; "
                  f"na janela inteira {_pct(v1 / v0 - 1 if v0 else None)}.")
    queda = _maior_queda(pontos)
    if queda:
        q, pico, vale = queda
        recup = next((m for m, v in pontos if m > vale and v >= dict(pontos)[pico]), None)
        linhas.append(f"Maior queda de pico a vale nos fechamentos mensais: {_pct(q)}, "
                      f"de {_mes_br(pico)} a {_mes_br(vale)}; "
                      + (f"voltou ao pico em {_mes_br(recup)}." if recup
                         else "ainda abaixo do pico no último mês.")
                      + " Queda intramês não aparece em fechamento mensal.")
    i_min = min(range(len(pontos)), key=lambda i: pontos[i][1])
    i_max = max(range(len(pontos)), key=lambda i: pontos[i][1])
    linhas.append(f"Mínimo {_br(pontos[i_min][1])} em {_mes_br(pontos[i_min][0])}, "
                  f"máximo {_br(pontos[i_max][1])} em {_mes_br(pontos[i_max][0])}.")
    amostra, freq = _amostrar(pontos)
    linhas.append(f"Pontos ({freq}): " + "; ".join(
        f"{_mes_br(m)} {_br(v)}" for m, v in amostra) + ".")
    return "\n".join(linhas)


def preco_ativo(tickers=None, anos=5) -> str:
    if isinstance(tickers, str):
        tickers = [t for t in tickers.replace(";", ",").split(",")]
    pedidos = list(dict.fromkeys(t for t in map(_normalizar_ticker, tickers or ()) if t))
    if not pedidos:
        return "Nenhum ticker informado: passe de 1 a 4 tickers (ex.: ['ITUB4', 'WEGE3'])."
    excedentes = pedidos[MAX_TICKERS:]
    pedidos = pedidos[:MAX_TICKERS]
    anos = _anos_arg(anos, 5)
    series: dict[str, tuple[str, list, date | None]] = {}
    try:
        from sqlalchemy import text

        engine = _engine()
        if engine is None:
            return "Preços: banco Supabase indisponível neste ambiente."
        with engine.connect() as conn:
            for sql, fonte, alvo in (
                    (_SQL_PRECO_B3, "market.historical_prices, Supabase", pedidos),
                    (_SQL_PRECO_EUA, "market_us.prices_monthly, Supabase", None)):
                faltam = alvo if alvo is not None else [t for t in pedidos
                                                         if t not in series]
                if not faltam:
                    continue
                brutos: dict[str, list] = {}
                for tk, d, v in conn.execute(text(sql), {"tickers": faltam,
                                                         "anos": anos}).all():
                    brutos.setdefault(str(tk).upper(), []).append((d, v))
                for tk, pts in brutos.items():
                    series[tk] = (fonte, _ultimo_por_mes(pts), _ultima_data(pts))
    except Exception as exc:  # noqa: BLE001 - ausência declarada
        return f"Preços de {', '.join(pedidos)}: falha na leitura do Supabase ({_limpo(exc)})."
    blocos = []
    for tk in pedidos:
        if tk in series:
            fonte, pontos, ultima = series[tk]
            blocos.append(texto_preco(tk, fonte, pontos, anos, ultima))
        else:
            blocos.append(f"{tk}: sem preço no banco (nem B3/FII nem EUA) nos "
                          f"últimos {anos} anos — confira o código de negociação.")
    if excedentes:
        blocos.append(f"Não consultados (teto de {MAX_TICKERS} por chamada): "
                      f"{', '.join(excedentes)}.")
    blocos.append("Retorno passado não é previsão.")
    return "\n\n".join(blocos)


# ─────────────────────────────────────────────────────────────────────────────
# meses_com_condicao
# ─────────────────────────────────────────────────────────────────────────────

#: campo da ferramenta -> (campo do painel, divisor do valor pedido, rótulo).
#: Variações vêm em % na pergunta e em fração no painel.
_CAMPOS_CONDICAO = {
    "selic": ("selic", 1.0, "Selic (% a.a.)"),
    "direcao_selic_6m": ("selic_6m", 1.0, "variação da Selic em 6 meses (p.p.)"),
    "ipca_12m": ("ipca12", 1.0, "IPCA 12m (%)"),
    "dolar_12m": ("usd12", 100.0, "dólar em 12 meses (%)"),
    "treasury_10a": ("us10", 1.0, "Treasury 10 anos (%)"),
    "pl_mediano": ("pl", 1.0, "P/L mediano (x)"),
    "bova11_12m": ("bova12", 100.0, "BOVA11 em 12 meses (%)"),
}
_OPERADORES = {">": float.__gt__, ">=": float.__ge__, "<": float.__lt__,
               "<=": float.__le__}
MAX_EPISODIOS_LISTADOS = 10


def _ler_condicoes(condicoes) -> tuple[list[tuple[str, str, float, str]], list[str]]:
    if isinstance(condicoes, str):
        try:
            condicoes = json.loads(condicoes)
        except ValueError:
            return [], [f"condições ilegíveis: {condicoes[:80]!r}"]
    if isinstance(condicoes, Mapping):
        condicoes = [condicoes]
    validas, erros = [], []
    for c in condicoes or ():
        if not isinstance(c, Mapping):
            erros.append(f"condição ilegível: {str(c)[:60]!r}")
            continue
        campo = str(c.get("campo") or "").strip().lower()
        op = str(c.get("operador") or "").strip()
        if campo not in _CAMPOS_CONDICAO:
            erros.append(f"campo '{campo}' não existe (use {', '.join(_CAMPOS_CONDICAO)})")
            continue
        if op not in _OPERADORES:
            erros.append(f"operador '{op}' não existe (use >, >=, < ou <=)")
            continue
        try:
            valor = float(c.get("valor"))
        except (TypeError, ValueError):
            erros.append(f"valor de '{campo}' não é número")
            continue
        painel_campo, divisor, rotulo = _CAMPOS_CONDICAO[campo]
        validas.append((painel_campo, op, valor / divisor, f"{rotulo} {op} {_br(valor)}"))
    return validas, erros


def _distancia(a: str, b: str) -> int:
    return abs((int(a[:4]) - int(b[:4])) * 12 + int(a[5:7]) - int(b[5:7]))


def texto_condicao(painel: list[dict], origem: str, condicoes) -> str:
    from core.memoria_mercado import amostra as am
    from core.memoria_mercado import cenarios_macro as cmac

    validas, erros = _ler_condicoes(condicoes)
    if not validas:
        return ("Nenhuma condição válida: " + "; ".join(erros or ["lista vazia"])
                + ". Formato: [{'campo': 'ipca_12m', 'operador': '>', 'valor': 6}].")
    descricao = " E ".join(v[3] for v in validas)

    def vale(linha: dict) -> bool:
        estado = linha.get("estado") or {}
        for campo, op, alvo, _ in validas:
            v = estado.get(campo)
            if v is None or not _OPERADORES[op](float(v), alvo):
                return False
        return True

    hoje = painel[-1]
    meses = [linha for linha in painel if vale(linha)]
    episodios: list[dict] = []
    for linha in meses:
        if not episodios or _distancia(linha["mes"], episodios[-1]["mes"]) >= cmac.JANELA_EPISODIO:
            episodios.append(linha)
    cab = (f"Meses com {descricao} — painel da Memória de Mercado, mês a mês de "
           f"{_mes_br(painel[0]['mes'])} a {_mes_br(hoje['mes'])} ({origem}).")
    linhas = [cab]
    if erros:
        linhas.append("Condições ignoradas: " + "; ".join(erros) + ".")
    linhas.append(f"Hoje ({_mes_br(hoje['mes'])}) {'SATISFAZ' if vale(hoje) else 'NÃO satisfaz'} "
                  f"a condição: {cmac._estado_txt(hoje.get('estado') or {}, hoje.get('refs'))}.")
    if not meses:
        linhas.append(f"Nenhum mês desde {_mes_br(painel[0]['mes'])} satisfaz a condição: "
                      "não há 'da última vez' para citar.")
        return "\n".join(linhas)
    linhas.append(f"{len(meses)} meses satisfazem, agrupados em {len(episodios)} episódios "
                  f"(um episódio começa {cmac.JANELA_EPISODIO} meses ou mais depois do "
                  "anterior; meses vizinhos têm quase o mesmo futuro e não contam como "
                  "casos independentes).")
    listados = episodios[-MAX_EPISODIOS_LISTADOS:]
    if len(episodios) > len(listados):
        linhas.append(f"Os {len(listados)} episódios mais recentes (mais "
                      f"{len(episodios) - len(listados)} antigos entram no resumo):")
    for ep in listados:
        linhas.append(f"- {_mes_br(ep['mes'])}: {cmac._estado_txt(ep.get('estado') or {})} "
                      f"→ depois: {cmac._depois_txt(ep.get('depois') or {})}.")
    vals = sorted(v for ep in episodios
                  if (v := (ep.get("depois") or {}).get("bova_12")) is not None)
    n = len(vals)
    if not n:
        linhas.append("Resumo: nenhum episódio com 12 meses completos depois.")
    else:
        pos = sum(1 for v in vals if v > 0)
        if n < am.N_MINIMO_EXPERIMENTAL:
            linhas.append(f"Resumo: BOVA11 subiu nos 12 meses seguintes em {pos} de {n} "
                          f"episódios com 12 meses completos (n={n}, abaixo de "
                          f"{am.N_MINIMO_EXPERIMENTAL}: cada episódio é um caso, não uma "
                          "amostra; sem faixa).")
        else:
            marca = ", experimental, abaixo de 30" if n < am.N_MINIMO_ROBUSTO else ""
            linhas.append(f"Resumo: BOVA11 nos 12 meses seguintes com mediana "
                          f"{_pct(am._percentil(vals, 0.5))}, p10 {_pct(am._percentil(vals, 0.1))}, "
                          f"p90 {_pct(am._percentil(vals, 0.9))}, positivo em {pos} de {n} "
                          f"episódios (n={n}{marca}).")
    base = cmac._base(painel)
    if base.get("n"):
        linhas.append(f"Contraponto, todos os {base['n']} meses desde "
                      f"{_mes_br(base['inicio'])} sem filtro: BOVA11 em 12 meses com "
                      f"mediana {_pct(base['mediana'])}, positivo em "
                      f"{100 * base['positivos']:.0f}% dos meses.")
    linhas.append("Condição repetida é contexto histórico, não previsão: o que veio "
                  "depois dependeu de fatos daquela época.")
    return "\n".join(linhas)


def meses_com_condicao(condicoes=None) -> str:
    painel, origem = _painel()
    if not painel:
        return f"Painel da Memória de Mercado indisponível ({origem}): sem meses para filtrar."
    return texto_condicao(painel, origem, condicoes)


# ─────────────────────────────────────────────────────────────────────────────
# Registro e execução
# ─────────────────────────────────────────────────────────────────────────────

FERRAMENTAS_MERCADO: tuple[Ferramenta, ...] = (
    Ferramenta(
        "serie_macro",
        "Série macro ou de mercado mês a mês, com mínimo, máximo, percentil e os "
        "pontos. Use para a trajetória longa de juros, inflação, câmbio, Treasury, "
        "P/L mediano, BOVA11, SMAL11, SPY e títulos do Tesouro.",
        {"type": "object",
         "properties": {
             "serie": {"type": "string", "enum": SERIES_MACRO},
             "anos": {"type": "integer", "description": f"Janela em anos (1 a {MAX_ANOS}); "
                      "padrão 10."}},
         "required": ["serie"]},
        serie_macro,
    ),
    Ferramenta(
        "preco_ativo",
        "Fechamento mensal de 1 a 4 ativos (ações e FIIs da B3 pelo código, ex. "
        "ITUB4, HGLG11; ações americanas pelo símbolo, ex. AAPL): retornos em "
        "1/3/12/36/60 meses, maior queda de pico a vale, mínimo, máximo e pontos.",
        {"type": "object",
         "properties": {
             "tickers": {"type": "array", "items": {"type": "string"},
                         "description": f"De 1 a {MAX_TICKERS} códigos."},
             "anos": {"type": "integer", "description": f"Janela em anos (1 a {MAX_ANOS}); "
                      "padrão 5."}},
         "required": ["tickers"]},
        preco_ativo,
    ),
    Ferramenta(
        "meses_com_condicao",
        "Meses desde 2010 em que uma condição macro valeu (todas as condições ao "
        "mesmo tempo), agrupados em episódios, com o que BOVA11, dólar e Selic "
        "fizeram nos 3, 6 e 12 meses seguintes, o n e o contraponto de todos os "
        "meses. Use para 'o que aconteceu das outras vezes em que...'. Valores em "
        "% (dólar e BOVA11 em variação de 12 meses, em %), P/L em vezes.",
        {"type": "object",
         "properties": {
             "condicoes": {
                 "type": "array",
                 "items": {"type": "object",
                           "properties": {
                               "campo": {"type": "string", "enum": list(_CAMPOS_CONDICAO)},
                               "operador": {"type": "string", "enum": list(_OPERADORES)},
                               "valor": {"type": "number"}},
                           "required": ["campo", "operador", "valor"]}}},
         "required": ["condicoes"]},
        meses_com_condicao,
    ),
)


def executar(nome: str, argumentos: str | Mapping | None,
             ferramentas: Sequence[Ferramenta] = FERRAMENTAS_MERCADO) -> str:
    """Roda a ferramenta pedida pela LLM; erro vira texto, nunca exceção."""
    alvo = next((f for f in ferramentas if f.nome == nome), None)
    if alvo is None:
        return (f"Ferramenta '{nome}' não existe. Disponíveis: "
                f"{', '.join(f.nome for f in ferramentas)}.")
    if isinstance(argumentos, Mapping):
        args = dict(argumentos)
    else:
        try:
            args = json.loads(argumentos or "{}")
        except ValueError:
            return f"Argumentos ilegíveis para {nome}: {str(argumentos)[:120]!r}."
    if not isinstance(args, dict):
        return f"Argumentos de {nome} precisam ser um objeto JSON."
    aceitos = alvo.parametros.get("properties", {})
    try:
        resultado = alvo.executar(**{k: v for k, v in args.items() if k in aceitos})
    except Exception as exc:  # noqa: BLE001 - ausência declarada
        logger.warning("ferramenta %s falhou", nome, exc_info=True)
        resultado = f"{nome}: falha ao executar ({_limpo(exc)})."
    return _cortar(resultado)
