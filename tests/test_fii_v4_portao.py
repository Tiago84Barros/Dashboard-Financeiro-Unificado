"""O portao das rotinas v4 tem de perguntar pelas tabelas que a consulta le."""
import inspect
import re

import pytest

from data_pipeline.market import fii_ingest


def _tabelas_no_codigo(func) -> set[str]:
    """Tabelas `market.*` que a funcao de fato menciona -- lidas do proprio SQL.

    Enumerar a mao o que o portao exige envelhece em silencio: foi assim que
    `snapshot_methodology_v4` passou a checar uma tabela e a ler outra. Aqui a
    lista sai do codigo, entao acrescentar uma tabela na consulta sem
    acrescenta-la na constante reprova o teste.
    """
    fonte = inspect.getsource(func)
    nomes = set(re.findall(r"market\.([a-z_][a-z0-9_]*)", fonte))
    nomes |= set(re.findall(r'repo\.upsert\(conn,\s*"([a-z_][a-z0-9_]*)"', fonte))
    return {f"market.{nome}" for nome in nomes}


class _Resultado:
    def __init__(self, valor):
        self._valor = valor

    def scalar(self):
        return self._valor


class _Conexao:
    def __init__(self, existentes):
        self._existentes = existentes

    def execute(self, stmt, params=None):
        return _Resultado((params or {}).get("nome") in self._existentes)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Motor:
    """Banco de mentira: `begin()` acusa qualquer escrita que passe pelo portao."""

    def __init__(self, existentes):
        self._existentes = existentes

    def connect(self):
        return _Conexao(self._existentes)

    def begin(self):
        raise AssertionError("gravou num banco sem as tabelas exigidas")


@pytest.mark.parametrize("func,constante", [
    (fii_ingest.snapshot_methodology_v4, fii_ingest.TABELAS_SNAPSHOT_V4),
    (fii_ingest.reconcile_brapi_cvm, fii_ingest.TABELAS_RECONCILIACAO),
])
def test_portao_cobre_toda_tabela_que_a_funcao_toca(func, constante):
    assert _tabelas_no_codigo(func) == set(constante)


def test_a_tabela_que_derrubava_o_actions_esta_no_portao():
    """`fii_metric_observations` mora so no armazem local desde a migracao
    local-first. O portao antigo olhava para `fii_score_snapshots`, que existe
    nos dois bancos -- e por isso aprovava no Supabase e morria na consulta
    seguinte, todo dia, levando junto o passo do benchmark."""
    assert "market.fii_metric_observations" in fii_ingest.TABELAS_SNAPSHOT_V4
    assert "market.fii_metric_observations" in fii_ingest.TABELAS_RECONCILIACAO


def test_ausentes_lista_so_o_que_falta():
    conn = _Conexao({"market.fiis"})
    assert fii_ingest._tabelas_ausentes(conn, ("market.fiis",)) == []
    assert fii_ingest._tabelas_ausentes(
        conn, ("market.fiis", "market.fii_metric_observations")
    ) == ["market.fii_metric_observations"]


def test_snapshot_bloqueia_nomeando_a_tabela_e_sem_gravar(monkeypatch):
    faltando = "market.fii_metric_observations"
    motor = _Motor(set(fii_ingest.TABELAS_SNAPSHOT_V4) - {faltando})
    monkeypatch.setattr(fii_ingest, "_engine", lambda: motor)
    resultado = fii_ingest.snapshot_methodology_v4()
    assert resultado["status"] == "blocked"
    assert resultado["gravados"] == 0
    assert [b for b in resultado["blockers"] if faltando in b]


def test_snapshot_nao_culpa_mais_uma_migracao_que_nao_esta_pendente(monkeypatch):
    """A migracao 023 rodou; a tabela e que fica noutro banco de proposito.

    Culpar a migracao mandava quem lesse o log procurar no lugar errado.
    """
    motor = _Motor(set(fii_ingest.TABELAS_SNAPSHOT_V4) - {"market.fii_metric_observations"})
    monkeypatch.setattr(fii_ingest, "_engine", lambda: motor)
    assert not [b for b in fii_ingest.snapshot_methodology_v4()["blockers"] if "023" in b]


def test_reconciliacao_pula_dizendo_o_que_falta(monkeypatch):
    faltando = "market.fii_metric_observations"
    motor = _Motor(set(fii_ingest.TABELAS_RECONCILIACAO) - {faltando})
    monkeypatch.setattr(fii_ingest, "_engine", lambda: motor)
    resultado = fii_ingest.reconcile_brapi_cvm()
    assert resultado["status"] == "skipped"
    assert resultado["tabelas_ausentes"] == [faltando]
