"""Painel da aba Configuracoes -> Restricoes: leitura do log e decisao do admin."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text

from core.lacunas import painel

AGORA = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)


def _ev(imp, horas_atras, fonte, codigo, modulo, msg="m"):
    return {"ts": (AGORA - timedelta(hours=horas_atras)).isoformat(), "impressao": imp,
            "fonte": fonte, "modulo": modulo, "codigo": codigo, "entidade": "",
            "mensagem": msg, "contexto": {}}


@pytest.fixture
def pasta(tmp_path):
    eventos = [
        _ev("det", 1, "tela", "detalhe.fii.versao", "views/fiis.py:render", "Metodologia 2.1"),
        _ev("res", 5, "tela", "tela.b3.sem_preco", "views/portfolio_b3_safras.py:f"),
        _ev("err", 3, "excecao", "KeyError", "views/configuracoes.py:render"),
        _ev("mot", 2, "motor", "fii.sem_vpa", "core/fii/score.py:g"),
    ]
    (tmp_path / "eventos.jsonl").write_text(
        "\n".join(json.dumps(e) for e in eventos), encoding="utf-8")
    (tmp_path / "estado.json").write_text(json.dumps(
        {"lacunas": {"mot": {"status": "legitima", "nota_triagem": "conhecida"}}}),
        encoding="utf-8")
    return tmp_path


@pytest.fixture
def engine():
    eng = create_engine("sqlite+pysqlite:///:memory:", future=True)
    with eng.begin() as con:
        con.execute(text("CREATE TABLE app_lacunas (impressao TEXT PRIMARY KEY, "
                         "status TEXT, nota_triagem TEXT)"))
        con.execute(text("INSERT INTO app_lacunas VALUES ('res', 'aberta', NULL)"))
    return eng


def test_tela_do_modulo_usa_o_prefixo_mais_longo():
    assert painel.tela_do_modulo("views/portfolio_b3_safras.py:f") == "Empresas B3 · Portfólio"
    assert painel.tela_do_modulo("views/fiis.py:render") == "Seleção de FIIs"
    assert painel.tela_do_modulo("core/fii/score.py:g") == "Motor de cálculo"
    assert painel.tela_do_modulo("design/chat_ativos.py:x") == "Chats com a LLM"
    assert painel.tela_do_modulo(None) == "—"


def test_carregar_classifica_e_poe_detalhe_por_ultimo(pasta):
    p = painel.carregar(pasta=pasta, agora=AGORA, ler_nuvem=False)
    por = {i["impressao"]: i for i in p.itens}
    assert por["det"]["natureza"] == "Detalhe técnico"
    assert por["det"]["status"] == "legitima"
    assert por["err"]["natureza"] == "Erro"
    assert por["res"]["natureza"] == "Restrição" and por["res"]["status"] == "aberta"
    assert por["mot"]["status"] == "legitima"          # estado local vence
    assert por["res"]["tela"] == "Empresas B3 · Portfólio"
    assert p.itens[-1]["impressao"] == "det"
    assert p.fontes["local"].startswith("ok")
    assert "cloud" not in p.fontes


def test_carregar_sem_pasta_nem_banco_nao_levanta(tmp_path):
    def _quebra():
        raise RuntimeError("sem banco")
    p = painel.carregar(engine=type("E", (), {"connect": staticmethod(_quebra)})(),
                        pasta=tmp_path / "nao_existe", agora=AGORA)
    assert p.itens == []
    assert p.fontes["local"] == "sem log local"
    assert p.fontes["cloud"].startswith("indisponivel")


def test_decidir_grava_na_nuvem_e_no_estado_local(pasta, engine):
    r = painel.decidir("res", "legitima", "fonte so publica mensal",
                       engine=engine, pasta=pasta, agora=AGORA)
    assert r == {"cloud": "ok", "local": "ok"}
    with engine.connect() as con:
        status, nota = con.execute(text(
            "SELECT status, nota_triagem FROM app_lacunas WHERE impressao='res'")).one()
    assert status == "legitima"
    assert nota == "[admin 2026-10-05] fonte so publica mensal"
    est = json.loads((pasta / "estado.json").read_text(encoding="utf-8"))["lacunas"]
    assert est["res"]["status"] == "legitima" and est["res"]["nota_triagem"] == nota
    assert est["mot"]["status"] == "legitima"          # o resto do estado fica


def test_decidir_item_que_so_existe_localmente(pasta, engine):
    r = painel.decidir("err", "resolvida", None, engine=engine, pasta=pasta, agora=AGORA)
    assert r == {"cloud": "sem linha na nuvem", "local": "ok"}
    est = json.loads((pasta / "estado.json").read_text(encoding="utf-8"))["lacunas"]
    assert est["err"]["status"] == "resolvida" and est["err"]["resolvida_em"]
    assert est["err"]["nota_triagem"] == "[admin 2026-10-05] sem observacao"


def test_decidir_na_cloud_sem_estado_local_nao_cria_arquivo(tmp_path, engine):
    r = painel.decidir("res", "incerta", "x", engine=engine, pasta=tmp_path, agora=AGORA)
    assert r == {"cloud": "ok"}
    assert not (tmp_path / "estado.json").exists()


def test_em_pr_nao_e_decisao_da_tela(pasta, engine):
    with pytest.raises(ValueError):
        painel.decidir("res", "em_pr", engine=engine, pasta=pasta, agora=AGORA)
