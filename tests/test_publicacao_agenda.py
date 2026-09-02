from datetime import datetime, timedelta, timezone

import pytest

from core.publicacao_agenda import (
    ALVOS,
    POR_CHAVE,
    alvos_devidos,
    motivo_para_publicar,
    registrar_resultado,
)

AGORA = datetime(2026, 9, 1, 19, 30, tzinfo=timezone.utc)


def _registro(dias_atras: float, status: str = "ok", **extra) -> dict:
    return {
        "ultima_publicacao": (AGORA - timedelta(days=dias_atras)).isoformat(),
        "ultimo_status": status,
        **extra,
    }


def test_nunca_publicado_esta_devendo():
    alvo = POR_CHAVE["fii_selection"]
    assert motivo_para_publicar(alvo, None, AGORA) == "nunca publicado"
    assert motivo_para_publicar(alvo, {}, AGORA) == "nunca publicado"


def test_dentro_da_cadencia_nao_deve_nada():
    alvo = POR_CHAVE["us_snapshot"]  # semanal
    assert motivo_para_publicar(alvo, _registro(3), AGORA) is None


def test_cadencia_vencida_deve_publicar():
    alvo = POR_CHAVE["us_snapshot"]  # semanal
    motivo = motivo_para_publicar(alvo, _registro(8), AGORA)
    assert motivo is not None and "8d" in motivo


def test_diaria_publicada_ontem_a_noite_esta_devendo_hoje():
    """O defeito real de 01/09/2026: a vitrine diária pulava dia sim, dia não.

    A publicação saiu 31/08 às 20:03 locais e o gatilho seguinte é 01/09 às
    19:30 locais -- 23h27 de idade. Com a régua em horas corridas o alvo era
    pulado por 33 minutos, publicava só no dia 02, e o log não acusava nada:
    pular estava certo pela regra que estava escrita.
    """
    alvo = POR_CHAVE["fii_selection"]
    local = datetime.now().astimezone().tzinfo
    ontem_a_noite = datetime(2026, 8, 31, 20, 3, tzinfo=local)
    gatilho = datetime(2026, 9, 1, 19, 30, tzinfo=local)
    registro = {"ultima_publicacao": ontem_a_noite.isoformat(), "ultimo_status": "ok"}

    assert gatilho - ontem_a_noite < timedelta(days=1)  # a armadilha
    motivo = motivo_para_publicar(alvo, registro, gatilho)
    assert motivo is not None and "1d" in motivo


def test_regime_estavel_nao_depende_do_jitter_do_agendador():
    """Publicando todo dia no mesmo horário, a idade é exatamente 24h.

    Ficar acima ou abaixo do limite passaria a ser decidido por segundos de
    atraso do agendador -- e um atraso de dois segundos apagaria a publicação
    do dia inteiro. Dia de calendário não tem essa borda.
    """
    alvo = POR_CHAVE["fii_selection"]
    local = datetime.now().astimezone().tzinfo
    ontem = datetime(2026, 8, 31, 19, 30, tzinfo=local)
    for atraso in (timedelta(seconds=-2), timedelta(0), timedelta(seconds=2)):
        gatilho = datetime(2026, 9, 1, 19, 30, tzinfo=local) + atraso
        registro = {"ultima_publicacao": ontem.isoformat(), "ultimo_status": "ok"}
        assert motivo_para_publicar(alvo, registro, gatilho) is not None


def test_publicado_hoje_mais_cedo_nao_republica():
    """Dia de calendário afrouxa a régua; não pode afrouxar a ponto de repetir.

    Duas rodadas no mesmo dia -- o gatilho de logon de manhã e o das 19:30 --
    não podem republicar a mesma vitrine diária.
    """
    alvo = POR_CHAVE["fii_selection"]
    local = datetime.now().astimezone().tzinfo
    manha = datetime(2026, 9, 1, 8, 15, tzinfo=local)
    noite = datetime(2026, 9, 1, 19, 30, tzinfo=local)
    registro = {"ultima_publicacao": manha.isoformat(), "ultimo_status": "ok"}
    assert motivo_para_publicar(alvo, registro, noite) is None


def test_o_dia_e_o_local_e_nao_o_utc():
    """O gatilho é local; a régua tem de ser da mesma grandeza.

    Em UTC-3 a virada do dia UTC cai às 21:00 locais. Uma publicação que termine
    depois disso divide o mesmo dia UTC com o gatilho da noite seguinte -- e a
    cadeia de FIIs leva perto de uma hora, então começar às 19:30 e fechar às
    21:10 não é hipótese remota. Uma régua em UTC pularia esse dia: o mesmo
    defeito, só que mais raro, e por isso mais difícil de enxergar.

    O par é construído no fuso da máquina, então o teste se anula onde a
    distinção não existe (em UTC ela não existe). Pular é honesto; fixar um fuso
    testaria uma máquina imaginária em vez desta.
    """
    local = datetime.now().astimezone().tzinfo
    ultima = datetime(2026, 8, 31, 21, 10, tzinfo=local)
    gatilho = datetime(2026, 9, 1, 19, 30, tzinfo=local)
    if ultima.astimezone(timezone.utc).date() != gatilho.astimezone(timezone.utc).date():
        pytest.skip("o fuso da máquina não separa dia local de dia UTC neste par")

    registro = {"ultima_publicacao": ultima.isoformat(), "ultimo_status": "ok"}
    assert motivo_para_publicar(POR_CHAVE["fii_selection"], registro, gatilho) is not None


def test_falha_anterior_ignora_a_cadencia():
    """Sem isso, um alvo mensal que falhou espera um mês pela nova tentativa.

    É o defeito das duas automações que estavam quebradas em 31/08/2026 sem
    ninguém saber.
    """
    alvo = POR_CHAVE["us_prices"]  # mensal
    registro = _registro(0.1, status="erro")
    assert motivo_para_publicar(alvo, registro, AGORA) == "última tentativa falhou"


def test_maquina_desligada_por_uma_semana_publica_o_que_venceu():
    """A cadência é contra a última publicação, não contra um horário.

    Um agendador puramente horário perderia esses dias em silêncio.
    """
    estado = {a.chave: _registro(7) for a in ALVOS}
    devidos = dict(alvos_devidos(estado, AGORA))
    chaves = {a.chave for a in devidos}
    assert "fii_selection" in chaves  # diária, 7 dias de atraso
    assert "b3_metrics" in chaves     # semanal, exatamente no limite
    assert "us_prices" not in chaves  # mensal, ainda em dia


def test_safra_pit_so_publica_quando_a_versao_muda():
    alvo = POR_CHAVE["us_vintages"]
    registro = _registro(365, versao="0.8.0")
    assert motivo_para_publicar(alvo, registro, AGORA, "0.8.0") is None
    motivo = motivo_para_publicar(alvo, registro, AGORA, "0.9.0")
    assert motivo is not None and "0.9.0" in motivo


def test_safra_pit_sem_versao_conhecida_nao_inventa_publicacao():
    alvo = POR_CHAVE["us_vintages"]
    registro = _registro(365, versao="0.8.0")
    assert motivo_para_publicar(alvo, registro, AGORA, None) is None


def test_registro_ilegivel_ou_futuro_publica_em_vez_de_confiar():
    alvo = POR_CHAVE["fii_selection"]
    assert motivo_para_publicar(alvo, {"ultima_publicacao": "ontem"}, AGORA) == (
        "nunca publicado"
    )
    futuro = {"ultima_publicacao": (AGORA + timedelta(days=2)).isoformat()}
    assert motivo_para_publicar(alvo, futuro, AGORA) == "registro com data futura"


def test_falha_nao_avanca_a_data_de_publicacao():
    """Carimbar a data numa falha tira o alvo da fila sem ele ter publicado."""
    estado = registrar_resultado({}, "fii_selection", ok=False, agora=AGORA)
    registro = estado["fii_selection"]
    assert registro["ultimo_status"] == "erro"
    assert "ultima_publicacao" not in registro
    assert motivo_para_publicar(POR_CHAVE["fii_selection"], registro, AGORA) == (
        "última tentativa falhou"
    )


def test_sucesso_grava_data_e_versao_sem_mutar_o_estado_recebido():
    original = {"us_vintages": {"ultimo_status": "erro"}}
    novo = registrar_resultado(original, "us_vintages", True, AGORA, versao="0.9.0")
    assert original == {"us_vintages": {"ultimo_status": "erro"}}
    assert novo["us_vintages"]["ultimo_status"] == "ok"
    assert novo["us_vintages"]["versao"] == "0.9.0"


def test_forcar_e_apenas_selecionam_sem_consultar_cadencia():
    estado = {a.chave: _registro(0) for a in ALVOS}
    assert alvos_devidos(estado, AGORA) == []
    forcados = alvos_devidos(estado, AGORA, forcar=True, apenas=("b3_metrics",))
    assert [a.chave for a, _ in forcados] == ["b3_metrics"]


@pytest.mark.parametrize("alvo", ALVOS, ids=[a.chave for a in ALVOS])
def test_publicador_que_simula_por_omissao_carrega_apply(alvo):
    """Chamar esses sem --apply sai com código 0 sem publicar nada.

    A rotina marcaria sucesso e a vitrine envelheceria em silêncio -- falha
    que nenhum verificador de "o comando deu certo?" pegaria.
    """
    exige_apply = {
        "scripts.publish_us_score_vintages",
        "scripts.publish_us_delistings",
        "scripts.publish_us_prices_monthly",
        "scripts/publish_b3_metrics_to_supabase.py",
    }
    for passo in alvo.passos:
        if set(passo) & exige_apply:
            assert "--apply" in passo, (alvo.chave, passo)


@pytest.mark.parametrize("alvo", ALVOS, ids=[a.chave for a in ALVOS])
def test_ingestao_de_fii_aponta_para_o_armazem(alvo):
    """Sem `--warehouse`, a cadeia bate no Supabase e falha onde já falhou.

    Foram 10 execuções diárias do `market-refresh.yml` batendo em
    `relation "market.fii_source_releases" does not exist`: das 22 tabelas
    `market.fii*`, 18 só existem no armazém local.
    """
    for passo in alvo.passos:
        if passo and passo[0] == "run_market_ingest.py":
            assert "--warehouse" in passo, (alvo.chave, passo)


def test_toda_chave_e_unica_e_tem_cadencia_coerente():
    assert len({a.chave for a in ALVOS}) == len(ALVOS)
    for alvo in ALVOS:
        if alvo.por_versao:
            assert alvo.versao_de, f"{alvo.chave} sem fonte de versão"
        else:
            assert alvo.cadencia_dias and alvo.cadencia_dias > 0


def test_artefato_declarado_e_rastreavel_pelo_git():
    """Artefato ignorado pelo git seria recusado por `git add` toda madrugada.

    E o modo de falhar é ruim: a vitrine vai para o Supabase, a rotina reclama
    do commit, e o caminho declarado parece certo -- o `.gitignore` é o último
    lugar em que se procura. `data/public/` já foi ignorado inteiro uma vez.
    """
    import subprocess
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[1]
    declarados = sorted({caminho for alvo in ALVOS for caminho in alvo.artefatos})
    assert declarados, "nenhum alvo declara artefato; o teste perdeu o objeto"

    for caminho in declarados:
        assert (raiz / caminho).exists(), f"{caminho} não existe em disco"
        proc = subprocess.run(["git", "check-ignore", "-q", caminho],
                              cwd=str(raiz), capture_output=True, check=False)
        assert proc.returncode != 0, f"{caminho} está no .gitignore"
        rastreado = subprocess.run(["git", "ls-files", "--error-unmatch", caminho],
                                   cwd=str(raiz), capture_output=True, check=False)
        assert rastreado.returncode == 0, f"{caminho} não está commitado na main"
