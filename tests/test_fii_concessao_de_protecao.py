"""A proteção cede o mínimo para a carteira existir, e cede visivelmente.

A regra permanente do usuário é que nenhuma criação de portfólio termine
zerada. Os portões de proteção acrescentados à elegibilidade zeraram a carteira
real, e a resposta não pode ser afrouxar o portão em silêncio: o universo
estrito continua o mesmo, e a readmissão é nomeada ticker a ticker.
"""
from __future__ import annotations

import pytest

from core.fii_carteira_protegida import montar_carteira_com_concessao
from core.fii_integrated_model import (
    ColunasDeElegibilidadeAusentes,
    IntegratedEligibilityPolicy,
    apply_integrated_eligibility,
)
from core.fii_portfolio_v4 import PortfolioPolicy

_BASE = {
    "tipo": "tijolo", "liquidez_diaria": 5e6, "pvp": .95,
    "history_months": 60, "max_drawdown": -.20,
    "dy_12m": .12, "income_recurrence": .90,
    "tenant_concentration": .10, "lease_expiry_concentration_24m": .10,
}


def _linha(ticker: str, **mudancas) -> dict:
    return {**_BASE, "ticker": ticker, **mudancas}


def _otimizador_que_exige(minimo: int):
    """Otimizador de teste: só fecha carteira com ``minimo`` candidatos.

    Isola a orquestração do solver real — o que está sob teste é quantos
    candidatos a concessão readmite, e se isso aparece nas notas.
    """
    chamadas: list[int] = []

    def otimizar(rows, scenario, *, policy, **kwargs):
        rows = list(rows)
        chamadas.append(len(rows))
        if len(rows) < minimo:
            return {"items": [], "status": "blocked",
                    "blockers": ["pré-seleção inviável"]}
        peso = 1.0 / policy.max_assets
        return {
            "items": [{**row, "weight": peso} for row in rows[:policy.max_assets]],
            "status": "diligence_only", "viability_notes": [],
        }

    otimizar.chamadas = chamadas
    return otimizar


# ── Parte 1: a concessão existe, é mínima e é visível ────────────────────────

def test_relatorio_oferece_so_quem_reprovou_apenas_na_protecao():
    linhas = [
        _linha("OK0011"),
        _linha("REND11", income_recurrence=.20),          # proteção
        _linha("LOCA11", tenant_concentration=.90),       # proteção
        _linha("LIQU11", liquidez_diaria=1_000.0,         # duro + proteção
               income_recurrence=.20),
        _linha("PVPP11", pvp=.10),                        # duro
    ]
    eligible, relatorio = apply_integrated_eligibility(
        linhas, IntegratedEligibilityPolicy())

    assert [row["ticker"] for row in eligible] == ["OK0011"]
    assert relatorio["eligible_count"] == 1, "o conjunto estrito não pode mudar"
    oferecidos = [row["ticker"] for row in relatorio["concession_candidates"]]
    assert oferecidos == ["REND11", "LOCA11"]
    assert relatorio["concession_count"] == 2


def test_reprovado_em_portao_duro_nunca_e_readmitido():
    linhas = [_linha(f"EST{i:02d}11") for i in range(4)] + [
        _linha("DURO11", liquidez_diaria=1_000.0, income_recurrence=.20),
        _linha("PROT11", income_recurrence=.20),
    ]
    eligible, relatorio = apply_integrated_eligibility(
        linhas, IntegratedEligibilityPolicy())
    # Exige mais uma linha do que o estrito tem: a única readmissão
    # possível é a de proteção, jamais a que reprovou em liquidez.
    otimizador = _otimizador_que_exige(len(eligible) + 1)

    resultado = montar_carteira_com_concessao(
        eligible, relatorio["concession_candidates"], object(),
        policy=PortfolioPolicy(max_assets=4), optimizer=otimizador,
    )

    readmitidos = resultado["concessao_de_elegibilidade"]["readmitidos"]
    assert "DURO11" not in readmitidos
    assert "DURO11" not in str(resultado["viability_notes"])
    assert readmitidos == ["PROT11"]


def test_universo_estrito_viavel_nao_usa_concessao():
    linhas = [_linha(f"EST{i:02d}11") for i in range(4)] + [
        _linha("PROT11", income_recurrence=.20)]
    eligible, relatorio = apply_integrated_eligibility(
        linhas, IntegratedEligibilityPolicy())
    otimizador = _otimizador_que_exige(4)

    resultado = montar_carteira_com_concessao(
        eligible, relatorio["concession_candidates"], object(),
        policy=PortfolioPolicy(max_assets=4), optimizer=otimizador,
    )

    assert len(resultado["items"]) == 4
    assert resultado["concessao_de_elegibilidade"]["usada"] is False
    assert resultado["protecao_cedida_na_elegibilidade"] == []
    assert not any("readmitidos" in nota for nota in resultado["viability_notes"])
    assert otimizador.chamadas == [4], "o estrito viável não tenta mais nada"


def test_universo_estrito_inviavel_sai_com_carteira_cheia_e_notas_nomeadas():
    linhas = [_linha(f"EST{i:02d}11") for i in range(3)] + [
        _linha("AUSE11", income_recurrence=None),         # severidade 4
        _linha("LOCA11", tenant_concentration=.90),       # severidade 3
        _linha("REND11", income_recurrence=.20),          # severidade 1
    ]
    eligible, relatorio = apply_integrated_eligibility(
        linhas, IntegratedEligibilityPolicy())
    assert len(eligible) == 3
    otimizador = _otimizador_que_exige(5)

    resultado = montar_carteira_com_concessao(
        eligible, relatorio["concession_candidates"], object(),
        policy=PortfolioPolicy(max_assets=5), optimizer=otimizador,
    )

    assert len(resultado["items"]) == 5
    # Ordem crescente de severidade: o piso de renda antes da concentração.
    assert resultado["concessao_de_elegibilidade"]["readmitidos"] == [
        "REND11", "LOCA11"]
    nota = " ".join(resultado["viability_notes"])
    assert "REND11 — renda recorrente abaixo do mínimo" in nota
    assert "LOCA11 — concentração de locatário acima do teto" in nota
    assert "não é ausência de risco" in nota
    detalhe = {item["ticker"]: item for item in
               resultado["protecao_cedida_na_elegibilidade"]}
    assert detalhe["REND11"]["motivos"] == ["renda recorrente abaixo do mínimo"]
    assert detalhe["LOCA11"]["na_carteira"] is True


def test_a_concessao_e_minima():
    """Com folga para readmitir 1, não readmite 2."""
    linhas = [_linha(f"EST{i:02d}11") for i in range(3)] + [
        _linha("REND11", income_recurrence=.20),
        _linha("LOCA11", tenant_concentration=.90),
        _linha("AUSE11", income_recurrence=None),
    ]
    eligible, relatorio = apply_integrated_eligibility(
        linhas, IntegratedEligibilityPolicy())
    otimizador = _otimizador_que_exige(4)

    resultado = montar_carteira_com_concessao(
        eligible, relatorio["concession_candidates"], object(),
        policy=PortfolioPolicy(max_assets=4), optimizer=otimizador,
    )

    assert resultado["concessao_de_elegibilidade"]["readmitidos"] == ["REND11"]
    assert resultado["concessao_de_elegibilidade"]["disponiveis"] == 3
    assert otimizador.chamadas == [3, 4]


def test_nem_a_concessao_inteira_viabiliza_mas_a_carteira_nao_volta_vazia():
    linhas = [_linha(f"EST{i:02d}11") for i in range(3)] + [
        _linha("REND11", income_recurrence=.20)]
    eligible, relatorio = apply_integrated_eligibility(
        linhas, IntegratedEligibilityPolicy())
    # Exige 4 linhas para montar, mas a carteira cheia pediria 6 ativos.
    otimizador = _otimizador_que_exige(4)

    resultado = montar_carteira_com_concessao(
        eligible, relatorio["concession_candidates"], object(),
        policy=PortfolioPolicy(max_assets=6), optimizer=otimizador,
    )

    assert len(resultado["items"]) == 4
    assert resultado["concessao_de_elegibilidade"]["readmitidos"] == ["REND11"]


def test_pontuacao_da_tentativa_estrita_nao_ve_os_readmitidos():
    """Score é relativo ao universo: readmitir repontua a tentativa, só ela."""
    linhas = [_linha(f"EST{i:02d}11") for i in range(3)] + [
        _linha("REND11", income_recurrence=.20)]
    eligible, relatorio = apply_integrated_eligibility(
        linhas, IntegratedEligibilityPolicy())
    universos: list[list[str]] = []

    def pontuar(rows):
        universos.append([str(row["ticker"]) for row in rows])
        return rows

    montar_carteira_com_concessao(
        eligible, relatorio["concession_candidates"], object(),
        policy=PortfolioPolicy(max_assets=4),
        score=pontuar, optimizer=_otimizador_que_exige(4),
    )

    assert universos[0] == ["EST0011", "EST0111", "EST0211"]
    assert universos[1][-1] == "REND11"


# ── Parte 2: leitura falha não pode virar "métrica ausente" ──────────────────

def test_quadro_sem_a_coluna_da_politica_recusa_classificar():
    linhas = [{k: v for k, v in _linha(f"AAA{i:02d}11").items()
               if k != "income_recurrence"} for i in range(20)]

    with pytest.raises(ColunasDeElegibilidadeAusentes) as erro:
        apply_integrated_eligibility(linhas, IntegratedEligibilityPolicy())

    assert erro.value.missing_columns == ("income_recurrence",)


def test_valor_ausente_na_coluna_presente_segue_sendo_reprovacao():
    """Ausência de VALOR continua reprovando; ausência de COLUNA é erro."""
    eligible, relatorio = apply_integrated_eligibility(
        [_linha("AAAA11", income_recurrence=None)], IntegratedEligibilityPolicy())

    assert eligible == []
    assert relatorio["exclusion_counts"]["renda recorrente ausente"] == 1
