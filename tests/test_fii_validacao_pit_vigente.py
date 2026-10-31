"""A validação PIT vigente não pode ser escondida pela auditoria diária.

``market.fii_validation_runs`` guarda dois tipos de run sem coluna que os
distinga: a validação PIT (``fii_pit.py``, com ``backtest`` no
``metrics_json``) e o gate de prontidão da auditoria diária
(``fii_ingest.record_validation_readiness``, só com ``data_audit``, sempre
``blocked``). O defeito: quem buscava "o último run" pegava a auditoria de
03/10 e a vitrine de FIIs parou de publicar desde 28/09 com "validação PIT
local não aprovada", embora o run 90 (aprovado) continuasse lá.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import StaticPool

RAIZ = Path(__file__).resolve().parents[1]
VERSAO = "6.10.0"
ESTRATEGIA = "fii_integrated_robust_optimizer.v6.8"


def _motor_com_schema_market():
    """SQLite em memória com um banco anexado chamado ``market``.

    As consultas citam ``market.fii_validation_runs`` com o schema explícito;
    o ATTACH reproduz esse nome sem tocar em banco nenhum de verdade.
    """
    motor = create_engine("sqlite://", poolclass=StaticPool)

    @event.listens_for(motor, "connect")
    def _anexa(dbapi_conn, _record):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS market")

    with motor.begin() as conn:
        conn.execute(text("""
            CREATE TABLE market.fii_validation_runs (
                id INTEGER PRIMARY KEY,
                methodology_version TEXT NOT NULL,
                as_of_date TEXT NOT NULL,
                status TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                blockers_json TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT
            )
        """))
        linhas = [
            # Run 90: validação PIT aprovada.
            (90, "2026-08-31", "passed",
             {"strategy_id": ESTRATEGIA,
              "backtest": {"strategy_id": ESTRATEGIA, "periods": 65}},
             [], "2026-09-18 17:37:52"),
            # Runs 91-93: gate de prontidão gravado pela auditoria diária.
            (91, "2026-09-29", "blocked", {"data_audit": {"checks": {}}},
             ["unknown_fii_type: 83/607 (13.7%)",
              "backtest PIT walk-forward ainda não executado"],
             "2026-09-29 23:26:56"),
            (92, "2026-10-02", "blocked", {"data_audit": {"checks": {}}},
             ["backtest PIT walk-forward ainda não executado"],
             "2026-10-02 22:40:52"),
            (93, "2026-10-03", "blocked", {"data_audit": {"checks": {}}},
             ["backtest PIT walk-forward ainda não executado"],
             "2026-10-03 11:12:16"),
        ]
        for run_id, as_of, status, metrics, blockers, quando in linhas:
            conn.execute(text("""
                INSERT INTO market.fii_validation_runs VALUES
                (:id, :v, :as_of, :status, :metrics, :blockers, :quando, :quando)
            """), {"id": run_id, "v": VERSAO, "as_of": as_of, "status": status,
                    "metrics": json.dumps(metrics),
                    "blockers": json.dumps(blockers, ensure_ascii=False),
                    "quando": quando})
    return motor


@pytest.fixture()
def motor():
    eng = _motor_com_schema_market()
    yield eng
    eng.dispose()


def test_publicador_escolhe_a_validacao_pit_e_nao_a_auditoria(motor):
    from scripts.publish_fii_selection_snapshot import _latest_validation

    validacao = _latest_validation(motor)

    assert validacao is not None
    assert validacao["status"] == "passed"
    assert str(validacao["as_of_date"]) == "2026-08-31"


def test_status_da_tela_le_a_validacao_pit_e_nao_a_auditoria(motor):
    from core.market_read import _fii_validation_status

    status = _fii_validation_status(VERSAO, motor)

    assert status["status"] == "passed"
    assert str(status["as_of_date"]) == "2026-08-31"


def test_sem_validacao_pit_nenhuma_o_publicador_nao_inventa_aprovacao(motor):
    from scripts.publish_fii_selection_snapshot import _latest_validation

    with motor.begin() as conn:
        conn.execute(text("DELETE FROM market.fii_validation_runs WHERE id = 90"))

    assert _latest_validation(motor) is None


# Toda leitura de "o run vigente" precisa do mesmo filtro. Guarda duplicada
# diverge: o defeito existia em cinco cópias da mesma consulta.
_LEITURA_DO_ULTIMO_RUN = re.compile(
    r"FROM\s+market\.fii_validation_runs\b(?P<resto>.{0,400}?)LIMIT\s+1",
    re.IGNORECASE | re.DOTALL,
)


def _leituras_do_ultimo_run():
    for arquivo in sorted(RAIZ.rglob("*.py")):
        relativo = arquivo.relative_to(RAIZ).as_posix()
        if relativo.startswith(("tests/", ".venv/", "venv/")) or "/." in "/" + relativo:
            continue
        try:
            fonte = arquivo.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for achado in _LEITURA_DO_ULTIMO_RUN.finditer(fonte):
            linha = fonte.count("\n", 0, achado.start()) + 1
            yield f"{relativo}:{linha}", achado.group("resto")


def test_toda_leitura_do_ultimo_run_filtra_a_validacao_pit():
    leituras = list(_leituras_do_ultimo_run())
    # Se a varredura não acha nada, ela quebrou -- não significa que está limpo.
    assert len(leituras) >= 5, leituras
    sem_filtro = [onde for onde, trecho in leituras
                  if "FILTRO_RUN_VALIDACAO_PIT" not in trecho]
    assert sem_filtro == []
