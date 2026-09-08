"""Teste opt-in em tabelas TEMP do Docker: rollback não toca o acervo real."""
import os
from contextlib import nullcontext
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.skipif(os.getenv("APP4_TEST_MACRO_TEMP_TABLES") != "1",
                              reason="requer Docker local e opt-in para tabelas temporárias")


def test_postgres_selects_one_vintage_per_period_before_window():
    from core.config import settings
    from core.macro_data.portfolio_context import load_portfolio_macro_snapshot
    assert settings.MACRO_LOCAL_DB_URL
    assert make_url(settings.MACRO_LOCAL_DB_URL).host in {"localhost", "127.0.0.1", "::1"}
    engine = create_engine(settings.MACRO_LOCAL_DB_URL)
    try:
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                conn.execute(text("CREATE TEMP TABLE macro_sector_exposures (asset_class text, sector text, factor text, sensitivity float, confidence float, channel text) ON COMMIT DROP"))
                conn.execute(text("CREATE TEMP TABLE macro_indicators (provider text, provider_code text, country_code text, category text, frequency text, unit text, source_url text, active boolean) ON COMMIT DROP"))
                conn.execute(text("CREATE TEMP TABLE macro_observations (id int, provider text, provider_code text, country_code text, reference_period date, value numeric, retrieved_at timestamptz, released_at timestamptz, is_preliminary boolean, is_forecast boolean, vintage_date date) ON COMMIT DROP"))
                conn.execute(text("INSERT INTO macro_sector_exposures VALUES ('b3','Synthetic','monetary_policy',0.5,0.55,'test')"))
                conn.execute(text("INSERT INTO macro_indicators VALUES ('synthetic','RATE','BRA','monetary_policy','monthly','%','https://example.org',true)"))
                conn.execute(text("INSERT INTO macro_observations VALUES (0,'synthetic','RATE','BRA','2025-01-01',1,'2025-01-02','2025-01-02',false,false,'2025-01-02')"))
                conn.execute(text("INSERT INTO macro_observations SELECT n,'synthetic','RATE','BRA','2025-02-01',2+n/100.0, '2025-02-01'::timestamptz + n * interval '1 hour','2025-02-01',false,false,'2025-02-01' FROM generate_series(1,25) n"))
                conn.execute(text("INSERT INTO macro_observations VALUES (99,'synthetic','RATE','BRA','2025-02-01',99,'2025-03-10','2025-03-10',false,false,'2025-03-10')"))
                class BoundEngine:
                    def connect(self):
                        return nullcontext(conn)
                snap = load_portfolio_macro_snapshot(BoundEngine(), asset_class='b3',
                    assets={'SYNTH': 'Synthetic', 'UNMAPPED': ''},
                    as_of=datetime(2025,2,28,tzinfo=timezone.utc))
                assert snap.coverage == .5
                assert len(snap.details) == 1
                assert float(snap.details[0]['value']) == 2.25
                assert snap.details[0]['direction'] == 'positive'
                expired = load_portfolio_macro_snapshot(BoundEngine(), asset_class='b3',
                    assets={'SYNTH': 'Synthetic'}, as_of=datetime(2026,1,1,tzinfo=timezone.utc))
                assert expired.impacts == {} and expired.source_count == 0
                assert any('vencida' in s for s in expired.limitations)
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
