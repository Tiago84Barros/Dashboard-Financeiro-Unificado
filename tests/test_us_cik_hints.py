# -*- coding: utf-8 -*-
"""A-146: 21 empresas ativas ficaram congeladas num parser antigo, em silencio.

A varredura fechou com `errors = 23` e `market_us.ingestion_errors` tinha 2.
Os outros 21 (CPRX, TMHC, AVNS, NFBK...) sumiram de `company_tickers.json`,
`get_profile` devolveu vazio e o ramo que trata isso retornava sem registrar
nada. Continuaram `analysis_status='eligible'`, disputando ranking com
demonstracoes lidas por um parser que ja tinha sido substituido -- que e pior
do que sair do universo, porque nao aparece em lugar nenhum.

O CIK deles ja estava em `market_us.companies`, gravado quando a SEC ainda os
listava.
"""
from __future__ import annotations

from data_pipeline.us.edgar import EdgarProvider


def _provider(mapa: dict) -> EdgarProvider:
    p = EdgarProvider(user_agent="teste teste@exemplo.com")
    p._ticker_map = dict(mapa)
    return p


def test_sem_dica_o_ticker_ausente_na_sec_nao_resolve():
    assert _provider({"AAPL": "0000320193"})._cik_for("CPRX") is None


def test_a_dica_local_resgata_o_ticker_que_sumiu_da_sec():
    p = _provider({"AAPL": "0000320193"})
    p.set_cik_hints({"CPRX": "0001369568"})
    assert p._cik_for("CPRX") == "0001369568"


def test_o_arquivo_oficial_vence_a_dica():
    """A dica e memoria; o arquivo da SEC reflete reestruturacao recente."""
    p = _provider({"TOI": "0001799191"})
    p.set_cik_hints({"TOI": "0000000009"})
    assert p._cik_for("TOI") == "0001799191"


def test_override_curado_vence_os_dois():
    p = _provider({"XOM": "0002115436"})
    p.set_cik_hints({"XOM": "0000000009"})
    assert p._cik_for("XOM") == "0000034088"


def test_dica_com_cik_curto_e_normalizada():
    p = _provider({})
    p.set_cik_hints({"cprx": 1369568})
    assert p._cik_for("CPRX") == "0001369568"


def test_dica_vazia_ou_invalida_nao_quebra():
    p = _provider({"AAPL": "0000320193"})
    p.set_cik_hints({"": "1", "ZZZZ": None, None: "2"})
    assert p._cik_for("AAPL") == "0000320193"
    assert p._cik_for("ZZZZ") is None


# ── o outro lado do A-146: a falha tem de deixar rastro ─────────────────────
class _SemPerfilProvider:
    """SEC nao resolve o CIK: `get_profile` devolve vazio."""
    pre_normalized = True
    calls_made = 0

    def get_profile(self, symbol):
        return None


class _ConnGravador:
    def __init__(self, registros):
        self.registros = registros

    def execute(self, stmt, params=None, *a, **k):
        self.registros.append((str(stmt), dict(params or {})))
        return None


class _EngineGravador:
    def __init__(self):
        self.registros: list = []

    def begin(self):
        conn = _ConnGravador(self.registros)

        class _Ctx:
            def __enter__(self_):
                return conn

            def __exit__(self_, *exc):
                return False
        return _Ctx()


def test_perfil_vazio_deixa_rastro_em_ingestion_errors():
    from data_pipeline.us import ingest

    eng = _EngineGravador()
    res = ingest.ingest_symbol(_SemPerfilProvider(), eng, "CPRX",
                               run_id=1, with_prices=False)
    assert res["ok"] is False
    sqls = " ".join(s for s, _ in eng.registros).lower()
    assert "ingestion_errors" in sqls, "a falha voltou a ser silenciosa"
    # `log_error` liga os parametros por posicao curta (:t = error_type).
    assert any("empty_profile" in p.values() for _, p in eng.registros)
