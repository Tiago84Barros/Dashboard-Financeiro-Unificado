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
