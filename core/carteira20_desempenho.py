"""Desempenho diário da Carteira 20 desde a execução, contra os comparadores.

Puro: sem banco e sem rede. ``scripts/carteira20_desempenho.py`` junta os
insumos (retorno total diário das ações, fechamento do BOVA11 e CDI) e grava o
artefato que a aba lê; aqui só se faz a conta.

É a curva "teórica" do protocolo (seq 0, ``medicao.curvas.teorica``): R$ 1.000
no fechamento do pregão de execução, pesos iguais, sem aporte, sem rebalancear
até a próxima revisão. Cada ponto é o valor **líquido de liquidação** -- quanto
sobraria vendendo tudo naquele fechamento:

- Carteira 20: ações inteiras, como manda ``operacao.lote`` do protocolo
  ("fracionário: 1 ação"). Compra no fechamento da execução pagando o spread
  do mercado fracionário medido no COTAHIST, mais 0,30% de corretagem; o que não
  fecha uma ação fica em **caixa, que não rende**. Na venda, o spread de volta e
  0,30%. A distância até a curva em frações (``carteira20_fracoes``) é o custo
  de ser pequeno;
- pesos iguais do universo: frações, 0,30% por lado -- é a régua contra a qual
  a pesquisa mediu a expectativa, e só se compara com ``carteira20_fracoes``;
- BOVA11: cotas inteiras com o spread do lote padrão, 0,30% em cada lado, 15%
  de IR sobre o ganho (ETF não tem isenção) e o troco em caixa;
- ações: venda até R$ 20 mil no mês é isenta de IR;
- CDI: sem custo de corretagem, 15% de IR sobre o rendimento.

Idealização que resta: os proventos voltam para a própria ação em fração
(retorno total), como ``operacao.proventos`` manda na curva teórica.

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

SERIES = ("carteira20", "carteira20_fracoes", "pesos_iguais", "bova11", "cdi")


def _liquido_acoes(bruto: pd.Series, pago: float = CAPITAL) -> pd.Series:
    """Valor de venda das ações menos o IR, que só incide acima da isenção mensal.

    ``pago`` é o custo de aquisição (preço, spread e corretagem): o ganho
    tributável é a venda menos ele.
    """
    venda = bruto * (1 - CUSTO_LADO)
    ir = ((venda - pago).clip(lower=0) * ALIQUOTA_IR).where(venda > ISENCAO_MENSAL_ACOES, 0.0)
    return venda - ir


def comprar_em_lotes(precos: dict[str, float], orcamento: float) -> tuple[dict[str, int], float]:
    """Quantas ações inteiras de cada papel cabem em ``orcamento`` a pesos iguais, e o troco.

    ``precos`` é o preço efetivo de uma ação (com spread e corretagem). Primeiro
    o piso de ``alvo / preço`` em cada papel, como ``core/aporte.py`` arredonda
    por lote; depois o troco compra, uma ação por vez, o papel mais abaixo do
    alvo -- só se a ação couber no caixa e deixar a posição mais perto do alvo
    do que estava (``preço < 2 × falta``). Ação mais cara que o dobro do alvo
    fica com zero: comprá-la desequilibraria mais do que deixá-la de fora.
    """
    if not precos:
        return {}, orcamento
    alvo = orcamento / len(precos)
    qtd = {i: int(alvo // p) for i, p in precos.items()}
    caixa = orcamento - sum(q * precos[i] for i, q in qtd.items())
    while True:
        candidatos = [(alvo - qtd[i] * p, i) for i, p in precos.items()
                      if p <= caixa + 1e-9 and p < 2 * (alvo - qtd[i] * p)]
        if not candidatos:
            return qtd, max(caixa, 0.0)
        _, escolhido = min(candidatos, key=lambda c: (-c[0], c[1]))
        qtd[escolhido] += 1
        caixa -= precos[escolhido]


def _carteira_de_acoes(fatores: pd.DataFrame, ids: list[str]) -> tuple[pd.Series, pd.Series]:
    """Valor bruto da carteira e de cada ação: compra a pesos iguais e segura."""
    por_acao = CAPITAL * (1 - CUSTO_LADO) / len(ids)
    posicoes = fatores.reindex(columns=ids).fillna(1.0) * por_acao
    return posicoes.sum(axis=1), posicoes.iloc[-1]


def _fator_cdi(cdi: dict[date, float], datas: list[date],
               calendario: set[date] | None = None) -> tuple[list[float], int, date | None]:
    """Fator acumulado do CDI em cada data, quantos pregões a série cobre e o dia que faltou.

    A taxa de ``d`` (em % ao dia útil, série 12 do BCB) remunera de ``d`` até o
    próximo dia útil: o valor no fechamento de ``t`` leva as taxas de
    ``[execução, t)``. Todo pregão do ``calendario`` dentro do intervalo
    precisa de taxa -- inclusive o que o BOVA11 pulou e por isso não está em
    ``datas``. Sem ela a série para ali; nunca se inventa CDI.
    """
    exigidos = sorted(set(calendario or ()) | set(datas))
    ordenado = sorted(cdi.items())
    fatores, f = [1.0], 1.0
    for anterior, atual in zip(datas, datas[1:]):
        faltante = next((d for d in exigidos if anterior <= d < atual and d not in cdi), None)
        if faltante is not None:
            return fatores, len(fatores), faltante
        for d, v in ordenado:
            if anterior <= d < atual:
                f *= 1.0 + v / 100.0
        fatores.append(f)
    return fatores, len(fatores), None


def calcular(execucao: date, retornos: pd.DataFrame, bova11: pd.Series,
             cdi: dict[date, float], carteira: list[dict], universo: list[dict], *,
             precos: dict[str, float] | None = None,
             custos: dict[str, tuple[float, float]] | None = None,
             custo_bova11: tuple[float, float] = (0.0, 0.0)) -> dict:
    """Curvas e retorno por ação desde o fechamento de ``execucao``.

    ``retornos``: colunas ``id``, ``data`` e ``ret`` (retorno total do dia,
    proventos reinvestidos no dia ex), mais ``suspeito`` opcional.
    ``bova11``: fechamento por data, incluindo o da execução (a base).
    ``carteira`` e ``universo``: itens ``{"id", "ticker"}`` do registro.
    ``precos``: fechamento unitário de cada ação da carteira na execução; sem
    ele a curva em ações inteiras não sai (fica ``None``, com aviso).
    ``custos``: ``(compra, venda)`` de cada ação, a fração do fechamento que o
    spread custa em cada lado; o que faltar entra como zero, com aviso.
    ``custo_bova11``: o mesmo para o ETF.

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
    fatores_cdi, cobertos, faltante = _fator_cdi(cdi, datas, set(r["data"]))
    if faltante is not None:
        avisos.append(f"CDI sem a taxa de {faltante:%d/%m/%Y}; as curvas param em "
                      f"{datas[cobertos - 1]:%d/%m/%Y}")
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
    # ação cujo primeiro negócio cai depois do corte não tem linha até aqui:
    # é buraco no meio da série, fator 1 desde a execução
    ids_uni = [u["id"] for u in universo]
    fatores = (pd.concat([base, fatores]).reindex(datas, method="ffill")
               .reindex(columns=list(dict.fromkeys([*fatores.columns, *ids_c20, *ids_uni])),
                        fill_value=1.0))

    if "suspeito" in r.columns:
        n_susp = int(r[r["id"].isin(ids_c20) & r["suspeito"].fillna(False).astype(bool)].shape[0])
        if n_susp:
            avisos.append(f"{n_susp} retorno(s) diário(s) da carteira marcados como suspeitos "
                          "na fonte; entram na conta como estão")

    bruto_c20, posicoes_fracao = _carteira_de_acoes(fatores, ids_c20)
    lotes = _carteira_em_lotes(fatores, carteira, precos, custos, avisos)
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
    etf, caixa_etf, cotas = _bova11_em_cotas(cota, custo_bova11)
    bruto_cdi = pd.Series([CAPITAL * f for f in fatores_cdi], index=datas)
    cdi_liq = bruto_cdi - (bruto_cdi - CAPITAL).clip(lower=0) * ALIQUOTA_IR

    curvas = {"carteira20": None if lotes is None else lotes["curva"],
              "carteira20_fracoes": _liquido_acoes(bruto_c20),
              "pesos_iguais": pesos_iguais, "bova11": etf, "cdi": cdi_liq}
    posicoes = posicoes_fracao if lotes is None else lotes["posicoes"]
    total = float(posicoes.sum())
    ativos = []
    for c in carteira:
        item = {"id": c["id"], "ticker": c["ticker"],
                "retorno": round(float(fatores[c["id"]].iloc[-1]) - 1, 6),
                "peso_atual": round(float(posicoes[c["id"]]) / total, 6) if total else 0.0}
        if lotes is not None:
            item.update(lotes["ativos"][c["id"]])
        ativos.append(item)
    caixa = {"carteira20": None if lotes is None else round(lotes["caixa"], 4),
             "bova11": round(caixa_etf, 4)}
    return {"pregoes": len(pregoes), "datas": [d.isoformat() for d in datas],
            "curvas": {k: None if s is None else
                       [None if pd.isna(v) else round(float(v), 4) for v in s.reindex(datas)]
                       for k, s in curvas.items()},
            "caixa": caixa, "cotas_bova11": cotas, "ativos": ativos, "avisos": avisos}


def _reais(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _carteira_em_lotes(fatores: pd.DataFrame, carteira: list[dict],
                       precos: dict[str, float] | None,
                       custos: dict[str, tuple[float, float]] | None,
                       avisos: list[str]) -> dict | None:
    """A Carteira 20 como dá para comprar: ações inteiras, spread e troco em caixa."""
    ids = [c["id"] for c in carteira]
    ticker = {c["id"]: c["ticker"] for c in carteira}
    sem_preco = [ticker[i] for i in ids if not (precos or {}).get(i)]
    if sem_preco:
        avisos.append("sem o fechamento da execução de " + ", ".join(sem_preco)
                      + ": a curva em ações inteiras não sai, só a em frações")
        return None
    custos = custos or {}
    sem_custo = [ticker[i] for i in ids if i not in custos]
    if sem_custo:
        avisos.append("sem spread medido de " + ", ".join(sem_custo)
                      + "; entram só com a corretagem")
    # mediana negativa é ruído de quem negociou fora do livro: não vira bônus
    compra = {i: max(float(custos.get(i, (0.0, 0.0))[0]), 0.0) for i in ids}
    venda = {i: max(float(custos.get(i, (0.0, 0.0))[1]), 0.0) for i in ids}
    efetivo = {i: precos[i] * (1 + compra[i]) * (1 + CUSTO_LADO) for i in ids}
    qtd, caixa = comprar_em_lotes(efetivo, CAPITAL)
    pago = sum(qtd[i] * efetivo[i] for i in ids)
    alvo = CAPITAL / len(ids)
    fora = [f"{ticker[i]} (1 ação custa {_reais(efetivo[i])}, o alvo é {_reais(alvo)})"
            for i in ids if qtd[i] == 0]
    if fora:
        avisos.append("fora da compra, uma ação passa do dobro do alvo: " + "; ".join(fora))
    f = fatores.reindex(columns=ids).fillna(1.0)
    posicoes = f.mul(pd.Series({i: qtd[i] * precos[i] for i in ids}), axis=1)
    venda_bruta = f.mul(pd.Series({i: qtd[i] * precos[i] * (1 - venda[i]) for i in ids}),
                        axis=1).sum(axis=1)
    curva = _liquido_acoes(venda_bruta, pago) + caixa
    return {"curva": curva, "posicoes": posicoes.iloc[-1], "caixa": caixa,
            "ativos": {i: {"quantidade": qtd[i], "preco_compra": round(precos[i], 4),
                           "custo_compra": round(compra[i], 6),
                           "custo_venda": round(venda[i], 6)} for i in ids}}


def _bova11_em_cotas(cota: pd.Series, custo: tuple[float, float]) -> tuple[pd.Series, float, int]:
    """BOVA11 em cotas inteiras: valor líquido de liquidação, troco e número de cotas."""
    compra, venda = (max(float(c), 0.0) for c in custo)
    efetivo = float(cota.iloc[0]) * (1 + compra) * (1 + CUSTO_LADO)
    cotas = int(CAPITAL // efetivo)
    pago = cotas * efetivo
    caixa = CAPITAL - pago
    venda_etf = cotas * cota * (1 - venda) * (1 - CUSTO_LADO)
    return venda_etf - (venda_etf - pago).clip(lower=0) * ALIQUOTA_IR + caixa, caixa, cotas


def _vazio(execucao: date, avisos: list[str]) -> dict:
    return {"pregoes": 0, "datas": [execucao.isoformat()], "curvas": {}, "caixa": {},
            "cotas_bova11": None, "ativos": [], "avisos": avisos}


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
