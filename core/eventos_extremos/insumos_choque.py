"""As fontes dos componentes de resistência a choque da antifragilidade.

Por que este módulo existe
--------------------------
A revisão de 02/09 (A-143) mediu a mesma carteira de duas formas:

===========================================================  =======  =========
chamada                                                      índice   cobertura
===========================================================  =======  =========
``calcular(pos)`` — como a tela fazia                        ``None``       59%
``calcular(pos, correlacao_estresse=…, perda_simulada=…)``     0,113        86%
===========================================================  =======  =========

O ``None`` não era defeito do motor: está declarado em
:data:`~core.eventos_extremos.antifragilidade.NUCLEO_MINIMO` que
*"diversificação sozinha não responde antifragilidade"*, e a tela chamava
``calcular`` sem nenhuma das três medições de choque. Motor correto, entrada
ausente — o mesmo formato de A-140 e A-141. Este módulo é a entrada.

O que ele mede, e com que fonte
-------------------------------
``perda_simulada``
    :func:`core.stress_tests.cenario_pior_caso` sobre a carteira consolidada.

``correlacao_estresse``
    :func:`core.global_portfolio.correlation.correlacao_media` sobre a janela
    recente dos retornos mensais da própria carteira — não uma correlação de
    mercado genérica, e não a correlação da série inteira. Correlação de longo
    prazo *não é* correlação sob estresse: publicar uma no lugar da outra
    responderia à pergunta errada com número plausível.

``qualidade_credito``
    **Sem fonte.** Nenhuma camada da carteira carrega rating de crédito — o
    registro de classes tem ``b3``, ``us`` e ``fii``, e nenhum adaptador traz
    nota de crédito. Sai ``None`` declarado, e não um número derivado de
    proxy. Ele não está no núcleo, então a ausência não bloqueia o índice.

O que ele não faz
-----------------
Não inventa insumo. Todo caminho que não mede devolve ``None`` **com motivo**,
nunca ``0.0``. Em ``perda_simulada``, ``0.0`` significaria "a carteira não
perde nada no pior cenário histórico" — a afirmação mais forte possível,
publicada exatamente quando não se mediu nada.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

from core.global_portfolio.correlation import (  # noqa: E402
    MIN_OBS_CORRELACAO as _MIN_OBS_CORRELACAO,
)

#: Piso de observações para a correlação valer, **lido de quem a calcula**.
#:
#: ``correlation.matriz`` devolve ``NaN`` em todo par com menos de
#: :data:`~core.global_portfolio.correlation.MIN_OBS_CORRELACAO` observações
#: comuns, e ``correlacao_media`` de uma matriz toda ``NaN`` é ``None``. Um
#: piso escrito à mão aqui, menor que o de lá, não daria erro: daria ``None``
#: em toda execução, para sempre, com o código parecendo correto — o mesmo
#: formato do horizonte errado em ``core.noticias.bases_historicas``. Por isso
#: ele não é escrito: é importado.
MIN_MESES_CORRELACAO = _MIN_OBS_CORRELACAO

#: Meses da janela em que a correlação é lida. É a janela de estresse: curta o
#: bastante para refletir o regime atual, longa o bastante para a correlação
#: existir. Tem de ser ``>= MIN_MESES_CORRELACAO``, senão a medição nunca sai.
JANELA_ESTRESSE_MESES = 24

assert JANELA_ESTRESSE_MESES >= MIN_MESES_CORRELACAO, (
    f"janela de estresse ({JANELA_ESTRESSE_MESES}) menor que o piso de "
    f"observacoes da correlacao ({MIN_MESES_CORRELACAO}): a correlacao sairia "
    f"'nao medida' em toda execucao")

#: ``asset_class`` do registro canônico -> rótulo de classe do motor de stress.
#:
#: O mapa é **explícito e sem padrão**. ``stress_tests.aplicar_stress`` usa
#: ``_CLASSE_TO_SHOCK.get(classe, "shock_stock_br")``: uma classe fora do mapa
#: não levanta erro, recebe o choque de ação brasileira. Uma carteira de ações
#: americanas atravessaria inteira como ação da B3 e a perda sairia errada com
#: cara de perda certa. Aqui, classe desconhecida vira limitação declarada.
CLASSE_PARA_STRESS: dict[str, str] = {
    "b3": "Ações BR",
    "us": "ETF Internacional",
    "fii": "FII",
}


@dataclass(frozen=True)
class Insumos:
    """Os três componentes de choque, com o motivo de cada ausência."""

    correlacao_estresse: float | None = None
    qualidade_credito: float | None = None
    perda_simulada: float | None = None
    limitacoes: tuple[str, ...] = field(default_factory=tuple)

    def como_kwargs(self) -> dict:
        """O que vai para ``antifragilidade.calcular`` — só os três."""
        return {"correlacao_estresse": self.correlacao_estresse,
                "qualidade_credito": self.qualidade_credito,
                "perda_simulada": self.perda_simulada}


# ────────────────────────────── perda simulada ───────────────────────────────

def _para_stress(posicoes, total_brl: float | None) -> tuple[list[dict],
                                                             list[str]]:
    """Traduz o quadro de posições para o formato do motor de stress.

    Sem ``valor_brl`` e sem ``total_brl``, a perda percentual ainda é apurável
    a partir dos pesos: ela é relativa, então uma carteira nocional de 1,0
    distribuída pelos pesos dá a mesma ``perda_pct`` que a carteira real.
    """
    limitacoes: list[str] = []
    linhas: list[dict] = []
    desconhecidas: set[str] = set()

    registros = posicoes.to_dict(orient="records")
    tem_valor = any(r.get("valor_brl") is not None for r in registros)

    for reg in registros:
        classe = str(reg.get("asset_class") or "").strip().lower()
        rotulo = CLASSE_PARA_STRESS.get(classe)
        if rotulo is None:
            desconhecidas.add(classe or "(vazia)")
            continue
        if tem_valor:
            valor = float(reg.get("valor_brl") or 0.0)
        else:
            valor = float(reg.get("weight_global") or 0.0)
        if valor <= 0:
            continue
        linhas.append({"classe": rotulo, "valor_mercado": valor,
                       "moeda": "BRL"})

    if desconhecidas:
        limitacoes.append(
            "classes fora do mapa de choque (" + ", ".join(sorted(desconhecidas))
            + "): ficaram de fora da perda simulada em vez de receber o "
              "choque de acao brasileira por omissao")
    return linhas, limitacoes


def perda_simulada(posicoes, *, total_brl: float | None = None
                   ) -> tuple[float | None, tuple[str, ...]]:
    """Perda no pior cenário histórico aplicável, como fração positiva.

    ``None`` quando não há posição mapeável ou quando o motor não devolve
    cenário. Nunca ``0.0`` por ausência: zero seria "esta carteira não perde
    nada", que é a conclusão mais forte que o módulo poderia publicar.
    """
    if posicoes is None or getattr(posicoes, "empty", True):
        return None, ("carteira vazia: perda simulada nao medida",)

    linhas, limitacoes = _para_stress(posicoes, total_brl)
    if not linhas:
        return None, tuple(limitacoes + [
            "nenhuma posicao mapeavel para o motor de stress: perda simulada "
            "nao medida"])

    try:
        from core.stress_tests import cenario_pior_caso

        pior = cenario_pior_caso(linhas)
    except Exception as exc:  # noqa: BLE001 - a tela abre sem o insumo
        logger.warning("perda simulada indisponivel: %s", exc)
        return None, tuple(limitacoes + [
            f"motor de stress indisponivel ({type(exc).__name__}): perda "
            f"simulada nao medida"])

    bruto = (pior or {}).get("perda_pct")
    if bruto is None:
        return None, tuple(limitacoes + [
            "nenhum cenario de stress aplicavel: perda simulada nao medida"])

    # ``perda_pct`` sai negativa (é um retorno). A antifragilidade espera a
    # perda como fração positiva, e a inversão acontece aqui, num lugar só.
    return abs(float(bruto)), tuple(limitacoes)


# ──────────────────────────── correlação de estresse ─────────────────────────

def correlacao_estresse(retornos, *, janela: int = JANELA_ESTRESSE_MESES
                        ) -> tuple[float | None, tuple[str, ...]]:
    """Correlação média entre os ativos na janela recente.

    Args:
        retornos: quadro de ``global_portfolio.returns.retornos_mensais``.
        janela: meses lidos do fim da série.
    """
    if retornos is None or getattr(retornos, "empty", True):
        return None, ("sem retornos da carteira: correlacao sob estresse nao "
                      "medida",)
    if retornos.shape[1] < 2:
        return None, ("carteira com menos de dois ativos com serie: "
                      "correlacao nao e definida",)

    recente = retornos.tail(int(janela))
    if len(recente) < MIN_MESES_CORRELACAO:
        return None, (
            f"apenas {len(recente)} meses de retorno na janela de estresse "
            f"(minimo {MIN_MESES_CORRELACAO}): correlacao nao medida. Ela "
            f"NAO foi substituida pela correlacao da serie inteira, que "
            f"responderia outra pergunta",)

    try:
        from core.global_portfolio.correlation import correlacao_media

        valor = correlacao_media(recente)
    except Exception as exc:  # noqa: BLE001
        logger.warning("correlacao de estresse indisponivel: %s", exc)
        return None, (f"correlacao indisponivel ({type(exc).__name__}): nao "
                      f"medida",)

    if valor is None:
        return None, ("nenhum par de ativos com historico comum na janela: "
                      "correlacao nao medida",)
    return float(valor), ()


# ─────────────────────────────── qualidade de crédito ────────────────────────

MOTIVO_SEM_CREDITO = (
    "qualidade de credito sem fonte: nenhuma classe do registro canonico "
    "(b3, us, fii) carrega rating, e derivar de proxy publicaria um numero "
    "que ninguem apurou. Componente fica em 'nao medido'")


# ──────────────────────────────────── tudo ───────────────────────────────────

def medir(posicoes, *, retornos=None, total_brl: float | None = None
          ) -> Insumos:
    """Reúne os três insumos. Nenhum deles é obrigatório.

    A função nunca levanta: a tela de inteligência tem de abrir mesmo sem
    carteira, sem preços e sem motor de stress. O que ela faz é devolver
    ``None`` **com motivo** em cada componente que não mediu.
    """
    limitacoes: list[str] = []

    perda, lim_perda = perda_simulada(posicoes, total_brl=total_brl)
    limitacoes.extend(lim_perda)

    corr, lim_corr = correlacao_estresse(retornos)
    limitacoes.extend(lim_corr)

    limitacoes.append(MOTIVO_SEM_CREDITO)

    return Insumos(correlacao_estresse=corr, qualidade_credito=None,
                   perda_simulada=perda, limitacoes=tuple(limitacoes))
