"""Sincronizador de lacunas: fusao local + nuvem, janela, estados e rotacao."""
import gzip
import json
from argparse import Namespace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text

from core.lacunas import fila
from scripts import lacunas_sincronizar as sinc

AGORA = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)


def _ev(imp, dias_atras, fonte="motor", msg="m", **kw):
    ts = (AGORA - timedelta(days=dias_atras)).isoformat()
    return {"ts": ts, "impressao": imp, "fonte": fonte, "modulo": "core/x.py:f",
            "codigo": kw.get("codigo", "c"), "entidade": kw.get("entidade", ""),
            "mensagem": msg, "contexto": {}}


def _nuvem(imp, ocorrencias, primeira, ultima, fonte="motor", **kw):
    return {"impressao": imp, "fonte": fonte, "modulo": "core/y.py:g", "codigo": "c",
            "entidade": "", "ultima_mensagem": kw.get("msg", "nuvem"), "contexto": {},
            "primeira_vez": (AGORA - timedelta(days=primeira)).isoformat(),
            "ultima_vez": (AGORA - timedelta(days=ultima)).isoformat(),
            "ocorrencias": ocorrencias, "status": kw.get("status", "aberta"),
            "reincidente": False, "pr_url": kw.get("pr_url"), "nota_triagem": None}


# ── agregacao e janela ───────────────────────────────────────────────────────

def test_agrega_por_impressao_e_conta_so_a_janela():
    local = fila.agregar_eventos([_ev("a", 1, msg="novo"), _ev("a", 20, msg="velho"),
                                  _ev("b", 30)], AGORA)
    assert local["a"]["ocorrencias"] == 2
    assert local["a"]["ocorrencias_janela"] == 1
    assert local["a"]["ultima_mensagem"] == "novo"
    assert local["b"]["ocorrencias_janela"] == 0


def test_linha_sem_data_ou_impressao_e_descartada():
    assert fila.agregar_eventos([{"impressao": "a", "ts": "lixo"}, {"ts": AGORA.isoformat()}],
                                AGORA) == {}


def test_nuvem_usa_delta_desde_a_foto_anterior_a_janela():
    linha = _nuvem("a", 410, primeira=200, ultima=1)
    fotos = [["2026-03-01", 100], ["2026-09-10", 400], ["2026-09-20", 405]]
    assert fila.ocorrencias_na_janela(linha, fotos, AGORA) == (10, False)


def test_nuvem_sem_foto_antiga_usa_o_total_e_avisa_que_aproximou():
    assert fila.ocorrencias_na_janela(_nuvem("a", 400, 200, 1), [], AGORA) == (400, True)


def test_nuvem_nascida_dentro_da_janela_e_exata():
    assert fila.ocorrencias_na_janela(_nuvem("a", 7, 5, 1), [], AGORA) == (7, False)


def test_nuvem_sem_ocorrencia_recente_nao_conta():
    assert fila.ocorrencias_na_janela(_nuvem("a", 400, 200, 30), [], AGORA) == (0, False)


def test_foto_do_dia_e_substituida_e_nao_duplicada():
    fotos = fila.registrar_foto({"a": [["2026-09-28", 3]]}, [_nuvem("a", 5, 9, 1)], AGORA)
    assert fotos == {"a": [["2026-09-28", 5]]}


# ── fusao e prioridade ───────────────────────────────────────────────────────

def test_fusao_soma_e_marca_a_origem():
    local = fila.agregar_eventos([_ev("a", 1), _ev("a", 2), _ev("b", 1)], AGORA)
    itens = {i["impressao"]: i for i in
             fila.fundir(local, [_nuvem("a", 3, 5, 0), _nuvem("c", 1, 3, 3)], {}, {}, AGORA)}
    assert itens["a"]["origem"] == "ambos"
    assert itens["a"]["ocorrencias"] == 5
    assert itens["a"]["ocorrencias_14d"] == 5
    assert itens["a"]["ultima_mensagem"] == "nuvem"  # a nuvem viu por ultimo
    assert itens["b"]["origem"] == "local"
    assert itens["c"]["origem"] == "cloud"


def test_prioridade_pesa_a_fonte_e_reduz_incerta():
    local = fila.agregar_eventos(
        [_ev("exc", 1, fonte="excecao")] * 2 + [_ev("llm", 1, fonte="llm")] * 5
        + [_ev("inc", 1)] * 4, AGORA)
    itens = fila.fundir(local, [], {"inc": {"status": "incerta"}}, {}, AGORA)
    prio = {i["impressao"]: i["prioridade"] for i in itens}
    assert prio == {"exc": 6, "llm": 5, "inc": 4.0}
    assert [i["impressao"] for i in fila.fila(itens)] == ["exc", "llm", "inc"]


def test_fila_exclui_triadas_e_sem_ocorrencia_recente():
    local = fila.agregar_eventos([_ev("a", 1), _ev("b", 1), _ev("c", 1), _ev("velha", 40)],
                                 AGORA)
    estado = {"a": {"status": "legitima"}, "b": {"status": "em_pr", "pr_url": "u"}}
    assert [i["impressao"] for i in fila.fila(fila.fundir(local, [], estado, {}, AGORA))] == ["c"]


def test_estado_local_vence_o_status_da_nuvem():
    itens = fila.fundir({}, [_nuvem("a", 1, 1, 1, status="aberta")],
                        {"a": {"status": "legitima", "nota_triagem": "ok"}}, {}, AGORA)
    assert itens[0]["status"] == "legitima"
    diffs = fila.diferencas_para_cloud(itens, [_nuvem("a", 1, 1, 1)])
    assert diffs == [{"impressao": "a", "status": "legitima", "pr_url": None,
                      "nota_triagem": "ok", "reincidente": False}]


def test_lacuna_so_local_nao_sobe_para_a_nuvem():
    itens = fila.fundir(fila.agregar_eventos([_ev("a", 1)], AGORA), [],
                        {"a": {"status": "legitima"}}, {}, AGORA)
    assert fila.diferencas_para_cloud(itens, []) == []


# ── transicoes ───────────────────────────────────────────────────────────────

def test_resolvida_que_reaparece_reabre_como_reincidente():
    estado = {"a": {"status": "resolvida", "resolvida_em": (AGORA - timedelta(days=3)).isoformat()}}
    itens = fila.fundir(fila.agregar_eventos([_ev("a", 1)], AGORA), [], estado, {}, AGORA)
    assert (itens[0]["status"], itens[0]["reincidente"]) == ("aberta", True)
    novo = fila.atualizar_estado(estado, itens)
    assert novo["a"]["status"] == "aberta" and novo["a"]["reincidente"]


def test_resolvida_sem_ocorrencia_nova_continua_resolvida():
    estado = {"a": {"status": "resolvida", "resolvida_em": (AGORA - timedelta(days=1)).isoformat()}}
    itens = fila.fundir(fila.agregar_eventos([_ev("a", 3)], AGORA), [], estado, {}, AGORA)
    assert itens[0]["status"] == "resolvida"


def test_pr_mergeado_resolve_e_fechado_reabre_com_nota():
    estado = {"m": {"status": "em_pr", "pr_url": "u1"},
              "f": {"status": "em_pr", "pr_url": "u2", "nota_triagem": "defeito: x"},
              "o": {"status": "em_pr", "pr_url": "u3"}}
    respostas = {"u1": "MERGED", "u2": "CLOSED", "u3": "OPEN"}
    assert fila.aplicar_prs(estado, respostas.get, AGORA) == ["m", "f"]
    assert estado["m"]["status"] == "resolvida"
    assert estado["f"]["status"] == "aberta"
    assert estado["f"]["pr_url"] is None
    assert estado["f"]["tentativas"][0]["nota_triagem"] == "defeito: x"
    assert "u2" in estado["f"]["nota_triagem"]
    assert estado["o"]["status"] == "em_pr"


def test_gh_indisponivel_nao_muda_nada():
    estado = {"a": {"status": "em_pr", "pr_url": "u"}}
    assert fila.aplicar_prs(estado, lambda url: None, AGORA) == []
    assert estado["a"]["status"] == "em_pr"


def test_marcar_valida_status_e_exige_pr_para_em_pr():
    with pytest.raises(ValueError):
        fila.marcar({}, "a", "inventado", agora=AGORA)
    with pytest.raises(ValueError):
        fila.marcar({}, "a", "em_pr", agora=AGORA)


def test_prefixo_ambiguo_e_rejeitado():
    assert fila.resolver_prefixo("ab", ["abc", "xyz"]) == "abc"
    with pytest.raises(ValueError):
        fila.resolver_prefixo("a", ["abc", "abd"])


# ── rotacao ──────────────────────────────────────────────────────────────────

def test_rotacao_move_meses_anteriores_mesmo_fora_do_dia_1(tmp_path):
    eventos = [_ev("a", 40), _ev("b", 60), _ev("c", 1)]
    (tmp_path / "eventos.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in eventos), encoding="utf-8")
    assert sinc.rotacionar(tmp_path, AGORA) == ["2026-07", "2026-08"]
    restantes = sinc._ler_jsonl(tmp_path / "eventos.jsonl")
    assert [e["impressao"] for e in restantes] == ["c"]
    with gzip.open(tmp_path / "eventos-2026-08.jsonl.gz", "rt", encoding="utf-8") as fh:
        assert [json.loads(linha)["impressao"] for linha in fh] == ["a"]
    # o historico rotacionado continua contando no total
    assert {e["impressao"] for e in sinc.eventos_locais(tmp_path)} == {"a", "b", "c"}


# ── ponta a ponta com SQLite no lugar do Supabase ────────────────────────────

@pytest.fixture
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'nuvem.db'}", future=True)
    with eng.begin() as con:
        con.execute(text("""
            CREATE TABLE app_lacunas (
                impressao TEXT PRIMARY KEY, fonte TEXT, modulo TEXT, codigo TEXT,
                entidade TEXT, ultima_mensagem TEXT, contexto TEXT,
                primeira_vez TEXT, ultima_vez TEXT, ocorrencias INTEGER,
                status TEXT, reincidente BOOLEAN, pr_url TEXT, nota_triagem TEXT)
        """))
        linha = _nuvem("nuv", 4, 3, 1, pr_url="https://gh/pr/9", status="em_pr")
        linha["contexto"] = "{}"
        con.execute(text("INSERT INTO app_lacunas VALUES (:impressao, :fonte, :modulo, "
                         ":codigo, :entidade, :ultima_mensagem, :contexto, :primeira_vez, "
                         ":ultima_vez, :ocorrencias, :status, :reincidente, :pr_url, "
                         ":nota_triagem)"), linha)
    return eng


def test_sincronizar_ponta_a_ponta(tmp_path, engine):
    pasta = tmp_path / "lacunas"
    pasta.mkdir()
    (pasta / "eventos.jsonl").write_text(json.dumps(_ev("loc", 1)) + "\n", encoding="utf-8")
    (pasta / "estado.json").write_text(json.dumps(
        {"lacunas": {"nuv": {"status": "em_pr", "pr_url": "https://gh/pr/9"}}}),
        encoding="utf-8")

    r = sinc.sincronizar(pasta, agora=AGORA, engine_factory=lambda: engine,
                         consultar=lambda url: "MERGED")

    assert r["prs_atualizados"] == ["nuv"]
    abertas = json.loads((pasta / "abertas.json").read_text(encoding="utf-8"))
    assert [i["impressao"] for i in abertas["fila"]] == ["loc"]
    assert abertas["fontes"]["cloud"] == "ok (1 lacunas)"
    assert abertas["por_status"]["resolvida"] == 1
    with engine.connect() as con:
        assert con.execute(text("SELECT status FROM app_lacunas")).scalar() == "resolvida"
    estado = json.loads((pasta / "estado.json").read_text(encoding="utf-8"))
    assert estado["fotos_cloud"]["nuv"] == [["2026-09-28", 4]]
    assert (pasta / "resumo-semanal.md").exists()


def test_sem_nuvem_a_fila_sai_so_com_o_local_e_diz_isso(tmp_path):
    pasta = tmp_path / "lacunas"
    pasta.mkdir()
    (pasta / "eventos.jsonl").write_text(json.dumps(_ev("loc", 1)) + "\n", encoding="utf-8")

    def falha():
        raise ConnectionError("sem rede")

    sinc.sincronizar(pasta, agora=AGORA, engine_factory=falha, consultar=lambda u: None)
    abertas = json.loads((pasta / "abertas.json").read_text(encoding="utf-8"))
    assert abertas["fontes"]["cloud"] == "indisponivel: ConnectionError"
    assert [i["impressao"] for i in abertas["fila"]] == ["loc"]


def test_marcar_pelo_prefixo_grava_no_estado(tmp_path):
    (tmp_path / "abertas.json").write_text(json.dumps({"fila": [{"impressao": "abcdef123"}]}),
                                           encoding="utf-8")
    args = Namespace(impressao="abcd", status="legitima", pr_url=None, nota="aviso correto")
    assert sinc.cmd_marcar(args, pasta=tmp_path, agora=AGORA) == "abcdef123"
    estado = json.loads((tmp_path / "estado.json").read_text(encoding="utf-8"))
    assert estado["lacunas"]["abcdef123"]["status"] == "legitima"
    assert estado["lacunas"]["abcdef123"]["nota_triagem"] == "aviso correto"


def test_resumo_lista_legitimas_e_reincidentes():
    local = fila.agregar_eventos([_ev("leg", 1, msg="sem VPA"), _ev("rei", 1)], AGORA)
    estado = {"leg": {"status": "legitima", "nota_triagem": "fundo novo"},
              "rei": {"reincidente": True}}
    md = fila.resumo_semanal(fila.fundir(local, [], estado, {}, AGORA), AGORA)
    legitimas, reincidentes = md.split("## Reincidentes")
    assert "`leg`" in legitimas and "fundo novo" in legitimas and "sem VPA" in legitimas
    assert "`rei`" in reincidentes and "`leg`" not in reincidentes


# ── detalhe tecnico e decisao do administrador (05/10/2026) ──────────────────

def test_detalhe_tecnico_nasce_legitimo_na_fusao_e_fica_fora_da_fila():
    local = fila.agregar_eventos([_ev("d", 1, fonte="tela", codigo="detalhe.fii.versao"),
                                  _ev("r", 1, fonte="tela", codigo="tela.fii.sem_dados")],
                                 AGORA)
    itens = {i["impressao"]: i for i in fila.fundir(local, [], {}, {}, AGORA)}
    assert itens["d"]["status"] == "legitima"
    assert itens["r"]["status"] == "aberta"
    assert [i["impressao"] for i in fila.fila(list(itens.values()))] == ["r"]


def test_detalhe_resolvido_que_reaparece_volta_a_legitimo():
    estado = {"d": {"status": "resolvida",
                    "resolvida_em": (AGORA - timedelta(days=3)).isoformat()}}
    local = fila.agregar_eventos([_ev("d", 1, codigo="detalhe.x")], AGORA)
    itens = fila.fundir(local, [], estado, {}, AGORA)
    assert (itens[0]["status"], itens[0]["reincidente"]) == ("legitima", True)
    assert fila.atualizar_estado(estado, itens)["d"]["status"] == "legitima"


def test_decisao_do_admin_na_nuvem_entra_no_estado_local():
    estado = {"a": {"status": "aberta", "nota_triagem": None},
              "b": {"status": "aberta", "nota_triagem": "[admin 2026-09-27] x"},
              "c": {"status": "aberta"}}
    cloud = [_nuvem("a", 1, 1, 1, status="legitima"),
             _nuvem("b", 1, 1, 1, status="legitima"),
             _nuvem("c", 1, 1, 1, status="legitima"),
             _nuvem("novo", 1, 1, 1, status="legitima")]
    cloud[0]["nota_triagem"] = "[admin 2026-09-28] limitacao conhecida"
    cloud[1]["nota_triagem"] = "[admin 2026-09-27] x"          # ja importada
    cloud[2]["nota_triagem"] = "triagem do corretor"           # nao e do admin
    cloud[3]["nota_triagem"] = "[admin 2026-09-28] y"          # sem estado local
    mudou = fila.importar_decisoes_admin(estado, cloud, AGORA)
    assert mudou == ["a"]
    assert estado["a"]["status"] == "legitima"
    assert estado["a"]["nota_triagem"].startswith("[admin 2026-09-28]")
    assert estado["b"]["status"] == "aberta" and estado["c"]["status"] == "aberta"
    assert "novo" not in estado


def test_decisao_resolvida_do_admin_marca_a_data():
    estado = {"a": {"status": "aberta"}}
    cloud = [_nuvem("a", 1, 1, 1, status="resolvida")]
    cloud[0]["nota_triagem"] = "[admin 2026-09-28] corrigido no PR"
    fila.importar_decisoes_admin(estado, cloud, AGORA)
    assert estado["a"]["status"] == "resolvida" and estado["a"]["resolvida_em"]
    cloud[0].update(status="aberta", nota_triagem="[admin 2026-09-29] voltou")
    fila.importar_decisoes_admin(estado, cloud, AGORA)
    assert estado["a"]["status"] == "aberta" and "resolvida_em" not in estado["a"]
