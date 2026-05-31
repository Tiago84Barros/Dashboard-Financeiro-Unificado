"""
core/segment_calibration.py — Calibração ótima automática por segmento.

Cada segmento da B3 tem características financeiras próprias: um banco não pode
ser avaliado como um varejista, nem uma utility regulada como uma empresa de
tecnologia asset-light. Este módulo gera, automaticamente, uma calibração
"ótima default" por segmento — o usuário não precisa calibrar manualmente.

A calibração tem DUAS camadas:

  (a) Arquétipo financeiro — classifica o segmento em um perfil (banco,
      seguradora, utility regulada, commodity cíclica, varejo, tech asset-light,
      construção, etc.) e define o tratamento adequado de dívida, liquidez,
      dividendos, valuation, crescimento e outliers para aquele perfil.

  (b) Refino data-driven — ajusta limites (teto de endividamento, referência de
      DY, faixas de winsorização) usando a distribuição REAL do segmento.

O resultado é um objeto SegmentCalibration. Os PESOS dos indicadores são
aplicados diretamente ao motor de score (_score_universo); os demais critérios
(filtros, limites de risco, thresholds de aprovação, normalização, tratamento de
outliers) compõem o perfil recomendado do segmento, exibido com transparência.

Fundamentação:
  - Damodaran, A. — valuation e métricas por setor (banks/financials, cyclicals,
    utilities, growth) exigem tratamentos distintos.
  - Fama & French (1992/1997) — heterogeneidade de fatores entre indústrias.
  - Penman, S. (2013) — análise de demonstrações financeiras por tipo de negócio.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


# Direção padrão: indicadores onde "menor é melhor"
_MENOR_MELHOR = ("P/L", "P/VP", "EV_EBIT", "P_FCO")


def _dir_melhor_alto(indicador: str) -> bool:
    """True se 'maior é melhor' para o indicador."""
    return indicador not in _MENOR_MELHOR and "endiv" not in indicador.lower()


# ──────────────────────────────────────────────────────────────────────────
# Perfis de arquétipo
# ──────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Arquetipo:
    """Perfil financeiro de um grupo de segmentos com tratamento próprio."""
    key:         str
    label:       str
    descricao:   str
    # tratamento de dívida: "ignorar" | "tolerante" | "normal" | "rigoroso"
    debt:        str
    # liquidez corrente é métrica relevante para este negócio?
    liquidez_relevante: bool
    # relevância de dividendos: "alta" | "media" | "baixa" | "nula"
    dy:          str
    # foco de valuation: "valor" | "crescimento" | "renda" | "misto"
    foco:        str
    # faixas de winsorização recomendadas (cíclicos → mais largas)
    winsor:      tuple[float, float]
    # critérios de aprovação recomendados
    score_entrada_min: float
    risk_penalty_max:  float
    # teto de endividamento (Dív/PL) acima do qual é sinal de risco; None = N/A
    endividamento_teto: float | None
    # piso de liquidez corrente recomendado; None = N/A
    liquidez_corrente_min: float | None


# Catálogo de arquétipos (fundamentação financeira por tipo de negócio)
ARQUETIPOS: dict[str, Arquetipo] = {
    "BANCO": Arquetipo(
        "BANCO", "Banco / Intermediário financeiro",
        "Alavancagem é o próprio negócio; índices de dívida e liquidez corrente "
        "não se aplicam. Avalia rentabilidade do capital (ROE), eficiência e renda.",
        debt="ignorar", liquidez_relevante=False, dy="alta", foco="renda",
        winsor=(0.05, 0.95), score_entrada_min=60.0, risk_penalty_max=8.0,
        endividamento_teto=None, liquidez_corrente_min=None,
    ),
    "SEGURADORA": Arquetipo(
        "SEGURADORA", "Seguradora / Previdência",
        "Estrutura de passivos atuariais; dívida convencional não se aplica. "
        "Foco em ROE, margem e consistência de resultado.",
        debt="ignorar", liquidez_relevante=False, dy="media", foco="renda",
        winsor=(0.05, 0.95), score_entrada_min=60.0, risk_penalty_max=8.0,
        endividamento_teto=None, liquidez_corrente_min=None,
    ),
    "SERVICOS_FIN": Arquetipo(
        "SERVICOS_FIN", "Serviços financeiros (bolsa, gestão, meios de pagamento)",
        "Financeiro asset-light: alta rentabilidade do capital, baixa "
        "alavancagem, crescimento. Liquidez corrente pouco informativa.",
        debt="tolerante", liquidez_relevante=False, dy="media", foco="misto",
        winsor=(0.05, 0.95), score_entrada_min=58.0, risk_penalty_max=9.0,
        endividamento_teto=1.5, liquidez_corrente_min=None,
    ),
    "UTILITY_REGULADA": Arquetipo(
        "UTILITY_REGULADA", "Utility regulada (energia, saneamento, gás)",
        "Intensiva em capital, fluxo de caixa regulado e previsível. Tolera "
        "alavancagem mais alta; prioriza dividendos e estabilidade de margem.",
        debt="tolerante", liquidez_relevante=True, dy="alta", foco="renda",
        winsor=(0.05, 0.95), score_entrada_min=58.0, risk_penalty_max=10.0,
        endividamento_teto=3.5, liquidez_corrente_min=0.8,
    ),
    "TELECOM": Arquetipo(
        "TELECOM", "Telecomunicações",
        "Intensiva em capex, fluxo recorrente. Margem operacional, dividendos e "
        "alavancagem controlada.",
        debt="tolerante", liquidez_relevante=True, dy="media", foco="renda",
        winsor=(0.05, 0.95), score_entrada_min=56.0, risk_penalty_max=10.0,
        endividamento_teto=3.0, liquidez_corrente_min=0.8,
    ),
    "COMMODITY_CICLICA": Arquetipo(
        "COMMODITY_CICLICA", "Commodity cíclica (mineração, siderurgia, papel, química)",
        "Resultados oscilam com o ciclo de preços. Avalia através do ciclo: "
        "valuation por EV/EBIT, margem operacional e disciplina de dívida; "
        "outliers mais largos.",
        debt="normal", liquidez_relevante=True, dy="media", foco="valor",
        winsor=(0.10, 0.90), score_entrada_min=55.0, risk_penalty_max=11.0,
        endividamento_teto=2.5, liquidez_corrente_min=1.0,
    ),
    "PETROLEO": Arquetipo(
        "PETROLEO", "Petróleo, gás e biocombustíveis",
        "Cíclica de commodity com forte geração de caixa. Dividendos, margem "
        "operacional e controle de alavancagem.",
        debt="normal", liquidez_relevante=True, dy="alta", foco="valor",
        winsor=(0.10, 0.90), score_entrada_min=55.0, risk_penalty_max=11.0,
        endividamento_teto=2.5, liquidez_corrente_min=1.0,
    ),
    "CONSUMO_DEFENSIVO": Arquetipo(
        "CONSUMO_DEFENSIVO", "Consumo não cíclico (alimentos, bebidas, agro)",
        "Demanda estável. Margem líquida, rentabilidade, dividendos e "
        "alavancagem moderada.",
        debt="normal", liquidez_relevante=True, dy="media", foco="misto",
        winsor=(0.05, 0.95), score_entrada_min=58.0, risk_penalty_max=10.0,
        endividamento_teto=2.0, liquidez_corrente_min=1.0,
    ),
    "VAREJO_CICLICO": Arquetipo(
        "VAREJO_CICLICO", "Varejo / consumo cíclico",
        "Sensível ao ciclo econômico e ao crédito. Crescimento e margem, com "
        "atenção rigorosa a endividamento e capital de giro (liquidez).",
        debt="rigoroso", liquidez_relevante=True, dy="baixa", foco="crescimento",
        winsor=(0.10, 0.90), score_entrada_min=58.0, risk_penalty_max=11.0,
        endividamento_teto=1.5, liquidez_corrente_min=1.2,
    ),
    "TECH_ASSET_LIGHT": Arquetipo(
        "TECH_ASSET_LIGHT", "Tecnologia / software (asset-light)",
        "Pouco capital físico, foco em crescimento e retorno sobre capital "
        "investido. Dívida deve ser baixa; dividendos não são prioridade.",
        debt="rigoroso", liquidez_relevante=True, dy="nula", foco="crescimento",
        winsor=(0.10, 0.90), score_entrada_min=58.0, risk_penalty_max=11.0,
        endividamento_teto=1.0, liquidez_corrente_min=1.2,
    ),
    "INDUSTRIAL": Arquetipo(
        "INDUSTRIAL", "Bens industriais / máquinas",
        "Capital moderado, exposta ao ciclo industrial. ROIC, margem "
        "operacional e crescimento, com dívida sob controle.",
        debt="normal", liquidez_relevante=True, dy="media", foco="misto",
        winsor=(0.07, 0.93), score_entrada_min=57.0, risk_penalty_max=10.0,
        endividamento_teto=2.0, liquidez_corrente_min=1.0,
    ),
    "CONSTRUCAO_IMOB": Arquetipo(
        "CONSTRUCAO_IMOB", "Construção civil / incorporação / imóveis",
        "Cíclica e intensiva em capital de giro, com alavancagem estrutural. "
        "Atenção rigorosa a dívida e liquidez; resultados voláteis.",
        debt="rigoroso", liquidez_relevante=True, dy="baixa", foco="valor",
        winsor=(0.10, 0.90), score_entrada_min=55.0, risk_penalty_max=12.0,
        endividamento_teto=1.8, liquidez_corrente_min=1.3,
    ),
    "SAUDE": Arquetipo(
        "SAUDE", "Saúde (hospitais, farma, equipamentos)",
        "Demanda resiliente, foco em rentabilidade do capital e crescimento, "
        "com dívida moderada.",
        debt="normal", liquidez_relevante=True, dy="baixa", foco="crescimento",
        winsor=(0.07, 0.93), score_entrada_min=58.0, risk_penalty_max=10.0,
        endividamento_teto=2.0, liquidez_corrente_min=1.0,
    ),
    "GENERICO": Arquetipo(
        "GENERICO", "Perfil genérico (multissetorial)",
        "Sem arquétipo específico identificado; usa critérios equilibrados de "
        "rentabilidade, dívida e liquidez.",
        debt="normal", liquidez_relevante=True, dy="media", foco="misto",
        winsor=(0.05, 0.95), score_entrada_min=58.0, risk_penalty_max=10.0,
        endividamento_teto=2.0, liquidez_corrente_min=1.0,
    ),
}


# Mapeamento por palavra-chave (SEGMENTO tem prioridade sobre SETOR)
_KW_SEGMENTO: list[tuple[tuple[str, ...], str]] = [
    (("banco", "bancos", "intermediári"), "BANCO"),
    (("segur", "previdência", "previdencia", "resseguro", "capitaliz"), "SEGURADORA"),
    (("bolsa", "gestão de rec", "gestao de rec", "serviços financ", "servicos financ",
      "meios de pagamento", "pagamento", "securitiz", "financeiro div"), "SERVICOS_FIN"),
    (("energia elétrica", "energia eletrica", "água", "agua", "saneamento", "gás",
      "gas "), "UTILITY_REGULADA"),
    (("telecom", "comunicaç", "comunicac"), "TELECOM"),
    (("mineraç", "mineracao", "siderurg", "metalurg", "papel", "celulose",
      "químic", "quimic", "madeira", "embalagens"), "COMMODITY_CICLICA"),
    (("petróleo", "petroleo", "petrolíf", "petrolif", "combustí", "combusti"), "PETROLEO"),
    (("aliment", "bebida", "agro", "carnes", "frigoríf", "frigorif", "açúcar",
      "acucar", "fumo", "agropecuá"), "CONSUMO_DEFENSIVO"),
    (("varejo", "comércio", "comercio", "tecidos", "vestuário", "vestuario",
      "calçados", "calcados", "utilidades dom", "eletrodom", "viagens", "lazer",
      "hotel", "turismo", "automó", "automo"), "VAREJO_CICLICO"),
    (("software", "tecnologia", "program", "computador", "internet", "ti ",
      "tecnolog"), "TECH_ASSET_LIGHT"),
    (("máquinas", "maquinas", "equipamentos", "material de transp", "aeroespacial",
      "transporte", "logíst", "logist", "serviços ind", "servicos ind",
      "construção pesada", "construcao pesada"), "INDUSTRIAL"),
    (("construção civil", "construcao civil", "incorpora", "imóv", "imov",
      "exploração de im", "exploracao de im"), "CONSTRUCAO_IMOB"),
    (("saúde", "saude", "hospital", "farmacêut", "farmaceut", "medic",
      "laboratóri", "laboratori"), "SAUDE"),
]

_KW_SETOR: list[tuple[tuple[str, ...], str]] = [
    (("financ",), "SERVICOS_FIN"),
    (("petróleo", "petroleo"), "PETROLEO"),
    (("materiais básicos", "materiais basicos"), "COMMODITY_CICLICA"),
    (("utilidade pública", "utilidade publica"), "UTILITY_REGULADA"),
    (("comunicaç", "comunicac"), "TELECOM"),
    (("consumo não cíclico", "consumo nao ciclico"), "CONSUMO_DEFENSIVO"),
    (("consumo cíclico", "consumo ciclico"), "VAREJO_CICLICO"),
    (("tecnologia",), "TECH_ASSET_LIGHT"),
    (("bens industriais", "industrial"), "INDUSTRIAL"),
    (("saúde", "saude"), "SAUDE"),
]


def classificar_arquetipo(setor: str, segmento: str = "") -> Arquetipo:
    """Classifica (setor, segmento) no arquétipo financeiro mais adequado."""
    seg = (segmento or "").strip().lower()
    if seg and seg != "todos":
        for kws, key in _KW_SEGMENTO:
            if any(kw in seg for kw in kws):
                return ARQUETIPOS[key]
    setl = (setor or "").strip().lower()
    if setl and setl != "todos":
        for kws, key in _KW_SETOR:
            if any(kw in setl for kw in kws):
                return ARQUETIPOS[key]
    return ARQUETIPOS["GENERICO"]


# ──────────────────────────────────────────────────────────────────────────
# Calibração final
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class SegmentCalibration:
    setor:        str
    segmento:     str
    arquetipo:    str
    arquetipo_label: str
    descricao:    str
    pesos:        dict[str, tuple[float, bool]]   # pronto p/ _score_universo
    pesos_pct:    dict[str, float]                # 0-100, p/ exibição
    debt_treatment: str
    liquidez_relevante: bool
    dy_relevancia: str
    valuation_foco: str
    winsor:       tuple[float, float]
    normalizacao: str
    aprovacao:    dict[str, float]
    limites_risco: dict[str, float | None]
    criterios:    dict[str, str]
    stats_segmento: dict[str, float]
    fonte:        str = "arquétipo + distribuição do segmento"


def _ajustar_pesos(
    base: dict[str, tuple[float, bool]],
    arq: Arquetipo,
) -> dict[str, tuple[float, bool]]:
    """Aplica o tratamento do arquétipo sobre os pesos-base e renormaliza.

    A base vem normalizada (frações que somam 1). Convertemos para uma escala
    de pontos (×100) para que os ajustes do arquétipo (em pontos: 22, 18, 10…)
    fiquem na mesma ordem de grandeza dos pesos-base, e renormalizamos ao final.
    """
    pesos: dict[str, float] = {k: float(v[0]) * 100.0 for k, v in base.items()}

    # Dívida
    if arq.debt == "ignorar":
        pesos.pop("Endividamento_Total", None)
    elif arq.debt == "tolerante":
        if "Endividamento_Total" in pesos:
            pesos["Endividamento_Total"] *= 0.5
    elif arq.debt == "rigoroso":
        pesos["Endividamento_Total"] = pesos.get("Endividamento_Total", 0.0) * 1.6 or 18.0

    # Liquidez corrente
    if not arq.liquidez_relevante:
        pesos.pop("Liquidez_Corrente", None)

    # Dividendos
    if arq.dy == "nula":
        pesos.pop("DY", None)
    elif arq.dy == "alta":
        pesos["DY"] = max(pesos.get("DY", 0.0), 22.0)
    elif arq.dy == "baixa":
        if "DY" in pesos:
            pesos["DY"] *= 0.5

    # Crescimento — reforça slopes para arquétipos de crescimento
    if arq.foco == "crescimento":
        for slope in ("ROE_slope_log", "ROIC_slope_log", "Margem_Liquida_slope_log"):
            pesos[slope] = max(pesos.get(slope, 0.0), 10.0)

    # Remove pesos não positivos
    pesos = {k: v for k, v in pesos.items() if v > 0}
    if not pesos:
        pesos = {k: float(v[0]) for k, v in base.items()}

    total = sum(pesos.values()) or 1.0
    return {
        k: (v / total, _dir_melhor_alto(k))
        for k, v in pesos.items()
    }


def _stats_segmento(df_seg: pd.DataFrame | None) -> dict[str, float]:
    """Estatísticas descritivas do segmento usadas no refino data-driven."""
    out: dict[str, float] = {}
    if df_seg is None or df_seg.empty:
        return out
    out["n_empresas"] = float(len(df_seg))
    for col in ("DY", "Endividamento_Total", "Liquidez_Corrente", "ROE", "ROIC"):
        if col in df_seg.columns:
            s = pd.to_numeric(df_seg[col], errors="coerce").dropna()
            if not s.empty:
                out[f"{col}_mediana"] = float(s.median())
                out[f"{col}_p90"] = float(s.quantile(0.90))
                out[f"{col}_p25"] = float(s.quantile(0.25))
    return out


def build_segment_calibration(
    setor: str,
    segmento: str,
    base_pesos: dict[str, tuple[float, bool]],
    df_segment: pd.DataFrame | None = None,
) -> SegmentCalibration:
    """
    Gera a calibração ótima automática do segmento.

    Args:
      setor:       SETOR predominante do universo filtrado.
      segmento:    SEGMENTO selecionado ("Todos" → calibra por setor).
      base_pesos:  pesos-base normalizados (saída de _get_pesos_setor).
      df_segment:  DataFrame de múltiplos do segmento (refino data-driven).

    Returns:
      SegmentCalibration pronta para uso e exibição.
    """
    arq = classificar_arquetipo(setor, segmento)
    pesos = _ajustar_pesos(base_pesos, arq)
    pesos_pct = {k: round(v[0] * 100.0, 1) for k, v in
                 sorted(pesos.items(), key=lambda x: x[1][0], reverse=True)}

    stats = _stats_segmento(df_segment)

    # Refino data-driven do teto de endividamento: respeita o arquétipo, mas não
    # fica abaixo do p90 observado no segmento (evita reprovar o segmento todo).
    endiv_teto = arq.endividamento_teto
    if endiv_teto is not None and stats.get("Endividamento_Total_p90"):
        endiv_teto = round(max(endiv_teto, stats["Endividamento_Total_p90"]), 2)

    # Referência de DY: maior entre o piso do arquétipo e a mediana do segmento.
    dy_ref = stats.get("DY_mediana")

    criterios = {
        "Qualidade": (
            "Rentabilidade do capital (ROE/ROIC) e margens são o núcleo do score; "
            f"foco de valuation: {arq.foco}."
        ),
        "Dividendos": {
            "alta":  "Dividendos têm peso alto — segmento pagador/renda.",
            "media": "Dividendos consideram peso moderado.",
            "baixa": "Dividendos têm peso reduzido — segmento de crescimento/reinvest.",
            "nula":  "Dividendos não entram no score — segmento de crescimento.",
        }[arq.dy] + (f" Mediana de DY do segmento: {dy_ref*100:.1f}%." if dy_ref else ""),
        "Crescimento": (
            "Inclinação histórica (slopes) reforçada no score."
            if arq.foco == "crescimento" else
            "Crescimento considerado de forma equilibrada."
        ),
        "Valuation": {
            "valor":        "Ênfase em valuation barato (EV/EBIT, P/VP) através do ciclo.",
            "crescimento":  "Valuation tolerante a múltiplos altos se houver crescimento.",
            "renda":        "Valuation ancorado em dividend yield e estabilidade.",
            "misto":        "Valuation equilibrado entre preço e qualidade.",
        }[arq.foco],
        "Endividamento": {
            "ignorar":   "Não avaliado — alavancagem é inerente ao negócio (financeiro).",
            "tolerante": "Tolera alavancagem mais alta (capital intensivo/regulado).",
            "normal":    "Endividamento avaliado de forma padrão.",
            "rigoroso":  "Endividamento avaliado com rigor — risco relevante no segmento.",
        }[arq.debt] + (f" Teto de risco: Dív/PL ≈ {endiv_teto}." if endiv_teto else ""),
        "Liquidez": (
            "Liquidez corrente relevante para a saúde de curto prazo."
            if arq.liquidez_relevante else
            "Liquidez corrente não se aplica a este tipo de negócio."
        ) + (f" Piso recomendado: {arq.liquidez_corrente_min}."
             if arq.liquidez_corrente_min else ""),
    }

    return SegmentCalibration(
        setor=setor or "—",
        segmento=segmento or "Todos",
        arquetipo=arq.key,
        arquetipo_label=arq.label,
        descricao=arq.descricao,
        pesos=pesos,
        pesos_pct=pesos_pct,
        debt_treatment=arq.debt,
        liquidez_relevante=arq.liquidez_relevante,
        dy_relevancia=arq.dy,
        valuation_foco=arq.foco,
        winsor=arq.winsor,
        normalizacao="Percentil intra-grupo (winsorizado)",
        aprovacao={
            "score_entrada_min": arq.score_entrada_min,
            "risk_penalty_max": arq.risk_penalty_max,
        },
        limites_risco={
            "endividamento_teto": endiv_teto,
            "liquidez_corrente_min": arq.liquidez_corrente_min,
        },
        criterios=criterios,
        stats_segmento=stats,
    )


# ──────────────────────────────────────────────────────────────────────────
# Helpers para UI
# ──────────────────────────────────────────────────────────────────────────

def pesos_rows(calib: SegmentCalibration) -> list[dict]:
    """Linhas para tabela de pesos (indicador, peso %, direção)."""
    return [
        {
            "Indicador": k,
            "Peso %": calib.pesos_pct[k],
            "Direção": "maior = melhor" if calib.pesos[k][1] else "menor = melhor",
        }
        for k in calib.pesos_pct
    ]


def criterios_rows(calib: SegmentCalibration) -> list[dict]:
    """Linhas para tabela de critérios por dimensão."""
    return [{"Dimensão": k, "Critério do segmento": v} for k, v in calib.criterios.items()]
