"""
core/b3_estrategias_fundamentalistas.py — carteiras "à moda dos grandes
fundamentalistas", para medir fora da amostra contra pesos iguais.

Pergunta (22/11/2026): a carteira padrão da B3 não se distingue de dividir
igualmente entre as empresas. A nota do motor já mistura qualidade, ROIC,
dívida, preço e dividendos (`core.b3_company_score.FACTOR_TRACKS`), então
somar "mais Buffett" à mesma nota tende a não mudar nada. Este módulo testa
outra coisa: REGRAS SIMPLES, que um investidor repete em casa, com a lógica
de quem as criou.

As cinco regras, fixadas ANTES de medir (não são ajustadas depois):

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
5. ``lider_segurado`` — a premissa original do usuário (22/11/2026): "nunca
   vender a líder do ano anterior, só se os fundamentos piorarem". A carteira
   da safra é a carteira padrão da safra (os líderes, Equilibrado sem portão)
   MAIS os nomes das safras anteriores que não degradaram. Degradar, fixado
   antes de medir, é uma de três coisas: deixar o universo, falhar na trava de
   solvência da rota de valor, ou ter prejuízo no último exercício (lucro de
   N-1 <= 0). Dado ausente não é degradação. Pesos iguais entre todos.

Duas regras a mais, fixadas antes de medir em 23/11/2026 (§23 do guia, com
regra de decisão própria, ``REGRA_DE_DECISAO_PEQUENAS_DIVIDENDO``):

6. ``pequenas_baratas`` — as empresas que o piso de R$ 1 bi de valor de
   mercado corta (no universo da rodada sem piso, fora do universo com piso,
   mesma liquidez mínima de R$ 500 mil) que a rota de valor classifica como
   ``oportunidade``. Pesos iguais.
7. ``dividendo_6`` — Bazin: DY médio dos três últimos exercícios (N-3 a N-1,
   todos presentes) de pelo menos 6%, sem falha da trava de solvência.
   Universo da carteira padrão. Pesos iguais.

O ``dividendo_6`` também é medido em outra janela, as safras 2013 a 2016 que
a §23 não viu (§24, regra própria fixada antes de medir em 24/11/2026:
``REGRA_DE_DECISAO_DIVIDENDO_OUTRA_JANELA`` e ``leitura_outra_janela``).

8. ``dividendo_segurado`` (§26, regra fixada antes de medir em 24/11/2026,
   ``REGRA_DE_DECISAO_DIVIDENDO_SEGURADO``) — a premissa do usuário: entra
   quem passa no ``dividendo_6``; quem já está só sai se a saúde financeira
   degradar (os mesmos motivos do ``lider_segurado``), por mais que o
   dividendo caia. Alvo de peso igual (1/N); o equilíbrio vem do aporte
   anual, que compra primeiro quem está mais abaixo do alvo
   (`simular_aportes`), sem vender quem subiu.

10. ``dividendo_6_topN`` (§28, regra fixada antes de medir em 26/11/2026,
    ``REGRA_DE_DECISAO_DIVIDENDO_TOPN``) — o ``dividendo_6`` limitado a N
    nomes (5, 10, 15 e 20): os N de maior DY médio, desempate por ticker. Com
    menos de N que passam, fica com todos. Pesos iguais.

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

VERSAO = "fundamentalistas-1.8.0"

ROTA_VALOR = "rota_valor"
FORMULA_MAGICA = "formula_magica"
QUALIDADE_CONSTANTE = "qualidade_constante"
QUALIDADE_3_ANOS = "qualidade_3_anos"
LIDER_SEGURADO = "lider_segurado"
ESTRATEGIAS = (ROTA_VALOR, FORMULA_MAGICA, QUALIDADE_CONSTANTE, QUALIDADE_3_ANOS,
               LIDER_SEGURADO)

#: Motivos de saída do líder segurado (regra 5).
SAIU_DO_UNIVERSO = "saiu_do_universo"
FALHA_SOLVENCIA = "falha_solvencia"
PREJUIZO = "prejuizo_no_ultimo_exercicio"

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

#: Regra de decisão, registrada antes de medir. Cinco braços elevam a chance
#: de um falso positivo de 1 em 20 para perto de 1 em 4; por isso não basta o
#: IC excluir o zero contra pesos iguais.
REGRA_DE_DECISAO = (
    "uma estratégia só vira candidata a carteira padrão se, LÍQUIDA do giro: "
    "(a) o excesso médio sobre pesos iguais for > 0 com IC 95% acima de zero; "
    "(b) a média sem a melhor safra continuar > 0; e (c) a diferença pareada "
    "contra a carteira padrão atual (Equilibrado, sem portão) tiver média > 0. "
    "Com cinco braços, passar só em (a) não basta. Nenhuma passando, nada muda "
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


#: Tamanhos da Rota de valor enxuta (medidos em 22/11/2026, fixados antes de
#: ver qualquer número). A rota completa tem ~39 nomes, inviável com pouco capital.
TAMANHOS_ROTA_ENXUTA = (10, 15, 20)


def rotulo_rota_topn(n: int) -> str:
    return f"rota_valor_top{int(n)}"


def carteira_rota_valor_topn(df_pit: pd.DataFrame | None, universo: Iterable[str],
                             n: int, *, selic: float | None = None) -> list[str]:
    """Os ``n`` primeiros da rota de valor, na ordem da PRÓPRIA rota.

    Mesmo filtro da rota completa (``oportunidade``); a ordem é a do
    ``valor_score`` da rota (60% desconto, 40% folga de solvência), desempate
    por margem de valor e depois ticker. Nenhum critério novo: a regra não
    tem nada a ajustar. Com menos de ``n`` oportunidades, fica com todas.
    """
    if int(n) <= 0:
        return []
    quadro = _do_universo(df_pit, universo)
    if quadro.empty:
        return []
    ranked = rank_value_opportunities(quadro, selic=selic)
    op = ranked[ranked["classificacao"] == OPORTUNIDADE].copy()
    if op.empty:
        return []
    op["Ticker"] = op["Ticker"].astype(str)
    op = op.sort_values(["valor_score", "margem_valor", "Ticker"],
                        ascending=[False, False, True], kind="mergesort")
    return sorted(op["Ticker"].head(int(n)))


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


# ── 5. Líder segurado ───────────────────────────────────────────────────────
def motivos_de_saida(lista: Iterable[str], universo: Iterable[str],
                     df_pit: pd.DataFrame | None,
                     series_pl: Mapping[str, list[dict]] | None,
                     ano_max: int) -> dict[str, list[str]]:
    """Por que cada nome segurado sai na safra (lista vazia: fica).

    ``series_pl`` já recortada em ``ano_max`` pelo chamador. Dado ausente não
    é degradação: sem retrato, não há falha de solvência; sem lucro de
    ``ano_max``, não há prejuízo.
    """
    no_universo = {str(t).upper() for t in universo}
    nomes = sorted({str(t).upper() for t in lista})
    falhas = falhas_de_solvencia(df_pit, no_universo)
    pl = {str(k).upper(): v for k, v in (series_pl or {}).items()}
    out: dict[str, list[str]] = {}
    for tk in nomes:
        motivos = []
        if tk not in no_universo:
            out[tk] = [SAIU_DO_UNIVERSO]
            continue
        if falhas.get(tk):
            motivos.append(FALHA_SOLVENCIA)
        lucro = {int(r["ano"]): r.get("lucro_mi") for r in (pl.get(tk) or [])
                 if r.get("ano") is not None}.get(int(ano_max))
        try:
            lucro = float(lucro) if lucro is not None else None
        except (TypeError, ValueError):
            lucro = None
        if lucro is not None and np.isfinite(lucro) and lucro <= 0:
            motivos.append(PREJUIZO)
        out[tk] = motivos
    return out


def manter_lideres(lista: Iterable[str], universo: Iterable[str],
                   df_pit: pd.DataFrame | None,
                   series_pl: Mapping[str, list[dict]] | None,
                   ano_max: int) -> list[str]:
    """Saída da regra 5: fica quem não tem nenhum motivo de saída."""
    return sorted(t for t, m in motivos_de_saida(lista, universo, df_pit, series_pl,
                                                 ano_max).items() if not m)


def segurar_lideres(safras: list[int], lideres_de, manter, *,
                    barrar_reentrada: bool = False) -> dict[int, list[str]]:
    """Lista de cada safra quando o líder só sai se degradar.

    ``lideres_de(safra)`` devolve os líderes da safra (a carteira padrão);
    ``manter(safra, lista)`` devolve quem da carteira anterior continua. A
    carteira da safra é a união dos dois: o líder novo entra, o antigo fica
    enquanto não degradar.

    ``barrar_reentrada`` (§26 e §27): quem a saída tirou na safra não volta
    pela entrada da mesma safra. Sem isso, um nome com prejuízo que ainda
    passa na regra de entrada sairia e voltaria no mesmo abril. A §19 fica
    sem a trava: lá a entrada é a própria carteira padrão.
    """
    out: dict[int, list[str]] = {}
    atual: list[str] = []
    for s in sorted(int(x) for x in safras):
        ficam = set(manter(s, atual)) if atual else set()
        novos = {str(t).upper() for t in lideres_de(s)}
        if barrar_reentrada:
            novos -= {str(t).upper() for t in atual} - ficam
        atual = sorted(ficam | novos)
        out[s] = list(atual)
    return out


# ── 6 e 7. Pequenas baratas e dividendo de 6% (23/11/2026) ──────────────────
#: Rótulos dos braços da §23 do guia. ``rota_valor_sem_piso`` é informativo:
#: fica fora da regra de decisão.
PEQUENAS_BARATAS = "pequenas_baratas"
DIVIDENDO_6 = "dividendo_6"
ROTA_VALOR_SEM_PISO = "rota_valor_sem_piso"

#: Bazin: dividendo médio de ``ANOS_DY_BAZIN`` exercícios sobre o preço, no
#: mínimo ``DY_ALVO_BAZIN``. DY fora de [0, ``DY_MAXIMO_CRIVEL``] (negativo ou
#: acima de 100% ao ano) é erro de dado e conta como ano ausente.
DY_ALVO_BAZIN = 0.06
ANOS_DY_BAZIN = 3
DY_MAXIMO_CRIVEL = 1.0

#: Regra de decisão da §23, registrada antes de medir (23/11/2026).
REGRA_DE_DECISAO_PEQUENAS_DIVIDENDO = (
    "pequenas baratas e dividendo de 6% só viram candidatas se, LÍQUIDAS do "
    "giro, passarem nos três testes da §19: (a) excesso médio sobre pesos "
    "iguais no próprio universo > 0 com IC 95% acima de zero; (b) média sem a "
    "melhor safra > 0; (c) diferença pareada contra a carteira padrão atual "
    "(Equilibrado, sem portão) com média > 0. Dois braços: a chance de um falso "
    "positivo fica perto de 1 em 10. A rota de valor sem piso de tamanho e o "
    "excesso das pequenas sobre os pesos iguais do universo com piso são só "
    "informativos. Passar não muda a tela; só abre a próxima etapa.")


# ── 7b. O dividendo de 6% em outra janela (24/11/2026, §24) ─────────────────
#: As safras que a §23 não viu: abril/2013 a março/2017. A §23 começou em 2017
#: porque a aba carrega só 10 anos de preço; o armazém tem preço desde 2000.
PRIMEIRA_SAFRA_OUTRA_JANELA = 2013
ULTIMA_SAFRA_OUTRA_JANELA = 2016

#: Leituras possíveis da §24, fixadas antes de medir.
CONFIRMA = "confirma"
NAO_CONTRADIZ = "nao_contradiz"
CONTRADIZ = "contradiz"
INCONCLUSIVA = "inconclusiva"

#: Regra de decisão da §24, registrada antes de medir (24/11/2026).
REGRA_DE_DECISAO_DIVIDENDO_OUTRA_JANELA = (
    "o dividendo de 6% (mesma regra da §23, sem mudar nada) é medido nas safras "
    "2013 a 2016, que a §23 não viu. Métrica: excesso por safra, LÍQUIDO do "
    "giro, sobre pesos iguais no universo da carteira padrão. Leituras: "
    "'confirma' se (a) a média > 0 com IC 95% acima de zero e (b) a média sem "
    "a melhor safra > 0; 'não contradiz' se a média > 0 e (b) > 0, mas o IC "
    "cruza o zero; 'contradiz' se a média <= 0 ou (b) <= 0; 'inconclusiva' "
    "com menos de três safras medidas. O teste (c) da §23 não se aplica: a "
    "carteira padrão não tem carteira antes de 2017. A janela inteira "
    "2013-2025, o excesso com IR e a conferência de 2017-2025 contra a §23 "
    "são só informativos. Passar não muda a tela.")


def leitura_outra_janela(resumo: Mapping | None) -> dict:
    """Os testes e a leitura da §24 sobre o resumo de `oos.resumir`.

    Com menos de três safras não há "média sem a melhor safra" e a leitura é
    ``inconclusiva`` -- nunca "contradiz" por falta de amostra.
    """
    r = dict(resumo or {})
    media = r.get("media")
    lo = (r.get("ic95") or [None, None])[0]
    sem_melhor = r.get("media_sem_melhor_safra")
    n = int(r.get("n_safras") or 0)
    testes = {
        "a_excesso_ew_ic_acima_de_zero": bool(
            media is not None and media > 0 and lo is not None and lo > 0),
        "b_sem_melhor_safra_positiva": bool(sem_melhor is not None and sem_melhor > 0),
    }
    if n < 3 or media is None or sem_melhor is None:
        leitura = INCONCLUSIVA
    elif media <= 0 or sem_melhor <= 0:
        leitura = CONTRADIZ
    elif testes["a_excesso_ew_ic_acima_de_zero"]:
        leitura = CONFIRMA
    else:
        leitura = NAO_CONTRADIZ
    return {"testes": testes, "leitura": leitura, "n_safras": n}


def pequenas(universo_sem_piso: Iterable[str],
             universo_com_piso: Iterable[str]) -> list[str]:
    """Quem o piso de tamanho corta: está no universo sem piso e não no com piso.

    Os dois universos são da MESMA safra e com o MESMO filtro de liquidez.
    Entra quem não tem valor de mercado medido (o piso também o corta).
    """
    com_piso = {str(t).upper() for t in universo_com_piso}
    return sorted({str(t).upper() for t in universo_sem_piso} - com_piso)


def carteira_pequenas_baratas(df_pit: pd.DataFrame | None,
                              universo_sem_piso: Iterable[str],
                              universo_com_piso: Iterable[str], *,
                              selic: float | None = None) -> list[str]:
    """Regra 6: a rota de valor aplicada só às pequenas (`pequenas`).

    ``df_pit`` é o retrato da safra na rodada SEM piso de tamanho (o retrato
    com piso não tem as pequenas). Mesmo critério da rota de valor: margem de
    Graham e Bazin e trava de solvência, classificação ``oportunidade``.
    """
    return carteira_rota_valor(df_pit, pequenas(universo_sem_piso, universo_com_piso),
                               selic=selic)


def _dy_por_ano(hist: pd.DataFrame | None) -> dict[int, float]:
    """DY de cada exercício; com duas linhas no ano, vale a de data mais nova."""
    if hist is None or hist.empty or "Data" not in hist.columns or "DY" not in hist.columns:
        return {}
    d = pd.DataFrame({"data": pd.to_datetime(hist["Data"], errors="coerce"),
                      "dy": pd.to_numeric(hist["DY"], errors="coerce")})
    d = d.dropna(subset=["data"]).sort_values("data", kind="mergesort")
    out: dict[int, float] = {}
    for data, dy in zip(d["data"], d["dy"]):
        out[int(data.year)] = dy
    return {a: float(v) for a, v in out.items()
            if v is not None and np.isfinite(float(v))
            and 0.0 <= float(v) <= DY_MAXIMO_CRIVEL}


def dy_medio(hist: pd.DataFrame | None, ano_max: int, *,
             anos: int = ANOS_DY_BAZIN) -> float | None:
    """Média do DY dos ``anos`` exercícios até ``ano_max``; None se faltar um.

    ``hist`` já recortado em ``ano_max`` pelo chamador
    (`core.b3_retrato_pit.historico_ate`, regra de vintage da safra).
    """
    por_ano = _dy_por_ano(hist)
    janela = range(int(ano_max) - int(anos) + 1, int(ano_max) + 1)
    if any(a not in por_ano for a in janela):
        return None
    return float(np.mean([por_ano[a] for a in janela]))


def dy_de_quem_passa(df_pit: pd.DataFrame | None, universo: Iterable[str],
                     historico: Mapping[str, pd.DataFrame] | None,
                     ano_max: int, *, alvo: float = DY_ALVO_BAZIN,
                     anos: int = ANOS_DY_BAZIN) -> dict[str, float]:
    """Quem passa no filtro da regra 7 e o DY médio de cada um.

    Ponto único do filtro do ``dividendo_6``: `carteira_dividendo_bazin` (a
    carteira inteira) e `carteira_dividendo_topn` (§28) leem daqui.
    """
    nomes = sorted({str(t).upper() for t in universo})
    falhas = falhas_de_solvencia(df_pit, nomes)
    hist = {str(k).upper(): v for k, v in (historico or {}).items()}
    fica: dict[str, float] = {}
    for tk in nomes:
        if falhas.get(tk):
            continue
        media = dy_medio(hist.get(tk), ano_max, anos=anos)
        if media is not None and media >= float(alvo):
            fica[tk] = media
    return fica


def carteira_dividendo_bazin(df_pit: pd.DataFrame | None, universo: Iterable[str],
                             historico: Mapping[str, pd.DataFrame] | None,
                             ano_max: int, *, alvo: float = DY_ALVO_BAZIN,
                             anos: int = ANOS_DY_BAZIN) -> list[str]:
    """Regra 7 (Bazin): DY médio dos últimos ``anos`` exercícios >= ``alvo``.

    Exige os ``anos`` exercícios com DY crível e nenhuma falha da trava de
    solvência da rota de valor (ausência de dado não é falha). Pesos iguais
    entre quem passa.
    """
    return sorted(dy_de_quem_passa(df_pit, universo, historico, ano_max,
                                   alvo=alvo, anos=anos))


# ── 7d. O dividendo de 6% com poucos nomes (26/11/2026, §28) ────────────────
#: Tamanhos da §28, fixados antes de medir (pedido do usuário de 26/11/2026).
TAMANHOS_DIVIDENDO_TOPN = (5, 10, 15, 20)

#: Regra de decisão da §28, registrada antes de medir (26/11/2026).
REGRA_DE_DECISAO_DIVIDENDO_TOPN = (
    "pergunta: o dividendo de 6% da §23, limitado a poucos nomes, se sai "
    "melhor? A entrada é a mesma da §23 (DY médio de N-3 a N-1 >= 6%, os três "
    "presentes, sem falha da trava de solvência, universo da carteira padrão). "
    "Dos que passam, ficam os N de MAIOR DY médio, desempate por ticker; com "
    "menos de N, todos. N = 5, 10, 15 e 20, fixados antes de medir. Pesos "
    "iguais, refeita todo abril. Um braço vira candidato só se, LÍQUIDO do "
    "giro, passar nos três testes da §19: (a) excesso médio sobre pesos iguais "
    "no universo > 0 com IC 95% acima de zero; (b) média sem a melhor safra > "
    "0; (c) diferença pareada contra a carteira padrão atual (Equilibrado, sem "
    "portão) com média > 0. Com mais de um passando, vale o MENOR N. Quatro "
    "braços: a chance de um falso positivo sobe para perto de 1 em 5. Só "
    "informativos: o dividendo de 6% inteiro refeito na mesma rodada, a "
    "diferença pareada contra ele, o número de nomes por safra e a comparação "
    "com Ibovespa, CDI, IFIX, S&P 500 e inflação. Passar não muda a tela; só "
    "abre a próxima etapa.")


def rotulo_dividendo_topn(n: int) -> str:
    return f"{DIVIDENDO_6}_top{int(n)}"


def carteira_dividendo_topn(df_pit: pd.DataFrame | None, universo: Iterable[str],
                            historico: Mapping[str, pd.DataFrame] | None,
                            ano_max: int, n: int, *, alvo: float = DY_ALVO_BAZIN,
                            anos: int = ANOS_DY_BAZIN) -> list[str]:
    """Os ``n`` de maior DY médio entre quem passa no ``dividendo_6`` (§28).

    Mesmo filtro da regra 7 (`dy_de_quem_passa`). Ordem: DY médio
    decrescente, desempate por ticker. Com menos de ``n``, fica com todos.
    """
    if int(n) <= 0:
        return []
    dy = dy_de_quem_passa(df_pit, universo, historico, ano_max, alvo=alvo, anos=anos)
    ordem = sorted(dy, key=lambda tk: (-dy[tk], tk))
    return sorted(ordem[:int(n)])


# ── 7c. O dividendo de 6% sobre a rota de valor (24/11/2026, §25) ───────────
#: Braços da §25. A união e o "fora da rota" saem das duas carteiras já
#: medidas (§19 e §23), sem critério novo.
ROTA_MAIS_DIVIDENDO = "rota_mais_dividendo"
DIVIDENDO_FORA_DA_ROTA = "dividendo_fora_da_rota"

#: Leituras possíveis da §25, fixadas antes de medir.
ACRESCENTA = "acrescenta"
NAO_SE_DISTINGUE = "nao_se_distingue"
TIRA = "tira"

#: Regra de decisão da §25, registrada antes de medir (24/11/2026).
REGRA_DE_DECISAO_SOBREPOSICAO_ROTA = (
    "pergunta: o dividendo de 6% acrescenta retorno à rota de valor? As duas "
    "regras ficam como estão (§19 e §23). Métrica: diferença pareada, safra a "
    "safra, LÍQUIDA do giro, entre 'rota + dividendo' (a união dos nomes das "
    "duas carteiras, pesos iguais) e a rota de valor sozinha, nas safras em "
    "que as duas mediram (2017 a 2025). Leituras: 'acrescenta' se (a) a média "
    "> 0 com IC 95% acima de zero e (b) a média sem a melhor safra > 0; 'tira' "
    "se a média < 0 com IC 95% abaixo de zero; 'não se distingue' nos outros "
    "casos; 'inconclusiva' com menos de três safras. Só informativos: a "
    "sobreposição por safra (quantos nomes do dividendo a rota já tem), o "
    "'dividendo fora da rota' contra pesos iguais e contra a rota, o meio a "
    "meio (média dos retornos líquidos das duas carteiras) contra a rota e a "
    "diferença com IR. Passar não muda a tela.")


def sobreposicao(rota: Iterable[str], dividendo: Iterable[str]) -> dict:
    """Quantos nomes as duas carteiras da mesma safra têm em comum.

    ``fracao_do_dividendo_na_rota`` é a parte do dividendo que a rota já
    compra; ``jaccard`` é comum / união. Carteira vazia dá fração ``None``.
    """
    r = {str(t).upper() for t in rota}
    d = {str(t).upper() for t in dividendo}
    comum, uniao_ = r & d, r | d
    return {
        "n_rota": len(r), "n_dividendo": len(d), "n_comum": len(comum),
        "n_uniao": len(uniao_),
        "fracao_do_dividendo_na_rota": (len(comum) / len(d)) if d else None,
        "fracao_da_rota_no_dividendo": (len(comum) / len(r)) if r else None,
        "jaccard": (len(comum) / len(uniao_)) if uniao_ else None,
        "comuns": sorted(comum),
    }


def carteira_uniao(*carteiras: Iterable[str]) -> list[str]:
    """Os nomes de todas as carteiras, uma vez cada (pesos iguais depois)."""
    return sorted({str(t).upper() for c in carteiras for t in c})


def carteira_fora_de(carteira: Iterable[str], excluir: Iterable[str]) -> list[str]:
    """Os nomes de ``carteira`` que ``excluir`` não tem."""
    fora = {str(t).upper() for t in excluir}
    return sorted({str(t).upper() for t in carteira} - fora)


def leitura_sobreposicao(resumo: Mapping | None) -> dict:
    """Os testes e a leitura da §25 sobre o resumo pareado (`oos.resumir`).

    ``resumo`` é o da diferença "rota + dividendo" menos rota, líquida.
    """
    r = dict(resumo or {})
    media = r.get("media")
    lo, hi = (list(r.get("ic95") or [None, None]) + [None, None])[:2]
    sem_melhor = r.get("media_sem_melhor_safra")
    n = int(r.get("n_safras") or 0)
    testes = {
        "a_media_ic_acima_de_zero": bool(
            media is not None and media > 0 and lo is not None and lo > 0),
        "b_sem_melhor_safra_positiva": bool(sem_melhor is not None and sem_melhor > 0),
        "media_ic_abaixo_de_zero": bool(
            media is not None and media < 0 and hi is not None and hi < 0),
    }
    if n < 3 or media is None:
        leitura = INCONCLUSIVA
    elif testes["a_media_ic_acima_de_zero"] and testes["b_sem_melhor_safra_positiva"]:
        leitura = ACRESCENTA
    elif testes["media_ic_abaixo_de_zero"]:
        leitura = TIRA
    else:
        leitura = NAO_SE_DISTINGUE
    return {"testes": testes, "leitura": leitura, "n_safras": n}


# ── 8. Dividendo segurado (24/11/2026, §26) ─────────────────────────────────
#: Braço da §26 e a variante informativa que não vende por prejuízo.
DIVIDENDO_SEGURADO = "dividendo_segurado"
DIVIDENDO_SEGURADO_SO_SOLVENCIA = "dividendo_segurado_so_solvencia"

#: Motivos de saída de cada braço da §26. O principal usa os três do líder
#: segurado (regra 5); a variante ignora o prejuízo de um exercício só.
MOTIVOS_DIVIDENDO_SEGURADO = (SAIU_DO_UNIVERSO, FALHA_SOLVENCIA, PREJUIZO)
MOTIVOS_SO_SOLVENCIA = (SAIU_DO_UNIVERSO, FALHA_SOLVENCIA)

#: Aporte anual da §26, em fração do patrimônio de abril. O principal é
#: ``APORTE_DIVIDENDO_SEGURADO``; os outros são sensibilidade.
APORTE_DIVIDENDO_SEGURADO = 0.10
APORTES_SENSIBILIDADE = (0.05, 0.20)

#: Regra de decisão da §26, registrada antes de medir (24/11/2026).
REGRA_DE_DECISAO_DIVIDENDO_SEGURADO = (
    "premissa do usuário (24/11/2026): comprar boas empresas boas pagadoras "
    "de dividendo e só vender quando a saúde financeira as tornar perigosas, "
    "por mais que o dividendo caia; o equilíbrio entre as empresas vem do "
    "aporte, não da venda das que subiram. Entrada, a cada abril: a regra do "
    "dividendo de 6% da §23, sem mudança (DY médio de N-3 a N-1 >= 6%, os três "
    "anos presentes, sem falha da trava de solvência). Saída: a do líder "
    "segurado da §19, sem mudança -- deixar o universo, falhar na trava de "
    "solvência da rota de valor ou ter prejuízo no último exercício (lucro de "
    "N-1 <= 0); dado ausente não é degradação; DY abaixo de 6% não tira "
    "ninguém. Pesos: o alvo é igual (1/N) entre todos os nomes da carteira. "
    "Na primeira safra o capital entra em 1/N. Depois, em cada abril, ninguém "
    "é vendido para voltar ao alvo: o aporte (10% do patrimônio de abril) "
    "mais o que sobrar das saídas compra as empresas mais abaixo do alvo, de "
    "baixo para cima, até o dinheiro acabar; quem está acima do alvo fica como "
    "está. Retorno da safra = valor em março/N+1 sobre (patrimônio de abril + "
    "aporte), líquido de custo de compra e de venda. Candidata só se, LÍQUIDA, "
    "passar nos três testes da §19: (a) excesso médio sobre pesos iguais no "
    "universo > 0 com IC 95% acima de zero; (b) média sem a melhor safra > 0; "
    "(c) diferença pareada contra a carteira padrão atual (Equilibrado, sem "
    "portão) com média > 0. Um braço sob a regra. Só informativos: aporte de "
    "5% e de 20%; a mesma carteira com a régua da §19 (volta a 1/N vendendo o "
    "excesso); a variante que só vende por solvência ou saída do universo; a "
    "diferença pareada contra o dividendo de 6% sem segurar; o giro de venda, "
    "os motivos de saída e o peso maior e menor por safra; o excesso com IR. "
    "Passar não muda a tela; só abre a próxima etapa.")


def quem_fica(motivos: Mapping[str, Iterable[str]],
              considerar: Iterable[str] = MOTIVOS_DIVIDENDO_SEGURADO) -> list[str]:
    """Os nomes sem nenhum motivo de saída entre os de ``considerar``.

    ``motivos`` é o de `motivos_de_saida`. A §26 usa os três motivos; a
    variante informativa, só ``MOTIVOS_SO_SOLVENCIA``.
    """
    conta = set(considerar)
    return sorted(str(t).upper() for t, m in motivos.items() if not conta & set(m or ()))


def encher_por_baixo(posicoes: Mapping[str, float], nomes: Iterable[str],
                     dinheiro: float) -> dict[str, float]:
    """Quanto comprar de cada nome para levantar os menores até um nível só.

    É o rebalanceamento só com aporte: o dinheiro vai primeiro ao nome mais
    abaixo, depois aos dois mais abaixo, e assim por diante; ninguém recebe
    se já está acima do nível final. Nome novo começa em zero. A soma das
    compras é ``dinheiro`` (ou nada, sem nome ou sem dinheiro).
    """
    alvo = sorted({str(t).upper() for t in nomes})
    if not alvo or dinheiro <= 0:
        return {}
    atual = {t: max(float(posicoes.get(t, 0.0) or 0.0), 0.0) for t in alvo}
    ordem = sorted(alvo, key=lambda t: (atual[t], t))
    soma = 0.0
    nivel = 0.0
    for k, t in enumerate(ordem, start=1):
        soma += atual[t]
        nivel = (soma + dinheiro) / k
        proximo = atual[ordem[k]] if k < len(ordem) else math.inf
        if nivel <= proximo:
            break
    return {t: nivel - atual[t] for t in ordem if nivel - atual[t] > 1e-12}


def simular_aportes(safras: list[dict], cfg, *,
                    aporte: float = APORTE_DIVIDENDO_SEGURADO,
                    capital: float = 100_000.0) -> list[dict]:
    """A cadeia da §26: alvo 1/N, equilíbrio pelo aporte, venda só na saída.

    Mesmo formato de entrada e de saída de `core.b3_oos_carteira.simular_custos`
    (os itens de `itens_da_cadeia`), para caber na régua `medir_escolha`. Dos
    ``pesos`` de cada safra só conta QUEM está; a fração vem das posições.

    Em cada abril: vende o que saiu da lista (corretagem + spread; IR na cadeia
    com IR, pelas regras de `simular_custos`), soma o aporte (``aporte`` x
    patrimônio de abril; nenhum na primeira safra) e compra por
    `encher_por_baixo`. Safra sem lista com ``rendimento_caixa``: vende tudo e
    rende a taxa; sem a chave, fica fora (``medida: False``).

    ``liquido`` = valor em março / (patrimônio de abril + aporte) - 1 (retorno
    ponderado pelo tempo: o aporte não conta como ganho); ``bruto`` = soma das
    frações depois da compra vezes o retorno; ``giro`` = compras / (patrimônio
    + aporte); ``giro_venda`` = vendas / patrimônio; ``peso_maior`` e
    ``peso_menor`` = maior e menor fração depois da compra.
    """
    from core.transaction_costs import custo_compra, custo_venda

    caixa_tk = "__caixa__"
    estado = {False: {"pos": {}, "base": {}, "valor": float(capital)},
              True: {"pos": {}, "base": {}, "valor": float(capital)}}
    prejuizo = 0.0
    primeira = True
    saida: list[dict] = []

    def _passo(e, nomes, rets, com_ir, em_caixa):
        nonlocal prejuizo
        pos, base = dict(e["pos"]), dict(e["base"])
        patrimonio = sum(pos.values()) if pos else e["valor"]
        novo = 0.0 if primeira else aporte * patrimonio
        disponivel = patrimonio + novo
        custo = vendas = realizado = 0.0
        livre = 0.0 if pos else patrimonio
        for tk in sorted(pos):
            if tk in nomes and not em_caixa:
                continue
            v = pos.pop(tk)
            b = base.pop(tk, v)
            if tk == caixa_tk:
                livre += v  # resgate do caixa: sem bolsa e sem IR de ações
                continue
            fee, _ = custo_venda(tk, v, 0.0, 0.0, cfg)
            custo += fee
            vendas += v
            realizado += v - b
            livre += v - fee
        ir = 0.0
        if com_ir:
            liquido_ir = realizado + prejuizo
            if vendas <= cfg.isencao_mes:
                if realizado < 0:
                    prejuizo += realizado
            elif liquido_ir > 0:
                ir = cfg.ir_rate * liquido_ir
                prejuizo = 0.0
            else:
                prejuizo = liquido_ir
            livre -= ir
        livre += novo
        if em_caixa:
            compras: dict[str, float] = {}
            pos, base = {caixa_tk: livre}, {caixa_tk: livre}
        else:
            compras = encher_por_baixo(pos, nomes, livre)
        for tk, valor in compras.items():
            fee = custo_compra(tk, valor, cfg)
            custo += fee
            pos[tk] = pos.get(tk, 0.0) + valor - fee
            base[tk] = base.get(tk, 0.0) + valor
        total = sum(pos.values())
        fracoes = {tk: v / total for tk, v in pos.items()} if total > 0 else {}
        bruto = sum(w * rets.get(tk, 0.0) for tk, w in fracoes.items())
        fim = {tk: v * (1.0 + rets.get(tk, 0.0)) for tk, v in pos.items()}
        e.update(pos=fim, base=base, valor=sum(fim.values()))
        return {
            "liquido": e["valor"] / disponivel - 1.0 if disponivel > 0 else 0.0,
            "bruto": bruto,
            "custo_pct": custo / disponivel if disponivel > 0 else 0.0,
            "ir_pct": ir / disponivel if disponivel > 0 else 0.0,
            "giro": sum(compras.values()) / disponivel if disponivel > 0 else 0.0,
            "giro_venda": vendas / patrimonio if patrimonio > 0 else 0.0,
            "fracoes": fracoes,
        }

    for s in safras:
        nomes = {str(t).upper() for t in (s.get("pesos") or {})}
        caixa = s.get("rendimento_caixa")
        if not nomes and caixa is None:
            for e in estado.values():
                e["valor"] = sum(e["pos"].values()) if e["pos"] else e["valor"]
                e["pos"], e["base"] = {}, {}
            saida.append({"safra": s["safra"], "medida": False})
            continue
        em_caixa = not nomes
        if em_caixa:
            rets = {caixa_tk: float(caixa)}
        else:
            rets = {str(tk).upper(): float(v or 0.0)
                    for tk, v in (s.get("retornos") or {}).items()}
        sem = _passo(estado[False], nomes, rets, False, em_caixa)
        com = _passo(estado[True], nomes, rets, True, em_caixa)
        primeira = False
        fr = [w for tk, w in sem["fracoes"].items() if tk != caixa_tk]
        item = {"safra": s["safra"], "medida": True,
                "bruto": sem["bruto"], "liquido": sem["liquido"],
                "liquido_com_ir": com["liquido"], "custo_pct": sem["custo_pct"],
                "ir_pct": com["ir_pct"], "giro": sem["giro"],
                "giro_venda": sem["giro_venda"],
                "peso_maior": max(fr) if fr else None,
                "peso_menor": min(fr) if fr else None}
        if em_caixa:
            item["caixa"] = True
        saida.append(item)
    return saida


# ── 9. Solvência em dois anos seguidos (24/11/2026, §27) ────────────────────
#: Braço da §27 e a variante informativa que também pede prejuízo em dois
#: exercícios seguidos.
DIVIDENDO_SEGURADO_DOIS_ANOS = "dividendo_segurado_dois_anos"
DIVIDENDO_SEGURADO_DOIS_ANOS_COM_PREJUIZO = "dividendo_segurado_dois_anos_com_prejuizo"

#: Motivos que só tiram o nome quando se repetem na safra anterior.
PERSISTENTES_DOIS_ANOS = (FALHA_SOLVENCIA,)
PERSISTENTES_COM_PREJUIZO = (FALHA_SOLVENCIA, PREJUIZO)

#: Regra de decisão da §27, registrada antes de medir (24/11/2026).
REGRA_DE_DECISAO_SOLVENCIA_DOIS_ANOS = (
    "hipótese da §26 (24/11/2026): a trava de solvência responde por 40 das 52 "
    "saídas do dividendo segurado, e 13 saídas voltaram depois; exigir a falha "
    "em dois anos seguidos cortaria giro sem perder retorno. Tudo igual à §26 "
    "(entrada do dividendo de 6% da §23; alvo 1/N; equilíbrio só pelo aporte "
    "de 10% do patrimônio de abril; ninguém vendido por ter subido), menos uma "
    "coisa: a falha da trava de solvência só tira o nome se ele também falhou "
    "na trava na safra anterior (retrato de abril de N-1, mesmo universo de "
    "N-1). Sem retrato na safra anterior, ou fora do universo dela, não há "
    "falha anterior: dado ausente não é degradação. Sair do universo e ter "
    "prejuízo no último exercício seguem tirando na hora. Candidata só se, "
    "LÍQUIDA, passar nos três testes da §19: (a) excesso médio sobre pesos "
    "iguais no universo > 0 com IC 95% acima de zero; (b) média sem a melhor "
    "safra > 0; (c) diferença pareada contra a carteira padrão atual "
    "(Equilibrado, sem portão) com média > 0. A hipótese do giro se confirma "
    "se o giro de venda médio anual ficar abaixo do da §26 E a diferença "
    "pareada contra a §26 não tiver IC 95% inteiro abaixo de zero. Um braço "
    "sob a regra. Só informativos: a variante que também pede prejuízo em dois "
    "exercícios seguidos; a §26 refeita na mesma rodada; a diferença pareada "
    "contra o dividendo de 6% sem segurar; as voltas (nome que sai e volta em "
    "safra posterior), os motivos de saída e o excesso com IR. Passar não muda "
    "a tela; só abre a próxima etapa.")


def motivos_persistentes(agora: Mapping[str, Iterable[str]],
                         antes: Mapping[str, Iterable[str]],
                         persistentes: Iterable[str] = PERSISTENTES_DOIS_ANOS
                         ) -> dict[str, list[str]]:
    """Motivos de saída da §27: os de ``persistentes`` só contam se repetidos.

    ``agora`` e ``antes`` são `motivos_de_saida` da safra e da anterior. Um
    motivo de ``persistentes`` fica só se o nome também o tinha em ``antes``;
    os outros motivos passam como estão. Nome ausente de ``antes`` não tem
    motivo anterior (dado ausente não é degradação).
    """
    conta = set(persistentes)
    out: dict[str, list[str]] = {}
    for tk, ms in agora.items():
        prev = set((antes or {}).get(tk) or ())
        out[str(tk).upper()] = [m for m in (ms or ()) if m not in conta or m in prev]
    return out
