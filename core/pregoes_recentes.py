"""Pregões recentes da B3 para o contexto das LLMs: seção MOVIMENTO RECENTE.

Antes daqui, a bolsa entrava no bloco CONTEXTO DE MERCADO só em fechamento
MENSAL. Em 29/10/2026 o último ponto do BOVA11 era de 05/10, porque o publicador
da brapi estava barrado com o Supabase acima de 500 MB. Com isso a LLM não via a
queda de 26/10: BOVA11 −14,5% e SMAL11 −28% num pregão. A "ressaca
pós-eleição" do noticiário virou manchete sem confirmação, e a LLM pediu ao
usuário os fechamentos diários que o projeto já tinha em disco.

A fonte é ``data/public/pregoes_recentes_b3.json.gz``, que
``scripts/publish_pregoes_recentes_b3.py`` gera do COTAHIST oficial. São 63
pregões com fechamento bruto, sem ajuste por provento. A seção traz:

* BOVA11 e SMAL11, os termômetros do Ibovespa e das small caps, com os últimos
  fechamentos dia a dia;
* cada ação ou FII da carteira em questão: variação em 1, 5 e 21 pregões e no
  mês, queda desde a máxima da janela e o maior pregão;
* a amplitude: das 100 ações mais negociadas, quantas caíram e quantas subiram
  em 5 pregões, a mediana e os extremos;
* a "Síntese para citar". A LLM copia o trecho, não a linha. Por isso o
  número sai com data e fonte na mesma frase.

Arquivo ausente, velho ou ilegível vira linha que nomeia a falha. Nada aqui
levanta.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Iterable, Mapping

ARQUIVO = Path(__file__).resolve().parents[1] / "data" / "public" / "pregoes_recentes_b3.json.gz"
FONTE = "COTAHIST da B3, fechamento bruto"
#: Termômetros do mercado: o COTAHIST não traz o índice, traz o ETF.
TERMOMETROS = {"BOVA11": "proxy do Ibovespa", "SMAL11": "proxy das small caps"}
#: Quantos fechamentos dia a dia saem para os termômetros.
DIAS_TERMOMETRO = 6
#: Ativos da carteira com linha própria; o resto fica para a ferramenta.
MAX_ATIVOS = 30
#: Universo da amplitude: as N ações de maior volume médio em 21 pregões.
AMPLITUDE_TOP = 100
#: Pregão com variação acima disso, com o mercado parado, pode ser desdobramento
#: ou grupamento: o COTAHIST não ajusta o preço.
SALTO_SUSPEITO = 0.35
MERCADO_PARADO = 0.05
IDADE_MAX_H = 48
#: Dias úteis de atraso normais: a B3 publica o arquivo do pregão na noite do
#: dia útil seguinte, e a rotina noturna o lê depois disso.
ATRASO_UTEIS = 3
_TIPO = {"acao": "ação", "fii": "FII", "etf": "ETF"}


def carregar() -> dict:
    """O arquivo publicado (``{}`` se ausente ou ilegível; não mutar)."""
    from core.inteligencia_ativos.arquivo_publicado import ler

    return ler(str(ARQUIVO), "pregoes_recentes_b3")


def _num(valor: float, casas: int = 2) -> str:
    texto = f"{valor:,.{casas}f}"
    return texto.replace(",", "§").replace(".", ",").replace("§", ".")


def _pct(fracao: float | None) -> str:
    if fracao is None:
        return "sem dado"
    return f"{100.0 * fracao:+.1f}%".replace(".", ",").replace("-", "−")


def _d(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d/%m/%Y")


def _dm(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d/%m")


def normalizar(ticker: object) -> str:
    tk = str(ticker or "").strip().upper()
    return tk[:-3] if tk.endswith(".SA") else tk


@dataclass(frozen=True)
class Movimento:
    ticker: str
    tipo: str
    nome: str
    data: str
    fechamento: float
    #: pregões -> variação (fração) contra o fechamento ``pregões`` atrás.
    var: Mapping[int, float | None]
    #: (data ISO do último fechamento do mês anterior, variação desde ele).
    mes: tuple[str, float] | None
    maxima: float
    data_maxima: str
    #: (data ISO, variação) do pregão de maior variação absoluta em 21 pregões.
    maior_dia: tuple[str, float] | None
    salto_suspeito: bool


def _pontos(art: Mapping, ticker: str) -> list[tuple[int, float]]:
    item = (art.get("por_ticker") or {}).get(ticker) or {}
    return [(i, float(c)) for i, c in enumerate(item.get("c") or ()) if c]


def _antes(pontos: list[tuple[int, float]], limite: int) -> float | None:
    """Último fechamento em pregão de índice ``<= limite``."""
    anteriores = [c for i, c in pontos if i <= limite]
    return anteriores[-1] if anteriores else None


def _var_dia(art: Mapping, ticker: str, iso: str) -> float | None:
    pregoes = art.get("pregoes") or []
    if iso not in pregoes:
        return None
    i = pregoes.index(iso)
    pontos = dict(_pontos(art, ticker))
    if i not in pontos or i - 1 not in pontos:
        return None
    return pontos[i] / pontos[i - 1] - 1


def movimento(art: Mapping, ticker: str) -> Movimento | None:
    """Movimento de um ticker no arquivo; ``None`` se ele não estiver lá."""
    tk = normalizar(ticker)
    pregoes = art.get("pregoes") or []
    pontos = _pontos(art, tk)
    if not pontos or not pregoes:
        return None
    item = art["por_ticker"][tk]
    i0, c0 = pontos[-1]
    var = {}
    for k in (1, 5, 21):
        base = _antes(pontos, i0 - k)
        var[k] = c0 / base - 1 if base else None
    mes_atual = pregoes[i0][:7]
    anteriores = [(i, c) for i, c in pontos if pregoes[i][:7] < mes_atual]
    mes = ((pregoes[anteriores[-1][0]], c0 / anteriores[-1][1] - 1)
           if anteriores else None)
    i_max, maxima = max(pontos, key=lambda p: p[1])
    maior = None
    for (ia, ca), (ib, cb) in zip(pontos, pontos[1:]):
        if ib < i0 - 20:
            continue
        v = cb / ca - 1
        if maior is None or abs(v) > abs(maior[1]):
            maior = (pregoes[ib], v)
    suspeito = False
    if maior and abs(maior[1]) > SALTO_SUSPEITO and tk not in TERMOMETROS:
        mercado = _var_dia(art, "BOVA11", maior[0])
        suspeito = mercado is None or abs(mercado) < MERCADO_PARADO
    return Movimento(ticker=tk, tipo=item.get("t") or "", nome=item.get("n") or "",
                     data=pregoes[i0], fechamento=c0, var=var, mes=mes,
                     maxima=maxima, data_maxima=pregoes[i_max], maior_dia=maior,
                     salto_suspeito=suspeito)


def ultimos_fechamentos(art: Mapping, tickers: Iterable[str]) -> dict[str, tuple[date, float]]:
    """Último fechamento de cada ticker presente: ``{ticker: (data, fechamento)}``."""
    pregoes = art.get("pregoes") or []
    saida = {}
    for tk in tickers:
        pontos = _pontos(art, normalizar(tk))
        if pontos:
            i, c = pontos[-1]
            saida[tk] = (date.fromisoformat(pregoes[i]), c)
    return saida


def fechamentos(art: Mapping, ticker: str, n: int) -> list[tuple[str, float | None]]:
    """Os últimos ``n`` pregões do arquivo com o fechamento do ticker (ou ``None``)."""
    pregoes = art.get("pregoes") or []
    item = (art.get("por_ticker") or {}).get(normalizar(ticker)) or {}
    serie = list(item.get("c") or ())
    return list(zip(pregoes, serie))[-n:] if serie else []


def linha_ativo(m: Movimento, *, rotulo: str = "") -> str:
    tipo = _TIPO.get(m.tipo, m.tipo)
    nome = ", ".join(p for p in (m.nome, tipo, rotulo) if p)
    partes = [f"  {m.ticker} ({nome}): R$ {_num(m.fechamento)} em {_d(m.data)}",
              " · ".join(f"{k} {'pregão' if k == 1 else 'pregões'} {_pct(m.var[k])}"
                         for k in (1, 5, 21))]
    if m.mes:
        partes.append(f"no mês (desde {_dm(m.mes[0])}) {_pct(m.mes[1])}")
    queda = m.fechamento / m.maxima - 1
    if queda < -0.005:
        partes.append(f"{_pct(queda)} desde a máxima da janela (R$ {_num(m.maxima)} "
                      f"em {_dm(m.data_maxima)})")
    else:
        partes.append("na máxima da janela de 63 pregões")
    if m.maior_dia:
        texto = f"maior pregão: {_dm(m.maior_dia[0])} {_pct(m.maior_dia[1])}"
        if m.salto_suspeito:
            texto += (" — com o BOVA11 parado no mesmo dia, pode ser desdobramento ou "
                      "grupamento (o COTAHIST não ajusta): confira antes de citar como "
                      "queda ou alta")
        partes.append(texto)
    return " · ".join(partes) + "."


def _linha_termometro(art: Mapping, m: Movimento) -> list[str]:
    linhas = [linha_ativo(m, rotulo=TERMOMETROS[m.ticker])]
    dias = [(d, c) for d, c in fechamentos(art, m.ticker, DIAS_TERMOMETRO + 1) if c]
    texto = []
    for (_, ca), (db, cb) in zip(dias, dias[1:]):
        texto.append(f"{_dm(db)} R$ {_num(cb)} ({_pct(cb / ca - 1)})")
    if texto:
        linhas.append(f"    dia a dia: {' · '.join(texto)}.")
    return linhas


def amplitude(art: Mapping, *, top: int = AMPLITUDE_TOP, pregoes: int = 5) -> dict | None:
    """Das ``top`` ações mais negociadas: quantas caíram e subiram em ``pregoes``."""
    por = art.get("por_ticker") or {}
    n_pregoes = len(art.get("pregoes") or [])
    acoes = sorted((tk for tk, it in por.items() if it.get("t") == "acao"),
                   key=lambda tk: -(por[tk].get("v") or 0))[:top]
    vars_ = []
    for tk in acoes:
        c = por[tk].get("c") or []
        if len(c) == n_pregoes and n_pregoes > pregoes and c[-1] and c[-1 - pregoes]:
            vars_.append((tk, c[-1] / c[-1 - pregoes] - 1))
    if not vars_:
        return None
    vars_.sort(key=lambda p: p[1])
    return {"n": len(vars_), "caiu": sum(1 for _, v in vars_ if v < 0),
            "subiu": sum(1 for _, v in vars_ if v > 0),
            "mediana": median(v for _, v in vars_),
            "quedas": [p for p in vars_[:5] if p[1] < 0],
            "altas": [p for p in reversed(vars_[-5:]) if p[1] > 0],
            "pregoes": pregoes, "desde": (art.get("pregoes") or [])[-1 - pregoes]}


def _dias_uteis_entre(inicio: date, fim: date) -> int:
    n, d = 0, inicio
    while d < fim:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def _sintese(art: Mapping, bova: Movimento | None, amp: dict | None) -> str | None:
    if bova is None:
        return None
    frases = []
    if bova.maior_dia and abs(bova.maior_dia[1]) >= 0.02:
        d, v = bova.maior_dia
        pregoes = art.get("pregoes") or []
        serie = dict(_pontos(art, "BOVA11"))
        i = pregoes.index(d)
        antes = _antes(list(serie.items()), i - 1)
        de_para = f" (R$ {_num(antes)} → R$ {_num(serie[i])})" if antes else ""
        frases.append(f"no pregão de {_d(d)} o BOVA11 (proxy do Ibovespa) "
                      f"{'caiu' if v < 0 else 'subiu'} {_pct(abs(v))[1:]}{de_para}")
    frases.append(f"em 5 pregões até {_d(bova.data)}, BOVA11 {_pct(bova.var[5])}"
                  f" e {_pct(bova.fechamento / bova.maxima - 1)} desde a máxima de "
                  f"{_d(bova.data_maxima)}")
    if amp:
        frases.append(f"das {amp['n']} ações mais negociadas, {amp['caiu']} caíram e "
                      f"{amp['subiu']} subiram em 5 pregões (mediana {_pct(amp['mediana'])})")
    return f"  Síntese para citar: {'; '.join(frases)} — {FONTE}."


def cabecalho(art: Mapping, agora: datetime) -> str:
    base = art.get("base_ate") or (art.get("pregoes") or [""])[-1]
    texto = ("MOVIMENTO RECENTE DA BOLSA (pregões diários da B3, "
             f"{FONTE}, sem ajuste por provento; último pregão {_d(base)}")
    avisos = []
    try:
        gerado = datetime.fromisoformat(str(art.get("gerado_em")))
        if gerado.tzinfo is None:
            gerado = gerado.replace(tzinfo=timezone.utc)
        horas = (agora - gerado).total_seconds() / 3600
        if horas > IDADE_MAX_H:
            avisos.append(f"VELHO: arquivo gerado há {horas:.0f} h")
    except (TypeError, ValueError):
        avisos.append("data de geração ilegível")
    atraso = _dias_uteis_entre(date.fromisoformat(base), agora.date())
    if atraso > ATRASO_UTEIS:
        avisos.append(f"ÚLTIMO PREGÃO HÁ {atraso} DIAS ÚTEIS: não descreve a bolsa de hoje")
    if avisos:
        texto += " — " + "; ".join(avisos)
    return texto + "):"


NOTA_LEITURA = (
    "  Leitura: é o \"agora\" do preço. Prevalece sobre o fechamento mensal da "
    "TRAJETÓRIA e da ferramenta preco_ativo quando eles forem mais velhos. "
    "Manchete que o movimento de preço confirma é fato medido: cite a variação "
    "com data e fonte em vez de descartá-la. Fechamento bruto: provento e "
    "desdobramento não estão descontados."
)


def linhas(tickers: Iterable[str] = (), *, art: Mapping | None = None,
           agora: datetime | None = None) -> list[str]:
    """A seção inteira. ``tickers``: os ativos B3/FII em questão (carteira, empresa)."""
    art = carregar() if art is None else art
    agora = agora or datetime.now(timezone.utc)
    if not art or not art.get("pregoes"):
        return ["MOVIMENTO RECENTE DA BOLSA: arquivo pregoes_recentes_b3 ausente ou "
                "ilegível neste ambiente — sem preço diário da B3. Não trate como "
                "mercado parado: diga que faltou o dado diário."]
    saida = [cabecalho(art, agora)]
    termometros = {tk: movimento(art, tk) for tk in TERMOMETROS}
    for tk, m in termometros.items():
        saida += (_linha_termometro(art, m) if m
                  else [f"  {tk} ({TERMOMETROS[tk]}): sem pregão no arquivo."])
    pedidos = list(dict.fromkeys(normalizar(t) for t in tickers if normalizar(t)))
    pedidos = [t for t in pedidos if t not in TERMOMETROS]
    if pedidos:
        saida.append("  Ativos em questão:")
        ausentes = []
        for tk in pedidos[:MAX_ATIVOS]:
            m = movimento(art, tk)
            if m:
                saida.append(linha_ativo(m))
            else:
                ausentes.append(tk)
        if ausentes:
            saida.append(f"  Sem pregão no COTAHIST nos últimos 21 pregões: "
                         f"{', '.join(ausentes)} (ticker antigo, sem negócio ou fora "
                         "da B3).")
        if len(pedidos) > MAX_ATIVOS:
            saida.append(f"  E mais {len(pedidos) - MAX_ATIVOS} ativos fora desta lista: "
                         "use a ferramenta preco_diario_recente para eles.")
    amp = amplitude(art)
    if amp:
        quedas = ", ".join(f"{tk} {_pct(v)}" for tk, v in amp["quedas"]) or "nenhuma"
        altas = ", ".join(f"{tk} {_pct(v)}" for tk, v in amp["altas"]) or "nenhuma"
        saida.append(
            f"  Amplitude (as {amp['n']} ações mais negociadas, 5 pregões desde "
            f"{_dm(amp['desde'])}): {amp['caiu']} caíram, {amp['subiu']} subiram, "
            f"mediana {_pct(amp['mediana'])}; maiores quedas {quedas}; maiores "
            f"altas {altas}.")
    sintese = _sintese(art, termometros.get("BOVA11"), amp)
    if sintese:
        saida.append(sintese)
    saida.append(NOTA_LEITURA)
    return saida
