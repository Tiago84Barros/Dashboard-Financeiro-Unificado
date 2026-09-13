"""A ausencia tem de CABER na tabela — e isso so um INSERT de verdade prova.

A rodada anterior fechou o achado 1 com testes que montavam a linha em
dicionario e liam SQL por AST. Nenhum tocava o banco. A linha de ausencia
tinha os tres campos de valor nulos e viola
``CHECK (num_nonnulls(value_numeric, value_text, value_json) = 1)``, de modo
que a derivacao levantava ``IntegrityError``, o fallback do SAVEPOINT em
``repository._upsert`` re-levantava, e o ``engine.begin()`` unico que envolve
as tres derivacoes (``fii_ingest.py:1417-1421``; auditoria, validacao e
snapshot vem depois, fora dele) derrubava a rodada inteira -- nem os valores
validos eram gravados. O regime piorou de "numero errado
publicado" para "pipeline caido".

Este arquivo exercita a gravacao contra o armazem local, sempre em transacao
revertida. Sem ele, a correcao continua sendo uma afirmacao sobre dicionarios.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text

from data_pipeline.market import fii_v2
from data_pipeline.market import repository as repo
from data_pipeline.market.fii_ingest import _latest_metric_rows

TICKER = "ZZAUS11"


@pytest.fixture(scope="module")
def motor():
    try:
        from scripts.publish_fii_selection_from_local import _warehouse_url

        engine = create_engine(_warehouse_url())
        with engine.connect() as conn:
            existe = conn.execute(text("""
                SELECT 1 FROM information_schema.tables
                 WHERE table_schema='market' AND table_name='fii_metric_observations'
            """)).scalar()
        if not existe:
            pytest.skip("armazem local sem market.fii_metric_observations")
    except Exception as exc:  # noqa: BLE001 - sem armazem, nao medimos
        pytest.skip(f"armazem local indisponivel: {exc}")
    yield engine
    engine.dispose()


@pytest.fixture
def conexao(motor):
    """Conexao em transacao SEMPRE revertida: nada do teste sobrevive."""
    conn = motor.connect()
    trans = conn.begin()
    try:
        yield conn
    finally:
        trans.rollback()
        conn.close()


def _linha_de_ausencia(recuo_dias: int = 0):
    """A linha que o proprio codigo de producao monta.

    ``recuo_dias`` so mexe nos carimbos de tempo, nunca na representacao do
    valor: dentro de uma transacao o ``now()`` do Postgres e o instante em que
    ela COMECOU, entao uma linha carimbada com o relogio do Python cai a frente
    dele e o ``knowledge_at <= now()`` do leitor nao a enxerga. E o mesmo
    motivo pelo qual, na rodada real, o que a derivacao grava so passa a ser
    lido na transacao seguinte.
    """
    linhas = fii_v2.income_metrics_from_monthly({TICKER: {}}, as_of=date(2026, 9, 20))
    linha = next(item for item in linhas if item["metric_name"] == "income_recurrence")
    if recuo_dias:
        quando = (datetime.now(timezone.utc) - timedelta(days=recuo_dias)).isoformat()
        linha = {**linha, "available_at": quando, "knowledge_at": quando}
    return linha


def test_a_linha_de_ausencia_cabe_na_tabela(conexao):
    """O INSERT que faltava. Com os tres campos de valor nulos a constraint
    recusa a linha, e o fallback linha-a-linha do repositorio re-levanta."""
    linha = _linha_de_ausencia()
    repo.upsert(conexao, "fii_metric_observations", [linha])
    gravada = conexao.execute(text("""
        SELECT value_numeric, value_text, value_json, metadata_json
          FROM market.fii_metric_observations
         WHERE ticker = :t AND metric_name = 'income_recurrence'
    """), {"t": TICKER}).mappings().one()
    assert gravada["value_numeric"] is None, "ausencia nao pode virar numero"
    assert gravada["value_text"] is None
    metadados = gravada["metadata_json"]
    if isinstance(metadados, str):
        metadados = json.loads(metadados)
    assert metadados["absence_reason"] == "sem_provento_observado"


def test_a_ausencia_gravada_derruba_o_valor_velho_no_leitor(conexao):
    """A prova a jusante, contra o banco: com a ausencia gravada depois do
    valor velho, o leitor que alimenta a decisao nao devolve mais o velho."""
    velho = fii_v2.metric_observation(
        ticker=TICKER, metric_name="income_recurrence", value=0.9,
        reference_date=date(2026, 7, 1),
        available_at=datetime.now(timezone.utc) - timedelta(days=60),
        source="brapi_fii_v2_derived", vintage="derived:2026-07-01")
    repo.upsert(conexao, "fii_metric_observations", [velho])
    antes = {r["ticker"]: r["value"]
             for r in _latest_metric_rows(conexao, ["income_recurrence"])}
    assert antes.get(TICKER) == pytest.approx(0.9), "controle: o valor velho vence sozinho"

    repo.upsert(conexao, "fii_metric_observations", [_linha_de_ausencia(recuo_dias=1)])
    depois = {r["ticker"] for r in _latest_metric_rows(conexao, ["income_recurrence"])}
    assert TICKER not in depois, (
        "a ausencia foi gravada e o leitor continua devolvendo o valor velho")


def test_a_derivacao_real_nao_derruba_a_rodada(conexao):
    """O achado novo: uma linha ruim apagava 376 boas. Executa a derivacao de
    verdade contra o armazem — revertida — e exige que ela conclua."""
    from data_pipeline.market.fii_ingest import _derive_income_observations

    gravadas = _derive_income_observations(conexao)
    assert gravadas > 0
    ausentes = conexao.execute(text("""
        SELECT count(*) FROM market.fii_metric_observations
         WHERE metric_name = 'income_recurrence'
           AND value_numeric IS NULL
           AND metadata_json ? 'absence_reason'
    """)).scalar()
    assert ausentes > 0, "a rodada nao gravou nenhuma ausencia"
