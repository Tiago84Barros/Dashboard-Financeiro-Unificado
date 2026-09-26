"""Serviço de análise de ativo: valida a estratégia antes de qualquer trabalho."""
import pytest
from sqlalchemy import create_engine, text

from core import inteligencia_ativos as servico
from core.estrategia import politica as pol
from core.estrategia import repositorio as repo

DONO = "55555555-5555-5555-5555-555555555555"
MINIMOS = {
    "objective": "renda_passiva", "time_horizon": "longo",
    "risk_profile": "moderado", "liquidity_need": "baixa",
    "predominant_strategy": "dividendos",
    "asset_class_targets": {"renda_fixa": 40, "acoes_br": 20, "fiis": 30,
                            "exterior": 10},
}
CARTEIRA = {"posicoes": [{"ticker": "HGLG11", "nome": "CSHG Logística",
                          "classe": "FII", "pct_carteira": 12.5}]}


@pytest.fixture()
def kw():
    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as conn:
        conn.execute(text("""
            CREATE TABLE investment_policies (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                version INTEGER NOT NULL, schema_version TEXT NOT NULL,
                status TEXT NOT NULL,
                policy_json TEXT NOT NULL DEFAULT '{}',
                interview_json TEXT NOT NULL DEFAULT '[]',
                completion_pct REAL NOT NULL DEFAULT 0,
                completed_at TEXT, created_at TEXT, updated_at TEXT,
                UNIQUE (user_id, version))
        """))
    return {"engine": eng, "owner_id": DONO}


def _concluida(kw):
    r = repo.iniciar(**kw)
    repo.salvar_rascunho(r.id, pol.aplicar({}, MINIMOS, fonte="manual")[0],
                         [], **kw)
    assert repo.concluir(r.id, **kw)[0]


def test_bloqueado_nao_le_carteira(kw, monkeypatch):
    import core.investimentos as inv

    def _nao_chame():
        raise AssertionError("carteira lida com a análise bloqueada")
    monkeypatch.setattr(inv, "get_carteira", _nao_chame)

    d = servico.analisar_ativo("HGLG11", **kw)
    assert d["analysis_available"] is False
    assert d["reason"] == "STRATEGY_CONFIGURATION_REQUIRED"
    assert d["configuration_status"] == "NOT_STARTED"
    assert "policy_context" not in d

    repo.iniciar(**kw)
    d = servico.analisar_ativo("HGLG11", **kw)
    assert d["configuration_status"] == "IN_PROGRESS"
    assert d["next_step"]["feature"] == "investment_strategy"


def test_concluida_libera_com_a_politica_como_premissa(kw):
    _concluida(kw)
    d = servico.analisar_ativo("hglg11", carteira=CARTEIRA, **kw)
    assert d["analysis_available"] is True
    assert d["asset"] == "HGLG11" and d["policy_version"] == 1
    assert "Objetivo principal: Renda passiva" in d["policy_context"]
    assert "Status: Concluída" in d["policy_context"]
    assert d["analysis"] is None  # a análise por LLM é a próxima etapa


def test_ativo_fora_da_carteira(kw):
    _concluida(kw)
    d = servico.analisar_ativo("PETR4", carteira=CARTEIRA, **kw)
    assert d["analysis_available"] is False
    assert d["reason"] == servico.ATIVO_FORA_DA_CARTEIRA


def test_premissa_recusa_politica_nao_concluida():
    rascunho = repo.Registro(
        id="r1", version=1, status_gravado="IN_PROGRESS",
        schema_version=pol.SCHEMA_VERSION,
        politica=pol.aplicar({}, MINIMOS, fonte="manual")[0], entrevista=[],
        completion_pct=100, completed_at=None, created_at=None,
        updated_at=None)
    for registro in (None, rascunho):
        with pytest.raises(servico.PoliticaNaoConcluida):
            servico.contexto_obrigatorio(registro)
