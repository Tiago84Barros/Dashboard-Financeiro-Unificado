"""Carteira de FIIs em safras anuais, com a estrutura da Criação de Portfólio B3.

Pedido do usuário de 21/11/2026: aplicar aos FIIs a mesma abordagem da
Empresas B3 -- score por ativo, carteira por segmento, e os scores do ano
anterior decidindo quem entra na carteira do ano seguinte. Este módulo é o
porte. É puro (sem banco nem Streamlit); quem o alimenta é
``scripts/medir_safras_fii.py``, com os snapshots PIT do warehouse.

Correspondência com a B3 (``views/portfolio_b3.py`` e ``core/b3_oos_carteira.py``):

=============================  ==============================================
B3                              FIIs (aqui)
=============================  ==============================================
segmento CVM                    tipo do FII (``VALID_TYPES``: tijolo, papel,
                                fof, hibrido), o da reconstrução PIT
score do ano N (FY N−1)         ``type_score`` do snapshot de 31/03/N, com o
                                que era público até essa data
universo PIT do ano             elegibilidade integrada ESTRITA na data
                                (``apply_integrated_eligibility``, sem
                                concessão)
``_processar_segmento``         `historico_do_segmento`: decaimento de
                                liderança, n pela heurística, mínimo de nomes
                                do cap, pesos por score com cap suave
``_simular_seg_backtest``       `simular_segmento`: DCA mensal sobre retorno
                                total (o FII reinveste o provento no próprio
                                retorno), estratégia × Pesos Iguais × Selic
``metricas_pit``                `metricas_pit`: tudo cortado em março/N
``aprova_economico``            o MESMO (importado), modo Econômico
``selecao_do_segmento``         o MESMO (importado): líder + maior
                                participação, com a regra de recência
``montar_carteira``             o MESMO: orçamento igual por segmento, cap
``itens_da_cadeia``             o MESMO: aquecimento e regra D1 (sem carteira
                                salvável, CAIXA à Selic)
custos/IR                       giro × (corretagem + slippage) e IR de 20%
                                sobre ganho de capital (``RastreadorIR``), o
                                provento isento -- a contabilidade do backtest
                                FII, não a de ações (``simular_custos`` é de
                                ações: 15% e isenção de R$ 20 mil)
Ibovespa                        IFIX oficial (bruto: é o índice)
=============================  ==============================================

O que da B3 NÃO entrou, e por quê:

- resiliência ROIC − Selic: FII não tem ROIC; o critério fica desligado
  (``exigir_resiliencia=False``), declarado aqui e no resultado;
- tetos de setor e de ciclo: o tipo já é o segmento e o orçamento já é
  igual por tipo; os tetos ficam em 1,0 (sem efeito);
- cap adaptativo: a B3 relaxa o cap porque muitos segmentos têm 2 ou 3
  empresas. Tipo de FII tem dezenas de fundos elegíveis; o default é o cap
  estrito (``cap_adaptativo=False``), que força o mínimo de nomes do cap
  entre os líderes;
- portão qualitativo (LLM), piso de qualidade e Score de Entrada: são da
  tela de ações e não têm equivalente medido para FII.

Regras herdadas do pipeline FII que continuam valendo:

- mês sem retorno rende 0 (A-118), na safra e no DCA;
- winsorização do retorno da safra pelos percentis do universo elegível
  (os mesmos limites da B3, ``core.b3_safras``);
- IFIX oficial bruto como referência principal; Pesos Iguais do universo
  elegível e Selic como as outras duas.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np
import pandas as pd

from core import b3_oos_carteira as _oos
from core import b3_selecao as _selecao
from core import pesos_por_score as _pps
from core.b3_precos_saneamento import limites_winsor
from core.b3_safras import WINSOR_MIN_PAPEIS, WINSOR_PCT_ALTO, WINSOR_PCT_BAIXO
from core.b3_vigencia import REBAL_MONTH
from core.backtest_liquido import IR_FII_GANHO, RastreadorIR, renda_reinvestida
from core.fii_methodology import VALID_TYPES
from core.fii_validation import portfolio_turnover

VERSAO = "fii-safras-1.0.0"

#: Parâmetros default: os da B3 (gamma, cap, soft, preset recomendado de
#: aprovação) com as exceções declaradas no docstring do módulo.
PARAMS_DEFAULT: dict[str, Any] = {
    "gamma": 0.90,
    "cap": 0.25,
    "soft": 0.05,
    "cap_adaptativo": False,
    "decaimento": True,
    "aporte": 1000.0,
    "janela_val_anos": 2,
    "transaction_cost": 0.0015,
    "slippage": 0.0010,
    # aprovação (aprova_economico)
    "criterio_modo": "economico",
    "thr_selic": 15.0,
    "usar_ew": False,
    "thr_ew": 0.0,
    "exigir_vantagem_selecao": False,
    "max_anos_lid": 5,
    "exigir_resiliencia": False,
    # montagem
    "teto_setor": 1.0,
    "teto_ciclico": 1.0,
}

def ano_da_safra(data: pd.Timestamp) -> int:
    """A safra em vigor num mês: abril/N a março/N+1 é a safra N."""
    data = pd.Timestamp(data)
    return int(data.year) if data.month >= REBAL_MONTH else int(data.year) - 1


def janela_da_safra(safra: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    """[início, fim) da vigência: 1º de abril de N a 1º de abril de N+1."""
    return (pd.Timestamp(year=int(safra), month=REBAL_MONTH, day=1),
            pd.Timestamp(year=int(safra) + 1, month=REBAL_MONTH, day=1))


def matriz_de_retornos(retornos: pd.DataFrame, coluna: str = "total_return") -> pd.DataFrame:
    """Long (date, ticker, coluna) -> matriz mês × fundo, NaN onde falta."""
    if retornos is None or retornos.empty:
        return pd.DataFrame()
    r = retornos[["date", "ticker", coluna]].copy()
    r["date"] = pd.to_datetime(r["date"]).dt.normalize()
    r[coluna] = pd.to_numeric(r[coluna], errors="coerce")
    return (r.pivot_table(index="date", columns="ticker", values=coluna, aggfunc="last")
            .sort_index())


# ── histórico de liderança (espelho de `_processar_segmento`) ──────────────

def historico_do_segmento(scores_por_ano: Mapping[int, Mapping[str, float]], *,
                          gamma: float, cap: float, soft: float,
                          cap_adaptativo: bool = False,
                          decaimento: bool = True) -> dict:
    """Líderes e pesos por ano de um segmento, como a B3 faz.

    ``scores_por_ano``: {ano N: {ticker: type_score}} dos fundos do segmento
    elegíveis em 31/03/N. O laço é o de ``views.portfolio_b3._processar_segmento``:
    decaimento pelos anos consecutivos de liderança, ranking total (score
    desc, ticker asc), n pela heurística dos três primeiros, no mínimo os
    nomes que o cap exige, pesos por score com cap suave.
    """
    from core.portfolio_constraints import minimum_assets_for_cap

    anos_lideranca: dict[str, int] = {}
    lids_por_ano: dict[int, list[str]] = {}
    pesos_por_ano: dict[int, dict[str, float]] = {}
    score_rows: list[dict] = []
    avisos: list[str] = []
    for ano in sorted(int(a) for a in scores_por_ano):
        bruto = {str(tk): float(s) for tk, s in (scores_por_ano[ano] or {}).items()
                 if s is not None and math.isfinite(float(s))}
        if not bruto:
            continue
        score_map = (_pps.apply_decay_penalty(bruto, anos_lideranca)
                     if decaimento else dict(bruto))
        for tk, s in score_map.items():
            score_rows.append({"Ano": ano, "ticker": tk, "Score_Ajustado": float(s),
                               "Score_Bruto": bruto[tk]})
        ranked = sorted(score_map.items(), key=lambda x: (-float(x[1]), str(x[0])))
        n = (_pps.select_n_heuristica([s for _, s in ranked[:3]])
             if len(ranked) >= 2 else 1)
        min_n = 1 if cap_adaptativo else minimum_assets_for_cap(cap)
        n = min(len(ranked), max(n, min_n))
        lids = [tk for tk, _ in ranked[:n]]
        lids_por_ano[ano] = lids
        if len(lids) >= 2:
            cap_ef = max(cap, 1.0 / len(lids)) if cap_adaptativo else cap
            try:
                w = _pps.apply_cap_soft(_pps.weights_from_scores(lids, score_map, gamma),
                                        cap_ef, soft)
            except ValueError as exc:
                w = {tk: 1.0 / len(lids) for tk in lids}
                avisos.append(f"{ano}: {exc}")
        elif lids:
            w = {lids[0]: 1.0}
        else:
            w = {}
        pesos_por_ano[ano] = w
        novos = set(lids)
        for tk in list(anos_lideranca):
            if tk not in novos:
                del anos_lideranca[tk]
        for tk in dict.fromkeys(lids):
            anos_lideranca[tk] = anos_lideranca.get(tk, 0) + 1
    return {"lids_por_ano": lids_por_ano, "pesos_por_ano": pesos_por_ano,
            "score_rows": score_rows, "avisos": avisos}


# ── DCA mensal (espelho de `_simular_seg_backtest`) ────────────────────────

def _selic_do_mes(selic_mensal: pd.Series | None, data: pd.Timestamp) -> float | None:
    """Selic a.a. observável no mês, ou None. Sem default: dado ausente
    continua ausente (AGENTS.md), e o portão que dependeria dela reprova."""
    if selic_mensal is None or selic_mensal.empty:
        return None
    anteriores = selic_mensal[selic_mensal.index <= data].dropna()
    if anteriores.empty:
        return None
    return float(anteriores.iloc[-1])


def simular_segmento(matriz: pd.DataFrame, lids_por_ano: Mapping[int, list[str]],
                     pesos_por_ano: Mapping[int, Mapping[str, float]],
                     universo_por_ano: Mapping[int, Iterable[str]], *,
                     aporte: float, custo_compra: float,
                     selic_mensal: pd.Series | None = None,
                     details_out: dict | None = None
                     ) -> tuple[float, float | None, float, dict[str, float]]:
    """DCA mensal do segmento: estratégia, Selic e Pesos Iguais.

    O DCA na Selic volta None se algum mês da janela não tem Selic observável:
    uma taxa suposta decidiria o portão sem ser histórica.

    ``matriz``: retorno total mensal (mês × fundo), NaN onde falta. Diferença
    para a B3, que simula com PREÇO: aqui o valor de cada posição cresce pelo
    retorno total, que já reinveste o provento.

    Por mês, nesta ordem: (1) o retorno do mês nas posições abertas -- NaN
    rende 0 (A-118); (2) fundo depois do seu último retorno válido saiu da
    bolsa: a posição vira caixa e é reinvestida, como a B3 faz com o papel
    deslistado; (3) a partir de abril, os pesos da safra do ano; ano sem
    líderes mantém os pesos vigentes; (4) caixa + aporte compram os fundos
    da estratégia vivos no mês, pagando ``custo_compra`` (fração).

    Pesos Iguais: o aporte vai em partes iguais aos fundos do segmento
    ELEGÍVEIS na safra em vigor (``universo_por_ano``) e vivos no mês. A B3
    usa todo o segmento de todos os anos; aqui o universo é PIT.
    """
    if matriz is None or matriz.empty:
        return 0.0, 0.0, 0.0, {}
    tickers = list(matriz.columns)
    primeiro = {tk: matriz[tk].first_valid_index() for tk in tickers}
    ultimo = {tk: matriz[tk].last_valid_index() for tk in tickers}

    def vivo(tk: str, dt: pd.Timestamp) -> bool:
        ini, fim = primeiro.get(tk), ultimo.get(tk)
        return ini is not None and fim is not None and ini <= dt <= fim

    val_est: dict[str, float] = {}
    val_ew: dict[str, float] = {}
    caixa_est = caixa_ew = 0.0
    selic_acum = 0.0
    selic_completa = True
    pesos_est: dict[str, float] = {}
    ultimo_ano = None
    prev_est = prev_ew = prev_sel = None
    mensal: list[dict] = []
    for dt, linha in matriz.iterrows():
        dt = pd.Timestamp(dt)
        taxa_aa = _selic_do_mes(selic_mensal, dt)
        if taxa_aa is None:
            selic_completa = False
            taxa_aa = 0.0
        taxa_m = (1.0 + taxa_aa) ** (1.0 / 12.0) - 1.0
        selic_acum = selic_acum * (1.0 + taxa_m) + aporte

        for carteira in (val_est, val_ew):
            for tk in list(carteira):
                r = linha.get(tk)
                r = float(r) if r is not None and np.isfinite(r) else 0.0
                carteira[tk] *= 1.0 + r
        for tk in list(val_est):
            if ultimo.get(tk) is not None and dt > ultimo[tk]:
                caixa_est += val_est.pop(tk)
        for tk in list(val_ew):
            if ultimo.get(tk) is not None and dt > ultimo[tk]:
                caixa_ew += val_ew.pop(tk)

        ano = int(dt.year)
        if ano != ultimo_ano and dt.month >= REBAL_MONTH:
            ultimo_ano = ano
            lids = list(lids_por_ano.get(ano) or [])
            if lids:
                pesos_est = dict(pesos_por_ano.get(ano) or
                                 {tk: 1.0 / len(lids) for tk in lids})

        caixa_est += aporte
        disp = [tk for tk, w in pesos_est.items() if w > 0 and vivo(tk, dt)]
        if disp:
            tw = sum(pesos_est[tk] for tk in disp) or 1.0
            for tk in disp:
                bruto = caixa_est * pesos_est[tk] / tw
                val_est[tk] = val_est.get(tk, 0.0) + bruto * (1.0 - custo_compra)
            caixa_est = 0.0

        caixa_ew += aporte
        universo = list(dict.fromkeys(universo_por_ano.get(ano_da_safra(dt)) or []))
        disp_ew = [tk for tk in universo if tk in primeiro and vivo(tk, dt)]
        if disp_ew:
            for tk in disp_ew:
                bruto = caixa_ew / len(disp_ew)
                val_ew[tk] = val_ew.get(tk, 0.0) + bruto * (1.0 - custo_compra)
            caixa_ew = 0.0

        cur_est = caixa_est + sum(val_est.values())
        cur_ew = caixa_ew + sum(val_ew.values())
        if prev_est and prev_ew and prev_sel:
            mensal.append({"Data": dt,
                           "strategy": (cur_est - aporte) / prev_est - 1.0,
                           "selic": (selic_acum - aporte) / prev_sel - 1.0,
                           "equal_weight": (cur_ew - aporte) / prev_ew - 1.0})
        prev_est, prev_ew, prev_sel = cur_est, cur_ew, selic_acum

    if details_out is not None:
        details_out["monthly_returns"] = mensal
        details_out["pending_cash_strategy"] = float(caixa_est)
        details_out["pending_cash_equal_weight"] = float(caixa_ew)
    total_est = caixa_est + sum(val_est.values())
    total_ew = caixa_ew + sum(val_ew.values())
    return (total_est, selic_acum if selic_completa else None, total_ew,
            dict(val_est))


# ── retorno de uma safra por fundo ─────────────────────────────────────────

def retorno_composto(matriz: pd.DataFrame, safra: int) -> dict[str, float]:
    """Retorno composto de cada fundo na vigência; mês ausente vale 0 (A-118).

    Fundo sem nenhum retorno válido na janela fica de fora (None no chamador):
    não há mercado observado para ele.
    """
    if matriz is None or matriz.empty:
        return {}
    ini, fim = janela_da_safra(safra)
    bloco = matriz[(matriz.index >= ini) & (matriz.index < fim)]
    out: dict[str, float] = {}
    for tk in bloco.columns:
        serie = bloco[tk]
        if serie.notna().any():
            out[str(tk)] = float((1.0 + serie.fillna(0.0)).prod() - 1.0)
    return out


def meses_da_safra(matriz: pd.DataFrame, safra: int) -> int:
    ini, fim = janela_da_safra(safra)
    return int(((matriz.index >= ini) & (matriz.index < fim)).sum()) if not matriz.empty else 0


def meses_observados(matriz: pd.DataFrame, safra: int) -> list[pd.Timestamp]:
    """Os meses da vigência presentes na matriz de retornos dos fundos."""
    if matriz.empty:
        return []
    ini, fim = janela_da_safra(safra)
    idx = pd.DatetimeIndex(matriz.index)
    return [pd.Timestamp(d) for d in idx[(idx >= ini) & (idx < fim)]]


def retorno_do_benchmark(serie: pd.Series | None, safra: int,
                         meses: Iterable[pd.Timestamp] | None = None) -> float | None:
    """IFIX composto nos meses da vigência (bruto, é o índice).

    Com ``meses`` (os da carteira), exige o IFIX em CADA um deles: doze meses
    de carteira contra nove de IFIX não são comparáveis, e o excesso fica
    indisponível (None) em vez de entrar na média.
    """
    if serie is None or serie.empty:
        return None
    s = serie.copy()
    s.index = pd.to_datetime(s.index).normalize()
    s = s[~s.index.duplicated(keep="last")]
    if meses is not None:
        alvo = pd.DatetimeIndex([pd.Timestamp(m).normalize() for m in meses])
        if alvo.empty:
            return None
        bloco = pd.to_numeric(s.reindex(alvo), errors="coerce")
        if bloco.isna().any():
            return None
        return float((1.0 + bloco).prod() - 1.0)
    ini, fim = janela_da_safra(safra)
    bloco = s[(s.index >= ini) & (s.index < fim)].dropna()
    return float((1.0 + bloco).prod() - 1.0) if len(bloco) else None


def renda_da_safra(total: pd.DataFrame, preco: pd.DataFrame,
                   safra: int) -> dict[str, float]:
    """Provento reinvestido de cada fundo na vigência (`renda_reinvestida`)."""
    if total.empty or preco.empty:
        return {}
    ini, fim = janela_da_safra(safra)
    bt = total[(total.index >= ini) & (total.index < fim)]
    bp = preco.reindex(index=bt.index)
    out: dict[str, float] = {}
    for tk in bt.columns:
        if tk not in bp.columns:
            continue
        r = renda_reinvestida(bt[tk].tolist(), bp[tk].tolist())
        if r is not None:
            out[str(tk)] = float(r)
    return out


def renda_winsorizada(bruto: float, winsorizado: float, renda: float) -> float:
    """Provento coerente com o retorno winsorizado da safra.

    O razão de IR recebe o retorno cortado; com o provento do caminho
    original, o ganho de preço (retorno − provento) ganharia uma perda que
    não existiu e abateria IR das safras seguintes. O corte para baixo sai
    primeiro do ganho de preço positivo e só depois do provento; o corte para
    cima vai todo para o ganho de preço. Assim provento + ganho = retorno
    winsorizado e o razão nunca vê uma perda de capital que o caminho
    original não teve.
    """
    corte = bruto - winsorizado
    if corte <= 0:
        return renda
    ganho = bruto - renda
    do_ganho = min(corte, max(ganho, 0.0))
    return renda - (corte - do_ganho)


# ── métricas PIT de um segmento na safra N (espelho de `metricas_pit`) ─────

def metricas_pit(hist: dict, safra: int, matriz: pd.DataFrame,
                 universo_por_ano: Mapping[int, Iterable[str]], *, ano_inicio: int,
                 params: Mapping[str, Any], selic_mensal: pd.Series | None = None) -> dict:
    """O que `aprova_economico` lê, só com dados até março/N.

    Usa os líderes dos anos < N, a matriz de retornos de abril/``ano_inicio``
    a março/N, o Rank-IC dos anos <= N−1 (cujo retorno termina em março/N) e
    o último ano de liderança de cada fundo.
    """
    janela_val = int(params.get("janela_val_anos", 2))
    aporte = float(params.get("aporte", 1000.0))
    custo = float(params.get("transaction_cost", 0.0)) + float(params.get("slippage", 0.0))
    lids = {a: v for a, v in hist["lids_por_ano"].items() if int(a) < safra}
    pesos = {a: v for a, v in hist["pesos_por_ano"].items() if int(a) < safra}
    tks = sorted({tk for nomes in lids.values() for tk in nomes}
                 | {tk for a, u in universo_por_ano.items() if int(a) < safra for tk in u})
    out: dict[str, Any] = {"val_est": 0.0, "val_selic": 0.0, "val_ew": 0.0, "maior": None,
                           "selecao_excesso": _selecao.intervalo_excesso([]), "n_linhas": 0}
    tks = [tk for tk in tks if tk in matriz.columns]
    if tks and not matriz.empty:
        df_seg = _oos._corte_ate_safra(matriz[tks], ano_inicio, safra)
        out["n_linhas"] = int(len(df_seg))
        if not df_seg.empty:
            v_est, v_sel, v_ew, contrib = simular_segmento(
                df_seg, lids, pesos, universo_por_ano, aporte=aporte,
                custo_compra=custo, selic_mensal=selic_mensal)
            positivos = {tk: v for tk, v in contrib.items() if v > 0}
            out.update(val_est=float(v_est),
                       val_selic=None if v_sel is None else float(v_sel),
                       val_ew=float(v_ew),
                       maior=max(positivos, key=positivos.get) if positivos else None)
            fim = pd.Timestamp(df_seg.index.max())
            ini_val = pd.Timestamp(year=int(fim.year) - janela_val, month=REBAL_MONTH, day=1)
            df_val = df_seg[pd.DatetimeIndex(df_seg.index) >= ini_val]
            if len(df_val) >= _oos.MIN_LINHAS_VALIDACAO:
                det: dict = {}
                simular_segmento(df_val, lids, pesos, universo_por_ano, aporte=aporte,
                                 custo_compra=custo, selic_mensal=selic_mensal,
                                 details_out=det)
                mensal = pd.DataFrame(det.get("monthly_returns") or [])
                if len(mensal) >= 12:
                    exc = (pd.to_numeric(mensal["strategy"], errors="coerce")
                           - pd.to_numeric(mensal["equal_weight"], errors="coerce")
                           ).replace([np.inf, -np.inf], np.nan).dropna()
                    out["selecao_excesso"] = _selecao.intervalo_excesso(exc.tolist())

    ic_pairs: list[tuple[int, float, float]] = []
    for row in hist["score_rows"]:
        ano = int(row["Ano"])
        if ano > safra - 1:
            continue
        ret = _retornos_cache(matriz, ano).get(str(row["ticker"]))
        if ret is not None:
            ic_pairs.append((ano, float(row["Score_Ajustado"]), float(ret)))
    out["rank_ic_mean"] = _oos._ic_medio(ic_pairs, safra - 1)

    historico: dict[str, list[int]] = {}
    for ano, nomes in lids.items():
        for tk in nomes:
            historico.setdefault(tk, []).append(int(ano))
    out["ultimo_lid"] = {tk: max(anos) for tk, anos in historico.items()}
    return out


_CACHE_RET: dict[tuple[int, int], dict[str, float]] = {}


def _retornos_cache(matriz: pd.DataFrame, ano: int) -> dict[str, float]:
    chave = (id(matriz), int(ano))
    if chave not in _CACHE_RET:
        _CACHE_RET[chave] = retorno_composto(matriz, ano)
    return _CACHE_RET[chave]


# ── a medição inteira ──────────────────────────────────────────────────────

def _scores_por_segmento(elegiveis_por_safra: Mapping[int, list[dict]]
                         ) -> dict[str, dict[int, dict[str, float]]]:
    out: dict[str, dict[int, dict[str, float]]] = {t: {} for t in VALID_TYPES}
    for safra, linhas in elegiveis_por_safra.items():
        for row in linhas or []:
            tipo = str(row.get("tipo") or "").lower()
            if tipo not in out:
                continue
            score = row.get("type_score")
            if score is None or not math.isfinite(float(score)):
                continue
            out[tipo].setdefault(int(safra), {})[str(row["ticker"])] = float(score)
    return out


def medir_safras(elegiveis_por_safra: Mapping[int, list[dict]], retornos: pd.DataFrame,
                 ifix: pd.Series | None, *, selic_decisao: Mapping[int, float],
                 selic_mensal: pd.Series | None = None,
                 params: Mapping[str, Any] | None = None) -> dict:
    """Carteira de safras FII, líquida de custo e IR, contra IFIX, EW e Selic.

    ``elegiveis_por_safra``: {N: linhas elegíveis em 31/03/N}, cada uma com
    ``ticker``, ``tipo`` e ``type_score``. ``retornos``: long com ``date``,
    ``ticker``, ``total_return`` e ``price_return`` (sem ``price_return`` o IR
    não é calculado e o resultado diz isso). ``selic_decisao``: Selic a.a.
    (fração) observável em 31/03/N -- o rendimento da safra em caixa.
    ``selic_mensal``: Selic a.a. (fração) por mês, para o DCA do portão.
    """
    p = {**PARAMS_DEFAULT, **(params or {})}
    _CACHE_RET.clear()
    total = matriz_de_retornos(retornos, "total_return")
    ir_calculado = retornos is not None and "price_return" in retornos.columns
    preco = matriz_de_retornos(retornos, "price_return") if ir_calculado else pd.DataFrame()
    safras = sorted(int(a) for a in elegiveis_por_safra)
    if not safras or total.empty:
        return {"versao": VERSAO, "status": "blocked",
                "blockers": ["sem safras elegíveis ou sem retornos"], "params": p}
    ano_inicio = safras[0]
    por_seg = _scores_por_segmento(elegiveis_por_safra)
    historicos = {
        tipo: historico_do_segmento(
            scores, gamma=p["gamma"], cap=p["cap"], soft=p["soft"],
            cap_adaptativo=bool(p["cap_adaptativo"]), decaimento=bool(p["decaimento"]))
        for tipo, scores in por_seg.items() if scores}
    universo_seg = {tipo: {a: list(s) for a, s in scores.items()}
                    for tipo, scores in por_seg.items()}

    linhas: list[dict] = []
    for safra in safras:
        segmentos: list[dict] = []
        itens: list[tuple[str, list[str], dict]] = []
        for tipo, hist in historicos.items():
            if safra not in hist["lids_por_ano"]:
                continue
            m = metricas_pit(hist, safra, total, universo_seg[tipo], ano_inicio=ano_inicio,
                             params=p, selic_mensal=selic_mensal)
            if m.get("val_selic") is None and m.get("val_est", 0.0) > 0:
                # sem Selic observável em algum mês do portão, a margem não
                # existe: reprova com o motivo, em vez de supor uma taxa
                aprovado, motivo = False, "Selic ausente no período do portão"
            else:
                aprovado, motivo = _oos.aprova_economico(m, p, safra)
            sel, pesos_seg, _ranking = _oos.selecao_do_segmento(
                hist, m, safra, max_anos_lid=int(p["max_anos_lid"]),
                pode_incluir_maior=_pps.pode_incluir_maior_participacao)
            segmentos.append({
                "tipo": tipo, "aprovado": bool(aprovado), "motivo": motivo,
                "n_lideres": len(hist["lids_por_ano"][safra]),
                "n_universo": len(universo_seg[tipo].get(safra) or []),
                "selecionados": sel, "maior": m.get("maior"),
                "margem_selic_pct": (None if m.get("val_selic") is None
                                     else _oos._margem_pct(m["val_est"], m["val_selic"])),
                "margem_ew_pct": _oos._margem_pct(m["val_est"], m["val_ew"]),
                "rank_ic_mean": m.get("rank_ic_mean"),
                "selecao": (m.get("selecao_excesso") or {}).get("veredito"),
            })
            if aprovado:
                itens.append((tipo, sel, pesos_seg))
        carteira = _oos.montar_carteira(itens, cap=float(p["cap"]),
                                        teto_setor=float(p["teto_setor"]),
                                        teto_ciclico=float(p["teto_ciclico"]))
        universo = [str(r["ticker"]) for r in elegiveis_por_safra.get(safra) or []]
        brutos = retorno_composto(total, safra)
        limites = limites_winsor([brutos[tk] for tk in universo if tk in brutos],
                                 pct_baixo=WINSOR_PCT_BAIXO, pct_alto=WINSOR_PCT_ALTO,
                                 minimo=WINSOR_MIN_PAPEIS)

        def _w(r: float) -> float:
            return min(max(r, limites[0]), limites[1]) if limites is not None else r

        retornos_cart = {tk: _w(brutos[tk]) if tk in brutos else 0.0
                         for tk in carteira["pesos"]}
        renda_safra = renda_da_safra(total, preco, safra) if ir_calculado else {}
        renda = {tk: renda_winsorizada(brutos[tk], retornos_cart[tk], renda_safra[tk])
                 for tk in carteira["pesos"] if tk in brutos and tk in renda_safra}
        ew_vals = [_w(brutos[tk]) for tk in universo if tk in brutos]
        meses = meses_da_safra(total, safra)
        selic = selic_decisao.get(safra)
        linhas.append({
            "safra": safra, "segmentos": segmentos,
            "pesos": carteira["pesos"], "retornos": retornos_cart, "renda": renda,
            "inviavel_no_cap": bool(carteira["inviavel"]),
            "exige_revisao": bool(carteira["exige_revisao"]),
            "mensuravel": meses > 0 and bool(brutos),
            "completa": meses >= 12,
            "meses": meses,
            "selic": None if selic is None else float(selic),
            "ifix": retorno_do_benchmark(ifix, safra, meses_observados(total, safra)),
            "ew": float(np.mean(ew_vals)) if ew_vals else None,
            "n_elegiveis": len(universo),
        })

    cadeia = _oos.itens_da_cadeia(linhas)
    resultado = _contabilizar(linhas, cadeia, p, ir_calculado=ir_calculado)
    contadas = [r for r in resultado if r.get("contada")]
    resumos = {chave: _oos.resumir(contadas, chave)
               for chave in ("excesso_liquido_ifix", "excesso_bruto_ifix",
                             "excesso_liquido_ew", "excesso_liquido_selic")}
    return {
        "versao": VERSAO, "status": "ok", "params": p,
        "ir_calculado": ir_calculado,
        "fora_do_porte": ["resiliência ROIC − Selic (FII não tem ROIC)",
                          "tetos de setor e de ciclo (o tipo já é o segmento)",
                          "portão qualitativo LLM, piso de qualidade, Score de Entrada"],
        "safras": resultado,
        "resumo": resumos,
        "leitura": _oos.leitura_honesta(resumos["excesso_liquido_ifix"], "IFIX",
                                        alternativa="comprar o IFIX"),
    }


def _contabilizar(linhas: list[dict], cadeia: list[dict], p: Mapping[str, Any], *,
                  ir_calculado: bool) -> list[dict]:
    """Bruto, custo de giro, IR e líquido de cada safra, em cadeia.

    A contabilidade é a do backtest FII (``core.fii_validation``): giro =
    metade da soma das diferenças de peso, entre a carteira anterior JÁ
    DERIVADA pelo retorno e o alvo; giro cheio (1,0) na entrada; ``RastreadorIR``
    com 20% sobre o ganho de capital, provento isento; caixa paga o giro e o
    IR de zerar a posição anterior. Safra incompleta (vigência em curso) é
    contabilizada mas não conta no resumo.
    """
    custo_unit = float(p["transaction_cost"]) + float(p["slippage"])
    rastreador = RastreadorIR(IR_FII_GANHO)
    anterior = pd.Series(dtype=float)
    out: list[dict] = []
    for linha, item in zip(linhas, cadeia):
        base = {k: v for k, v in linha.items() if k not in ("retornos", "renda")}
        base["retornos"] = linha["retornos"]
        if "retornos" in item:
            alvo = pd.Series(item["pesos"], dtype=float)
            giro = portfolio_turnover(anterior, alvo) if not anterior.empty else 1.0
            custo = giro * custo_unit
            ir = rastreador.rebalancear(item["pesos"]) if ir_calculado else 0.0
            bruto = float(sum(w * float(item["retornos"].get(tk, 0.0))
                              for tk, w in item["pesos"].items()))
            if ir_calculado:
                rastreador.evoluir(item["retornos"], linha.get("renda") or {})
            derivada = {tk: w * (1.0 + float(item["retornos"].get(tk, 0.0)))
                        for tk, w in item["pesos"].items()}
            soma = sum(derivada.values())
            anterior = (pd.Series({tk: v / soma for tk, v in derivada.items()})
                        if soma > 0 else pd.Series(dtype=float))
            base.update(caixa=False, bruto=bruto, giro=giro, custo=custo, ir=ir)
        elif "rendimento_caixa" in item:
            giro = portfolio_turnover(anterior, pd.Series(dtype=float)) if not anterior.empty else 0.0
            custo = giro * custo_unit
            ir = rastreador.rebalancear({}) if ir_calculado else 0.0
            bruto = float(item["rendimento_caixa"]) * min(linha["meses"], 12) / 12.0
            anterior = pd.Series(dtype=float)
            base.update(caixa=True, bruto=bruto, giro=giro, custo=custo, ir=ir)
        else:
            base.update(caixa=False, bruto=None, giro=None, custo=None, ir=None,
                        contada=False, motivo_fora="aquecimento ou sem mercado observado")
            out.append(base)
            continue
        liquido = base["bruto"] - base["custo"] - base["ir"]
        base["liquido"] = liquido
        for nome, ref in (("ifix", linha.get("ifix")), ("ew", linha.get("ew")),
                          ("selic", linha.get("selic"))):
            if ref is None:
                base[f"excesso_liquido_{nome}"] = None
                continue
            ref_v = float(ref) * (min(linha["meses"], 12) / 12.0 if nome == "selic" else 1.0)
            base[f"excesso_liquido_{nome}"] = liquido - ref_v
            if nome == "ifix":
                base["excesso_bruto_ifix"] = base["bruto"] - ref_v
        base.setdefault("excesso_bruto_ifix", None)
        base["contada"] = bool(linha.get("completa"))
        if not base["contada"]:
            base["motivo_fora"] = f"vigência em curso ({linha['meses']} de 12 meses)"
        out.append(base)
    return out
