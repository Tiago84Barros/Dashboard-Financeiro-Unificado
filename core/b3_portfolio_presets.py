"""Perfis pré-configurados da Criação de Portfólio B3.

Por que existem. A aba tem ~20 parâmetros e alguns deles, mal calibrados,
degradam a carteira sem que isso apareça — a varredura automatizada de
29/07/2026 mediu o custo de cada um sobre o universo real. Um usuário que
mexa no controle errado obtém uma carteira pior sem qualquer sinal de que
foi a configuração, não o mercado.

Estes perfis NÃO são "carteira ótima" — isso não existe de forma verificável
fora da amostra, e persegui-lo seria sobreajuste. São **combinações medidas**:
cada valor tem uma evidência associada, e o perfil recomendado é o único que a
varredura verificou como livre de conflito entre restrições.

Evidências que fundamentam o perfil recomendado (universo real, 29/07/2026):

* **Critério "Econômico (Brasil)"** — os dois modos estatísticos aprovam
  **zero segmentos**. Não é preferência: com mediana de 3 empresas por
  segmento, o efeito mínimo detectável é 0,533, inalcançável por sinal real.
* **Resiliência desligada** — exigir ROIC acima da Selic por 5 p.p. corta a
  carteira de 10 para **6 ativos** e penaliza utilities (20% passam) mais que
  cíclicas (27%), porque concessão limita retorno contábil por desenho.
* **Teto setorial 30% + teto cíclico 60%** — a combinação verificada sem
  conflito entre restrições; produziu 10 ativos (60% cíclico, 20%
  defensivo, 7 setores) com os dois limites ativos, e resultado idêntico
  em sementes de hash distintas após o conserto de determinismo.
* **Margem de 15% vs Selic** — 25% derruba para 7 ativos; 5% não muda o
  resultado (10). O piso intermediário mantém exigência sem estrangular.

Puro (sem Streamlit, sem banco). Coberto por tests/test_b3_portfolio_presets.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field

VERSION = "b3-presets-1.0.0"

RECOMENDADO = "Equilibrado (recomendado)"
CONSERVADOR = "Conservador (mais defensivo)"
AMPLO = "Amplo (diagnóstico, não para decidir)"
PERSONALIZADO = "Personalizado"


@dataclass(frozen=True)
class Preset:
    """Uma combinação de parâmetros com a evidência que a sustenta."""
    nome: str
    resumo: str
    valores: dict = field(default_factory=dict)
    evidencias: tuple[str, ...] = ()
    ressalva: str = ""


PRESETS: dict[str, Preset] = {
    RECOMENDADO: Preset(
        nome=RECOMENDADO,
        resumo="Diversificação protegida por tetos, sem filtros que estrangulam "
               "o universo. Foi a única combinação verificada sem conflito "
               "entre restrições.",
        valores={
            "pb3_criterio_aprov2": "Econômico (Brasil)",
            "pb3_thr_selic_hist": 15.0,
            "pb3_teto_setor": 30,
            "pb3_teto_ciclico": 60,
            "pb3_resiliencia": False,
            "pb3_min_empresas": 5,
            "pb3_cheapness": 0,
            "pb3_piso_qualidade": True,
        },
        evidencias=(
            "Modos estatísticos aprovam 0 segmentos no universo real.",
            "Tetos 30%/60% produziram 10 ativos sem conflito entre si.",
            "Margem de 15%: 25% cai para 7 ativos; 5% não muda (10).",
            "Resiliência a 5 p.p. cortaria de 10 para 6 ativos.",
            "Piso de qualidade ligado: sem ele, o líder do segmento entra "
            "mesmo no pior decil de endividamento da bolsa (UNIP6, 30/07/2026).",
        ),
    ),
    CONSERVADOR: Preset(
        nome=CONSERVADOR,
        resumo="Menos exposição ao ciclo industrial/commodities e exigência de "
               "retorno acima do risco-livre. Carteira menor por construção.",
        valores={
            "pb3_criterio_aprov2": "Econômico (Brasil)",
            "pb3_thr_selic_hist": 20.0,
            "pb3_teto_setor": 25,
            "pb3_teto_ciclico": 50,
            "pb3_resiliencia": True,
            "pb3_roic_spread": 0.0,
            "pb3_min_empresas": 5,
            "pb3_cheapness": 0,
            "pb3_piso_qualidade": True,
        },
        evidencias=(
            "Resiliência a 0 p.p. custa 1 ativo (10 → 9); a 5 p.p. custaria 4.",
            "Tetos mais apertados podem conflitar — o app declara se ocorrer.",
        ),
        ressalva="Menos ativos significa mais risco específico por nome. "
                 "Confira se a carteira final ainda tem 5+ ativos.",
    ),
    AMPLO: Preset(
        nome=AMPLO,
        resumo="Universo máximo para EXPLORAR o que o motor enxerga. Sem tetos "
               "de concentração — não use para decidir alocação.",
        valores={
            "pb3_criterio_aprov2": "Econômico (Brasil)",
            "pb3_thr_selic_hist": 10.0,
            "pb3_teto_setor": 100,
            "pb3_teto_ciclico": 100,
            "pb3_resiliencia": False,
            "pb3_min_empresas": 1,
            "pb3_cheapness": 30,
            # Desligado DE PROPÓSITO: este perfil existe para ver o universo
            # inteiro que o motor enxerga, inclusive o que o piso barraria.
            "pb3_piso_qualidade": False,
        },
        evidencias=(
            "Grupo mínimo 1 chega a 13 ativos; barganha 30% chega a 12.",
            "Sem tetos, nada impede concentração de setor ou de fator.",
            "Piso de qualidade DESLIGADO: empresa com FCO negativo ou "
            "patrimônio negativo pode entrar (103 no universo têm o sinal).",
        ),
        ressalva="Perfil de diagnóstico: sem proteção de concentração. A "
                 "carteira daqui pode ficar dominada por um único fator.",
    ),
}


# Desvios cujo CUSTO foi medido. Cada entrada devolve um alerta quando a
# configuração atual cai no caso — para o usuário saber que foi a calibragem,
# não o mercado, que mudou o resultado.
def avaliar_configuracao(valores: dict) -> list[str]:
    """Alertas sobre configurações com custo conhecido. Nunca bloqueia."""
    alertas: list[str] = []
    criterio = str(valores.get("pb3_criterio_aprov2") or "")
    if criterio and not criterio.startswith("Econômico"):
        alertas.append(
            f"O critério “{criterio}” aprovou **zero segmentos** na varredura "
            "sobre o universo real. Com mediana de 3 empresas por segmento, o "
            "teste não tem poder para aprovar nada — use-o como diagnóstico, "
            "não para montar carteira.")

    if valores.get("pb3_resiliencia"):
        spread = float(valores.get("pb3_roic_spread") or 0.0)
        if spread >= 3.0:
            alertas.append(
                f"Exigir ROIC acima da Selic por {spread:g} p.p. cortou a "
                "carteira de 10 para 6 ativos na medição, e penaliza utilities "
                "(20% passam) mais que cíclicas (27%) — concessão limita "
                "retorno contábil por desenho regulatório.")

    if float(valores.get("pb3_thr_selic_hist") or 0) >= 25.0:
        alertas.append(
            "Margem de 25% ou mais vs Selic reduziu a carteira para 7 ativos "
            "na medição. Exigência alta encolhe a diversificação.")

    teto_setor = int(valores.get("pb3_teto_setor") or 100)
    teto_ciclico = int(valores.get("pb3_teto_ciclico") or 100)
    if teto_setor >= 100 and teto_ciclico >= 100:
        alertas.append(
            "Sem teto setorial nem de ciclo: nada impede a carteira de "
            "concentrar num único fator de risco. Foi assim que uma carteira "
            "real saiu 100% cíclica.")
    if teto_setor <= 25 and teto_ciclico <= 50:
        alertas.append(
            "Tetos de 25% por setor e 50% para cíclicos são difíceis de "
            "satisfazer juntos — se conflitarem, o app avisa em vez de violar "
            "em silêncio.")

    if "pb3_piso_qualidade" in valores and not valores.get("pb3_piso_qualidade"):
        alertas.append(
            "Piso absoluto de qualidade DESLIGADO: a carteira volta a aceitar o "
            "líder do segmento seja ele qual for. No universo real há **103 "
            "empresas** com sinal conclusivo de FCO negativo, patrimônio "
            "negativo ou endividamento fora de faixa — e o líder de um segmento "
            "pode ser uma delas.")

    if int(valores.get("pb3_min_empresas") or 5) <= 2:
        alertas.append(
            "Grupo mínimo baixo cria segmentos com 1–2 empresas, onde nenhuma "
            "medida de poder preditivo é calculável.")
    return alertas


def identificar_perfil(valores: dict) -> str:
    """Nome do perfil que corresponde exatamente aos valores, ou Personalizado."""
    for nome, preset in PRESETS.items():
        if all(valores.get(chave) == esperado
               for chave, esperado in preset.valores.items()):
            return nome
    return PERSONALIZADO
