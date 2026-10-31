"""
core/b3_oos_carteira.py — fora da amostra da CARTEIRA montada, por perfil.

`data/vantagem_oos.json` mede o Rank-IC do RANKING, na configuração padrão da
aba, bruto de custos e sem portão nenhum (auditoria B3-03). Isso responde "o
score ordena?" -- não responde "a carteira que a aba entrega a cada perfil
teria batido o mercado depois de pagar o giro?". As duas perguntas divergem
porque entre o ranking e a carteira há quatro decisões que o Rank-IC não vê:
a APROVAÇÃO do segmento (margem sobre a Selic, teste de seleção, recência,
resiliência), a escolha de SÓ líder + maior participação dentro do top-n, o
orçamento igual por segmento e os tetos (por ativo, setor e classe de ciclo).

Este módulo refaz essas quatro decisões safra a safra, point-in-time:

- **Aprovação PIT.** Para a safra N (vigência abril/N a março/N+1) o
  backtest do segmento é recortado em março/N e a validação de seleção usa os
  `janela_val_anos` anos que terminam ali -- a mesma conta de
  `views/portfolio_b3._processar_segmento`, só que parada no tempo. Aprovar
  com o backtest inteiro (até hoje) seria escolher o segmento sabendo como
  ele foi.
- **Carteira PIT.** Líder e pesos vêm de `lids_por_ano[N]`/`pesos_por_ano[N]`
  (score com lag=1, dados até N-1), a maior participação vem do backtest
  recortado, e o resto é a montagem da tela (orçamento por segmento,
  `project_capped_simplex`, `project_dual_capped`).
- **Portões determinísticos PIT (desde a 2.32.0).** O piso de qualidade (nos
  perfis que o ligam) e o Score de Entrada julgam a safra com o retrato que
  ela teria lido: múltiplos e série de PL cortados em N-1 pela regra de
  vintage do score (`core.b3_retrato_pit`). `aplicar_piso` roda antes dos
  portões; a guarda (``exclui``) barra o substituto de cada veto e, em
  `montar_carteira`, quem sobrou -- a ordem da tela.
- **Retorno líquido.** O retorno bruto de cada papel na janela é o de
  `core.b3_safras.retorno_da_safra` (mesmas pontas, mesma winsorização); o
  líquido desconta o giro do rebalanceamento de abril com
  `CostConfig.brasil_pf_default()` -- compra E venda pagam corretagem e meia
  spread, e o IR entra como sensibilidade à parte (depende do porte).

O que fica FORA, e por quê, é devolvido em `FORA_DO_PIT` e vai para o JSON e a
tela: cada item ali é uma decisão da carteira de hoje que não dá para refazer
no passado sem olhar o futuro (o portão de LLM, por exemplo, lê o dossiê de
HOJE). A medição não finge cobrir o que não cobre.

Módulo puro: sem streamlit, sem banco. As funções da view entram como
parâmetro (`simular`, `pode_incluir_maior`) para o core não importar a view.
Coberto por tests/test_b3_oos_carteira.py.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from core import b3_selecao as _selecao
from core.b3_holdings_health import classify_cycle
from core.b3_precos_saneamento import limites_winsor
from core.b3_quality_floor import apply_with_substitution
from core.b3_safras import (
    WINSOR_MIN_PAPEIS,
    WINSOR_PCT_ALTO,
    WINSOR_PCT_BAIXO,
    _janela_de_mercado,
    _preco_nas_pontas,
    bootstrap_excesso,
)
from core.b3_vigencia import REBAL_MONTH, janela_de_vigencia
from core.portfolio_constraints import (
    minimum_assets_for_cap,
    project_capped_simplex,
    project_dual_capped,
)
from core.transaction_costs import CostConfig, custo_compra, custo_venda

logger = logging.getLogger(__name__)

#: 2.0.0: banda do portão por UM veto por safra na carteira (era um por
#: segmento, que empatava as duas variantes quando o segmento tem um só nome).
#: 2.1.0: nome selecionado com peso 0 também é candidato a veto -- o portão
#: real roda antes do filtro de peso, e o substituto entra com o PRÓPRIO peso.
#: 2.2.0: com ``--portao-llm``, o portão de verdade entra como variante à parte
#: (``PORTAO_LLM_MEDIDO``): parecer do LLM sobre o dossiê da época,
#: anonimizado (`core.b3_portao_pit`). A métrica principal segue sem portão.
#: 2.3.0: piso de qualidade e Score de Entrada entram com o retrato da safra
#: (`core.b3_retrato_pit`, score 2.32.0); o substituto de qualquer veto -- da
#: banda ou do portão medido -- passa a pular quem a guarda da safra exclui, e
#: o excluído sai no fim, como no laço final da tela.
VERSAO_MEDICAO = "oos-carteira-2.3.0"
CAMINHO_MEDICAO = Path(__file__).resolve().parents[1] / "data" / "oos_carteira_b3.json"

#: Mesmo piso da view para a janela de validação valer: menos que 18 meses é
#: holdout episódico.
MIN_LINHAS_VALIDACAO = 18
#: Carteira nocional da sensibilidade de IR. A corretagem default é zero, então
#: o custo de transação não depende do porte; o IR depende (isenção de R$ 20 mil
#: de vendas no mês) e por isso sai à parte, com o porte declarado.
CAPITAL_NOCIONAL = 100_000.0

SEM_PORTAO = "sem_portao"
PORTAO_VETA_O_MELHOR = "portao_veta_o_melhor"
PORTAO_VETA_O_PIOR = "portao_veta_o_pior"
VARIANTES = (SEM_PORTAO, PORTAO_VETA_O_MELHOR, PORTAO_VETA_O_PIOR)
# Fora de VARIANTES: só existe quando a rodada chama o LLM (``--portao-llm``).
PORTAO_LLM_MEDIDO = "portao_llm_medido"

PORTAO_LLM = {
    "dentro_da_medicao": False,
    "por_que": (
        "O parecer de LLM e o portão da Inteligência dos Ativos leem o dossiê "
        "de HOJE (notícias, fatos relevantes, veredito e balanços até a data "
        "da chamada). Aplicá-los a uma safra passada seria vetar com o que só "
        "se soube depois -- o número sairia melhor do que qualquer investidor "
        "teria conseguido. O de LLM ainda é opcional (desligado por padrão) e "
        "não determinístico, e tem medição direta à parte (medicao_direta); "
        "o da Inteligência fica sempre ligado, então a carteira entregue "
        "passa por ele e a medição só o cobre pela banda."),
    "como_medido": (
        "Banda de ATÉ UM veto por safra, no nível da carteira. Em cada safra a "
        "medição testa todas as opções -- não vetar e vetar cada nome "
        "selecionado, inclusive a maior participação que entra sem peso (ele "
        "sai de todo segmento em que foi escolhido e o próximo do ranking da "
        "safra no segmento herda a vaga, com o peso próprio ou, sem ele, o do "
        "vetado; sem próximo no ranking, o segmento sai e seu orçamento vai "
        "para os demais) --, remonta com cap e tetos e fica com o veto que "
        "MAIS derruba o retorno BRUTO da safra ('veta o melhor', adversário) "
        "e com o que MAIS o sobe ('veta o pior', favorável). No bruto são "
        "limites estritos para qualquer portão que vete no máximo um nome por "
        "safra, e o sem-portão fica sempre entre eles. No líquido não são: o "
        "veto muda o giro, e os números líquidos dessas variantes são os dos "
        "cenários escolhidos pelo bruto. Portão que vete mais de um nome por safra pode "
        "sair da banda. O substituto pula quem o Score de Entrada da SAFRA "
        "exclui e o excluído sai no fim, como na tela (desde a 2.3.0; antes "
        "a guarda lia o retrato de hoje e ficava fora). Até a medição "
        "1.0.0 o veto era um por SEGMENTO; como quase todo segmento escolhe "
        "só o líder, 'o melhor' e 'o pior' eram o mesmo papel, as duas "
        "variantes empatavam e mediam, na prática, 'trocar todos os líderes "
        "pelo segundo do ranking'."),
    "medicao_direta": (
        "Variante 'portão de LLM medido' (só nas rodadas com --portao-llm): o "
        "LLM de produção, com o prompt de produção, lê o dossiê REMONTADO na "
        "data da decisão (balanços até N-1, trimestres até o 4T de N-1, preço "
        "e dividendos até 31/03/N) e o veredito decide quem sai, com a mesma "
        "regra da tela (até 2 candidatos por veto, falha nunca veta). Para o "
        "modelo não usar o que sabe do futuro, o dossiê sai anonimizado: "
        "código e nome trocados, só o setor amplo, anos relativos, valores em "
        "R$ e por ação multiplicados por fatores ocultos e o macro em faixas. "
        "Uma sonda pergunta ao próprio modelo qual empresa e qual ano ele "
        "acha que é; a taxa de acerto sai junto do número, porque anonimizar "
        "não garante nada para empresa grande e conhecida. O dossiê da época "
        "vê MENOS que o de produção: sem documentos CVM, notícias, métricas "
        "do provedor e detalhe do armazém (o acervo não cobre as safras). Os "
        "balanços são os de hoje, inclusive reapresentações posteriores. "
        "Empresa que saiu da bolsa não está no market.*: o dossiê dela sai da "
        "DFP da CVM (primeira versão entregue até o corte, a mesma fonte com "
        "que o score a reconstrói), sem trimestres e com proventos estimados "
        "pelo caixa pago; preço, faixa e valor de mercado que atravessam "
        "evento de capital não detectado ficam em branco em vez de errados."),
}

FORA_DO_PIT = (
    "Portão da Inteligência dos Ativos (sempre ligado) e parecer de LLM: leem "
    "o veredito e o dossiê de HOJE. Ficam para a banda das variantes 'veta o "
    "melhor'/'veta o pior'; o de LLM tem ainda a medição direta sobre o "
    "dossiê da época (--portao-llm) -- ver portao_llm.",
    "Piso de qualidade e Score de Entrada ENTRAM desde a 2.32.0, com o "
    "retrato da safra (exercícios até N-1, vintage do score). Duas "
    "diferenças restam: o retrato da safra é o último exercício anual, e o "
    "de hoje é o TTM; e a série de PL e lucro não tem data de publicação, "
    "então o exercício N-1 conta como público em abril/N pelo prazo da CVM. "
    "A aprovação do segmento (backtest por segmento) segue sem os dois "
    "portões, como na tela.",
    "Troca por classe de liquidez e substituição por correlação/Markowitz: "
    "dependem do retrato atual.",
    "Camada macro local: o contexto de hoje, sem histórico por safra.",
    "Filtros de valor de mercado e liquidez do perfil (desde a 2.31.0, cada "
    "safra usa o volume e o valor de mercado da época): o valor de mercado "
    "da época é ESTIMADO pelo de hoje vezes a razão de preços sem "
    "dividendos, então emissões e recompras posteriores não entram; quem "
    "não tem valor de mercado hoje não recebe marca de tamanho no passado.",
    "Spread de ROIC (perfil Conservador): recortado até N-1 aqui, mas na tela "
    "usa todos os anos -- a medição é mais estrita que a tela.",
)


# ── aprovação PIT ───────────────────────────────────────────────────────────

def _margem_pct(valor: float, referencia: float) -> float:
    """Espelho de `views/portfolio_b3._margem_pct`: 0 quando não há base."""
    try:
        v, r = float(valor), float(referencia)
    except (TypeError, ValueError):
        return 0.0
    if not (np.isfinite(v) and np.isfinite(r)) or r <= 0:
        return 0.0
    return (v / r - 1.0) * 100.0


def _corte_ate_safra(df: pd.DataFrame, ano_inicio: int, safra: int) -> pd.DataFrame:
    """Linhas de abril/ano_inicio até março/safra (exclusive abril/safra)."""
    if df.empty:
        return df
    idx = pd.DatetimeIndex(df.index)
    inicio = pd.Timestamp(year=int(ano_inicio), month=REBAL_MONTH, day=1)
    fim = pd.Timestamp(year=int(safra), month=REBAL_MONTH, day=1)
    return df[(idx >= inicio) & (idx < fim)]


def _ic_medio(ic_pairs, ate_ano: int) -> float:
    """Rank-IC médio dos anos <= ate_ano com pelo menos 5 pares (regra da view)."""
    por_ano: dict[int, list[tuple[float, float]]] = {}
    for ano, score, ret in ic_pairs or []:
        if int(ano) <= int(ate_ano):
            por_ano.setdefault(int(ano), []).append((float(score), float(ret)))
    ics: list[float] = []
    for pares in por_ano.values():
        if len(pares) < 5:
            continue
        s = pd.Series([p[0] for p in pares])
        r = pd.Series([p[1] for p in pares])
        ic = s.corr(r, method="spearman")
        if pd.notna(ic) and np.isfinite(ic):
            ics.append(float(ic))
    return float(np.mean(ics)) if ics else float("nan")


def metricas_pit(res: dict, safra: int, df_precos_all: pd.DataFrame, *,
                 simular: Callable, cost_cfg: CostConfig | None = None) -> dict:
    """As métricas que `_aprovado` lê, recalculadas com dados até março/safra.

    `res["_oos_ctx"]` vem do embrulho do script de medição: os argumentos com
    que a view chamou `_processar_segmento` (universo de entrada, ROIC por ano,
    aporte, Selic). `simular` é `views.portfolio_b3._simular_seg_backtest`.
    """
    ctx = res.get("_oos_ctx") or {}
    cost_cfg = cost_cfg or CostConfig.brasil_pf_default()
    ano_inicio = int(ctx.get("ano_inicio"))
    janela_val = int(ctx.get("janela_val_anos", 2))
    aporte = float(ctx.get("aporte", 1000.0))
    taxa_selic_aa = float(ctx.get("taxa_selic_aa", 0.1075))
    selic_macro = ctx.get("selic_macro") or {}

    tks = [tk for tk in (ctx.get("tickers_entrada") or [])
           if tk in df_precos_all.columns]
    lids = {a: v for a, v in (res.get("lids_por_ano") or {}).items() if int(a) < safra}
    pesos = {a: v for a, v in (res.get("pesos_por_ano") or {}).items() if int(a) < safra}

    out = {"val_est": 0.0, "val_selic": 0.0, "val_ew": 0.0, "maior": None,
           "selecao_excesso": _selecao.intervalo_excesso([]),
           "n_linhas": 0}
    if tks and not df_precos_all.empty:
        df_seg = _corte_ate_safra(df_precos_all[tks].dropna(how="all"),
                                  ano_inicio, safra)
        out["n_linhas"] = int(len(df_seg))
        if not df_seg.empty:
            v_est, v_sel, v_ew, contrib = simular(
                df_seg, lids, pesos, aporte, taxa_selic_aa, selic_macro,
                dividendos=None, cost_cfg=cost_cfg)
            out.update(val_est=float(v_est), val_selic=float(v_sel),
                       val_ew=float(v_ew),
                       maior=max(contrib, key=contrib.get) if contrib else None)
            ultimo = pd.Timestamp(df_seg.index.max())
            ini_val = pd.Timestamp(year=int(ultimo.year) - janela_val,
                                   month=REBAL_MONTH, day=1)
            df_val = df_seg[pd.DatetimeIndex(df_seg.index) >= ini_val]
            if len(df_val) >= MIN_LINHAS_VALIDACAO:
                det: dict = {}
                simular(df_val, lids, pesos, aporte, taxa_selic_aa, selic_macro,
                        dividendos=None, cost_cfg=cost_cfg, details_out=det)
                mensal = pd.DataFrame(det.get("monthly_returns") or [])
                if len(mensal) >= 12:
                    exc = (pd.to_numeric(mensal["strategy"], errors="coerce")
                           - pd.to_numeric(mensal["equal_weight"], errors="coerce")
                           ).replace([np.inf, -np.inf], np.nan).dropna()
                    out["selecao_excesso"] = _selecao.intervalo_excesso(exc.tolist())

    out["rank_ic_mean"] = _ic_medio(res.get("ic_pairs"), safra - 1)

    hist: dict[str, list[int]] = {}
    for ano, nomes in lids.items():
        for tk in nomes:
            hist.setdefault(tk, []).append(int(ano))
    out["ultimo_lid"] = {tk: max(anos) for tk, anos in hist.items()}

    # ROIC − Selic só com exercícios <= N-1 (a view usa todos -- ver FORA_DO_PIT).
    lideres = list(hist) or list(ctx.get("tickers_entrada") or [])
    spreads: list[float] = []
    roic = ctx.get("roic") or {}
    for tk in lideres:
        for ano, valor in roic.get(tk, []):
            if int(ano) > safra - 1:
                continue
            sel = selic_macro.get(int(ano))
            if sel is not None and valor is not None and np.isfinite(valor):
                spreads.append(float(valor) - float(sel))
    if spreads:
        arr = np.asarray(spreads, dtype=float)
        out["roic_spread_mean"] = float(arr.mean())
        out["roic_hit_rate"] = float((arr > 0).mean())
    else:
        out["roic_spread_mean"] = float("nan")
        out["roic_hit_rate"] = float("nan")
    return out


def aprova_economico(m: dict, params: dict, safra: int) -> tuple[bool, str]:
    """Espelho do `_aprovado` da view no modo Econômico (Brasil), com as
    métricas PIT. Os três perfis do código usam esse modo; outro modo levanta,
    para a medição não fingir que cobriu um critério que não implementa."""
    if params.get("criterio_modo", "economico") != "economico":
        raise ValueError("medição PIT só implementa o critério Econômico (Brasil)")
    if m.get("val_est", 0.0) <= 0:
        return False, "sem patrimônio no backtest"
    if _margem_pct(m["val_est"], m.get("val_selic", 0.0)) < float(params["thr_selic"]):
        return False, "margem sobre a Selic"
    if params.get("usar_ew") and m.get("val_ew", 0.0) > 0:
        if _margem_pct(m["val_est"], m["val_ew"]) < float(params.get("thr_ew", 0.0)):
            return False, "margem sobre pesos iguais"
    ic = float(m.get("rank_ic_mean", float("nan")))
    if np.isfinite(ic) and ic < -0.05:
        return False, "Rank-IC contra"
    if _selecao.reprova(m.get("selecao_excesso"),
                        bool(params.get("exigir_vantagem_selecao", False))):
        return False, "teste de seleção"
    ultimo = max(m.get("ultimo_lid", {}).values()) if m.get("ultimo_lid") else 0
    if (int(safra) - 1 - int(ultimo)) > int(params["max_anos_lid"]):
        return False, "liderança antiga"
    if params.get("exigir_resiliencia"):
        rs = float(m.get("roic_spread_mean", float("nan")))
        hr = float(m.get("roic_hit_rate", float("nan")))
        if not np.isfinite(rs) or rs < float(params.get("thr_roic_spread", 0.0)):
            return False, "resiliência (ROIC − Selic)"
        if np.isfinite(hr) and hr < 0.5:
            return False, "resiliência (anos acima da Selic)"
    return True, ""


# ── carteira da safra ───────────────────────────────────────────────────────

def _scores_do_ano(res: dict, safra: int) -> dict[str, float]:
    return {str(r["ticker"]): float(r["Score_Ajustado"])
            for r in (res.get("score_rows") or []) if int(r["Ano"]) == int(safra)}


def selecao_do_segmento(res: dict, m: dict, safra: int, *, max_anos_lid: int,
                        pode_incluir_maior: Callable) -> tuple[list[str], dict, list]:
    """Líder + maior participação PIT, como a tela faz com `score_proximo`.

    Devolve (selecionados, pesos do segmento, ranking). O peso vem de
    `pesos_por_ano[N]`: nome fora do top-n tem peso 0 e é descartado na
    montagem -- mesma regra de `pesos_p.get(tk, 0.0)` na view.
    """
    lids = list((res.get("lids_por_ano") or {}).get(safra) or [])
    pesos = dict((res.get("pesos_por_ano") or {}).get(safra) or {})
    if not lids:
        return [], pesos, []
    scores = _scores_do_ano(res, safra)
    ranking = sorted(scores.items(), key=lambda x: (-float(x[1]), str(x[0])))
    lider = lids[0]
    selecionados = [lider]
    maior = m.get("maior")
    if maior and maior != lider:
        ok = pode_incluir_maior(str(maior), m.get("ultimo_lid", {}), scores,
                                int(safra) - 1, int(max_anos_lid))
        if ok[0]:
            selecionados.append(str(maior))
    return selecionados, pesos, ranking


#: Um segmento na banda do portão: (setor, selecionados, pesos, ranking PIT).
Segmento = tuple[str, list[str], dict, list]

#: Regra de exclusão do Score de Entrada da safra (`_entry_guard_exclui` sobre
#: a guarda montada com o retrato PIT). ``None`` = ninguém excluído.
Exclui = Callable[[str], bool] | None


def _nunca(_tk: str) -> bool:
    return False


def substituto_no_segmento(selecionados: list[str], ranking: list,
                           exclui: Exclui = None) -> str | None:
    """Quem herda a vaga de um vetado: o próximo do ranking PIT do segmento
    que ainda não está na seleção (o vetado está nela, então é pulado) e que
    o Score de Entrada da safra não exclui.

    Mesma ordem de `_aplicar_portao_inteligencia` e
    `_aplicar_gate_qualitativo`. Até a medição 2.2.0 a guarda ficava de fora
    porque lia o retrato de hoje e o sem-portão também não passava por ela;
    desde a 2.3.0 as duas pontas leem a guarda da safra."""
    exclui = exclui or _nunca
    for cand, _sc in ranking:
        if str(cand) not in selecionados and not exclui(str(cand)):
            return str(cand)
    return None


def candidatos_a_veto(segmentos: list[Segmento]) -> list[str]:
    """Nomes que um portão poderia vetar: TODO selecionado, inclusive o de
    peso 0. A maior participação fora do top-n entra na seleção sem peso, e o
    portão da tela (`_aplicar_gate_qualitativo`) roda antes do filtro de
    peso: vetá-lo põe o próximo do ranking com o peso PRÓPRIO dele
    (`pesos.get(sub) or ...`), o que muda a carteira. Quando não muda, o
    empate fica com "não vetar"."""
    nomes = {str(tk) for _setor, sel, _p, _rk in segmentos for tk in sel}
    return sorted(nomes)


def tickers_da_banda(segmentos: list[Segmento], exclui: Exclui = None) -> list[str]:
    """Selecionados mais os substitutos de cada veto possível: os retornos que
    `escolher_veto` precisa para avaliar todas as opções."""
    nomes = {str(tk) for _setor, sel, _p, _rk in segmentos for tk in sel}
    for _setor, sel, _p, ranking in segmentos:
        sub = substituto_no_segmento(sel, ranking, exclui)
        if sub:
            nomes.add(sub)
    return sorted(nomes)


def vetar_na_carteira(segmentos: list[Segmento], vetado: str | None,
                      exclui: Exclui = None,
                      ) -> tuple[list[tuple[str, list[str], dict]], list[dict]]:
    """Aplica UM veto à carteira inteira: o nome sai de todo segmento em que
    foi escolhido (o portão julga o papel, não o segmento) e, em cada um, o
    próximo do ranking que a guarda da safra não exclui herda vaga e peso --
    `pesos[sub] = pesos.get(sub) or pesos.get(vetado)`, como na tela. Não
    muta a entrada. Quem a guarda exclui e não foi vetado sai em
    `montar_carteira(..., exclui=)`, o laço final da tela.

    Devolve os itens no formato de `montar_carteira` e as trocas feitas."""
    itens: list[tuple[str, list[str], dict]] = []
    trocas: list[dict] = []
    for setor, sel, pesos, ranking in segmentos:
        sel_v, pesos_v = list(sel), dict(pesos)
        if vetado is not None and vetado in sel_v:
            sub = substituto_no_segmento(sel_v, ranking, exclui)
            sel_v = [tk for tk in sel_v if tk != vetado]
            if sub:
                sel_v.append(sub)
                pesos_v[sub] = pesos_v.get(sub) or pesos_v.get(vetado, 0.0)
            trocas.append({"sai": vetado, "entra": sub, "setor": setor})
        itens.append((setor, sel_v, pesos_v))
    return itens, trocas


def aplicar_portao_medido(segmentos: list[Segmento], avaliar, *,
                          max_substitutos: int = 2,
                          exclui: Exclui = None) -> tuple[list[tuple[str, list[str], dict]], dict]:
    """O portão de LLM DE VERDADE, segmento a segmento, com a mesma mecânica
    de `views.portfolio_b3._aplicar_gate_qualitativo`: o selecionado vetado
    sai; os próximos do ranking do segmento que não estão na seleção nem já
    entraram são avaliados, até ``max_substitutos``; o primeiro não vetado
    herda a vaga com `pesos.get(sub) or pesos.get(vetado)`. Parecer
    indisponível nunca veta (fica em ``nao_avaliados``).

    ``avaliar(ticker) -> str`` devolve a classificação ('aprovar',
    'aprovar_com_ressalvas', 'vetar' ou qualquer outra coisa = não avaliado).
    Como na tela, o candidato que ``exclui`` (o Score de Entrada da safra)
    barra é pulado sem consumir avaliação. Não muta a entrada.

    Devolve os itens no formato de `montar_carteira` e o log
    ``{"vetados", "trocas", "nao_avaliados", "avaliados"}``."""
    exclui = exclui or _nunca
    itens: list[tuple[str, list[str], dict]] = []
    log: dict = {"vetados": [], "trocas": [], "nao_avaliados": [], "avaliados": {}}

    def _aval(tk: str) -> str:
        if tk not in log["avaliados"]:
            log["avaliados"][tk] = str(avaliar(tk))
        cls = log["avaliados"][tk]
        if (cls not in ("aprovar", "aprovar_com_ressalvas", "vetar")
                and tk not in log["nao_avaliados"]):
            log["nao_avaliados"].append(tk)
        return cls

    for setor, sel, pesos, ranking in segmentos:
        sel = [str(tk) for tk in sel]
        pesos_v = dict(pesos)
        finais: list[str] = []
        for tk in sel:
            if _aval(tk) != "vetar":
                finais.append(tk)
                continue
            log["vetados"].append({"tk": tk, "setor": setor})
            substituto, avaliacoes = None, 0
            for cand, _sc in ranking:
                cand = str(cand)
                if cand in finais or cand in sel or exclui(cand):
                    continue
                avaliacoes += 1
                if _aval(cand) != "vetar":
                    substituto = cand
                    break
                log["vetados"].append({"tk": cand, "setor": setor})
                if avaliacoes >= max_substitutos:
                    break
            if substituto:
                finais.append(substituto)
                pesos_v[substituto] = pesos_v.get(substituto) or pesos_v.get(tk, 0.0)
            log["trocas"].append({"sai": tk, "entra": substituto, "setor": setor})
        itens.append((setor, finais, pesos_v))
    return itens, log


def escolher_veto(segmentos: list[Segmento], retornos: dict[str, float | None], *,
                  veta: str, cap: float, teto_setor: float,
                  teto_ciclico: float, exclui: Exclui = None) -> dict:
    """O limite da banda do portão: o veto ÚNICO da safra que mais derruba
    (`veta="melhor"`, adversário) ou mais sobe (`veta="pior"`, favorável) o
    retorno bruto da CARTEIRA montada.

    Enumera todas as opções -- não vetar e vetar cada nome com peso --,
    remonta com cap e tetos e escolhe pelo retorno bruto (`None` rende 0, a
    regra de `retorno_da_safra`). Escolher pelo impacto, e não pelo maior
    retorno do vetado, é o que torna o limite estrito: o nome que mais rendeu
    pode ter peso pequeno ou um substituto que rendeu quase o mesmo. "Não
    vetar" entra como opção, então no bruto vale sempre
    `adversário <= sem portão <= favorável`. Empate fica com não vetar e,
    entre vetos, com a ordem alfabética (determinístico). ``exclui`` é a
    guarda da safra: vale para o substituto e para o laço final, nas
    mesmas condições do sem-portão."""
    if veta not in ("melhor", "pior"):
        raise ValueError(f"veta deve ser 'melhor' ou 'pior', não {veta!r}")

    def _bruto(pesos_fin: dict[str, float]) -> float:
        return sum(w * float(retornos.get(tk) or 0.0) for tk, w in pesos_fin.items())

    opcoes = []
    for i, vetado in enumerate([None] + candidatos_a_veto(segmentos)):
        itens, trocas = vetar_na_carteira(segmentos, vetado, exclui)
        cart = montar_carteira(itens, cap=cap, teto_setor=teto_setor,
                               teto_ciclico=teto_ciclico, exclui=exclui)
        opcoes.append((_bruto(cart["pesos"]), i, {
            "vetado": vetado, "trocas": trocas, "itens": itens, "carteira": cart}))
    sinal = 1.0 if veta == "melhor" else -1.0
    # Arredondado: veto que não muda nada não pode vencer "não vetar" por
    # ruído de ponto flutuante da reprojeção.
    bruto, _i, escolhida = min(opcoes, key=lambda o: (round(sinal * o[0], 12), o[1]))
    return {**escolhida, "bruto": bruto}


# ── piso de qualidade da safra ──────────────────────────────────────────────

def aplicar_piso(selecionados: list[str], ranking: list, pesos: dict, *,
                 df_decisao: pd.DataFrame | None,
                 piso_ativo: bool,
                 seg_label: str,
                 selic: float,
                 log: dict | None = None) -> list[str]:
    """O piso de qualidade da tela (com substituição, se o perfil o liga),
    julgado com o retrato da SAFRA (``core.b3_retrato_pit``), nunca o de hoje.

    Roda antes dos portões, como na tela. O Score de Entrada não entra aqui:
    na tela ele barra o substituto de cada portão e, no laço final, quem
    sobrou -- é o ``exclui`` de `escolher_veto`, `aplicar_portao_medido` e
    `montar_carteira`. Os nomes de peso 0 também seguem: a banda os veta
    (`candidatos_a_veto`) antes de a montagem descartá-los."""
    sel = [str(tk) for tk in selecionados]
    if piso_ativo and df_decisao is not None and not df_decisao.empty:
        sel = apply_with_substitution(
            sel, ranking, df_decisao, seg_label=seg_label, selic=float(selic),
            pesos=pesos, log=log if log is not None else {},
        )
    return sel


def montar_carteira(itens_por_segmento: list[tuple[str, list[str], dict]], *,
                    cap: float, teto_setor: float, teto_ciclico: float,
                    exclui: Callable[[str], bool] | None = None) -> dict:
    """Orçamento igual por segmento, tetos e cap -- a montagem da tela.

    `itens_por_segmento`: (setor, selecionados, pesos do segmento). Como o
    laço final da tela, descarta quem tem peso 0 e quem ``exclui`` (o Score
    de Entrada da safra) barra, sem substituição. Devolve
    `{"pesos", "inviavel", "exige_revisao", "avisos"}`. Quando a tela
    mostraria a revisão em vez de carteira (poucos ativos para o cap, ou tetos
    que não fecham), os pesos saem mesmo assim e a safra é MARCADA.
    """
    grupos: list[tuple[str, dict[str, float]]] = []
    for setor, sel, pesos in itens_por_segmento:
        locais = {tk: float(pesos.get(tk, 0.0) or 0.0) for tk in sel
                  if exclui is None or not exclui(tk)}
        locais = {tk: w for tk, w in locais.items() if w > 0}
        if locais:
            grupos.append((setor, locais))
    if not grupos:
        return {"pesos": {}, "inviavel": True, "exige_revisao": True, "avisos": []}
    orc = 1.0 / len(grupos)
    pesos_fin: dict[str, float] = {}
    setor_de: dict[str, str] = {}
    for setor, locais in grupos:
        total = sum(locais.values())
        for tk, w in locais.items():
            pesos_fin[tk] = pesos_fin.get(tk, 0.0) + orc * w / total
            setor_de.setdefault(tk, setor)

    inviavel = len(pesos_fin) < minimum_assets_for_cap(cap)
    exige_revisao = inviavel
    avisos: list[str] = []
    if not inviavel:
        pesos_fin = project_capped_simplex(pesos_fin, cap)
        if teto_setor < 1.0 or teto_ciclico < 1.0:
            ciclico = {tk: classify_cycle(setor_de[tk]) == "ciclico" for tk in pesos_fin}
            pesos_fin, avisos = project_dual_capped(
                pesos_fin, setor_de, ciclico, cap, float(teto_setor), float(teto_ciclico))
            excede = (
                any(w > cap + 1e-6 for w in pesos_fin.values())
                or any(sum(w for tk, w in pesos_fin.items() if setor_de[tk] == s)
                       > teto_setor + 1e-6 for s in set(setor_de.values()))
                or sum(w for tk, w in pesos_fin.items() if ciclico[tk])
                > teto_ciclico + 1e-6)
            exige_revisao = bool(excede)
    total = sum(pesos_fin.values())
    if total > 0:
        pesos_fin = {tk: w / total for tk, w in pesos_fin.items()}
    return {"pesos": pesos_fin, "inviavel": inviavel,
            "exige_revisao": exige_revisao, "avisos": list(avisos or [])}


# ── retorno da safra ────────────────────────────────────────────────────────

def retornos_por_ticker(tickers, universo, df_precos: pd.DataFrame, safra: int,
                        saidas: dict | None = None) -> dict[str, float | None]:
    """Retorno bruto de cada papel na vigência, com as regras de
    `retorno_da_safra` (pontas de mercado, tolerância, winsorização pelos
    limites do universo). `None` = sem preço (rende 0 na carteira)."""
    saidas = saidas or {}
    inicio, fim = janela_de_vigencia(int(safra))
    ini_m, corte = _janela_de_mercado(df_precos, inicio, fim)
    observados: dict[str, float] = {}
    for tk in dict.fromkeys(list(universo) + list(tickers)):
        if tk not in df_precos.columns:
            continue
        p = _preco_nas_pontas(df_precos[tk], ini_m, corte, saida=saidas.get(tk))
        if p is not None:
            observados[tk] = p[1] / p[0] - 1.0
    limites = limites_winsor([observados[tk] for tk in universo if tk in observados],
                             pct_baixo=WINSOR_PCT_BAIXO, pct_alto=WINSOR_PCT_ALTO,
                             minimo=WINSOR_MIN_PAPEIS)
    out: dict[str, float | None] = {}
    for tk in tickers:
        r = observados.get(tk)
        if r is not None and limites is not None:
            r = min(max(r, limites[0]), limites[1])
        out[tk] = r
    return out


def retorno_benchmark(serie: pd.Series | None, df_precos: pd.DataFrame,
                      safra: int) -> float | None:
    """Retorno do Ibovespa (proxy) nas MESMAS pontas de mercado da safra."""
    if serie is None or serie.empty:
        return None
    inicio, fim = janela_de_vigencia(int(safra))
    ini_m, corte = _janela_de_mercado(df_precos, inicio, fim)
    p = _preco_nas_pontas(serie, ini_m, corte)
    return None if p is None else p[1] / p[0] - 1.0


# ── custos do giro ──────────────────────────────────────────────────────────

def simular_custos(safras: list[dict], cfg: CostConfig, *,
                   capital: float = CAPITAL_NOCIONAL) -> list[dict]:
    """Encadeia as safras com rebalanceamento anual em abril.

    Cada item de `safras`: `{"safra", "pesos", "retornos"}` (retorno bruto por
    papel, `None` = 0) ou `{"safra", "pesos": {}}` quando não houve carteira.
    No rebalanceamento, compra paga `custo_compra` e venda paga a parte de
    corretagem+spread de `custo_venda`: na primeira safra o giro é 100% de
    compra; nas seguintes, só a diferença entre a carteira-alvo e a que DERIVOU
    na safra anterior.

    IR (sensibilidade, `liquido_com_ir`): 15% sobre o ganho realizado nas
    vendas, custo médio, prejuízo compensado nas safras seguintes, isento no
    mês com vendas <= `cfg.isencao_mes` -- com carteira de `capital`. Sai à
    parte porque depende do porte; o custo de transação não depende
    (corretagem default zero).

    Safra sem carteira: o investidor fica em caixa; a seguinte parte de caixa.
    """
    saida: list[dict] = []
    valor = float(capital)
    pos: dict[str, float] = {}      # valor de mercado por papel
    base: dict[str, float] = {}     # custo médio (para IR)
    valor_ir = float(capital)
    pos_ir: dict[str, float] = {}
    base_ir: dict[str, float] = {}
    prejuizo = 0.0
    for s in safras:
        pesos = s.get("pesos") or {}
        if not pesos:
            valor = sum(pos.values()) if pos else valor
            valor_ir = sum(pos_ir.values()) if pos_ir else valor_ir
            pos, base, pos_ir, base_ir = {}, {}, {}, {}
            saida.append({"safra": s["safra"], "medida": False})
            continue
        rets = {tk: float(s["retornos"].get(tk) or 0.0) for tk in pesos}
        bruto = sum(w * rets[tk] for tk, w in pesos.items())

        def _rebalancear(valor_atual, posicoes, bases, com_ir):
            nonlocal prejuizo
            alvo = {tk: w * valor_atual for tk, w in pesos.items()}
            custo = 0.0
            vendas = 0.0
            realizado = 0.0
            for tk in set(posicoes) | set(alvo):
                atual = posicoes.get(tk, 0.0)
                delta = alvo.get(tk, 0.0) - atual
                if delta > 1e-9:
                    custo += custo_compra(tk, delta, cfg)
                elif delta < -1e-9:
                    venda = -delta
                    fee, _ = custo_venda(tk, venda, 0.0, 0.0, cfg)
                    custo += fee
                    vendas += venda
                    if com_ir and atual > 0:
                        b = bases.get(tk, atual)
                        realizado += venda - b * venda / atual
            ir = 0.0
            if com_ir:
                liquido = realizado + prejuizo
                if vendas <= cfg.isencao_mes:
                    # mês isento: ganho não paga; prejuízo realizado se acumula
                    if realizado < 0:
                        prejuizo += realizado
                elif liquido > 0:
                    ir = cfg.ir_rate * liquido
                    prejuizo = 0.0
                else:
                    prejuizo = liquido
            investido = valor_atual - custo - ir
            fator = investido / valor_atual if valor_atual > 0 else 0.0
            novas_pos = {tk: alvo[tk] * fator * (1.0 + rets[tk]) for tk in alvo}
            novas_bases: dict[str, float] = {}
            for tk in alvo:
                atual = posicoes.get(tk, 0.0)
                b = bases.get(tk, atual)
                mantido = min(atual, alvo[tk])
                b_mant = b * mantido / atual if atual > 0 else 0.0
                comprado = max(alvo[tk] - atual, 0.0)
                novas_bases[tk] = (b_mant + comprado) * fator
            # Giro = fração comprada (um lado): 100% na primeira safra e numa
            # troca total, 0% quando a carteira-alvo é a que derivou.
            giro = sum(max(alvo.get(tk, 0.0) - posicoes.get(tk, 0.0), 0.0)
                       for tk in set(posicoes) | set(alvo)) / valor_atual
            return novas_pos, novas_bases, custo, ir, giro, investido

        pos, base, custo, _, giro, investido = _rebalancear(valor, pos, base, False)
        liquido = sum(pos.values()) / valor - 1.0
        valor_ini = valor
        valor = sum(pos.values())
        pos_ir, base_ir, custo_i, ir, _, _ = _rebalancear(valor_ir, pos_ir, base_ir, True)
        liquido_ir = sum(pos_ir.values()) / valor_ir - 1.0
        valor_ir = sum(pos_ir.values())
        saida.append({
            "safra": s["safra"], "medida": True,
            "bruto": bruto, "liquido": liquido, "liquido_com_ir": liquido_ir,
            "custo_pct": custo / valor_ini, "ir_pct": ir / max(valor_ir, 1e-12),
            "giro": giro,
        })
    return saida


# ── resumo ──────────────────────────────────────────────────────────────────

def resumir(linhas: list[dict], chave: str) -> dict:
    """Média, IC 95% por bootstrap (o mesmo de `core.b3_safras`), n e anos
    negativos de uma coluna de excesso."""
    pares = [(int(r["safra"]), float(r[chave])) for r in linhas
             if r.get(chave) is not None and np.isfinite(float(r[chave]))]
    valores = [v for _, v in pares]
    lo, hi = bootstrap_excesso(valores)
    # Robustez a uma safra só: no perfil Amplo (2.30.0) a safra 2017 rendeu
    # +57,7 pp sobre pesos iguais e sozinha leva a média de ~+4 para +10 pp.
    # Média com IC que exclui o zero e que depende de um ano não é a mesma
    # evidência que nove anos parecidos -- a tela precisa dizer qual é.
    melhor = max(pares, key=lambda x: x[1]) if len(pares) >= 3 else None
    resto = [v for a, v in pares if melhor is None or a != melhor[0]]
    return {
        "media": float(np.mean(valores)) if valores else None,
        "ic95": [lo, hi],
        "n_safras": len(valores),
        "safras_negativas": [a for a, v in pares if v < 0],
        "ic_cruza_zero": (lo is None or hi is None or (lo <= 0 <= hi)),
        "melhor_safra": melhor[0] if melhor else None,
        "media_sem_melhor_safra": float(np.mean(resto)) if melhor else None,
    }


def leitura_honesta(resumo: dict, rotulo: str = "pesos iguais") -> str:
    """Uma frase que não promete mais do que o intervalo sustenta."""
    n = resumo.get("n_safras") or 0
    media = resumo.get("media")
    lo, hi = (resumo.get("ic95") or [None, None])
    if n < 2 or media is None or lo is None:
        return f"Amostra insuficiente ({n} safra(s)): sem intervalo, sem conclusão."
    neg = len(resumo.get("safras_negativas") or [])
    base = (f"Excesso médio de {media * 100:+.1f} pp por safra sobre {rotulo}, "
            f"IC 95% [{lo * 100:+.1f}; {hi * 100:+.1f}] pp, {n} safras, "
            f"{neg} negativa(s).")
    sem_melhor = resumo.get("media_sem_melhor_safra")
    robustez = ""
    if sem_melhor is not None and resumo.get("melhor_safra") is not None:
        robustez = (f" Sem a melhor safra ({resumo['melhor_safra']}), a média "
                    f"seria {sem_melhor * 100:+.1f} pp.")
    if lo <= 0 <= hi:
        return base + (" O intervalo cruza o zero: a medição NÃO distingue esta "
                       "carteira de dividir igualmente entre as empresas.") + robustez
    if hi < 0:
        return base + " O intervalo está todo abaixo de zero: a carteira perdeu." + robustez
    return base + " O intervalo exclui o zero." + robustez


def carregar(caminho: Path | str | None = None) -> dict | None:
    """Medição gravada, ou None se não houver/ilegível."""
    p = Path(caminho) if caminho else CAMINHO_MEDICAO
    try:
        dados = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.info("oos_carteira indisponivel: %s", type(exc).__name__)
        return None
    return dados if isinstance(dados, dict) else None


def vencida(dados: dict | None, versao_atual: str, versao_presets: str,
            versao_medicao: str = VERSAO_MEDICAO) -> list[str]:
    """Motivos pelos quais a medição gravada não vale para o código atual.

    A versão da MEDIÇÃO conta também: com o score e os perfis iguais, a banda
    do portão da 1.0.0 (um veto por segmento) leria como a da 2.0.0."""
    if not dados:
        return ["sem medição gravada"]
    motivos = []
    if str(dados.get("versao_medicao")) != str(versao_medicao):
        motivos.append(f"medição {dados.get('versao_medicao')} ≠ {versao_medicao}")
    if str(dados.get("versao_metodologia")) != str(versao_atual):
        motivos.append(f"metodologia {dados.get('versao_metodologia')} ≠ {versao_atual}")
    if str(dados.get("versao_presets")) != str(versao_presets):
        motivos.append(f"perfis {dados.get('versao_presets')} ≠ {versao_presets}")
    return motivos


__all__ = [
    "VERSAO_MEDICAO", "CAMINHO_MEDICAO", "VARIANTES", "PORTAO_LLM", "FORA_DO_PIT",
    "SEM_PORTAO", "PORTAO_VETA_O_MELHOR", "PORTAO_VETA_O_PIOR",
    "metricas_pit", "aprova_economico", "selecao_do_segmento",
    "substituto_no_segmento", "candidatos_a_veto", "tickers_da_banda",
    "vetar_na_carteira", "aplicar_portao_medido", "escolher_veto",
    "aplicar_piso", "montar_carteira", "retornos_por_ticker",
    "retorno_benchmark", "simular_custos", "resumir", "leitura_honesta",
    "carregar", "vencida",
]
