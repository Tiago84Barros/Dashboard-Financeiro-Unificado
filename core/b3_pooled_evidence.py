"""Estatística dimensionada à amostra — teste no universo e encolhimento hierárquico.

Auditoria 2026-07 §16, peça (c). O desenho anterior fazia 78 testes
independentes, um por segmento, sobre uma base com **mediana de 3 empresas por
segmento**. Nessa configuração quase nenhum teste tem poder, e o resultado
agregado é ruído: alguns segmentos "passam" por sorte, a maioria fica muda, e a
correção FDR — correta em si — ainda por cima encarece a barra por causa da
multiplicidade que o próprio desenho criou.

Este módulo troca o desenho, não o rigor. Duas mudanças:

1. **Teste no nível do universo.** O Rank-IC anual é calculado sobre TODAS as
   empresas de uma vez (N > 300), não segmento a segmento. Essa é a pergunta que
   os dados brasileiros conseguem responder: *o meu score ordena vencedores
   acima de perdedores no mercado como um todo?* Um único teste, com amplitude
   real, em vez de 78 sem poder.

2. **Encolhimento hierárquico (empirical Bayes).** A estimativa de cada segmento
   é puxada em direção à média do universo na medida da sua própria incerteza.
   Um segmento com 3 empresas e IC de 0,40 — quase certamente ruído — é
   encolhido quase inteiramente; um com 40 empresas e IC de 0,15 preserva a
   maior parte do seu valor. Segmento sem nenhuma observação recebe a estimativa
   do universo, que é a melhor inferência disponível — e não uma reprovação.

Puro (sem banco, sem rede). Coberto por tests/test_b3_pooled_evidence.py.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from core.b3_evidence import (
    A_FAVOR,
    CONTRA,
    INCONCLUSIVO,
    minimum_detectable_effect,
)

VERSION = "b3-pooled-evidence-1.0.0"

# Mínimo de ativos para um Rank-IC anual ser calculável com algum sentido.
MIN_ATIVOS_ANO = 5


@dataclass(frozen=True)
class SegmentSample:
    """Observações de um segmento: ICs anuais medidos e amplitude típica."""
    key: str
    ic_values: tuple[float, ...] = ()
    n_assets: float = 0.0


@dataclass(frozen=True)
class ShrunkEstimate:
    """Estimativa do segmento após emprestar força do universo."""
    key: str
    ic_bruto: float | None
    ic_encolhido: float
    peso_proprio: float      # 0 = totalmente pooled; 1 = totalmente próprio
    erro_padrao: float | None
    anos: int

    @property
    def explicacao(self) -> str:
        if self.ic_bruto is None:
            return ("Sem observação própria: adota a estimativa do universo "
                    f"({self.ic_encolhido:+.3f}).")
        return (f"IC bruto {self.ic_bruto:+.3f} em {self.anos} ano(s) encolhido "
                f"para {self.ic_encolhido:+.3f} (peso próprio "
                f"{self.peso_proprio:.0%} — o resto vem do universo).")


@dataclass(frozen=True)
class UniverseEvidence:
    """Veredito do teste com amplitude real: o mercado inteiro de uma vez."""
    ic_medio: float
    t_stat: float | None
    p_value: float | None
    anos: int
    n_medio_ativos: float
    efeito_minimo_detectavel: float | None
    estado: str
    explicacao: str


def pooled_yearly_ics(pairs: list[tuple], *, min_ativos: int = MIN_ATIVOS_ANO
                      ) -> dict[int, float]:
    """Rank-IC por ano sobre o universo agrupado.

    Args:
        pairs: tuplas ``(ano, score, retorno)`` de TODAS as empresas, de todos
            os segmentos. Os scores são percentis dentro do grupo de comparação,
            então são comparáveis entre segmentos por construção.
        min_ativos: mínimo de empresas no ano para calcular o IC.

    Returns:
        ``{ano: rank_ic}`` apenas para os anos calculáveis.
    """
    por_ano: dict[int, list[tuple[float, float]]] = {}
    for item in pairs or []:
        try:
            ano, score, retorno = int(item[0]), float(item[1]), float(item[2])
        except (TypeError, ValueError, IndexError):
            continue
        if not (np.isfinite(score) and np.isfinite(retorno)):
            continue
        por_ano.setdefault(ano, []).append((score, retorno))

    saida: dict[int, float] = {}
    for ano, observacoes in sorted(por_ano.items()):
        if len(observacoes) < min_ativos:
            continue
        scores = np.array([o[0] for o in observacoes], dtype=float)
        retornos = np.array([o[1] for o in observacoes], dtype=float)
        # Spearman = Pearson sobre os postos; evita depender do scipy aqui.
        rank_s = _ranks(scores)
        rank_r = _ranks(retornos)
        if rank_s.std() <= 0 or rank_r.std() <= 0:
            continue
        ic = float(np.corrcoef(rank_s, rank_r)[0, 1])
        if np.isfinite(ic):
            saida[ano] = ic
    return saida


def _ranks(values: np.ndarray) -> np.ndarray:
    """Postos com média em empates (equivalente a scipy.stats.rankdata)."""
    ordem = values.argsort()
    postos = np.empty(len(values), dtype=float)
    postos[ordem] = np.arange(1, len(values) + 1, dtype=float)
    # média nos empates
    for valor in np.unique(values):
        mascara = values == valor
        if mascara.sum() > 1:
            postos[mascara] = postos[mascara].mean()
    return postos


def universe_evidence(yearly_ics: dict[int, float] | list[float], *,
                      n_medio_ativos: float = 0.0,
                      alpha: float = 0.10,
                      ic_contra: float = -0.05) -> UniverseEvidence:
    """Testa o poder preditivo no universo — o teste que tem amplitude."""
    valores = (list(yearly_ics.values()) if isinstance(yearly_ics, dict)
               else list(yearly_ics or []))
    limpos = [float(v) for v in valores if v is not None and np.isfinite(float(v))]
    anos = len(limpos)
    mde = minimum_detectable_effect(limpos, alpha=alpha)

    if anos == 0:
        return UniverseEvidence(
            float("nan"), None, None, 0, n_medio_ativos, None, INCONCLUSIVO,
            "Nenhum ano com Rank-IC calculável no universo.")

    media = float(np.mean(limpos))
    t_stat = p_value = None
    if anos >= 2:
        desvio = float(np.std(limpos, ddof=1))
        if desvio > 0:
            t_stat = float(media / (desvio / math.sqrt(anos)))
            try:
                from scipy.stats import t as _t
                p_value = float(_t.sf(t_stat, df=anos - 1))
            except Exception:
                from math import erf
                p_value = float(0.5 * (1 - erf(t_stat / math.sqrt(2))))

    if media <= ic_contra:
        estado = CONTRA
        texto = (f"Rank-IC médio {media:+.3f} no universo ({anos} anos, "
                 f"~{n_medio_ativos:.0f} empresas/ano): o score ordena ao "
                 "contrário do retorno.")
    elif p_value is not None and p_value < alpha and media > 0:
        estado = A_FAVOR
        texto = (f"Rank-IC médio {media:+.3f} no universo ({anos} anos, "
                 f"~{n_medio_ativos:.0f} empresas/ano), p={p_value:.3f} < "
                 f"{alpha:.2f}. Este é o teste com amplitude real.")
    else:
        estado = INCONCLUSIVO
        detalhe = (f" Efeito mínimo detectável: {mde:.3f}."
                   if mde is not None else "")
        texto = (f"Rank-IC médio {media:+.3f} no universo ({anos} anos) não "
                 f"atinge significância a {alpha:.0%}.{detalhe}")
    return UniverseEvidence(media, t_stat, p_value, anos, n_medio_ativos,
                            mde, estado, texto)


def _erro_padrao(sample: SegmentSample) -> float | None:
    """Erro-padrão do IC médio do segmento.

    Com 2+ anos, usa a dispersão observada. Com menos, cai para a variância
    amostral teórica do Spearman (≈ 1/(n−1)), que depende só da amplitude.
    """
    valores = [float(v) for v in sample.ic_values
               if v is not None and np.isfinite(float(v))]
    if len(valores) >= 2:
        desvio = float(np.std(valores, ddof=1))
        if np.isfinite(desvio) and desvio > 0:
            return desvio / math.sqrt(len(valores))
    n = float(sample.n_assets or 0)
    if n > 2:
        # variância do IC ≈ 1/(n−1); com T anos, divide-se por T
        anos = max(len(valores), 1)
        return math.sqrt(1.0 / ((n - 1.0) * anos))
    return None


def shrink_segment_estimates(samples: list[SegmentSample], *,
                             universe_mean: float | None = None
                             ) -> list[ShrunkEstimate]:
    """Encolhe as estimativas por segmento em direção à média do universo.

    Empirical Bayes por método dos momentos: ``w = τ²/(τ² + se²)``, com τ²
    (variância entre segmentos) estimada como a variância observada das
    estimativas menos a variância amostral média. Quando a dispersão entre
    segmentos não excede o ruído de medida, τ² → 0 e tudo colapsa para a média
    do universo — que é a conclusão correta: os segmentos não se distinguem.
    """
    if not samples:
        return []

    brutos: dict[str, float | None] = {}
    erros: dict[str, float | None] = {}
    for sample in samples:
        valores = [float(v) for v in sample.ic_values
                   if v is not None and np.isfinite(float(v))]
        brutos[sample.key] = float(np.mean(valores)) if valores else None
        erros[sample.key] = _erro_padrao(sample)

    medidos = [(brutos[s.key], erros[s.key]) for s in samples
               if brutos[s.key] is not None and erros[s.key]]
    if universe_mean is not None and np.isfinite(universe_mean):
        mu = float(universe_mean)
    elif medidos:
        pesos = np.array([1.0 / (e ** 2) for _, e in medidos])
        mu = float(np.average([b for b, _ in medidos], weights=pesos))
    else:
        mu = 0.0

    if len(medidos) >= 2:
        estimativas = np.array([b for b, _ in medidos], dtype=float)
        variancias = np.array([e ** 2 for _, e in medidos], dtype=float)
        tau2 = max(0.0, float(estimativas.var(ddof=1) - variancias.mean()))
    else:
        tau2 = 0.0

    saida: list[ShrunkEstimate] = []
    for sample in samples:
        bruto = brutos[sample.key]
        erro = erros[sample.key]
        anos = len([v for v in sample.ic_values
                    if v is not None and np.isfinite(float(v))])
        if bruto is None or not erro:
            # Sem observação própria: a melhor inferência é a do universo.
            saida.append(ShrunkEstimate(sample.key, None, mu, 0.0, erro, anos))
            continue
        peso = tau2 / (tau2 + erro ** 2) if (tau2 + erro ** 2) > 0 else 0.0
        encolhido = peso * bruto + (1.0 - peso) * mu
        saida.append(ShrunkEstimate(
            sample.key, bruto, float(encolhido), float(peso), erro, anos))
    return saida
