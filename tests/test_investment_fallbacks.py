import core.investimentos as investimentos
import core.proventos as proventos


def test_snapshot_tesouro_legado_nao_substitui_consolidado_xp():
    sql = investimentos._SQL_POSICOES_SNAPSHOT.lower()

    assert "td-snap-%" in sql
    assert "tesouro_direto" in sql
    assert "effective_source_table" in sql
    assert "dense_rank()" in sql
    assert "partition by pps.asset_id" in sql
    assert "asset_source_rank = 1" in sql


def test_evolucao_trata_linhas_tesouro_rotuladas_como_xp_como_tesouro():
    sql = investimentos._SQL_EVOLUCAO_SNAPSHOTS.lower()

    assert "td-snap-%" in sql
    assert "effective_source_table" in sql


def test_falha_da_carteira_real_nao_retorna_mock(monkeypatch):
    monkeypatch.setattr(investimentos.settings, "MOCK_MODE", False)
    monkeypatch.setattr(
        investimentos,
        "_carteira_real",
        lambda: (_ for _ in ()).throw(RuntimeError("segredo-nao-deve-ir-para-ui")),
    )
    dados = investimentos.get_carteira.__wrapped__()

    assert dados["data_source"] == "error"
    assert dados["posicoes"] == []
    assert dados["total_mercado"] == 0.0
    assert "segredo" not in dados["error_message"]


def test_falha_de_proventos_reais_nao_retorna_eventos_mock(monkeypatch):
    monkeypatch.setattr(proventos.settings, "MOCK_MODE", False)
    monkeypatch.setattr(
        proventos,
        "_proventos_real",
        lambda: (_ for _ in ()).throw(PermissionError("token-privado")),
    )
    dados = proventos.get_proventos.__wrapped__()

    assert dados["data_source"] == "error"
    assert dados["eventos"] == []
    assert dados["por_ativo_12m"] == []
    assert "token" not in dados["error_message"]


def test_falha_de_cashflow_real_retorna_ausencia_e_nao_mock(monkeypatch):
    monkeypatch.setattr(investimentos.settings, "MOCK_MODE", False)
    monkeypatch.setattr(
        investimentos,
        "_cashflow_real",
        lambda: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    assert investimentos.get_cashflow_mensal.__wrapped__() == []


def test_falha_de_evolucao_real_retorna_estado_vazio(monkeypatch):
    monkeypatch.setattr(investimentos.settings, "MOCK_MODE", False)
    monkeypatch.setattr(
        investimentos,
        "_evolucao_real",
        lambda: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    dados = investimentos.get_evolucao_patrimonial.__wrapped__()
    assert dados["data_source"] == "error"
    assert dados["snapshots"] == []


def test_evolucao_nao_soma_tesouro_rotulado_como_xp_na_mesma_data():
    # Ago/26: o consolidado da B3 e o extrato do Tesouro caem em 2026-08-31.
    # As linhas td-snap contam como tesouro_direto e o desempate por ativo
    # fica com uma so -- a mesma regra do ponto de hoje.
    sql = " ".join(investimentos._SQL_EVOLUCAO_SNAPSHOTS.lower().split())

    assert "when pps.source_id like 'td-snap-%' then 'tesouro_direto'" in sql
    assert "partition by ls.corte, pps.asset_id" in sql
    assert "asset_source_rank = 1" in sql
