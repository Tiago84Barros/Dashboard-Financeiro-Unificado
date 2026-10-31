"""
core/b3_precos_saneamento.py -- saltos implausiveis no preco mensal da B3.

Modulo puro (sem streamlit, sem banco). Coberto por
tests/test_b3_precos_saneamento.py.

Por que existe (auditoria app4 04/10/2026, B3-01)
-------------------------------------------------
``market.historical_prices`` traz, para o retorno mensal, 401 saltos acima de
+100% e 235 abaixo de -60% em 1.091 tickers. Em 736 dos 771 saltos extremos o
``close`` BRUTO salta junto com o ajustado (desdobramento/grupamento que o
ajuste da fonte nao retroagiu); em 35 so o ajustado explode e o ``close``
fica plano (MMAQ4, RSUL3: retroajuste corrompido). Nos dois casos o "retorno"
e unidade de medida, nao dinheiro do acionista. So ~63 revertem no mes
seguinte, entao "salto que volta" pegaria menos de 10% do problema.

Evidencia de morte e de vida nao sao simetricas
-----------------------------------------------
A primeira versao (PR #491) zerava TODO retorno fora de [-60%, +100%]. Isso
apaga crash real: AMER3 em 01/2023 (-81,9%, close bruto cai junto com o
ajustado, igual a um grupamento) e em 08/2024 (-89,5%). Cortar a perda real
infla a safra -- o contrario do que a correcao quer. Agora o salto candidato
(> +100% ou < -60%) so e neutralizado COM evidencia de evento societario:

 (b) razao de preco t/t-1 EXATAMENTE um fator redondo: k ou 1/k com k inteiro
     de 2 a 100, ou fracao comum (3/2, 5/2, 4/3, 5/3, 5/4 e inversas), com
     tolerancia ``TOL_FATOR`` de 0,1% em log. Medido nos 661 saltos
     candidatos: a distribuicao da distancia ao fator mais proximo tem um
     pico em < 0,05% (72 casos: 44 antes de 2010, 28 depois; 1:3 = -66,6667%,
     1:10 = -90,0000%) e depois uma nuvem dispersa que e movimento de mercado
     (0,2%-5% de distancia: sem pico). AMER3 fica a 8,4% e
     4,8% do fator mais proximo: nao e neutralizada.
 (c) o ``close`` bruto do mesmo mes fica estavel (|retorno| <= 35%) enquanto o
     ajustado salta: retroajuste corrompido, nenhuma queda real o produz.
 (a) evento conhecido na data (marcador "ex-" da especificacao do COTAHIST,
     ``scripts/construir_memoria_mercado``) NAO entra: so existe no armazem
     local, que o app publicado nao alcanca, e cobre 16 dos 661 candidatos
     (desdobramento nao deixa marcador). Foi usado so para calibrar (b).

Sem evidencia a queda FICA (e perda real). O salto para CIMA sem evidencia so
e neutralizado acima de ``LIMIAR_ALTA_SEM_EVIDENCIA`` (+300% em um mes): com
a tolerancia de 0,1% o que sobra acima de +100% sao subidas reais de papel
sem liquidez e restos de split nao redondo; acima de +300% a hipotese de
unidade de cotacao quebrada pesa mais que a de ganho real.

Neutralizar = o retorno do mes vira ZERO e o preco de todos os meses
anteriores e multiplicado por ``1 + r`` (rebase, como
``serie.neutralizar_eventos_societarios`` da Memoria de Mercado 1.1.0): o
antes e o depois ficam na mesma unidade.

``VERSAO_SANEAMENTO`` entra no relatorio para o numero publicado carregar a
regra que o produziu.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

VERSAO_SANEAMENTO = "2.0.0"

#: Retorno mensal candidato: mais que dobrar / perder mais de 60%.
LIMIAR_ALTA = 1.0
LIMIAR_QUEDA = -0.60
#: Subida sem nenhuma evidencia so e neutralizada acima disto.
LIMIAR_ALTA_SEM_EVIDENCIA = 3.0
#: Distancia maxima (em log) da razao de precos ao fator redondo mais proximo.
TOL_FATOR = 0.001
#: Retorno do close bruto abaixo do qual ele "ficou estavel" (caso c).
CLOSE_ESTAVEL = 0.35

_K = [float(k) for k in range(2, 101)]
_FRACOES = [1.5, 2.5, 3.5, 4 / 3, 5 / 3, 5 / 4]
FATORES = np.array(sorted(set(
    _K + [1 / k for k in _K] + _FRACOES + [1 / f for f in _FRACOES])))


def e_fator_redondo(razao: float, tol: float = TOL_FATOR) -> bool:
    """``razao`` = preco_t / preco_{t-1} esta a ``tol`` (log) de k, 1/k ou
    fracao comum -- a assinatura de desdobramento/grupamento."""
    if not np.isfinite(razao) or razao <= 0:
        return False
    return bool(np.min(np.abs(np.log(razao / FATORES))) <= tol)


@dataclass(frozen=True)
class RelatorioSaneamento:
    """O que o saneamento mexeu -- a tela e o PR citam estes numeros."""
    versao: str = VERSAO_SANEAMENTO
    n_saltos: int = 0
    n_tickers: int = 0
    n_fator_redondo: int = 0
    n_close_estavel: int = 0
    n_alta_sem_evidencia: int = 0
    #: ticker -> [(data, retorno, motivo)]
    saltos: dict[str, list[tuple[pd.Timestamp, float, str]]] = field(
        default_factory=dict)


def _fator_de_rebase(serie: pd.Series, mascara: pd.Series) -> pd.Series:
    """Para cada ponto, produto de (1 + r) dos saltos POSTERIORES a ele."""
    f = (1.0 + serie.pct_change(fill_method=None)).where(mascara, 1.0).fillna(1.0)
    # produto acumulado de tras para frente, deslocado um passo: o salto em t
    # corrige os pontos < t, nao o proprio t.
    acum = f[::-1].cumprod()[::-1]
    return acum.shift(-1).fillna(1.0)


def neutralizar_saltos_mensais(
        precos: pd.DataFrame, brutos: pd.DataFrame | None = None, *,
        limiar_alta: float = LIMIAR_ALTA,
        limiar_queda: float = LIMIAR_QUEDA,
        limiar_alta_sem_evidencia: float = LIMIAR_ALTA_SEM_EVIDENCIA,
        tol_fator: float = TOL_FATOR,
        close_estavel: float = CLOSE_ESTAVEL,
) -> tuple[pd.DataFrame, RelatorioSaneamento]:
    """Painel mensal (datas x tickers) sem os saltos COM evidencia de evento
    societario ou de dado corrompido (ver docstring do modulo).

    ``brutos`` e o painel mensal do ``close`` nao ajustado, mesmo formato; sem
    ele a regra (c) nao existe. Ausencia continua ausencia: o NaN nao vira
    zero nem e preenchido. O retorno e medido entre observacoes consecutivas
    do PROPRIO papel (mes sem cotacao no meio nao esconde o salto).
    """
    if precos is None or precos.empty:
        return precos, RelatorioSaneamento()
    saida = precos.copy()
    achados: dict[str, list[tuple[pd.Timestamp, float, str]]] = {}
    contagem = {"fator": 0, "close": 0, "alta": 0}
    for tk in precos.columns:
        s = precos[tk].dropna()
        s = s[s > 0]
        if len(s) < 2:
            continue
        r = s.pct_change(fill_method=None)
        candidatos = r[(r > limiar_alta) | (r < limiar_queda)]
        if candidatos.empty:
            continue
        rb = None
        if brutos is not None and tk in brutos.columns:
            b = brutos[tk].reindex(s.index)
            b = b.where(b > 0)
            rb = b.pct_change(fill_method=None)
        mascara = pd.Series(False, index=s.index)
        motivos: dict[pd.Timestamp, str] = {}
        for d, ret in candidatos.items():
            if e_fator_redondo(1.0 + ret, tol_fator):
                motivo = "fator"
            elif (rb is not None and pd.notna(rb.get(d))
                    and abs(rb[d]) <= close_estavel):
                motivo = "close"
            elif ret > limiar_alta_sem_evidencia:
                motivo = "alta"
            else:
                continue  # queda sem evidencia: perda real, fica
            mascara[d] = True
            motivos[d] = motivo
            contagem[motivo] += 1
        if not mascara.any():
            continue
        fator = _fator_de_rebase(s, mascara)
        saida.loc[s.index, tk] = s * fator
        achados[str(tk)] = [(d, float(r[d]), m) for d, m in motivos.items()]
    n = int(sum(len(v) for v in achados.values()))
    return saida, RelatorioSaneamento(
        n_saltos=n, n_tickers=len(achados), n_fator_redondo=contagem["fator"],
        n_close_estavel=contagem["close"], n_alta_sem_evidencia=contagem["alta"],
        saltos=achados)


def limites_winsor(retornos, *, pct_baixo: float, pct_alto: float,
                   minimo: int) -> tuple[float, float] | None:
    """Percentis da secao transversal; ``None`` abaixo de ``minimo`` pontos
    (com poucos papeis o percentil e o proprio dado e cortaria sinal real)."""
    v = np.asarray([x for x in retornos if x is not None and np.isfinite(x)],
                   dtype=float)
    if len(v) < minimo:
        return None
    return (float(np.percentile(v, pct_baixo)),
            float(np.percentile(v, pct_alto)))
