"""Estado de evidência estatística — separar "inconclusivo" de "reprovado".

Auditoria 2026-07 §16: nos modos estatísticos da carteira B3, um segmento que
não podia sequer ser medido (amplitude insuficiente) era rotulado como
*reprovado*, do mesmo modo que um segmento medido e ruim. São situações
opostas, e confundi-las é o erro clássico de tratar **ausência de evidência
como evidência de ausência**.

Com mediana de 3 empresas por segmento na B3, o Rank-IC cross-seccional
frequentemente nem chega a ser calculável (exige ao menos 5 empresas alinhadas
no ano). Quando é calculável, poucos anos de observação deixam o teste com
poder baixíssimo: só um efeito enorme seria detectado.

Este módulo classifica o estado da evidência em três — a favor, contra,
inconclusivo — e quantifica o **efeito mínimo detectável** (MDE), que responde
à pergunta honesta: *"que tamanho de habilidade este teste conseguiria enxergar
com os dados que tenho?"*. Puro, sem banco e sem rede.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

VERSION = "b3-evidence-1.0.0"

A_FAVOR = "evidencia_a_favor"
CONTRA = "evidencia_contra"
INCONCLUSIVO = "inconclusivo"

# Motivos de inconclusão — distintos entre si e ambos diferentes de reprovação.
SEM_AMPLITUDE = "sem_amplitude"          # não deu para medir
SEM_SIGNIFICANCIA = "sem_significancia"  # mediu, mas não distingue de acaso

_ROTULOS = {
    A_FAVOR: "Evidência a favor",
    CONTRA: "Evidência contra",
    INCONCLUSIVO: "Inconclusivo",
}


@dataclass(frozen=True)
class EvidenceVerdict:
    """Veredito sobre o que os dados permitem afirmar — não sobre o ativo."""
    estado: str
    motivo: str
    explicacao: str
    anos_medidos: int
    efeito_minimo_detectavel: float | None
    # Só evidência CONTRA justifica bloquear por conta da estatística.
    # "Inconclusivo" nunca é, sozinho, razão para reprovar.
    bloqueante: bool

    @property
    def rotulo(self) -> str:
        return _ROTULOS.get(self.estado, self.estado)


def minimum_detectable_effect(observacoes: list[float] | np.ndarray, *,
                              alpha: float = 0.10, power: float = 0.80
                              ) -> float | None:
    """Menor Rank-IC médio que o teste detectaria, dado o tamanho da amostra.

    Teste t unilateral de uma amostra: MDE = (t_alpha + t_power) · s / √n.
    Devolve None com menos de 2 observações ou dispersão nula (sem base para
    estimar o erro-padrão).
    """
    valores = np.asarray([v for v in (observacoes or [])
                          if v is not None and np.isfinite(v)], dtype=float)
    n = len(valores)
    if n < 2:
        return None
    desvio = float(valores.std(ddof=1))
    # Observações idênticas dão desvio ~1e-17 (ruído de ponto flutuante), não
    # zero exato. Sem dispersão real não há erro-padrão a estimar.
    escala = max(float(np.abs(valores).mean()), 1e-12)
    if not np.isfinite(desvio) or desvio <= escala * 1e-9:
        return None
    try:
        from scipy.stats import t as _t
        t_alpha = float(_t.ppf(1 - alpha, df=n - 1))
        t_power = float(_t.ppf(power, df=n - 1))
    except Exception:                      # scipy ausente: aproximação normal
        t_alpha, t_power = 1.2816, 0.8416
    return float((t_alpha + t_power) * desvio / math.sqrt(n))


def classify_evidence(*,
                      ic_values: list[float] | None,
                      ic_mean: float | None = None,
                      p_value: float | None = None,
                      min_anos: int = 2,
                      alpha: float = 0.10,
                      ic_contra: float = -0.05) -> EvidenceVerdict:
    """Classifica o que os dados permitem afirmar sobre o poder preditivo.

    Args:
        ic_values: Rank-ICs anuais efetivamente calculados (pode ser vazio).
        ic_mean: média já calculada; deduzida de ``ic_values`` se ausente.
        p_value: significância do IC médio (unilateral), se disponível.
        min_anos: mínimo de anos medidos para o teste ser considerado viável.
        alpha: nível para declarar significância.
        ic_contra: IC médio a partir do qual há evidência CONTRA o sinal.

    Returns:
        ``EvidenceVerdict``. Só ``evidencia_contra`` é bloqueante.
    """
    limpos = [float(v) for v in (ic_values or [])
              if v is not None and np.isfinite(float(v))]
    anos = len(limpos)
    media = (float(ic_mean) if ic_mean is not None and np.isfinite(float(ic_mean))
             else (float(np.mean(limpos)) if limpos else float("nan")))
    mde = minimum_detectable_effect(limpos, alpha=alpha)

    if anos < min_anos or not np.isfinite(media):
        return EvidenceVerdict(
            INCONCLUSIVO, SEM_AMPLITUDE,
            (f"Amplitude insuficiente: {anos} ano(s) com Rank-IC calculável "
             f"(mínimo {min_anos}). O teste não chegou a ser aplicado — isso "
             "não é evidência contra o segmento."),
            anos, mde, False)

    # Sinal claramente anti-preditivo: aqui há, de fato, evidência CONTRA.
    if media <= ic_contra:
        return EvidenceVerdict(
            CONTRA, "sinal_anti_preditivo",
            (f"Rank-IC médio {media:+.3f} em {anos} ano(s): o score ordenou "
             "ao contrário do retorno realizado."),
            anos, mde, True)

    significativo = p_value is not None and np.isfinite(p_value) and p_value < alpha
    if significativo and media > 0:
        return EvidenceVerdict(
            A_FAVOR, "significante",
            (f"Rank-IC médio {media:+.3f} em {anos} ano(s), p={p_value:.3f} "
             f"< {alpha:.2f}."),
            anos, mde, False)

    detalhe_mde = (f" Com esta amostra, só um Rank-IC ≥ {mde:.3f} seria "
                   "detectável." if mde is not None else "")
    return EvidenceVerdict(
        INCONCLUSIVO, SEM_SIGNIFICANCIA,
        (f"Rank-IC médio {media:+.3f} em {anos} ano(s) não se distingue do "
         f"acaso ao nível {alpha:.0%}.{detalhe_mde} Não rejeitar a hipótese "
         "nula não prova ausência de habilidade."),
        anos, mde, False)


def evidence_label(verdict: EvidenceVerdict) -> str:
    """Rótulo curto para tabelas de auditoria."""
    if verdict.estado == INCONCLUSIVO and verdict.motivo == SEM_AMPLITUDE:
        return "Inconclusivo (sem amplitude)"
    if verdict.estado == INCONCLUSIVO:
        return "Inconclusivo (sem significância)"
    return verdict.rotulo
