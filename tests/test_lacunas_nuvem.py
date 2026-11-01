"""Ponte app_lacunas <-> corretor na nuvem (core/lacunas/nuvem.py)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, InvalidToken

from core.lacunas import fila, nuvem

AGORA = datetime(2026, 11, 2, 9, 23, tzinfo=timezone.utc)
RAIZ = Path(__file__).resolve().parents[1]


def _item(imp, status="aberta", prioridade=4.0, ultima="2026-11-01T10:00:00+00:00", **extra):
    base = {"impressao": imp, "status": status, "prioridade": prioridade,
            "ultima_vez": ultima, "natureza": "Restrição", "tela": "Investimentos",
            "fonte": "tela", "modulo": "views/investimentos.py:_tab", "codigo": "tela.x",
            "ultima_mensagem": "Fora da matriz: CDB8266EXTI", "ocorrencias": 3,
            "ocorrencias_14d": 2}
    base.update(extra)
    return base


def test_fila_exportada_segue_a_ordem_do_corretor_e_corta_a_mensagem():
    itens = [_item("a" * 40, prioridade=1), _item("b" * 40, prioridade=9),
             _item("c" * 40, status="legitima", prioridade=50),
             _item("d" * 40, prioridade=5, ultima_mensagem="x" * 5000)]
    dados = nuvem.montar_fila(itens, {"cloud": "ok (4 registros)"}, AGORA)
    assert [i["impressao"][0] for i in dados["fila"]] == ["b", "d", "a"]
    assert dados["total_na_fila"] == 3
    assert len(dados["fila"][1]["ultima_mensagem"]) == nuvem.LIMITE_MENSAGEM


def test_cifra_vai_e_volta_e_nao_deixa_o_ativo_em_texto_claro():
    chave = Fernet.generate_key().decode()
    dados = nuvem.montar_fila([_item("a" * 40)], {"cloud": "ok"}, AGORA)
    token = nuvem.cifrar(dados, chave)
    assert "CDB8266EXTI" not in token
    assert nuvem.decifrar(token, chave)["fila"][0]["ultima_mensagem"].endswith("CDB8266EXTI")
    with pytest.raises(InvalidToken):
        nuvem.decifrar(token, Fernet.generate_key().decode())


def test_sem_chave_nao_cifra():
    with pytest.raises(ValueError, match=nuvem.VARIAVEL_CHAVE):
        nuvem.cifrar({}, "")


def test_token_sai_do_log_do_actions_com_horario_na_frente():
    chave = Fernet.generate_key().decode()
    velho = nuvem.cifrar({"n": 1}, chave)
    novo = nuvem.cifrar({"n": 2}, chave)
    log = "\n".join(
        f"2026-11-02T09:24:0{n}.1234567Z {linha}"
        for n, linha in enumerate(
            ["fontes: {}", *nuvem.bloco_de_log(velho).splitlines(),
             "re-run", *nuvem.bloco_de_log(novo).splitlines()]))
    assert nuvem.decifrar(nuvem.extrair_token(log), chave) == {"n": 2}
    with pytest.raises(ValueError):
        nuvem.extrair_token("nada aqui")


def test_decisoes_ultima_valida_vence_e_lixo_e_ignorado():
    linhas = [
        json.dumps({"impressao": "a" * 40, "status": "em_pr", "pr": 7, "data": "2026-11-02"}),
        "isto nao e json",
        json.dumps({"impressao": "b" * 40, "status": "aberta", "data": "2026-11-02"}),
        json.dumps({"impressao": "curta", "status": "legitima", "data": "2026-11-02"}),
        json.dumps({"impressao": "a" * 40, "status": "resolvida", "pr": 7,
                    "data": "2026-11-02T12:00:00+00:00"}),
    ]
    decisoes = nuvem.ler_decisoes(linhas)
    assert list(decisoes) == ["a" * 40]
    assert decisoes["a" * 40]["status"] == "resolvida"


def _linha(imp, **extra):
    base = {"impressao": imp, "status": "aberta", "pr_url": None, "nota_triagem": None,
            "reincidente": False, "ultima_vez": "2026-11-02T08:00:00+00:00"}
    base.update(extra)
    return base


def test_aplicar_e_idempotente():
    dec = {"a" * 40: {"impressao": "a" * 40, "status": "legitima", "data": "2026-11-02",
                      "nota": "CDB não tem PU diário em nenhuma base"}}
    [m] = nuvem.diferencas(dec, [_linha("a" * 40)])
    assert m["status"] == "legitima"
    assert m["nota_triagem"].startswith(fila.PREFIXO_NOTA_NUVEM + "2026-11-02]")
    gravada = _linha("a" * 40, **{k: m[k] for k in ("status", "pr_url", "nota_triagem")})
    assert nuvem.diferencas(dec, [gravada]) == []


def test_em_pr_segue_o_pr():
    dec = {"a" * 40: {"impressao": "a" * 40, "status": "em_pr", "pr": 650,
                      "data": "2026-11-02T08:00:00+00:00"}}
    [m] = nuvem.diferencas(dec, [_linha("a" * 40, ultima_vez="2026-11-02T07:00:00+00:00")],
                           consultar_pr=lambda url: "MERGED")
    assert m["status"] == "resolvida" and m["pr_url"].endswith("/pull/650")
    [m] = nuvem.diferencas(dec, [_linha("a" * 40)], consultar_pr=lambda url: "CLOSED")
    assert m["status"] == "aberta" and "fechado sem merge" in m["nota_triagem"]
    [m] = nuvem.diferencas(dec, [_linha("a" * 40)], consultar_pr=lambda url: None)
    assert m["status"] == "em_pr"


def test_resolvida_que_volta_depois_do_deploy_reabre_como_reincidente():
    merge = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)
    dec = {"a" * 40: {"impressao": "a" * 40, "status": "resolvida", "pr": 650,
                      "data": merge.isoformat()}}
    dentro_da_folga = (merge + nuvem.FOLGA_DEPLOY - timedelta(minutes=1)).isoformat()
    [m] = nuvem.diferencas(dec, [_linha("a" * 40, ultima_vez=dentro_da_folga)])
    assert m["status"] == "resolvida"
    depois = (merge + nuvem.FOLGA_DEPLOY + timedelta(hours=1)).isoformat()
    [m] = nuvem.diferencas(dec, [_linha("a" * 40, ultima_vez=depois)])
    assert m["status"] == "aberta" and m["reincidente"] is True
    reaberta = _linha("a" * 40, ultima_vez=depois,
                      **{k: m[k] for k in ("status", "pr_url", "nota_triagem", "reincidente")})
    assert nuvem.diferencas(dec, [reaberta]) == []   # nao volta a "resolvida"


def test_decisao_posterior_do_admin_vence_a_da_nuvem():
    dec = {"a" * 40: {"impressao": "a" * 40, "status": "legitima", "data": "2026-11-02"}}
    admin = _linha("a" * 40, status="aberta", nota_triagem="[admin 2026-11-05] reabrir")
    assert nuvem.diferencas(dec, [admin]) == []
    antiga = _linha("a" * 40, status="aberta", nota_triagem="[admin 2026-10-31] x")
    assert nuvem.diferencas(dec, [antiga])[0]["status"] == "legitima"


def test_sincronizador_local_importa_a_decisao_da_nuvem():
    """Sem isso o estado local venceria e devolveria o status antigo a nuvem."""
    estado = {"a" * 40: {"status": "aberta", "nota_triagem": None}}
    nota = nuvem.nota_nuvem("2026-11-02", "legitima por x")
    mudadas = fila.importar_decisoes_admin(
        estado, [{"impressao": "a" * 40, "status": "legitima", "nota_triagem": nota}], AGORA)
    assert mudadas == ["a" * 40] and estado["a" * 40]["status"] == "legitima"


def test_arquivo_de_decisoes_existe_e_so_tem_json_valido():
    arquivo = RAIZ / nuvem.ARQUIVO_DECISOES
    assert arquivo.exists()
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        if linha.strip():
            d = json.loads(linha)
            assert d["status"] in nuvem.STATUS_DECISAO and len(d["impressao"]) >= 8


def test_workflow_roda_aplicar_antes_de_exportar_e_le_a_chave_do_secret():
    texto = (RAIZ / ".github" / "workflows" / nuvem.WORKFLOW).read_text(encoding="utf-8")
    assert texto.index("lacunas_nuvem.py aplicar") < texto.index("lacunas_nuvem.py exportar")
    assert f"secrets.{nuvem.VARIAVEL_CHAVE}" in texto
    assert nuvem.ARQUIVO_DECISOES in texto


def test_exportar_imprime_o_bloco_que_o_ler_acha(monkeypatch, capsys):
    import importlib.util

    from core.lacunas import painel

    spec = importlib.util.spec_from_file_location("lacunas_nuvem_cli",
                                                  RAIZ / "scripts" / "lacunas_nuvem.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    chave = Fernet.generate_key().decode()
    monkeypatch.setenv(nuvem.VARIAVEL_CHAVE, chave)
    monkeypatch.setattr(painel, "carregar", lambda **kw: painel.Painel(
        itens=[_item("a" * 40)], fontes={"cloud": "ok (1 registros)"}, gerado_em=""))
    assert cli.cmd_exportar() == 0
    log = "\n".join(f"2026-11-02T09:24:00Z {linha}" for linha in capsys.readouterr().out.splitlines())
    assert "CDB8266EXTI" not in log
    dados = nuvem.decifrar(nuvem.extrair_token(log), chave)
    assert dados["fila"][0]["impressao"] == "a" * 40

    monkeypatch.setattr(painel, "carregar", lambda **kw: painel.Painel(
        itens=[], fontes={"cloud": "indisponivel: OperationalError"}, gerado_em=""))
    assert cli.cmd_exportar() == 1   # fonte que falha deixa o run vermelho
