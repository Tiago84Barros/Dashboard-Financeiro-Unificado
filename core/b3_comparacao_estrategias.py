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

**Valorização da carteira** (régua 1.2.0, 27/11/2026): a valorização só pelo
preço sai **papel a papel**, da mesma série diária (``preco`` de
`rendimentos_do_ano`), e só depois é ponderada (`linha_de_renda`). Dividir o
retorno total da carteira pelo fator médio (régua 1.1.0) não isola o preço
quando os papéis têm valorização e proventos diferentes: com pesos iguais, A
+100% sem provento e B 0% com fator de 20% dão 145,45 em vez de 150. O custo
de transação entra como ``(1 + líquido) / (1 + bruto)`` da própria régua.
Na mesma versão, o salto de desdobramento (``SALTO_DE_PRECO``) passou a
valer só entre pregões a até ``PASSO_DIARIO_DIAS``: a série é mensal até 2024,
e a regra tirava do índice quedas reais de um mês (março de 2020).

**Trajetória de renda** (`trajetoria_de_renda`): o investidor que gasta os
dividendos. Começa com ``CAPITAL_INICIAL`` na primeira safra medida; em cada
safra recebe ``renda = capital × caixa`` e o capital cresce só pela
valorização, ``capital × (1 + valorização) × fator_custo``. Safra sem
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

VERSAO = "1.2.0"

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
# recebido dentro do ano. Só vale entre pregões a até PASSO_DIARIO_DIAS
# corridos: `market.historical_prices` é mensal até 2024 e, num passo de um
# mês, queda ou alta de 45% é mercado (março de 2020), não desdobramento.
SALTO_DE_PRECO = 1.45
PASSO_DIARIO_DIAS = 7


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

    ``serie``: [(data, fechamento, ajustado), ...] em ordem de data, da
    véspera da vigência ao fim dela (sem a data, [(fechamento, ajustado)],
    todo passo conta como diário). Devolve ``{"fator", "caixa", "preco"}`` ou None
    (dado ruim):

    - ``fator`` = ``rendimento_por_razao`` nas pontas: o provento
      **reinvestido**, ``Π r_i - 1``, que separa o retorno total do preço:
      ``1 + retorno = (1 + valorização) × (1 + fator)``;
    - ``caixa`` = o provento **recebido** por quem não reinveste, em fração do
      capital do início: ``Σ (1 - 1/r_i) × P_{i-1}/P_0`` nas datas ex, com
      ``r_i`` a razão diária ajustado/bruto (o provento por ação é
      ``P_{i-1} × (1 - 1/r_i)``, a convenção do ajuste retroativo) e ``P``
      o fechamento sem os saltos de desdobramento (``SALTO_DE_PRECO``, só
      em passo diário: a até ``PASSO_DIARIO_DIAS`` corridos);
    - ``preco`` = a valorização só pelo preço, ``P_fim/P_0 - 1``, no mesmo
      índice sem os saltos de desdobramento.

    O caixa vem da própria série que desconta desdobramento, não dos
    pagamentos da brapi (que não descontam; ver o cabeçalho do módulo).
    """
    datas: list = []
    pts: list[tuple[float, float]] = []
    try:
        for item in serie:
            if len(item) == 3:
                datas.append(item[0])
                item = item[1:]
            c, a = item
            pts.append((float(c), float(a)))
    except (TypeError, ValueError):
        return None
    if datas and len(datas) != len(pts):
        return None
    if len(pts) < 2 or any(not (math.isfinite(c) and math.isfinite(a) and c > 0 and a > 0)
                           for c, a in pts):
        return None
    fator = rendimento_por_razao(pts[0][0], pts[0][1], pts[-1][0], pts[-1][1])
    if fator is None:
        return None
    caixa, indice = 0.0, 1.0
    for i, ((c0, a0), (c1, a1)) in enumerate(zip(pts, pts[1:])):
        cr = c1 / c0
        r = (a1 / a0) / cr
        if not (RAZAO_DIA_MIN <= r < RAZAO_DIA_MAX):
            return None
        if r > 1.0 + LIMIAR_DATA_EX:
            caixa += (1.0 - 1.0 / r) * indice
        salto = cr >= SALTO_DE_PRECO or cr <= 1.0 / SALTO_DE_PRECO
        if salto and datas:
            salto = _dias(datas[i], datas[i + 1]) <= PASSO_DIARIO_DIAS
        if not (abs(r - 1.0) <= LIMIAR_DATA_EX and salto):
            indice *= cr
    return {"fator": fator, "caixa": caixa, "preco": indice - 1.0}


def _dias(d0, d1) -> float:
    """Dias corridos entre duas datas (date, datetime ou Timestamp)."""
    delta = d1 - d0
    try:
        return delta.total_seconds() / 86400.0
    except AttributeError:
        return float(delta.days)


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


def linha_de_renda(safra: int, pesos: dict[str, float],
                   rendimentos: dict[str, dict | None], *, liquido,
                   bruto=None) -> dict:
    """Uma safra da trajetória a partir dos papéis (régua 1.2.0).

    ``pesos``: {ticker: peso} no início da safra; ``rendimentos``: {ticker:
    `rendimentos_do_ano` | None}. Fator, caixa e valorização são ponderados
    papel a papel, renormalizados sobre os mesmos papéis com dado (os três
    têm o mesmo None). ``fator_custo`` = ``(1 + liquido) / (1 + bruto)``, o
    custo de transação da própria régua (1 sem ``bruto``: pesos iguais, que
    é bruto de custo). Devolve a linha de `trajetoria_de_renda` + ``cobertura``.
    """
    com = {t: {"fator": v["fator"], "caixa": v["caixa"], "preco": v["preco"]}
           for t, v in ((str(t).upper(), (rendimentos or {}).get(str(t).upper()))
                        for t in pesos) if v is not None}
    pes = {str(t).upper(): w for t, w in pesos.items()}
    y, cob = rendimento_da_carteira(pes, {t: v["fator"] for t, v in com.items()})
    yc, _ = rendimento_da_carteira(pes, {t: v["caixa"] for t, v in com.items()})
    vp, _ = rendimento_da_carteira(pes, {t: v["preco"] for t, v in com.items()})
    liq = None if liquido is None else float(liquido)
    custo = 1.0
    if liq is not None and bruto is not None and 1.0 + float(bruto) > 0:
        custo = (1.0 + liq) / (1.0 + float(bruto))
    return {"safra": int(safra), "retorno": liq, "rendimento": y, "renda_caixa": yc,
            "valorizacao": vp, "fator_custo": custo, "cobertura": cob}


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

    ``linhas``: [{"safra", "valorizacao", "renda_caixa", "fator_custo",
    "retorno", "rendimento"}] em ordem de safra (`linha_de_renda`):
    ``valorizacao`` é a valorização só pelo preço, ponderada papel a papel;
    ``renda_caixa`` o provento recebido em fração do capital do início;
    ``fator_custo`` o custo de transação (1 sem custo); ``retorno`` e
    ``rendimento`` (o fator reinvestido) só informam. O capital cresce só pelo
    preço, ``capital × (1 + valorizacao) × fator_custo``, e a renda do ano é
    ``capital × renda_caixa``.

    Ano sem rendimento (None) não vira zero: a separação entre preço e
    provento é desconhecida, a trajetória sai ``insuficiente`` e as métricas
    que dependem dela (renda, quedas, tendência, capital final) ficam None.
    """
    v = float(capital)
    anos, faltam = [], []
    for t in sorted(linhas, key=lambda r: int(r["safra"])):
        s = int(t["safra"])
        y, yc, vp = t.get("rendimento"), t.get("renda_caixa"), t.get("valorizacao")
        custo = float(t.get("fator_custo", 1.0))
        ret = None if t.get("retorno") is None else float(t["retorno"])
        info = {"rendimento": y, "renda_caixa": yc, "valorizacao": vp,
                "fator_custo": custo, "retorno": ret}
        if yc is None or vp is None or v is None:
            if yc is None or vp is None:
                faltam.append(s)
            anos.append({"safra": s, "capital_inicio": v, "renda": None, **info})
            v = None
            continue
        yc, vp = float(yc), float(vp)
        anos.append({"safra": s, "capital_inicio": v, "renda": v * yc, **info})
        v = v * (1.0 + vp) * custo
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
