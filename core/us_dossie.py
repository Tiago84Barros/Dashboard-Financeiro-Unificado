"""
core/us_dossie.py
Dossiê determinístico por empresa (EUA) — espelha core/dossie_b3.

Tudo que é NÚMERO é calculado em código (core.us_metrics). A classificação e as
red flags são regras determinísticas e testáveis. Um LLM pode narrar depois, mas
não recalcula nem inventa métricas (dossie_to_text serializa o dossiê pronto).

Classes: consolidada | crescimento | turnaround | ciclica | assimetrica | inadequada.
Coberto por tests/test_us_dossie.py.
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence

from core.severidade_flags import (
    SEVERIDADE_COBERTURA,
    SEVERIDADE_CONTEXTO,
    SEVERIDADE_RISCO,
    agrupa_flags_por_severidade,
)
from core.us_metrics import compute_company_metrics

logger = logging.getLogger("us_dossie")

# setores estruturalmente cíclicos (pista; a decisão pondera fundamentos)
_CYCLICAL_SECTORS = frozenset({"Energy", "Basic Materials", "Materials", "Industrials"})


def _crescimento_de_receita(m: dict) -> tuple[float | None, str]:
    """Taxa de crescimento da receita e o nome da conta que a produziu.

    Preferência pela inclinação da regressão log-linear de 3 anos: o CAGR só
    olha as duas pontas, então uma receita que sobe, despenca e volta recebe a
    mesma taxa de uma que sobe todo ano — e a classe da empresa muda com isso.

    O CAGR fica como recuo quando a regressão não sai (série com valor não
    positivo, que o log não aceita). O recuo não é silencioso: o motivo exibido
    e o texto do dossiê dizem qual das duas contas respondeu, porque os limiares
    de 25% e 12% foram calibrados na taxa composta e as duas não são a mesma
    grandeza.
    """
    t = m.get("revenue_trend_3y")
    if t is not None:
        return t, "inclinação da regressão de 3 anos"
    c = m.get("revenue_cagr_3y")
    if c is not None:
        return c, "CAGR de 3 anos, sem regressão disponível"
    return None, "sem série de receita"


def classify_company(m: dict, sector: str | None = None) -> tuple[str, str]:
    """Classifica a empresa a partir do snapshot de métricas. (classe, motivo)."""
    years = m.get("_years") or 0
    net_income = m.get("_net_income")
    fcf = m.get("_fcf")
    equity = m.get("_equity")

    if years < 3 or net_income is None or equity is None:
        return "inadequada", "histórico/dados insuficientes para análise confiável"
    if equity < 0:
        return "inadequada", "patrimônio líquido negativo"

    g, base_g = _crescimento_de_receita(m)
    op_margin = m.get("operating_margin")
    net_margin = m.get("net_margin")
    ndte = m.get("net_debt_ebitda")
    roic = m.get("roic")

    # Assimétrica: crescimento alto e persistente + rentabilidade operacional + baixa alavancagem
    if (g is not None and g >= 0.25 and (op_margin is None or op_margin > 0)
            and (ndte is None or ndte < 3)):
        return "assimetrica", (f"crescimento de receita ~{g*100:.0f}%/ano "
                               f"({base_g}) com operação rentável")

    # Turnaround: prejuízo recente virando (margem líquida negativa ou muito baixa,
    # mas FCF ou operação melhorando)
    if net_margin is not None and net_margin < 0:
        if fcf is not None and fcf > 0:
            return "turnaround", "prejuízo contábil, porém geração de caixa positiva"
        return "inadequada", "prejuízo persistente sem geração de caixa"

    # Crescimento: crescimento sólido, rentável
    if g is not None and g >= 0.12 and (net_margin is None or net_margin > 0):
        return "crescimento", (f"crescimento de receita ~{g*100:.0f}%/ano "
                               f"({base_g}) rentável")

    # Cíclica: setor cíclico e margem/retorno voláteis (proxy: baixo ROIC atual)
    if sector in _CYCLICAL_SECTORS and (roic is None or roic < 0.10):
        return "ciclica", f"setor cíclico ({sector}) com retorno sobre capital moderado/baixo"

    # Consolidada: rentável, caixa positivo, alavancagem controlada
    if (net_margin is not None and net_margin > 0 and (fcf is None or fcf > 0)
            and (ndte is None or ndte < 3)):
        return "consolidada", "rentável, gera caixa e alavancagem sob controle"

    return "inadequada", "perfil não se encaixa em tese clara com os dados atuais"


def _linha_de_persistencia(p, texto: str) -> str | None:
    """Uma linha de red flag QUALIFICADA pela frequência medida no histórico.

    A condição que nunca acendeu não vira linha. A que acendeu vira sempre —
    nada é silenciado — mas dizendo em quantos exercícios acendeu, e sob qual
    severidade (ver core/severidade_flags.py):

      - maioria estrita da janela avaliável, com 3+ exercícios → risco confirmado;
      - acendeu, sem maioria                                    → CONTEXTO:;
      - menos de 3 exercícios avaliáveis                        → COBERTURA:.

    O número do último exercício sai do texto de propósito: a grandeza aqui é
    a frequência. O valor corrente continua no bloco de métricas do dossiê.
    """
    if p.acesos <= 0:
        return None
    quando = f"em {p.acesos} dos últimos {p.anos} exercícios"
    if not p.julgavel:
        return (f"COBERTURA: {texto} {quando} — série curta demais para "
                f"separar episódio isolado de padrão.")
    if p.maioria:
        # Padrão que cessou é padrão observado; a ressalva vai junto, no lugar
        # de virar silêncio — foi o defeito oposto que este módulo corrige.
        if not p.acende_no_ultimo:
            return f"{texto} {quando} (não no último exercício)."
        return f"{texto} {quando}."
    if p.acesos == 1 and p.acende_no_ultimo:
        return f"CONTEXTO: {texto} apenas no último exercício, {quando}."
    return f"CONTEXTO: {texto} {quando} — sem padrão na janela."


def _acende_na_foto(nome: str, m: dict) -> bool:
    """A condição acende na FOTO do dossiê (as métricas de `compute_company_metrics`)?

    Esta é a regra que valia até 15/09/2026 para todas as linhas. Ela sobrevive
    aqui com um papel estreito e específico: rede de segurança. `compute_company_metrics`
    monta cada métrica com `_latest`, que aceita o valor não nulo mais recente
    ainda que venha de um exercício anterior; a medição por ano, não. Quando
    nenhum exercício da janela é avaliável e mesmo assim a foto acusa a
    condição, a linha PRECISA aparecer — como limitação de cobertura, porque a
    frequência é justamente o que não foi possível apurar.
    """
    if nome == "alavancagem":
        v = m.get("net_debt_ebitda")
        return v is not None and v > 4
    if nome == "cobertura_juros":
        v = m.get("interest_coverage")
        return v is not None and v < 2
    if nome == "fcf_negativo":
        v = m.get("_fcf")
        return v is not None and v < 0
    if nome == "conversao_caixa":
        v = m.get("cash_conversion")
        return v is not None and v < 0.5 and (m.get("_net_income") or 0) > 0
    if nome == "divida_patrimonio":
        v = m.get("debt_to_equity")
        return v is not None and v > 2
    if nome == "patrimonio_negativo":
        v = m.get("_equity")
        return v is not None and v < 0
    return False


def red_flags(m: dict, income: Sequence[dict] = (),
              balance: Sequence[dict] = (),
              cashflow: Sequence[dict] = ()) -> list[str]:
    """Sinais de alerta determinísticos, medidos no HISTÓRICO da empresa.

    Até 15/09/2026 cada linha era uma leitura do último exercício em ``m`` e
    saía idêntica — palavra por palavra — para quem teve um ano ruim em cinco e
    para quem teve cinco em cinco. Quem decide qual dos dois é o caso agora é
    `core.us_risco_historico`, sobre as séries anuais.

    ``m`` continua entrando como rede de segurança (ver `_acende_na_foto`):
    condição que a foto acusa e que nenhum exercício da janela conseguiu
    avaliar sai como COBERTURA:, nunca como silêncio.

    Regra preservada: condição sem dado nenhum não gera linha.
    """
    from core import us_risco_historico as hist

    persistencias = hist.avalia(income, balance, cashflow)
    flags: list[str] = []
    for nome, _cond, texto in hist.CONDICOES:
        persistencia = persistencias.get(nome)
        if persistencia is None:
            if _acende_na_foto(nome, m):
                flags.append(
                    f"COBERTURA: {texto} no último dado disponível — nenhum "
                    f"exercício da janela permitiu apurar com que frequência "
                    f"a condição se repete.")
            continue
        linha = _linha_de_persistencia(persistencia, texto)
        if linha:
            flags.append(linha)
    return flags


def investment_notes(m: dict, label: str) -> dict:
    """Tese/condições de invalidação determinísticas por classe."""
    tese, invalidacao = [], []
    if label in ("crescimento", "assimetrica"):
        tese.append("Crescimento de receita acima da média com operação rentável.")
        invalidacao.append("Desaceleração persistente da receita.")
        invalidacao.append("Deterioração de margem operacional.")
        invalidacao.append("Aumento relevante de dívida ou diluição.")
    elif label == "consolidada":
        tese.append("Rentabilidade estável, geração de caixa e retorno ao acionista.")
        invalidacao.append("Queda estrutural de margem ou ROIC.")
        invalidacao.append("Alavancagem subindo sem retorno correspondente.")
    elif label == "turnaround":
        tese.append("Recuperação operacional em curso (caixa antes do lucro contábil).")
        invalidacao.append("Caixa operacional volta a ficar negativo.")
        invalidacao.append("Necessidade de capital externo (diluição/dívida).")
    elif label == "ciclica":
        tese.append("Exposição a ciclo setorial; avaliar no ponto do ciclo.")
        invalidacao.append("Queda de demanda/preços do setor.")
    else:
        tese.append("Sem tese clara com os dados atuais.")
    return {"tese": tese, "condicoes_invalidacao": invalidacao}


def assemble_dossie(symbol: str, *, name: str | None, sector: str | None,
                    industry: str | None, income: Sequence[dict],
                    balance: Sequence[dict], cashflow: Sequence[dict],
                    price: Optional[float] = None,
                    market_cap: Optional[float] = None,
                    score_row: Optional[dict] = None) -> dict:
    """Monta o dossiê determinístico (puro) de uma empresa."""
    m = compute_company_metrics(income, balance, cashflow, price=price,
                                market_cap=market_cap)
    label, motivo = classify_company(m, sector)
    return {
        "symbol": (symbol or "").upper(),
        "name": name, "sector": sector, "industry": industry,
        "classification": label, "classification_reason": motivo,
        "metrics": m,
        "red_flags": red_flags(m, income, balance, cashflow),
        "notes": investment_notes(m, label),
        "score": (score_row or {}).get("score"),
        "series_years": m.get("_years"),
    }


def dossie_to_text(d: dict) -> str:
    """Serializa o dossiê para narração por LLM (sem recálculo de números)."""
    from core.market_companies import translate_us_industry, translate_us_sector

    if d.get("erro"):
        return f"DOSSIÊ INDISPONÍVEL: {d['erro']}"
    m = d.get("metrics", {})
    setor = translate_us_sector(d.get("sector"), d.get("industry"))
    industria = translate_us_industry(d.get("industry") or d.get("sector"))

    def pct(x):
        return "—" if x is None else f"{x*100:.1f}%"

    def num(x, mult=False):
        return "—" if x is None else (f"{x:.2f}" if not mult else f"{x:.2f}×")

    L = [
        f"EMPRESA: {d['symbol']} — {d.get('name')} | Setor: {setor} / {industria}",
        f"CLASSIFICAÇÃO: {d.get('classification')} ({d.get('classification_reason')})"
        + (f" | Pontuação: {d['score']}" if d.get("score") is not None else ""),
        "\nQUALIDADE — margem bruta {} | operacional {} | líquida {} | FCF {} | "
        "ROE {} | ROIC {}".format(
            pct(m.get("gross_margin")), pct(m.get("operating_margin")),
            pct(m.get("net_margin")), pct(m.get("fcf_margin")),
            pct(m.get("roe")), pct(m.get("roic"))),
        # Cada taxa carrega a conta que a produziu. Regressão, CAGR e taxa
        # simétrica respondem à mesma pergunta com aritméticas diferentes, e um
        # rótulo só de horizonte ("3a") faria o leitor — humano ou LLM —
        # comparar números que não são comparáveis.
        "CRESCIMENTO — receita 3a (regressão) {} | receita 5a (regressão) {} "
        "[R²={}] | receita 5a (CAGR ponta a ponta) {} | lucro op. 3a (simétrica) "
        "{} | LPA 3a (simétrica) {} | FCL 3a (simétrica) {}".format(
            pct(m.get("revenue_trend_3y")), pct(m.get("revenue_trend_5y")),
            num(m.get("revenue_trend_r2_5y")), pct(m.get("revenue_cagr_5y")),
            pct(m.get("op_income_trend_3y")), pct(m.get("eps_trend_3y")),
            pct(m.get("fcf_trend_3y"))),
        "SOLIDEZ — dív.líq/EBITDA {} | cobertura juros {} | liquidez corrente {}".format(
            num(m.get("net_debt_ebitda"), True), num(m.get("interest_coverage"), True),
            num(m.get("current_ratio"), True)),
        "AVALIAÇÃO — P/L {} | EV/EBIT {} | EV/EBITDA {} | P/FCL {} | retorno do FCL {}".format(
            num(m.get("pe"), True), num(m.get("ev_ebit"), True),
            num(m.get("ev_ebitda"), True), num(m.get("p_fcf"), True),
            pct(m.get("fcf_yield"))),
        # O dividend yield é derivado do `dividends_paid` do EDGAR sobre o valor
        # de mercado — não da tabela FMP, cujos termos proíbem armazenamento.
        # Ele se refere ao ÚLTIMO EXERCÍCIO FECHADO: defasa até um ano do
        # dividendo corrente, e a LLM precisa disso para não datar errado.
        # "N/D" aqui é ausência de dado, nunca não-pagamento: dos símbolos sem
        # a linha no último exercício, 42% pagaram dividendo nos 12 meses.
        "RETORNO AO ACIONISTA — retorno total ao acionista {} | dividend yield "
        "do último exercício {} | dividendo por ação {} | payout {}".format(
            pct(m.get("shareholder_yield")), pct(m.get("dividend_yield")),
            num(m.get("dividends_per_share")), pct(m.get("payout_ratio"))),
    ]
    # As três categorias saem APARTADAS. Sob um cabeçalho único, as 2.419 de
    # 3.737 empresas que acendem alguma linha chegavam à LLM com a mesma cara
    # — inclusive aquelas cujo próprio texto declara não ser padrão. Quem lê o
    # cabeçalho antes da linha é quem decide.
    grupos = agrupa_flags_por_severidade(d.get("red_flags"))
    if grupos[SEVERIDADE_RISCO]:
        L.append("\nSINAIS DE ALERTA — RISCO CONFIRMADO NO HISTÓRICO "
                 "(verificados em código, não são opinião):")
        L.extend(f"  - {f}" for f in grupos[SEVERIDADE_RISCO])
    if grupos[SEVERIDADE_CONTEXTO]:
        L.append("\nOBSERVAÇÕES DE CONTEXTO (a condição foi medida em código "
                 "sobre janela suficiente e JÁ DESCARTADA como padrão — NÃO "
                 "são sinais de alerta, NÃO são risco confirmado e NÃO "
                 "justificam veto nem ressalva por si sós):")
        L.extend(f"  - {f}" for f in grupos[SEVERIDADE_CONTEXTO])
    if grupos[SEVERIDADE_COBERTURA]:
        L.append("\nLIMITAÇÕES DE COBERTURA (série curta demais para separar "
                 "episódio de padrão — ausência de prova não é prova de risco; "
                 "entram em qualidade_dados como lacuna, nunca como risco):")
        L.extend(f"  - {f}" for f in grupos[SEVERIDADE_COBERTURA])
    notes = d.get("notes", {})
    if notes.get("tese"):
        L.append("\nTESE:")
        L.extend(f"  - {t}" for t in notes["tese"])
    if notes.get("condicoes_invalidacao"):
        L.append("CONDIÇÕES DE INVALIDAÇÃO:")
        L.extend(f"  - {c}" for c in notes["condicoes_invalidacao"])
    return "\n".join(L)


def build_dossie(symbol: str) -> dict:
    """Dossiê a partir do warehouse local (best-effort; nunca levanta para a UI)."""
    sym = (symbol or "").strip().upper()
    try:
        import core.us_read as ur
        bundle = ur.load_company_bundle(sym)
        if not bundle or not bundle.get("income"):
            return {"symbol": sym, "erro": "sem dados locais para esta empresa"}
        return assemble_dossie(
            sym, name=bundle.get("name"), sector=bundle.get("sector"),
            industry=bundle.get("industry"), income=bundle["income"],
            balance=bundle.get("balance", []), cashflow=bundle.get("cashflow", []),
            price=bundle.get("price"), market_cap=bundle.get("market_cap"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("build_dossie(%s) falhou: %s", sym, exc)
        return {"symbol": sym, "erro": str(exc)[:300]}
