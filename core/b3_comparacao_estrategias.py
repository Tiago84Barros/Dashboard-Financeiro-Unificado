"""
core/b3_comparacao_estrategias.py — régua única da comparação das estratégias
B3 (§29 do guia de metodologia), fixada em 26/11/2026 ANTES de medir.

A pergunta do usuário: qual estratégia vira o modelo padrão da Criação de
Portfólio B3, sabendo que o padrão tem de render na **valorização** das ações
e no **crescimento contínuo dos dividendos**. As §19–§28 mediram cada
estratégia só pelo retorno total contra pesos iguais; esta régua acrescenta a
renda e a valorização separadas, com a mesma janela e os mesmos dados
corrigidos (leitor da CVM do #805, preços limpos em 26/11/2026).

**Renda e valorização de cada papel na safra** (`rendimentos_do_pregao`,
régua 1.3.0, 27/11/2026): saem do pregão **diário** bruto do COTAHIST
(`market.b3_security_history`) e dos proventos por data com, pelo motor da
Carteira 20 (`core.carteira20_retornos.calcular(componentes=True)`), que
devolve por dia o fechamento, ``k`` (ações novas por antiga: desdobramento,
grupamento, bonificação) e o caixa por ação. Quem compra na véspera da
vigência (31 de março) e gasta os proventos acumula ``n`` ações e recebe
``n × caixa``; daí saem três números por papel:

- ``caixa``: o provento recebido em fração do capital do início;
- ``preco``: a valorização só pelo preço, ``n × P_fim / P_0 - 1``;
- ``fator``: o provento reinvestido, ``(1 + retorno) / (1 + preco) - 1``.

Os proventos vêm da foto mais nova da brapi (`core.proventos_b3`), que os
divide pelos desdobramentos, grupamentos e bonificações posteriores. O pregão
do COTAHIST é bruto, então o provento é multiplicado de volta pelos mesmos
eventos que a foto lista (`eventos_de_acoes`, `fator_de_desajuste`). Na
conferência de 27/11/2026 contra o motor (ticker-ano com bonificação), a
mediana da razão ficou em 1,03; sem o desajuste, a renda antiga sai menor. Os
proventos do banco que completam os anos sem foto (451 linhas, 31 com evento
depois) ficam como estão. O código da série segue as trocas de ticker
(`market.ticker_alias`), como na Carteira 20.

Até a régua 1.2.1 a renda saía da razão ajustado/bruto de
`market.historical_prices`, que é mensal até 2025 e mistura duas bases de
ajuste em 2026 (inventava provento na safra 2025); achados P1 e P2 da revisão
do PR #810.

- Ponta = o último pregão até a data, com tolerância de
  ``TOLERANCIA_PONTA_DIAS``; início = véspera da vigência, fim = último dia
  dela.
- Fator ou caixa fora de [``RENDIMENTO_MIN``, ``RENDIMENTO_MAX``) é tratado
  como dado ruim e sai (não vira zero); entre ``RENDIMENTO_MIN`` e 0 vira 0.
- Papel sem rendimento é tirado e os pesos dos outros são renormalizados; a
  cobertura (fração do peso com dado) vai para a saída.

**Valorização da carteira** (régua 1.2.0): a valorização só pelo preço é
calculada **papel a papel** e só depois ponderada (`linha_de_renda`). Dividir
o retorno total da carteira pelo fator médio (régua 1.1.0) não isola o preço
quando os papéis têm valorização e proventos diferentes: com pesos iguais, A
+100% sem provento e B 0% com fator de 20% dão 145,45 em vez de 150. O custo
de transação entra como ``(1 + líquido) / (1 + bruto)`` da própria régua.

**Custo antes da renda** (régua 1.2.1): o custo de transação sai no
rebalanceamento de abril, antes do ano (como em `simular_custos`); a renda do
ano é sobre o capital já investido, ``capital × fator_custo``, e não sobre o
capital de antes do custo (achado da terceira revisão do PR #810).

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
excesso médio. Se nenhuma passa, o padrão da tela fica, e a
saída diz qual regra cada estratégia não cumpriu. A renda total e as
pareadas contra a nota + fatores e contra a líder de segmento são
informativas. Nove safras é pouco: a regra pode errar nos dois sentidos, e a
saída diz isso.

Decisão do usuário de 26/11/2026, confirmada em 27/11/2026: a ``rota_valor``
inteira (+16,3 pp) é a padrão da tela (`views/portfolio_b3_rota_valor.py`),
fora da regra. Na régua 1.2.1 a regra escolhia a ``rota_valor_top20``; na
1.3.0 a rota falha a R2 por uma queda de renda a mais e a regra escolheria a
``nota_fatores``. A regra continua gravando a sua escolha em ``escolha``; a do
usuário vai em ``decisao_do_usuario``.

Funções puras: nada de banco nem de Streamlit.
"""
from __future__ import annotations

import math
from datetime import date

VERSAO = "1.3.0"

CAPITAL_INICIAL = 100_000.0
TOLERANCIA_PONTA_DIAS = 45
RENDIMENTO_MIN = -0.05
RENDIMENTO_MAX = 1.0
# Eventos de ações da brapi que mudam a quantidade por ação antiga. Os
# proventos da foto vêm divididos por eles (os posteriores à data com); o
# resto da lista (resgate, subscrição) não ajusta provento.
ROTULOS_DE_ACOES = frozenset({"DESDOBRAMENTO", "BONIFICACAO", "GRUPAMENTO"})


def _data(v) -> date | None:
    """date de date, datetime, Timestamp ou texto ISO; None se ilegível."""
    if v is None:
        return None
    if isinstance(v, date):  # date, datetime e Timestamp (NaT sai None)
        try:
            return date(v.year, v.month, v.day)
        except (TypeError, ValueError):
            return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def eventos_de_acoes(dividends_data) -> list[tuple[date, float]]:
    """[(data com, fator)] dos desdobramentos, grupamentos e bonificações.

    ``dividends_data`` é o ``dividendsData`` de uma foto da brapi. Fica só o
    rótulo de :data:`ROTULOS_DE_ACOES`, com fator positivo e diferente de 1,
    sobre o papel dos proventos (o ``isinCode`` mais comum de
    ``cashDividends``): o resgate de um recibo de subscrição (outro ISIN) não
    mexe no provento da ação. ``fator`` é ações novas por antiga (5 no
    desdobramento 5 para 1, 0,008 no grupamento 1 para 125).
    """
    dd = dividends_data or {}
    isins: dict = {}
    for x in dd.get("cashDividends") or []:
        i = (x or {}).get("isinCode")
        if i:
            isins[i] = isins.get(i, 0) + 1
    isin = max(isins, key=isins.get) if isins else None
    saida = []
    for x in dd.get("stockDividends") or []:
        x = x or {}
        if str(x.get("label") or "").strip().upper() not in ROTULOS_DE_ACOES:
            continue
        if isin and x.get("assetIssued") and x["assetIssued"] != isin:
            continue
        d = _data(x.get("lastDatePrior"))
        try:
            f = float(x.get("factor"))
        except (TypeError, ValueError):
            continue
        if d is not None and math.isfinite(f) and f > 0 and f != 1.0:
            saida.append((d, f))
    return sorted(saida)


def fator_de_desajuste(eventos, data_com) -> float:
    """Π dos fatores com data com depois da do provento (1 sem nenhum).

    A brapi divide o provento antigo por cada desdobramento, grupamento e
    bonificação que veio depois, para a ação de hoje. O pregão do COTAHIST é
    o bruto do dia: provento bruto = provento da foto × este fator. O evento
    na mesma data com do provento não conta (o provento é da ação antiga).
    """
    d = _data(data_com)
    if d is None:
        return 1.0
    f = 1.0
    for de, fe in eventos or []:
        if _data(de) is not None and _data(de) > d:
            f *= float(fe)
    return f


def rendimentos_do_pregao(linhas) -> dict | None:
    """Fator, caixa e valorização de um papel na safra, do pregão diário.

    ``linhas``: [(preco, ret, k, caixa), ...] em ordem de data, da ponta
    inicial (véspera da vigência; dela só conta o ``preco``) à final, como
    ``core.carteira20_retornos.calcular(componentes=True)`` devolve: ``preco``
    o fechamento bruto, ``k`` ações novas por antiga no dia, ``caixa`` o
    dinheiro por ação antiga e ``1 + ret = (k × preco + caixa) / P_{t-1}``.
    Quem compra uma ação na ponta inicial e gasta os proventos tem ``n`` ações
    (``n ×= k``) e recebe ``n × caixa`` em cada dia. Devolve
    ``{"fator", "caixa", "preco"}`` ou None (dado ruim):

    - ``preco`` = ``n × P_fim / P_0 - 1``, a valorização só pelo preço;
    - ``caixa`` = ``Σ n × caixa / P_0``, o provento recebido em fração do
      capital do início;
    - ``fator`` = ``Π (1 + ret) / (1 + preco) - 1``, o provento reinvestido:
      ``1 + retorno = (1 + valorização) × (1 + fator)``.

    ``ret`` vazio (salto sem evento) conta zero; o motor já põe ``k`` que
    deixa a posição parada nesse dia. Fator ou caixa fora de
    [``RENDIMENTO_MIN``, ``RENDIMENTO_MAX``) sai como dado ruim; entre
    ``RENDIMENTO_MIN`` e 0 vira 0.
    """
    try:
        pts = [(float(p), r, float(k), float(c)) for p, r, k, c in linhas]
    except (TypeError, ValueError):
        return None
    if len(pts) < 2:
        return None
    p0, p1 = pts[0][0], pts[-1][0]
    if not (math.isfinite(p0) and math.isfinite(p1) and p0 > 0 and p1 > 0):
        return None
    n, caixa, total = 1.0, 0.0, 1.0
    for _, r, k, c in pts[1:]:
        if not (math.isfinite(k) and k > 0 and math.isfinite(c)):
            return None
        caixa += n * c
        n *= k
        try:
            r = float(r)
        except (TypeError, ValueError):
            r = 0.0
        total *= 1.0 + (r if math.isfinite(r) else 0.0)
    preco = n * p1 / p0 - 1.0
    fator = total / (1.0 + preco) - 1.0
    caixa /= p0
    if not (RENDIMENTO_MIN <= fator < RENDIMENTO_MAX
            and RENDIMENTO_MIN <= caixa < RENDIMENTO_MAX):
        return None
    return {"fator": max(0.0, fator), "caixa": max(0.0, caixa), "preco": preco}


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
    `rendimentos_do_pregao` | None}. Fator, caixa e valorização são ponderados
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
    ``rendimento`` (o fator reinvestido) só informam. O custo sai no
    rebalanceamento de abril, antes do ano (como em
    `core.b3_oos_carteira.simular_custos`): o capital investido é
    ``capital × fator_custo``, a renda do ano é ``investido × renda_caixa`` e
    o capital cresce só pelo preço, ``investido × (1 + valorizacao)``.

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
        investido = v * custo
        anos.append({"safra": s, "capital_inicio": v, "capital_investido": investido,
                     "renda": investido * yc, **info})
        v = investido * (1.0 + vp)
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
