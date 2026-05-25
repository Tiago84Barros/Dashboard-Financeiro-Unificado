"""
core/stress_tests.py — testes de estresse históricos para portfólios.

Implementa a recomendação A4 do parecer da banca examinadora (2026-05-23):
ferramenta padrão era "historical VaR + parametric VaR com bootstrap",
mas para retail basta apresentar "se acontecer crise tipo 2008, seu
portfólio perde ~38% e leva ~22 meses para recuperar".

Cada cenário aplica choques por classe de ativo, calculados em retornos
observados nos eventos históricos:

  • Subprime 2008          — IBOV -41% (2008), USDBRL +30%, IFIX -10%
  • Joesley Day 2017       — IBOV -8.8% (1 dia), USDBRL +8%
  • Janeiro Vermelho 2015  — IBOV -13% (jan/15), USDBRL +12%, downgrade BR
  • COVID Crash 2020       — IBOV -29% (1 mês), USDBRL +25%, IFIX -22%
  • Crise CDS 2002         — IBOV -34% (1 ano), USDBRL +52%

A modelagem é simplificada (choque uniforme por classe, não modela
correlação cross-asset). Para análise rigorosa usar copulas
(Embrechts-McNeil-Straumann, 2002) — recomendação M2 do parecer.

Função pura: recebe lista de posicoes (dict) e retorna dict por cenário.
Nenhum acesso ao DB. Pode ser invocada em loop tight.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ──────────────────────────────────────────────────────────────────────────
# Cenários históricos (calibrados em dados públicos B3 / BCB)
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class StressScenario:
    """Choque histórico aplicável a um portfólio.

    Os shocks por classe são retornos esperados durante o evento
    (negativos para perdas). Tempo de recuperação é mediana histórica
    em meses até o portfólio voltar ao valor pré-choque.
    """
    nome:        str
    descricao:   str
    data_ref:    str
    shock_stock_br: float    # Ações BR (IBOV-like)
    shock_fii:      float    # FIIs (IFIX-like)
    shock_etf_intl: float    # ETF / ações exterior em USD (S&P500-like)
    shock_renda_fixa: float  # Tesouro pré/IPCA marcado a mercado
    shock_tesouro:    float  # Tesouro Selic (LFT) — menos exposto
    shock_fundo_rf:   float  # Fundos RF — exposição parcial via DI
    cambio_usd_brl:   float  # Variação USD/BRL (positivo = BRL desvaloriza)
    tempo_recuperacao_meses: int


# Cenários calibrados em dados públicos
SCENARIOS: list[StressScenario] = [
    StressScenario(
        nome="Subprime 2008",
        descricao="Crise financeira global; IBOV -41% no ano; USDBRL +30%",
        data_ref="set/2008 – fev/2009",
        shock_stock_br=-0.41, shock_fii=-0.10,
        shock_etf_intl=-0.38, shock_renda_fixa=-0.05,
        shock_tesouro=0.00, shock_fundo_rf=-0.03,
        cambio_usd_brl=+0.30, tempo_recuperacao_meses=22,
    ),
    StressScenario(
        nome="Janeiro Vermelho 2015",
        descricao="Downgrade rating BR; recessão; ajuste fiscal",
        data_ref="jan/2015 – jul/2015",
        shock_stock_br=-0.13, shock_fii=-0.08,
        shock_etf_intl=-0.03, shock_renda_fixa=-0.08,
        shock_tesouro=-0.02, shock_fundo_rf=-0.04,
        cambio_usd_brl=+0.12, tempo_recuperacao_meses=14,
    ),
    StressScenario(
        nome="Joesley Day 2017",
        descricao="Áudio Temer-JBS; IBOV cai 8.8% em um dia",
        data_ref="17/mai/2017",
        shock_stock_br=-0.09, shock_fii=-0.04,
        shock_etf_intl=0.00, shock_renda_fixa=-0.05,
        shock_tesouro=0.00, shock_fundo_rf=-0.02,
        cambio_usd_brl=+0.08, tempo_recuperacao_meses=2,
    ),
    StressScenario(
        nome="COVID Crash 2020",
        descricao="Pandemia; IBOV -29% em um mês; circuit breakers",
        data_ref="fev/2020 – mar/2020",
        shock_stock_br=-0.29, shock_fii=-0.22,
        shock_etf_intl=-0.20, shock_renda_fixa=-0.06,
        shock_tesouro=-0.01, shock_fundo_rf=-0.04,
        cambio_usd_brl=+0.25, tempo_recuperacao_meses=6,
    ),
    StressScenario(
        nome="Crise CDS 2002",
        descricao="Receio Lula presidente; risco-país explode",
        data_ref="abr/2002 – out/2002",
        shock_stock_br=-0.34, shock_fii=-0.15,
        shock_etf_intl=-0.10, shock_renda_fixa=-0.15,
        shock_tesouro=-0.05, shock_fundo_rf=-0.10,
        cambio_usd_brl=+0.52, tempo_recuperacao_meses=18,
    ),
]


# ──────────────────────────────────────────────────────────────────────────
# Aplicação de choque ao portfólio
# ──────────────────────────────────────────────────────────────────────────

# Mapa classe (do _CLASS_LABEL em core/investimentos) → atributo do shock
_CLASSE_TO_SHOCK = {
    "Ações BR":           "shock_stock_br",
    "FII":                "shock_fii",
    "ETF":                "shock_etf_intl",
    "ETF Brasil":         "shock_stock_br",
    "ETF Internacional":  "shock_etf_intl",
    "Renda Fixa":         "shock_renda_fixa",
    "Tesouro Direto":     "shock_tesouro",
    "Fundo RF":           "shock_fundo_rf",
    "BDR":                "shock_etf_intl",  # BDRs replicam ativo exterior
    "Cripto":             "shock_etf_intl",  # proxy (correlação imperfeita)
    "Outros":             "shock_stock_br",
}


def aplicar_stress(
    posicoes: list[dict],
    scenario: StressScenario,
) -> dict:
    """
    Aplica um cenário de estresse à carteira atual.

    Cada posição deve ter ao menos: classe (str), valor_mercado (float),
    moeda (str opcional, default BRL).

    Retorna dict:
      total_pre:        valor de mercado pré-choque (R$)
      total_pos:        valor estimado pós-choque (R$)
      perda_absoluta:   total_pre - total_pos
      perda_pct:        perda relativa em decimal
      por_classe:       {classe: {pre, pos, perda_pct}}
      tempo_recuperacao_meses: heurística histórica do cenário
    """
    total_pre = 0.0
    total_pos = 0.0
    por_classe: dict[str, dict] = {}

    for pos in posicoes:
        classe = str(pos.get("classe") or "Outros")
        vm_br  = float(pos.get("valor_mercado") or 0)
        moeda  = str(pos.get("moeda") or "BRL").upper()
        if vm_br <= 0:
            continue

        shock_attr = _CLASSE_TO_SHOCK.get(classe, "shock_stock_br")
        shock = float(getattr(scenario, shock_attr))

        # Ajuste cambial para ativos em USD (Nomad)
        if moeda == "USD":
            # USD valoriza durante crise (flight to safety) — ganho cambial
            # parcialmente compensa perda do ativo
            shock_efetivo = (1 + shock) * (1 + scenario.cambio_usd_brl) - 1
        else:
            shock_efetivo = shock

        vm_pos = vm_br * (1 + shock_efetivo)
        total_pre += vm_br
        total_pos += vm_pos

        agg = por_classe.setdefault(classe, {"pre": 0.0, "pos": 0.0})
        agg["pre"] += vm_br
        agg["pos"] += vm_pos

    for classe, agg in por_classe.items():
        pre = agg["pre"]
        agg["perda_pct"] = (
            (agg["pos"] - pre) / pre if pre > 0 else 0.0
        )

    return {
        "cenario":         scenario.nome,
        "descricao":       scenario.descricao,
        "data_ref":        scenario.data_ref,
        "total_pre":       round(total_pre, 2),
        "total_pos":       round(total_pos, 2),
        "perda_absoluta":  round(total_pre - total_pos, 2),
        "perda_pct":       (total_pos - total_pre) / total_pre if total_pre > 0 else 0.0,
        "por_classe":      {k: {"pre": round(v["pre"], 2),
                                "pos": round(v["pos"], 2),
                                "perda_pct": v["perda_pct"]}
                            for k, v in por_classe.items()},
        "tempo_recuperacao_meses": scenario.tempo_recuperacao_meses,
    }


def aplicar_todos_cenarios(posicoes: list[dict]) -> list[dict]:
    """Roda todos os 5 cenários históricos e retorna lista de resultados."""
    return [aplicar_stress(posicoes, s) for s in SCENARIOS]


def cenario_pior_caso(posicoes: list[dict]) -> dict:
    """Retorna o cenário com maior perda percentual (worst-case stress)."""
    resultados = aplicar_todos_cenarios(posicoes)
    return min(resultados, key=lambda r: r["perda_pct"]) if resultados else {}
