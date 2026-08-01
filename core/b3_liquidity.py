"""Piso de negociabilidade por CLASSE de ação, com troca pela classe irmã.

Por que existe. O filtro de liquidez da carteira agia sobre o **valor de mercado
da empresa**, e valor de mercado não distingue classe: uma ordinária que quase
não negocia herda o porte da companhia inteira e passa. Medido em 30/07/2026
sobre uma carteira real, o app escolheu **BRAP3 girando ~R$ 649 mil/dia quando
BRAP4 girava ~R$ 46,8 milhões — 72× mais**, com a mesma exposição econômica.
Quem investe fica com a tese certa no papel errado, e descobre isso na hora de
sair da posição.

A troca aqui é entre classes da MESMA empresa, então a tese não muda: o que
muda é a facilidade de entrar e sair. Por isso ela é automática — é ganho sem
contrapartida. Já EXCLUIR um ativo ilíquido que não tem classe irmã seria outra
coisa: custaria diversificação e mudaria a carteira. Este módulo nunca exclui;
quando não há para onde trocar, ele avisa e segue.

Diferença entre as classes que o módulo NÃO resolve, e por isso avisa: ON e PN
diferem em voto e em tag-along, e units carregam composição própria. A escolha
por giro é sobre negociabilidade, não sobre direitos societários.

Puro (sem banco, sem rede). Coberto por tests/test_b3_liquidity.py.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

VERSION = "b3-liquidity-1.0.0"


@dataclass(frozen=True)
class LiquidityPolicy:
    """Limites da troca por liquidez.

    piso_diario: abaixo disso a classe é considerada de negociação difícil.
    vantagem_minima: a irmã precisa girar N× mais para justificar a troca. Não
        é 1,0 porque trocar por diferença marginal só produz ruído — e a série
        de preços é MENSAL, então a estimativa diária tem folga de erro.
    """

    piso_diario: float = 1_000_000.0
    vantagem_minima: float = 3.0


def _num(valor: object) -> float:
    try:
        numero = float(valor)                      # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("nan")
    return numero


def formata_reais(valor: float) -> str:
    """Milhar com ponto, no padrão pt-BR.

    Existe porque aplicar ``.replace(',', '.')`` sobre a FRASE inteira também
    troca a pontuação do texto — o aviso saía com "por dia. abaixo do piso".
    A troca tem de ficar restrita ao número.
    """
    return f"{valor:,.0f}".replace(",", ".")


def melhor_classe(ticker: str, irmas: Iterable[str],
                  giro: Mapping[str, float],
                  policy: LiquidityPolicy | None = None) -> str | None:
    """Classe irmã que vale a troca, ou None.

    Devolve None quando o ticker já é líquido o bastante, quando não há giro
    medido (ausente nunca vira veredito) ou quando nenhuma irmã supera a
    vantagem mínima.
    """
    policy = policy or LiquidityPolicy()
    alvo = str(ticker).upper()
    giro_atual = _num(giro.get(alvo))
    if giro_atual != giro_atual:                   # sem medição: não decide
        return None
    if giro_atual >= policy.piso_diario:
        return None

    # Ordenação TOTAL: sem o desempate por ticker, duas irmãs com o mesmo giro
    # sairiam na ordem de iteração do dicionário e a carteira deixaria de ser
    # reproduzível — o mesmo defeito já corrigido no ranking de segmentos.
    candidatas = sorted(
        ((str(t).upper(), _num(giro.get(str(t).upper())))
         for t in irmas if str(t).upper() != alvo),
        key=lambda par: (-(par[1] if par[1] == par[1] else -1.0), par[0]),
    )
    for tk, g in candidatas:
        if g != g:
            continue
        if g >= giro_atual * policy.vantagem_minima and g >= policy.piso_diario:
            return tk
    return None


def aplicar_piso_de_liquidez(
    itens: Sequence[dict],
    irmas_por_ticker: Mapping[str, Sequence[str]],
    giro: Mapping[str, float],
    *,
    policy: LiquidityPolicy | None = None,
    veto: Callable[[str], str | None] | None = None,
) -> tuple[list[dict], list[dict], list[str]]:
    """Troca classes ilíquidas pela irmã mais negociada da mesma empresa.

    Args:
        itens: carteira selecionada; cada dict tem ao menos ``tk``. O peso e os
            demais campos seguem intactos — trocar de classe não é trocar de
            empresa, então o orçamento da vaga não muda.
        irmas_por_ticker: classes ativas de cada empresa, por ticker.
        giro: giro financeiro diário estimado, em reais, por ticker.
        veto: recebe a candidata e devolve o MOTIVO da recusa, ou None para
            aceitar. É como a chamadora impede que a troca entre com um papel
            que o piso de qualidade reprovaria — o módulo não conhece
            fundamento, e não deveria.

    Returns:
        (itens novos, substituições, avisos). Nunca remove item.
    """
    policy = policy or LiquidityPolicy()
    presentes = {str(i.get("tk") or "").upper() for i in itens}
    saida: list[dict] = []
    trocas: list[dict] = []
    avisos: list[str] = []

    for item in itens:
        tk = str(item.get("tk") or "").upper()
        candidata = melhor_classe(tk, irmas_por_ticker.get(tk, ()), giro, policy)

        if candidata and candidata in presentes:
            # As duas classes já estão na carteira: trocar criaria duplicata.
            candidata = None

        motivo_veto = veto(candidata) if (candidata and veto) else None
        if candidata and motivo_veto:
            avisos.append(
                f"**{tk}** gira pouco (R$ {formata_reais(_num(giro.get(tk)))}/dia) e a "
                f"classe irmã **{candidata}** seria mais negociável, mas foi "
                f"recusada: {motivo_veto}.")
            candidata = None

        if not candidata:
            g = _num(giro.get(tk))
            if g == g and g < policy.piso_diario and not motivo_veto:
                avisos.append(
                    f"**{tk}** gira cerca de R$ {formata_reais(g)} por dia, abaixo do "
                    f"piso de R$ {formata_reais(policy.piso_diario)}, e não há classe "
                    "irmã mais líquida da mesma empresa. Segue na carteira: "
                    "montar posição aos poucos é viável, desmontar às pressas "
                    "não.")
            saida.append(item)
            continue

        novo = dict(item)
        novo["tk"] = candidata
        presentes.discard(tk)
        presentes.add(candidata)
        saida.append(novo)
        trocas.append({
            "sai": tk, "entra": candidata,
            "giro_sai": _num(giro.get(tk)),
            "giro_entra": _num(giro.get(candidata)),
            "setor": item.get("setor"), "segmento": item.get("segmento"),
        })
    return saida, trocas, avisos
