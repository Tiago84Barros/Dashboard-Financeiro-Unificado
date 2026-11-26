"""
core/b3_comparacao_estrategias.py — régua única da comparação das estratégias
B3 (§29 do guia de metodologia), fixada em 26/11/2026 ANTES de medir.

A pergunta do usuário: qual estratégia vira o modelo padrão da Criação de
Portfólio B3, sabendo que o padrão tem de render na **valorização** das ações
e no **crescimento contínuo dos dividendos**. As §19–§28 mediram cada
estratégia só pelo retorno total contra pesos iguais; esta régua acrescenta a
renda e a valorização separadas, com a mesma janela e os mesmos dados
corrigidos (leitor da CVM do #805, preços limpos em 26/11/2026).

**Rendimento de dividendos da safra** (`rendimento_por_razao`): a razão entre o
preço ajustado e o preço de fechamento nas duas pontas da vigência,
``(a1/a0) / (c1/c0) - 1``. As duas séries da brapi já descontam desdobramento
e grupamento; só o ajustado desconta proventos. A razão, portanto, mede só o
provento reinvestido, na mesma base do retorno da régua. Os proventos por
pagamento da brapi (`core.proventos_b3`) NÃO servem: não descontam
desdobramento (BBAS3 sai com o dobro do rendimento antes do desdobramento de
2024; WEGE3, ITSA4 e CMIG4 também incham). Na conferência de 26/11/2026, 68%
dos ticker-ano das duas fontes ficaram a menos de 1 p.p. e a mediana da razão
foi 0,92.

- Ponta = o último pregão até a data, com tolerância de
  ``TOLERANCIA_PONTA_DIAS``; início = véspera da vigência (31 de março),
  fim = último dia dela.
- Rendimento fora de [``RENDIMENTO_MIN``, ``RENDIMENTO_MAX``) é tratado como
  dado ruim e sai (não vira zero); entre ``RENDIMENTO_MIN`` e 0 vira 0 (ruído
  de arredondamento do ajuste).
- Papel sem rendimento é tirado e os pesos dos outros são renormalizados; a
  cobertura (fração do peso com dado) vai para a saída.

**Trajetória de renda** (`trajetoria_de_renda`): o investidor que gasta os
dividendos. Começa com ``CAPITAL_INICIAL`` na primeira safra medida; em cada
safra recebe ``renda = capital × y`` e o capital cresce só pela valorização,
``capital × (1 + retorno líquido - y)``. Mede:

- ``crescimento_renda_aa``: tendência da renda, ``exp(b) - 1`` da reta de
  mínimos quadrados de ``log(renda)`` contra o ano (renda zero em algum ano
  deixa a tendência indefinida e reprova);
- ``quedas_de_renda``: anos em que a renda caiu em relação ao ano anterior;
- ``renda_total``, ``renda_ultimo_sobre_primeiro`` e ``capital_final``
  (a valorização acumulada, sem os dividendos).

**Regra** (`decidir_padrao`), contra a carteira de pesos iguais do universo
da safra (o mesmo benchmark das §19–§28, com a renda pela média simples dos
rendimentos do universo):

- R1 — retorno: excesso líquido médio sobre pesos iguais > 0 com IC 95%
  acima de zero, e média sem a melhor safra > 0 (os testes (a) e (b) da §19);
- R2 — dividendos crescendo sem parar: tendência da renda ≥ a dos pesos
  iguais E quedas de renda ≤ as dos pesos iguais;
- R3 — valorização: capital final (só preço) ≥ o dos pesos iguais.

Candidata = passa R1, R2 e R3. Entre as candidatas, vira padrão a de maior
excesso médio. Se nenhuma passa, o padrão da tela (nota + fatores) fica, e a
saída diz qual regra cada estratégia não cumpriu. A renda total e as
pareadas contra a nota + fatores e contra a líder de segmento são
informativas. Nove safras é pouco: a regra pode errar nos dois sentidos, e a
saída diz isso.

Funções puras: nada de banco nem de Streamlit.
"""
from __future__ import annotations

import math

VERSAO = "1.0.0"

CAPITAL_INICIAL = 100_000.0
TOLERANCIA_PONTA_DIAS = 45
RENDIMENTO_MIN = -0.05
RENDIMENTO_MAX = 1.0


def rendimento_por_razao(c0, a0, c1, a1) -> float | None:
    """Provento reinvestido entre as duas pontas: ``(a1/a0)/(c1/c0) - 1``.

    None quando falta ponta, há preço não positivo ou o resultado cai fora de
    [RENDIMENTO_MIN, RENDIMENTO_MAX). Entre RENDIMENTO_MIN e 0 devolve 0.
    """
    try:
        vals = [float(v) for v in (c0, a0, c1, a1)]
    except (TypeError, ValueError):
        return None
    if any(not math.isfinite(v) or v <= 0 for v in vals):
        return None
    c0, a0, c1, a1 = vals
    y = (a1 / a0) / (c1 / c0) - 1.0
    if not (RENDIMENTO_MIN <= y < RENDIMENTO_MAX):
        return None
    return max(0.0, y)


def rendimento_da_carteira(pesos: dict[str, float],
                           rendimento: dict[str, float | None]) -> tuple[float | None, float]:
    """(rendimento ponderado, cobertura) com renormalização sobre quem tem dado."""
    total = sum(float(w) for w in pesos.values() if w and w > 0)
    if total <= 0:
        return None, 0.0
    com = {t: float(w) for t, w in pesos.items()
           if w and w > 0 and rendimento.get(t) is not None}
    soma = sum(com.values())
    if soma <= 0:
        return None, 0.0
    y = sum(w * float(rendimento[t]) for t, w in com.items()) / soma
    return y, soma / total


def rendimento_medio(tickers, rendimento: dict[str, float | None]) -> tuple[float | None, float]:
    """Pesos iguais em ``tickers``: o rendimento do benchmark do universo."""
    lista = list(dict.fromkeys(str(t) for t in tickers))
    return rendimento_da_carteira({t: 1.0 for t in lista}, rendimento)


def _tendencia_log(rendas: list[float]) -> float | None:
    """``exp(b) - 1`` da reta de log(renda) contra o índice do ano."""
    if len(rendas) < 2 or any(r is None or r <= 0 for r in rendas):
        return None
    xs = list(range(len(rendas)))
    ys = [math.log(r) for r in rendas]
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    den = sum((x - mx) ** 2 for x in xs)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    return math.exp(b) - 1.0


def trajetoria_de_renda(linhas: list[dict], capital: float = CAPITAL_INICIAL) -> dict:
    """Investidor que gasta os dividendos.

    ``linhas``: [{"safra", "retorno", "rendimento"}] em ordem de safra;
    ``retorno`` é o total líquido da safra e ``rendimento`` a parte em
    dividendos (None vira 0: sem dado não se inventa renda).
    """
    v = float(capital)
    anos = []
    for t in sorted(linhas, key=lambda r: int(r["safra"])):
        y = float(t.get("rendimento") or 0.0)
        ret = float(t["retorno"])
        renda = v * y
        anos.append({"safra": int(t["safra"]), "capital_inicio": v, "renda": renda,
                     "rendimento": y, "retorno": ret})
        v = v * (1.0 + ret - y)
    rendas = [a["renda"] for a in anos]
    quedas = sum(1 for a, b in zip(rendas, rendas[1:]) if b < a)
    return {
        "anos": anos,
        "capital_final": v,
        "renda_total": sum(rendas),
        "crescimento_renda_aa": _tendencia_log(rendas),
        "quedas_de_renda": quedas,
        "renda_ultimo_sobre_primeiro": (rendas[-1] / rendas[0])
        if rendas and rendas[0] > 0 else None,
    }


def decidir_padrao(vs_ew: dict, renda: dict, renda_ew: dict) -> dict:
    """R1, R2, R3 da §29 e o veredito (`candidata`)."""
    lo = (vs_ew.get("ic95") or [None, None])[0]
    sem_melhor = vs_ew.get("media_sem_melhor_safra")
    r1 = bool(vs_ew.get("media") is not None and vs_ew["media"] > 0
              and lo is not None and lo > 0
              and sem_melhor is not None and sem_melhor > 0)
    g, g_ew = renda.get("crescimento_renda_aa"), renda_ew.get("crescimento_renda_aa")
    r2 = bool(g is not None and g_ew is not None and g >= g_ew
              and renda["quedas_de_renda"] <= renda_ew["quedas_de_renda"])
    r3 = bool(renda["capital_final"] >= renda_ew["capital_final"])
    testes = {"r1_retorno": r1, "r2_dividendo_crescente": r2, "r3_valorizacao": r3}
    return {"testes": testes, "candidata": all(testes.values()),
            "falhou": [k for k, ok in testes.items() if not ok]}


def escolher_padrao(bracos: dict[str, dict], atual: str) -> dict:
    """Entre as candidatas, a de maior excesso médio; sem candidata, fica ``atual``.

    ``bracos``: {rótulo: {"vs_equal_weight": {...}, "decisao": {...}}}.
    """
    cand = [(k, b["vs_equal_weight"]["media"]) for k, b in bracos.items()
            if b.get("decisao", {}).get("candidata")
            and b["vs_equal_weight"].get("media") is not None]
    if not cand:
        return {"padrao": atual, "trocou": False, "candidatas": [],
                "motivo": "nenhuma estratégia passou R1, R2 e R3"}
    cand.sort(key=lambda x: (-x[1], x[0]))
    return {"padrao": cand[0][0], "trocou": cand[0][0] != atual,
            "candidatas": [k for k, _ in cand],
            "motivo": "maior excesso médio entre as candidatas"}
