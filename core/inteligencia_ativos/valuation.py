"""
core/inteligencia_ativos/valuation.py
Valuation: o múltiplo atual contra o histórico do próprio ativo e contra os
pares, com as faixas de referência e as premissas usadas. Puro, sem I/O.

Responde, por métrica: se ela faz sentido para ESTE ativo (e por que não,
quando não faz), o valor atual, a média e a mediana do histórico numa janela
declarada, em que percentil do próprio histórico o valor atual está, onde ele
fica diante da mediana dos pares, e as faixas (interquartil e mínimo–máximo)
que servem de referência.

Nunca dá veredito de preço. Cada frase é uma comparação verificável ("O
P/VP atual está abaixo da média histórica dos últimos 10 anos") e fica no
bloco DADO; o que ela significa é INTERPRETAÇÃO, e é da LLM. Um múltiplo
abaixo da média pode refletir risco maior, lucro no pico do ciclo ou juros
mais altos -- não é, por si, oportunidade.

Quem lê os números é ``fontes_valuation``; quem escolhe os pares é
``pares``. Aqui só se calcula e se escreve.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from core.inteligencia_ativos.fundamentos import (
    ACAO,
    ETF,
    FII,
    NAO_DISPONIVEL,
    PCT,
    RENDA_FIXA,
    ROTULO_TIPO,
    Dado,
    Metrica,
    X,
    _tem_valor,
    formatar,
)

# frequência do histórico → (janela em observações, mínimo, unidade no plural)
ANUAL = "anual"
MENSAL = "mensal"
DIARIA = "diaria"
JANELA: dict[str, tuple[int, int, str, str]] = {
    ANUAL: (10, 5, "anos", "observações anuais"),
    MENSAL: (60, 12, "meses", "observações mensais"),
    DIARIA: (756, 60, "pregões", "observações diárias"),
}

# diferença relativa até a qual o valor é "em linha" com a referência
TOLERANCIA_EM_LINHA = 0.05
# Taxa do Tesouro anda em centésimos: 5% de 7,4% são 0,37 p.p., diferença que
# o mercado de juros não chama de "em linha". 1% dá ~0,07 p.p.
TOLERANCIA: dict[str, float] = {"taxa_mercado": 0.01}
# artigo do rótulo na frase ("A Taxa de mercado", "O P/L")
ARTIGO: dict[str, str] = {"taxa_mercado": "A"}

ACIMA = "acima"
ABAIXO = "abaixo"
EM_LINHA = "em_linha"
SEM_DADO = "sem_dado"

CATALOGO: dict[str, tuple[Metrica, ...]] = {
    ACAO: (
        Metrica("p_l", "P/L", X),
        Metrica("p_vp", "P/VP", X),
        Metrica("dividend_yield", "Dividend yield", PCT),
        Metrica("ev_ebit", "EV/EBIT", X),
    ),
    FII: (
        Metrica("p_vp", "P/VP", X),
        Metrica("dividend_yield", "Dividend yield (12 meses)", PCT),
        Metrica("cap_rate", "Cap rate implícito", PCT),
    ),
    RENDA_FIXA: (
        Metrica("taxa_mercado", "Taxa de mercado (venda)", PCT),
    ),
    ETF: (),
}

# Por que cada métrica existe para a classe. Vai para as premissas.
PORQUE: dict[str, str] = {
    "p_l": "preço pago por unidade de lucro; só tem leitura com lucro positivo",
    "p_vp": "preço contra o patrimônio contábil; só tem leitura com "
            "patrimônio positivo",
    "dividend_yield": "proventos dos últimos 12 meses (ou do exercício) "
                      "sobre o preço",
    "ev_ebit": "valor da firma (mercado + dívida líquida) contra o resultado "
               "operacional; não se aplica a bancos e seguradoras, cuja "
               "dívida é matéria-prima",
    "cap_rate": "renda imobiliária implícita no preço; só para fundos de "
                "tijolo",
    "taxa_mercado": "a taxa que o mercado exige hoje para o título; na "
                    "renda fixa é o equivalente do múltiplo",
}

# Por que não se aplica (texto padrão por razão).
NAO_SE_APLICA = {
    "lucro_negativo": "Não se aplica: lucro do último exercício não é "
                      "positivo, e P/L negativo não tem leitura.",
    "patrimonio_negativo": "Não se aplica: patrimônio líquido não é positivo.",
    "financeira": "Não se aplica: em bancos e seguradoras a dívida é "
                  "matéria-prima, e EV/EBIT não mede o mesmo que nas demais.",
    "ebit_negativo": "Não se aplica: resultado operacional não é positivo.",
    "fii_nao_tijolo": "Não se aplica: cap rate só tem leitura em fundo de "
                      "tijolo (imóveis físicos).",
    "fora_do_tesouro": "Não se aplica: só títulos do Tesouro Direto têm taxa "
                       "de mercado publicada; CDB, LCI e similares não têm "
                       "cotação pública.",
}

GUIA_INTERPRETACAO: dict[str, tuple[str, ...]] = {
    ACAO: (
        "O múltiplo atual difere do histórico por mudança de preço ou de "
        "lucro? Lucro no pico ou no vale do ciclo distorce o P/L.",
        "O que mudou na empresa, no setor e nos juros desde o período do "
        "histórico justifica um múltiplo diferente?",
        "A diferença em relação aos pares se explica por crescimento, "
        "rentabilidade ou risco diferentes?",
    ),
    FII: (
        "O P/VP diferente do histórico reflete o preço ou uma reavaliação do "
        "patrimônio (VPA)?",
        "O dividend yield atual é recorrente ou inclui resultado não "
        "recorrente?",
        "Diferenças contra os pares se explicam por qualidade dos imóveis, "
        "vacância ou alavancagem?",
    ),
    RENDA_FIXA: (
        "A taxa atual está acima ou abaixo do histórico por mudança dos "
        "juros em geral ou do prêmio deste vencimento?",
        "Para quem já tem o título, taxa maior hoje significa marcação a "
        "mercado menor; para quem compra, taxa maior contratada.",
    ),
    ETF: (),
}

AVISO = ("Nenhuma destas comparações é veredito sobre o preço do ativo: "
         "múltiplo diferente da média ou dos pares pode refletir risco, "
         "crescimento, ciclo ou juros diferentes.")


@dataclass(frozen=True)
class Entrada:
    """O que a fonte leu para uma métrica.

    ``historico``: pares (referência, valor) em ordem cronológica, na
    ``frequencia`` indicada. ``excluida``: chave de ``NAO_SE_APLICA`` (ou
    texto livre) quando a métrica não se aplica a este ativo.
    """
    atual: Dado | None = None
    historico: tuple = ()
    frequencia: str = ANUAL
    fonte_historico: str | None = None
    excluida: str | None = None


@dataclass(frozen=True)
class Estatistica:
    n: int
    inicio: str
    fim: str
    media: float
    mediana: float
    p25: float
    p75: float
    minimo: float
    maximo: float
    frequencia: str | None = None

    def como_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "n", "inicio", "fim", "media", "mediana", "p25", "p75",
            "minimo", "maximo", "frequencia")}

    @classmethod
    def de_dict(cls, d: dict | None) -> "Estatistica | None":
        return cls(**d) if d else None


@dataclass(frozen=True)
class Faixa:
    rotulo: str
    minimo: float
    maximo: float


@dataclass(frozen=True)
class LinhaValuation:
    chave: str
    rotulo: str
    unidade: str
    aplicavel: bool
    motivo: str | None = None          # por que não se aplica / sem dado
    atual: float | None = None
    fonte: str | None = None
    referencia: str | None = None
    historico: Estatistica | None = None
    fonte_historico: str | None = None
    pares: Estatistica | None = None
    percentil_historico: float | None = None
    posicao_historica: str = SEM_DADO
    posicao_pares: str = SEM_DADO
    comparacao_historica: str = NAO_DISPONIVEL
    comparacao_pares: str = NAO_DISPONIVEL
    faixas: tuple[Faixa, ...] = ()

    def texto_atual(self, moeda: str = "BRL") -> str:
        return formatar(self.atual, self.unidade, moeda)


@dataclass(frozen=True)
class Valuation:
    tipo: str | None
    moeda: str
    linhas: tuple[LinhaValuation, ...] = ()
    premissas: tuple[str, ...] = ()
    grupo_pares: str | None = None      # descrição do grupo usado
    motivo: str | None = None

    @property
    def com_dado(self) -> tuple[LinhaValuation, ...]:
        return tuple(ln for ln in self.linhas if ln.aplicavel and ln.atual is not None)

    @property
    def fontes(self) -> tuple[str, ...]:
        vistas: list[str] = []
        for ln in self.com_dado:
            for f in (ln.fonte, ln.fonte_historico):
                if f and f not in vistas:
                    vistas.append(f)
        return tuple(vistas)

    def linha(self, chave: str) -> LinhaValuation | None:
        return next((ln for ln in self.linhas if ln.chave == chave), None)

    def como_dict(self) -> dict:
        return {
            "tipo": self.tipo, "moeda": self.moeda, "motivo": self.motivo,
            "grupo_pares": self.grupo_pares, "premissas": list(self.premissas),
            "fontes": list(self.fontes),
            "linhas": [{
                "chave": ln.chave, "rotulo": ln.rotulo, "unidade": ln.unidade,
                "aplicavel": ln.aplicavel, "motivo": ln.motivo,
                "atual": ln.atual, "texto": ln.texto_atual(self.moeda),
                "fonte": ln.fonte, "referencia": ln.referencia,
                "historico": ln.historico.como_dict() if ln.historico else None,
                "fonte_historico": ln.fonte_historico,
                "pares": ln.pares.como_dict() if ln.pares else None,
                "percentil_historico": ln.percentil_historico,
                "posicao_historica": ln.posicao_historica,
                "posicao_pares": ln.posicao_pares,
                "comparacao_historica": ln.comparacao_historica,
                "comparacao_pares": ln.comparacao_pares,
                "faixas": [[f.rotulo, f.minimo, f.maximo] for f in ln.faixas],
            } for ln in self.linhas],
        }

    @classmethod
    def de_dict(cls, d: dict | None) -> "Valuation":
        d = d or {}
        linhas = tuple(LinhaValuation(
            chave=ln["chave"], rotulo=ln["rotulo"], unidade=ln["unidade"],
            aplicavel=bool(ln.get("aplicavel")), motivo=ln.get("motivo"),
            atual=ln.get("atual"), fonte=ln.get("fonte"),
            referencia=ln.get("referencia"),
            historico=Estatistica.de_dict(ln.get("historico")),
            fonte_historico=ln.get("fonte_historico"),
            pares=Estatistica.de_dict(ln.get("pares")),
            percentil_historico=ln.get("percentil_historico"),
            posicao_historica=ln.get("posicao_historica") or SEM_DADO,
            posicao_pares=ln.get("posicao_pares") or SEM_DADO,
            comparacao_historica=ln.get("comparacao_historica") or NAO_DISPONIVEL,
            comparacao_pares=ln.get("comparacao_pares") or NAO_DISPONIVEL,
            faixas=tuple(Faixa(*f) for f in ln.get("faixas") or ()),
        ) for ln in d.get("linhas") or ())
        return cls(d.get("tipo"), d.get("moeda") or "BRL", linhas,
                   tuple(d.get("premissas") or ()), d.get("grupo_pares"),
                   d.get("motivo"))


# -- estatística ---------------------------------------------------------------------

def _quantil(ordenados: list[float], q: float) -> float:
    """Quantil com interpolação linear (o mesmo do numpy por padrão)."""
    if len(ordenados) == 1:
        return ordenados[0]
    pos = (len(ordenados) - 1) * q
    base = math.floor(pos)
    frac = pos - base
    if base + 1 >= len(ordenados):
        return ordenados[-1]
    return ordenados[base] + (ordenados[base + 1] - ordenados[base]) * frac


def estatistica(valores, *, inicio: str = "", fim: str = "",
                frequencia: str | None = None) -> Estatistica | None:
    xs = [float(v) for v in valores if _tem_valor(v)]
    if not xs:
        return None
    o = sorted(xs)
    return Estatistica(len(o), str(inicio), str(fim), statistics.fmean(o),
                       statistics.median(o), _quantil(o, 0.25),
                       _quantil(o, 0.75), o[0], o[-1], frequencia)


def janela(historico, frequencia: str) -> tuple[list, int]:
    """Últimas observações válidas dentro da janela da frequência."""
    tamanho, minimo, _, _ = JANELA[frequencia]
    validos = [(str(r), float(v)) for r, v in historico if _tem_valor(v)]
    return validos[-tamanho:], minimo


def percentil(valor: float, amostra) -> float | None:
    """Percentil (0–100) do valor dentro da amostra: fração ≤ valor, com
    empates contados pela metade."""
    xs = [float(v) for v in amostra if _tem_valor(v)]
    if not xs or not _tem_valor(valor):
        return None
    abaixo = sum(1 for x in xs if x < valor)
    iguais = sum(1 for x in xs if x == valor)
    return round(100.0 * (abaixo + 0.5 * iguais) / len(xs), 0)


def posicao(valor, referencia, tolerancia: float = TOLERANCIA_EM_LINHA) -> str:
    """ACIMA / ABAIXO / EM_LINHA (até ``tolerancia`` relativa)."""
    if not _tem_valor(valor) or not _tem_valor(referencia):
        return SEM_DADO
    v, r = float(valor), float(referencia)
    if r == 0:
        return EM_LINHA if v == 0 else (ACIMA if v > 0 else ABAIXO)
    if abs(v - r) / abs(r) <= tolerancia:
        return EM_LINHA
    return ACIMA if v > r else ABAIXO


_POSICAO_TXT = {ACIMA: "acima", ABAIXO: "abaixo", EM_LINHA: "em linha com"}


def _periodo(est: Estatistica) -> str:
    return est.inicio if est.inicio == est.fim else f"{est.inicio}–{est.fim}"


def frase_historica(rotulo: str, unidade: str, atual, est: Estatistica | None,
                    pct: float | None, frequencia: str, n_valido: int,
                    minimo: int, moeda: str = "BRL", *, artigo: str = "O",
                    tolerancia: float = TOLERANCIA_EM_LINHA) -> tuple[str, str]:
    if not _tem_valor(atual):
        return SEM_DADO, NAO_DISPONIVEL
    _, _, plural, obs = JANELA[frequencia]
    if est is None or n_valido < minimo:
        return SEM_DADO, (f"Histórico insuficiente para comparar: {n_valido} "
                          f"{obs} (mínimo {minimo}).")
    pos = posicao(atual, est.media, tolerancia)
    rel = _POSICAO_TXT[pos]
    prep = " a" if pos == EM_LINHA else " da"
    frase = (f"{artigo} {rotulo} atual ({formatar(atual, unidade, moeda)}) está {rel}"
             f"{prep} média histórica dos últimos {est.n} {plural} "
             f"({formatar(est.media, unidade, moeda)}; mediana "
             f"{formatar(est.mediana, unidade, moeda)}; {est.n} {obs}, "
             f"{_periodo(est)})")
    if pct is not None:
        frase += f" e no percentil {int(pct)} do próprio histórico"
    return pos, frase + "."


def frase_pares(rotulo: str, unidade: str, atual, est: Estatistica | None,
                grupo: str | None, moeda: str = "BRL", *, artigo: str = "O",
                tolerancia: float = TOLERANCIA_EM_LINHA) -> tuple[str, str]:
    if not _tem_valor(atual):
        return SEM_DADO, NAO_DISPONIVEL
    if est is None:
        return SEM_DADO, ("Sem pares comparáveis com este dado."
                          if grupo else "Sem grupo de pares comparáveis.")
    pos = posicao(atual, est.mediana, tolerancia)
    rel = _POSICAO_TXT[pos]
    prep = " a" if pos == EM_LINHA else " da"
    return pos, (f"{artigo} {rotulo} ({formatar(atual, unidade, moeda)}) está {rel}"
                 f"{prep} mediana dos pares comparáveis "
                 f"({formatar(est.mediana, unidade, moeda)}; {est.n} "
                 f"{'par' if est.n == 1 else 'pares'}).")


# -- montagem ------------------------------------------------------------------------

def montar(tipo: str | None, entradas: dict[str, Entrada], *,
           pares: dict[str, tuple] | None = None, grupo_pares: str | None = None,
           moeda: str = "BRL", premissas_fonte: tuple[str, ...] = (),
           motivo: str | None = None) -> Valuation:
    """Encaixa as entradas no catálogo da classe e calcula as comparações.

    ``pares``: chave → valores da métrica nos pares escolhidos por
    ``pares.selecionar``. Chave fora do catálogo levanta, como em
    ``fundamentos.montar``.
    """
    if tipo is None or not CATALOGO.get(tipo):
        return Valuation(tipo, moeda, (), (), grupo_pares, motivo or (
            f"{ROTULO_TIPO.get(tipo, 'Classe')}: sem métrica de valuation "
            "com dado no projeto." if tipo else
            "Classe de ativo sem catálogo de valuation."))
    catalogo = CATALOGO[tipo]
    estranhas = set(entradas) - {m.chave for m in catalogo}
    if estranhas:
        raise ValueError(f"métricas fora do catálogo de valuation de {tipo}: "
                         f"{sorted(estranhas)}")
    pares = pares or {}
    linhas = []
    frequencias: set[str] = set()
    for mt in catalogo:
        e = entradas.get(mt.chave) or Entrada()
        if e.excluida:
            linhas.append(LinhaValuation(
                mt.chave, mt.rotulo, mt.unidade, False,
                NAO_SE_APLICA.get(e.excluida, e.excluida)))
            continue
        atual = e.atual.valor if e.atual and _tem_valor(e.atual.valor) else None
        janela_, minimo = janela(e.historico, e.frequencia)
        valores = [v for _, v in janela_]
        est_h = (estatistica(valores, inicio=janela_[0][0], fim=janela_[-1][0],
                             frequencia=e.frequencia) if janela_ else None)
        if est_h is not None and est_h.n < minimo:
            est_h_ok = None
        else:
            est_h_ok = est_h
        if est_h_ok is not None:
            frequencias.add(e.frequencia)
        pct = percentil(atual, valores) if est_h_ok and atual is not None else None
        pos_h, frase_h = frase_historica(mt.rotulo, mt.unidade, atual, est_h_ok,
                                         pct, e.frequencia, len(valores),
                                         minimo, moeda,
                                         artigo=ARTIGO.get(mt.chave, "O"),
                                         tolerancia=TOLERANCIA.get(
                                             mt.chave, TOLERANCIA_EM_LINHA))
        est_p = estatistica(pares.get(mt.chave) or ())
        pos_p, frase_p = frase_pares(mt.rotulo, mt.unidade, atual, est_p,
                                     grupo_pares, moeda,
                                     artigo=ARTIGO.get(mt.chave, "O"),
                                     tolerancia=TOLERANCIA.get(
                                         mt.chave, TOLERANCIA_EM_LINHA))
        faixas = []
        if est_h_ok:
            faixas.append(Faixa(f"Interquartil do histórico ({_periodo(est_h_ok)})",
                                est_h_ok.p25, est_h_ok.p75))
            faixas.append(Faixa(f"Mínimo–máximo do histórico ({_periodo(est_h_ok)})",
                                est_h_ok.minimo, est_h_ok.maximo))
        if est_p and est_p.n >= 3:
            faixas.append(Faixa(f"Interquartil dos pares ({est_p.n})",
                                est_p.p25, est_p.p75))
        linhas.append(LinhaValuation(
            mt.chave, mt.rotulo, mt.unidade, True,
            None if atual is not None else NAO_DISPONIVEL,
            atual, e.atual.fonte if e.atual else None,
            e.atual.referencia if e.atual else None,
            est_h_ok, e.fonte_historico if est_h_ok else None, est_p, pct,
            pos_h, pos_p, frase_h, frase_p, tuple(faixas)))
    return Valuation(tipo, moeda, tuple(linhas),
                     premissas(tipo, frequencias, grupo_pares, premissas_fonte),
                     grupo_pares, motivo)


def premissas(tipo: str, frequencias, grupo_pares: str | None,
              extras: tuple[str, ...] = ()) -> tuple[str, ...]:
    saida = [f"Métricas da classe {ROTULO_TIPO[tipo]}: " + "; ".join(
        f"{m.rotulo} — {PORQUE[m.chave]}" for m in CATALOGO[tipo]) + "."]
    for fq in sorted(frequencias):
        tamanho, minimo, plural, obs = JANELA[fq]
        saida.append(f"Histórico {fq}: até os últimos {tamanho} {plural}; "
                     f"comparação só com pelo menos {minimo} {obs}.")
    saida.append("Percentil = posição do valor atual entre as observações do "
                 "próprio histórico na janela (0 = menor, 100 = maior).")
    tol = sorted({TOLERANCIA.get(m.chave, TOLERANCIA_EM_LINHA)
                  for m in CATALOGO[tipo]})
    saida.append("\"Em linha\" = diferença de até " + " ou ".join(
        f"{t * 100:.0f}%" for t in tol) + " da referência"
        + (" (1% na taxa do Tesouro, que anda em centésimos)."
           if any(m.chave in TOLERANCIA for m in CATALOGO[tipo]) else "."))
    saida.append("Faixas de referência são o intervalo interquartil (25%–75%) "
                 "e o mínimo–máximo observados; não são preço-alvo.")
    saida.append(f"Pares: {grupo_pares}." if grupo_pares
                 else "Pares: nenhum grupo comparável encontrado.")
    saida.extend(extras)
    return tuple(saida)


# -- textos --------------------------------------------------------------------------

def resumo(v: Valuation) -> str:
    if not v.linhas:
        return v.motivo or NAO_DISPONIVEL
    comparadas = [ln for ln in v.com_dado if ln.posicao_historica != SEM_DADO
                  or ln.posicao_pares != SEM_DADO]
    if not v.com_dado:
        return (f"{ROTULO_TIPO[v.tipo]}: nenhuma métrica de valuation com "
                "valor atual disponível.")
    partes = []
    for ln in comparadas[:2]:
        pedacos = []
        if ln.posicao_historica != SEM_DADO:
            pedacos.append(f"{_POSICAO_TXT[ln.posicao_historica]}"
                           f"{' a' if ln.posicao_historica == EM_LINHA else ' da'} "
                           "média histórica")
        if ln.posicao_pares != SEM_DADO:
            pedacos.append(f"{_POSICAO_TXT[ln.posicao_pares]}"
                           f"{' a' if ln.posicao_pares == EM_LINHA else ' da'} "
                           "mediana dos pares")
        partes.append(f"{ln.rotulo} {ln.texto_atual(v.moeda)} "
                      f"({'; '.join(pedacos)})")
    if not partes:
        partes = [f"{ln.rotulo} {ln.texto_atual(v.moeda)}" for ln in v.com_dado[:2]]
    return (f"{len(v.com_dado)} de {len(v.linhas)} métricas com valor atual: "
            + "; ".join(partes) + ". Comparação, não veredito.")


def texto(v: Valuation, ticker: str) -> str:
    """Bloco da LLM: DADO (do backend) separado de INTERPRETAÇÃO (dela)."""
    linhas = [f"=== VALUATION: {ticker} ==="]
    if not v.linhas:
        linhas.append(v.motivo or NAO_DISPONIVEL)
        return "\n".join(linhas)
    linhas.append("[DADO — fornecido pelo sistema; não invente valores "
                  f"ausentes: onde estiver \"{NAO_DISPONIVEL}\", diga isso]")
    for ln in v.linhas:
        if not ln.aplicavel:
            linhas.append(f"- {ln.rotulo}: {ln.motivo}")
            continue
        extras = [x for x in (ln.referencia and f"ref. {ln.referencia}",
                              ln.fonte and f"fonte: {ln.fonte}") if x]
        linhas.append(f"- {ln.rotulo} atual: {ln.texto_atual(v.moeda)}"
                      + (f" ({'; '.join(extras)})" if extras else ""))
        linhas.append(f"  Histórico: {ln.comparacao_historica}")
        linhas.append(f"  Pares: {ln.comparacao_pares}")
        for f in ln.faixas:
            linhas.append(f"  Faixa — {f.rotulo}: "
                          f"{formatar(f.minimo, ln.unidade, v.moeda)} a "
                          f"{formatar(f.maximo, ln.unidade, v.moeda)}")
    linhas.append("Premissas:")
    linhas += [f"- {p}" for p in v.premissas]
    linhas.append("[INTERPRETAÇÃO — sua tarefa, usando só o bloco DADO]")
    linhas.append(f"- {AVISO}")
    linhas += [f"- {q}" for q in GUIA_INTERPRETACAO.get(v.tipo, ())]
    return "\n".join(linhas)
