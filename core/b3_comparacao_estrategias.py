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

**Caixa recebido** (`rendimentos_do_ano`, régua 1.1.0, 27/11/2026): a razão
das pontas é o provento *reinvestido* (um fator multiplicativo), não o caixa
de quem gasta os dividendos. O caixa sai da série diária: em cada data ex,
``(1 - 1/r_i) × P_{i-1}/P_0``, com ``r_i`` a razão diária ajustado/bruto.
Razão diária fora de [``RAZAO_DIA_MIN``, ``RAZAO_DIA_MAX``) é quebra da série
e o ticker-ano sai. A régua 1.0.0 usava o fator como caixa e subtraía o fator
do retorno (``1 + retorno - y``), o que não isola o preço; corrigido pelos
achados da revisão do PR #810.

**Trajetória de renda** (`trajetoria_de_renda`): o investidor que gasta os
dividendos. Começa com ``CAPITAL_INICIAL`` na primeira safra medida; em cada
safra recebe ``renda = capital × caixa`` e o capital cresce só pela
valorização, ``capital × (1 + retorno líquido) / (1 + fator)``. Safra sem
rendimento deixa a trajetória ``insuficiente`` (métricas None, R2 e R3
reprovam) em vez de virar renda zero. Mede:

- ``crescimento_renda_aa``: tendência da renda, ``exp(b) - 1`` da reta de
  mínimos quadrados de ``log(renda)`` contra o ano (renda zero medida em algum
  ano deixa a tendência indefinida e reprova);
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

Decisão do usuário de 26/11/2026: a regra escolheu a ``rota_valor_top20``;
o usuário ficou com a ``rota_valor`` inteira (+16,3 pp, só se separa da top
20 em 2025), que virou a padrão da tela (`views/portfolio_b3_rota_valor.py`).
A regra continua gravando a sua escolha em ``escolha``; a da tela é do usuário.

Funções puras: nada de banco nem de Streamlit.
"""
from __future__ import annotations

import math

VERSAO = "1.1.0"

CAPITAL_INICIAL = 100_000.0
TOLERANCIA_PONTA_DIAS = 45
RENDIMENTO_MIN = -0.05
RENDIMENTO_MAX = 1.0
# Razão diária ajustado/bruto: acima de 1 + LIMIAR_DATA_EX é data ex de
# provento (abaixo disso é arredondamento do ajuste); fora de
# [RAZAO_DIA_MIN, RAZAO_DIA_MAX) é quebra da série e o ticker-ano sai.
LIMIAR_DATA_EX = 5e-4
RAZAO_DIA_MIN = 0.95
RAZAO_DIA_MAX = 2.0
# Salto do fechamento acompanhado pelo ajustado (razão ~1) de SALTO_DE_PRECO
# vezes ou mais, para cima ou para baixo, é desdobramento/grupamento (3:2 já
# conta) ou degrau da série: não entra no índice de preço que pesa o caixa
# recebido dentro do ano.
SALTO_DE_PRECO = 1.45


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


def rendimentos_do_ano(serie) -> dict | None:
    """Fator e caixa dos proventos de um papel na safra, da série diária.

    ``serie``: [(fechamento, ajustado), ...] em ordem de data, da véspera da
    vigência ao fim dela. Devolve ``{"fator", "caixa"}`` ou None (dado ruim):

    - ``fator`` = ``rendimento_por_razao`` nas pontas: o provento
      **reinvestido**, ``Π r_i - 1``, que separa o retorno total do preço:
      ``1 + retorno = (1 + valorização) × (1 + fator)``;
    - ``caixa`` = o provento **recebido** por quem não reinveste, em fração do
      capital do início: ``Σ (1 - 1/r_i) × P_{i-1}/P_0`` nas datas ex, com
      ``r_i`` a razão diária ajustado/bruto (o provento por ação é
      ``P_{i-1} × (1 - 1/r_i)``, a convenção do ajuste retroativo) e ``P``
      o fechamento sem os saltos de desdobramento (``SALTO_DE_PRECO``).

    O caixa vem da própria série que desconta desdobramento, não dos
    pagamentos da brapi (que não descontam; ver o cabeçalho do módulo).
    """
    try:
        pts = [(float(c), float(a)) for c, a in serie]
    except (TypeError, ValueError):
        return None
    if len(pts) < 2 or any(not (math.isfinite(c) and math.isfinite(a) and c > 0 and a > 0)
                           for c, a in pts):
        return None
    fator = rendimento_por_razao(pts[0][0], pts[0][1], pts[-1][0], pts[-1][1])
    if fator is None:
        return None
    caixa, indice = 0.0, 1.0
    for (c0, a0), (c1, a1) in zip(pts, pts[1:]):
        cr = c1 / c0
        r = (a1 / a0) / cr
        if not (RAZAO_DIA_MIN <= r < RAZAO_DIA_MAX):
            return None
        if r > 1.0 + LIMIAR_DATA_EX:
            caixa += (1.0 - 1.0 / r) * indice
        salto = cr >= SALTO_DE_PRECO or cr <= 1.0 / SALTO_DE_PRECO
        if not (abs(r - 1.0) <= LIMIAR_DATA_EX and salto):
            indice *= cr
    return {"fator": fator, "caixa": caixa}


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

    ``linhas``: [{"safra", "retorno", "rendimento", "renda_caixa"}] em ordem
    de safra; ``retorno`` é o total líquido da safra, ``rendimento`` o fator
    dos proventos reinvestidos e ``renda_caixa`` o provento recebido em
    fração do capital do início (`rendimentos_do_ano`). O capital cresce só
    pelo preço, ``capital × (1 + retorno) / (1 + rendimento)``, e a renda do
    ano é ``capital × renda_caixa``.

    Ano sem rendimento (None) não vira zero: a separação entre preço e
    provento é desconhecida, a trajetória sai ``insuficiente`` e as métricas
    que dependem dela (renda, quedas, tendência, capital final) ficam None.
    """
    v = float(capital)
    anos, faltam = [], []
    for t in sorted(linhas, key=lambda r: int(r["safra"])):
        s = int(t["safra"])
        y, yc = t.get("rendimento"), t.get("renda_caixa")
        ret = float(t["retorno"])
        if y is None or yc is None or v is None:
            if y is None or yc is None:
                faltam.append(s)
            anos.append({"safra": s, "capital_inicio": v, "renda": None,
                         "rendimento": y, "renda_caixa": yc, "retorno": ret})
            v = None
            continue
        y, yc = float(y), float(yc)
        anos.append({"safra": s, "capital_inicio": v, "renda": v * yc,
                     "rendimento": y, "renda_caixa": yc, "retorno": ret})
        v = v * (1.0 + ret) / (1.0 + y)
    if faltam:
        return {"anos": anos, "insuficiente": True, "safras_sem_rendimento": faltam,
                "capital_final": None, "renda_total": None,
                "crescimento_renda_aa": None, "quedas_de_renda": None,
                "renda_ultimo_sobre_primeiro": None}
    rendas = [a["renda"] for a in anos]
    quedas = sum(1 for a, b in zip(rendas, rendas[1:]) if b < a)
    return {
        "anos": anos,
        "insuficiente": False,
        "safras_sem_rendimento": [],
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
    # Trajetória insuficiente (de um lado ou do outro) reprova R2 e R3: sem a
    # separação entre preço e provento não há o que comparar.
    ok = not renda.get("insuficiente") and not renda_ew.get("insuficiente")
    g, g_ew = renda.get("crescimento_renda_aa"), renda_ew.get("crescimento_renda_aa")
    q, q_ew = renda.get("quedas_de_renda"), renda_ew.get("quedas_de_renda")
    r2 = bool(ok and g is not None and g_ew is not None and g >= g_ew
              and q is not None and q_ew is not None and q <= q_ew)
    k, k_ew = renda.get("capital_final"), renda_ew.get("capital_final")
    r3 = bool(ok and k is not None and k_ew is not None and k >= k_ew)
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


def motivos_vencida(dados: dict, versao_metodologia: str,
                    versao_presets: str) -> list[str]:
    """Por que a medicao gravada nao vale para o metodo de hoje (vazio = em dia).

    Ponto unico da regra: a tela da rota de valor e o Grau de Confianca leem
    daqui, para os dois nunca discordarem sobre a mesma medicao."""
    motivos = []
    if str(dados.get("versao_regua")) != str(VERSAO):
        motivos.append(f"régua {dados.get('versao_regua')} ≠ {VERSAO}")
    if str(dados.get("versao_metodologia")) != str(versao_metodologia):
        motivos.append(f"metodologia {dados.get('versao_metodologia')} ≠ {versao_metodologia}")
    if str(dados.get("versao_presets")) != str(versao_presets):
        motivos.append(f"perfis {dados.get('versao_presets')} ≠ {versao_presets}")
    return motivos
