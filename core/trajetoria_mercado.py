"""Trajetória das séries de mercado para o contexto das LLMs.

O bloco CONTEXTO DE MERCADO dava só o último valor de cada série. Com uma foto,
a LLM não tem como dizer "o dólar caiu 6% em três meses e está no quartil mais
baixo dos últimos cinco anos" -- que é a matéria-prima de uma análise que
correlaciona câmbio, juros e bolsa. Este módulo dá, para cada série, o valor
agora, há 1, 3 e 12 meses, e onde o valor de hoje cai no histórico disponível
(mínimo, máximo e percentil), com a janela NOMEADA: quatro anos de dólar não
são "o histórico", e a linha diz quantos anos são.

Nada aqui ingere dado novo. As fontes são as que já existem:

``Supabase`` -- dólar e SPY (``asset_quotes``), BOVA11 e SMAL11 em fechamento
    mensal desde 2009 (``market.historical_prices``; ``asset_quotes`` só tem
    meio ano) e os títulos do Tesouro (``tesouro_market_rates``). O plano free
    estourou o egress (restrição de 14/10/2026): a conta é feita no SQL e
    volta UMA linha por série, não a série diária.
``valuation_historico`` -- o P/L mediano ponto-no-tempo das 50 ações mais
    negociadas da B3, mês a mês desde 2011, e o prêmio do lucro sobre o juro
    real de hoje: a régua de "a bolsa está cara ou barata contra a própria
    história". NÃO é o P/L do Ibovespa, e a linha diz isso colado ao número.
``Arquivos publicados`` -- o CDI diário desde 2010 (``cdi_diario``, a trajetória
    efetiva da Selic) e o IPCA 12m (``macro_brasil``).
``Armazém local`` -- juros, inflação e crédito dos EUA (FRED) com décadas de
    história. Em produção, sem o armazém, entram os insumos publicados, que têm
    só 24 observações por série -- e a linha diz isso.

Fonte que falha vira linha nomeando a falha; nada aqui levanta.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable, Iterable, Mapping

logger = logging.getLogger(__name__)

#: Janela máxima do "histórico" contra o qual o valor de hoje é posicionado.
JANELA_ANOS = 10
#: Distâncias de comparação, em meses.
MESES_ATRAS = (1, 3, 12)
#: Quanto o ponto de comparação pode estar antes do alvo e ainda valer por ele:
#: série diária com feriado ou buraco curto vale; série mensal tem o ponto no
#: dia 1 e precisa de um mês de folga; além disso a comparação sai "sem dado".
FOLGA_DIARIA = timedelta(days=10)
FOLGA_MENSAL = timedelta(days=45)
#: Janela com menos que isto não é "histórico longo": a linha avisa.
ANOS_HISTORICO_CURTO = 3.0


@dataclass(frozen=True)
class Serie:
    """Como uma série é lida e escrita na linha do prompt."""
    nome: str
    fonte: str
    #: ``taxa`` (% e variação em p.p.), ``preco`` (variação em %), ``spread``
    #: (pontos percentuais, variação em p.p.) ou ``multiplo`` (P/L: "20,9x",
    #: variação em %).
    tipo: str
    prefixo: str = ""
    casas: int = 2
    mensal: bool = False
    #: Dias sem ponto novo até a linha avisar; ``None``: 80 se mensal, senão 10.
    atraso_max: int | None = None
    #: Vai colada ao percentil: a LLM copia o trecho, não a linha.
    ressalva: str = ""


@dataclass(frozen=True)
class Resumo:
    """O que a linha precisa saber da série; vem do SQL ou de :func:`resumir`."""
    data: date
    valor: float
    #: meses -> (data, valor) do último ponto em ou antes de ``data - meses``.
    atras: Mapping[int, tuple[date, float] | None]
    inicio: date
    n: int
    minimo: float
    maximo: float
    #: % das observações da janela com valor <= o de hoje.
    percentil: float


def _menos_meses(d: date, meses: int) -> date:
    ano, mes = divmod(d.year * 12 + d.month - 1 - meses, 12)
    mes += 1
    for dia in (d.day, 30, 29, 28):
        try:
            return date(ano, mes, dia)
        except ValueError:
            continue
    raise ValueError(d)


def _como_data(valor) -> date:
    return valor.date() if isinstance(valor, datetime) else valor


def resumir(serie: Mapping[date, float], *, hoje: date | None = None,
            janela_anos: int = JANELA_ANOS) -> Resumo | None:
    """Resumo de uma série completa (data -> valor); ``None`` se vazia."""
    pontos = sorted((_como_data(d), float(v)) for d, v in serie.items()
                    if v is not None and v == v)
    if not pontos:
        return None
    d0, v0 = pontos[-1]
    corte = _menos_meses(hoje or d0, 12 * janela_anos)
    janela = [v for d, v in pontos if d >= corte]
    inicio = next(d for d, _ in pontos if d >= corte)
    atras: dict[int, tuple[date, float] | None] = {}
    for meses in MESES_ATRAS:
        alvo = _menos_meses(d0, meses)
        antes = [(d, v) for d, v in pontos if d <= alvo]
        atras[meses] = antes[-1] if antes else None
    return Resumo(data=d0, valor=v0, atras=atras, inicio=inicio, n=len(janela),
                  minimo=min(janela), maximo=max(janela),
                  percentil=100.0 * sum(1 for v in janela if v <= v0) / len(janela))


def _num(valor: float, casas: int) -> str:
    texto = f"{valor:,.{casas}f}"
    return texto.replace(",", "§").replace(".", ",").replace("§", ".")


def _valor(s: Serie, v: float) -> str:
    sufixo = {"taxa": "%", "spread": "%", "multiplo": "x"}.get(s.tipo, "")
    return f"{s.prefixo}{_num(v, s.casas)}{sufixo}"


def _variacao(s: Serie, de: float, para: float) -> str:
    if s.tipo in ("preco", "multiplo"):
        if not de:
            return "variação indefinida"
        return f"{100.0 * (para / de - 1):+.1f}%".replace(".", ",")
    return f"{para - de:+.2f}".replace(".", ",") + " p.p."


def _anos(inicio: date, fim: date) -> float:
    return (fim - inicio).days / 365.25


def linha(s: Serie, r: Resumo | None, *, hoje: date | None = None) -> str:
    """Uma linha de prompt: agora, 1/3/12 meses atrás e posição na janela."""
    if r is None:
        return f"  {s.nome} ({s.fonte}): sem observações."
    folga = FOLGA_MENSAL if s.mensal else FOLGA_DIARIA
    partes = [f"  {s.nome} ({s.fonte}): {_valor(s, r.valor)} em {r.data:%d/%m/%Y}"]
    comparacoes = []
    for meses in MESES_ATRAS:
        rotulo = f"há {meses} {'mês' if meses == 1 else 'meses'}"
        ponto = r.atras.get(meses)
        alvo = _menos_meses(r.data, meses)
        if ponto is None:
            comparacoes.append(f"{rotulo}: sem dado (série começa em {r.inicio:%m/%Y})")
        elif alvo - _como_data(ponto[0]) > folga:
            comparacoes.append(f"{rotulo}: sem dado (último ponto antes do alvo "
                               f"é de {_como_data(ponto[0]):%d/%m/%Y})")
        else:
            # Série mensal datada no último pregão: "há 1 mês" de 07/10 cai em
            # 31/08, e a linha diz a data em vez de fingir que é 07/09.
            em = (f" em {_como_data(ponto[0]):%d/%m/%Y}"
                  if alvo - _como_data(ponto[0]) > timedelta(days=3) else "")
            comparacoes.append(f"{rotulo} {_valor(s, ponto[1])}{em} "
                               f"({_variacao(s, ponto[1], r.valor)})")
    partes.append("; ".join(comparacoes))
    anos = _anos(r.inicio, r.data)
    janela = (f"janela {r.inicio:%m/%Y}–{r.data:%m/%Y} ({_num(anos, 1)} anos, "
              f"{r.n} obs.)")
    if anos < ANOS_HISTORICO_CURTO:
        janela += " — HISTÓRICO CURTO, não é o ciclo longo"
    # O percentil leva a janela na mesma frase: a LLM copia "percentil 99" e
    # larga o resto da linha (medido com o Nemotron: "Ibovespa em recorde
    # (percentil 99)" sobre meio ano de BOVA11).
    curto = ", histórico curto" if anos < ANOS_HISTORICO_CURTO else ""
    ressalva = f", {s.ressalva}" if s.ressalva else ""
    partes.append(f"{janela}: mín. {_valor(s, r.minimo)}, máx. {_valor(s, r.maximo)}, "
                  f"hoje no percentil {r.percentil:.0f} de {_num(anos, 1)} anos"
                  f"{curto}{ressalva}")
    linha_txt = " · ".join(partes)
    # Mensal: o IPCA e o CPI saem ~40 dias depois do mês, que fica no dia 1.
    atraso = s.atraso_max if s.atraso_max is not None else (80 if s.mensal else 10)
    if hoje is not None and (hoje - r.data).days > atraso:
        linha_txt += f" · ÚLTIMO PONTO HÁ {(hoje - r.data).days} DIAS"
    return linha_txt + "."


# ─────────────────────────────────────────────────────────────────────────────
# Supabase: a conta no SQL, uma linha por série
# ─────────────────────────────────────────────────────────────────────────────

#: Séries de ``asset_quotes``: ticker -> descrição.
COTACOES: dict[str, Serie] = {
    "USDBRL": Serie("Dólar (USDBRL)", "asset_quotes, Supabase", "preco", "R$ ", 4),
    "SPY": Serie("SPY (ETF do S&P 500)", "asset_quotes, Supabase", "preco", "US$ "),
}
#: Títulos com mais história em ``tesouro_market_rates`` (taxa de compra).
TITULOS: dict[str, Serie] = {
    "TIPCA2029": Serie("Tesouro IPCA+ 2029 (juro real)", "tesouro_market_rates, Supabase",
                       "taxa"),
    "TIPCA2035": Serie("Tesouro IPCA+ 2035 (juro real longo)",
                       "tesouro_market_rates, Supabase", "taxa"),
    "TPRE2028": Serie("Tesouro Prefixado 2028", "tesouro_market_rates, Supabase", "taxa"),
    "TPRE2032": Serie("Tesouro Prefixado 2032", "tesouro_market_rates, Supabase", "taxa"),
}

_SQL_RESUMO = """
WITH s AS ({fonte}),
u AS (SELECT DISTINCT ON (chave) chave, d AS d0, v AS v0 FROM s ORDER BY chave, d DESC),
agg AS (SELECT s.chave, count(*) AS n, min(s.d) AS inicio, min(s.v) AS minimo,
               max(s.v) AS maximo, avg((s.v <= u.v0)::int) * 100 AS percentil
          FROM s JOIN u USING (chave) GROUP BY s.chave)
SELECT u.chave, u.d0, u.v0, agg.n, agg.inicio, agg.minimo, agg.maximo, agg.percentil,
       a1.d AS d1, a1.v AS v1, a3.d AS d3, a3.v AS v3, a12.d AS d12, a12.v AS v12
  FROM u JOIN agg USING (chave)
  LEFT JOIN LATERAL (SELECT d, v FROM s WHERE s.chave = u.chave
                      AND s.d <= u.d0 - interval '1 month' ORDER BY d DESC LIMIT 1) a1 ON true
  LEFT JOIN LATERAL (SELECT d, v FROM s WHERE s.chave = u.chave
                      AND s.d <= u.d0 - interval '3 months' ORDER BY d DESC LIMIT 1) a3 ON true
  LEFT JOIN LATERAL (SELECT d, v FROM s WHERE s.chave = u.chave
                      AND s.d <= u.d0 - interval '12 months' ORDER BY d DESC LIMIT 1) a12 ON true
"""
#: Fechamento mensal longo de ``market.historical_prices``: o histórico antigo
#: tem um ponto por mês e o último ano tem vários, e o percentil sobre os pontos
#: crus pesaria o ano recente; por isso o último pregão de cada mês.
HISTORICO: dict[str, Serie] = {
    "BOVA11": Serie("BOVA11 (ETF do Ibovespa, fechamento mensal)",
                    "market.historical_prices, Supabase", "preco", "R$ ",
                    mensal=True, atraso_max=10),
    "SMAL11": Serie("SMAL11 (ETF de small caps, fechamento mensal)",
                    "market.historical_prices, Supabase", "preco", "R$ ",
                    mensal=True, atraso_max=10),
}
_FONTE_HISTORICO = (
    "SELECT DISTINCT ON (ticker, date_trunc('month', date)) ticker AS chave, "
    "date AS d, COALESCE(adjusted_close, close)::float8 AS v "
    "FROM market.historical_prices WHERE ticker = ANY(:chaves) "
    "AND COALESCE(adjusted_close, close) IS NOT NULL "
    "AND date >= current_date - interval '{anos} years' "
    "ORDER BY ticker, date_trunc('month', date), date DESC")
_FONTE_COTACOES = (
    "SELECT a.ticker AS chave, aq.timestamp::date AS d, aq.close::float8 AS v "
    "FROM asset_quotes aq JOIN assets a ON a.id = aq.asset_id "
    "WHERE a.ticker = ANY(:chaves) AND aq.close IS NOT NULL "
    "AND aq.timestamp >= current_date - interval '{anos} years'")
_FONTE_TITULOS = (
    "SELECT security_key AS chave, base_date AS d, buy_rate::float8 AS v "
    "FROM tesouro_market_rates WHERE security_key = ANY(:chaves) "
    "AND buy_rate IS NOT NULL AND base_date >= current_date - interval '{anos} years'")


def _resumo_da_linha(r: Mapping) -> Resumo:
    atras = {}
    for meses in MESES_ATRAS:
        d, v = r[f"d{meses}"], r[f"v{meses}"]
        atras[meses] = None if d is None else (_como_data(d), float(v))
    return Resumo(data=_como_data(r["d0"]), valor=float(r["v0"]), atras=atras,
                  inicio=_como_data(r["inicio"]), n=int(r["n"]),
                  minimo=float(r["minimo"]), maximo=float(r["maximo"]),
                  percentil=float(r["percentil"]))


def resumos_sql(conn, fonte: str, chaves: Iterable[str]) -> dict[str, Resumo]:
    """Resumo de cada chave calculado no banco: volta uma linha por série."""
    from sqlalchemy import text

    sql = _SQL_RESUMO.format(fonte=fonte.format(anos=JANELA_ANOS))
    linhas = conn.execute(text(sql), {"chaves": list(chaves)}).mappings().all()
    return {str(r["chave"]): _resumo_da_linha(r) for r in linhas}


def linhas_supabase(engine, *, hoje: date | None = None,
                    resumos_out: dict[str, Resumo] | None = None) -> list[str]:
    """Linhas do Supabase; ``resumos_out`` recebe os resumos (o prêmio usa o juro real)."""
    if engine is None:
        return ["  Dólar, bolsa e Tesouro: banco Supabase indisponível."]
    saida: list[str] = []
    try:
        # Uma conexão para as duas consultas: daqui, abrir conexão custa mais que a conta.
        with engine.connect() as conn:
            for fonte, series in ((_FONTE_COTACOES, COTACOES), (_FONTE_HISTORICO, HISTORICO),
                                  (_FONTE_TITULOS, TITULOS)):
                try:
                    resumos = resumos_sql(conn, fonte, series)
                except Exception as exc:  # noqa: BLE001 - ausência declarada
                    conn.rollback()
                    nomes = ", ".join(s.nome for s in series.values())
                    saida.append(f"  {nomes}: falha na leitura ({_limpo(exc)}).")
                    continue
                if resumos_out is not None:
                    resumos_out.update(resumos)
                saida += [linha(s, resumos.get(chave), hoje=hoje)
                          for chave, s in series.items()]
    except Exception as exc:  # noqa: BLE001
        saida.append(f"  Dólar, bolsa e Tesouro: falha na conexão ({_limpo(exc)}).")
    return saida


# ─────────────────────────────────────────────────────────────────────────────
# Arquivos publicados: CDI e IPCA 12m
# ─────────────────────────────────────────────────────────────────────────────

CDI = Serie("CDI anualizado (trajetória efetiva da Selic)",
            "BCB/SGS 12, arquivo cdi_diario", "taxa")
IPCA_12M = Serie("IPCA acumulado em 12 meses", "BCB/SGS 13522", "taxa", mensal=True)


def linha_cdi(cdi: Mapping[date, float] | None, *, hoje: date | None = None) -> str:
    if not cdi:
        return f"  {CDI.nome}: arquivo cdi_diario ausente ou ilegível."
    from core.macro_brasil import cdi_anualizado

    return linha(CDI, resumir({d: cdi_anualizado(t) for d, t in cdi.items()}, hoje=hoje),
                 hoje=hoje)


def linha_ipca_12m(observacoes: Iterable[Mapping] | None, origem: str,
                   *, hoje: date | None = None) -> str:
    """IPCA 12m das observações do BCB (armazém ou arquivo publicado)."""
    serie = {o["reference_period"]: float(o["value"]) for o in (observacoes or ())
             if o.get("provider") == "bcb_sgs" and str(o.get("provider_code")) == "13522"
             and isinstance(o.get("reference_period"), date)}
    s = Serie(IPCA_12M.nome, f"{IPCA_12M.fonte}, {origem}", "taxa", mensal=True)
    if not serie:
        return f"  {s.nome} ({s.fonte}): sem observações."
    return linha(s, resumir(serie, hoje=hoje), hoje=hoje)


# ─────────────────────────────────────────────────────────────────────────────
# EUA: armazém local (décadas) ou insumos publicados (24 observações)
# ─────────────────────────────────────────────────────────────────────────────

#: Séries do FRED: código -> descrição. CPI entra como variação em 12 meses.
SERIES_EUA: dict[str, Serie] = {
    "FEDFUNDS": Serie("Fed Funds efetivo (média mensal)", "FRED", "taxa", mensal=True),
    "DGS10": Serie("Treasury 10 anos", "FRED", "taxa"),
    "BAMLH0A0HYM2": Serie("Spread de high yield dos EUA (ICE BofA)", "FRED", "spread"),
    "CPIAUCSL": Serie("Inflação ao consumidor dos EUA (CPI, variação em 12 meses)", "FRED",
                      "taxa", mensal=True),
}


def cpi_em_12_meses(indice: Mapping[date, float]) -> dict[date, float]:
    """Índice mensal -> variação em 12 meses, só onde o mês de um ano antes existe."""
    saida = {}
    for d, v in indice.items():
        base = indice.get(_menos_meses(d, 12))
        if base:
            saida[d] = 100.0 * (v / base - 1)
    return saida


def series_eua_do_armazem(engine) -> dict[str, dict[date, float]]:
    """Última safra de cada período, na janela de :data:`JANELA_ANOS` (+1 ano do CPI)."""
    from sqlalchemy import text

    corte = _menos_meses(date.today(), 12 * (JANELA_ANOS + 1))
    with engine.connect() as conn:
        linhas = conn.execute(text("""
            SELECT DISTINCT ON (provider_code, reference_period)
                   provider_code, reference_period, value
              FROM macro_observations
             WHERE provider = 'fred' AND provider_code = ANY(:codigos)
               AND value IS NOT NULL AND reference_period >= :corte
             ORDER BY provider_code, reference_period,
                      COALESCE(vintage_date, '9999-12-31'::date) DESC, retrieved_at DESC
        """), {"codigos": list(SERIES_EUA), "corte": corte}).all()
    series: dict[str, dict[date, float]] = {}
    for codigo, periodo, valor in linhas:
        series.setdefault(str(codigo), {})[_como_data(periodo)] = float(valor)
    return series


def series_eua_publicadas(insumos) -> dict[str, dict[date, float]]:
    series: dict[str, dict[date, float]] = {}
    for o in insumos.observacoes:
        codigo = str(o.get("provider_code"))
        periodo = o.get("reference_period")
        if (o.get("provider") == "fred" and codigo in SERIES_EUA
                and isinstance(periodo, date) and o.get("value") is not None):
            series.setdefault(codigo, {})[periodo] = float(o["value"])
    return series


def linhas_eua(series: Mapping[str, Mapping[date, float]], origem: str,
               *, hoje: date | None = None) -> list[str]:
    saida = []
    for codigo, s in SERIES_EUA.items():
        bruta = series.get(codigo) or {}
        if codigo == "CPIAUCSL":
            bruta = cpi_em_12_meses(bruta)
        serie = Serie(s.nome, f"{s.fonte} {codigo}, {origem}", s.tipo, mensal=s.mensal)
        saida.append(linha(serie, resumir(bruta, hoje=hoje) if bruta else None, hoje=hoje))
    return saida


# ─────────────────────────────────────────────────────────────────────────────
# Bolsa cara ou barata: P/L mediano publicado em valuation_historico
# ─────────────────────────────────────────────────────────────────────────────

VALUATION_B3 = Serie(
    "P/L mediano das 50 ações mais negociadas da B3 (ponto-no-tempo)",
    "arquivo valuation_historico", "multiplo", casas=1, mensal=True, atraso_max=45,
    ressalva="mediana das 50 mais negociadas, NÃO o P/L do Ibovespa")
#: Juro real para o prêmio: o mais longo primeiro (a ação é ativo de prazo longo).
JUROS_REAIS = ("TIPCA2035", "TIPCA2029")


def _lista(tickers, limite: int = 10) -> str:
    tickers = list(tickers or ())
    mais = f" e mais {len(tickers) - limite}" if len(tickers) > limite else ""
    return ", ".join(tickers[:limite]) + mais


def linhas_valuation_b3(mercado: Mapping | None, juros: Mapping[str, Resumo] | None = None,
                        *, origem: str = "", hoje: date | None = None) -> list[str]:
    """P/L mediano contra a própria história e prêmio sobre o juro real de hoje."""
    fonte = VALUATION_B3.fonte + (f", {origem}" if origem else "")
    s = Serie(VALUATION_B3.nome, fonte, VALUATION_B3.tipo, casas=VALUATION_B3.casas,
              mensal=True, atraso_max=VALUATION_B3.atraso_max, ressalva=VALUATION_B3.ressalva)
    serie = (mercado or {}).get("serie") or []
    pontos = {date.fromisoformat(str(d)): float(pl) for d, pl, *_ in serie if pl}
    if not pontos:
        return [f"  {s.nome} ({fonte}): sem a série mercado_b3 no arquivo."]
    r = resumir(pontos, hoje=hoje)
    ultimo = (mercado or {}).get("ultimo") or {}
    fora = []
    if ultimo.get("sem_lpa"):
        fora.append(f"sem LPA do exercício {ultimo.get('exercicio')} na base: "
                    f"{_lista(ultimo['sem_lpa'])}")
    if ultimo.get("sem_preco"):
        fora.append(f"sem preço no mês (ticker mudou?): {_lista(ultimo['sem_preco'])}")
    if ultimo.get("implausiveis"):
        fora.append(f"LPA em escala errada: {_lista(ultimo['implausiveis'])}")
    composicao = (f"  Composição de hoje: {ultimo.get('validas')} de {ultimo.get('universo')} "
                  f"ações na conta, {ultimo.get('negativas', 0)} com prejuízo; fora da "
                  f"conta -- " + ("; ".join(fora) if fora else "nenhuma") + ".")
    saida = [linha(s, r, hoje=hoje), composicao]
    # Prêmio: lucro ÷ preço é rendimento real (o lucro acompanha a inflação),
    # então se compara com o juro real. Só hoje: o juro real tem 1 a 3 anos.
    ey = next((float(e) for d, pl, e, *_ in reversed(serie) if e is not None), None)
    juro = next(((k, (juros or {})[k]) for k in JUROS_REAIS if k in (juros or {})), None)
    if ey is None or juro is None:
        saida.append("  Prêmio do lucro sobre o juro real: sem o juro real do Tesouro "
                     "IPCA+ nesta leitura.")
    else:
        chave, jr = juro
        premio = f"{ey - jr.valor:+.2f}".replace(".", ",")
        saida.append(
            f"  Prêmio do lucro sobre o juro real (só o valor de hoje, sem série histórica "
            f"comparável): lucro ÷ preço mediano {_num(ey, 2)}% − {TITULOS[chave].nome} "
            f"{_num(jr.valor, 2)}% em {jr.data:%d/%m/%Y} = {premio} p.p. (mediana das 50 "
            "mais negociadas, NÃO o Ibovespa; prêmio negativo = a renda fixa real paga mais "
            "que o lucro corrente da ação mediana).")
    return saida


# ─────────────────────────────────────────────────────────────────────────────
# Montagem
# ─────────────────────────────────────────────────────────────────────────────

CABECALHO = (
    "TRAJETÓRIA (valor agora, há 1/3/12 meses e posição na janela disponível; "
    "percentil = % das observações da janela com valor menor ou igual ao de hoje. "
    "A janela de cada série é a que existe no banco, NÃO necessariamente o ciclo "
    "longo -- leia os anos indicados antes de chamar um nível de histórico):"
)


def _limpo(exc: object, limite: int = 120) -> str:
    return " ".join(str(exc).split())[:limite]


def linhas_trajetoria(
    *,
    supabase: Callable[[], object] | None = None,
    cdi: Callable[[], Mapping[date, float]] | None = None,
    bcb: Callable[[], tuple[Iterable[Mapping] | None, str]] | None = None,
    eua: Callable[[], tuple[Mapping[str, Mapping[date, float]] | None, str]] | None = None,
    valuation: Callable[[], tuple[Mapping | None, str]] | None = None,
    hoje: date | None = None,
) -> list[str]:
    """Todas as linhas da seção; cada leitor é injetável e cada falha é nomeada."""
    hoje = hoje or date.today()
    saida = [CABECALHO]
    resumos: dict[str, Resumo] = {}
    try:
        saida += linhas_supabase(supabase() if supabase else None, hoje=hoje,
                                 resumos_out=resumos)
    except Exception as exc:  # noqa: BLE001
        saida.append(f"  Dólar, bolsa e Tesouro: falha na leitura ({_limpo(exc)}).")
    try:
        mercado, origem = valuation() if valuation else (None, "leitor ausente")
        saida += (linhas_valuation_b3(mercado, resumos, origem=origem, hoje=hoje)
                  if mercado is not None
                  else [f"  {VALUATION_B3.nome}: indisponível ({origem})."])
    except Exception as exc:  # noqa: BLE001
        saida.append(f"  {VALUATION_B3.nome}: falha na leitura ({_limpo(exc)}).")
    try:
        saida.append(linha_cdi(cdi() if cdi else {}, hoje=hoje))
    except Exception as exc:  # noqa: BLE001
        saida.append(f"  {CDI.nome}: falha na leitura ({_limpo(exc)}).")
    try:
        obs, origem = bcb() if bcb else (None, "leitor ausente")
        saida.append(linha_ipca_12m(obs, origem, hoje=hoje) if obs is not None
                     else f"  {IPCA_12M.nome}: indisponível ({origem}).")
    except Exception as exc:  # noqa: BLE001
        saida.append(f"  {IPCA_12M.nome}: falha na leitura ({_limpo(exc)}).")
    try:
        series, origem = eua() if eua else (None, "leitor ausente")
        if series is None:
            saida.append(f"  Juros, inflação e crédito dos EUA: indisponíveis ({origem}).")
        else:
            saida += linhas_eua(series, origem, hoje=hoje)
    except Exception as exc:  # noqa: BLE001
        saida.append(f"  Juros, inflação e crédito dos EUA: falha na leitura ({_limpo(exc)}).")
    return saida
