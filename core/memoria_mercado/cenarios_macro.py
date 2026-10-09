"""Cenários análogos: das outras vezes em que o macro se pareceu com o de hoje.

O resto do pacote compara o cenário de um *evento* (fato relevante, resultado)
com o cenário dos eventos do mesmo tipo. Este módulo faz a pergunta de cenário
sem evento nenhum, que é a que as LLMs recebem: "da última vez que a Selic
começou a cair com a bolsa subindo forte, o que aconteceu depois?". A resposta
que a LLM dava era narrativa de memória; aqui ela passa a ser uma lista de
meses, com a similaridade de cada um e o que BOVA11, dólar e Selic fizeram nos
12 meses seguintes.

As três recusas do pacote valem aqui também:

1. **Um mês não é um episódio, e um episódio não é uma amostra.** Meses
   vizinhos têm quase o mesmo cenário e quase o mesmo futuro: contar 2016-08,
   2016-09 e 2016-10 como três análogos triplica um caso só. Os análogos saem
   um por episódio (:data:`JANELA_EPISODIO` meses entre eles), e abaixo de
   :data:`amostra.N_MINIMO_EXPERIMENTAL` episódios não sai faixa -- sai a
   lista, caso a caso.
2. **Ausente fica fora da média.** O P/L mediano começa em 2011 e o IPCA de
   hoje pode ainda não ter saído: dimensão sem valor sai do denominador e a
   ``cobertura`` diz quanto do peso foi medido, como em
   :func:`similaridade.calcular`.
3. **Análogo não é previsão.** Nada aqui decide compra ou venda; o contraponto
   (todos os meses, sem filtro de cenário) vai sempre ao lado, porque "subiu em
   4 de 5 análogos" sem a taxa-base (BOVA11 sobe em uns 60% de todas as
   janelas de 12 meses) parece sinal e não é.

Os pesos e as escalas são priores declarados, como em :mod:`similaridade` --
onde a dimensão coincide (juros, inflação, Treasury, valuation) a escala é a
mesma de lá.
"""
from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from math import isfinite
from pathlib import Path

from core.memoria_mercado import amostra as am
from core.memoria_mercado import similaridade as sm

CENARIOS_MACRO_VERSAO = "1.0.0"
ESQUEMA = "cenarios_analogos.v1"
CAMINHO_PADRAO = (Path(__file__).resolve().parents[2] / "data" / "public"
                  / "cenarios_analogos.json.gz")

#: Arquivo com mais horas que isto vai marcado como VELHO no contexto: o "hoje"
#: do painel é o do dia em que foi gerado.
IDADE_VELHA_HORAS = 48

#: Meses mínimos entre dois análogos: com 12, as janelas de 12 meses depois de
#: cada um não se sobrepõem, e cada episódio conta uma vez.
JANELA_EPISODIO = 12

#: O episódio de hoje não é análogo de si mesmo: os últimos 12 meses têm o
#: cenário quase igual e o futuro ainda não aconteceu.
EXCLUSAO_RECENTE = 12

#: Similaridade mínima para um mês contar como análogo. Bem acima do
#: ``SIMILARIDADE_INVALIDANTE`` (25) do pacote: aquele é o piso para "dá para
#: comparar"; este é o piso para "parece com hoje".
LIMIAR_ANALOGO = 70.0

#: Quantos episódios a LLM lê por extenso; o resumo conta todos.
MAX_LISTADOS = 5

HORIZONTES = (3, 6, 12)

#: chave: (rótulo, forma de comparar, escala, peso). Forma "distancia":
#: ``1 - |a-b|/escala``; "razao": ``1 - |a/b-1|/escala``. Escalas em p.p.
#: para juros e inflação, em fração para variações e para o P/L.
DIMENSOES: dict[str, tuple[str, str, float, float]] = {
    "selic": ("Selic meta", "distancia", sm.ESCALAS[sm.DIM_JUROS_BR], 0.20),
    "selic_6m": ("direção da Selic em 6 meses", "distancia", 3.0, 0.20),
    "ipca12": ("IPCA em 12 meses", "distancia", sm.ESCALAS[sm.DIM_INFLACAO], 0.10),
    "usd12": ("dólar em 12 meses", "distancia", 0.30, 0.10),
    "us10": ("Treasury de 10 anos", "distancia", sm.ESCALAS[sm.DIM_JUROS_US], 0.10),
    "pl": ("P/L mediano das 50 mais negociadas", "razao",
           sm.ESCALAS[sm.DIM_VALUATION], 0.15),
    "bova12": ("BOVA11 em 12 meses", "distancia", 0.50, 0.15),
}


# ── painel mensal (puro: dicionários de data -> valor) ───────────────────────

def _mes(d: date) -> date:
    return date(d.year, d.month, 1)


def _soma_meses(m: date, n: int) -> date:
    t = m.year * 12 + m.month - 1 + n
    return date(t // 12, t % 12 + 1, 1)


def _fim(m: date) -> date:
    return _soma_meses(m, 1) - timedelta(days=1)


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if isfinite(f) else None


class _Serie:
    """Valor "conhecido no fim do mês": a última observação até lá, se recente."""

    def __init__(self, pontos, tolerancia_dias: int):
        self.pontos = sorted((d, f) for d, v in (pontos or {}).items()
                             if (f := _num(v)) is not None)
        self.tol = tolerancia_dias

    def em(self, ate: date) -> tuple[float, date] | None:
        lo, hi = 0, len(self.pontos)
        while lo < hi:                       # última data <= ate
            meio = (lo + hi) // 2
            if self.pontos[meio][0] <= ate:
                lo = meio + 1
            else:
                hi = meio
        if lo == 0:
            return None
        d, v = self.pontos[lo - 1]
        return (v, d) if (ate - d).days <= self.tol else None


def _var(serie: _Serie, m: date, meses: int, ate=None) -> float | None:
    a = serie.em(ate or _fim(m))
    b = serie.em(_fim(_soma_meses(m, -meses)))
    if not a or not b or b[0] <= 0:
        return None
    return a[0] / b[0] - 1


def painel_mensal(*, selic, ipca12, usd, us10, bova, pl, hoje: date,
                  inicio: date = date(2010, 1, 1)) -> list[dict]:
    """Estado macro de cada mês e o que veio depois. A última linha é hoje.

    ``selic`` e ``us10``: diárias. ``ipca12``: mês de referência (dia 1) ->
    IPCA 12m. ``usd``: mês (dia 1) -> USDBRL médio. ``bova``: fechamentos
    (mensais com rótulo no dia 1, ou diários). ``pl``: data -> P/L mediano.

    O IPCA de um mês só é conhecido no meio do mês seguinte: o estado do mês
    ``m`` usa o IPCA de referência ``m-1`` ou anterior. O desfecho só usa meses
    já fechados -- o mês corrente, ainda em curso, nunca é "o futuro" de
    ninguém.
    """
    s_selic = _Serie(selic, 10)
    s_ipca = _Serie(ipca12, 75)
    s_usd = _Serie(usd, 45)
    s_us10 = _Serie(us10, 10)
    s_pl = _Serie(pl, 45)
    # BOVA11 vem com rótulo mensal no dia 1 (fechamento do mês) até o ponto em
    # que a série vira diária: reetiquetar o dia 1 para o fim do mês evita que
    # o "fechamento de março" seja lido como preço de 1º de março.
    s_bova = _Serie(_bova_no_fim_do_mes(bova), 10)

    mes_hoje = _mes(hoje)
    linhas: list[dict] = []
    m = _mes(inicio)
    while m <= mes_hoje:
        ate = hoje if m == mes_hoje else _fim(m)
        selic_m = s_selic.em(ate)
        selic_6 = s_selic.em(_fim(_soma_meses(m, -6)))
        ipca_m = s_ipca.em(_soma_meses(m, -1))
        usd_m = s_usd.em(ate)
        usd_a = s_usd.em(_soma_meses(usd_m[1], -12)) if usd_m else None
        bova_m = s_bova.em(ate)
        bova_a = s_bova.em(_fim(_soma_meses(m, -12)))
        us10_m = s_us10.em(ate)
        pl_m = s_pl.em(ate)
        estado = {
            "selic": selic_m[0] if selic_m else None,
            "selic_6m": (round(selic_m[0] - selic_6[0], 4)
                         if selic_m and selic_6 else None),
            "ipca12": ipca_m[0] if ipca_m else None,
            "usd12": (round(usd_m[0] / usd_a[0] - 1, 4)
                      if usd_m and usd_a and usd_a[0] > 0 else None),
            "us10": us10_m[0] if us10_m else None,
            "pl": pl_m[0] if pl_m else None,
            "bova12": (round(bova_m[0] / bova_a[0] - 1, 4)
                       if bova_m and bova_a and bova_a[0] > 0 else None),
        }
        refs = {"ipca12": ipca_m[1].isoformat()[:7] if ipca_m else None,
                "usd12": usd_m[1].isoformat()[:7] if usd_m else None,
                "bova": bova_m[1].isoformat() if bova_m else None}
        depois: dict[str, float | None] = {}
        for h in HORIZONTES:
            alvo = _soma_meses(m, h)
            depois[f"bova_{h}"] = (_var_futura(s_bova, m, alvo)
                                   if alvo < mes_hoje else None)
        alvo = _soma_meses(m, 12)
        fechado = alvo < mes_hoje
        u0, u1 = s_usd.em(_fim(m)), s_usd.em(_fim(alvo)) if fechado else None
        depois["usd_12"] = (round(u1[0] / u0[0] - 1, 4)
                            if fechado and u0 and u1 and u0[0] > 0 else None)
        s0, s1 = s_selic.em(_fim(m)), s_selic.em(_fim(alvo)) if fechado else None
        depois["selic_12"] = (round(s1[0] - s0[0], 4)
                              if fechado and s0 and s1 else None)
        linhas.append({"mes": m.isoformat()[:7], "estado": estado,
                       "refs": refs, "depois": depois})
        m = _soma_meses(m, 1)
    return linhas


def _bova_no_fim_do_mes(bova) -> dict[date, float]:
    """Reetiqueta o fechamento mensal (dia 1, sem outros pregões no mês)."""
    por_mes: dict[date, list[date]] = {}
    for d in (bova or {}):
        por_mes.setdefault(_mes(d), []).append(d)
    saida: dict[date, float] = {}
    for m, dias in por_mes.items():
        for d in dias:
            alvo = _fim(m) if (d.day == 1 and len(dias) == 1) else d
            saida[alvo] = bova[d]
    return saida


def _var_futura(serie: _Serie, m: date, alvo: date) -> float | None:
    a, b = serie.em(_fim(m)), serie.em(_fim(alvo))
    if not a or not b or a[0] <= 0:
        return None
    return round(b[0] / a[0] - 1, 4)


# ── casamento ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Analogo:
    mes: str
    fator: float
    cobertura: float
    estado: dict
    depois: dict


@dataclass(frozen=True)
class Resultado:
    hoje: dict
    episodios: tuple[Analogo, ...] = ()
    meses_comparados: int = 0
    base: dict = field(default_factory=dict)
    limitacoes: tuple[str, ...] = ()


def similaridade(hoje: dict, entao: dict) -> tuple[float | None, float]:
    """(fator 0-100, cobertura 0-1). Ausente sai do denominador."""
    medido = soma = 0.0
    total = sum(p for *_, p in DIMENSOES.values())
    for chave, (_r, forma, escala, peso) in DIMENSOES.items():
        a, b = _num(hoje.get(chave)), _num(entao.get(chave))
        if a is None or b is None:
            continue
        if forma == "razao":
            if b <= 0:
                continue
            dist = abs(a / b - 1)
        else:
            dist = abs(a - b)
        soma += peso * max(0.0, 1.0 - dist / escala)
        medido += peso
    if medido <= 0:
        return None, 0.0
    return round(100 * soma / medido, 1), round(medido / total, 4)


def _distancia_meses(a: str, b: str) -> int:
    ya, ma = map(int, a.split("-"))
    yb, mb = map(int, b.split("-"))
    return abs((ya - yb) * 12 + ma - mb)


def analogos(painel: list[dict], *, limiar: float = LIMIAR_ANALOGO,
             janela: int = JANELA_EPISODIO) -> Resultado:
    """Episódios do passado parecidos com a última linha do painel."""
    if not painel:
        return Resultado(hoje={}, limitacoes=("painel vazio",))
    atual = painel[-1]
    candidatos = []
    for linha in painel[:-EXCLUSAO_RECENTE] if len(painel) > EXCLUSAO_RECENTE else []:
        fator, cob = similaridade(atual["estado"], linha["estado"])
        if fator is None or cob < sm.COBERTURA_MINIMA:
            continue
        candidatos.append(Analogo(linha["mes"], fator, cob, linha["estado"],
                                  linha["depois"]))
    escolhidos: list[Analogo] = []
    for c in sorted(candidatos, key=lambda a: (-a.fator, a.mes)):
        if c.fator < limiar:
            break
        if all(_distancia_meses(c.mes, e.mes) >= janela for e in escolhidos):
            escolhidos.append(c)
    base = _base(painel)
    limitacoes = []
    if not escolhidos:
        limitacoes.append(f"nenhum mês com similaridade >= {limiar:.0f}/100")
    return Resultado(hoje=atual, episodios=tuple(escolhidos),
                     meses_comparados=len(candidatos), base=base,
                     limitacoes=tuple(limitacoes))


def _base(painel: list[dict]) -> dict:
    vals = sorted(v for linha in painel if (v := _num(linha["depois"].get("bova_12"))) is not None)
    if not vals:
        return {"n": 0}
    return {"n": len(vals), "inicio": painel[0]["mes"],
            "mediana": am._percentil(vals, 0.5), "p10": am._percentil(vals, 0.1),
            "p90": am._percentil(vals, 0.9),
            "positivos": sum(1 for v in vals if v > 0) / len(vals)}


# ── texto para a LLM ─────────────────────────────────────────────────────────

def _br(v: float, casas: int = 2) -> str:
    return f"{v:.{casas}f}".replace(".", ",")


def _pp(v) -> str:
    return "?" if v is None else f"{'+' if v >= 0 else '−'}{_br(abs(v))} p.p."


def _pct(v, casas: int = 1) -> str:
    return "?" if v is None else f"{'+' if v >= 0 else '−'}{_br(abs(100 * v), casas)}%"


def _mes_br(m: str) -> str:
    return f"{m[5:7]}/{m[:4]}"


def _estado_txt(e: dict, refs: dict | None = None) -> str:
    refs = refs or {}
    partes = []
    if e.get("selic") is not None:
        partes.append(f"Selic {_br(e['selic'])}% ({_pp(e.get('selic_6m'))} em 6 meses)")
    if e.get("ipca12") is not None:
        ref = f" até {_mes_br(refs['ipca12'])}" if refs.get("ipca12") else ""
        partes.append(f"IPCA 12m {_br(e['ipca12'])}%{ref}")
    if e.get("usd12") is not None:
        partes.append(f"dólar {_pct(e['usd12'])} em 12 meses")
    if e.get("us10") is not None:
        partes.append(f"Treasury 10 anos {_br(e['us10'])}%")
    if e.get("pl") is not None:
        partes.append(f"P/L mediano {_br(e['pl'], 1)}x")
    if e.get("bova12") is not None:
        partes.append(f"BOVA11 {_pct(e['bova12'])} em 12 meses")
    return ", ".join(partes) or "sem dimensão medida"


def _depois_txt(d: dict) -> str:
    bova = ", ".join(f"{_pct(d.get(f'bova_{h}'))} em {h}m" for h in HORIZONTES
                     if d.get(f"bova_{h}") is not None)
    partes = [f"BOVA11 {bova}"] if bova else ["BOVA11 sem preço depois"]
    if d.get("usd_12") is not None:
        partes.append(f"dólar {_pct(d['usd_12'])} em 12m")
    if d.get("selic_12") is not None:
        partes.append(f"Selic {_pp(d['selic_12'])} em 12m")
    return "; ".join(partes)


def linhas_cenarios(publicado: dict | None, origem: str = "") -> list[str]:
    """Seção CENÁRIOS ANÁLOGOS do bloco de contexto. Falha é nomeada."""
    cab = "CENÁRIOS ANÁLOGOS (Memória de Mercado, macro mês a mês desde 2010"
    cab += f"; {origem})" if origem else ")"
    if not publicado or not publicado.get("painel"):
        return [cab + ":", f"  indisponível ({origem or 'painel ausente'}). "
                "Sem análogos históricos nesta resposta: não invente episódios."]
    res = analogos(publicado["painel"])
    hoje = res.hoje
    linhas = [cab + ":",
              f"  Hoje ({_mes_br(hoje['mes'])}): {_estado_txt(hoje['estado'], hoje.get('refs'))}. "
              "P/L mediano das 50 mais negociadas, NÃO o P/L do Ibovespa."]
    n = len(res.episodios)
    criterio = (f"similaridade >= {LIMIAR_ANALOGO:.0f}/100 em {len(DIMENSOES)} "
                f"dimensões com pesos declarados e não calibrados; um mês por "
                f"episódio, {JANELA_EPISODIO} meses ou mais entre eles; fora "
                f"os últimos {EXCLUSAO_RECENTE} meses")
    if not n:
        linhas.append(f"  Nenhum episódio desde {_mes_br(publicado['painel'][0]['mes'])} "
                      f"passa no critério ({criterio}; {res.meses_comparados} meses "
                      "comparados): o cenário de hoje NÃO tem análogo no histórico, "
                      "e isso é o achado -- não cite 'da última vez' sem base.")
    else:
        linhas.append(f"  Episódios parecidos ({criterio}):")
        for a in res.episodios[:MAX_LISTADOS]:
            linhas.append(f"  - {_mes_br(a.mes)}, similaridade {a.fator:.0f}/100 "
                          f"(cobertura {100 * a.cobertura:.0f}%): {_estado_txt(a.estado)} "
                          f"→ depois: {_depois_txt(a.depois)}.")
        if n > MAX_LISTADOS:
            linhas.append(f"  (mais {n - MAX_LISTADOS} episódios acima do limiar "
                          "contam no resumo abaixo)")
        linhas.append("  " + _resumo(res.episodios))
        linhas.append("  Não escolha o episódio que confirma uma tese: quem cita um "
                      "episódio cita também o mais parecido, o resumo e o contraponto.")
    b = res.base
    sintese = _sintese(res)
    if sintese:
        linhas.insert(2, f"  Síntese para citar: {sintese}")
    if b.get("n"):
        linhas.append(
            f"  Contraponto, todos os {b['n']} meses desde {_mes_br(b['inicio'])} "
            f"sem filtro de cenário (janelas sobrepostas, não independentes): "
            f"BOVA11 em 12 meses com mediana {_pct(b['mediana'])}, p10 "
            f"{_pct(b['p10'])}, p90 {_pct(b['p90'])}, positivo em "
            f"{100 * b['positivos']:.0f}% dos meses.")
    linhas.append("  Análogo é contexto histórico, não previsão: não decide compra "
                  "nem venda, e o que veio depois dependeu de fatos daquela época.")
    return linhas


def _sintese(res: Resultado) -> str:
    """Uma frase com o episódio mais recente, o mais parecido, o n e o contraponto.

    A LLM copia o trecho, não a linha: a frase que ela tende a copiar já leva
    a ressalva. "Da última vez" é o episódio mais RECENTE, e é ele que a LLM
    cita quando a pergunta vem nessas palavras; o mais parecido vai junto para
    que um caso conveniente não fale sozinho.
    """
    b = res.base
    contra = (f"em todos os {b['n']} meses desde {_mes_br(b['inicio'])}, BOVA11 teve "
              f"mediana {_pct(b['mediana'])} em 12 meses e subiu em "
              f"{100 * b['positivos']:.0f}% deles" if b.get("n") else "")
    if not res.episodios:
        return ("o cenário de hoje não tem análogo no histórico desde "
                f"{_mes_br(res.base.get('inicio') or '2010-01')}"
                + (f"; {contra}" if contra else "") + ".")

    def caso(a: Analogo) -> str:
        return (f"{_mes_br(a.mes)} (similaridade {a.fator:.0f}/100): "
                f"{_depois_txt(a.depois)}")

    recente = max(res.episodios, key=lambda a: a.mes)
    parecido = res.episodios[0]
    if recente is parecido:
        partes = [f"da última vez com cenário parecido, que é também o mais "
                  f"parecido, {caso(recente)}"]
    else:
        partes = [f"da última vez com cenário parecido, {caso(recente)}",
                  f"o episódio mais parecido foi {caso(parecido)}"]
    vals = [v for a in res.episodios if (v := _num(a.depois.get("bova_12"))) is not None]
    n = len(res.episodios)
    partes.append(f"BOVA11 subiu nos 12 meses seguintes em "
                  f"{sum(1 for v in vals if v > 0)} de {len(vals)} episódios "
                  f"com 12 meses completos (n={n} no critério"
                  f"{', caso a caso, sem faixa' if n < am.N_MINIMO_EXPERIMENTAL else ''})")
    if contra:
        partes.append(contra)
    return "; ".join(partes) + " — análogo é contexto histórico, não previsão."


def _resumo(eps) -> str:
    vals = sorted(v for a in eps if (v := _num(a.depois.get("bova_12"))) is not None)
    n = len(vals)
    if not n:
        return "Resumo: nenhum episódio com 12 meses completos depois."
    pos = sum(1 for v in vals if v > 0)
    if n < am.N_MINIMO_EXPERIMENTAL:
        return (f"Resumo: BOVA11 subiu nos 12 meses seguintes em {pos} de {n} "
                f"episódios (n={n}, abaixo de {am.N_MINIMO_EXPERIMENTAL}: cada "
                "episódio é um caso, não uma amostra; sem faixa).")
    marca = ("experimental, abaixo de 30" if n < am.N_MINIMO_ROBUSTO else "")
    return (f"Resumo: BOVA11 nos 12 meses seguintes com mediana "
            f"{_pct(am._percentil(vals, 0.5))}, p10 {_pct(am._percentil(vals, 0.1))}, "
            f"p90 {_pct(am._percentil(vals, 0.9))}, positivo em {pos} de {n} "
            f"episódios (n={n}{', ' + marca if marca else ''}).")


# ── arquivo publicado ────────────────────────────────────────────────────────

def serializar(gerado_em: datetime, painel: list[dict], fontes: dict) -> bytes:
    """``gerado_em`` no topo: é o carimbo que ``atualizar_vitrines`` lê."""
    carga = {"schema": ESQUEMA, "versao": CENARIOS_MACRO_VERSAO,
             "gerado_em": gerado_em.isoformat(), "fontes": fontes, "painel": painel}
    # mtime=0: mesmo conteúdo, mesmos bytes.
    return gzip.compress(json.dumps(carga, ensure_ascii=False, sort_keys=True).encode(),
                         mtime=0)


def carregar_publicado(caminho: Path = CAMINHO_PADRAO, *,
                       agora: datetime | None = None) -> tuple[dict | None, str]:
    """(carga, origem). Sem carga, ``origem`` diz por quê; velho vai marcado."""
    try:
        carga = json.loads(gzip.decompress(Path(caminho).read_bytes()).decode("utf-8"))
    except FileNotFoundError:
        return None, "arquivo cenarios_analogos ausente"
    except (OSError, ValueError) as exc:
        return None, f"arquivo cenarios_analogos ilegível ({type(exc).__name__})"
    if not isinstance(carga, dict) or carga.get("schema") != ESQUEMA:
        return None, "arquivo cenarios_analogos com esquema desconhecido"
    try:
        gerado = datetime.fromisoformat(str(carga.get("gerado_em")))
    except ValueError:
        return carga, "gerado em data desconhecida -- trate como VELHO"
    if gerado.tzinfo is None:
        gerado = gerado.replace(tzinfo=timezone.utc)
    horas = ((agora or datetime.now(timezone.utc)) - gerado).total_seconds() / 3600
    origem = f"gerado em {gerado.strftime('%d/%m/%Y')}"
    if horas > IDADE_VELHA_HORAS:
        origem += (f", {horas / 24:.0f} dias atrás — VELHO: o 'hoje' abaixo é o "
                   "do dia da geração; diga a data")
    return carga, origem
