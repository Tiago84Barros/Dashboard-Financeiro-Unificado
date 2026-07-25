from datetime import date, datetime, timezone

from core.market_freshness import classificar_cotacao, intervalo_referencia


def test_classifica_cotacao_recente_antiga_ausente_e_futura():
    hoje = datetime(2026, 7, 25, tzinfo=timezone.utc)
    assert classificar_cotacao(datetime(2026, 7, 24, tzinfo=timezone.utc), hoje) == "fresh"
    assert classificar_cotacao(datetime(2026, 7, 10, tzinfo=timezone.utc), hoje) == "stale"
    assert classificar_cotacao(None, hoje) == "missing"
    assert classificar_cotacao(datetime(2026, 7, 26, tzinfo=timezone.utc), hoje) == "invalid"


def test_intervalo_referencia_preserva_datas_distintas():
    minimo, maximo = intervalo_referencia(
        [date(2026, 6, 30), date(2026, 5, 17), None]
    )
    assert minimo == date(2026, 5, 17)
    assert maximo == date(2026, 6, 30)


def test_intervalo_referencia_vazio_e_explicito():
    assert intervalo_referencia([None]) == (None, None)
