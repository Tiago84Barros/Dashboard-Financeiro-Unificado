"""
core/b3_portao_pit.py — o parecer do portão de LLM como ele seria em março/N.

A banda do OOS da carteira (`core/b3_oos_carteira.py`, `PORTAO_LLM`) limita o
efeito de um portão hipotético que vete até um nome por safra. Este módulo
existe para medir o portão DE VERDADE: o mesmo prompt de
`core.dossie_b3.gerar_parecer_empresa`, o mesmo LLM, sobre um dossiê
reconstruído com o que se sabia na data de corte da safra (31/03/N).

Duas ameaças de look-ahead, tratadas de formas diferentes:

1. O DOSSIÊ. O de produção lê o estado de hoje. Aqui cada bloco é cortado na
   data: DRE/balanço/DFC anuais até o exercício N-1, trimestres até N-1,
   preços e proventos até 31/03/N, valor de mercado ESTIMADO pelo de hoje
   vezes a razão de preços (o estimador de `core.b3_universo_pit`). O que não
   tem histórico -- documentos CVM indexados, notícias, métricas do provedor,
   detalhe do armazém -- fica FORA e é declarado no próprio dossiê como
   limitação de cobertura. Ou seja: o portão medido vê MENOS que o de
   produção, e isso entra na leitura do resultado.

2. A MEMÓRIA DO MODELO. O LLM foi treinado depois das safras e pode saber o
   que aconteceu com a empresa. O dossiê sai anonimizado: código e nome
   trocados, anos relativos ("A-1" = último exercício fechado), valores em R$
   e por ação multiplicados por fatores determinísticos ocultos (razões,
   margens, múltiplos e variações ficam reais), e só o setor amplo fica --
   subsetor e segmento ocultos, porque bastavam para nomear a empresa. A anonimização não garante
   nada -- setor e trajetória podem bastar --, então `PROMPT_SONDA` pergunta
   ao próprio modelo qual empresa e qual ano ele acha que é, e a taxa de
   acerto vai para a medição.

Tudo aqui é leitura; nada é gravado em banco.
"""
from __future__ import annotations

import hashlib
import math
import threading
from datetime import date

VERSAO_DOSSIE_PIT = "dossie-pit-1.2.0"

#: Faixa dos fatores de escala (log-uniforme). Larga o bastante para o porte
#: não identificar a empresa, estreita o bastante para não mudar de ordem de
#: grandeza (receita de R$ 10 bi não vira R$ 100 mi).
FAIXA_ESCALA = (0.5, 2.0)

_CAMPOS_MI = ("receita_mi", "ebit_mi", "ebitda_mi", "lucro_mi", "pl_mi", "caixa_mi",
              "div_bruta_mi", "div_liq_mi", "fco_mi")

COBERTURA_PIT = (
    "COBERTURA: dossiê HISTÓRICO reconstruído na data da decisão. Os anos são "
    "relativos a ela (A = ano da decisão; A-1 = último exercício fechado). "
    "Código, nome e escala foram ocultados de propósito: valores em R$ e por "
    "ação estão multiplicados por fatores constantes não informados; razões, "
    "margens, múltiplos, yields e variações são os reais.",
    "COBERTURA: sem documentos CVM, notícias, métricas do provedor e detalhe "
    "do armazém da época -- o acervo histórico não cobre a data da decisão.",
)


def data_de_corte(safra: int) -> date:
    """O que se sabia quando a carteira da safra foi montada (abril/N)."""
    return date(int(safra), 3, 31)


def _unitario(chave: str) -> float:
    return int(hashlib.sha256(chave.encode("utf-8")).hexdigest()[:13], 16) / 16 ** 13


def fator_escala(chave: str, faixa: tuple[float, float] = FAIXA_ESCALA) -> float:
    """Fator determinístico, log-uniforme em ``faixa``, a partir de ``chave``."""
    lo, hi = math.log(faixa[0]), math.log(faixa[1])
    return math.exp(lo + (hi - lo) * _unitario(chave))


def codigo_anonimo(tk: str, safra: int) -> str:
    """Código estável por (ticker, safra): a mesma empresa em safras
    diferentes recebe códigos diferentes, para o modelo não encadear anos."""
    return "EMPRESA-" + hashlib.sha256(f"cod|{tk}|{safra}".encode()).hexdigest()[:6].upper()


def rotulo_ano(ano, safra: int) -> str:
    """Ano absoluto → relativo à decisão: N → "A", N-1 → "A-1"."""
    d = int(safra) - int(ano)
    if d < 0:
        raise ValueError(f"ano {ano} posterior à safra {safra}: vazamento de futuro")
    return "A" if d == 0 else f"A-{d}"


def _esc(v, k: float, casas: int = 1):
    return None if v is None else round(float(v) * k, casas)


def anonimizar(bruto: dict, tk: str, safra: int) -> dict:
    """Dossiê no formato de `dossie_b3.dossie_to_text`, sem identidade.

    ``bruto`` sai de `coletar_dossie_pit` (anos absolutos, valores reais).
    As red flags e a regra de juros são calculadas DEPOIS da anonimização,
    para que nenhum valor absoluto real chegue ao texto."""
    from core.dossie_b3 import _checks, _sensibilidade_juros

    f = fator_escala(f"rs|{tk}|{safra}")
    g = fator_escala(f"ps|{tk}|{safra}")

    serie = []
    for s in bruto.get("serie_anual") or []:
        linha = {**s, "ano": rotulo_ano(s["ano"], safra)}
        for c in _CAMPOS_MI:
            linha[c] = _esc(s.get(c), f)
        linha["lpa"] = _esc(s.get("lpa"), g, 4)
        serie.append(linha)

    tris_b = bruto.get("trimestres") or {}
    tris = {"serie": [{**t, "ano": rotulo_ano(t["ano"], safra),
                       "receita_mi": _esc(t.get("receita_mi"), f),
                       "lucro_mi": _esc(t.get("lucro_mi"), f)}
                      for t in tris_b.get("serie") or []],
            "yoy": dict(tris_b.get("yoy") or {})}
    if tris["yoy"].get("ref"):
        ult, ant = (tris_b["serie"][-1], tris_b["serie"][-5])
        tris["yoy"]["ref"] = (f"{rotulo_ano(ult['ano'], safra)}T{ult['tri']} vs "
                              f"{rotulo_ano(ant['ano'], safra)}T{ant['tri']}")

    dv_b = bruto.get("dividendos") or {}
    dv = {**dv_b,
          "por_ano": {rotulo_ano(a, safra): round(v * g, 4)
                      for a, v in (dv_b.get("por_ano") or {}).items()},
          "ult_12m_ps": _esc(dv_b.get("ult_12m_ps"), g, 4),
          "devolucao_capital_12m_ps": _esc(dv_b.get("devolucao_capital_12m_ps"), g, 4)}

    v_b = bruto.get("valuation") or {}
    val = {**v_b,
           "market_cap_mi": _esc(v_b.get("market_cap_mi"), f),
           "preco": _esc(v_b.get("preco"), g, 2),
           "min_52s": _esc(v_b.get("min_52s"), g, 2),
           "max_52s": _esc(v_b.get("max_52s"), g, 2),
           "data_preco": "data da decisão" if v_b.get("data_preco") else None,
           "ano_base_valuation": (rotulo_ano(v_b["ano_base_valuation"], safra)
                                  if v_b.get("ano_base_valuation") else None)}

    # `_checks` ordena a série pelo ano (`fracao_pl_em_queda_com_lucro`):
    # recebe o ano NUMÉRICO relativo; nenhuma frase dele imprime o ano da série.
    serie_num = [{**s, "ano": int(safra) - (int(s["ano"][2:]) if s["ano"] != "A" else 0)}
                 for s in serie]
    docs = {"n_docs": 0}
    flags = _checks(serie_num, tris, dv, {}, docs, val) + list(COBERTURA_PIT)
    if bruto.get("fonte") == "dfp_cvm_saida":
        flags += list(COBERTURA_SAIDA)
    return {
        "ticker": codigo_anonimo(tk, safra),
        "nome": "(anonimizada)",
        # Só o setor amplo: subsetor e segmento identificam a empresa (medido
        # na sonda: "Motores, Compressores e Outros" -> WEGE3, confiança 85).
        "setor": bruto.get("setor"), "subsetor": "(oculto)", "segmento": "(oculto)",
        "serie_anual": serie,
        "trimestres": tris,
        "dividendos": dv,
        "valuation": val,
        "metricas_banco": {},
        "sensibilidade_juros": _sensibilidade_juros(serie),
        "eventos_societarios": docs,
        "red_flags": flags,
        "atualidade": {},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Leitura (banco) — só os loaders do dossiê, com corte
# ─────────────────────────────────────────────────────────────────────────────

def _preco_bruto(tk: str, ref: date) -> tuple[float | None, str | None]:
    """Último fechamento NÃO ajustado (COTAHIST) até ``ref``.

    `historical_prices.close` vem ajustado por desdobramento até hoje, e o
    provento por ação do banco não: dividir um pelo outro daria DY errado em
    toda empresa que desdobrou depois da safra."""
    from core.dossie_b3 import _f, _rows, _table_exists
    if not _table_exists("market.b3_security_history"):
        return None, None
    rows = _rows(
        """
        SELECT trade_date, close_unitario FROM market.b3_security_history
        WHERE ticker = :t AND trade_date <= CAST(:ref AS date)
          AND close_unitario IS NOT NULL
        ORDER BY trade_date DESC, financial_volume DESC NULLS LAST LIMIT 1
        """,
        t=tk, ref=ref.isoformat(),
    )
    if not rows:
        return None, None
    return _f(rows[0]["close_unitario"]), str(rows[0]["trade_date"])


def coletar_dossie_pit(tk: str, safra: int) -> dict:
    """Dossiê com anos e valores REAIS, cortado em `data_de_corte(safra)`."""
    from core.b3_vigencia import ano_base_do_score
    from core.dossie_b3 import (
        _dividendos,
        _ident,
        _market_cap,
        _precos,
        _series_anuais,
        _trimestres,
        _valuation,
    )

    tk = str(tk).strip().upper()
    corte = data_de_corte(safra)
    ano_base = ano_base_do_score(safra)
    serie = _series_anuais(tk, ate_ano=ano_base)
    if not serie:
        # Quem saiu da bolsa não tem DRE no market.*: entra pela DFP da CVM,
        # a mesma fonte com que o score o reconstruiu (`core.b3_saidas`).
        # Sem isto, o portão medido nunca vetaria justamente as saídas.
        saida = coletar_dossie_saida_pit(tk, safra)
        if saida is not None:
            return saida
    tris = _trimestres(tk, ate_ano=ano_base)
    precos = _precos(tk, ref=corte)

    # Preço exibido e DY na escala da época (ver `_preco_bruto`); retorno de
    # 12 meses e faixa de 52 semanas na razão, que o ajuste não altera.
    bruto, data_bruto = _preco_bruto(tk, corte)
    adj = precos.get("preco")
    # A série ajustada é mensal e a linha datada no dia 1 é o FECHAMENTO do
    # mês (medido: WEGE3 2019-03-01 = 9,00 = 18,00 / 2, o fechamento bruto de
    # 29/03/2019). O fator só vale entre preços do mesmo mês.
    perto = bool(data_bruto and precos.get("data_preco")
                 and data_bruto[:7] == str(precos["data_preco"])[:7])
    if bruto and adj and perto:
        k = bruto / adj
        precos = {**precos, "preco": bruto,
                  "min_52s": precos["min_52s"] * k if precos.get("min_52s") else None,
                  "max_52s": precos["max_52s"] * k if precos.get("max_52s") else None}
    divs = _dividendos(tk, precos.get("preco"), ref=corte)

    # Valor de mercado da época: o de hoje vezes a razão de preços ajustados
    # (estimador de `core.b3_universo_pit`); emissões e recompras posteriores
    # não entram.
    mcap = None
    hoje_mcap = _market_cap(tk)
    adj_hoje = _precos(tk).get("preco")
    if hoje_mcap and adj and adj_hoje:
        mcap = hoje_mcap * adj / adj_hoje

    ident = _ident(tk)
    return {
        "ticker": tk, "safra": int(safra), "corte": corte.isoformat(),
        "setor": ident.get("setor"), "subsetor": ident.get("subsetor"),
        "segmento": ident.get("segmento"),
        "serie_anual": serie, "trimestres": tris, "dividendos": divs,
        "valuation": _valuation(mcap, serie, precos),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Empresas que saíram da B3: dossiê pela DFP da CVM
# ─────────────────────────────────────────────────────────────────────────────

COBERTURA_SAIDA = (
    "COBERTURA: sem resultados trimestrais nesta reconstrução; proventos por "
    "ação estimados pelo caixa pago no ano (DFC) sobre as ações implícitas no "
    "LPA, por exercício civil.",
)


def _dir_cache_dfp():
    """``data/cache/cvm/dfp`` da árvore ou de uma ancestral (o cache é
    ignorado pelo git; num worktree ele só existe na árvore principal)."""
    from pathlib import Path
    raiz = Path(__file__).resolve().parents[1]
    for p in (raiz, *raiz.parents):
        d = p / "data" / "cache" / "cvm" / "dfp"
        if d.is_dir():
            return d
    return None


_DFP_TRAVA = threading.Lock()
_DFP_CACHE: dict[int, dict] = {}


def _dfps_das_saidas(ano: int) -> dict:
    """{cd_cvm: DemonstracaoAnual} do exercício ``ano``, só das saídas.

    Um zip por exercício, lido uma vez por processo (as threads do avaliador
    pedem o mesmo ano ao mesmo tempo)."""
    with _DFP_TRAVA:
        if ano not in _DFP_CACHE:
            from core import b3_saidas
            from data_pipeline.market import b3_saidas as bs
            d = _dir_cache_dfp()
            zp = d / f"dfp_cia_aberta_{ano}.zip" if d else None
            cds = {int(e["cd_cvm"]) for e in b3_saidas.empresas(b3_saidas.carregar())
                   if e.get("cd_cvm")}
            _DFP_CACHE[ano] = (bs.ler_dfp(zp.read_bytes(), ano, cds)
                               if zp is not None and zp.exists() and cds else {})
        return _DFP_CACHE[ano]


def _mi_r(v):
    return None if v is None else round(float(v) / 1e6, 1)


def serie_anual_dfp(dems: dict, classe: str, corte: date, ano_base: int,
                    max_anos: int = 12) -> list[dict]:
    """Série anual no formato de `dossie_b3._series_anuais`, da DFP.

    ``dems`` é {exercício: DemonstracaoAnual}. Só entra exercício até
    ``ano_base`` cuja DFP (primeira versão) foi recebida até ``corte`` --
    a DFP é a da época, sem reapresentação posterior."""
    from data_pipeline.market import b3_saidas as bs
    out: list[dict] = []
    for ano in sorted(dems):
        dem = dems[ano]
        recebida = getattr(dem, "available_at", None)
        if ano > int(ano_base):
            continue
        if recebida is not None and not _nulo(recebida) and recebida.date() > corte:
            continue
        f = bs.fundamentos(dem, classe)
        rec, ll, pl = f.get("revenue"), f.get("net_income"), f.get("equity")
        out.append({
            "ano": int(ano),
            "receita_mi": _mi_r(rec), "ebit_mi": _mi_r(f.get("ebit")),
            "ebitda_mi": None, "lucro_mi": _mi_r(ll),
            "pl_mi": _mi_r(pl), "caixa_mi": _mi_r(f.get("cash")),
            "div_bruta_mi": _mi_r(f.get("gross_debt")),
            "div_liq_mi": _mi_r(f.get("net_debt")),
            "fco_mi": _mi_r(f.get("fco")), "lpa": f.get("lpa"),
            "margem_liq_pct": round(ll / rec * 100, 1) if rec and ll is not None else None,
            "roe_pct": round(ll / pl * 100, 1) if pl and ll is not None else None,
            "_dividendos_pagos": f.get("dividendos_pagos"),
        })
    return out[-max_anos:]


def _nulo(v) -> bool:
    try:
        return bool(v != v)  # NaT/NaN
    except Exception:  # noqa: BLE001
        return False


def dividendos_dfp(serie: list[dict], preco: float | None) -> dict:
    """Provento por ação por exercício = dividendos+JCP pagos (DFC) / ações
    implícitas (lucro / LPA). ``ult_12m_ps`` é o do último exercício: o ano
    civil, não os 12 meses até o corte."""
    from data_pipeline.market.b3_saidas import LPA_MINIMO
    por_ano: dict[str, float] = {}
    for s in serie:
        pago, lpa, lucro = s.get("_dividendos_pagos"), s.get("lpa"), s.get("lucro_mi")
        if pago is None or lpa is None or lucro is None or abs(lpa) < LPA_MINIMO:
            continue
        acoes = lucro * 1e6 / lpa
        if acoes > 0:
            por_ano[str(s["ano"])] = round(pago / acoes, 4)
    ult = por_ano.get(str(serie[-1]["ano"])) if serie else None
    return {
        "por_ano": dict(sorted(por_ano.items())[-8:]),
        "ult_12m_ps": ult,
        "dy_12m_pct": round(ult / preco * 100, 1) if ult is not None and preco else None,
        "dy_12m_bruto_pct": None,
        "devolucao_capital_12m_ps": 0.0,
        "suspeita_duplicacao_classe": False,
        "n_eventos": len(por_ano),
    }


#: Mês com variação acima disto (ou abaixo do inverso) no índice de retorno
#: total, ou faixa de 52 semanas mais larga que isto, denuncia evento de
#: capital que a reconstrução não pegou (medido: grupamento da BRPR3 em
#: fev/2023, índice de 64 para 2.544). O número some em vez de ir errado.
SALTO_EVENTO = 3.0


def _precos_saida(tk: str, corte: date, indice_tr: dict) -> dict:
    """Preço não ajustado e faixa de 52 semanas do COTAHIST; retorno de 12
    meses pelo índice de retorno total mensal de `data/b3_saidas.json`.
    Faixa e retorno que atravessam evento de capital não detectado ficam
    ``None`` (ver `SALTO_EVENTO`)."""
    from datetime import timedelta

    from core.dossie_b3 import _f, _rows, _table_exists
    out: dict = {"preco": None, "data_preco": None, "ret_12m_pct": None,
                 "min_52s": None, "max_52s": None}
    preco, data = _preco_bruto(tk, corte)
    out["preco"], out["data_preco"] = preco, data
    if preco is not None and _table_exists("market.b3_security_history"):
        st = _rows(
            """
            SELECT MIN(close_unitario) AS mn, MAX(close_unitario) AS mx
            FROM market.b3_security_history
            WHERE ticker = :t AND trade_date <= CAST(:ref AS date)
              AND trade_date > CAST(:ini AS date) AND close_unitario IS NOT NULL
            """,
            t=tk, ref=corte.isoformat(), ini=(corte - timedelta(days=365)).isoformat(),
        )
        mn, mx = (_f(st[0]["mn"]), _f(st[0]["mx"])) if st else (None, None)
        if mn and mx and mx / mn <= SALTO_EVENTO:
            out["min_52s"], out["max_52s"] = mn, mx
    out["ret_12m_pct"] = retorno_12m_indice(indice_tr, corte)
    return out


def retorno_12m_indice(indice_tr: dict, corte: date) -> float | None:
    """Retorno de 12 meses até o mês do corte pelo índice mensal
    ``{"AAAA-MM": nível}``; ``None`` se faltar ponta ou se algum mês da
    janela saltar mais que `SALTO_EVENTO`."""
    meses = [f"{corte.year - 1 + (corte.month + i - 1) // 12}-"
             f"{(corte.month + i - 1) % 12 + 1:02d}" for i in range(13)]
    niveis = [indice_tr.get(m) for m in meses]
    if not niveis[0] or not niveis[-1]:
        return None
    validos = [v for v in niveis if v]
    for a, b in zip(validos, validos[1:]):
        if b / a > SALTO_EVENTO or a / b > SALTO_EVENTO:
            return None
    return round((niveis[-1] / niveis[0] - 1) * 100, 1)


def coletar_dossie_saida_pit(tk: str, safra: int) -> dict | None:
    """`coletar_dossie_pit` para quem saiu da B3; ``None`` se não for saída
    curada ou se não houver DFP até o corte."""
    from core import b3_saidas
    from core.b3_vigencia import ano_base_do_score
    from core.dossie_b3 import _valuation

    e = next((x for x in b3_saidas.empresas(b3_saidas.carregar())
              if x.get("ticker") == tk), None)
    if e is None or not e.get("cd_cvm"):
        return None
    corte = data_de_corte(safra)
    ano_base = ano_base_do_score(safra)
    cd = int(e["cd_cvm"])
    dems = {}
    for ano in range(int(ano_base) - 11, int(ano_base) + 1):
        dem = _dfps_das_saidas(ano).get(cd)
        if dem is not None:
            dems[ano] = dem
    serie = serie_anual_dfp(dems, tk[4:], corte, ano_base)
    if not serie:
        return None

    precos = _precos_saida(tk, corte, e.get("precos") or {})
    # Valor de mercado de 31/12 do último exercício (o de `b3_saidas`, ações
    # da DFP x fechamento) levado ao corte pela razão de preços não ajustados.
    vm = {int(f["ano"]): f.get("valor_mercado") for f in e.get("fundamentos") or []}
    mcap = vm.get(int(serie[-1]["ano"]))
    preco_dez, _ = _preco_bruto(tk, date(int(serie[-1]["ano"]), 12, 31))
    razao = (precos["preco"] / preco_dez) if preco_dez and precos.get("preco") else None
    # Três meses não multiplicam o preço por 3: razão assim é evento de
    # capital entre dez e o corte, e o valor de mercado sai em vez de sair errado.
    sem_evento = bool(razao) and 1 / SALTO_EVENTO <= razao <= SALTO_EVENTO
    mcap = float(mcap) * razao if mcap and sem_evento else None
    # O provento por ação está nas ações de dezembro; com evento até o corte,
    # dividir pelo preço do corte daria DY na unidade errada.
    divs = dividendos_dfp(serie, precos.get("preco") if sem_evento else None)
    serie = [{k: v for k, v in s.items() if not k.startswith("_")} for s in serie]
    return {
        "ticker": tk, "safra": int(safra), "corte": corte.isoformat(),
        "setor": e.get("SETOR"), "subsetor": e.get("SUBSETOR"),
        "segmento": e.get("SEGMENTO"),
        "serie_anual": serie, "trimestres": {}, "dividendos": divs,
        "valuation": _valuation(mcap, serie, precos),
        "fonte": "dfp_cvm_saida",
    }


def carregar_macro_por_ano() -> dict[int, dict]:
    """``public.macro`` pelo engine do projeto (na medição, o armazém local)."""
    from core.dossie_b3 import _f, _rows
    rows = _rows("SELECT ano, selic, ipca, cambio, pib FROM public.macro ORDER BY ano")
    return {int(r["ano"]): {c: _f(r[c]) for c in ("selic", "ipca", "cambio", "pib")}
            for r in rows if r.get("ano") is not None}


def _faixa(v: float, cortes: tuple[float, ...], rotulos: tuple[str, ...]) -> str:
    """Rótulo da faixa de ``v``: ``rotulos[i]`` para ``v < cortes[i]``, o último acima."""
    for corte, rotulo in zip(cortes, rotulos):
        if v < corte:
            return rotulo
    return rotulos[-1]


def contexto_macro_pit(safra: int, macro_por_ano: dict[int, dict]) -> str:
    """Macro do último exercício fechado, em FAIXAS e anos relativos.

    ``macro_por_ano`` = {ano: {"selic", "ipca", "cambio", "pib"}} de
    ``public.macro``. Em faixas, não em nível: medido na sonda (WEGE3/2019),
    "Selic 6,50% | câmbio 3,87" bastava para o modelo cravar o ano da
    decisão -- e com o ano ele sabe o que veio depois. A faixa e a direção
    dizem ao parecer o regime de juros e de inflação sem datar o dossiê.
    Noticiário e detalhe do armazém da época não existem: o bloco diz isso,
    para a ausência não ser lida como calmaria."""
    ano = int(safra) - 1
    m = macro_por_ano.get(ano) or {}
    ant = macro_por_ano.get(ano - 1) or {}

    def _ok(v):
        return v is not None and not (isinstance(v, float) and math.isnan(v))

    def _selic(d):
        # Fração (ou % em linhas antigas, a regra de `core.b3_db.load_selic_macro`).
        v = d.get("selic")
        return (v * 100 if abs(v) <= 1 else v) if _ok(v) else None

    partes = []
    sel, sel_ant = _selic(m), _selic(ant)
    if sel is not None:
        txt = _faixa(sel, (7, 10, 13), ("até 7% a.a.", "7% a 10% a.a.",
                                        "10% a 13% a.a.", "acima de 13% a.a."))
        if sel_ant is not None:
            d = sel - sel_ant
            txt += (", em alta no ano" if d > 0.25 else
                    ", em queda no ano" if d < -0.25 else ", estável no ano")
        partes.append(f"Selic no fim do ano {txt}")
    else:
        partes.append("Selic n/d")
    if _ok(m.get("ipca")):
        partes.append("IPCA " + _faixa(m["ipca"], (3, 4.5, 6.5), (
            "abaixo de 3%", "3% a 4,5%", "4,5% a 6,5%", "acima de 6,5%")))
    else:
        partes.append("IPCA n/d")
    if _ok(m.get("pib")) and _ok(ant.get("pib")) and ant["pib"]:
        partes.append("PIB nominal a/a " + _faixa(
            (m["pib"] / ant["pib"] - 1) * 100, (5, 10), ("até 5%", "5% a 10%", "acima de 10%")))
    else:
        partes.append("PIB nominal a/a n/d")
    if _ok(m.get("cambio")) and _ok(ant.get("cambio")) and ant["cambio"]:
        partes.append("real frente ao dólar " + _faixa(
            (m["cambio"] / ant["cambio"] - 1) * 100, (-10, 10),
            ("apreciou mais de 10%", "variou menos de 10%", "depreciou mais de 10%")))
    else:
        partes.append("câmbio n/d")
    linha = ("MACRO DO EXERCÍCIO A-1 (public.macro, em faixas): " + " | ".join(partes)
             if m else "MACRO DO EXERCÍCIO A-1: indisponível em public.macro (lacuna).")
    return (linha + "\nNOTICIÁRIO E DETALHE DO ARMAZÉM: não reconstruídos para a data "
            "da decisão -- lacuna de fonte, não calmaria de mercado.")


PROMPT_SONDA = """\
Abaixo está um dossiê financeiro ANONIMIZADO de uma empresa listada na B3: \
código e nome foram trocados, anos são relativos à data do dossiê (A-1 = último \
exercício fechado) e valores em R$ e por ação foram multiplicados por fatores \
ocultos. Diga qual empresa você acha que é e em que ano civil fica a data do \
dossiê. Se não souber, responda null -- não há penalidade por não saber.

{dossie}

{macro}

Responda APENAS com JSON: {{"ticker": "<código B3, ex. ABCD3, ou null>", \
"ano_da_decisao": <ano civil inteiro ou null>, "confianca": <int 0-100>}}
"""


def acertou_sonda(resposta: dict, tk: str, safra: int) -> dict:
    """A sonda identificou a empresa (raiz de 4 letras) e/ou o ano?"""
    palpite = str((resposta or {}).get("ticker") or "").strip().upper()
    try:
        ano = int((resposta or {}).get("ano_da_decisao"))
    except (TypeError, ValueError):
        ano = None
    return {"empresa": bool(palpite) and palpite[:4] == str(tk).upper()[:4],
            "ano": ano == int(safra),
            "ano_perto": ano is not None and abs(ano - int(safra)) <= 1}


# ─────────────────────────────────────────────────────────────────────────────
# Chamadas ao LLM (o mesmo caminho da tela, sem o cache diário do Streamlit)
# ─────────────────────────────────────────────────────────────────────────────

# O timeout do cliente é de LEITURA (entre bytes), não total: o OpenRouter
# mantém a conexão viva com espaços enquanto o modelo gratuito enfileira, e uma
# chamada ficou pendurada 30+ min sem nunca estourar os 90 s. As chamadas
# legítimas mais lentas medidas levaram ~430 s.
PRAZO_LLM_S = 600.0


def _com_prazo(fn, prazo: float):
    """Roda `fn` numa thread daemon e desiste depois de `prazo` segundos.

    A thread abandonada segue até o provedor soltar, mas não segura o processo
    (daemon) nem o parecer: estouro é falha, e falha nunca veta."""
    res: dict = {}

    def _alvo():
        try:
            res["v"] = fn()
        except BaseException as exc:  # noqa: BLE001 -- repassado abaixo
            res["e"] = exc

    t = threading.Thread(target=_alvo, daemon=True)
    t.start()
    t.join(prazo)
    if t.is_alive():
        raise TimeoutError(f"provedor não respondeu em {prazo:.0f} s")
    if "e" in res:
        raise res["e"]
    return res["v"]


def _llm_json(prompt: str) -> tuple[dict, str | None]:
    """Uma chamada pela cadeia de produção; devolve (json, "provedor/modelo")."""
    from core.llm_b3 import _call_llm, _parse_json, _report_model, ultimo_modelo
    # `ultimo_modelo` é por thread: lido na mesma thread da chamada.
    raw, modelo = _com_prazo(
        lambda: (_call_llm(prompt, model=_report_model()), ultimo_modelo()), PRAZO_LLM_S)
    if not raw:
        raise ValueError("resposta vazia do provedor")
    out = _parse_json(raw, None)
    if not isinstance(out, dict):
        raise ValueError("resposta não interpretável")
    return out, modelo


def prompt_do_parecer(texto_dossie: str, macro_txt: str) -> str:
    """O `_PROMPT_PARECER` de produção sobre o dossiê da época.

    Sem trechos CVM (o acervo não cobre a data) e com o macro do exercício
    fechado no lugar do contexto de mercado de hoje."""
    from core.contexto_mercado import REGRA_CONTEXTO_MERCADO
    from core.dossie_b3 import _PROMPT_PARECER
    return _PROMPT_PARECER.format(
        portfolio_ctx="(sem contexto de portfólio)",
        peers_ctx="(sem pares mapeados)",
        dossie=texto_dossie,
        rag="(nenhum trecho CVM disponível para a data da decisão)",
        contexto_mercado=macro_txt,
        regra_contexto=REGRA_CONTEXTO_MERCADO,
    )


def hash_prompt(prompt: str) -> str:
    from core.seguranca.procedencia import sem_marcadores_aleatorios
    return hashlib.sha256(sem_marcadores_aleatorios(prompt).encode("utf-8")).hexdigest()[:16]


def montar_entrada_pit(tk: str, safra: int, macro_por_ano: dict[int, dict]) -> dict:
    """Dossiê anonimizado em texto + macro da época, prontos para o prompt."""
    from core.dossie_b3 import dossie_to_text
    bruto = coletar_dossie_pit(tk, safra)
    if not bruto["serie_anual"]:
        raise LookupError("sem série anual até o exercício N-1")
    anon = anonimizar(bruto, tk, safra)
    return {"codigo": anon["ticker"], "texto": dossie_to_text(anon),
            "macro": contexto_macro_pit(safra, macro_por_ano)}


def parecer_pit(entrada: dict) -> dict:
    """Parecer do LLM sobre ``montar_entrada_pit``. Levanta em falha -- quem
    chama decide (falha nunca veta)."""
    from core.dossie_b3 import _sanitizar_parecer
    prompt = prompt_do_parecer(entrada["texto"], entrada["macro"])
    out, modelo = _llm_json(prompt)
    if out.get("classificacao_selecao") not in CLASSIFICACOES_VALIDAS:
        # `_sanitizar_parecer` trocaria por "nao_avaliado" e apagaria o que o
        # modelo de fato devolveu -- a falha registra o valor bruto.
        raise ValueError(f"classificação fora do esquema: "
                         f"{str(out.get('classificacao_selecao'))[:40]!r} "
                         f"({len(out)} chaves: {', '.join(sorted(map(str, out))[:4])}; "
                         f"{modelo})")
    p = _sanitizar_parecer(out, entrada["codigo"])
    return {"classificacao": p.get("classificacao_selecao"),
            "motivo": str(p.get("motivo_selecao") or "")[:400],
            "confianca": p.get("confianca"),
            "score_qualitativo": p.get("score_qualitativo"),
            "modelo": modelo, "codigo": entrada["codigo"],
            "hash_prompt": hash_prompt(prompt)}


def sonda_pit(entrada: dict, tk: str, safra: int) -> dict:
    out, modelo = _llm_json(PROMPT_SONDA.format(dossie=entrada["texto"],
                                                macro=entrada["macro"]))
    return {**acertou_sonda(out, tk, safra),
            "palpite": str(out.get("ticker") or "")[:12] or None,
            "ano_palpite": out.get("ano_da_decisao"),
            "confianca": out.get("confianca"), "modelo": modelo}


# ─────────────────────────────────────────────────────────────────────────────
# Avaliador: cache persistente + ondas paralelas
# ─────────────────────────────────────────────────────────────────────────────

CLASSIFICACOES_VALIDAS = ("aprovar", "aprovar_com_ressalvas", "vetar")
FALHOU = "falhou"
PENDENTE = "pendente"


class AvaliadorPortaoPIT:
    """Vereditos do portão por (ticker, safra), com cache em JSON.

    ``entrada(tk, safra)``, ``parecer(entrada)`` e ``sonda(entrada, tk,
    safra)`` são injetados (o runner passa `montar_entrada_pit`,
    `parecer_pit` e `sonda_pit`; os testes, funções puras). Falha não é
    gravada e conta como não avaliado -- nunca veta, como na tela. A
    repetição ``rep 1`` existe só para medir a concordância do LLM consigo
    mesmo; o portão medido usa sempre ``rep 0``.
    """

    def __init__(self, caminho, entrada, parecer, sonda=None, *, workers: int = 8,
                 intervalo_s: float = 3.5, frac_sonda: float = 0.3,
                 frac_repeticao: float = 0.1, tentativas: int = 2, log=print):
        import json
        import threading
        from pathlib import Path
        self.caminho = Path(caminho) if caminho else None
        self.entrada, self.parecer, self.sonda = entrada, parecer, sonda
        self.workers, self.intervalo_s = workers, intervalo_s
        self.frac_sonda, self.frac_repeticao = frac_sonda, frac_repeticao
        self.log = log
        self.registros: dict[str, dict] = {}
        self.falhas: dict[str, str] = {}
        self._entradas: dict[tuple[str, int], dict] = {}
        self.consultados: set[tuple[str, int]] = set()
        self._trava = threading.Lock()
        self._ultimo_inicio = 0.0
        self.chamadas = 0
        # Na tela, resposta fora do esquema vira "não avaliado" do dia; aqui
        # ganha mais uma tentativa (quem monta a carteira reexecuta), e o
        # número de tentativas perdidas vai para o resumo.
        self.tentativas = max(1, int(tentativas))
        self.tentativas_falhas = 0
        if self.caminho and self.caminho.exists():
            dados = json.loads(self.caminho.read_text(encoding="utf-8"))
            if dados.get("versao") == VERSAO_DOSSIE_PIT:
                self.registros = dados.get("registros") or {}

    @staticmethod
    def chave(tk: str, safra: int, rep: int = 0) -> str:
        return f"{tk}|{int(safra)}|{int(rep)}"

    def classificacao(self, tk: str, safra: int) -> str:
        reg = self.registros.get(self.chave(tk, safra))
        if reg is not None:
            return reg["classificacao"]
        return FALHOU if self.chave(tk, safra) in self.falhas else PENDENTE

    # -- portão -----------------------------------------------------------
    def portao(self, segmentos, safra):
        from core.b3_oos_carteira import aplicar_portao_medido
        return aplicar_portao_medido(segmentos, lambda tk: self.classificacao(tk, safra))

    def __call__(self, segmentos, safra):
        return self.portao(segmentos, safra)

    def preparar(self, por_safra) -> None:
        """Ondas: aplica o portão com o que já se sabe, busca os pares que
        faltam (os selecionados e, onde houve veto, os substitutos) e repete
        até nada faltar. Depois, repetição e sonda numa amostra fixa.

        Guarda em ``self.consultados`` os pares que o portão DESTE perfil
        consultou: o cache é compartilhado entre perfis, e filtrar só pela
        safra misturaria no resumo os nomes que outro perfil avaliou."""
        from core.b3_oos_carteira import aplicar_portao_medido
        onda = 0
        while True:
            faltam: set[tuple[str, int]] = set()
            consultados: set[tuple[str, int]] = set()
            for p in por_safra:
                def _av(tk, safra=p["safra"]):
                    consultados.add((tk, safra))
                    cls = self.classificacao(tk, safra)
                    if cls == PENDENTE:
                        faltam.add((tk, safra))
                    return cls
                aplicar_portao_medido(p["segmentos"], _av)
            if not faltam:
                break
            onda += 1
            self.log(f"  portão: onda {onda}, {len(faltam)} pareceres a buscar")
            self._paralelo(self._um_parecer, [(tk, s, 0) for tk, s in sorted(faltam)])
        self.consultados = consultados

        base = sorted((tk, s) for tk, s in consultados
                      if (self.registros.get(self.chave(tk, s)) or {}).get("classificacao")
                      in CLASSIFICACOES_VALIDAS)
        reps = [(tk, s, 1) for tk, s in base
                if _unitario(f"rep|{tk}|{s}") < self.frac_repeticao
                and self.chave(tk, s, 1) not in self.registros
                and self.chave(tk, s, 1) not in self.falhas]
        if reps:
            self.log(f"  portão: {len(reps)} repetições (concordância)")
            self._paralelo(self._um_parecer, reps)
        if self.sonda is not None:
            sondas = [(tk, s) for tk, s in base
                      if _unitario(f"sonda|{tk}|{s}") < self.frac_sonda
                      and "sonda" not in self.registros[self.chave(tk, s)]
                      and f"sonda|{tk}|{s}" not in self.falhas]
            if sondas:
                self.log(f"  portão: {len(sondas)} sondas de contaminação")
                self._paralelo(self._uma_sonda, sondas)

    # -- rede ---------------------------------------------------------------
    def _respirar(self) -> None:
        """Espaça o INÍCIO das chamadas (limite por minuto do provedor)."""
        import time
        with self._trava:
            agora = time.monotonic()
            inicio = max(agora, self._ultimo_inicio + self.intervalo_s)
            self._ultimo_inicio = inicio
            self.chamadas += 1
        if inicio > agora:
            time.sleep(inicio - agora)

    def _entrada(self, tk: str, safra: int) -> dict:
        e = self._entradas.get((tk, safra))
        if e is None:
            e = self.entrada(tk, safra)
            with self._trava:
                self._entradas[(tk, safra)] = e
        return e

    def _um_parecer(self, tk: str, safra: int, rep: int) -> None:
        chave = self.chave(tk, safra, rep)
        for tentativa in range(self.tentativas):
            try:
                e = self._entrada(tk, safra)
                self._respirar()
                reg = self.parecer(e)
                if reg.get("classificacao") not in CLASSIFICACOES_VALIDAS:
                    raise ValueError(
                        f"classificação fora do esquema: {reg.get('classificacao')!r}")
            except Exception as exc:  # noqa: BLE001 -- falha nunca veta
                with self._trava:
                    self.tentativas_falhas += 1
                    if tentativa == self.tentativas - 1:
                        self.falhas[chave] = f"{type(exc).__name__}: {str(exc)[:160]}"
                        n_falhas = len(self.falhas)
                if tentativa == self.tentativas - 1 and n_falhas <= 10:
                    self.log(f"    falhou {chave}: {self.falhas[chave]}")
                continue
            with self._trava:
                self.registros[chave] = {**reg, "tentativa": tentativa + 1}
            return

    def _uma_sonda(self, tk: str, safra: int) -> None:
        try:
            e = self._entrada(tk, safra)
            self._respirar()
            res = self.sonda(e, tk, safra)
        except Exception as exc:  # noqa: BLE001
            with self._trava:
                self.falhas[f"sonda|{tk}|{safra}"] = f"{type(exc).__name__}: {str(exc)[:160]}"
            return
        with self._trava:
            self.registros[self.chave(tk, safra)]["sonda"] = res

    def _paralelo(self, fn, pares) -> None:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futuros = [ex.submit(fn, *par) for par in pares]
            for i, _f in enumerate(as_completed(futuros), 1):
                if i % 25 == 0:
                    self.log(f"    {i}/{len(pares)} (falhas: {len(self.falhas)})")
                    self.gravar()
        self.gravar()

    def gravar(self) -> None:
        if not self.caminho:
            return
        import json
        with self._trava:
            regs = json.loads(json.dumps(dict(sorted(self.registros.items())), default=str))
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self.caminho.write_text(json.dumps(
            # As falhas não são reaproveitadas (a rodada seguinte tenta de
            # novo); ficam no arquivo só para auditoria da última rodada.
            {"versao": VERSAO_DOSSIE_PIT, "registros": regs,
             "falhas_da_ultima_rodada": dict(sorted(self.falhas.items()))},
            indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    # -- resumo ---------------------------------------------------------------
    def resumo(self, safras=None, pares=None) -> dict:
        """Resumo dos pareceres das ``safras`` -- ou, com ``pares``, só dos
        (ticker, safra) que o portão de um perfil consultou."""
        from collections import Counter

        def _da(k):  # "TK|safra|rep" (falhas de sonda são filtradas antes)
            tk, safra = k.split("|")[0], int(k.split("|")[1])
            return ((safras is None or safra in safras)
                    and (pares is None or (tk, safra) in pares))
        base = {k: r for k, r in self.registros.items() if k.endswith("|0") and _da(k)}
        falhas = {k: m for k, m in self.falhas.items()
                  if not k.startswith("sonda|") and _da(k)}
        cls = Counter(r["classificacao"] for r in base.values())
        modelos = Counter(str(r.get("modelo")) for k, r in self.registros.items() if _da(k))
        pares_rep = [(k, k[:-1] + "1") for k in base if k[:-1] + "1" in self.registros]
        concord = [base[a]["classificacao"] == self.registros[b]["classificacao"]
                   for a, b in pares_rep]
        concord_veto = [(base[a]["classificacao"] == "vetar")
                        == (self.registros[b]["classificacao"] == "vetar")
                        for a, b in pares_rep]
        sondas = [r["sonda"] for r in base.values() if r.get("sonda")]

        def _taxa(xs):
            return round(sum(xs) / len(xs), 4) if xs else None
        return {
            "pareceres": len(base),
            "classificacoes": dict(cls),
            "taxa_veto": _taxa([r["classificacao"] == "vetar" for r in base.values()]),
            "falhas": len(falhas),
            # Por causa: sem dossiê (sem DRE nem DFP até o corte), o modelo
            # respondeu "não avaliado" em todas as tentativas, ou erro de rede.
            "falhas_sem_dossie": sum(1 for m in falhas.values()
                                     if m.startswith("LookupError")),
            "falhas_modelo_nao_avaliou": sum(1 for m in falhas.values()
                                             if "fora do esquema" in m),
            "tentativas_perdidas_nesta_rodada": self.tentativas_falhas,
            "pareceres_na_segunda_tentativa": sum(
                1 for r in base.values() if int(r.get("tentativa") or 1) > 1),
            "exemplos_falha": sorted(set(self.falhas.values()))[:5],
            "modelos": dict(modelos),
            "repeticoes": len(pares_rep),
            "concordancia_classificacao": _taxa(concord),
            "concordancia_veto": _taxa(concord_veto),
            "sondas": len(sondas),
            "sonda_acerta_empresa": _taxa([s["empresa"] for s in sondas]),
            "sonda_acerta_ano": _taxa([s["ano"] for s in sondas]),
            "sonda_ano_mais_menos_1": _taxa([s["ano_perto"] for s in sondas]),
            "vetos_com_empresa_identificada": sum(
                1 for r in base.values()
                if r["classificacao"] == "vetar" and (r.get("sonda") or {}).get("empresa")),
            # A memória importa se o veredito muda quando o modelo reconhece
            # a empresa: taxa de veto entre as sondadas, por acerto.
            "taxa_veto_sonda_identificou": _taxa(
                [r["classificacao"] == "vetar" for r in base.values()
                 if (r.get("sonda") or {}).get("empresa") is True]),
            "taxa_veto_sonda_nao_identificou": _taxa(
                [r["classificacao"] == "vetar" for r in base.values()
                 if (r.get("sonda") or {}).get("empresa") is False]),
            "chamadas_nesta_rodada": self.chamadas,
        }


__all__ = [
    "AvaliadorPortaoPIT", "CLASSIFICACOES_VALIDAS", "COBERTURA_PIT", "FAIXA_ESCALA",
    "PROMPT_SONDA", "VERSAO_DOSSIE_PIT", "acertou_sonda", "anonimizar",
    "codigo_anonimo", "coletar_dossie_pit", "contexto_macro_pit", "data_de_corte",
    "fator_escala", "hash_prompt", "montar_entrada_pit", "parecer_pit",
    "prompt_do_parecer", "rotulo_ano", "sonda_pit",
]
