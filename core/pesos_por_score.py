"""Regras de seleção e peso por score, comuns à carteira da Empresas B3 e à
carteira de safras dos FIIs.

Ponto único (regra dos gêmeos, CLAUDE.md): até 21/11/2026 estas funções
viviam em ``views/empresas_b3.py`` e ``views/portfolio_b3.py``. A carteira de
safras dos FIIs (``core/fii_safras_carteira.py``) aplica a mesma estrutura, e
duas cópias da mesma conta divergem na primeira correção. As views reexportam
os nomes antigos (``_select_n_heuristica`` etc.) para não quebrar quem os
importa ou os substitui em teste.

Escala: as duas telas dão score de 0 a 100. ``eps`` de ``select_n_heuristica``
é medido nessa escala.
"""
from __future__ import annotations

import math


def select_n_heuristica(scores_desc: list[float], eps: float = 0.35) -> int:
    """Quantos líderes: 1, 2 ou 3, pela distância entre os três primeiros."""
    if len(scores_desc) < 2:
        return 1
    gap12 = scores_desc[0] - scores_desc[1]
    gap23 = scores_desc[1] - scores_desc[2] if len(scores_desc) >= 3 else 1.0
    if gap12 <= eps and gap23 <= eps:
        return 3
    if gap12 <= eps:
        return 2
    return 1


def weights_from_scores(tickers: list[str], score_map: dict[str, float],
                        gamma: float = 0.90) -> dict[str, float]:
    """Peso ∝ (score − mínimo + ε)^gamma; score ausente ou não finito vale 0."""
    scores = [score_map.get(tk, 0.0) for tk in tickers]
    scores = [float(s) if (s is not None and math.isfinite(s)) else 0.0 for s in scores]
    mn = min(scores) if scores else 0.0
    eps = 1e-6
    raw = [(max(s - mn, 0) + eps) ** gamma for s in scores]
    tot = sum(raw) or 1.0
    return {tk: r / tot for tk, r in zip(tickers, raw)}


def apply_cap_soft(weights: dict[str, float],
                   cap: float = 0.25, soft: float = 0.05) -> dict[str, float]:
    """Aplica tilt suave e projeta em pesos válidos com cap estrito."""
    from core.portfolio_constraints import project_capped_simplex

    tilted = {
        tk: float(v) + (float(v) - soft) * 0.5 if float(v) > soft else float(v)
        for tk, v in weights.items()
    }
    return project_capped_simplex(tilted, cap)


def apply_decay_penalty(
    score_map: dict[str, float],
    anos_lideranca: dict[str, int],
    desconto_aa: float = 0.07,
    cap: float = 0.30,
) -> dict[str, float]:
    """Desconta scores de líderes consecutivos — força rotação de carteira."""
    return {
        tk: s * (1.0 - min(anos_lideranca.get(tk, 0) * desconto_aa, cap))
        for tk, s in score_map.items()
    }


def rank_ticker(score_map: dict[str, float], ticker: str) -> int | None:
    """Posição (1 = melhor) com ordenação total: score desc, ticker asc."""
    ranked = sorted(score_map.items(),
                    key=lambda x: (-float(x[1]), str(x[0])))
    for idx, (tk, _) in enumerate(ranked, start=1):
        if tk == ticker:
            return idx
    return None


def pode_incluir_maior_participacao(
    ticker: str,
    ultimo_lid: dict[str, int],
    score_proximo: dict[str, float],
    ano_ref: int,
    max_anos_lid: int,
) -> tuple[bool, str, int | None, int | None]:
    """Regra de entrada: maior participacao precisa ter recencia ou rank atual forte."""
    ultimo = ultimo_lid.get(ticker)
    rank_atual = rank_ticker(score_proximo, ticker)
    if ultimo is not None and (ano_ref - int(ultimo)) <= int(max_anos_lid):
        return True, "Maior participacao com lideranca recente", ultimo, rank_atual
    if rank_atual is not None and rank_atual <= 3:
        return True, "Maior participacao ainda bem ranqueada no score atual", ultimo, rank_atual
    return False, "Maior participacao descartada por lideranca antiga/baixo rank atual", ultimo, rank_atual
