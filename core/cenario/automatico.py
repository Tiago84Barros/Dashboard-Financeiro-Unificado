"""
core/cenario/automatico.py
Cenário econômico lido dos dados, sem perguntar nada ao usuário.

Desde 30/09/2026 o usuário não preenche mais "Meu cenário": o banco tem dado
suficiente para o próprio programa dizer em que ambiente estamos. Cada item
sai com valor, tendência calculada, confiança (pela idade do dado), fonte e
data de referência:

- **Taxa de juros**: Selic de ``public.macro`` (ou dos insumos publicados);
  tendência contra o ano anterior.
- **Expectativa de juros**: prefixado de ~1 ano da curva do Tesouro contra a
  Selic. Prefixado abaixo da Selic é o mercado precificando corte.
- **Inflação**: IPCA acumulado do ano corrente; tendência pelos dois últimos
  anos fechados (o acumulado parcial não se compara com ano cheio).
- **Expectativa de inflação**: inflação implícita (prefixado contra IPCA+) de
  ~2 anos, contra o último IPCA anual fechado.
- **Câmbio**: USDBRL diário (``asset_quotes``); tendência em ~3 meses.
- **Atividade**: PIB (Banco Mundial) e confiança do consumidor (ICC).
- **Fiscal**: dívida bruta em % do PIB (Banco Mundial).
- **Crédito**: juro real de mercado (Tesouro IPCA+ ~3 anos) e a Selic.
- **Economia internacional**: Fed Funds e Treasury 10 anos.
- **Mercado de capitais**: spread high yield dos EUA (apetite a risco).
- Commodities e risco geopolítico não têm série no banco: saem como ausentes,
  nomeados, e a LLM os lê nas notícias.

``de_dados`` é puro; ``carregar`` lê (com cache de 15 minutos) e nunca grava.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass

from core.cenario.modelo import CHAVES, DADOS, Cenario, Item

logger = logging.getLogger(__name__)

TTL = 900
LIMIAR_JUROS_PP = 0.5        # prefixado 1 ano vs Selic
LIMIAR_INFLACAO_PP = 0.5
LIMIAR_CAMBIO_PCT = 3.0      # variação do dólar em ~3 meses
LIMIAR_ICC = 1.0
LIMIAR_FISCAL_PP = 1.0
LIMIAR_FED_PP = 0.25
LIMIAR_SPREAD_PP = 0.25
FRESCO_DIAS = 45
MEDIO_DIAS = 400

SEM_SERIE = "sem série no banco; leia nas notícias do contexto"


@dataclass(frozen=True)
class Insumos:
    """O que ``de_dados`` lê. Cada campo pode faltar; a falha vira motivo."""
    observacoes: tuple = ()                       # insumos macro publicados
    macro_anual: dict | None = None               # public.macro por ano
    curva: object | None = None                   # DataFrame da curva
    usdbrl: tuple[tuple[dt.date, float], ...] = ()  # mais recente primeiro
    falhas: dict | None = None                    # fonte → motivo


# -- formatação --------------------------------------------------------------------

def _n(v: float, casas: int = 2) -> str:
    return f"{v:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(v: float) -> str:
    return f"{_n(v)}%"


def _data(d: dt.date) -> str:
    return d.strftime("%d/%m/%Y")


def _direcao(delta: float | None, limiar: float) -> str:
    if delta is None:
        return "incerta"
    if delta > limiar:
        return "alta"
    if delta < -limiar:
        return "queda"
    return "estavel"


def _confianca(ref: dt.date | None, hoje: dt.date) -> str:
    if ref is None:
        return "baixa"
    idade = (hoje - ref).days
    if idade <= FRESCO_DIAS:
        return "alta"
    return "media" if idade <= MEDIO_DIAS else "baixa"


def _item(valor: str, direcao: str, ref: dt.date | None, fonte: str,
          hoje: dt.date, confianca: str | None = None) -> Item:
    return Item(current_value=valor, expected_direction=direcao,
                confidence=confianca or _confianca(ref, hoje), source=fonte,
                last_updated=ref.isoformat() if ref else None)


def _ausente(motivo: str) -> Item:
    """Item sem dado: o motivo fica na fonte para a tela e a LLM o nomearem."""
    return Item(source=motivo)


# -- leitura das séries --------------------------------------------------------------

def _como_pct(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:
        return None
    return x * 100.0 if abs(x) <= 1 else x


def _serie(observacoes, pais: str, codigo: str) -> list[tuple[dt.date, float]]:
    """Observações de uma série, da mais recente para a mais antiga."""
    saida = []
    for o in observacoes or ():
        if (o.get("country_code"), o.get("provider_code")) != (pais, codigo):
            continue
        if o.get("is_forecast") or o.get("reference_period") is None:
            continue
        try:
            saida.append((o["reference_period"], float(o.get("value"))))
        except (TypeError, ValueError):
            continue
    saida.sort(key=lambda t: t[0], reverse=True)
    return saida


def _antes(serie, dias: int) -> tuple[dt.date, float] | None:
    """A observação mais recente com ao menos ``dias`` de distância da última."""
    if not serie:
        return None
    limite = serie[0][0] - dt.timedelta(days=dias)
    return next((t for t in serie if t[0] <= limite), None)


def _anual(macro: dict | None, chave: str, *, pct: bool = True
           ) -> list[tuple[int, float]]:
    saida = []
    for ano, linha in (macro or {}).items():
        v = (linha or {}).get(chave)
        v = _como_pct(v) if pct else v
        if v is not None:
            saida.append((int(ano), float(v)))
    saida.sort(reverse=True)
    return saida


def _ref_ano(ano: int, hoje: dt.date) -> dt.date:
    """Ano corrente: o último valor observado vale até hoje; ano fechado, 31/12."""
    return hoje if ano >= hoje.year else dt.date(ano, 12, 31)


def _vertice(curva, familia: str, venc: dt.date) -> float | None:
    from core.tesouro_curva import _mais_proximo
    try:
        v = _mais_proximo(curva, familia, venc)
    except Exception:  # noqa: BLE001 — curva malformada vira ausência
        return None
    return None if v is None else v * 100.0


def _data_curva(curva) -> dt.date | None:
    try:
        d = curva["base_date"].max()
    except Exception:  # noqa: BLE001
        return None
    if hasattr(d, "to_pydatetime"):
        d = d.to_pydatetime()
    if isinstance(d, dt.datetime):
        d = d.date()
    return d if isinstance(d, dt.date) else None


def _curva_vazia(curva) -> bool:
    return curva is None or getattr(curva, "empty", True)


# -- itens ----------------------------------------------------------------------------

def _selic_anual(ins: Insumos) -> list[tuple[int, float]]:
    """Selic por ano: public.macro, completada pelos insumos publicados nos
    anos em que a tabela não tem a coluna."""
    por_ano = {d.year: v for d, v in reversed(_serie(ins.observacoes, "BRA",
                                                        "selic"))}
    por_ano.update(dict(_anual(ins.macro_anual, "selic")))
    return sorted(por_ano.items(), reverse=True)


def _selic(ins: Insumos, hoje: dt.date) -> tuple[float, dt.date, str] | None:
    anual = _anual(ins.macro_anual, "selic")
    if anual:
        ano, v = anual[0]
        return v, _ref_ano(ano, hoje), f"public.macro (BCB/SGS), {ano}"
    s = _serie(ins.observacoes, "BRA", "selic")
    if s:
        return s[0][1], s[0][0], "insumos macro publicados (Selic)"
    return None


def _juros(ins, hoje, selic) -> Item:
    if selic is None:
        return _ausente("Selic sem dado em public.macro e nos insumos")
    v, ref, fonte = selic
    anual = _selic_anual(ins)
    delta = anual[0][1] - anual[1][1] if len(anual) > 1 else None
    texto = f"Selic {_pct(v)} a.a."
    if delta is not None:
        texto += f" ({_pct(anual[1][1])} em {anual[1][0]})"
    return _item(texto, _direcao(delta, 0.0), ref, fonte, hoje)


def _expectativa_juros(ins, hoje, selic) -> Item:
    if _curva_vazia(ins.curva):
        return _ausente(_falha(ins, "curva", "curva do Tesouro sem dado"))
    pre1 = _vertice(ins.curva, "tesouro prefixado", hoje + dt.timedelta(days=365))
    pre5 = _vertice(ins.curva, "tesouro prefixado",
                    hoje + dt.timedelta(days=5 * 365))
    if pre1 is None:
        return _ausente("curva do Tesouro sem prefixado")
    ref = _data_curva(ins.curva)
    texto = f"prefixado de ~1 ano {_pct(pre1)} a.a."
    delta = None
    if selic is not None:
        delta = pre1 - selic[0]
        leitura = {"queda": "mercado precifica corte",
                   "alta": "mercado precifica alta",
                   "estavel": "mercado precifica juros estáveis"}[
            _direcao(delta, LIMIAR_JUROS_PP)]
        texto += f" contra Selic {_pct(selic[0])}: {leitura}"
    if pre5 is not None:
        texto += f"; ~5 anos {_pct(pre5)}"
    return _item(texto, _direcao(delta, LIMIAR_JUROS_PP), ref,
                 "Curva do Tesouro Direto (taxa de compra)", hoje)


def _inflacao(ins, hoje) -> Item:
    anual = _anual(ins.macro_anual, "ipca", pct=False)
    if not anual:
        s = _serie(ins.observacoes, "BRA", "ipca")
        if not s:
            return _ausente("IPCA sem dado em public.macro e nos insumos")
        anual = [(d.year, v) for d, v in s]
    ano, v = anual[0]
    fechados = [(a, x) for a, x in anual if a < hoje.year]
    if ano >= hoje.year:
        texto = f"IPCA acumulado em {ano} até o último mês divulgado: {_pct(v)}"
    else:
        texto = f"IPCA {ano}: {_pct(v)}"
    delta = None
    if len(fechados) > 1:
        delta = fechados[0][1] - fechados[1][1]
        texto += (f"; anos fechados {fechados[1][0]} {_pct(fechados[1][1])} → "
                  f"{fechados[0][0]} {_pct(fechados[0][1])}")
    return _item(texto, _direcao(delta, LIMIAR_INFLACAO_PP),
                 _ref_ano(ano, hoje), f"public.macro (IPCA, BCB/SGS 433), {ano}",
                 hoje)


def _expectativa_inflacao(ins, hoje) -> Item:
    if _curva_vazia(ins.curva):
        return _ausente(_falha(ins, "curva", "curva do Tesouro sem dado"))
    from core.tesouro_curva import indice_implicito
    try:
        be = indice_implicito(ins.curva, "IPCA", hoje + dt.timedelta(days=2 * 365))
    except Exception:  # noqa: BLE001
        be = None
    if be is None:
        return _ausente("curva do Tesouro sem par prefixado/IPCA+")
    be *= 100.0
    texto = f"inflação implícita de ~2 anos {_pct(be)} a.a."
    fechados = [(a, x) for a, x in _anual(ins.macro_anual, "ipca", pct=False)
                if a < hoje.year]
    delta = None
    if fechados:
        delta = be - fechados[0][1]
        texto += f" contra IPCA {fechados[0][0]} {_pct(fechados[0][1])}"
    return _item(texto, _direcao(delta, LIMIAR_INFLACAO_PP),
                 _data_curva(ins.curva),
                 "Curva do Tesouro (prefixado contra IPCA+)", hoje)


def _cambio(ins, hoje) -> Item:
    if ins.usdbrl:
        d, v = ins.usdbrl[0]
        texto = f"US$ 1 = R$ {_n(v, 4)}"
        antes = _antes(list(ins.usdbrl), 90)
        delta = None
        if antes:
            delta = (v / antes[1] - 1.0) * 100.0
            texto += (f" ({'+' if delta >= 0 else ''}{_n(delta, 1)}% desde "
                      f"{_data(antes[0])})")
        return _item(texto, _direcao(delta, LIMIAR_CAMBIO_PCT), d,
                     "USDBRL diário (asset_quotes)", hoje)
    anual = _anual(ins.macro_anual, "cambio", pct=False)
    if anual:
        ano, v = anual[0]
        delta = ((v / anual[1][1] - 1.0) * 100.0) if len(anual) > 1 else None
        return _item(f"US$ 1 = R$ {_n(v, 4)} ({ano})",
                     _direcao(delta, LIMIAR_CAMBIO_PCT), _ref_ano(ano, hoje),
                     f"public.macro (câmbio), {ano}", hoje)
    return _ausente(_falha(ins, "usdbrl", "dólar sem cotação gravada"))


def _atividade(ins, hoje) -> Item:
    partes, refs, delta = [], [], None
    pib = _serie(ins.observacoes, "BRA", "NY.GDP.MKTP.KD.ZG")
    if pib:
        partes.append(f"PIB {pib[0][0].year} {'+' if pib[0][1] >= 0 else ''}"
                      f"{_pct(pib[0][1])}")
        refs.append(dt.date(pib[0][0].year, 12, 31))
    icc = _anual(ins.macro_anual, "icc", pct=False)
    if icc:
        ano, v = icc[0]
        d = (ins.macro_anual.get(ano) or {}).get("icc_delta")
        partes.append(f"confiança do consumidor {_n(v, 1)}"
                      + (f" ({'+' if d >= 0 else ''}{_n(d, 1)} pontos em {ano})"
                         if d is not None else ""))
        refs.append(_ref_ano(ano, hoje))
        delta = d
    if not partes:
        return _ausente("PIB e confiança do consumidor sem dado")
    return _item("; ".join(partes), _direcao(delta, LIMIAR_ICC), max(refs),
                 "Banco Mundial (PIB) e public.macro (ICC)", hoje)


def _fiscal(ins, hoje) -> Item:
    s = _serie(ins.observacoes, "BRA", "GC.DOD.TOTL.GD.ZS")
    if not s:
        return _ausente("dívida pública em % do PIB sem dado")
    (d, v), anterior = s[0], (s[1] if len(s) > 1 else None)
    texto = f"dívida do governo central {_n(v, 1)}% do PIB em {d.year}"
    delta = None
    if anterior:
        delta = v - anterior[1]
        texto += f" ({_n(anterior[1], 1)}% em {anterior[0].year})"
    return _item(texto, _direcao(delta, LIMIAR_FISCAL_PP),
                 dt.date(d.year, 12, 31), "Banco Mundial (GC.DOD.TOTL.GD.ZS)",
                 hoje)


def _credito(ins, hoje, selic, expectativa: Item) -> Item:
    if _curva_vazia(ins.curva):
        return _ausente(_falha(ins, "curva", "curva do Tesouro sem dado"))
    real = _vertice(ins.curva, "tesouro ipca+", hoje + dt.timedelta(days=3 * 365))
    if real is None:
        return _ausente("curva do Tesouro sem IPCA+")
    texto = f"juro real de mercado (IPCA+ ~3 anos) {_pct(real)} a.a."
    if selic is not None:
        texto += f"; Selic {_pct(selic[0])}"
    # o custo do crédito segue a expectativa de juros da própria curva
    return _item(texto, expectativa.expected_direction or "incerta",
                 _data_curva(ins.curva),
                 "Curva do Tesouro Direto (IPCA+) e Selic", hoje)


def _global(ins, hoje) -> Item:
    partes, refs, delta = [], [], None
    politica = _serie(ins.observacoes, "US", "WS_CBPOL|D.US")
    efetivo = _serie(ins.observacoes, "US", "FEDFUNDS")
    if politica or efetivo:
        atual = (politica or efetivo)[0]
        partes.append(f"juro do Fed {_pct(atual[1])}")
        refs.append(atual[0])
        # a série mensal tem mais história para a tendência que a diária
        base = efetivo or politica
        antes = _antes(base, 180)
        if antes:
            delta = base[0][1] - antes[1]
            partes[-1] += (f" (Fed Funds efetivo {_pct(base[0][1])}; "
                           f"{_pct(antes[1])} em {_data(antes[0])})")
    t10 = _serie(ins.observacoes, "US", "DGS10")
    if t10:
        partes.append(f"Treasury 10 anos {_pct(t10[0][1])}")
        refs.append(t10[0][0])
    if not partes:
        return _ausente("juros dos EUA sem dado nos insumos")
    return _item("; ".join(partes), _direcao(delta, LIMIAR_FED_PP), max(refs),
                 "FRED/BIS (insumos macro publicados)", hoje)


def _capitais(ins, hoje) -> Item:
    s = _serie(ins.observacoes, "US", "BAMLH0A0HYM2")
    if not s:
        return _ausente("spread de crédito high yield sem dado")
    d, v = s[0]
    antes = s[-1] if len(s) > 1 else None
    texto = f"spread high yield EUA {_n(v)} pp"
    delta = None
    if antes:
        delta = v - antes[1]
        texto += f" ({_n(antes[1])} pp em {_data(antes[0])})"
    texto += "; spread menor = mais apetite a risco"
    return _item(texto, _direcao(delta, LIMIAR_SPREAD_PP), d,
                 "FRED (BAMLH0A0HYM2)", hoje)


def _falha(ins: Insumos, fonte: str, padrao: str) -> str:
    return ((ins.falhas or {}).get(fonte)) or padrao


def de_dados(ins: Insumos, *, hoje: dt.date,
             agora: dt.datetime | None = None) -> Cenario:
    """Puro: os insumos → o cenário dos 12 itens, com ``origem=DADOS``."""
    selic = _selic(ins, hoje)
    expectativa = _expectativa_juros(ins, hoje, selic)
    itens = {
        "interest_rate": _juros(ins, hoje, selic),
        "interest_rate_outlook": expectativa,
        "inflation": _inflacao(ins, hoje),
        "inflation_outlook": _expectativa_inflacao(ins, hoje),
        "economic_activity": _atividade(ins, hoje),
        "fx": _cambio(ins, hoje),
        "fiscal_policy": _fiscal(ins, hoje),
        "credit": _credito(ins, hoje, selic, expectativa),
        "commodities": _ausente(SEM_SERIE),
        "global_economy": _global(ins, hoje),
        "geopolitical_risk": _ausente(SEM_SERIE),
        "capital_markets": _capitais(ins, hoje),
    }
    assert set(itens) == set(CHAVES)
    agora = agora or dt.datetime.combine(hoje, dt.time())
    return Cenario(itens=itens, versao=0, salvo_em=agora.isoformat(),
                   origem=DADOS)


# -- I/O ------------------------------------------------------------------------------

def _ler_usdbrl(engine) -> tuple[tuple[dt.date, float], ...]:
    from sqlalchemy import text
    with engine.connect() as conn:
        linhas = conn.execute(text(
            "SELECT aq.timestamp, aq.close FROM asset_quotes aq "
            "JOIN assets a ON a.id = aq.asset_id WHERE a.ticker = 'USDBRL' "
            "ORDER BY aq.timestamp DESC LIMIT 130")).all()
    saida = []
    for ts, close in linhas:
        if close is None:
            continue
        d = ts.date() if isinstance(ts, dt.datetime) else ts
        saida.append((d, float(close)))
    return tuple(saida)


def ler_insumos(engine=None) -> Insumos:
    """Lê cada fonte por conta própria: a que falha vira motivo, não erro."""
    falhas: dict[str, str] = {}
    observacoes = ()
    try:
        from core.cenario.referencias import _observacoes
        observacoes = _observacoes()
    except Exception as exc:  # noqa: BLE001
        falhas["insumos"] = f"insumos macro ilegíveis ({type(exc).__name__})"
    macro = None
    try:
        from core.b3_data import load_macro_history
        macro = load_macro_history()
    except Exception as exc:  # noqa: BLE001
        falhas["macro"] = f"public.macro ilegível ({type(exc).__name__})"
    if engine is None:
        try:
            from core.database import get_engine
            engine = get_engine()
        except Exception as exc:  # noqa: BLE001
            falhas["curva"] = falhas["usdbrl"] = (
                f"banco indisponível ({type(exc).__name__})")
    curva, usdbrl = None, ()
    if engine is not None:
        try:
            from core.tesouro_curva import taxas_mais_recentes
            curva = taxas_mais_recentes(engine)
        except Exception as exc:  # noqa: BLE001
            falhas["curva"] = f"curva do Tesouro ilegível ({type(exc).__name__})"
        try:
            usdbrl = _ler_usdbrl(engine)
        except Exception as exc:  # noqa: BLE001
            falhas["usdbrl"] = f"USDBRL ilegível ({type(exc).__name__})"
    return Insumos(observacoes=tuple(observacoes or ()), macro_anual=macro,
                   curva=curva, usdbrl=usdbrl, falhas=falhas)


_CACHE: dict = {}


def carregar(engine=None, *, hoje: dt.date | None = None) -> Cenario:
    """O cenário de agora. Cache de ``TTL`` segundos: o dado muda uma vez
    por dia e a aba roda a análise a cada interação."""
    hoje = hoje or dt.date.today()
    guardado = _CACHE.get("c")
    if guardado and time.monotonic() - guardado[0] < TTL and guardado[1] == hoje:
        return guardado[2]
    try:
        cenario = de_dados(ler_insumos(engine), hoje=hoje,
                           agora=dt.datetime.now())
    except Exception:  # noqa: BLE001 — sem cenário a análise segue
        logger.warning("cenario automatico: falha ao montar", exc_info=True)
        return Cenario(origem=DADOS)
    _CACHE["c"] = (time.monotonic(), hoje, cenario)
    return cenario
