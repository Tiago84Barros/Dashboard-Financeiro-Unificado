"""Regressão do motor institucional de Criação de Portfólio dos EUA."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.us_portfolio_creation import (
    USPortfolioCreationParams,
    build_industry_audit,
    build_portfolio_creation,
    prepare_eligible_universe,
)


def _universe(groups: int = 9, names_per_group: int = 6) -> pd.DataFrame:
    """Universo sintético com a negociabilidade MEDIDA.

    A coluna `giro_diario_usd` não é decoração: desde us-liquidity-2.0.0 um
    universo sem medição de giro não produz carteira com piso ligado (é o
    achado A-004). Sem ela, todo teste de peso/limite aqui passaria a exercitar
    o caminho de bloqueio em vez do otimizador.
    """
    sectors = ["Technology", "Healthcare", "Industrials"]
    rows = []
    for group in range(groups):
        for rank in range(names_per_group):
            strength = 88 - group * 1.5 - rank * 5
            rows.append({
                "symbol": f"U{group:02d}{rank}",
                "name": f"Empresa {group}-{rank}",
                "sector": sectors[group % len(sectors)],
                "industry": f"Industry {group}",
                "exchange": "NASDAQ" if group % 2 else "NYSE",
                "security_type": "common",
                "is_active": True,
                "is_reit": False,
                "score": strength,
                "score_quality": strength,
                "score_growth": strength - 2,
                "score_solidity": strength - 1,
                "score_capital_efficiency": strength + 1,
                "score_valuation": strength - 3,
                "score_shareholder": strength - 4,
                "coverage": 88.0,
                "_years": 10,
                "_market_cap": 4_000_000_000 + group * 10_000_000,
                "roic": .12,
                "roe": .18,
                "fcf_yield": .06,
                "cash_conversion": 1.0,
                "current_ratio": 1.5,
                "net_margin": .15,
                "interest_coverage": 8.0,
                "giro_diario_usd": 5_000_000.0,
                "giro_diario_usd_at": pd.Timestamp.now(tz="UTC"),
            })
    return pd.DataFrame(rows)


def test_filtros_sequenciais_reconciliam_universo():
    frame = _universe(groups=2, names_per_group=5)
    frame.loc[0, "is_active"] = False
    frame.loc[1, "exchange"] = "OTC"
    frame.loc[2, "_market_cap"] = np.nan
    frame.loc[3, "coverage"] = 10
    frame.loc[4, "_years"] = 1
    params = USPortfolioCreationParams(
        min_market_cap=1_000_000_000, min_coverage=50, min_years=5,
        min_fundamental_score=40,
    )
    eligible, audit = prepare_eligible_universe(frame, params)
    assert len(eligible) == 5
    assert int(audit["count"].sum()) + len(eligible) == len(frame)
    assert set(eligible["exchange"]) <= {"NYSE", "NASDAQ"}


def test_auditoria_aprova_industrias_com_lideres_fortes():
    params = USPortfolioCreationParams(
        min_companies_per_industry=4, min_entry_score=55, min_score_edge=0,
        min_market_cap=1_000_000_000,
    )
    eligible, _ = prepare_eligible_universe(_universe(), params)
    audit = build_industry_audit(eligible, params)
    assert not audit.empty
    assert (audit["company_count"] == 6).all()
    assert audit["status"].eq("Aprovada").any()
    assert audit["history_periods"].eq(0).all()


def test_historico_pit_e_exigido_sem_inventar_fallback():
    params = USPortfolioCreationParams(
        min_companies_per_industry=4, min_entry_score=50, min_score_edge=0,
        require_historical_signal=True, min_history_periods=2,
    )
    eligible, _ = prepare_eligible_universe(_universe(groups=2), params)
    audit = build_industry_audit(eligible, params, pd.DataFrame())
    assert audit["status"].eq("Excluída").all()
    assert audit["reason"].eq("Histórico PIT indisponível").all()


def test_carteira_respeita_limites_e_soma_um():
    params = USPortfolioCreationParams(
        top_n=15, leaders_per_industry=2, min_companies_per_industry=4,
        min_entry_score=50, min_score_edge=0, max_weight=.10,
        max_industry_weight=.18, max_sector_weight=.40,
        min_market_cap=1_000_000_000,
    )
    result = build_portfolio_creation(_universe(), params)
    holdings = result["holdings"]
    assert result["ok"] is True
    assert len(holdings) == 15
    assert holdings["weight"].sum() == pytest.approx(1.0, abs=1e-8)
    assert holdings["weight"].max() <= .10 + 1e-7
    assert holdings.groupby("industry_group")["weight"].sum().max() <= .18 + 1e-7
    assert holdings.groupby("sector_group")["weight"].sum().max() <= .40 + 1e-7
    assert result["metrics"]["effective_assets"] > 10


def test_macro_tilt_is_bounded_and_fundamental_mode_is_backward_compatible():
    params = USPortfolioCreationParams(
        top_n=15, leaders_per_industry=2, min_companies_per_industry=4,
        min_entry_score=50, min_score_edge=0, max_weight=.10,
        max_industry_weight=.18, max_sector_weight=.40,
    )
    baseline = build_portfolio_creation(_universe(), params)
    impacts = {
        symbol: (100.0 if index % 2 else -100.0)
        for index, symbol in enumerate(baseline["holdings"]["symbol"])
    }
    fundamental = build_portfolio_creation(
        _universe(), params, macro_impacts=impacts, macro_mode="fundamental")
    contextual = build_portfolio_creation(
        _universe(), params, macro_impacts=impacts, macro_mode="moderate")

    assert fundamental["holdings"]["weight"].tolist() == pytest.approx(
        baseline["holdings"]["weight"].tolist())
    assert contextual["holdings"]["weight"].sum() == pytest.approx(1.0)
    assert contextual["macro"]["turnover"] <= .10 + 1e-9
    assert contextual["holdings"]["macro_score_adjustment"].abs().max() <= 10
    assert contextual["holdings"]["weight"].max() <= .10 + 1e-7
    assert (
        contextual["holdings"].groupby("industry_group")["weight"].sum().max()
        <= .18 + 1e-7
    )
    assert (
        contextual["holdings"].groupby("sector_group")["weight"].sum().max()
        <= .40 + 1e-7
    )


def test_cap_adaptativo_documenta_ajuste_matematico():
    params = USPortfolioCreationParams(
        top_n=10, leaders_per_industry=2, min_companies_per_industry=4,
        min_entry_score=50, min_score_edge=0, max_weight=.05,
        max_industry_weight=.10, max_sector_weight=.20,
        adaptive_caps=True,
    )
    result = build_portfolio_creation(_universe(), params)
    assert result["ok"] is True
    assert any("ajustado" in warning for warning in result["warnings"])
    assert result["holdings"]["weight"].sum() == pytest.approx(1.0, abs=1e-8)


def test_coluna_de_giro_ausente_bloqueia_a_carteira_em_vez_de_aprovar_todos():
    """Achado A-004: `"giro_diario_usd" in work` pulava o gate inteiro.

    Com a coluna ausente e piso > 0 o motor não filtrava ninguém — o usuário
    pedia US$ 1 mi/dia de negociabilidade e recebia uma carteira em que nenhuma
    posição tinha sido verificada. Ausência de coluna significa "ninguém foi
    verificado", e nesse estado não se publica carteira.
    """
    universo = _universe().drop(columns=["giro_diario_usd"])
    params = USPortfolioCreationParams(min_entry_score=50, min_score_edge=0)
    result = build_portfolio_creation(universo, params)

    assert result["ok"] is False
    assert result["blocked"] is True
    assert result["holdings"].empty
    assert "giro_diario_usd" in result["blocking_error"]
    # A mensagem precisa dizer O QUE INGERIR, senão é só um beco sem saída.
    assert "run_us_ingest.py" in result["blocking_error"]
    assert len(result["liquidity_unverified"]) == len(universo)


def test_giro_ausente_com_piso_zerado_segue_em_modo_exploratorio():
    """Zerar o piso é a decisão explícita do usuário de explorar sem validar."""
    universo = _universe().drop(columns=["giro_diario_usd"])
    params = USPortfolioCreationParams(
        min_entry_score=50, min_score_edge=0, min_daily_turnover_usd=0.0)
    result = build_portfolio_creation(universo, params)

    assert result["blocking_error"] is None
    assert not result["holdings"].empty
    assert any("exploratório" in w for w in result["warnings"])


def test_modo_exploratorio_permite_analise_mas_nao_publicacao_sem_liquidez():
    """A-004/R2: o piso zero não autoriza avaliação nem persistência.

    O resultado continua trazendo holdings para investigação, mas deve declarar
    explicitamente que não é publicável quando alguma posição não tem uma
    medição de liquidez válida. A view usa este contrato para desabilitar os
    dois canais que tornam a carteira efetiva.
    """
    universo = _universe().drop(columns=["giro_diario_usd"])
    result = build_portfolio_creation(
        universo,
        USPortfolioCreationParams(
            min_entry_score=50, min_score_edge=0, min_daily_turnover_usd=0.0),
    )

    assert not result["holdings"].empty
    assert result["blocked"] is False
    assert result["can_publish"] is False
    assert "não verificada" in result["publication_blocking_error"]


def test_documentacao_registra_a_regra_de_liquidez_em_vigor():
    """Evita documentação 2.0.0 após a regra UTC/7 dias da versão 2.1.0."""
    from core.us_liquidity import VERSION

    root = Path(__file__).resolve().parents[1]
    docs = (root / "docs" / "empresas_americanas.md").read_text(encoding="utf-8")

    assert VERSION in docs
    for regra in ("UTC", "7 dias", "inclusivo", "futuro"):
        assert regra in docs


def test_carteira_com_piso_ligado_nunca_contem_simbolo_nao_verificado():
    """Metade do universo perde a medição; nenhuma delas pode chegar à carteira."""
    universo = _universe()
    sem_medida = universo["symbol"].iloc[::2].tolist()
    universo.loc[universo["symbol"].isin(sem_medida), "giro_diario_usd"] = np.nan
    params = USPortfolioCreationParams(
        top_n=15, leaders_per_industry=2, min_companies_per_industry=2,
        min_entry_score=50, min_score_edge=0, max_weight=.10,
        max_industry_weight=.18, max_sector_weight=.40)
    result = build_portfolio_creation(universo, params)

    assert not result["holdings"].empty
    assert not set(result["holdings"]["symbol"]) & set(sem_medida)
    assert set(result["liquidity_unverified"]) == set(sem_medida)
    assert result["blocking_error"] is None


def test_giro_infinito_nao_vira_o_ativo_mais_liquido_do_mercado():
    """`inf >= piso` é True: sem tratamento, lixo entraria como aprovado."""
    universo = _universe()
    universo.loc[universo.index[:6], "giro_diario_usd"] = np.inf
    params = USPortfolioCreationParams(
        top_n=15, leaders_per_industry=2, min_companies_per_industry=2,
        min_entry_score=50, min_score_edge=0, max_weight=.10,
        max_industry_weight=.18, max_sector_weight=.40)
    result = build_portfolio_creation(universo, params)
    infinitos = set(universo["symbol"].iloc[:6])

    assert not set(result["holdings"]["symbol"]) & infinitos
    assert infinitos <= set(result["liquidity_unverified"])


@pytest.mark.parametrize("timestamp", [
    pd.Timestamp.now(tz="UTC"),
    pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=8),
    pd.NaT,
    "timestamp inválido",
])
def test_criacao_exige_timestamp_atual_para_giro_medido(timestamp):
    """Atual permite a carteira; stale, ausente e inválido bloqueiam sem rede."""
    universo = _universe()
    universo["giro_diario_usd_at"] = timestamp
    result = build_portfolio_creation(
        universo, USPortfolioCreationParams(min_entry_score=50, min_score_edge=0))

    if isinstance(timestamp, pd.Timestamp) and timestamp >= pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=1):
        assert result["blocked"] is False
        assert not result["holdings"].empty
    else:
        assert result["blocked"] is True
        assert result["holdings"].empty
        assert len(result["liquidity_unverified"]) == len(universo)


def test_params_registram_a_versao_da_metodologia_de_liquidez():
    """Carteira salva precisa dizer sob qual regra de elegibilidade nasceu."""
    from core.us_liquidity import VERSION as LIQ
    from core.us_portfolio_creation import params_to_dict

    params = params_to_dict(USPortfolioCreationParams())
    assert params["liquidity_version"] == LIQ
    assert params["schema_version"] == "us_portfolio_creation_v3"


@pytest.mark.parametrize("piso_invalido", [float("nan"), -1.0,
                                            float("inf"), float("-inf")])
def test_piso_de_giro_invalido_bloqueia_o_motor_sem_excecao(piso_invalido):
    """Entrada inválida não pode virar exploração nem chegar à publicação."""
    result = build_portfolio_creation(
        _universe(),
        USPortfolioCreationParams(
            min_daily_turnover_usd=piso_invalido,
            min_entry_score=50,
            min_score_edge=0,
        ),
    )

    assert result["ok"] is False
    assert result["blocked"] is True
    assert result["can_publish"] is False
    assert result["holdings"].empty
    assert result["blocking_error"] == (
        "Piso de negociabilidade inválido: informe um valor finito maior ou igual a zero."
    )


def test_interface_expoe_paridade_e_equivalentes_americanos():
    root = Path(__file__).resolve().parents[1]
    source = (root / "views" / "empresas_americanas.py").read_text(encoding="utf-8")
    for token in (
        "Etapa 2 de 3 · Aplicação em escala",
        "Auditoria por Indústria",
        "Treasury 3 meses",
        "S&P 500 (SPY)",
        "Peso máximo por indústria",
        "Usar na Avaliação de Portfólio",
        "Metodologia e equivalências B3 × Estados Unidos",
        "can_publish",
        "disabled=not can_publish",
    ):
        assert token in source
