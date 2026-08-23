"""O motor de movimentacao: transforma sinais em `Acao`, com duas guardas.

Fluxo (secao 8 da spec, Fase 3b Task 4): combinar `Sinal`s por ativo -> peso
alvo (tilt proporcional ao score) -> projetar por
`core.portfolio_constraints` (teto por ativo, depois teto por classe usando
`alvos`) -> resolver SE hoje e dia de mexer por `core.rebalancing` -> so entao
descontar o custo por `core.transaction_costs` e decidir a acao final.

Duas regras nao-negociaveis, na ordem em que aparecem no plano
(docs/superpowers/plans/2026-08-16-fase-3b-motor-de-movimentacao.md):

1. **Movimento menor que o custo vira `manter`.** Cost e movimento sao
   comparados na MESMA unidade (fracao do patrimonio), para a comparacao ter
   leitura direta: um `custo_estimado` que consumiria o proprio `delta` de
   peso nao vale a pena executar. Quando a classe do ativo NAO esta calibrada
   (`CostConfig.calibrado is False`, `custo_transaction_costs.CostConfig.nao_calibrado`),
   o custo sai `math.nan`. Comparar `nan >= delta` sempre da `False` em
   Python — o bug classico que aprovaria o movimento por acidente. Este
   modulo NUNCA faz essa comparacao: o guard `if not cfg.calibrado` intercepta
   antes, e a acao cai para `manter` de proposito (nao provamos que o
   movimento compensa o custo, entao nao o recomendamos), com
   `custo_calibrado=False` visivel para quem olhar a recomendacao decidir por
   fora.

2. **Sinal sobre dado ausente nunca vira acao.** Ativo do quadro de posicoes
   sem NENHUM `Sinal` (nao so o de risco — qualquer um) fica com `score=None`
   e `acao="indeterminado"`, nunca "manter": "nao sabemos" (indeterminado) e
   "sabemos e a resposta e nao mexer" (manter) sao coisas diferentes, mesmo
   principio de `roles.indeterminados`. Esse ativo fica PINADO no peso atual
   antes da projecao — sem evidencia, sem tilt — mas `peso_sugerido` ainda
   pode diferir de `peso_atual` em resultado, porque a projecao devolve um
   simplex (soma 1) e mexer em outros ativos necessariamente redistribui a
   fatia dos que nao mexeram; isso e identidade contabil, nao uma
   recomendacao sobre o ativo indeterminado.

`peso_sugerido` e SEMPRE o peso pos-projecao (`peso_projetado`), mesmo quando
`acao` acaba em "manter" por causa da politica de rebalanceamento ou do
guard de custo — ele documenta "para onde o modelo aponta", enquanto `acao`
documenta "o que fazer agora". Separar as duas coisas evita apagar
informacao real (o alvo) so porque a execucao foi adiada.

Camada pura: sem SQL, sem Streamlit, sem I/O. Coberto por
tests/test_global_advisor.py.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

import pandas as pd

from core.global_portfolio.signals import Sinal
from core.portfolio_constraints import project_capped_simplex, project_class_capped
from core.rebalancing import RebalancePolicy
from core.transaction_costs import (
    CostConfig,
    custo_compra,
    custo_por_classe,
    custo_venda,
)

# Score +-1 no maximo produz ate +-50% de variacao relativa sobre o peso
# atual do ativo. Escolha de desenho (mesmo espirito de LIMITE_ATIVO_DEFAULT
# em signals.py), nao uma constante fisica -- exposta para o chamador ajustar.
SENSIBILIDADE_SCORE_DEFAULT = 0.5

# Peso projetado abaixo disto e tratado como saida completa ("vender"), nao
# reducao parcial ("reduzir"). Escolha de desenho, nao fato sobre o ativo.
LIMIAR_VENDA_DEFAULT = 0.001

# Sem teto por ativo explicito, a projecao so normaliza para soma 1 (nenhum
# ativo isolado pode ultrapassar 100% de qualquer forma) -- default permissivo
# de proposito, para carteiras de teste pequenas nao esbarrarem em
# InfeasiblePortfolioConstraint por acidente. Quem quiser o teto real da
# tela (LIMITE_ATIVO_DEFAULT de signals.py) passa explicitamente.
CAP_ATIVO_DEFAULT = 1.0

# Diferenca de peso abaixo disto e ruido numerico da projecao, nunca um
# movimento real.
_EPSILON_MOVIMENTO = 1e-9

ACOES_VALIDAS: frozenset[str] = frozenset({
    "aumentar", "reduzir", "vender", "manter", "indeterminado",
})


@dataclass(frozen=True)
class Acao:
    """Uma recomendacao de movimento para um ativo, com a decomposicao que a gerou.

    `componentes` e `analisadores` documentam PROCEDENCIA: `componentes` e
    `{nome_do_sinal: valor}` para todo `Sinal` que existiu para este ativo
    (a media desses valores e o `score`); `analisadores` e o conjunto dos
    modulos (`concentration`, `correlation`, `factors`, `risk`, `roles`,
    `metrics`, `targets`) que de fato produziram esses sinais. Ambos vazios
    quando `acao == "indeterminado"` -- nenhum sinal existiu para o ativo.

    `score` e `None` exatamente quando `acao == "indeterminado"`.

    `custo_estimado` e `math.nan` exatamente quando `custo_calibrado is
    False` -- nunca um numero que parece apurado para uma classe sem
    parametros calibrados (ver `core.transaction_costs.CostConfig.nao_calibrado`).
    """

    symbol: str
    acao: str
    peso_atual: float
    peso_sugerido: float
    score: float | None
    componentes: dict[str, float]
    analisadores: frozenset[str]
    custo_estimado: float
    custo_calibrado: bool


def _coletar_posicoes(df_posicoes: pd.DataFrame) -> tuple[dict[str, float], dict[str, str]]:
    linhas = df_posicoes.to_dict(orient="records")
    peso_atual: dict[str, float] = {}
    classe_por_symbol: dict[str, str] = {}
    for linha in linhas:
        symbol = str(linha["symbol"])
        peso_atual[symbol] = float(linha.get("weight_global") or 0.0)
        classe_por_symbol[symbol] = str(linha.get("asset_class") or "").strip().lower()
    return peso_atual, classe_por_symbol


def _agrupar_sinais(sinais: list[Sinal] | None,
                    symbols: list[str]) -> tuple[dict[str, dict[str, float]], dict[str, set[str]]]:
    """Agrupa sinais por ativo. Sinal de ativo fora do quadro de posicoes e ignorado
    (nao ha `peso_atual` para basear um `Acao`)."""
    componentes: dict[str, dict[str, float]] = {s: {} for s in symbols}
    analisadores: dict[str, set[str]] = {s: set() for s in symbols}
    validos = set(symbols)
    for sinal in sinais or []:
        if sinal.symbol not in validos:
            continue
        componentes[sinal.symbol][sinal.nome] = sinal.valor
        analisadores[sinal.symbol].add(sinal.analisador)
    return componentes, analisadores


def _scores(componentes: dict[str, dict[str, float]]) -> dict[str, float | None]:
    """Media dos componentes por ativo; `None` (indeterminado) sem nenhum sinal."""
    saida: dict[str, float | None] = {}
    for symbol, comp in componentes.items():
        saida[symbol] = (sum(comp.values()) / len(comp)) if comp else None
    return saida


def _pesos_alvo_brutos(symbols: list[str], peso_atual: dict[str, float],
                       scores: dict[str, float | None], sensibilidade: float) -> dict[str, float]:
    """Tilt proporcional ao score sobre o peso atual. Ativo indeterminado (score
    `None`) ou com peso atual zero (motor tilta posicoes existentes, nao origina
    posicao nova a partir de zero -- fora de escopo desta fase) fica pinado."""
    saida: dict[str, float] = {}
    for symbol in symbols:
        atual = peso_atual[symbol]
        score = scores[symbol]
        if score is None or atual <= 0:
            saida[symbol] = atual
        else:
            saida[symbol] = max(atual * (1.0 + sensibilidade * score), 0.0)
    return saida


def _projetar(symbols: list[str], pesos_bruto: dict[str, float],
             classe_por_symbol: dict[str, str], alvos: Mapping[str, float] | None,
             cap_ativo: float) -> dict[str, float]:
    """Teto por ativo (sempre) e depois, por classe presente em `alvos`, o teto
    do proprio alvo (nunca empurra uma classe SUBPONDERADA para cima -- essa
    direcao e papel do sinal `desvio_alvo`, ja embutido no score; aqui so o
    teto superior e reforcado como garantia dura, nao sugestao).

    Ordem deterministica: `symbols` ja chega ordenado por quem chama; classes
    de `alvos` sao percorridas em ordem alfabetica -- o resultado independe da
    ordem de insercao de `alvos` ou de `sinais`.
    """
    pesos = project_capped_simplex({s: pesos_bruto[s] for s in symbols}, cap_ativo)

    if alvos:
        for classe in sorted(alvos):
            alvo = alvos[classe]
            if alvo is None or alvo <= 0:
                continue
            marcados = {s: (classe_por_symbol.get(s) == classe) for s in symbols}
            if not any(marcados.values()):
                continue
            pesos, _avisos = project_class_capped(pesos, marcados, cap_ativo, float(alvo))
    return pesos


def _resolver_custo(symbol: str, delta: float, classe: str, patrimonio_total: float,
                    custos: Mapping[str, CostConfig] | None) -> tuple[float, bool]:
    """(custo_estimado, custo_calibrado) da ordem que executaria `delta` de peso."""
    cfg = custo_por_classe(classe, custos)
    if not cfg.calibrado:
        return math.nan, False
    valor_bruto = abs(delta) * patrimonio_total
    if delta > 0:
        custo = custo_compra(symbol, valor_bruto, cfg)
    else:
        # Sem rastreio de lote/preco medio neste modulo (funcao pura, sem
        # I/O): lucro entra como 0.0, o que faz o IR sair 0.0 por construcao
        # (custo_venda so tributa lucro > 0) -- o custo relatado aqui e a
        # friccao de negociacao (corretagem + meia-spread), nunca o IR, que
        # exigiria base de custo que este modulo nao tem e nao inventa.
        fee, ir = custo_venda(symbol, valor_bruto, 0.0, 0.0, cfg)
        custo = fee + ir
    return custo, True


def recomendar(
    df_posicoes: pd.DataFrame,
    sinais: list[Sinal],
    *,
    alvos: Mapping[str, float],
    politica: RebalancePolicy,
    custos: Mapping[str, CostConfig],
    patrimonio_total: float,
    data_atual: date,
    ultimo_rebal: date | None = None,
    cap_ativo: float = CAP_ATIVO_DEFAULT,
    sensibilidade: float = SENSIBILIDADE_SCORE_DEFAULT,
    limiar_venda: float = LIMIAR_VENDA_DEFAULT,
) -> list[Acao]:
    """Combina `sinais` em `Acao` por ativo. Ver o docstring do modulo para as
    duas regras (custo nao calibrado -> `manter`; sinal ausente -> `indeterminado`).

    `patrimonio_total` (em R$) converte o `delta` de peso (fracao) no
    `valor_bruto` que `core.transaction_costs` espera -- sem ele nao ha como
    comparar um custo fixo (`corretagem_fixa`) contra o movimento em fracao.

    Determinismo: toda estrutura intermediaria e construida iterando
    `symbols = sorted(peso_atual)` (nunca a ordem de `df_posicoes` ou de
    `sinais`), e a saida final e ordenada por `symbol`. O mesmo input produz
    a mesma lista de `Acao` independente da ordem de `df_posicoes` ou de
    `sinais` na chamada.
    """
    if df_posicoes is None or df_posicoes.empty:
        return []

    peso_atual, classe_por_symbol = _coletar_posicoes(df_posicoes)
    symbols = sorted(peso_atual)

    componentes, analisadores = _agrupar_sinais(sinais, symbols)
    scores = _scores(componentes)

    pesos_bruto = _pesos_alvo_brutos(symbols, peso_atual, scores, sensibilidade)
    peso_projetado = _projetar(symbols, pesos_bruto, classe_por_symbol, alvos, cap_ativo)

    deve_rebalancear, _motivo = politica.deve_rebalancear(
        peso_atual, peso_projetado, data_atual, ultimo_rebal)

    acoes: list[Acao] = []
    for symbol in symbols:
        atual = peso_atual[symbol]
        sugerido = peso_projetado.get(symbol, atual)
        score = scores[symbol]
        comp = componentes[symbol]
        analis = frozenset(analisadores[symbol])

        if score is None:
            acoes.append(Acao(symbol=symbol, acao="indeterminado", peso_atual=atual,
                              peso_sugerido=sugerido, score=None, componentes=comp,
                              analisadores=analis, custo_estimado=0.0, custo_calibrado=True))
            continue

        delta = sugerido - atual

        if not deve_rebalancear or abs(delta) <= _EPSILON_MOVIMENTO:
            acoes.append(Acao(symbol=symbol, acao="manter", peso_atual=atual,
                              peso_sugerido=sugerido, score=score, componentes=comp,
                              analisadores=analis, custo_estimado=0.0, custo_calibrado=True))
            continue

        classe = classe_por_symbol[symbol]
        custo, calibrado = _resolver_custo(symbol, delta, classe, patrimonio_total, custos)

        if not calibrado:
            # Regra 1, ramo "nao sabemos o custo": NUNCA comparar nan >= delta
            # (sempre False, aprovaria por acidente). O movimento fica manter
            # ate a classe ser calibrada -- decisao deliberada, nao omissao.
            acoes.append(Acao(symbol=symbol, acao="manter", peso_atual=atual,
                              peso_sugerido=sugerido, score=score, componentes=comp,
                              analisadores=analis, custo_estimado=custo, custo_calibrado=False))
            continue

        custo_fracao = (custo / patrimonio_total) if patrimonio_total > 0 else math.inf
        if custo_fracao >= abs(delta):
            # Regra 1, ramo "sabemos o custo e ele consome o proprio movimento".
            acoes.append(Acao(symbol=symbol, acao="manter", peso_atual=atual,
                              peso_sugerido=sugerido, score=score, componentes=comp,
                              analisadores=analis, custo_estimado=custo, custo_calibrado=True))
            continue

        if delta > 0:
            acao_final = "aumentar"
        elif sugerido <= limiar_venda:
            acao_final = "vender"
        else:
            acao_final = "reduzir"

        acoes.append(Acao(symbol=symbol, acao=acao_final, peso_atual=atual,
                          peso_sugerido=sugerido, score=score, componentes=comp,
                          analisadores=analis, custo_estimado=custo, custo_calibrado=True))

    return sorted(acoes, key=lambda a: a.symbol)
