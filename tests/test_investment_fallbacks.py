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


def test_evolucao_xp_ignora_linhas_tesouro_rotuladas_como_xp():
    sql = investimentos._SQL_EVOLUCAO_SNAPSHOTS.lower()

    assert "td-snap-%" in sql
    assert "not like" in sql


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
