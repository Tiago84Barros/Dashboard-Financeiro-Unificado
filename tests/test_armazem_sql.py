"""Rota ``POST /sql`` do túnel e o cliente ``consultar_sql``.

Nada aqui toca o Postgres: a rota sobe de verdade em 127.0.0.1, com
``executar`` trocado por um dublê. O que se prende é o que abriria o armazém
(SQL que não é SELECT, papel ausente caindo para o superusuário, escrita
escondida) ou faria a resposta chegar diferente da leitura direta (tipos).
A barreira de verdade -- o papel ``app4_leitura`` sem acesso a ``public`` --
foi medida no armazém em 18/11/2026 e está descrita em
``scripts/criar_papel_leitura_armazem.py``; o teste dela é o de ``comandos``.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys
import threading
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

_RAIZ = pathlib.Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

import core.armazem_remoto as ar  # noqa: E402
import core.armazem_sql as asql  # noqa: E402
from scripts import criar_papel_leitura_armazem as papel  # noqa: E402
from scripts import servir_armazem_leitura as srv  # noqa: E402

TOKEN = "t" * 40


# ── validar_sql ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("sql", [
    "select 1",
    "  SELECT * FROM market.assets WHERE ticker = :t;",
    "with x as (select 1) select * from x",
    "select ';' as ponto_e_virgula_no_literal",
    "select 1 -- comentário; com ponto e vírgula",
    "select 1 /* ; */",
])
def test_sql_aceito(sql):
    assert asql.validar_sql(sql) == sql.strip().rstrip(";")


@pytest.mark.parametrize("sql,motivo", [
    ("", "informe"),
    (None, "informe"),
    ("insert into market.assets values (1)", "só SELECT"),
    ("delete from market.assets", "só SELECT"),
    ("-- select\ndrop table x", "só SELECT"),
    ("select 1; drop table market.assets", "um comando"),
    ("select 1;/* x */ delete from x", "um comando"),
    ("select 'a'; select 2", "um comando"),
    ("x" * (asql.SQL_MAX_CARACTERES + 1), "caracteres"),
])
def test_sql_recusado(sql, motivo):
    with pytest.raises(asql.ConsultaRecusada, match=motivo):
        asql.validar_sql(sql)


def test_params():
    assert asql.validar_params(None) == {}
    assert asql.validar_params({"t": ["A", "B"], "n": 3, "d": None}) == {
        "t": ["A", "B"], "n": 3, "d": None}
    for ruim in ([1], {"a b": 1}, {"x": {"y": 1}}, {"x": [[1]]},
                 {"x": list(range(asql.LISTA_MAX + 1))},
                 {f"p{i}": 1 for i in range(asql.PARAMS_MAX + 1)}):
        with pytest.raises(asql.ConsultaRecusada):
            asql.validar_params(ruim)


def test_params_para_json_aceito_pelo_servidor():
    entrada = {"d": dt.date(2026, 11, 13), "ts": pd.Timestamp("2026-11-13 10:00"),
               "n": np.int64(7), "f": np.float64(1.5), "dec": Decimal("2.5"),
               "lista": ("PETR4", "VALE3"), "nada": None}
    saida = asql.params_para_json(entrada)
    assert saida == {"d": "2026-11-13", "ts": "2026-11-13T10:00:00", "n": 7,
                     "f": 1.5, "dec": 2.5, "lista": ["PETR4", "VALE3"], "nada": None}
    assert asql.validar_params(saida) == saida


# ── montar_frame: o mesmo DataFrame que a leitura direta ─────────────────────

def test_montar_frame_remonta_os_tipos():
    corpo = {"colunas": ["ticker", "date", "close", "updated_at", "n"],
             "tipos": [25, 1082, 1700, 1184, 20],
             "linhas": [["PETR4", "2026-11-13", "45.30", "2026-11-14T03:00:00+00:00", 1],
                        ["PETR4", "2026-11-12", None, None, 2]]}
    f = asql.montar_frame(corpo)
    assert f["date"].tolist() == [dt.date(2026, 11, 13), dt.date(2026, 11, 12)]
    assert f["close"].dtype == float and np.isnan(f["close"].iloc[1])
    assert str(f["updated_at"].dtype) == "datetime64[ns, UTC]"
    assert f["n"].tolist() == [1, 2]


def test_montar_frame_carimbo_com_e_sem_fracao_de_segundo():
    """Medido no túnel em 18/11/2026: a 1ª linha sem fração fazia o pandas
    recusar a seguinte, e o loader caía na cópia congelada do Supabase."""
    corpo = {"colunas": ["updated_at", "criado"], "tipos": [1184, 1114],
             "linhas": [["2026-10-04T17:26:23+00:00", "2026-10-04T17:26:23"],
                        ["2026-10-04T17:26:23.875634+00:00", "2026-10-04T17:26:23.5"]]}
    f = asql.montar_frame(corpo)
    assert f["updated_at"].iloc[1] == pd.Timestamp("2026-10-04 17:26:23.875634", tz="UTC")
    assert f["criado"].iloc[1] == pd.Timestamp("2026-10-04 17:26:23.5")


def test_montar_frame_vazio_mantem_colunas():
    f = asql.montar_frame({"colunas": ["a", "b"], "tipos": [25, 1082], "linhas": []})
    assert f.empty and list(f.columns) == ["a", "b"]


# ── rota e cliente, pelo servidor de verdade ────────────────────────────────

@pytest.fixture
def servidor(monkeypatch):
    ar._limpar_memoria()
    http = srv.ThreadingHTTPServer(("127.0.0.1", 0), srv.fabricar_handler(TOKEN))
    threading.Thread(target=http.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{http.server_address[1]}"
    monkeypatch.setattr(ar, "_config", lambda: (url, TOKEN))
    yield url
    http.shutdown()
    http.server_close()
    ar._limpar_memoria()


def _com_papel(monkeypatch, executar):
    monkeypatch.setattr(srv, "_url_sql", lambda: "postgresql://app4_leitura@x/db")
    monkeypatch.setattr(srv, "engine_leitura", lambda url: url)
    monkeypatch.setattr(asql, "executar", executar)


def test_ida_e_volta_entrega_o_mesmo_frame(servidor, monkeypatch):
    vistos = {}

    def _executar(engine, sql, params, **_):
        vistos.update(engine=engine, sql=sql, params=params)
        return {"colunas": ["ticker", "date", "close"], "tipos": [25, 1082, 1700],
                "linhas": [["PETR4", dt.date(2026, 11, 13), Decimal("45.30")]]}

    _com_papel(monkeypatch, _executar)
    f = ar.consultar_sql("select ticker, date, close from market.historical_prices "
                         "where ticker = any(:t) and date >= :d;",
                         {"t": ["PETR4"], "d": dt.date(2026, 11, 1)})
    assert vistos["engine"] == "postgresql://app4_leitura@x/db"
    assert vistos["params"] == {"t": ["PETR4"], "d": "2026-11-01"}
    assert not vistos["sql"].endswith(";")
    assert f.to_dict("records") == [
        {"ticker": "PETR4", "date": dt.date(2026, 11, 13), "close": 45.3}]


def test_sem_papel_restrito_e_503_e_nunca_o_superusuario(servidor, monkeypatch):
    monkeypatch.setattr(srv, "_url_sql", lambda: "")
    chamado = []
    monkeypatch.setattr(asql, "executar", lambda *a, **k: chamado.append(1))
    with pytest.raises(ar.ArmazemRemotoIndisponivel, match="HTTP 503.*ARMAZEM_SQL_DB_URL"):
        ar.consultar_sql("select 1")
    assert chamado == []


def test_url_do_papel_nao_cai_para_a_do_superusuario(monkeypatch):
    import core.config as cfg

    monkeypatch.setattr(cfg, "_get_secret",
                        lambda k, *a, **kw: "" if k == "ARMAZEM_SQL_DB_URL" else "postgresql://postgres@x/db")
    assert srv._url_sql() == ""


def test_escrita_e_recusada_antes_do_banco(servidor, monkeypatch):
    chamado = []
    _com_papel(monkeypatch, lambda *a, **k: chamado.append(1))
    with pytest.raises(ar.ArmazemRemotoIndisponivel, match="HTTP 400: só SELECT") as exc:
        ar.consultar_sql("delete from market.assets")
    assert exc.value.fora_do_ar is False
    assert chamado == []


def test_teto_de_linhas_e_413_sem_pausar(servidor, monkeypatch):
    def _estoura(*_a, **_k):
        raise asql.ConsultaRecusada("a consulta passa de 10 linhas; filtre mais")

    _com_papel(monkeypatch, _estoura)
    with pytest.raises(ar.ArmazemRemotoIndisponivel, match="HTTP 413") as exc:
        ar.consultar_sql("select 1")
    assert exc.value.fora_do_ar is False
    assert ar._falhas == {}


def test_erro_do_banco_no_pedido_e_400(servidor, monkeypatch):
    from sqlalchemy.exc import ProgrammingError

    class _SemPermissao(Exception):
        pass

    def _negado(*_a, **_k):
        raise ProgrammingError("select", {}, _SemPermissao(
            "permission denied for table transactions\nDETAIL"))

    _com_papel(monkeypatch, _negado)
    with pytest.raises(ar.ArmazemRemotoIndisponivel,
                       match="HTTP 400: ProgrammingError: permission denied for table transactions$"):
        ar.consultar_sql("select * from public.transactions")


def test_banco_fora_e_503_e_pausa(servidor, monkeypatch):
    def _cai(*_a, **_k):
        raise RuntimeError("conexão recusada")

    _com_papel(monkeypatch, _cai)
    with pytest.raises(ar.ArmazemRemotoIndisponivel, match="HTTP 503"):
        ar.consultar_sql("select 1")
    with pytest.raises(ar.ArmazemRemotoIndisponivel, match="nova tentativa"):
        ar.consultar_sql("select 1")


def test_corpo_invalido(servidor):
    import requests

    cab = {"Authorization": f"Bearer {TOKEN}"}
    r = requests.post(f"{servidor}/sql", data=b"nao e json", headers=cab, timeout=5)
    assert r.status_code == 400
    r = requests.post(f"{servidor}/sql", data=b"x" * (srv.CORPO_MAX_BYTES + 1),
                      headers=cab, timeout=5)
    assert r.status_code == 413
    r = requests.post(f"{servidor}/sql", json={"sql": "select 1"}, timeout=5)
    assert r.status_code == 401


def test_nao_configurado_e_none(monkeypatch):
    monkeypatch.setattr(ar, "_config", lambda: ("", ""))
    assert ar.consultar_sql("select 1") is None


# ── o papel: a barreira de verdade ───────────────────────────────────────────

def test_papel_so_le_mercado_e_setores():
    cmds = papel.comandos("segredo", "postgres")
    texto = "\n".join(cmds).lower()
    assert "nosuperuser" in texto and "nobypassrls" in texto
    assert "default_transaction_read_only = on" in texto
    concessoes = [c for c in cmds if c.startswith("GRANT SELECT")]
    assert concessoes == [
        "GRANT SELECT ON ALL TABLES IN SCHEMA market TO app4_leitura",
        "GRANT SELECT ON ALL TABLES IN SCHEMA market_us TO app4_leitura",
        "GRANT SELECT ON public.setores TO app4_leitura",
    ]
    assert not any(" insert" in c.lower() or "update" in c.lower().split("set")[0]
                   for c in cmds if c.startswith("GRANT"))


def test_gravar_linha_troca_sem_duplicar(tmp_path):
    env = tmp_path / ".env"
    env.write_text("A=1\nARMAZEM_SQL_DB_URL=velha\nB=2\n", encoding="utf-8")
    papel.gravar_linha(env, "ARMAZEM_SQL_DB_URL", "nova")
    assert env.read_text(encoding="utf-8").splitlines() == [
        "A=1", "B=2", "ARMAZEM_SQL_DB_URL=nova"]
