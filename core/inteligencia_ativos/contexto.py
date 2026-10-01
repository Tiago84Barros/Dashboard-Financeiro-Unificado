"""
core/inteligencia_ativos/contexto.py
Monta o ``ContextoInvestidor``: a política concluída + a carteira completa +
os cálculos determinísticos sobre ela.

É daqui que sai tudo o que uma análise de ativo sabe sobre o investidor. Só
aceita política COMPLETED (``contexto_obrigatorio`` levanta para o resto), e
sempre recebe a carteira inteira: o peso de um ativo, o desvio da classe e a
concentração do setor só existem em relação ao todo.

Nenhum número é somado aqui: pesos, faixas, desvios, concentração e alertas
vêm de ``calculos.calcular`` e ficam em ``ContextoInvestidor.calculos``.
"""
from __future__ import annotations

from core.estrategia import politica as pol
from core.estrategia import repositorio as repo
from core.inteligencia_ativos import calculos
from core.inteligencia_ativos.modelos import ContextoInvestidor

# O mapa rótulo da carteira → classe da política mora em calculos.py, junto
# com os números; aqui só é reexportado para quem já o importava daqui.
_CLASSE_POLITICA = calculos.CLASSE_POLITICA
classe_politica = calculos.classe_politica


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


def montar(registro: repo.Registro | None, carteira: dict, *,
           faixas: dict | None = None, cenario=None,
           sinais_cenario: tuple = ()) -> ContextoInvestidor:
    """Política concluída + carteira completa + cálculos determinísticos.

    ``faixas`` é opcional (formato em ``calculos.alocacao``): faixas
    aceitáveis que o usuário der por ativo, setor, subclasse ou classe.
    """
    texto = contexto_obrigatorio(registro)   # levanta se não concluída
    v = pol.valores(registro.politica)
    posicoes = tuple(dict(p) for p in carteira.get("posicoes") or [])
    calc = calculos.calcular(posicoes, v, faixas)
    fora = sum(p.peso for p in calc.pesos if p.classe_politica is None)

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
        total_mercado=calc.total,
        posicoes=posicoes,
        peso_por_classe={k: round(x, 2) for k, x in
                         calc.peso_por(calculos.DIM_CLASSE).items()},
        peso_por_setor={k: round(x, 2) for k, x in
                        calc.peso_por(calculos.DIM_SETOR).items()},
        peso_fora_da_politica=round(fora, 2),
        calculos=calc,
        cenario=cenario,
        sinais_cenario=tuple(sinais_cenario),
    )


def texto_carteira(ctx: ContextoInvestidor) -> str:
    """A carteira completa como a LLM vai lê-la, ao lado da política."""
    calc = ctx.calculos
    linhas = ["=== CARTEIRA COMPLETA DO USUÁRIO ==="]
    if ctx.total_mercado:
        valor = f"{ctx.total_mercado:,.2f}".replace(",", "X").replace(".", ",")
        linhas.append(f"Valor total: R$ {valor.replace('X', '.')}")
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
    for p in calc.pesos:
        extra = f" · emissor {p.emissor}"
        if p.indexador is not None:
            extra += f" · indexador {p.indexador}"
        linhas.append(f"- {p.ticker} · {p.nome} · {p.classe} · "
                      f"{p.setor or '—'} · {calculos.fmt_pct(p.peso, 2)}{extra}")
    linhas += ["", calculos.texto(calc)]
    return "\n".join(linhas)
