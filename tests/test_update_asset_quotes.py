import pytest

from scripts import update_asset_quotes as uaq


def test_moeda_filtra_so_os_ativos_daquela_moeda():
    sql, params = uaq._sql_ativos(False, "usd")
    assert params == {"moeda": "USD"}
    assert "upper(coalesce(currency, 'BRL')) = :moeda" in sql


def test_sem_moeda_mantem_o_comportamento_antigo():
    assert uaq._sql_ativos(False, None) == (uaq._SQL_TODOS, {})
    assert uaq._sql_ativos(True, None) == (uaq._SQL_SEM_COTACAO, {})


def test_moeda_nao_se_combina_com_apenas_sem_cotacao():
    with pytest.raises(ValueError):
        uaq._sql_ativos(True, "USD")
