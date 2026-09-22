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

# Import no TOPO, de proposito (rodada 2, A-3). `scipy` esta pinado em
# requirements.txt, entao o `try: from scipy.stats import t / except
# Exception: <aproximacao>` que estava aqui era ramo MORTO em producao --
# nao servia de resiliencia, servia de armadilha: um `except Exception`
# largo engole tambem o erro que NAO e "scipy ausente" (instalacao
# quebrada, conflito de ABI) e troca a distribuicao em silencio por uma
# que pode inverter o veredito. Erro de import tem que aparecer como erro.
from scipy.stats import t as _t_student

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


def _finitos(observacoes: list[float] | np.ndarray | None) -> np.ndarray:
    """Observações finitas, na ordem de entrada — ausência não é observação."""
    if observacoes is None:
        return np.asarray([], dtype=float)
    return np.asarray([v for v in observacoes
                       if v is not None and np.isfinite(v)], dtype=float)


def desvio_com_dispersao(observacoes: list[float] | np.ndarray | None, *,
                         ddof: int = 1) -> float | None:
    """Desvio-padrão amostral quando há dispersão REAL; None quando não há.

    Esta é a regra ÚNICA de dispersão dos vereditos — o MDE, o p-valor das
    safras (``core.b3_safras``) e o teste no universo
    (``core.b3_pooled_evidence``) chamam ESTA função em vez de cada um
    reimplementar a sua guarda.

    A guarda é RELATIVA à escala das observações, não absoluta. Observações
    praticamente idênticas dão desvio ~1e-17 (ruído de ponto flutuante), não
    zero exato, e ``desvio > 0`` deixa esse ruído passar como se fosse
    dispersão: medido em 2026-09, com os Rank-ICs anuais {2018..2023} todos
    iguais a 0,30 exceto um 0,30 + 1e-12, a guarda absoluta produzia
    p = 5,0e-61 ("evidência a favor" sobre dispersão que não existe) enquanto
    a relativa devolvia "inconclusivo". Como os dois blocos da tela leem os
    MESMOS ICs anuais desde a rodada 2, a divergência aparecia como dois
    cards contraditórios lado a lado.

    Devolve None com menos de 2 observações finitas ou sem dispersão real.
    """
    valores = _finitos(observacoes)
    n = len(valores)
    if n < 2:
        return None
    desvio = float(valores.std(ddof=ddof))
    escala = max(float(np.abs(valores).mean()), 1e-12)
    if not np.isfinite(desvio) or desvio <= escala * 1e-9:
        return None
    return desvio


def teste_t_unilateral(observacoes: list[float] | np.ndarray | None
                       ) -> tuple[float | None, float | None]:
    """(t, p) do teste t unilateral À DIREITA de ``média > 0``, com ddof=1.

    Distribuição t, nunca a aproximação normal: com n na casa de 5 a 15
    observações elas não são intercambiáveis, e é justamente aí que a
    diferença morde — em ``[0.2, 0.0, 0.1]`` o t dá 0,113 e a normal 0,042,
    lados opostos de ``alpha = 0,10``.

    Devolve ``(None, None)`` quando ``desvio_com_dispersao`` não encontra
    dispersão: sem erro-padrão estimável não há teste, e publicar um p-valor
    ali seria publicar o ruído de ponto flutuante como se fosse sinal.
    """
    valores = _finitos(observacoes)
    desvio = desvio_com_dispersao(valores)
    if desvio is None:
        return (None, None)
    n = len(valores)
    estatistica = float(valores.mean()) / (desvio / math.sqrt(n))
    return (estatistica, float(_t_student.sf(estatistica, df=n - 1)))


def sinal_significante(p_value: float | None, *, alpha: float = 0.10) -> bool:
    """O portão do sinal aprova apenas significância DEMONSTRADA.

    ``p_value is None`` é o que ``teste_t_unilateral`` devolve quando não há
    dispersão real entre os Rank-ICs anuais: não existe teste, logo não
    existe significância demonstrada, e num portão que exige prova positiva
    a resposta é ``False``.

    Isso **não** é evidência contra o segmento, e os dois lados foram
    medidos antes da escolha. Quem classifica o estado é
    ``classify_evidence``, que com o mesmo ``None`` devolve
    ``inconclusivo`` (não bloqueante) — a tela imprime "🟡 Inconclusivo",
    nunca "❌ Reprovado (evidência contra)". O outro lado, fabricar o
    p-valor, era o que a cópia da tela fazia até 2026-09: com ``sd == 0`` e
    média > 0 ela gravava ``p = 0.0`` e ``t = inf``, isto é, certeza
    absoluta exatamente onde não há grau de liberdade para afirmar nada.
    Sob postos independentes (sem nenhuma habilidade preditiva, 20.000
    sorteios por tamanho) isso alcança 2,7% dos segmentos de 5 ativos com
    2 anos de Rank-IC.
    """
    if p_value is None:
        return False
    valor = float(p_value)
    return bool(np.isfinite(valor) and valor < alpha)


def minimum_detectable_effect(observacoes: list[float] | np.ndarray, *,
                              alpha: float = 0.10, power: float = 0.80
                              ) -> float | None:
    """Menor Rank-IC médio que o teste detectaria, dado o tamanho da amostra.

    Teste t unilateral de uma amostra: MDE = (t_alpha + t_power) · s / √n.
    Devolve None com menos de 2 observações ou sem dispersão real — a guarda
    é a de ``desvio_com_dispersao``, compartilhada com o teste t.
    """
    valores = _finitos(observacoes)
    n = len(valores)
    desvio = desvio_com_dispersao(valores)
    if desvio is None:
        return None
    t_alpha = float(_t_student.ppf(1 - alpha, df=n - 1))
    t_power = float(_t_student.ppf(power, df=n - 1))
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
