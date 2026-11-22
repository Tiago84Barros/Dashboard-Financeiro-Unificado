"""
core/b3_estrategias_fundamentalistas.py — quatro carteiras "à moda dos grandes
fundamentalistas", para medir fora da amostra contra pesos iguais.

Pergunta (22/11/2026): a carteira padrão da B3 não se distingue de dividir
igualmente entre as empresas. A nota do motor já mistura qualidade, ROIC,
dívida, preço e dividendos (`core.b3_company_score.FACTOR_TRACKS`), então
somar "mais Buffett" à mesma nota tende a não mudar nada. Este módulo testa
outra coisa: REGRAS SIMPLES, que um investidor repete em casa, com a lógica
de quem as criou.

As quatro regras, fixadas ANTES de medir (não são ajustadas depois):

1. ``rota_valor`` — a rota de valor que o app já tem (`core.b3_value_route`:
   margem de segurança de Graham e Bazin + trava de solvência), aplicada ao
   retrato da safra. Compra, em pesos iguais, quem sai como ``oportunidade``.
   Nunca tinha sido medida fora da amostra.
2. ``formula_magica`` — Greenblatt: soma do posto de ROIC (retorno sobre o
   capital) e do posto do rendimento operacional (1/EV_EBIT). Ficam o terço
   melhor, no mínimo ``MIN_NOMES``. Como no livro, financeiras saem (EV/EBIT
   e ROIC não descrevem banco) e só entra quem tem os dois números com
   EV_EBIT > 0.
3. ``qualidade_constante`` — o "compounder" do Buffett: ROIC acima da Selic
   do ano em pelo menos ``FRACAO_ANOS_ACIMA`` dos últimos ``JANELA_ANOS``
   exercícios (mínimo ``MIN_ANOS`` exercícios com ROIC), lucro positivo no
   último exercício e maior que o de três exercícios antes, e nenhuma falha
   da trava de solvência da rota de valor (ausência de dado não é falha).
   Pesos iguais entre quem passa.
4. ``qualidade_3_anos`` — a regra 3 segurada por ``ANOS_SEGURANDO`` anos: a
   lista é refeita na primeira safra da cadeia e a cada três; nas safras do
   meio só SAI quem quebrou a trava de solvência ou deixou o universo. Os
   pesos voltam a ser iguais entre os que ficam.

Todas leem só o que se sabia em abril/N: o retrato e o histórico da safra
(exercícios até N-1, pela regra de vintage do motor), os múltiplos com o preço
de fim do exercício (``data_pipeline.market.ingest``, histórico anual) e a
Selic de cada ano. Safra sem nenhum nome vira caixa a Selic (regra D1, em
`core.b3_oos_carteira.itens_da_cadeia`).

Puro: sem banco, sem Streamlit. A medição é
`scripts/medir_estrategias_fundamentalistas_b3.py`; os testes,
`tests/test_b3_estrategias_fundamentalistas.py`.
"""
from __future__ import annotations

import math
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from core.b3_value_route import OPORTUNIDADE, rank_value_opportunities

VERSAO = "fundamentalistas-1.0.0"

ROTA_VALOR = "rota_valor"
FORMULA_MAGICA = "formula_magica"
QUALIDADE_CONSTANTE = "qualidade_constante"
QUALIDADE_3_ANOS = "qualidade_3_anos"
ESTRATEGIAS = (ROTA_VALOR, FORMULA_MAGICA, QUALIDADE_CONSTANTE, QUALIDADE_3_ANOS)

#: Fórmula mágica: fração do universo elegível que fica e o piso de nomes.
FRACAO_FORMULA = 1 / 3
MIN_NOMES = 10

#: Qualidade constante.
JANELA_ANOS = 7
MIN_ANOS = 5
FRACAO_ANOS_ACIMA = 0.70
ANOS_CRESCIMENTO = 3

#: Quantas safras a carteira de qualidade fica parada.
ANOS_SEGURANDO = 3

#: Setores que a fórmula mágica exclui (comparação sem acento e sem caixa).
PREFIXOS_SETOR_EXCLUIDO = ("financ",)

#: Regra de decisão, registrada antes de medir. Quatro braços elevam a chance
#: de um falso positivo de 1 em 20 para perto de 1 em 5; por isso não basta o
#: IC excluir o zero contra pesos iguais.
REGRA_DE_DECISAO = (
    "uma estratégia só vira candidata a carteira padrão se, LÍQUIDA do giro: "
    "(a) o excesso médio sobre pesos iguais for > 0 com IC 95% acima de zero; "
    "(b) a média sem a melhor safra continuar > 0; e (c) a diferença pareada "
    "contra a carteira padrão atual (Equilibrado, sem portão) tiver média > 0. "
    "Com quatro braços, passar só em (a) não basta. Nenhuma passando, nada muda "
    "na tela.")


def _norm(texto) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).strip().lower()


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    if df is None or col not in df.columns:
        return pd.Series(np.nan, index=getattr(df, "index", None), dtype=float)
    return pd.to_numeric(df[col], errors="coerce").replace([np.inf, -np.inf], np.nan)


def _do_universo(df_pit: pd.DataFrame | None, universo: Iterable[str]) -> pd.DataFrame:
    """As linhas do retrato que estão no universo, uma por ticker."""
    if df_pit is None or df_pit.empty or "Ticker" not in df_pit.columns:
        return pd.DataFrame(columns=["Ticker"])
    alvos = {str(t).upper() for t in universo}
    d = df_pit.copy()
    d["Ticker"] = d["Ticker"].astype(str).str.upper()
    d = d[d["Ticker"].isin(alvos)]
    return d.drop_duplicates("Ticker", keep="last").reset_index(drop=True)


def pesos_iguais(nomes: Iterable[str]) -> dict[str, float]:
    lista = sorted(dict.fromkeys(str(t) for t in nomes))
    return {t: 1.0 / len(lista) for t in lista} if lista else {}


# ── 1. Rota de valor ────────────────────────────────────────────────────────
def carteira_rota_valor(df_pit: pd.DataFrame | None, universo: Iterable[str], *,
                        selic: float | None = None) -> list[str]:
    """Quem a rota de valor classifica como ``oportunidade`` no retrato da safra."""
    quadro = _do_universo(df_pit, universo)
    if quadro.empty:
        return []
    ranked = rank_value_opportunities(quadro, selic=selic)
    return sorted(ranked.loc[ranked["classificacao"] == OPORTUNIDADE, "Ticker"].astype(str))


def falhas_de_solvencia(df_pit: pd.DataFrame | None,
                        universo: Iterable[str]) -> dict[str, list[str]]:
    """Falhas da trava de solvência da rota de valor, por ticker do retrato.

    Ticker sem falha sai com lista vazia; ticker fora do retrato não aparece
    (não há como dizer que falhou). Ausência de dado não é falha.
    """
    quadro = _do_universo(df_pit, universo)
    if quadro.empty:
        return {}
    ranked = rank_value_opportunities(quadro)
    return {str(t): list(f) for t, f in zip(ranked["Ticker"], ranked["falhas_solvencia"])}


# ── 2. Fórmula mágica ───────────────────────────────────────────────────────
def carteira_formula_magica(df_pit: pd.DataFrame | None, universo: Iterable[str], *,
                            setor_de: Mapping[str, str] | None = None,
                            fracao: float = FRACAO_FORMULA,
                            min_nomes: int = MIN_NOMES) -> list[str]:
    """Greenblatt: melhor soma de postos de ROIC e de 1/EV_EBIT.

    Empate na soma desempata pelo maior rendimento operacional e depois pelo
    ticker, para a lista não depender da ordem dos dados.
    """
    quadro = _do_universo(df_pit, universo)
    if quadro.empty:
        return []
    setores = {str(k).upper(): v for k, v in (setor_de or {}).items()}
    fora = quadro["Ticker"].map(
        lambda t: _norm(setores.get(t, "")).startswith(PREFIXOS_SETOR_EXCLUIDO))
    quadro = quadro[~fora]
    roic, ev_ebit = _num(quadro, "ROIC"), _num(quadro, "EV_EBIT")
    ok = roic.notna() & ev_ebit.notna() & (ev_ebit > 0)
    elig = pd.DataFrame({"Ticker": quadro.loc[ok, "Ticker"].values,
                         "roic": roic[ok].values,
                         "ey": (1.0 / ev_ebit[ok]).values})
    if elig.empty:
        return []
    elig["pontos"] = (elig["roic"].rank(ascending=False, method="min")
                      + elig["ey"].rank(ascending=False, method="min"))
    elig = elig.sort_values(["pontos", "ey", "Ticker"], ascending=[True, False, True],
                            kind="mergesort")
    n = min(len(elig), max(int(min_nomes), math.ceil(float(fracao) * len(elig))))
    return sorted(elig["Ticker"].head(n).astype(str))


# ── 3. Qualidade constante ──────────────────────────────────────────────────
def _anos_roic(df: pd.DataFrame | None) -> dict[int, float]:
    if df is None or df.empty or "Data" not in df.columns or "ROIC" not in df.columns:
        return {}
    anos = pd.to_datetime(df["Data"], errors="coerce").dt.year
    roic = pd.to_numeric(df["ROIC"], errors="coerce")
    out: dict[int, float] = {}
    for a, r in zip(anos, roic):
        if pd.notna(a) and pd.notna(r) and np.isfinite(float(r)):
            out[int(a)] = float(r)
    return out


def persistencia_roic(hist: pd.DataFrame | None, ano_max: int,
                      selic_por_ano: Mapping[int, float], *,
                      selic_padrao: float | None = None,
                      janela: int = JANELA_ANOS) -> tuple[int, int]:
    """(anos com ROIC acima da Selic do ano, anos com ROIC) na janela até ano_max.

    Ano sem Selic medida usa ``selic_padrao``; sem as duas, o ano não conta.
    """
    roic = _anos_roic(hist)
    acima = medidos = 0
    for ano in range(int(ano_max) - int(janela) + 1, int(ano_max) + 1):
        if ano not in roic:
            continue
        selic = selic_por_ano.get(ano, selic_padrao)
        if selic is None or not np.isfinite(float(selic)):
            continue
        medidos += 1
        acima += int(roic[ano] > float(selic))
    return acima, medidos


def lucro_cresceu(serie: list[dict] | None, ano_max: int,
                  anos: int = ANOS_CRESCIMENTO) -> bool:
    """Lucro positivo em ano_max e maior que o de ``anos`` exercícios antes."""
    por_ano = {int(r["ano"]): r.get("lucro_mi") for r in (serie or [])
               if r.get("ano") is not None}
    fim, ini = por_ano.get(int(ano_max)), por_ano.get(int(ano_max) - int(anos))
    if fim is None or ini is None:
        return False
    try:
        fim, ini = float(fim), float(ini)
    except (TypeError, ValueError):
        return False
    return np.isfinite(fim) and np.isfinite(ini) and fim > 0 and fim > ini


def carteira_qualidade_constante(df_pit: pd.DataFrame | None, universo: Iterable[str],
                                 historico: Mapping[str, pd.DataFrame] | None,
                                 series_pl: Mapping[str, list[dict]] | None,
                                 ano_max: int, selic_por_ano: Mapping[int, float], *,
                                 selic_padrao: float | None = None) -> list[str]:
    """Quem passa nos três critérios da regra 3 (ver o docstring do módulo).

    ``historico`` e ``series_pl`` já recortados em ``ano_max`` pelo chamador
    (`core.b3_retrato_pit.historico_ate` / `series_pl_ate`).
    """
    nomes = sorted({str(t).upper() for t in universo})
    falhas = falhas_de_solvencia(df_pit, nomes)
    hist = {str(k).upper(): v for k, v in (historico or {}).items()}
    pl = {str(k).upper(): v for k, v in (series_pl or {}).items()}
    fica = []
    for tk in nomes:
        if falhas.get(tk):
            continue
        acima, medidos = persistencia_roic(hist.get(tk), ano_max, selic_por_ano,
                                           selic_padrao=selic_padrao)
        if medidos < MIN_ANOS or acima < FRACAO_ANOS_ACIMA * medidos:
            continue
        if not lucro_cresceu(pl.get(tk), ano_max):
            continue
        fica.append(tk)
    return fica


# ── 4. Qualidade segurada ───────────────────────────────────────────────────
def segurar(safras: list[int], formar, manter, *,
            anos: int = ANOS_SEGURANDO) -> dict[int, list[str]]:
    """Lista de cada safra quando a carteira fica parada ``anos`` safras.

    ``formar(safra)`` devolve a lista nova; ``manter(safra, lista)`` devolve
    quem continua (sai só quem quebrou a regra de saída). A lista é refeita na
    primeira safra e a cada ``anos``; carteira que zerou é refeita na safra
    seguinte, sem esperar.
    """
    ordem = sorted(int(s) for s in safras)
    out: dict[int, list[str]] = {}
    atual: list[str] = []
    formada_em: int | None = None
    for s in ordem:
        if formada_em is None or s - formada_em >= int(anos) or not atual:
            atual, formada_em = sorted(formar(s)), s
        else:
            atual = sorted(manter(s, atual))
        out[s] = list(atual)
    return out


def manter_qualidade(lista: Iterable[str], universo: Iterable[str],
                     df_pit: pd.DataFrame | None) -> list[str]:
    """Saída da regra 4: deixou o universo ou quebrou a trava de solvência."""
    no_universo = {str(t).upper() for t in universo}
    falhas = falhas_de_solvencia(df_pit, no_universo)
    return sorted(t for t in (str(x).upper() for x in lista)
                  if t in no_universo and not falhas.get(t))
