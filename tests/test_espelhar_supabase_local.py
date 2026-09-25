"""Espelho Supabase -> armazém local: as partes que não tocam banco.

O que se prende aqui é o que já quebrou ou apagaria dado sem avisar: o
meta-comando ``\\restrict`` do pg_dump 17.6+ derrubando o DDL inteiro, política
de RLS e FK para ``auth.`` chegando a um banco que não as tem, e linha que só
existe no local sendo apagada pelo espelho.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

_RAIZ = pathlib.Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

import scripts.espelhar_supabase_local as esp  # noqa: E402

_DUMP = """--
-- PostgreSQL database dump
--
\\restrict AbC123
SET statement_timeout = 0;
SELECT pg_catalog.set_config('search_path', '', false);
CREATE TABLE public.budgets (
    id uuid NOT NULL,
    user_id uuid
);
ALTER TABLE public.budgets OWNER TO postgres;
ALTER TABLE ONLY public.budgets
    ADD CONSTRAINT budgets_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.budgets
    ADD CONSTRAINT budgets_user_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id);
CREATE POLICY dono ON public.budgets USING ((user_id = auth.uid()));
ALTER TABLE public.budgets ENABLE ROW LEVEL SECURITY;
COMMENT ON TABLE public.budgets IS 'x';
\\unrestrict AbC123
"""


def test_limpar_ddl_tira_meta_comando_e_o_que_so_existe_no_supabase():
    inst = esp.limpar_ddl(_DUMP)
    assert inst[0].startswith("CREATE TABLE public.budgets")
    assert any("budgets_pkey PRIMARY KEY" in i for i in inst)
    texto = "\n".join(inst)
    for proibido in ("\\restrict", "\\unrestrict", "auth.", "POLICY", "ROW LEVEL",
                     "OWNER TO", "COMMENT ON", "set_config", "statement_timeout"):
        assert proibido not in texto
    assert len(inst) == 2


@pytest.mark.parametrize("nome", ["budgets; DROP TABLE x", "Budgets", "a.b", ""])
def test_nome_de_tabela_invalido_e_recusado(nome):
    with pytest.raises(ValueError):
        esp._q(nome)


class _R:
    def __init__(self, valor):
        self.valor = valor

    def scalar(self):
        return self.valor

    def __iter__(self):
        return iter(self.valor)


class _Conn:
    """Banco falso: ``tabelas`` = {nome: (colunas, pk, linhas)}."""

    def __init__(self, tabelas):
        self.t = tabelas

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        sql = str(sql)
        nome = (params or {}).get("t", "").replace('public."', "").rstrip('"')
        if "to_regclass(:t)" in sql and "pg_attribute" not in sql:
            return _R(nome if nome in self.t else None)
        if "attisdropped" in sql:
            return _R([(c, "text") for c in self.t[nome][0]])
        if "indisprimary" in sql:
            return _R([(c,) for c in self.t[nome][1]])
        alvo = sql.split("FROM ")[1].replace('public."', "").split('"')[0]
        if "count(*)" in sql:
            return _R(len(self.t[alvo][2]))
        return _R(self.t[alvo][2])


class _Eng:
    def __init__(self, tabelas):
        self.tabelas = tabelas

    def connect(self):
        return _Conn(self.tabelas)


def _diag(monkeypatch, sup, loc, tabelas):
    monkeypatch.setattr(esp, "TABELAS", tuple(tabelas))
    return {d["tabela"]: d for d in esp.diagnosticar(_Eng(sup), _Eng(loc))}


def test_linha_so_no_local_bloqueia_o_espelho(monkeypatch):
    sup = {"budgets": (["id", "valor"], ["id"], [(1,), (2,)])}
    loc = {"budgets": (["id"], ["id"], [(1,), (3,)])}
    d = _diag(monkeypatch, sup, loc, ["budgets"])["budgets"]
    assert d["so_local"] == 1
    assert d["bloqueio"] == "1 linha(s) só no local"
    assert d["colunas_faltam"] == [("valor", "text")]


def test_tabela_com_descarte_declarado_nao_bloqueia(monkeypatch):
    sup = {"portfolio_positions": (["id"], ["id"], [(1,)])}
    loc = {"portfolio_positions": (["id"], ["id"], [(1,), (9,)])}
    d = _diag(monkeypatch, sup, loc, ["portfolio_positions"])["portfolio_positions"]
    assert d["so_local"] == 1 and d["bloqueio"] == ""


def test_sem_pk_com_dado_local_bloqueia_e_ausente_no_supabase_tambem(monkeypatch):
    sup = {"macro": (["ano"], [], [(2025,)])}
    loc = {"macro": (["ano"], [], [(2024,)]), "fantasma": (["id"], ["id"], [])}
    diag = _diag(monkeypatch, sup, loc, ["macro", "fantasma"])
    assert diag["macro"]["bloqueio"] == "sem chave primária para comparar"
    assert diag["fantasma"]["bloqueio"] == "não existe no Supabase"


def test_tabela_ausente_no_local_nao_bloqueia(monkeypatch):
    sup = {"debts": (["id"], ["id"], [(1,)])}
    d = _diag(monkeypatch, sup, {}, ["debts"])["debts"]
    assert d["existe_local"] is False and d["n_local"] is None and d["bloqueio"] == ""


def test_linha_apagada_no_app_sai_sem_bloquear(monkeypatch):
    # A 3 veio do espelho anterior e sumiu do Supabase: foi apagada no app.
    # A 4 nunca veio de lá: foi criada no local e continua bloqueando.
    sup = {"transactions": (["id"], ["id"], [(1,)])}
    loc = {"transactions": (["id"], ["id"], [(1,), (3,), (4,)])}
    monkeypatch.setattr(esp, "_chaves_espelhadas", lambda _c, _t: {1, 3})
    d = _diag(monkeypatch, sup, loc, ["transactions"])["transactions"]
    assert d["apagadas_na_origem"] == 1
    assert d["so_local"] == 1 and d["bloqueio"] == "1 linha(s) só no local"

    monkeypatch.setattr(esp, "_chaves_espelhadas", lambda _c, _t: {1, 3, 4})
    d = _diag(monkeypatch, sup, loc, ["transactions"])["transactions"]
    assert d["apagadas_na_origem"] == 2 and d["bloqueio"] == ""


def test_chave_e_montada_pelo_banco():
    assert esp._expr_chave(["user_id", "data"]) == \
        "concat_ws('|', \"user_id\"::text, \"data\"::text)"


def test_poda_mantem_os_backups_mais_novos(tmp_path):
    for dia in range(1, 6):
        (tmp_path / f"espelho_antes_2026090{dia}_010000.dump").write_bytes(b"x")
    (tmp_path / "fii_warehouse_pre_v690.dump").write_bytes(b"x")
    apagados = esp.podar_backups(tmp_path, manter=2)
    assert len(apagados) == 3
    restantes = sorted(p.name for p in tmp_path.iterdir())
    assert restantes == ["espelho_antes_20260904_010000.dump",
                         "espelho_antes_20260905_010000.dump",
                         "fii_warehouse_pre_v690.dump"]


def test_verificador_do_espelho(monkeypatch):
    from datetime import datetime, timedelta, timezone

    import sqlalchemy

    import scripts.publish_fii_selection_from_local as pub
    import scripts.verificar_frescor_vitrines as ver

    linha = {"v": None}

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, *_a):
            class _R:
                def first(self_inner):
                    return linha["v"]
            return _R()

    class _Eng:
        def connect(self):
            return _Conn()

        def dispose(self):
            pass

    monkeypatch.setattr(pub, "_warehouse_url", lambda: "postgresql://x")
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda _u: _Eng())
    assert "nunca rodou" in ver.verificar_espelho()["detalhe"]

    agora = datetime.now(timezone.utc)
    linha["v"] = (agora - timedelta(days=9), len(esp.TABELAS), 5000)
    assert "acima do limite" in ver.verificar_espelho()["detalhe"]

    linha["v"] = (agora - timedelta(hours=2), len(esp.TABELAS) - 1, 5000)
    assert "tabelas espelhadas" in ver.verificar_espelho()["detalhe"]

    linha["v"] = (agora - timedelta(hours=2), len(esp.TABELAS), 5000)
    assert ver.verificar_espelho()["ok"]
