from core.macro_data.readiness import backfill_readiness


class _Settings:
    FRED_API_KEY = ""

    def macro_enabled(self, provider):
        return provider in {"world_bank", "fred"}

    def macro_series(self):
        return {"world_bank": ({"code": "GDP", "country": "BRA"},), "fred": ()}


def test_readiness_explains_missing_series_and_credential_without_secrets():
    rows = {row.provider: row for row in backfill_readiness(_Settings())}
    assert rows["world_bank"].ready is True
    assert rows["fred"].ready is False
    assert "configurada" in rows["fred"].detail
    assert "credencial" not in rows["fred"].detail
