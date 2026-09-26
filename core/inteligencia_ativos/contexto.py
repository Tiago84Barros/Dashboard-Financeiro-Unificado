"""
core/inteligencia_ativos/contexto.py
Monta o ``ContextoInvestidor``: a política concluída + a carteira completa.

É daqui que sai tudo o que uma análise de ativo sabe sobre o investidor. Só
aceita política COMPLETED (``contexto_obrigatorio`` levanta para o resto), e
sempre recebe a carteira inteira: o peso de um ativo, o desvio da classe e a
concentração do setor só existem em relação ao todo.
"""
from __future__ import annotations

from core.estrategia import politica as pol
from core.estrategia import repositorio as repo
from core.inteligencia_ativos.modelos import ContextoInvestidor

# Rótulo de classe da carteira (core/investimentos._CLASS_LABEL) → classe da
# política. O que não está aqui (Cripto, Outros, ETF sem país) fica fora das
# classes que a política cobre, e a análise diz isso em vez de encaixar.
_CLASSE_POLITICA: dict[str, str] = {
    "Renda Fixa": "renda_fixa",
    "Tesouro Direto": "renda_fixa",
    "Fundo RF": "renda_fixa",
    "FII": "fiis",
    "Ações BR": "acoes_br",
    "ETF Brasil": "acoes_br",
    "ETF Internacional": "exterior",
    "BDR": "exterior",
}


class PoliticaNaoConcluida(RuntimeError):
    """Tentativa de montar a premissa com política que não está concluída."""


def contexto_obrigatorio(registro: repo.Registro | None) -> str:
    """Premissa que toda análise de ativo leva à LLM.

    Levanta em vez de devolver texto vazio: uma análise sem a estratégia do
    usuário seria genérica, e parecer personalizada sem ser é pior do que
    não responder.
    """
    if registro is None or registro.status != pol.COMPLETED:
        raise PoliticaNaoConcluida(
            "A análise de ativos exige uma estratégia concluída.")
    return pol.texto_da_politica(registro.politica, versao=registro.version,
                                 status=registro.status)


def classe_politica(posicao: dict) -> str | None:
    """Classe da política para uma posição da carteira, ou None.

    A moeda vence o rótulo: a carteira rotula ação americana como "Ações BR"
    quando o tipo bruto é ``stock`` fora do Brasil, e o que decide exposição
    internacional é a moeda em que o ativo é cotado.
    """
    moeda = str(posicao.get("moeda") or "BRL").upper()
    if moeda not in ("", "BRL"):
        return "exterior"
    return _CLASSE_POLITICA.get(str(posicao.get("classe") or ""))


def _pct(posicao: dict) -> float:
    try:
        return float(posicao.get("pct_carteira") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def montar(registro: repo.Registro | None, carteira: dict) -> ContextoInvestidor:
    texto = contexto_obrigatorio(registro)   # levanta se não concluída
    v = pol.valores(registro.politica)
    posicoes = tuple(dict(p) for p in carteira.get("posicoes") or [])

    peso_classe = {c: 0.0 for c, _ in pol.CLASSES}
    peso_setor: dict[str, float] = {}
    fora = 0.0
    for p in posicoes:
        pct = _pct(p)
        classe = classe_politica(p)
        if classe is None:
            fora += pct
        else:
            peso_classe[classe] += pct
        setor = p.get("setor")
        if setor:
            peso_setor[setor] = peso_setor.get(setor, 0.0) + pct

    restricoes = tuple(str(v[c]) for c in ("user_constraints",
                                           "liquidity_constraints") if v.get(c))
    return ContextoInvestidor(
        versao_politica=registro.version,
        objetivo=v.get("objective"),
        horizonte=v.get("time_horizon"),
        perfil_risco=v.get("risk_profile"),
        capacidade_risco=v.get("risk_capacity"),
        estrategia=v.get("predominant_strategy"),
        necessidade_liquidez=v.get("liquidity_need"),
        alocacao_alvo=dict(v.get("asset_class_targets") or {}),
        limites_classe=dict(v.get("asset_class_limits") or {}),
        limite_por_ativo=v.get("single_asset_limit_pct"),
        limite_por_setor=v.get("sector_limit_pct"),
        restricoes=restricoes,
        texto_politica=texto,
        total_mercado=float(carteira.get("total_mercado") or 0.0),
        posicoes=posicoes,
        peso_por_classe={k: round(x, 2) for k, x in peso_classe.items()},
        peso_por_setor={k: round(x, 2) for k, x in peso_setor.items()},
        peso_fora_da_politica=round(fora, 2),
    )


def texto_carteira(ctx: ContextoInvestidor) -> str:
    """A carteira completa como a LLM vai lê-la, ao lado da política."""
    linhas = ["=== CARTEIRA COMPLETA DO USUÁRIO ==="]
    if ctx.total_mercado:
        valor = f"{ctx.total_mercado:,.2f}".replace(",", "X").replace(".", ",")
        linhas.append(f"Valor de mercado total: R$ {valor.replace('X', '.')}")
    linhas.append("\n[Peso por classe da política: atual vs alvo]")
    for classe, rotulo in pol.CLASSES:
        alvo = ctx.alocacao_alvo.get(classe)
        alvo_txt = f"{alvo:g}%" if alvo is not None else "sem alvo"
        linhas.append(f"- {rotulo}: {ctx.peso_por_classe.get(classe, 0):.1f}% "
                      f"(alvo {alvo_txt})")
    if ctx.peso_fora_da_politica:
        linhas.append(f"- Fora das classes da política: "
                      f"{ctx.peso_fora_da_politica:.1f}%")
    linhas.append("\n[Posições]")
    for p in sorted(ctx.posicoes, key=_pct, reverse=True):
        linhas.append(f"- {p.get('ticker')} · {p.get('nome') or ''} · "
                      f"{p.get('classe') or '—'} · {p.get('setor') or '—'} · "
                      f"{_pct(p):.1f}%")
    return "\n".join(linhas)
