"""Desempenho diário da Carteira 20 desde a execução, contra os comparadores.

Puro: sem banco e sem rede. ``scripts/carteira20_desempenho.py`` junta os
insumos (retorno total diário das ações, fechamento do BOVA11 e CDI) e grava o
artefato que a aba lê; aqui só se faz a conta.

É a curva "teórica" do protocolo (seq 0, ``medicao.curvas.teorica``): R$ 1.000
no fechamento do pregão de execução, pesos iguais, sem aporte, sem rebalancear
até a próxima revisão. Cada ponto é o valor **líquido de liquidação** -- quanto
sobraria vendendo tudo naquele fechamento:

- ações (Carteira 20 e pesos iguais do universo): 0,30% na compra e 0,30% na
  venda; venda de ações até R$ 20 mil no mês é isenta de IR;
- BOVA11: 0,30% em cada lado e 15% de IR sobre o ganho (ETF não tem isenção);
- CDI: sem custo de corretagem, 15% de IR sobre o rendimento.

Não é a medição mensal do protocolo (aportes, preço médio, compensação de
prejuízo, IPCA): essa tem publicador próprio. Esta curva só mostra o caminho.
"""
from __future__ import annotations

import hashlib
from datetime import date

import pandas as pd

from core import carteira20 as c20

CAPITAL = 1000.0
CUSTO_LADO = 0.003
ALIQUOTA_IR = 0.15
ISENCAO_MENSAL_ACOES = 20_000.0
# ~12 meses de pregões: até aqui o gráfico usa um ponto por pregão; depois, o
# último pregão de cada mês (e o mais recente).
PONTOS_DIARIOS_ATE = 252

SERIES = ("carteira20", "pesos_iguais", "bova11", "cdi")


def _liquido_acoes(bruto: pd.Series) -> pd.Series:
    """Valor de venda das ações menos o IR, que só incide acima da isenção mensal."""
    venda = bruto * (1 - CUSTO_LADO)
    ir = ((venda - CAPITAL).clip(lower=0) * ALIQUOTA_IR).where(venda > ISENCAO_MENSAL_ACOES, 0.0)
    return venda - ir


def _carteira_de_acoes(fatores: pd.DataFrame, ids: list[str]) -> tuple[pd.Series, pd.Series]:
    """Valor bruto da carteira e de cada ação: compra a pesos iguais e segura."""
    por_acao = CAPITAL * (1 - CUSTO_LADO) / len(ids)
    posicoes = fatores.reindex(columns=ids).fillna(1.0) * por_acao
    return posicoes.sum(axis=1), posicoes.iloc[-1]


def _fator_cdi(cdi: dict[date, float], datas: list[date]) -> tuple[list[float], int]:
    """Fator acumulado do CDI em cada data e quantos pregões a série cobre.

    A taxa de ``d`` (em % ao dia útil, série 12 do BCB) remunera de ``d`` até o
    próximo dia útil: o valor no fechamento de ``t`` leva as taxas de
    ``[execução, t)``. Intervalo entre pregões sem nenhuma taxa é lacuna e a
    série para ali -- nunca se inventa CDI.
    """
    ordenado = sorted(cdi.items())
    fatores, f = [1.0], 1.0
    for anterior, atual in zip(datas, datas[1:]):
        taxas = [v for d, v in ordenado if anterior <= d < atual]
        if not taxas:
            break
        for v in taxas:
            f *= 1.0 + v / 100.0
        fatores.append(f)
    return fatores, len(fatores)


def calcular(execucao: date, retornos: pd.DataFrame, bova11: pd.Series,
             cdi: dict[date, float], carteira: list[dict], universo: list[dict]) -> dict:
    """Curvas e retorno por ação desde o fechamento de ``execucao``.

    ``retornos``: colunas ``id``, ``data`` e ``ret`` (retorno total do dia,
    proventos reinvestidos no dia ex), mais ``suspeito`` opcional.
    ``bova11``: fechamento por data, incluindo o da execução (a base).
    ``carteira`` e ``universo``: itens ``{"id", "ticker"}`` do registro.

    Os pregões são os do BOVA11 depois da execução, até a última data que as
    três fontes cobrem. A fonte das ações só tem linha em dia com negócio, e o
    retorno do dia em que a ação volta a negociar cobre o intervalo inteiro: um
    buraco no meio da série é dia sem negócio e rende zero. Depois do último
    negócio não há essa prova, então a curva para no último pregão que todas as
    ações da carteira cobrem; ação da carteira sem nenhuma linha impede a conta.
    No universo, a lacuna deixa a curva de pesos iguais vazia dali em diante.
    """
    avisos: list[str] = []
    r = retornos.copy()
    r["data"] = pd.to_datetime(r["data"]).dt.date
    r = r[r["data"] > execucao]
    bova = bova11.copy()
    bova.index = pd.to_datetime(bova.index).date
    bova = bova.sort_index()

    if not len(r) or not any(d > execucao for d in bova.index):
        return _vazio(execucao, ["nenhum pregão depois da execução nas três fontes"])
    if execucao not in bova.index:
        raise ValueError(f"sem fechamento do BOVA11 no pregão de execução ({execucao})")
    ultima = r.groupby("id")["data"].max()
    sem_dado = [c["ticker"] for c in carteira if c["id"] not in ultima.index]
    if sem_dado:
        return _vazio(execucao, ["sem nenhum pregão na fonte: " + ", ".join(sem_dado)
                                 + "; a curva não sai com ação da carteira faltando"])
    ids_c20 = [c["id"] for c in carteira]
    fim_c20 = ultima.reindex(ids_c20).min()
    atrasadas = [c["ticker"] for c in carteira if ultima[c["id"]] == fim_c20]
    if fim_c20 < r["data"].max():
        avisos.append(f"{', '.join(atrasadas)} sem negócio na fonte depois de "
                      f"{fim_c20:%d/%m/%Y}; as curvas param na mesma data")
    pregoes = [d for d in bova.index if execucao < d <= fim_c20]
    if not pregoes:
        return _vazio(execucao, avisos)
    datas = [execucao, *pregoes]
    fatores_cdi, cobertos = _fator_cdi(cdi, datas)
    if cobertos < len(datas):
        avisos.append(f"CDI publicado só até {datas[cobertos - 1]:%d/%m/%Y}; "
                      "as curvas param na mesma data")
        datas = datas[:cobertos]
        pregoes = datas[1:]
    if not pregoes:
        return _vazio(execucao, avisos)

    # Compõe no calendário das ações e só depois amostra nas datas do BOVA11:
    # um pregão sem fechamento do ETF não pode apagar o retorno das ações nele.
    r = r[r["data"] <= pregoes[-1]]
    pulados = sorted(set(r["data"]) - set(bova.index))
    if pulados:
        avisos.append("BOVA11 sem fechamento em " + ", ".join(f"{d:%d/%m/%Y}" for d in pulados)
                      + "; o gráfico pula o dia, sem perder o retorno das ações nele")
    largo = r.pivot_table(index="data", columns="id", values="ret", aggfunc="sum").fillna(0.0)
    fatores = (1 + largo).cumprod()
    base = pd.DataFrame(1.0, index=[execucao], columns=fatores.columns)
    fatores = pd.concat([base, fatores]).reindex(datas, method="ffill")

    ids_uni = [u["id"] for u in universo]
    if "suspeito" in r.columns:
        n_susp = int(r[r["id"].isin(ids_c20) & r["suspeito"].fillna(False).astype(bool)].shape[0])
        if n_susp:
            avisos.append(f"{n_susp} retorno(s) diário(s) da carteira marcados como suspeitos "
                          "na fonte; entram na conta como estão")

    bruto_c20, posicoes = _carteira_de_acoes(fatores, ids_c20)
    sem_uni = [u["ticker"] for u in universo if u["id"] not in ultima.index]
    if sem_uni or not ids_uni:
        avisos.append("pesos iguais sem curva: " + (", ".join(sem_uni) or "universo vazio")
                      + " sem nenhum pregão na fonte")
        pesos_iguais = None
    else:
        bruto_uni, _ = _carteira_de_acoes(fatores, ids_uni)
        pesos_iguais = _liquido_acoes(bruto_uni)
        fim_uni = ultima.reindex(ids_uni).min()
        if fim_uni < datas[-1]:
            avisos.append(f"pesos iguais só até {fim_uni:%d/%m/%Y}: ação do universo sem "
                          "negócio na fonte depois disso")
            pesos_iguais = pesos_iguais.where(pesos_iguais.index <= fim_uni)
    cota = bova.reindex(datas).ffill()
    venda_etf = CAPITAL * (1 - CUSTO_LADO) * (cota / cota.iloc[0]) * (1 - CUSTO_LADO)
    etf = venda_etf - (venda_etf - CAPITAL).clip(lower=0) * ALIQUOTA_IR
    bruto_cdi = pd.Series([CAPITAL * f for f in fatores_cdi], index=datas)
    cdi_liq = bruto_cdi - (bruto_cdi - CAPITAL).clip(lower=0) * ALIQUOTA_IR

    curvas = {"carteira20": _liquido_acoes(bruto_c20), "pesos_iguais": pesos_iguais,
              "bova11": etf, "cdi": cdi_liq}
    total = float(posicoes.sum())
    ativos = [{"id": c["id"], "ticker": c["ticker"],
               "retorno": round(float(fatores[c["id"]].iloc[-1]) - 1, 6),
               "peso_atual": round(float(posicoes[c["id"]]) / total, 6)}
              for c in carteira]
    return {"pregoes": len(pregoes), "datas": [d.isoformat() for d in datas],
            "curvas": {k: None if s is None else
                       [None if pd.isna(v) else round(float(v), 4) for v in s.reindex(datas)]
                       for k, s in curvas.items()},
            "ativos": ativos, "avisos": avisos}


def _vazio(execucao: date, avisos: list[str]) -> dict:
    return {"pregoes": 0, "datas": [execucao.isoformat()], "curvas": {}, "ativos": [],
            "avisos": avisos}


def impressao_corpo(corpo: dict) -> str:
    """SHA-256 do corpo vigente da revisão, já com as correções aplicadas.

    O hash da revisão não muda quando uma correção substitui o corpo, então o
    artefato carrega também esta impressão e a aba recusa o que foi calculado
    para um corpo que não vale mais.
    """
    return hashlib.sha256(c20.canonico(corpo).encode("utf-8")).hexdigest()


def amostrar(datas: list[str]) -> list[int]:
    """Índices dos pontos do gráfico: diários no primeiro ano, mensais depois."""
    if len(datas) <= PONTOS_DIARIOS_ATE + 1:
        return list(range(len(datas)))
    ultimos: dict[str, int] = {}
    for i, d in enumerate(datas):
        ultimos[d[:7]] = i
    return sorted({0, *ultimos.values(), len(datas) - 1})


def retorno_acumulado(valores: list[float]) -> list[float]:
    """Valor líquido de liquidação → retorno sobre os R$ 1.000 aplicados (lacuna fica lacuna)."""
    return [None if v is None else v / CAPITAL - 1 for v in valores]
