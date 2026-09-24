"""
core/rentabilidade.py
Rentabilidade ponderada pelo dinheiro (TIR) e comparação com o CDI.

Por que TIR e não TWR
---------------------
A TWR (retorno ponderado pelo tempo, RI-002 do vault) exige o valor da
carteira em cada data de fluxo. O app não tem isso de forma confiável:
  - as fotos da XP cobrem uma corretora só e mudam de universo ao longo do
    tempo (em ago/2026 a foto só tem renda fixa);
  - a posição da B3 (todas as corretoras) só existe a partir de set/2026;
  - reconstruir a posição diária por preço exige casar quantidade crua do
    extrato com preço ajustado por desdobramento, o que erra em silêncio
    em volta de cada evento corporativo.

A TIR só precisa dos fluxos e do valor final — exatamente o que o extrato da
B3 e a posição atual dão. Ela responde "como foi o MEU dinheiro", que é a
pergunta de quem aporta. Para comparar com o CDI sem o viés de janela, os
MESMOS fluxos são aplicados no CDI (Public Market Equivalent): quanto haveria
hoje se cada compra tivesse ido para o CDI e cada venda/provento tivesse
saído dele.

Convenção de sinal (ótica do investidor)
  compra   → negativo (dinheiro sai do bolso)
  venda    → positivo
  provento → positivo
  valor final da posição → positivo, na data final

Tudo aqui é puro: sem banco, sem rede. O carregamento fica em
core/investimentos.py (fluxos) e em ``carregar_cdi_diario`` (BCB).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

Fluxo = tuple[date, float]


def _anos(d0: date, d1: date) -> float:
    return (d1 - d0).days / 365.0


def _vpl(taxa: float, fluxos: list[Fluxo], d0: date) -> float:
    return sum(v / (1.0 + taxa) ** _anos(d0, d) for d, v in fluxos)


def xirr(fluxos: list[Fluxo]) -> float | None:
    """TIR anualizada (base 365 dias corridos) de fluxos com datas irregulares.

    Devolve ``None`` quando não há troca de sinal (a TIR não existe) ou quando
    a raiz não fica entre -99,99% e +10.000% a.a. Usa bisseção: é mais lenta
    que Newton, mas não diverge nem salta para uma raiz espúria quando a
    carteira tem vendas grandes no meio do caminho.
    """
    fl = [(d, float(v)) for d, v in fluxos if v]
    if not fl:
        return None
    if not (any(v < 0 for _, v in fl) and any(v > 0 for _, v in fl)):
        return None
    fl.sort(key=lambda x: x[0])
    d0 = fl[0][0]
    lo, hi = -0.9999, 100.0
    f_lo, f_hi = _vpl(lo, fl, d0), _vpl(hi, fl, d0)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2.0
        f_mid = _vpl(mid, fl, d0)
        if abs(f_mid) < 1e-9 or (hi - lo) < 1e-10:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2.0


def fator_cdi(cdi_diario: dict[date, float], inicio: date, fim: date) -> float:
    """Fator acumulado do CDI entre ``inicio`` (inclusive) e ``fim`` (exclusive).

    ``cdi_diario`` mapeia o dia útil para a taxa do dia em % (série 12 do BCB:
    a taxa de ``d`` remunera de ``d`` até o próximo dia útil). Dinheiro que
    entra em ``inicio`` rende o CDI de ``inicio``; o de ``fim`` já não conta.
    """
    if fim <= inicio:
        return 1.0
    f = 1.0
    for d, taxa in cdi_diario.items():
        if inicio <= d < fim:
            f *= 1.0 + taxa / 100.0
    return f


def _indice_cdi(cdi_diario: dict[date, float]) -> tuple[list[date], list[float]]:
    """Índice acumulado: ``idx[i]`` = fator de dias[0] até dias[i] (exclusive)."""
    dias = sorted(cdi_diario)
    idx = []
    acc = 1.0
    for d in dias:
        idx.append(acc)
        acc *= 1.0 + cdi_diario[d] / 100.0
    return dias, idx + [acc]


def comparar_com_cdi(
    fluxos: list[Fluxo],
    valor_final: float,
    data_final: date,
    cdi_diario: dict[date, float],
) -> dict:
    """TIR da carteira, TIR do CDI com os mesmos fluxos e PME de Kaplan-Schoar.

    ``fluxos`` NÃO inclui o valor final — ele entra separado em ``valor_final``.

    Devolve:
      tir_carteira   TIR anual da carteira (fração; None se não existir)
      tir_cdi        TIR anual de uma conta CDI que recebeu os mesmos fluxos
      valor_cdi      saldo que essa conta CDI teria em ``data_final``
      diferenca      valor_final − valor_cdi (R$ ganhos/perdidos contra o CDI)
      pme            (Σ saídas corrigidas + valor final) / Σ entradas corrigidas
                     pelo CDI. >1 = bateu o CDI; não depende de saldo positivo
      cobertura_cdi  True se a série do CDI cobre do primeiro fluxo à data final
    """
    fl = sorted(((d, float(v)) for d, v in fluxos if v), key=lambda x: x[0])
    out = {
        "tir_carteira": None,
        "tir_cdi": None,
        "valor_cdi": None,
        "diferenca": None,
        "pme": None,
        "cobertura_cdi": False,
    }
    if not fl:
        return out
    out["tir_carteira"] = xirr(fl + [(data_final, float(valor_final))])

    if not cdi_diario:
        return out
    dias, idx = _indice_cdi(cdi_diario)
    # A série do BCB só tem dias úteis; um fluxo de sábado entra no próximo
    # útil. Cobertura: o primeiro dia da série não pode ser posterior ao
    # primeiro dia útil depois do primeiro fluxo (folga de 7 dias corridos),
    # e o último não pode ficar mais de 7 dias antes da data final.
    primeiro, ultimo = dias[0], dias[-1]
    out["cobertura_cdi"] = (
        primeiro <= fl[0][0] + timedelta(days=7)
        and ultimo >= data_final - timedelta(days=7)
    )
    if not out["cobertura_cdi"]:
        return out

    import bisect

    def fator_ate_fim(d: date) -> float:
        i = bisect.bisect_left(dias, d)
        j = bisect.bisect_left(dias, data_final)
        return idx[j] / idx[i] if j > i else 1.0

    saldo = 0.0
    fv_aportes = 0.0
    fv_retiradas = 0.0
    for d, v in fl:
        f = fator_ate_fim(d)
        saldo += -v * f
        if v < 0:
            fv_aportes += -v * f
        else:
            fv_retiradas += v * f
    out["valor_cdi"] = saldo
    out["diferenca"] = float(valor_final) - saldo
    out["pme"] = (fv_retiradas + float(valor_final)) / fv_aportes if fv_aportes > 0 else None
    out["tir_cdi"] = xirr(fl + [(data_final, saldo)]) if saldo > 0 else None
    return out


def cdi_anualizado(cdi_diario: dict[date, float], inicio: date, fim: date) -> float | None:
    """CDI acumulado no período, anualizado em base 365 (a mesma da TIR)."""
    if fim <= inicio or not cdi_diario:
        return None
    f = fator_cdi(cdi_diario, inicio, fim)
    anos = _anos(inicio, fim)
    return f ** (1.0 / anos) - 1.0 if anos > 0 else None


# ─────────────────────────────────────────────────────────────────────────────
# Universo conciliado
# ─────────────────────────────────────────────────────────────────────────────

MOTIVO_SEM_COMPRA = "posição sem nenhuma compra no extrato"
MOTIVO_QTD_DIVERGE = (
    "quantidade do extrato não fecha com a posição "
    "(subscrição, bonificação, desdobramento ou compra fora do extrato)"
)
MOTIVO_VENDA_SEM_COMPRA = "vendeu mais do que comprou no extrato (posição anterior ao extrato)"
MOTIVO_COMPRA_SEM_POSICAO = "comprou e não vendeu, mas não está na posição (transferência ou troca de código)"


def conciliar_universo(
    transacoes: list[dict],
    proventos: list[dict],
    posicoes: dict[str, dict],
    tolerancia: float = 0.5,
) -> dict:
    """Separa os ativos cujo extrato explica a posição dos que não explica.

    Um ativo só entra na TIR se Σ compras − Σ vendas do extrato bater com a
    quantidade em carteira hoje (ou der zero para quem já saiu). Qualquer
    diferença significa dinheiro que entrou ou saiu fora do extrato — uma
    subscrição paga, uma posição de antes do primeiro extrato — e a TIR desse
    ativo sairia inflada ou achatada sem aviso. Excluir e declarar é melhor
    do que medir errado.

    transacoes: {data, ticker, tipo ('buy'|'sell'), quantidade, preco, taxas}
    proventos:  {data, ticker, valor}
    posicoes:   {ticker: {quantidade, valor_mercado}}
    """
    liquido: dict[str, float] = {}
    comprou: set[str] = set()
    for t in transacoes:
        q = float(t["quantidade"] or 0)
        sinal = 1.0 if t["tipo"] == "buy" else -1.0
        liquido[t["ticker"]] = liquido.get(t["ticker"], 0.0) + sinal * q
        if t["tipo"] == "buy":
            comprou.add(t["ticker"])

    incluidos: set[str] = set()
    excluidos: list[dict] = []
    for tk in sorted(set(liquido) | set(posicoes)):
        q_pos = float((posicoes.get(tk) or {}).get("quantidade") or 0)
        q_liq = liquido.get(tk)
        em_carteira = tk in posicoes and q_pos > tolerancia
        if q_liq is None or tk not in comprou:
            if em_carteira:
                excluidos.append({"ticker": tk, "motivo": MOTIVO_SEM_COMPRA})
            elif q_liq is not None and q_liq < -tolerancia:
                excluidos.append({"ticker": tk, "motivo": MOTIVO_VENDA_SEM_COMPRA})
            continue
        if em_carteira:
            if abs(q_liq - q_pos) <= tolerancia:
                incluidos.add(tk)
            else:
                excluidos.append({"ticker": tk, "motivo": MOTIVO_QTD_DIVERGE})
        elif abs(q_liq) <= tolerancia:
            incluidos.add(tk)
        elif q_liq < 0:
            excluidos.append({"ticker": tk, "motivo": MOTIVO_VENDA_SEM_COMPRA})
        else:
            excluidos.append({"ticker": tk, "motivo": MOTIVO_COMPRA_SEM_POSICAO})

    fluxos: list[Fluxo] = []
    for t in transacoes:
        if t["ticker"] not in incluidos:
            continue
        bruto = float(t["quantidade"] or 0) * float(t["preco"] or 0)
        taxas = float(t.get("taxas") or 0)
        if t["tipo"] == "buy":
            fluxos.append((t["data"], -(bruto + taxas)))
        else:
            fluxos.append((t["data"], bruto - taxas))
    for p in proventos:
        if p["ticker"] in incluidos and p["valor"]:
            fluxos.append((p["data"], float(p["valor"])))

    valor_total = sum(float(p.get("valor_mercado") or 0) for p in posicoes.values())
    valor_final = sum(
        float(posicoes[tk].get("valor_mercado") or 0) for tk in incluidos if tk in posicoes
    )
    return {
        "fluxos": sorted(fluxos, key=lambda x: x[0]),
        "valor_final": valor_final,
        "valor_total": valor_total,
        "incluidos": sorted(incluidos),
        "excluidos": excluidos,
        "n_em_carteira": sum(1 for p in posicoes.values() if float(p.get("quantidade") or 0) > tolerancia),
        "n_em_carteira_incluidos": sum(1 for tk in incluidos if tk in posicoes),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Série do CDI (BCB SGS 12 — % ao dia útil)
# ─────────────────────────────────────────────────────────────────────────────

_URL_SGS12 = (
    "https://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados"
    "?formato=json&dataInicial={ini}&dataFinal={fim}"
)


def parse_sgs(payload: list[dict]) -> dict[date, float]:
    """Converte a resposta JSON do SGS em {data: valor}. Ignora linha ruim."""
    out: dict[date, float] = {}
    for row in payload or []:
        try:
            d = datetime.strptime(row["data"], "%d/%m/%Y").date()
            out[d] = float(str(row["valor"]).replace(",", "."))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def carregar_cdi_diario(inicio: date, fim: date, timeout: float = 15.0) -> dict[date, float]:
    """Baixa o CDI diário do BCB em janelas de até 9 anos (o SGS recusa >10).

    Falha de rede devolve o que já veio (possivelmente vazio); quem chama
    decide o que mostrar — ``comparar_com_cdi`` marca ``cobertura_cdi=False``.
    """
    import requests

    out: dict[date, float] = {}
    ini = inicio
    while ini <= fim:
        fim_janela = min(fim, date(ini.year + 9, ini.month, 1) - timedelta(days=1))
        url = _URL_SGS12.format(ini=ini.strftime("%d/%m/%Y"), fim=fim_janela.strftime("%d/%m/%Y"))
        try:
            r = requests.get(url, timeout=timeout)
            if not r.ok:
                logger.warning("[rentabilidade] SGS 12 respondeu HTTP %s.", r.status_code)
                break
            out.update(parse_sgs(r.json()))
        except Exception as exc:
            logger.warning("[rentabilidade] CDI indisponível (%s).", type(exc).__name__)
            break
        ini = fim_janela + timedelta(days=1)
    return out
