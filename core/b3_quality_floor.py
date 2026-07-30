"""Piso absoluto de qualidade — o diagnóstico passa a REPROVAR, não só alertar.

Por que este módulo existe. A carteira B3 escolhe o LÍDER de cada segmento
aprovado; a qualidade é medida DENTRO do segmento, nunca contra o mercado. Uma
empresa no pior decil de endividamento da bolsa entra por ser a melhor do seu
segmento, e nada a barra. Foi o que aconteceu em 30/07/2026: UNIP6 (payout 310%,
dívida/PL 3,24) entrou como líder de Petroquímicos numa carteira gerada pelo
perfil recomendado, com o motor de saúde gritando CRÍTICO ao lado, na mesma tela.

A decisão anterior era deliberada — *alertar, não remover* — e continua válida
como padrão de exibição. O que ela não deveria ter feito é ser a única opção: o
usuário nunca teve como pedir "não me traga isso". Este módulo dá a ele o
interruptor.

**O piso não define limiares próprios.** Ele reaproveita ``check_holdings`` e
reprova o que aquele motor já classifica como CRÍTICO. Definir critérios
paralelos aqui recriaria exatamente o defeito que esta sessão inteira corrigiu:
dois motores julgando a mesma empresa com réguas diferentes, divergindo em
silêncio. Se a régua mudar lá, muda aqui junto — por construção, não por
disciplina.

Ausência de dado NÃO reprova. Holding legítima (ITSA4, BRAP3) não tem margem
operacional nem P/FCO próprios e cairia fora por um artefato de estrutura
societária, não por qualidade. Fica declarada como ``sem_evidencia`` e a decisão
volta para quem lê.

Puro (sem Streamlit, sem banco). Coberto por tests/test_b3_quality_floor.py.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from core.b3_holdings_health import ATENCAO, CRITICO, check_holdings
from core.b3_value_route import ValuePolicy

VERSION = "b3-quality-floor-1.0.0"

APROVADO = "aprovado"
REPROVADO = "reprovado"
SEM_EVIDENCIA = "sem_evidencia"


@dataclass(frozen=True)
class FloorPolicy:
    """O que o piso reprova. Padrões conservadores por decisão de projeto."""

    # Reprova o que check_holdings marca como CRÍTICO: falha conclusiva de
    # solvência (FCO negativo, patrimônio negativo, margem operacional negativa)
    # ou payout acima do lucro CONFIRMADO por aperto de caixa.
    reprovar_criticos: bool = True
    # Reprovar também o nível ATENÇÃO derrubaria quase toda a bolsa: com Selic a
    # 15%, apenas 28% do universo tem ROIC acima do risco-livre, e ROIC abaixo da
    # Selic sozinho gera ATENÇÃO. Fica desligado; quem quiser aperto usa o filtro
    # de resiliência, cujo custo em nº de ativos está medido nos perfis.
    reprovar_atencao: bool = False
    # Ausência de dado nunca reprova — ver docstring do módulo.
    reprovar_sem_evidencia: bool = False


@dataclass(frozen=True)
class FloorVerdict:
    """Veredito do piso para UMA empresa."""
    ticker: str
    situacao: str                   # aprovado | reprovado | sem_evidencia
    motivos: tuple[str, ...] = ()

    @property
    def aprovado(self) -> bool:
        return self.situacao == APROVADO


def evaluate(df_mult: pd.DataFrame, tickers: list[str], *,
             policy: FloorPolicy | None = None,
             value_policy: ValuePolicy | None = None,
             selic: float | None = None) -> dict[str, FloorVerdict]:
    """Veredito do piso por ticker, na mesma régua de ``check_holdings``."""
    policy = policy or FloorPolicy()
    saude = check_holdings(df_mult, list(tickers or []),
                           policy=value_policy, selic=selic)
    veredito: dict[str, FloorVerdict] = {}
    for h in saude:
        reprova = ((policy.reprovar_criticos and h.nivel == CRITICO)
                   or (policy.reprovar_atencao and h.nivel == ATENCAO)
                   or (policy.reprovar_sem_evidencia
                       and h.classificacao_valor == "sem_evidencia"))
        if reprova:
            situacao = REPROVADO
        elif h.classificacao_valor == "sem_evidencia":
            situacao = SEM_EVIDENCIA
        else:
            situacao = APROVADO
        veredito[h.ticker] = FloorVerdict(h.ticker, situacao, tuple(h.alertas))
    return veredito


def apply_with_substitution(
    selecionados: list[str],
    ranked_prox: list[tuple[str, float]],
    df_mult: pd.DataFrame,
    *,
    seg_label: str = "",
    policy: FloorPolicy | None = None,
    value_policy: ValuePolicy | None = None,
    selic: float | None = None,
    pesos: dict[str, float] | None = None,
    log: dict | None = None,
    max_substitutos_avaliados: int = 3,
) -> list[str]:
    """Reprova pelo piso e chama o próximo do MESMO segmento.

    A vaga setorial é preservada: quem entra vem do mesmo segmento e herda o
    orçamento de peso de quem saiu. É isso que torna qualidade e diversificação
    compatíveis — sem substituição, reprovar um líder custaria um setor inteiro
    da carteira, e a escolha viraria mesmo um dilema.

    Quando NENHUM candidato do segmento passa, a vaga fica vazia e o motivo vai
    para ``log["sem_substituto"]``. Rebaixar em silêncio para o menos ruim seria
    devolver ao usuário o problema que o piso existe para resolver.

    Espelha ``_aplicar_gate_qualitativo`` de views/portfolio_b3.py (mesma forma,
    mesmo contrato de log) porque os dois portões rodam em sequência sobre a
    mesma lista — divergir na mecânica só criaria bug.
    """
    log = log if log is not None else {}
    log.setdefault("reprovados", [])
    log.setdefault("substituicoes", [])
    log.setdefault("sem_substituto", [])
    pesos = pesos if pesos is not None else {}

    universo = [str(t) for t in selecionados]
    universo += [str(c) for c, _ in (ranked_prox or [])]
    vereditos = evaluate(df_mult, sorted(set(universo)), policy=policy,
                         value_policy=value_policy, selic=selic)

    finais: list[str] = []
    for tk in (str(t) for t in selecionados):
        v = vereditos.get(tk)
        if v is None or v.situacao != REPROVADO:
            finais.append(tk)
            continue
        log["reprovados"].append({"tk": tk, "segmento": seg_label,
                                  "motivo": "; ".join(v.motivos)})
        substituto, avaliados = None, 0
        for cand, _sc in (ranked_prox or []):
            cand = str(cand)
            if cand in finais or cand in selecionados:
                continue
            vc = vereditos.get(cand)
            avaliados += 1
            if vc is not None and vc.situacao != REPROVADO:
                substituto = cand
                break
            if vc is not None:
                log["reprovados"].append({"tk": cand, "segmento": seg_label,
                                          "motivo": "; ".join(vc.motivos)})
            if avaliados >= max_substitutos_avaliados:
                break
        if substituto:
            finais.append(substituto)
            pesos[substituto] = pesos.get(substituto) or pesos.get(tk, 0.0)
            log["substituicoes"].append({"entra": substituto, "sai": tk,
                                         "segmento": seg_label})
        else:
            log["sem_substituto"].append({"tk": tk, "segmento": seg_label})
    return finais
