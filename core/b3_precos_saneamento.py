"""
core/b3_precos_saneamento.py -- saltos implausiveis no preco mensal da B3.

Modulo puro (sem streamlit, sem banco). Coberto por
tests/test_b3_precos_saneamento.py.

Por que existe (auditoria app4 04/10/2026, B3-01)
-------------------------------------------------
``market.historical_prices`` traz, para o retorno mensal, 401 saltos acima de
+100% e 235 abaixo de -60% em 1.091 tickers. Medido em 04/10/2026 no armazem
local: em 736 dos 771 saltos extremos (> +100% ou < -50%) o ``close`` BRUTO
salta junto com o ajustado -- ou seja, nao e provento mal ajustado, e
desdobramento/grupamento (ou troca de unidade da cotacao) que o ajuste da
fonte nao retroagiu. Nos outros 35 o ``close`` fica plano e so o ajustado
explode (MMAQ4 +2.637% em 05/2024, RSUL3 com close 0,10 e ajustado 14.593).
Em ambos os casos o "retorno" e unidade de medida, nao dinheiro do acionista.

So 63 dos 772 saltos revertem no mes seguinte: o filtro "salto que volta"
pegaria menos de 10% do problema. O corte e por magnitude.

Regra (``neutralizar_saltos_mensais``)
--------------------------------------
Retorno mensal > ``LIMIAR_ALTA`` (+100%) ou < ``LIMIAR_QUEDA`` (-60%) vira
retorno ZERO: o preco de todos os meses ANTERIORES ao salto e multiplicado
por ``1 + r``, exatamente o que ``serie.neutralizar_eventos_societarios`` faz
no diario da Memoria de Mercado (1.1.0). O antes e o depois ficam na mesma
unidade e a serie continua com o mesmo numero de pontos. Nao e "fabricar
calma": o retorno acumulado antes e depois do salto e preservado, so o salto
em si (que o preco nao consegue separar de desdobramento) sai.

Os cortes sao assimetricos de proposito. Dobrar em um mes (+100%) e raro mas
existe; cair 60% em um mes tambem. Um grupamento 1:10 faz +900%, um
desdobramento 1:3 faz -67%, 1:2 faz -50% (esse ultimo NAO e pego: -50% e
queda real plausivel, e a winsorizacao da safra absorve o resto).

Excecao -- a cauda de quem saiu da bolsa. Queda abaixo do corte nas
``CAUDA_SAIDA_MESES`` ultimas observacoes de um papel cuja serie TERMINA antes
do fim do painel e o desfecho real do acionista (core.b3_saidas, A-116):
neutralizar apagaria exatamente a perda de quem faliu e reabriria o vies de
sobrevivencia que a 2.28.0 fechou.

``VERSAO_SANEAMENTO`` entra no relatorio para o numero publicado carregar a
regra que o produziu.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

VERSAO_SANEAMENTO = "1.0.0"

#: Mais que dobrar em um mes. Medido: 401 ocorrencias, so ~30 revertem.
LIMIAR_ALTA = 1.0
#: Perder mais de 60% em um mes. Medido: 235 ocorrencias abaixo deste corte.
LIMIAR_QUEDA = -0.60
#: Observacoes finais de um papel deslistado onde a queda e real.
CAUDA_SAIDA_MESES = 3


@dataclass(frozen=True)
class RelatorioSaneamento:
    """O que o saneamento mexeu -- a tela e o PR citam estes numeros."""
    versao: str = VERSAO_SANEAMENTO
    n_saltos: int = 0
    n_tickers: int = 0
    saltos: dict[str, list[tuple[pd.Timestamp, float]]] = field(default_factory=dict)


def _fator_de_rebase(serie: pd.Series, mascara: pd.Series) -> pd.Series:
    """Para cada ponto, produto de (1 + r) dos saltos POSTERIORES a ele."""
    f = (1.0 + serie.pct_change(fill_method=None)).where(mascara, 1.0).fillna(1.0)
    # produto acumulado de tras para frente, deslocado um passo: o salto em t
    # corrige os pontos < t, nao o proprio t.
    acum = f[::-1].cumprod()[::-1]
    return acum.shift(-1).fillna(1.0)


def neutralizar_saltos_mensais(
        precos: pd.DataFrame, *,
        limiar_alta: float = LIMIAR_ALTA,
        limiar_queda: float = LIMIAR_QUEDA,
        cauda_saida: int = CAUDA_SAIDA_MESES,
) -> tuple[pd.DataFrame, RelatorioSaneamento]:
    """Painel mensal (datas x tickers) sem os saltos implausiveis.

    Ausencia continua ausencia: o NaN nao vira zero nem e preenchido. O
    retorno do salto e medido entre observacoes consecutivas do PROPRIO
    papel (mes sem cotacao no meio nao esconde o salto).
    """
    if precos is None or precos.empty:
        return precos, RelatorioSaneamento()
    saida = precos.copy()
    fim_painel = precos.dropna(how="all").index.max()
    achados: dict[str, list[tuple[pd.Timestamp, float]]] = {}
    for tk in precos.columns:
        s = precos[tk].dropna()
        s = s[s > 0]
        if len(s) < 2:
            continue
        r = s.pct_change(fill_method=None)
        mascara = (r > limiar_alta) | (r < limiar_queda)
        if not mascara.any():
            continue
        saiu = s.index[-1] < fim_painel - pd.DateOffset(months=1)
        if saiu and cauda_saida > 0:
            cauda = pd.Series(False, index=s.index)
            cauda.iloc[-cauda_saida:] = True
            mascara &= ~((r < limiar_queda) & cauda)
        if not mascara.any():
            continue
        fator = _fator_de_rebase(s, mascara)
        saida.loc[s.index, tk] = s * fator
        achados[str(tk)] = [(d, float(r[d])) for d in s.index[mascara]]
    n = int(sum(len(v) for v in achados.values()))
    return saida, RelatorioSaneamento(n_saltos=n, n_tickers=len(achados),
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
