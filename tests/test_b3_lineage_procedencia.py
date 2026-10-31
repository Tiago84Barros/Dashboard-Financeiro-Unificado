"""Procedencia das demonstracoes: ponteiro existir nao e ponteiro levar a algum lugar."""
from sqlalchemy import create_engine, text

from core.b3_validation import lineage_counts


def _base():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    conn = engine.connect()
    conn.execute(text("ATTACH DATABASE ':memory:' AS market"))
    conn.execute(text("""
        CREATE TABLE market.brapi_raw_payloads (
            id INTEGER PRIMARY KEY, endpoint TEXT, fetched_at TEXT)"""))
    conn.execute(text("""
        CREATE TABLE market.income_statements (
            ticker TEXT, period TEXT, first_seen_at TEXT, raw_payload_id INTEGER)"""))
    return conn


def _payload(conn, pid, quando):
    conn.execute(text("INSERT INTO market.brapi_raw_payloads VALUES (:i,'quote',:q)"),
                 {"i": pid, "q": quando})


def _linha(conn, ticker, quando, pid):
    conn.execute(text("INSERT INTO market.income_statements VALUES (:t,'annual',:q,:p)"),
                 {"t": ticker, "q": quando, "p": pid})


def test_procedencia_valida_conta_como_rastreada():
    conn = _base()
    _payload(conn, 10, "2026-07-01")
    _linha(conn, "PETR4", "2026-07-06", 10)
    c = lineage_counts(conn, "market.income_statements")
    assert c["traced_rows"] == 1
    assert c["impossible_rows"] == 0 and c["dangling_rows"] == 0


def test_payload_coletado_depois_da_linha_nao_e_procedencia():
    """O caso real: a sequencia reiniciou e o ponteiro caiu noutra geracao.

    Medido no Supabase em 01/09/2026: 84.116 de 85.889 linhas apontavam para um
    payload coletado DEPOIS da propria linha. A causa nao pode ser posterior ao
    efeito. Contando nao-nulos, as 85.889 apareciam como linhagem completa.
    """
    conn = _base()
    _payload(conn, 10, "2026-08-28")          # geracao nova, mesmo id
    _linha(conn, "PETR4", "2026-07-06", 10)   # linha escrita em julho
    c = lineage_counts(conn, "market.income_statements")
    assert c["pointer_rows"] == 1             # o ponteiro existe...
    assert c["traced_rows"] == 0              # ...e nao sustenta procedencia
    assert c["impossible_rows"] == 1


def test_ponteiro_orfao_aparece_separado_de_impossivel():
    conn = _base()
    _linha(conn, "VALE3", "2026-07-06", 99999)
    c = lineage_counts(conn, "market.income_statements")
    assert c["dangling_rows"] == 1
    assert c["traced_rows"] == 0 and c["impossible_rows"] == 0


def test_linha_sem_ponteiro_nao_entra_em_nenhum_balde_de_defeito():
    conn = _base()
    _linha(conn, "ITUB4", "2026-07-06", None)
    c = lineage_counts(conn, "market.income_statements")
    assert c["rows"] == 1
    assert (c["pointer_rows"], c["traced_rows"],
            c["dangling_rows"], c["impossible_rows"]) == (0, 0, 0, 0)


def test_os_baldes_somam_o_total_de_ponteiros():
    """Nenhuma linha com ponteiro pode sumir da contabilidade."""
    conn = _base()
    _payload(conn, 1, "2026-07-01")
    _payload(conn, 2, "2026-08-28")
    _linha(conn, "PETR4", "2026-07-06", 1)      # rastreada
    _linha(conn, "VALE3", "2026-07-06", 2)      # impossivel
    _linha(conn, "ITUB4", "2026-07-06", 77)     # orfa
    _linha(conn, "BBAS3", "2026-07-06", None)   # sem ponteiro
    c = lineage_counts(conn, "market.income_statements")
    assert c["rows"] == 4 and c["pointer_rows"] == 3
    assert c["traced_rows"] + c["impossible_rows"] + c["dangling_rows"] == 3


def test_trimestral_nao_entra_na_contagem_anual():
    conn = _base()
    _payload(conn, 1, "2026-07-01")
    conn.execute(text("INSERT INTO market.income_statements "
                      "VALUES ('PETR4','quarterly','2026-07-06',1)"))
    assert lineage_counts(conn, "market.income_statements")["rows"] == 0


# ── INF-A2: a tabela de payloads pode sair do banco conectado ────────────────
import json  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

import pytest  # noqa: E402

from core import b3_validation as bv  # noqa: E402


def _sem_payloads():
    """Banco como o Supabase depois do DROP: demonstrações sem a tabela bruta."""
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    conn = engine.connect()
    conn.execute(text("ATTACH DATABASE ':memory:' AS market"))
    conn.execute(text("""
        CREATE TABLE market.income_statements (
            ticker TEXT, period TEXT, first_seen_at TEXT, raw_payload_id INTEGER)"""))
    _linha(conn, "PETR4", "2026-07-06", 10)
    _linha(conn, "VALE3", "2026-07-06", None)
    _linha(conn, "ITUB4", "2026-07-06", 11)
    conn.execute(text(
        "INSERT INTO market.income_statements VALUES ('WEGE3','quarterly','2026-07-06',12)"))
    return conn


def _artefato(tmp_path, *, gerado_em="2026-10-01T03:00:00+00:00", tabelas=None, schema=None):
    caminho = tmp_path / "b3_linhagem.json"
    caminho.write_text(json.dumps({
        "schema": schema or bv.LINHAGEM_SCHEMA,
        "gerado_em": gerado_em,
        "base": "armazém local (dfu_warehouse)",
        "tabelas": tabelas if tabelas is not None else {
            "market.income_statements": {
                "rows": 6081, "traced_rows": 89, "pointer_rows": 5379,
                "dangling_rows": 0, "impossible_rows": 5290}},
    }), encoding="utf-8")
    return caminho


AGORA = datetime(2026, 10, 4, tzinfo=timezone.utc)


def test_tabela_viva_diz_que_mediu_no_banco():
    conn = _base()
    _payload(conn, 10, "2026-07-01")
    _linha(conn, "PETR4", "2026-07-06", 10)
    assert lineage_counts(conn, "market.income_statements")["verificacao"] == "banco"


def test_sem_payloads_nem_artefato_nao_chama_ninguem_de_orfao(tmp_path):
    """Sem como verificar, os baldes ficam None: nem zero rastreadas nem tudo órfão."""
    conn = _sem_payloads()
    c = lineage_counts(conn, "market.income_statements",
                       artefato=tmp_path / "nao_existe.json", agora=AGORA)
    assert c["verificacao"] == "indisponivel"
    assert c["rows"] == 3 and c["pointer_rows"] == 2      # trimestral fora
    assert c["traced_rows"] is None
    assert c["dangling_rows"] is None and c["impossible_rows"] is None
    assert "brapi_raw_payloads" in c["motivo"]
    assert "publish_b3_linhagem" in c["motivo"]


def test_sem_payloads_usa_a_contagem_publicada_do_armazem(tmp_path):
    conn = _sem_payloads()
    c = lineage_counts(conn, "market.income_statements",
                       artefato=_artefato(tmp_path), agora=AGORA)
    assert c["verificacao"] == "artefato_armazem"
    assert (c["traced_rows"], c["dangling_rows"], c["impossible_rows"]) == (89, 0, 5290)
    assert c["rows"] == 6081 and c["pointer_rows"] == 5379
    # a medida é do armazém; as linhas deste banco seguem ao lado, sem misturar
    assert c["rows_banco_atual"] == 3 and c["pointer_rows_banco_atual"] == 2
    assert c["gerado_em"] == "2026-10-01T03:00:00+00:00"
    assert c["idade_dias"] == pytest.approx(2.9, abs=0.1)
    assert c["velho"] is False


def test_contagem_publicada_velha_vai_marcada(tmp_path):
    conn = _sem_payloads()
    c = lineage_counts(conn, "market.income_statements",
                       artefato=_artefato(tmp_path, gerado_em="2026-09-01T03:00:00+00:00"),
                       agora=AGORA)
    assert c["verificacao"] == "artefato_armazem" and c["velho"] is True


@pytest.mark.parametrize("kw", [
    {"schema": "outra.v9"},
    {"tabelas": {}},
    {"gerado_em": "ontem"},
])
def test_artefato_ruim_vira_indisponivel(tmp_path, kw):
    conn = _sem_payloads()
    c = lineage_counts(conn, "market.income_statements",
                       artefato=_artefato(tmp_path, **kw), agora=AGORA)
    assert c["verificacao"] == "indisponivel" and c["traced_rows"] is None


def test_artefato_sem_a_tabela_pedida_diz_o_motivo(tmp_path):
    conn = _sem_payloads()
    outro = {"market.balance_sheets": {"rows": 1, "traced_rows": 1, "pointer_rows": 1,
                                       "dangling_rows": 0, "impossible_rows": 0}}
    c = lineage_counts(conn, "market.income_statements",
                       artefato=_artefato(tmp_path, tabelas=outro), agora=AGORA)
    assert c["verificacao"] == "indisponivel"
    assert "não traz market.income_statements" in c["motivo"]


def test_artefato_versionado_no_repositorio_e_legivel():
    """O arquivo que a rotina commita precisa abrir com o leitor do app."""
    lido = bv.ler_artefato_linhagem(bv.ARTEFATO_LINHAGEM)
    assert lido is not None
    assert set(lido["tabelas"]) <= {
        "market.income_statements", "market.balance_sheets",
        "market.cash_flow_statements"}
    for medida in lido["tabelas"].values():
        assert medida["traced_rows"] + medida["dangling_rows"] \
            + medida["impossible_rows"] == medida["pointer_rows"]


def test_publicador_mede_e_recusa_armazem_sem_payloads():
    from scripts.publish_b3_linhagem import medir

    class _Conn:
        """Só o que `medir` precisa para descobrir que não há payloads."""
        dialect = type("D", (), {"name": "postgresql"})()

        def execute(self, *_a, **_k):
            return type("R", (), {"scalar": staticmethod(lambda: False)})()

    with pytest.raises(LookupError, match="brapi_raw_payloads"):
        medir(_Conn())


def test_manifesto_nao_cai_quando_os_payloads_saem(monkeypatch, tmp_path):
    """Antes, a junção com a tabela ausente derrubava o manifesto inteiro, e com
    ele a validação B3 das telas Empresas B3, Confiança e Portfólio B3."""
    from sqlalchemy import event
    from sqlalchemy import inspect as _inspect

    # SQLite em memória reaproveita a mesma conexão (SingletonThreadPool).
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)

    @event.listens_for(engine, "connect")
    def _anexa(dbapi_conn, _rec):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS market")

    with engine.begin() as conn:
        conn.execute(text("""CREATE TABLE market.assets (
            is_active BOOLEAN, asset_type TEXT, company_id INTEGER)"""))
        conn.execute(text("INSERT INTO market.assets VALUES (1,'stock',1)"))
        conn.execute(text("""CREATE TABLE market.income_statements (
            ticker TEXT, period TEXT, first_seen_at TEXT, raw_payload_id INTEGER)"""))
        _linha(conn, "PETR4", "2026-07-06", 10)

    def _existe(conn, table):
        schema, nome = table.split(".")
        return _inspect(conn).has_table(nome, schema=schema)

    monkeypatch.setattr(bv, "_table_exists", _existe)
    monkeypatch.setattr(bv, "_survivorship_status", lambda: {"strict_available": False})
    monkeypatch.setattr(bv, "ARTEFATO_LINHAGEM", tmp_path / "nao_existe.json")
    m = bv.build_data_manifest(engine)
    assert m["universe"] == 1
    assert m["lineage"]["income"]["verificacao"] == "indisponivel"
    assert m["lineage"]["income"]["pointer_rows"] == 1
