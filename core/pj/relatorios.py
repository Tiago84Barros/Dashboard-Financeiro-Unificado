"""Relatórios da empresa a partir dos lançamentos: puros, sem banco.

Um lançamento é um dict com ``data`` (date), ``valor`` (com sinal: entrada
positiva), ``categoria`` (código de :mod:`core.pj.plano_contas` ou ``None``) e
``status`` (``pago`` ou ``previsto``). Só o pago entra em DRE e caixa.

Lançamento sem categoria fica FORA da DRE e é contado à parte: somá-lo em
"outras despesas" daria um resultado com cara de certo e composição inventada.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from core.pj.plano_contas import CODIGOS_FOLHA, GRUPOS_DRE, POR_CODIGO

_GRUPOS_RESULTADO = ("receita_bruta", "deducoes", "custos", "despesas_operacionais",
                     "pessoal", "financeiro")


def _pagos(lancs, periodos: set[tuple[int, int]] | None = None):
    for lanc in lancs:
        if lanc.get("status") != "pago" or not isinstance(lanc.get("data"), date):
            continue
        if periodos is not None and (lanc["data"].year, lanc["data"].month) not in periodos:
            continue
        yield lanc


def _grupo(lanc) -> str | None:
    cat = POR_CODIGO.get(lanc.get("categoria") or "")
    return cat.grupo if cat else None


def dre(lancs: list[dict], periodos: list[tuple[int, int]]) -> dict:
    """DRE gerencial (caixa) somando os ``periodos`` (ano, mês).

    Devolve linhas por grupo e por categoria, subtotais e o que ficou fora.
    """
    alvo = set(periodos)
    por_grupo: dict[str, float] = defaultdict(float)
    por_categoria: dict[str, float] = defaultdict(float)
    sem_cat_n, sem_cat_total = 0, 0.0
    for lanc in _pagos(lancs, alvo):
        grupo = _grupo(lanc)
        if grupo is None:
            sem_cat_n += 1
            sem_cat_total += float(lanc["valor"])
            continue
        por_grupo[grupo] += float(lanc["valor"])
        por_categoria[lanc["categoria"]] += float(lanc["valor"])

    g = {k: round(por_grupo.get(k, 0.0), 2) for k, _ in GRUPOS_DRE}
    receita_liquida = round(g["receita_bruta"] + g["deducoes"], 2)
    lucro_bruto = round(receita_liquida + g["custos"], 2)
    operacional = round(lucro_bruto + g["despesas_operacionais"] + g["pessoal"], 2)
    resultado = round(operacional + g["financeiro"], 2)
    return {
        "grupos": g,
        "categorias": {k: round(v, 2) for k, v in por_categoria.items()},
        "receita_liquida": receita_liquida,
        "lucro_bruto": lucro_bruto,
        "resultado_operacional": operacional,
        "resultado": resultado,
        "margem": round(resultado / g["receita_bruta"], 4) if g["receita_bruta"] > 0 else None,
        "sem_categoria_n": sem_cat_n,
        "sem_categoria_total": round(sem_cat_total, 2),
    }


def serie_mensal(lancs: list[dict], ate: date, meses: int = 12) -> list[dict]:
    """Últimos ``meses`` meses até ``ate`` (inclusive), do mais antigo ao atual."""
    periodos = []
    ano, mes = ate.year, ate.month
    for _ in range(meses):
        periodos.append((ano, mes))
        ano, mes = (ano, mes - 1) if mes > 1 else (ano - 1, 12)
    periodos.reverse()
    saida = []
    for a, m in periodos:
        d = dre(lancs, [(a, m)])
        entradas = sum(float(x["valor"]) for x in _pagos(lancs, {(a, m)}) if float(x["valor"]) > 0)
        saidas = sum(-float(x["valor"]) for x in _pagos(lancs, {(a, m)}) if float(x["valor"]) < 0)
        saida.append({
            "ano": a, "mes": m,
            "receita": d["grupos"]["receita_bruta"],
            "resultado": d["resultado"],
            "entradas": round(entradas, 2),
            "saidas": round(saidas, 2),
            "folha": round(-sum(float(x["valor"]) for x in _pagos(lancs, {(a, m)})
                                if x.get("categoria") in CODIGOS_FOLHA), 2),
            "das_pago": round(-sum(float(x["valor"]) for x in _pagos(lancs, {(a, m)})
                                   if x.get("categoria") == "das"), 2),
            "sem_categoria": d["sem_categoria_n"],
        })
    return saida


def meses_com_movimento(lancs: list[dict]) -> list[tuple[int, int]]:
    return sorted({(x["data"].year, x["data"].month) for x in _pagos(lancs)})


def saldo_ate(saldo_inicial: float, data_inicial: date | None, lancs: list[dict],
              data: date) -> float:
    """Saldo da conta em ``data``: saldo inicial + pagos depois da data inicial."""
    total = float(saldo_inicial or 0.0)
    for x in _pagos(lancs):
        if (data_inicial is None or x["data"] > data_inicial) and x["data"] <= data:
            total += float(x["valor"])
    return round(total, 2)


def saldo_inicial_pelo_extrato(saldo_final: float, movimentos: list[dict],
                               data_saldo: date) -> tuple[float, date] | None:
    """Saldo de abertura deduzido do primeiro extrato: saldo final menos os
    movimentos até a data do saldo, na véspera do primeiro movimento."""
    ate = [m for m in movimentos if m["data"] <= data_saldo]
    if not ate:
        return None
    primeiro = min(m["data"] for m in ate)
    from datetime import timedelta
    return round(float(saldo_final) - sum(float(m["valor"]) for m in ate), 2), primeiro - timedelta(days=1)


def meses_desde_o_inicio(serie: list[dict], lancs: list[dict]) -> list[dict]:
    """Os meses de ``serie`` a partir do início da atividade (primeiro
    lançamento pago), para a RBT12 do Simples.

    Não corta na primeira RECEITA da janela: mês sem faturamento de empresa
    que já existia é zero de verdade, e descartá-lo faria a RBT12 anualizar
    só os meses bons (achado do Codex no PR #680).
    """
    datas = [x["data"] for x in _pagos(lancs)]
    if not datas:
        return []
    inicio = min(datas)
    return [s for s in serie if (s["ano"], s["mes"]) >= (inicio.year, inicio.month)]
