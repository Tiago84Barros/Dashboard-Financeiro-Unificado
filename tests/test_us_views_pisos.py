"""Os pisos EUA ligados nas telas — renderização real, não só o motor puro.

Todos os defeitos de interface desta sessão (card_metrica com float,
PortfolioHealth posicional, filtro lendo coluna inexistente) passaram pelos
testes de lógica e só apareceram na tela. Estes testes exercitam o caminho de
renderização com Streamlit de verdade, sem banco.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def _universo(n: int = 24) -> pd.DataFrame:
    """Cross-section mínimo com as colunas que o motor e as telas exigem."""
    metade = n // 2
    return pd.DataFrame({
        "symbol": [f"S{i:02d}" for i in range(n)],
        "name": [f"Empresa {i}" for i in range(n)],
        "sector": ["Tecnologia"] * n,
        "industry": ["Software"] * metade + ["Semicondutores"] * (n - metade),
        "exchange": ["NASDAQ"] * n,
        "is_active": [True] * n,
        "score": np.r_[np.linspace(70, 88, metade), np.linspace(46, 60, n - metade)],
        "coverage": [90.0] * n,
        "_market_cap": [5e9] * n,
        "_years": [12] * n,
        "roe": np.linspace(.08, .30, n),
        "net_margin": np.linspace(.03, .22, n),
        "giro_diario_usd": np.r_[np.full(metade, 5e7), np.full(n - metade, 2e5)],
        "crise_razao": np.r_[np.full(metade, 0.95), np.full(n - metade, 0.25)],
        "crise_margem_normal": [0.18] * n,
        "crise_margem_crise": np.r_[np.full(metade, 0.171),
                                    np.full(n - metade, 0.045)],
        "crise_anos_2008": [3] * n,
        "crise_anos_covid": [3] * n,
    })


def _params_frouxos(cls, **kw):
    """Parâmetros com os cortes RELATIVOS desligados.

    ``build_entry_scores`` normaliza contra o próprio recorte: num fixture
    sintético, sem as métricas que alimentam as seis trilhas, todo mundo sai com
    entry_score 50 e vantagem 0 — e nenhuma indústria é aprovada. Afrouxar aqui
    é o que permite o teste chegar até o piso, que é o objeto sob teste.
    """
    return cls(**{"min_entry_score": 0.0, "min_score_edge": 0.0, **kw})


# ── Motor ────────────────────────────────────────────────────────────────────

def test_piso_de_giro_remove_quem_nao_negocia():
    """Valor de mercado alto não é negociabilidade — metade do universo tem
    US$ 5 bi e gira US$ 200 mil/dia."""
    from core.us_portfolio_creation import (
        USPortfolioCreationParams, prepare_eligible_universe,
    )
    u = _universo()
    sem, _ = prepare_eligible_universe(
        u, USPortfolioCreationParams(min_daily_turnover_usd=0.0))
    com, exc = prepare_eligible_universe(u, USPortfolioCreationParams())

    assert len(com) == len(sem) - 12
    assert exc.loc[exc["key"] == "liquidity", "count"].iloc[0] == 12
    assert exc.attrs["liquidity_warnings"]


def test_sem_serie_de_volume_nao_e_removida():
    """Ausência de medição não é prova de iliquidez — é o erro da faixa de
    validação, que gravava NULL e depois lia o NULL como falha."""
    from core.us_portfolio_creation import (
        USPortfolioCreationParams, prepare_eligible_universe,
    )
    u = _universo()
    u["giro_diario_usd"] = np.nan
    elegiveis, exc = prepare_eligible_universe(u, USPortfolioCreationParams())

    assert len(elegiveis) == len(u)
    assert exc.loc[exc["key"] == "liquidity", "count"].iloc[0] == 0
    assert any("não foram verificadas" in a
               for a in exc.attrs["liquidity_warnings"])


def test_universo_sem_a_coluna_de_giro_nao_quebra():
    """A vitrine publicada antes desta mudança não tem a coluna."""
    from core.us_portfolio_creation import (
        USPortfolioCreationParams, build_portfolio_creation,
    )
    u = _universo().drop(columns=["giro_diario_usd"])
    r = build_portfolio_creation(u, _params_frouxos(USPortfolioCreationParams))
    assert not r["holdings"].empty


def test_piso_de_qualidade_substitui_dentro_da_industria():
    """A vaga da indústria é preservada: exigir qualidade não custa
    diversificação. No universo real de 03/08/2026 este caminho não dispara —
    as 21 indústrias com líder 'Excluída' são 100% 'Excluída' —, então a
    substituição precisa de fixture para ser exercitada de verdade."""
    from core.us_portfolio_creation import (
        USPortfolioCreationParams, prepare_eligible_universe,
        build_industry_audit, select_industry_leaders,
    )
    p = _params_frouxos(USPortfolioCreationParams,
                        min_daily_turnover_usd=0.0, leaders_per_industry=1)
    elegiveis, _ = prepare_eligible_universe(_universo(), p)

    # Marca o líder de cada indústria como reprovado; o segundo é limpo.
    for _, g in elegiveis.groupby("industry_group"):
        # MESMO desempate do motor: com entry_score empatado, ordenar só por
        # ele marca outra empresa e o teste passa a exercitar o caso errado.
        topo = g.sort_values(["entry_score", "fundamental_score"],
                             ascending=False).index[0]
        elegiveis.loc[topo, "entry_status"] = "Excluída"
        elegiveis.loc[topo, "risk_driver"] = "margem líquida negativa"

    audit = build_industry_audit(elegiveis, p)
    escolhidos = select_industry_leaders(elegiveis, audit, p)
    log = escolhidos.attrs["quality_floor_log"]

    reprovados = {d["symbol"] for d in log["reprovados"]}
    assert reprovados, "o piso não reprovou ninguém — fixture ou gate quebrado"
    assert log["substituicoes"], "reprovou mas não substituiu: a vaga sumiu"
    assert not reprovados & set(escolhidos["symbol"])
    # A indústria continua representada — é o ponto da substituição.
    assert escolhidos["industry_group"].nunique() == \
        elegiveis["industry_group"].nunique()


def test_piso_desligado_deixa_a_reprovada_entrar():
    """O toggle precisa mudar o resultado, senão é decoração."""
    from core.us_portfolio_creation import (
        USPortfolioCreationParams, prepare_eligible_universe,
        build_industry_audit, select_industry_leaders,
    )
    base = dict(min_daily_turnover_usd=0.0, leaders_per_industry=1)
    elegiveis, _ = prepare_eligible_universe(
        _universo(), _params_frouxos(USPortfolioCreationParams, **base))
    for _, g in elegiveis.groupby("industry_group"):
        # MESMO desempate do motor: com entry_score empatado, ordenar só por
        # ele marca outra empresa e o teste passa a exercitar o caso errado.
        topo = g.sort_values(["entry_score", "fundamental_score"],
                             ascending=False).index[0]
        elegiveis.loc[topo, "entry_status"] = "Excluída"

    p_off = _params_frouxos(USPortfolioCreationParams,
                            apply_quality_floor=False, **base)
    audit = build_industry_audit(elegiveis, p_off)
    livres = set(select_industry_leaders(elegiveis, audit, p_off)["symbol"])

    p_on = _params_frouxos(USPortfolioCreationParams, **base)
    barrados = set(select_industry_leaders(
        elegiveis, build_industry_audit(elegiveis, p_on), p_on)["symbol"])
    assert livres != barrados


# ── Renderização ─────────────────────────────────────────────────────────────

def _sem_streamlit_runtime():
    try:
        from streamlit.testing.v1 import AppTest       # noqa: F401
        return False
    except Exception:
        return True


@pytest.mark.skipif(_sem_streamlit_runtime(), reason="streamlit.testing indisponível")
def test_ciclo_renderiza_sem_erro(tmp_path):
    """Percentuais, cards e column_config passam pelo Streamlit de verdade."""
    from streamlit.testing.v1 import AppTest

    dados = tmp_path / "u.json"
    dados.write_text(_universo().to_json(orient="records"), encoding="utf-8")
    script = tmp_path / "app.py"
    script.write_text(
        "import pandas as pd\n"
        f"u = pd.read_json(r'{dados}')\n"
        "from views.empresas_americanas import _render_us_piso_e_ciclo\n"
        "_render_us_piso_e_ciclo(u)\n", encoding="utf-8")

    at = AppTest.from_file(str(script), default_timeout=60).run()
    assert not at.exception, at.exception
    texto = " ".join(c.value for c in at.caption)
    assert "2008" in texto


@pytest.mark.skipif(_sem_streamlit_runtime(), reason="streamlit.testing indisponível")
def test_ciclo_sem_dado_declara_em_vez_de_sumir(tmp_path):
    """A vitrine publicada hoje não traz as colunas; a seção precisa dizer
    isso em vez de renderizar uma tabela vazia que parece 'tudo certo'."""
    from streamlit.testing.v1 import AppTest

    u = _universo().drop(columns=["crise_razao", "crise_anos_2008",
                                  "crise_anos_covid"])
    dados = tmp_path / "u.json"
    dados.write_text(u.to_json(orient="records"), encoding="utf-8")
    script = tmp_path / "app.py"
    script.write_text(
        "import pandas as pd\n"
        f"u = pd.read_json(r'{dados}')\n"
        "from views.empresas_americanas import _render_us_ciclo\n"
        "_render_us_ciclo(u)\n", encoding="utf-8")

    at = AppTest.from_file(str(script), default_timeout=60).run()
    assert not at.exception, at.exception
    assert any("Sem série anual suficiente" in c.value for c in at.caption)


def test_o_piso_de_qualidade_nao_reordena_por_conta_propria():
    """Trava do escopo: o piso REMOVE e substitui, nunca promove.

    Se um dia ele passar a mexer na ordem, a carteira mudaria por um motivo que
    a auditoria por indústria não explica — e o usuário veria um líder que não é
    o de maior score sem nenhuma linha dizendo por quê.
    """
    from core.us_portfolio_creation import (
        USPortfolioCreationParams, prepare_eligible_universe,
        build_industry_audit, select_industry_leaders,
    )
    p = _params_frouxos(USPortfolioCreationParams, min_daily_turnover_usd=0.0,
                        leaders_per_industry=2)
    elegiveis, _ = prepare_eligible_universe(_universo(), p)
    audit = build_industry_audit(elegiveis, p)

    com = select_industry_leaders(elegiveis, audit, p)
    sem = select_industry_leaders(
        elegiveis, audit,
        _params_frouxos(USPortfolioCreationParams, min_daily_turnover_usd=0.0,
                        leaders_per_industry=2, apply_quality_floor=False))
    # Sem nenhuma reprovação, ligar o piso não pode alterar nada.
    assert com["symbol"].tolist() == sem["symbol"].tolist()
    assert not (com.attrs["quality_floor_log"].get("reprovados") or [])
