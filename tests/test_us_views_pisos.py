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
        "giro_diario_usd_at": [pd.Timestamp.now(tz="UTC")] * n,
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
        USPortfolioCreationParams,
        prepare_eligible_universe,
    )
    u = _universo()
    sem, _ = prepare_eligible_universe(
        u, USPortfolioCreationParams(min_daily_turnover_usd=0.0))
    com, exc = prepare_eligible_universe(u, USPortfolioCreationParams())

    assert len(com) == len(sem) - 12
    assert exc.loc[exc["key"] == "liquidity", "count"].iloc[0] == 12
    assert exc.attrs["liquidity_warnings"]


def test_sem_serie_de_volume_sai_do_universo_de_carteira_com_piso_ligado():
    """INVERTE `test_sem_serie_de_volume_nao_e_removida` (achado A-004).

    O teste antigo exigia `len(elegiveis) == len(u)` com o piso ligado: o
    universo inteiro sem medição de giro seguia elegível. A premissa ("ausência
    de medição não é prova de iliquidez") vale para explorar o universo e não
    para montar carteira — não medir a negociabilidade de um papel que se vai
    comprar é risco não verificado, não é neutralidade.

    A ausência continua ausência: elas não são contadas como reprovadas, vão
    para uma linha de exclusão própria e voltam quando o piso é zerado.
    """
    from core.us_portfolio_creation import (
        USPortfolioCreationParams,
        prepare_eligible_universe,
    )
    u = _universo()
    u["giro_diario_usd"] = np.nan
    elegiveis, exc = prepare_eligible_universe(u, USPortfolioCreationParams())

    assert elegiveis.empty
    assert exc.loc[exc["key"] == "liquidity", "count"].iloc[0] == 0
    assert exc.loc[exc["key"] == "liquidity_unverified", "count"].iloc[0] == len(u)
    assert len(exc.attrs["liquidity_unverified"]) == len(u)
    assert any("não verificada" in a for a in exc.attrs["liquidity_warnings"])

    livre, exc_livre = prepare_eligible_universe(
        u, USPortfolioCreationParams(min_daily_turnover_usd=0.0))
    assert len(livre) == len(u)
    assert any("exploratório" in a for a in exc_livre.attrs["liquidity_warnings"])


def test_universo_sem_a_coluna_de_giro_bloqueia_a_publicacao():
    """INVERTE `test_universo_sem_a_coluna_de_giro_nao_quebra` (achado A-004).

    "Não quebra" era o requisito errado: a vitrine sem a coluna fazia o motor
    PULAR o gate inteiro e devolver carteira cheia, com o piso de US$ 1 mi/dia
    ligado na tela e nenhuma posição verificada. Não quebrar continua valendo —
    o motor devolve payload completo, sem exceção —, mas o resultado é bloqueio
    com instrução de ingestão, não carteira.
    """
    from core.us_portfolio_creation import (
        USPortfolioCreationParams,
        build_portfolio_creation,
    )
    u = _universo().drop(columns=["giro_diario_usd"])
    r = build_portfolio_creation(u, _params_frouxos(USPortfolioCreationParams))

    assert r["ok"] is False and r["blocked"] is True
    assert r["holdings"].empty
    assert "run_us_ingest.py" in r["blocking_error"]

    # Zerar o piso é a decisão explícita de explorar sem validar liquidez.
    sem_piso = build_portfolio_creation(
        u, _params_frouxos(USPortfolioCreationParams, min_daily_turnover_usd=0.0))
    assert not sem_piso["holdings"].empty
    assert sem_piso["blocking_error"] is None


def test_piso_de_qualidade_substitui_dentro_da_industria():
    """A vaga da indústria é preservada: exigir qualidade não custa
    diversificação. No universo real de 03/08/2026 este caminho não dispara —
    as 21 indústrias com líder 'Excluída' são 100% 'Excluída' —, então a
    substituição precisa de fixture para ser exercitada de verdade."""
    from core.us_portfolio_creation import (
        USPortfolioCreationParams,
        build_industry_audit,
        prepare_eligible_universe,
        select_industry_leaders,
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
        USPortfolioCreationParams,
        build_industry_audit,
        prepare_eligible_universe,
        select_industry_leaders,
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


def _app_filtro_liquidez(tmp_path, frame: pd.DataFrame, piso: float):
    """Roda `_render_us_filtro_liquidez` no Streamlit de verdade, sem banco.

    Pickle e não JSON: `to_json` grava ±infinito como null e o caso mais
    perigoso (inf passando por `>= piso`) viraria um NaN comum no caminho.
    """
    from streamlit.testing.v1 import AppTest

    dados = tmp_path / "u.pkl"
    frame.to_pickle(dados)
    script = tmp_path / "app.py"
    piso_expr = (
        "float('nan')" if np.isnan(piso) else
        "float('inf')" if np.isposinf(piso) else
        "float('-inf')" if np.isneginf(piso) else repr(piso)
    )
    script.write_text(
        "import pandas as pd, streamlit as st\n"
        f"u = pd.read_pickle(r'{dados}')\n"
        "from views.empresas_americanas import _render_us_filtro_liquidez\n"
        f"saida = _render_us_filtro_liquidez(u, {piso_expr})\n"
        "st.text(','.join(sorted(saida['symbol'].astype(str))))\n",
        encoding="utf-8")
    at = AppTest.from_file(str(script), default_timeout=60).run()
    assert not at.exception, at.exception
    restantes = [s for s in at.text[0].value.split(",") if s]
    return at, restantes


def _universo_tres_estados() -> pd.DataFrame:
    """S00/S01 não verificadas, S02/S03 lixo (±inf), S04/S05 abaixo, S06/S07 ok."""
    u = _universo(8)
    u["giro_diario_usd"] = [np.nan, np.nan, np.inf, -np.inf,
                            2e5, 0.0, 5e7, 4e6]
    return u


def test_tela_com_piso_remove_o_nao_verificado_e_declara_quantos(tmp_path):
    """A tela replicava `giro.isna() | (giro >= piso)`: prometia "≥ US$ 20
    milhões/dia" e listava empresa cujo volume ninguém mediu."""
    u = _universo_tres_estados()
    at, restantes = _app_filtro_liquidez(tmp_path, u, 1e6)

    assert set(restantes) == {"S06", "S07"}
    # Card CSS, não informação solta: os três estados aparecem no markdown.
    cards = " ".join(m.value for m in at.markdown)
    assert "Não verificadas" in cards and "Medida abaixo do piso" in cards
    assert any("sem série de volume ou sem data" in c.value
               for c in at.caption)


def test_tela_sem_piso_mantem_tudo_mas_avisa_que_nao_validou(tmp_path):
    """Modo exploratório: o não verificado aparece e a tela diz que aparece."""
    u = _universo_tres_estados()
    at, restantes = _app_filtro_liquidez(tmp_path, u, 0.0)

    assert set(restantes) == set(u["symbol"])
    assert any("não validada" in c.value for c in at.caption)


@pytest.mark.parametrize("piso_invalido", [float("nan"), -1.0,
                                            float("inf"), float("-inf")])
def test_tela_bloqueia_piso_invalido_sem_excecao(tmp_path, piso_invalido):
    """O mesmo valor inválido do motor não pode renderizar análise enganosa."""
    at, restantes = _app_filtro_liquidez(tmp_path, _universo_tres_estados(), piso_invalido)

    assert restantes == []
    assert any("Piso de negociabilidade inválido" in error.value for error in at.error)


def test_tela_exige_timestamp_atual_para_o_giro_alto(tmp_path):
    """Valor alto não basta: a UI usa a mesma janela de frescor do motor."""
    u = _universo(4)
    u["giro_diario_usd"] = 50e6
    u["giro_diario_usd_at"] = [pd.Timestamp.now(tz="UTC"),
                                 pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=8),
                                 pd.NaT, "data inválida"]
    at, restantes = _app_filtro_liquidez(tmp_path, u, 1e6)

    assert restantes == ["S00"]
    assert any("sem série de volume ou sem data" in c.value for c in at.caption)


def test_tela_sem_a_coluna_de_volume_nao_finge_ter_aplicado_o_piso(tmp_path):
    """Sem a coluna, o piso não pode ser aplicado — e devolver o universo
    inteiro seria afirmar que ele foi."""
    u = _universo(8).drop(columns=["giro_diario_usd"])
    at, restantes = _app_filtro_liquidez(tmp_path, u, 5e6)

    assert restantes == []
    assert any("não publica o volume negociado" in w.value for w in at.warning)


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
        USPortfolioCreationParams,
        build_industry_audit,
        prepare_eligible_universe,
        select_industry_leaders,
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


def test_vitrine_nao_carrega_o_bloco_mais_pesado_por_padrao():
    """Trava de custo: `financials` fora da consulta principal da vitrine.

    Medido em 03/08/2026 sobre a vitrine publicada: a consulta trafega 29 MB
    descomprimidos e 24 MB deles (83%) são esse bloco, que só serve de reserva
    para derivar payout_ratio em vitrine antiga. Buscá-lo sempre fazia a leitura
    estourar a conexão antes de terminar — o app publicado não conseguia abrir a
    aba. Se alguém devolver `financials` ao caminho normal, isto falha.
    """
    import re
    from pathlib import Path
    fonte = (Path(__file__).resolve().parents[1]
             / "core" / "us_read.py").read_text(encoding="utf-8")
    corpo = fonte.split("def load_snapshot_scored")[1].split("\ndef ")[0]
    chamada = re.search(r"_snapshot_df\((.*?)\)", corpo, re.S)
    assert chamada, "load_snapshot_scored não chama mais _snapshot_df"
    assert "financials" not in chamada.group(1), (
        "financials voltou para a consulta principal da vitrine")
