"""INF-A2: o app publicado não pode trazer do Supabase o que vai descartar.

Medido em 04/10/2026: a vitrine de FIIs tem 434 linhas e 2,9 MB de JSON, e
era relida em segundo plano a cada expiração do cache de 15 min mesmo com o
artefato local válido -- o resultado ia para uma memória que o artefato
vencia na chamada seguinte. A lista de empresas dos EUA (0,87 MB) e o
histórico de preços dos FIIs (1,24 MB) eram relidos a cada 5 min e 1 h sem
que a fonte tivesse mudado.
"""
import datetime as dt

import pandas as pd
import pytest

import core.us_data as us_data
from core import market_read


class _Connection:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _Engine:
    def connect(self):
        return _Connection()


def _snapshot_frame(size: int = 3, *, age_days: int = 0) -> pd.DataFrame:
    rows = []
    today = dt.datetime.now(dt.timezone.utc).date()
    for index in range(size):
        ticker = f"T{index:03}11"
        payload = {"ticker": ticker, "score": index / 10}
        rows.append({
            "ticker": ticker,
            "payload_json": payload,
            "as_of_date": today - dt.timedelta(days=age_days),
            "available_at": today,
            "knowledge_at": today,
            "reference_date": today,
            "vintage": "test",
            "source": "synthetic",
            "quality_status": "published",
            "schema_version": "fii_selection_inputs.v2",
            "generated_at": pd.Timestamp.now(tz="UTC"),
            "payload_sha256": market_read._snapshot_payload_digest(payload),
            "coverage_json": {"coverage_pct": 100},
        })
    return pd.DataFrame(rows)


def _artifact(age_days: int) -> pd.DataFrame:
    frame = _snapshot_frame(age_days=age_days)
    frame.attrs.update({
        "snapshot_source": "local_verified_artifact",
        "snapshot_fallback": True,
        "fallback_reason": "local_artifact_preferred",
        "snapshot_read_attempts": 0,
    })
    return frame


@pytest.fixture(autouse=True)
def _estado_limpo():
    market_read._reset_fii_snapshot_memory_cache()
    yield
    job = market_read._FII_SNAPSHOT_JOB
    if job is not None:
        job[0].join(timeout=5)
    market_read._reset_fii_snapshot_memory_cache()


@pytest.fixture
def leituras_remotas(monkeypatch):
    """Conta as páginas lidas do 'Supabase' e devolve a vitrine pedida."""
    estado = {"paginas": 0, "remota": _snapshot_frame()}

    def read_page(*args, **kwargs):
        estado["paginas"] += 1
        return estado["remota"]

    monkeypatch.setattr(market_read, "_fii_snapshot_engine", lambda: _Engine())
    monkeypatch.setattr(market_read.pd, "read_sql_query", read_page)
    return estado


def _espera_worker():
    job = market_read._FII_SNAPSHOT_JOB
    if job is not None:
        job[0].join(timeout=5)


def test_artefato_dentro_do_alvo_nao_dispara_leitura_do_supabase(
        monkeypatch, leituras_remotas):
    monkeypatch.setattr(market_read, "_load_fii_snapshot_artifact",
                        lambda: _artifact(age_days=1))

    for _ in range(3):
        frame = market_read._load_fii_selection_snapshot()
        assert len(frame) == 3
        assert frame.attrs["snapshot_source"] == "local_verified_artifact"
        assert frame.attrs["fallback_reason"] == "local_artifact_preferred"

    assert market_read._FII_SNAPSHOT_JOB is None
    assert leituras_remotas["paginas"] == 0


def test_artefato_velho_le_o_supabase_e_serve_a_vitrine_mais_nova(
        monkeypatch, leituras_remotas):
    monkeypatch.setattr(market_read, "_load_fii_snapshot_artifact",
                        lambda: _artifact(age_days=6))
    leituras_remotas["remota"] = _snapshot_frame(age_days=0)

    primeira = market_read._load_fii_selection_snapshot()
    # A tela não espera o Supabase: sai o artefato, com o aviso de idade.
    assert primeira.attrs["snapshot_source"] == "local_verified_artifact"
    assert primeira.attrs["fallback_reason"] == "remote_refresh_in_background"
    assert primeira.attrs["snapshot_stale_warning"] is True
    _espera_worker()

    segunda = market_read._load_fii_selection_snapshot()
    # Antes do INF-A2 esta leitura era descartada: o artefato sempre vencia.
    assert segunda.attrs["snapshot_source"] == "database"
    assert segunda.attrs["fallback_reason"] == "remote_newer_than_local_artifact"
    assert segunda.attrs["snapshot_age_days"] == 0
    assert "load_error" not in segunda.attrs
    assert leituras_remotas["paginas"] == 1


def test_supabase_tao_velho_quanto_o_artefato_nao_e_relido_a_cada_ttl(
        monkeypatch, leituras_remotas):
    monkeypatch.setattr(market_read, "_load_fii_snapshot_artifact",
                        lambda: _artifact(age_days=6))
    leituras_remotas["remota"] = _snapshot_frame(age_days=6)

    market_read._load_fii_selection_snapshot()
    _espera_worker()
    for _ in range(3):
        frame = market_read._load_fii_selection_snapshot()
        assert frame.attrs["snapshot_source"] == "local_verified_artifact"
        assert frame.attrs["fallback_reason"] == "local_artifact_stale"

    assert leituras_remotas["paginas"] == 1


def test_nova_tentativa_remota_so_depois_do_intervalo(monkeypatch, leituras_remotas):
    monkeypatch.setattr(market_read, "_load_fii_snapshot_artifact",
                        lambda: _artifact(age_days=6))
    leituras_remotas["remota"] = _snapshot_frame(age_days=6)

    market_read._load_fii_selection_snapshot()
    _espera_worker()
    market_read._FII_SNAPSHOT_LAST_REMOTE_TRY_AT = (
        dt.datetime.now(dt.timezone.utc)
        - dt.timedelta(seconds=market_read._FII_SNAPSHOT_REMOTE_RETRY_SECONDS + 1)
    )
    market_read._load_fii_selection_snapshot()
    _espera_worker()

    assert leituras_remotas["paginas"] == 2


def test_artefato_reprovado_nao_e_servido_e_cai_no_supabase(
        monkeypatch, leituras_remotas):
    reprovado = _snapshot_frame()
    reprovado.attrs["load_error"] = "snapshot_hash_invalid"
    monkeypatch.setattr(market_read, "_load_fii_snapshot_artifact", lambda: reprovado)

    frame = market_read._load_fii_selection_snapshot()

    assert frame.attrs["snapshot_source"] == "database"
    assert "load_error" not in frame.attrs
    assert leituras_remotas["paginas"] == 1


def test_leituras_da_tabela_inteira_ficam_em_cache_de_12h():
    doze_horas = 12 * 3600
    assert market_read.load_mercado_retorno_mensal._info.ttl == doze_horas
    assert us_data.companies._info.ttl == doze_horas
    assert us_data.overview._info.ttl == doze_horas
