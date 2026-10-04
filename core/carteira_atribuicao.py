"""Atribuição Brinson mensal da carteira contra a meta de alocação (Tema 5).

A tela de Investimentos dizia quanto a carteira rendeu, e desde o INV-A4 quanto
ela oscila, mas não dizia DE ONDE veio a diferença para a meta do usuário: de
ter pesos diferentes da meta (alocação) ou de escolher ativos melhores ou piores
que a referência de cada classe (seleção). Este módulo decompõe o resultado de
cada mês fechado em três efeitos por classe da política
(``asset_class_targets``: renda fixa, ações Brasil, FIIs, exterior).

**Brinson-Fachler, por mês.** Com ``w_p`` o peso real da classe no início do
mês, ``w_b`` o peso da meta, ``R_p,i`` o retorno da classe na carteira,
``R_b,i`` o da referência da classe e ``R_b = Σ w_b·R_b,i``:

- alocação  = (w_p − w_b)·(R_b,i − R_b)
- seleção   = w_b·(R_p,i − R_b,i)
- interação = (w_p − w_b)·(R_p,i − R_b,i)

A soma dos três nas quatro classes é exatamente ``R_p − R_b``. Classe sem
posição (``w_p = 0``) recebe ``R_p,i = R_b,i`` por convenção: não há seleção
nem interação a medir onde nada foi escolhido.

**Encadeamento: Carino (logarítmico).** Efeitos mensais não somam ao excesso
do período (retornos compõem). Cada efeito do mês ``t`` é multiplicado por
``k_t / K``, com ``k_t = [ln(1+R_t) − ln(1+B_t)] / (R_t − B_t)`` e ``K`` o
mesmo fator sobre o período inteiro; a soma encadeada fecha o excesso
acumulado ``R − B`` sem resíduo (testado a 1e-12).

**Referências por classe.** Ações Brasil: BOVA11 (o fundo reinveste os
proventos, então é retorno total do Ibovespa). FIIs: o IFIX (retorno total,
série oficial da B3) em ``market.historical_prices`` — só no pregão exato do
fim de mês; o XFIX11 foi descartado porque caiu de 13,87 para 13,22 entre
abril e setembro de 2026 sem provento registrado, e subestimaria o IFIX em
cerca de 1% ao mês. Exterior: SPY convertido pelo USDBRL, sem dividendos —
igual aos ETFs americanos da carteira, cujos dividendos não estão no banco.
Renda fixa: CDI.

**Cobertura honesta.** O peso real usa o valor INTEIRO da classe: os ativos
medidos pela série diária (``core.carteira_risco``) a ``q·P`` do início do mês,
e os sem preço diário (IPCA+, prefixado, CDB) pelo valor de mercado da foto de
posição do fim do mês anterior. O retorno da classe sai só da parte medida —
nunca de um zero no lugar do que falta —, e a cobertura de cada classe é
declarada. A renda fixa tem retorno MODELADO (Tesouro Selic pelo CDI), então a
seleção dela não é medida. Mês em que falta a referência de alguma classe ou o
retorno de uma classe com peso fica marcado incompleto, com o motivo, e não
entra no encadeamento. FIP e o que está fora das quatro classes saem dos pesos
e aparecem na cobertura.
"""
from __future__ import annotations

import logging
import math
from bisect import bisect_right
from datetime import date, timedelta
from typing import Sequence

from core.config import settings
from core.user_context import user_cache_data

logger = logging.getLogger(__name__)

CLASSES = ("renda_fixa", "acoes_br", "fiis", "exterior")
ROTULO = {"renda_fixa": "Renda fixa", "acoes_br": "Ações Brasil",
          "fiis": "Fundos imobiliários", "exterior": "Exterior"}
REFERENCIA = {"renda_fixa": "CDI", "acoes_br": "BOVA11", "fiis": "IFIX",
              "exterior": "SPY em reais"}
EFEITOS = ("alocacao", "selecao", "interacao")
LINKING = "Carino (logarítmico)"

# Pregão de fronteira até 5 dias corridos do fim do mês: feriado e fim de
# semana cabem; série que parou no dia 25 (atraso do job) não vira "mês".
_FOLGA_FIM_MES = 5
# A classe precisa de retorno medido em 90% dos pregões do mês; abaixo disso o
# "retorno do mês" seria de outro período.
_MIN_DIAS_CLASSE = 0.90
# Foto de posição aceita até 7 dias do pregão de início (a do dia 31 serve
# para o pregão de 29 quando o mês acaba no fim de semana).
_FOLGA_FOTO = 7
_FFILL_EXTERIOR = 4
_TOL = 1e-12


# ─────────────────────────────────────────────────────────────────────────────
# Funções puras
# ─────────────────────────────────────────────────────────────────────────────

def _fim_do_mes(ano: int, mes: int) -> date:
    prox = date(ano + (mes == 12), mes % 12 + 1, 1)
    return prox - timedelta(days=1)


def fronteiras_mensais(dias: Sequence[date], hoje: date) -> list[tuple[str, date, date]]:
    """``(AAAA-MM, t0, t1)`` de cada mês FECHADO coberto pela série.

    ``t0`` é o último pregão do mês anterior e ``t1`` o último do mês; os dois
    precisam cair a até ``_FOLGA_FIM_MES`` dias do fim do respectivo mês.
    """
    ultimo_do_mes: dict[tuple[int, int], date] = {}
    for d in dias:
        ultimo_do_mes[(d.year, d.month)] = d
    validos = {k: d for k, d in ultimo_do_mes.items()
               if (_fim_do_mes(*k) - d).days <= _FOLGA_FIM_MES}
    saida = []
    for (ano, mes), t1 in sorted(validos.items()):
        if (ano, mes) >= (hoje.year, hoje.month):
            continue
        ant = (ano - (mes == 1), 12 if mes == 1 else mes - 1)
        t0 = validos.get(ant)
        if t0 is None:
            continue
        saida.append((f"{ano:04d}-{mes:02d}", t0, t1))
    return saida


def brinson_fachler(w_p: dict[str, float], w_b: dict[str, float],
                    r_p: dict[str, float | None], r_b: dict[str, float]) -> dict:
    """Efeitos de um período. Pesos de cada lado precisam somar 1.

    Classe com ``w_p = 0`` e sem retorno próprio recebe ``R_p,i = R_b,i``.
    Classe com peso e sem retorno é erro do chamador (mês incompleto).
    """
    classes = [c for c in CLASSES if c in w_p or c in w_b]
    classes += sorted((set(w_p) | set(w_b)) - set(classes))
    for lado, w in (("carteira", w_p), ("meta", w_b)):
        if abs(sum(w.values()) - 1.0) > 1e-9:
            raise ValueError(f"Pesos da {lado} somam {sum(w.values()):.6f}, não 1.")
    rp: dict[str, float] = {}
    for c in classes:
        r = r_p.get(c)
        if r is None:
            if w_p.get(c, 0.0) > 0:
                raise ValueError(f"Classe {c} com peso e sem retorno.")
            r = r_b[c]
        rp[c] = r
    total_b = sum(w_b.get(c, 0.0) * r_b[c] for c in classes)
    total_p = sum(w_p.get(c, 0.0) * rp[c] for c in classes)
    efeitos = {}
    for c in classes:
        dw = w_p.get(c, 0.0) - w_b.get(c, 0.0)
        efeitos[c] = {
            "alocacao": dw * (r_b[c] - total_b),
            "selecao": w_b.get(c, 0.0) * (rp[c] - r_b[c]),
            "interacao": dw * (rp[c] - r_b[c]),
        }
    return {"R_p": total_p, "R_b": total_b, "excesso": total_p - total_b,
            "efeitos": efeitos, "r_p": rp}


def _k(r: float, b: float) -> float:
    if abs(r - b) < _TOL:
        return 1.0 / (1.0 + r)
    return (math.log1p(r) - math.log1p(b)) / (r - b)


def encadear_carino(periodos: Sequence[dict]) -> dict:
    """Encadeia efeitos de períodos ``brinson_fachler`` pelo método de Carino.

    Devolve os efeitos acumulados por classe, os retornos compostos e o
    resíduo ``Σ efeitos − (R − B)`` — zero a menos de arredondamento.
    """
    if not periodos:
        return {"R_p": None, "R_b": None, "excesso": None, "efeitos": {}, "residuo": None}
    r_acc = math.prod(1.0 + p["R_p"] for p in periodos) - 1.0
    b_acc = math.prod(1.0 + p["R_b"] for p in periodos) - 1.0
    k_total = _k(r_acc, b_acc)
    efeitos: dict[str, dict[str, float]] = {}
    for p in periodos:
        fator = _k(p["R_p"], p["R_b"]) / k_total
        for c, ef in p["efeitos"].items():
            dst = efeitos.setdefault(c, dict.fromkeys(EFEITOS, 0.0))
            for e in EFEITOS:
                dst[e] += ef[e] * fator
    soma = sum(v for ef in efeitos.values() for v in ef.values())
    return {"R_p": r_acc, "R_b": b_acc, "excesso": r_acc - b_acc,
            "efeitos": efeitos, "residuo": soma - (r_acc - b_acc)}


def totais_por_efeito(efeitos: dict[str, dict[str, float]]) -> dict[str, float]:
    return {e: sum(ef[e] for ef in efeitos.values()) for e in EFEITOS}


def retorno_no_intervalo(serie: Sequence[dict], t0: date, t1: date,
                         min_dias: float = _MIN_DIAS_CLASSE) -> tuple[float | None, int, int]:
    """Composto dos retornos diários em ``(t0, t1]``; None se medido em poucos dias.

    Dia sem retorno (nenhum ativo medível) fica fora do produto — não entra
    como zero —, e a proporção de dias medidos decide se o mês vale.
    """
    dias = [s for s in serie if t0 < s["data"] <= t1]
    medidos = [s["retorno"] for s in dias if s["retorno"] is not None]
    if not dias or len(medidos) < min_dias * len(dias):
        return None, len(medidos), len(dias)
    return math.prod(1.0 + r for r in medidos) - 1.0, len(medidos), len(dias)


def retorno_preco(cotacoes: dict[date, float], t0: date, t1: date,
                  ffill_dias: int = 0) -> float | None:
    """``P(t1)/P(t0) − 1`` com o fechamento exato (ou até ``ffill_dias`` antes)."""
    def _p(d: date) -> float | None:
        if d in cotacoes:
            return cotacoes[d]
        if ffill_dias:
            datas = sorted(cotacoes)
            i = bisect_right(datas, d) - 1
            if i >= 0 and (d - datas[i]).days <= ffill_dias:
                return cotacoes[datas[i]]
        return None
    p0, p1 = _p(t0), _p(t1)
    if not p0 or not p1 or p0 <= 0 or p1 <= 0:
        return None
    return p1 / p0 - 1.0


def normalizar(valores: dict[str, float]) -> dict[str, float]:
    total = sum(v for v in valores.values() if v > 0)
    return {c: (max(v, 0.0) / total if total > 0 else 0.0) for c, v in valores.items()}


def mes_atribuicao(mes: str, w_p: dict[str, float], w_b: dict[str, float],
                   r_p: dict[str, float | None], r_b: dict[str, float | None],
                   extras_motivo: Sequence[str] = ()) -> dict:
    """Um mês: Brinson se tudo que pesa tem retorno e referência; senão, o motivo."""
    motivos = list(extras_motivo)
    for c in CLASSES:
        if r_b.get(c) is None:
            motivos.append(f"sem referência de {ROTULO[c]} ({REFERENCIA[c]})")
        elif w_p.get(c, 0.0) > 0 and r_p.get(c) is None:
            motivos.append(f"{ROTULO[c]} sem retorno medido no mês")
    base = {"mes": mes, "w_p": w_p, "w_b": w_b, "r_p": r_p, "r_b": r_b}
    if motivos:
        return {**base, "completo": False, "motivos": motivos}
    bf = brinson_fachler(w_p, w_b, r_p, {c: float(r_b[c]) for c in CLASSES})
    return {**base, **bf, "completo": True, "motivos": []}


# ─────────────────────────────────────────────────────────────────────────────
# Contexto da LLM
# ─────────────────────────────────────────────────────────────────────────────

def mes_br(mes: str) -> str:
    """``2026-07`` → ``07/2026``."""
    return f"{mes[5:]}/{mes[:4]}"


def _pp(v: float | None, casas: int = 2) -> str:
    """Efeito em pontos percentuais com sinal."""
    return "ausente" if v is None else f"{v * 100:+.{casas}f} p.p."


def _pct(v: float | None, casas: int = 1) -> str:
    return "ausente" if v is None else f"{v * 100:.{casas}f}%"


def _desde(atr: dict) -> str:
    d = atr.get("meta_desde")
    return f", definida em {d:%d/%m/%Y}" if hasattr(d, "strftime") else ""


def resumo_atribuicao(atr: dict) -> str | None:
    """Uma frase com o excesso acumulado, a divisão nos três efeitos e o maior item."""
    acc = atr.get("acumulado") or {}
    if acc.get("excesso") is None:
        return None
    tot = totais_por_efeito(acc["efeitos"])
    nome = {"alocacao": "alocação", "selecao": "seleção", "interacao": "interação"}
    maior = max(((c, e, v) for c, ef in acc["efeitos"].items() for e, v in ef.items()),
                key=lambda x: abs(x[2]))
    return (f"Contra a meta, a carteira ficou {_pp(acc['excesso'])} em "
            f"{', '.join(mes_br(m) for m in atr.get('meses_encadeados') or [])}: "
            f"alocação {_pp(tot['alocacao'])}, seleção {_pp(tot['selecao'])} e "
            f"interação {_pp(tot['interacao'])} (maior item: {nome[maior[1]]} em "
            f"{ROTULO[maior[0]]}, {_pp(maior[2])}).")


def bloco_atribuicao_para_prompt(atr: dict | None) -> str:
    """Bloco "ATRIBUIÇÃO CONTRA A META" para o chat da Visão Geral."""
    if not atr:
        return ""
    if not atr.get("disponivel"):
        return ("ATRIBUIÇÃO CONTRA A META (Brinson): indisponível — "
                + str(atr.get("motivo") or "sem motivo informado") + ".")
    acc = atr.get("acumulado") or {}
    linhas = [
        f"ATRIBUIÇÃO CONTRA A META (Brinson-Fachler mensal, encadeado por {LINKING}; "
        f"meta da política versão {atr.get('meta_versao')}{_desde(atr)}, aplicada também aos "
        "meses anteriores a ela):",
        "- Meta: " + ", ".join(f"{ROTULO[c]} {_pct(atr['meta'].get(c), 0)}" for c in CLASSES)
        + ". Referências: " + ", ".join(f"{ROTULO[c]} = {REFERENCIA[c]}" for c in CLASSES) + ".",
    ]
    resumo = resumo_atribuicao(atr)
    if resumo:
        linhas.append(f"- Resumo: {resumo}")
        linhas.append(f"- Acumulado: carteira {_pct(acc['R_p'], 2)} | meta {_pct(acc['R_b'], 2)}")
        for c in CLASSES:
            ef = acc["efeitos"].get(c)
            if ef:
                linhas.append(f"  - {ROTULO[c]}: alocação {_pp(ef['alocacao'])}, seleção "
                              f"{_pp(ef['selecao'])}, interação {_pp(ef['interacao'])}")
    else:
        linhas.append("- Nenhum mês completo para encadear.")
    for m in atr.get("meses") or []:
        if m["completo"]:
            linhas.append(
                f"- {mes_br(m['mes'])}: excesso {_pp(m['excesso'])} (carteira {_pct(m['R_p'], 2)}, "
                f"meta {_pct(m['R_b'], 2)}); pesos reais "
                + ", ".join(f"{ROTULO[c]} {_pct(m['w_p'].get(c), 0)}" for c in CLASSES))
        else:
            linhas.append(f"- {mes_br(m['mes'])}: incompleto — " + "; ".join(m["motivos"]))
    linhas.append(
        "- Leitura: renda fixa com retorno modelado pelo CDI (seleção não medida); "
        f"{_pct(atr.get('pct_fora_politica'))} do patrimônio fora das quatro classes "
        "(FIP e outros) não entra. Exterior sem dividendos dos dois lados.")
    return "\n".join(linhas)


REGRA_ATRIBUICAO = (
    "- Pergunta sobre por que a carteira rendeu mais ou menos, se a culpa é dos "
    "pesos ou dos ativos, ou como ela foi contra a meta: COMECE a resposta com a "
    "frase do Resumo do bloco ATRIBUIÇÃO CONTRA A META (excesso, os três efeitos "
    "e o maior item, com os meses); depois detalhe por classe. Mês incompleto se "
    "diz incompleto, com o motivo — não estime o que o bloco não mediu.")


# ─────────────────────────────────────────────────────────────────────────────
# Carga (I/O)
# ─────────────────────────────────────────────────────────────────────────────

_SQL_IFIX = """
    SELECT date AS data, close AS fechamento
    FROM market.historical_prices
    WHERE ticker = 'IFIX' AND date >= :ini AND close IS NOT NULL
"""

_SQL_FOTOS_VALOR = """
    SELECT s.report_date AS data, UPPER(TRIM(a.ticker)) AS ticker,
           COALESCE(s.institution, '') AS instituicao,
           COALESCE(s.asset_type, '') AS tipo,
           COALESCE(s.currency, a.currency, 'BRL') AS moeda,
           SUM(s.market_value) AS valor
    FROM portfolio_position_snapshots s
    JOIN assets a ON a.id = s.asset_id
    WHERE s.user_id = :uid
      AND s.report_date BETWEEN :ini AND :fim
    GROUP BY 1, 2, 3, 4, 5
"""

# Ativo vendido antes de hoje só existe na foto: a classe sai do tipo dela.
_CLASSE_DO_TIPO = {"stock": "acoes_br", "etf": "acoes_br", "fii": "fiis",
                   "fixed_income": "renda_fixa", "treasury": "renda_fixa"}


@user_cache_data(ttl=21600)
def get_atribuicao_carteira() -> dict:
    """Atribuição Brinson mensal contra a meta, com cobertura declarada."""
    if settings.MOCK_MODE:
        return {"data_source": "mock", "disponivel": False,
                "motivo": "Atribuição não é simulada em modo mock"}
    try:
        return _atribuicao_real()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[carteira_atribuicao] indisponível (%s).", type(exc).__name__)
        return {"data_source": "error", "disponivel": False,
                "motivo": "Não foi possível montar a atribuição da carteira"}


def _meta_politica(engine, owner: str) -> tuple[dict[str, float] | None, dict]:
    from core.estrategia import politica as pol
    from core.estrategia import repositorio as repo

    estado = repo.carregar(engine=engine, owner_id=owner)
    reg = estado.vigente
    if reg is None:
        return None, {}
    alvos = dict(pol.valores(reg.politica).get("asset_class_targets") or {})
    meta = {c: float(alvos.get(c) or 0.0) for c in CLASSES}
    if sum(meta.values()) <= 0:
        return None, {}
    return normalizar(meta), {"versao": reg.version, "status": reg.status,
                              "desde": getattr(reg, "completed_at", None)}


def _foto_mais_proxima(datas: Sequence[date], t0: date) -> date | None:
    perto = [d for d in datas if abs((d - t0).days) <= _FOLGA_FOTO]
    return min(perto, key=lambda d: (abs((d - t0).days), d > t0)) if perto else None


def _atribuicao_real() -> dict:
    from core.carteira_risco import (
        FONTE_MODELADA,
        _ler,
        alinhar_precos,
        get_insumos_carteira,
        retornos_diarios,
    )
    from core.database import get_engine
    from core.inteligencia_ativos.calculos import classe_politica
    from core.investimentos import _base_ticker
    from core.rentabilidade import fator_cdi

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Engine indisponível.")
    owner = settings.OWNER_USER_ID
    if not owner:
        raise RuntimeError("OWNER_USER_ID não configurado.")

    meta, meta_info = _meta_politica(engine, owner)
    if meta is None:
        return {"data_source": "real", "disponivel": False,
                "motivo": "Sem meta de alocação vigente na política (Inteligência dos Ativos)"}

    ins = get_insumos_carteira()
    if not ins.get("disponivel"):
        return {"data_source": "real", "disponivel": False, "motivo": ins.get("motivo")}
    dias, hoje, posicao = ins["dias"], ins["hoje"], ins["posicao"]
    quantidades, precos, proventos = ins["quantidades"], ins["precos"], ins["proventos"]
    fonte_de, cotacoes, cdi = ins["fonte_de"], ins["cotacoes"], ins["cdi"]
    meses = fronteiras_mensais(dias, hoje)
    if not meses:
        return {"data_source": "real", "disponivel": False,
                "motivo": "Nenhum mês fechado inteiro na série diária"}

    # Classe da política de cada ativo de hoje (a da tela de Investimentos).
    classe_de = {tk: classe_politica(p) for tk, p in posicao.items()}
    medidos_por_classe: dict[str, list[str]] = {c: [] for c in CLASSES}
    for tk in quantidades:
        if classe_de.get(tk) in medidos_por_classe:
            medidos_por_classe[classe_de[tk]].append(tk)

    # Série diária por classe: a mesma função do risco, só com os ativos dela.
    serie_classe = {
        c: retornos_diarios(dias, {tk: quantidades[tk] for tk in tks},
                            {tk: precos[tk] for tk in tks},
                            {tk: proventos[tk] for tk in tks if tk in proventos})
        for c, tks in medidos_por_classe.items() if tks
    }
    fonte_classe = {}
    for c, tks in medidos_por_classe.items():
        fontes = {fonte_de.get(tk) for tk in tks}
        fonte_classe[c] = (None if not tks else "modelada" if fontes == {FONTE_MODELADA}
                           else "observada" if FONTE_MODELADA not in fontes else "mista")

    # Valor do que não tem preço diário: a foto de posição do início do mês.
    fotos = _ler(engine, _SQL_FOTOS_VALOR,
                 {"uid": owner, "ini": meses[0][1] - timedelta(days=_FOLGA_FOTO),
                  "fim": meses[-1][1] + timedelta(days=_FOLGA_FOTO)})
    foto: dict[date, dict[str, dict]] = {}
    for r in fotos:
        tk = _base_ticker(r.ticker)
        v = float(r.valor or 0.0)
        cur = foto.setdefault(r.data, {}).get(tk)
        # A foto de 31/08 traz o Tesouro duas vezes (XP e Tesouro Direto): máximo.
        if cur is None or v > cur["valor"]:
            foto[r.data][tk] = {"valor": v, "tipo": str(r.tipo), "moeda": str(r.moeda).upper()}

    ifix = {r.data: float(r.fechamento) for r in _ler(engine, _SQL_IFIX, {"ini": meses[0][1]})
            if r.fechamento}
    spy_brl = {}
    fx = alinhar_precos(dias, cotacoes.get("USDBRL", {}), _FFILL_EXTERIOR)
    spy = alinhar_precos(dias, cotacoes.get("SPY", {}), _FFILL_EXTERIOR)
    for d, a, b in zip(dias, spy, fx):
        if a is not None and b is not None:
            spy_brl[d] = a * b
    bova = cotacoes.get("BOVA11", {})
    idx = {d: i for i, d in enumerate(dias)}

    saida_meses = []
    for mes, t0, t1 in meses:
        i0 = idx[t0]
        valor = dict.fromkeys(CLASSES, 0.0)
        medido = dict.fromkeys(CLASSES, 0.0)
        fora = 0.0
        notas: list[str] = []
        for tk, qs in quantidades.items():
            q, p = qs[i0], precos[tk][i0]
            if not q:
                continue
            c = classe_de.get(tk)
            if p is None or p <= 0:
                notas.append(f"{tk} sem preço no início")
                continue
            if c in valor:
                valor[c] += q * p
                medido[c] += q * p
            else:
                fora += q * p
        d_foto = _foto_mais_proxima(sorted(foto), t0)
        if d_foto is not None:
            for tk, f in foto[d_foto].items():
                if tk in quantidades:
                    continue
                c = (classe_de[tk] if tk in classe_de
                     else "exterior" if f["moeda"] not in ("", "BRL")
                     else _CLASSE_DO_TIPO.get(f["tipo"]))
                v = f["valor"] * (fx[i0] or 0.0) if f["moeda"] not in ("", "BRL") else f["valor"]
                if c in valor:
                    valor[c] += v
                else:
                    fora += v
        else:
            # Sem foto perto do mês: o valor de hoje do que não tem preço, avisado.
            notas.append("sem foto de posição no início do mês: valor de hoje para o que não tem preço")
            for tk, p in posicao.items():
                if tk in quantidades:
                    continue
                c = classe_de.get(tk)
                if c in valor:
                    valor[c] += p["valor"]
                else:
                    fora += p["valor"]
        total_classes = sum(valor.values())
        w_p = normalizar(valor)
        r_p: dict[str, float | None] = {}
        dias_medidos = {}
        for c in CLASSES:
            if medido[c] <= 0 or c not in serie_classe:
                r_p[c] = None
                continue
            r, n_med, n = retorno_no_intervalo(serie_classe[c], t0, t1)
            r_p[c] = r
            dias_medidos[c] = (n_med, n)
        r_b = {
            "renda_fixa": (fator_cdi(cdi, t0, t1) - 1.0) if ins["cdi_ok"] else None,
            "acoes_br": retorno_preco(bova, t0, t1),
            "fiis": retorno_preco(ifix, t0, t1),
            "exterior": retorno_preco(spy_brl, t0, t1),
        }
        m = mes_atribuicao(mes, w_p, meta, r_p, r_b,
                           () if total_classes > 0 else ("nenhum valor nas classes da política",))
        m.update({
            "t0": t0, "t1": t1, "foto": d_foto, "notas": notas,
            "valor_classes": total_classes, "valor_fora": fora,
            "cobertura": {c: (medido[c] / valor[c] if valor[c] > 0 else None) for c in CLASSES},
            "dias_medidos": dias_medidos,
        })
        saida_meses.append(m)

    completos = [m for m in saida_meses if m["completo"]]
    acumulado = encadear_carino(completos)
    total = ins["total"]
    fora_hoje = sum(p["valor"] for tk, p in posicao.items() if classe_de.get(tk) is None)
    return {
        "data_source": "real",
        "disponivel": True,
        "motivo": None,
        "linking": LINKING,
        "meta": meta,
        "meta_versao": meta_info.get("versao"),
        "meta_status": meta_info.get("status"),
        "meta_desde": meta_info.get("desde"),
        "referencias": dict(REFERENCIA),
        "fonte_classe": fonte_classe,
        "meses": saida_meses,
        "meses_encadeados": [m["mes"] for m in completos],
        "contiguo": _contiguo([m["mes"] for m in completos]),
        "acumulado": acumulado,
        "pct_fora_politica": fora_hoje / total if total else None,
        "fora_politica": sorted({str(p.get("classe")) for tk, p in posicao.items()
                                 if classe_de.get(tk) is None}),
    }


def _contiguo(meses: Sequence[str]) -> bool:
    nums = [int(m[:4]) * 12 + int(m[5:]) for m in meses]
    return all(b - a == 1 for a, b in zip(nums, nums[1:]))
