"""A vela mensal da brapi não pisa no pregão diário de ``historical_prices``.

A brapi data a vela mensal (interval 1mo, o padrão de ``bootstrap`` e
``annual``) no 1º dia útil do mês, com o fechamento do FIM do mês e o volume
do mês inteiro. Pela chave (ticker, date) o upsert regravava o pregão daquele
dia com esses números (IVVB11 em 03/08/2026: 449,35 no lugar de 435,83).

Agora ``normalize.price_rows`` marca a vela agregada e o ``repository._upsert``
só a grava no mês sem pregão em outra data. Os testes com banco rodam num
banco descartável do armazém local (porta 5433); sem armazém, são pulados.
Dados sintéticos.
"""
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine, text

import data_pipeline.market.normalize as nz
from data_pipeline.market import repository as repo


def _ts(y, m, d):
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())


def _vela(y, m, d, close, volume=100):
    return {"date": _ts(y, m, d), "open": close, "high": close, "low": close,
            "close": close, "adjustedClose": close, "volume": volume}


def _mensal(*velas, intervalo="1mo"):
    q = {"symbol": "ETFX11", "historicalDataPrice": list(velas)}
    if intervalo is not None:
        q["usedInterval"] = intervalo
    return q


# ── Sem banco: a marca ───────────────────────────────────────────────────────

def test_chave_da_marca_e_a_mesma_nos_dois_modulos():
    assert repo._CHAVE_BARRA_AGREGADA == nz.CHAVE_BARRA_AGREGADA


@pytest.mark.parametrize("intervalo", ["1mo", "1wk", "5d", "3mo", "1MO"])
def test_intervalo_agregado_marca_a_vela(intervalo):
    rows = nz.price_rows(_mensal(_vela(2026, 8, 3, 50.0), intervalo=intervalo))
    assert rows and all(r[nz.CHAVE_BARRA_AGREGADA] is True for r in rows)


def test_intervalo_diario_nao_marca():
    rows = nz.price_rows(_mensal(_vela(2026, 8, 3, 50.0), _vela(2026, 8, 4, 51.0),
                                 intervalo="1d"))
    assert len(rows) == 2 and not any(nz.CHAVE_BARRA_AGREGADA in r for r in rows)


def test_sem_intervalo_espacamento_mensal_marca():
    q = _mensal(_vela(2026, 6, 1, 48.0), _vela(2026, 7, 1, 49.0),
                _vela(2026, 8, 3, 50.0), intervalo=None)
    assert all(r[nz.CHAVE_BARRA_AGREGADA] for r in nz.price_rows(q))


def test_sem_intervalo_espacamento_diario_nao_marca():
    q = _mensal(_vela(2026, 8, 3, 50.0), _vela(2026, 8, 4, 50.5),
                _vela(2026, 8, 5, 51.0), intervalo=None)
    assert not any(nz.CHAVE_BARRA_AGREGADA in r for r in nz.price_rows(q))


def test_sem_intervalo_com_poucas_velas_segue_como_pregao():
    # duas velas um ano à parte: não há como saber, fica o comportamento antigo
    q = _mensal(_vela(2024, 12, 30, 30.0), _vela(2025, 12, 30, 40.0), intervalo=None)
    assert not any(nz.CHAVE_BARRA_AGREGADA in r for r in nz.price_rows(q))


def test_normalize_all_carrega_a_marca():
    q = {**_mensal(_vela(2026, 8, 3, 50.0)), "currency": "BRL"}
    assert nz.normalize_all(q)["historical_prices"][0][nz.CHAVE_BARRA_AGREGADA]


class _ConnSemConsulta:
    def execute(self, *a, **k):  # pragma: no cover - não pode ser chamado
        pytest.fail("lote sem vela agregada não consulta o banco")


def test_lote_diario_nao_consulta_o_banco_e_sai_sem_marca():
    rows = [{"ticker": "ETFX11", "date": date(2026, 8, 3), "close": 1.0}]
    assert repo._sem_barra_que_pisa_no_diario(_ConnSemConsulta(), rows) == rows


# ── Com banco: armazém local, banco descartável ──────────────────────────────

_DDL = """
CREATE SCHEMA market;
CREATE TABLE market.historical_prices (
    ticker text, date date, open numeric, high numeric, low numeric,
    close numeric, adjusted_close numeric, volume bigint,
    UNIQUE (ticker, date));
"""


@pytest.fixture
def banco():
    try:
        from scripts.construir_memoria_mercado import warehouse_url
        url = warehouse_url()
    except Exception as erro:  # noqa: BLE001
        pytest.skip(f"sem armazem local ({erro})")
    nome = f"teste_barra_{uuid.uuid4().hex[:10]}"
    administrador = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with administrador.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{nome}"'))
    except Exception as erro:  # noqa: BLE001
        administrador.dispose()
        pytest.skip(f"armazem local nao respondeu ({erro})")
    engine = create_engine(url.rsplit("/", 1)[0] + f"/{nome}")
    with engine.begin() as conn:
        conn.exec_driver_sql(_DDL)
    repo.reset_db_cols_cache()
    try:
        yield engine
    finally:
        repo.reset_db_cols_cache()
        engine.dispose()
        with administrador.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{nome}" WITH (FORCE)'))
        administrador.dispose()


def _gravar(engine, quote):
    with engine.begin() as conn:
        return repo.upsert(conn, "historical_prices", nz.price_rows(quote))


def _serie(engine):
    with engine.connect() as conn:
        return {d: (float(c), v) for d, c, v in conn.execute(text(
            "SELECT date, close, volume FROM market.historical_prices "
            "WHERE ticker = 'ETFX11' ORDER BY date")).fetchall()}


def _diario(*velas):
    return _mensal(*velas, intervalo="1d")


def test_vela_mensal_nao_pisa_no_pregao_do_dia_1(banco):
    _gravar(banco, _diario(_vela(2026, 8, 3, 435.83, 10), _vela(2026, 8, 4, 437.0, 12)))
    assert _gravar(banco, _mensal(_vela(2026, 8, 3, 449.35, 900))) == 0
    assert _serie(banco) == {date(2026, 8, 3): (435.83, 10), date(2026, 8, 4): (437.0, 12)}


def test_vela_mensal_preenche_so_o_mes_vazio(banco):
    _gravar(banco, _diario(_vela(2026, 8, 3, 435.83), _vela(2026, 8, 31, 449.35)))
    n = _gravar(banco, _mensal(_vela(2026, 7, 1, 420.0, 800), _vela(2026, 8, 3, 449.35, 900)))
    assert n == 1
    s = _serie(banco)
    assert s[date(2026, 7, 1)] == (420.0, 800)          # julho vazio: entra
    assert s[date(2026, 8, 3)] == (435.83, 100)        # agosto tem pregão: fica


def test_vela_mensal_atualiza_a_propria_vela_do_mes(banco):
    # mês só com a vela mensal gravada antes (histórico só mensal): o mês
    # corrente fecha, e a vela nova substitui a antiga
    _gravar(banco, _mensal(_vela(2026, 11, 3, 100.0, 300)))
    assert _gravar(banco, _mensal(_vela(2026, 11, 3, 104.0, 650))) == 1
    assert _serie(banco) == {date(2026, 11, 3): (104.0, 650)}


def test_pregao_diario_continua_atualizando_e_corrige_a_vela(banco):
    _gravar(banco, _mensal(_vela(2026, 8, 3, 449.35, 900)))
    _gravar(banco, _diario(_vela(2026, 8, 3, 435.83, 10)))
    assert _serie(banco) == {date(2026, 8, 3): (435.83, 10)}
    _gravar(banco, _diario(_vela(2026, 8, 3, 436.0, 11)))
    assert _serie(banco) == {date(2026, 8, 3): (436.0, 11)}


def test_mesmo_resultado_por_qualquer_caminho_que_grava_vela(banco):
    """``ingest_ticker``/``renormalize`` gravam ``normalize_all(...)``;
    ``ingest_benchmark``/``backfill_series`` gravam ``price_rows(...)``.
    Os dois lotes têm de dar a mesma série."""
    _gravar(banco, _diario(_vela(2026, 8, 3, 435.83, 10), _vela(2026, 8, 4, 437.0, 12)))
    q = _mensal(_vela(2026, 7, 1, 420.0, 800), _vela(2026, 8, 3, 449.35, 900))
    with banco.begin() as conn:
        repo.upsert(conn, "historical_prices", nz.normalize_all(q)["historical_prices"])
    via_normalize_all = _serie(banco)
    with banco.begin() as conn:
        conn.exec_driver_sql("DELETE FROM market.historical_prices WHERE date < '2026-08-01'")
    _gravar(banco, q)
    assert _serie(banco) == via_normalize_all == {
        date(2026, 7, 1): (420.0, 800), date(2026, 8, 3): (435.83, 10),
        date(2026, 8, 4): (437.0, 12)}


def test_mes_so_com_o_pregao_da_data_da_vela_recebe_a_vela(banco):
    """Limite aceito: a tabela não diz se a linha do dia 1 veio de vela ou de
    pregão. Se o mês só tem o pregão do dia 1, a série diária completa diz que
    foi o único pregão do mês, e a vela mensal tem os mesmos números. No mês
    corrente, a rotina diária seguinte regrava o dia 1 com o pregão."""
    _gravar(banco, _diario(_vela(2026, 11, 3, 100.0, 300)))
    assert _gravar(banco, _mensal(_vela(2026, 11, 3, 100.0, 300))) == 1
    _gravar(banco, _diario(_vela(2026, 11, 3, 100.5, 310), _vela(2026, 11, 4, 101.0, 20)))
    assert _serie(banco) == {date(2026, 11, 3): (100.5, 310), date(2026, 11, 4): (101.0, 20)}
