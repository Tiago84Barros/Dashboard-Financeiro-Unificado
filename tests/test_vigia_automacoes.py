"""Vigia de automações: a decisão pura e a rodada com I/O trocado por dublês."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from core import vigia_automacoes as va
from core.frescor import TOLERANCIA_DIAS, limite_do_alvo
from core.publicacao_agenda import POR_CHAVE

AGORA = datetime(2026, 10, 4, 15, 0, tzinfo=timezone.utc)


def _run(conclusao, horas_atras, status="completed", ramo="main"):
    return {"status": status, "conclusion": conclusao, "headBranch": ramo,
            "createdAt": (AGORA - timedelta(hours=horas_atras)).isoformat(),
            "url": f"https://github.com/x/y/actions/runs/{horas_atras}"}


# --- workflows ---------------------------------------------------------------

def test_duas_falhas_consecutivas_avisam():
    p = va.problema_workflow("data_pipeline.yml",
                             [_run("failure", 1), _run("failure", 25), _run("success", 49)])
    assert p is not None
    assert p.chave == "workflow:data_pipeline.yml"
    assert "2 execuções" in p.texto


def test_uma_falha_isolada_nao_avisa():
    assert va.problema_workflow("x.yml", [_run("failure", 1), _run("success", 25)]) is None


def test_sucesso_no_meio_zera_a_sequencia():
    runs = [_run("failure", 1), _run("success", 25), _run("failure", 49),
            _run("failure", 73)]
    assert va.problema_workflow("x.yml", runs) is None


def test_ordem_vem_da_data_e_nao_da_posicao_na_lista():
    runs = [_run("success", 49), _run("failure", 1), _run("failure", 25)]
    assert va.problema_workflow("x.yml", runs) is not None


def test_em_andamento_cancelado_e_pulado_sao_ignorados():
    runs = [_run(None, 0, status="in_progress"), _run("cancelled", 1),
            _run("failure", 2), _run("skipped", 3), _run("timed_out", 4),
            _run("success", 5)]
    falhas, completa = va.falhas_consecutivas(runs)
    assert [r["conclusion"] for r in falhas] == ["failure", "timed_out"]
    assert completa


def test_workflow_desligado_por_variavel_so_pula_e_nao_avisa():
    """`noticias.yml` sem NOTICIAS_COLETA_ATIVA: tudo `skipped`."""
    assert va.problema_workflow("noticias.yml",
                                [_run("skipped", h) for h in range(10)]) is None


def test_janela_toda_em_falha_diz_pelo_menos():
    p = va.problema_workflow("x.yml", [_run("failure", h * 24) for h in range(10)])
    assert "pelo menos 10" in p.texto


def test_outro_ramo_nao_conta():
    runs = [_run("failure", 1, ramo="feat/x"), _run("failure", 2, ramo="feat/x"),
            _run("success", 3)]
    assert va.problema_workflow("x.yml", runs) is None


def test_assinatura_nao_muda_a_cada_execucao():
    """Contagem e URL mudam a cada cron; a assinatura não pode."""
    a = va.problema_workflow("x.yml", [_run("failure", 1), _run("failure", 25)])
    b = va.problema_workflow("x.yml", [_run("failure", 0), _run("failure", 1),
                                       _run("failure", 25)])
    assert a.assinatura == b.assinatura


def test_detecta_schedule_ativo_e_ignora_comentado():
    assert va.tem_agendamento("on:\n  schedule:\n    - cron: '0 9 * * *'\n")
    assert not va.tem_agendamento(
        "# recoloque `schedule: - cron`\n#  schedule:\non:\n  workflow_dispatch:\n")


# --- publicações -------------------------------------------------------------

def _estado(chave, dias_atras, status="ok"):
    quando = datetime(2026, 10, 4, 12, 0).astimezone() - timedelta(days=dias_atras)
    return {chave: {"ultima_publicacao": quando.isoformat(), "ultimo_status": status,
                    "ultima_tentativa": quando.isoformat()}}


def test_limite_sai_da_agenda_e_da_tolerancia_da_tela():
    assert limite_do_alvo("fii_selection") == 4  # == _FII_SNAPSHOT_MAX_AGE_DAYS
    assert limite_do_alvo("us_snapshot") == POR_CHAVE["us_snapshot"].cadencia_dias + TOLERANCIA_DIAS
    assert limite_do_alvo("us_vintages") is None  # por versão


def test_vitrine_dentro_do_limite_nao_avisa():
    problemas, verif = va.problemas_vitrine(_estado("fii_selection", 4), date(2026, 10, 4))
    assert problemas == []
    assert "vitrine:fii_selection" in verif


def test_vitrine_fora_do_limite_avisa_e_cita_a_falha():
    problemas, _ = va.problemas_vitrine(_estado("fii_selection", 6, "erro"),
                                        date(2026, 10, 4))
    assert [p.chave for p in problemas] == ["vitrine:fii_selection"]
    assert "há 6 dias" in problemas[0].texto
    assert "falhou" in problemas[0].texto


def test_alvo_por_versao_e_alvo_sem_registro_nao_sao_medidos():
    estado = _estado("us_vintages", 400)
    problemas, verif = va.problemas_vitrine(estado, date(2026, 10, 4))
    assert problemas == []
    assert "vitrine:us_vintages" not in verif
    assert "vitrine:fii_selection" not in verif


def test_estado_vazio_e_um_problema_proprio():
    problemas, _ = va.problemas_vitrine({}, date(2026, 10, 4))
    assert [p.chave for p in problemas] == ["vitrines:estado"]


# --- antispam e resolução ----------------------------------------------------

P = va.Problema("workflow:x.yml", "failure", "x quebrado")


def test_problema_novo_avisa():
    d = va.decidir([P], {}, AGORA, {P.chave})
    assert d.avisar == [P]
    assert d.memoria[P.chave]["ultimo_aviso"] == AGORA.isoformat()


def test_mesmo_problema_dentro_de_24h_nao_reavisa():
    d1 = va.decidir([P], {}, AGORA, {P.chave})
    d2 = va.decidir([P], d1.memoria, AGORA + timedelta(hours=23), {P.chave})
    assert d2.avisar == []
    assert d2.memoria == d1.memoria


def test_mesmo_problema_depois_de_24h_reavisa_e_mantem_o_inicio():
    d1 = va.decidir([P], {}, AGORA, {P.chave})
    d2 = va.decidir([P], d1.memoria, AGORA + timedelta(hours=24), {P.chave})
    assert d2.avisar == [P]
    assert d2.memoria[P.chave]["primeiro_aviso"] == AGORA.isoformat()
    msg = va.montar_mensagem(d2, d1.memoria)
    assert "segue desde" in msg[1]


def test_problema_que_muda_reavisa_antes_de_24h():
    d1 = va.decidir([P], {}, AGORA, {P.chave})
    outro = va.Problema(P.chave, "timed_out", "x estourou o tempo")
    d2 = va.decidir([outro], d1.memoria, AGORA + timedelta(hours=1), {P.chave})
    assert d2.avisar == [outro]


def test_resolucao_avisa_uma_vez():
    d1 = va.decidir([P], {}, AGORA, {P.chave})
    d2 = va.decidir([], d1.memoria, AGORA + timedelta(hours=2), {P.chave})
    assert [c for c, _ in d2.resolvidos] == [P.chave]
    assert P.chave not in d2.memoria
    assert va.montar_mensagem(d2, d1.memoria)[0] == "Dashboard: automação normalizada"
    d3 = va.decidir([], d2.memoria, AGORA + timedelta(hours=3), {P.chave})
    assert d3.resolvidos == [] and va.montar_mensagem(d3, d2.memoria) is None


def test_nao_verificado_nao_vira_resolvido():
    """`gh` que falhou não pode dizer que o workflow voltou."""
    d1 = va.decidir([P], {}, AGORA, {P.chave})
    d2 = va.decidir([], d1.memoria, AGORA + timedelta(hours=2), set())
    assert d2.resolvidos == []
    assert P.chave in d2.memoria


def test_tudo_em_dia_nao_gera_mensagem():
    assert va.montar_mensagem(va.decidir([], {}, AGORA, set()), {}) is None


def test_cegueira_do_gh_so_avisa_depois_de_48h():
    meta, p = va.atualizar_cegueira({}, False, AGORA)
    assert p is None
    meta, p = va.atualizar_cegueira(meta, False, AGORA + timedelta(hours=47))
    assert p is None
    meta, p = va.atualizar_cegueira(meta, False, AGORA + timedelta(hours=48))
    assert p is not None and p.chave == "vigia:github"
    meta, p = va.atualizar_cegueira(meta, True, AGORA + timedelta(hours=49))
    assert p is None and meta["falhando_desde"] is None


# --- rodada com I/O trocado ----------------------------------------------------

@pytest.fixture
def rodada(monkeypatch, tmp_path):
    from scripts import vigia_automacoes as sv

    estado = tmp_path / "estado.json"
    estado.write_text(json.dumps(_estado("fii_selection", 6, "erro")), encoding="utf-8")
    memoria = tmp_path / "vigia.json"
    monkeypatch.setattr(sv, "_gh", lambda: "gh")
    monkeypatch.setattr(sv, "workflows_agendados", lambda: ["a.yml", "b.yml"])
    runs = {"a.yml": [_run("failure", 1), _run("failure", 25)],
            "b.yml": [_run("success", 1)]}
    monkeypatch.setattr(sv, "runs_agendados", lambda nome, gh: runs[nome])
    # Sem isto a rodada abriria conexão com o armazém local de verdade.
    monkeypatch.setattr(sv, "atualidade_b3_do_armazem", lambda hoje: None)
    enviados = []
    import scripts.notificar as notif
    monkeypatch.setattr(notif, "notificar",
                        lambda texto, assunto=None: enviados.append((assunto, texto)) or True)
    return sv, estado, memoria, enviados


def test_dry_run_nao_envia_nem_grava(rodada):
    sv, estado, memoria, enviados = rodada
    linhas = []
    sv.vigiar(dry_run=True, estado_publicacao=estado, memoria_path=memoria,
              agora=AGORA, saida=linhas.append)
    assert enviados == [] and not memoria.exists()
    assert any("a.yml" in linha and "[dry-run] avisaria" in linha for linha in linhas)


def test_rodada_real_envia_uma_vez_e_grava_memoria(rodada):
    sv, estado, memoria, enviados = rodada
    sv.vigiar(estado_publicacao=estado, memoria_path=memoria, agora=AGORA,
              saida=lambda _l: None)
    assert len(enviados) == 1
    assert "a.yml" in enviados[0][1] and "fii_selection" in enviados[0][1]
    sv.vigiar(estado_publicacao=estado, memoria_path=memoria,
              agora=AGORA + timedelta(hours=1), saida=lambda _l: None)
    assert len(enviados) == 1


def test_aviso_que_nao_saiu_e_tentado_de_novo(rodada, monkeypatch):
    sv, estado, memoria, enviados = rodada
    import scripts.notificar as notif
    monkeypatch.setattr(notif, "notificar", lambda texto, assunto=None: False)
    sv.vigiar(estado_publicacao=estado, memoria_path=memoria, agora=AGORA,
              saida=lambda _l: None)
    assert json.loads(memoria.read_text(encoding="utf-8"))["avisos"] == {}


# --- demonstrações da B3 no armazém (B3-02) -------------------------------------

def _avaliacao_local():
    from core.b3_atualidade_trimestral import avaliar_universo
    # armazém em 04/10/2026: 2026T1 com 397 empresas, 2026T2 com 1
    return avaliar_universo({(2025, 2): 413, (2025, 3): 404, (2025, 4): 389,
                             (2026, 1): 397, (2026, 2): 1}, date(2026, 10, 4))


def test_armazem_com_trimestre_atrasado_avisa_com_assinatura_estavel():
    p = va.problema_atualidade_b3(_avaliacao_local())
    assert p.chave == va.CHAVE_ATUALIDADE_B3
    assert p.assinatura == "2026T1->2026T2"
    assert "run_market_ingest.py annual --warehouse" in p.texto


def test_armazem_em_dia_ou_nao_medido_nao_avisa():
    from core.b3_atualidade_trimestral import avaliar_universo
    em_dia = avaliar_universo({(2026, 1): 402, (2026, 2): 392}, date(2026, 10, 4))
    assert va.problema_atualidade_b3(em_dia) is None
    assert va.problema_atualidade_b3(None) is None
    assert va.problema_atualidade_b3(avaliar_universo({}, date(2026, 10, 4))) is None


def test_rodada_avisa_armazem_atrasado(rodada, monkeypatch):
    sv, estado, memoria, enviados = rodada
    monkeypatch.setattr(sv, "atualidade_b3_do_armazem", lambda hoje: _avaliacao_local())
    sv.vigiar(estado_publicacao=estado, memoria_path=memoria, agora=AGORA,
              saida=lambda _l: None)
    assert "armazém local atrasadas" in enviados[0][1]
    avisos = json.loads(memoria.read_text(encoding="utf-8"))["avisos"]
    assert va.CHAVE_ATUALIDADE_B3 in avisos


# ── Coleta de notícias (incidente de 07/10/2026) ──────────────────────────────
from datetime import datetime as _dt  # noqa: E402
from datetime import timedelta as _td  # noqa: E402
from datetime import timezone as _tz  # noqa: E402

from core.vigia_automacoes import (  # noqa: E402
    CHAVE_COLETA_NOTICIAS,
    problemas_noticias,
)

_AGORA = _dt(2026, 10, 9, 22, 30, tzinfo=_tz.utc)


def _estados(*, global_h=0.5, av_sucesso_h=1.0, av_erro=None):
    return {
        "global": {"ultima_tentativa": _AGORA - _td(hours=global_h),
                   "ultimo_sucesso": _AGORA - _td(hours=global_h)},
        "provedor:alphavantage": {
            "ultima_tentativa": _AGORA - _td(hours=0.5),
            "ultimo_sucesso": (None if av_sucesso_h is None
                               else _AGORA - _td(hours=av_sucesso_h)),
            "ultimo_erro": av_erro},
        "provedor:rss": {"ultima_tentativa": _AGORA - _td(hours=0.5),
                         "ultimo_sucesso": _AGORA - _td(hours=0.5)},
    }


def _por_dia(av_recentes=(1, 0), av_normal=100):
    hoje = _AGORA.date()
    av = {hoje - _td(days=i): av_normal for i in range(3, 63)}
    av.update({hoje - _td(days=1): av_recentes[0], hoje - _td(days=2): av_recentes[1]})
    rss = {hoje - _td(days=i): 20 for i in range(1, 63)}
    return {"alphavantage": av, "rss": rss}


def test_provedor_que_responde_vazio_avisa_mesmo_com_sucesso_recente():
    # O caso de 07/10: o Alpha Vantage "funcionava" (sucesso há 1 h) e trazia
    # 1 notícia por dia contra ~100. Só o rendimento enxerga isso.
    problemas, verificados = problemas_noticias(_estados(), _por_dia(), _AGORA)
    chaves = [p.chave for p in problemas]
    assert chaves == ["noticias:rendimento:alphavantage"]
    assert "1 e 0 notícia(s)" in problemas[0].texto and "100/dia" in problemas[0].texto
    assert {"noticias:rendimento:rss", "noticias:provedor:alphavantage",
            CHAVE_COLETA_NOTICIAS} <= verificados


def test_rendimento_normal_nao_avisa_e_provedor_pequeno_nao_e_julgado():
    problemas, verificados = problemas_noticias(
        _estados(), _por_dia(av_recentes=(90, 110)), _AGORA)
    assert problemas == []
    pequeno = {"x": {_AGORA.date() - _td(days=i): 3 for i in range(3, 63)}}
    _, verif = problemas_noticias(_estados(), pequeno, _AGORA)
    assert "noticias:rendimento:x" not in verif


def test_provedor_sem_sucesso_ha_dias_avisa_com_o_erro():
    problemas, _ = problemas_noticias(
        _estados(av_sucesso_h=60, av_erro="alphavantage: limite de chamadas excedido"),
        _por_dia(av_recentes=(90, 110)), _AGORA)
    assert [p.chave for p in problemas] == ["noticias:provedor:alphavantage"]
    assert "limite de chamadas" in problemas[0].texto


def test_coleta_parada_avisa_uma_vez_sem_repetir_por_provedor():
    # 14 a 22/10: nada rodou. Um aviso só, o da coleta; sem ele, cada provedor
    # "renderia zero" e o recado viraria uma lista de falsos culpados.
    problemas, _ = problemas_noticias(_estados(global_h=40), _por_dia(av_recentes=(0, 0)),
                                      _AGORA)
    assert [p.chave for p in problemas] == [CHAVE_COLETA_NOTICIAS]
    assert "DFU - Coleta de noticias" in problemas[0].texto


def test_assinatura_estavel_enquanto_o_problema_dura():
    # Assinatura que muda sozinha viraria aviso diário disfarçado.
    a, _ = problemas_noticias(_estados(), _por_dia(av_recentes=(1, 0)), _AGORA)
    b, _ = problemas_noticias(_estados(), _por_dia(av_recentes=(3, 2)), _AGORA)
    assert a[0].assinatura == b[0].assinatura


def test_provedor_fora_da_rotacao_nao_e_falha():
    estados = _estados()
    estados["provedor:finnhub"] = {"ultima_tentativa": _AGORA - _td(days=20),
                                   "ultimo_sucesso": None}
    _, verificados = problemas_noticias(estados, {}, _AGORA)
    assert "noticias:provedor:finnhub" not in verificados


def test_chave_de_dia_em_texto_iso_tambem_vale():
    por_dia = {k: {d.isoformat(): n for d, n in v.items()}
               for k, v in _por_dia().items()}
    problemas, _ = problemas_noticias(_estados(), por_dia, _AGORA)
    assert [p.chave for p in problemas] == ["noticias:rendimento:alphavantage"]
