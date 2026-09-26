"""Política de investimentos: validação, progresso, status e procedência."""
import datetime as dt

import pytest

from core.estrategia import politica as pol

AGORA = dt.datetime(2026, 9, 26, tzinfo=dt.timezone.utc)

MINIMOS = {
    "objective": "renda_passiva",
    "time_horizon": "longo",
    "risk_profile": "moderado",
    "liquidity_need": "baixa",
    "predominant_strategy": "dividendos",
    "asset_class_targets": {"renda_fixa": 30, "acoes_br": 30, "fiis": 30,
                            "exterior": 10},
}


def _completa():
    nova, erros = pol.aplicar({}, MINIMOS, fonte="entrevista", agora=AGORA)
    assert erros == {}
    return nova


# -- normalização --------------------------------------------------------------

def test_escolha_aceita_chave_e_rotulo_e_recusa_o_resto():
    assert pol.normalizar("risk_profile", "Moderado") == ("moderado", None)
    assert pol.normalizar("risk_profile", "arrojado") == ("arrojado", None)
    valor, erro = pol.normalizar("risk_profile", "agressivo")
    assert valor is None and "opção inválida" in erro


def test_alocacao_exige_soma_100_e_nao_completa_o_que_falta():
    ok, erro = pol.normalizar("asset_class_targets",
                              {"renda_fixa": "40", "acoes_br": 60})
    assert erro is None
    assert ok == {"renda_fixa": 40.0, "acoes_br": 60.0, "fiis": 0.0,
                  "exterior": 0.0}
    _, erro = pol.normalizar("asset_class_targets",
                             {"renda_fixa": 40, "acoes_br": 30})
    assert "somam 70%" in erro
    _, erro = pol.normalizar("asset_class_targets", {"cripto": 100})
    assert "classe desconhecida" in erro


def test_numero_respeita_faixa_e_formato_brasileiro():
    assert pol.normalizar("monthly_contribution", "1.500,50") == (1500.5, None)
    assert pol.normalizar("max_drawdown_tolerance_pct", "25%") == (25.0, None)
    assert pol.normalizar("max_drawdown_tolerance_pct", 150)[1]
    assert pol.normalizar("emergency_reserve_months", True)[1]  # bool não é número
    assert pol.normalizar("monthly_contribution", float("nan"))[1]


def test_multi_ordena_pelo_schema_e_remove_repeticao():
    valor, erro = pol.normalizar(
        "strategy_priorities", ["dividendos", "crescimento", "dividendos"])
    assert erro is None and valor == ["crescimento", "dividendos"]
    assert pol.normalizar("strategy_priorities", []) == ([], None)


def test_bool_e_texto():
    assert pol.normalizar("income_needed_now", "não") == (False, None)
    assert pol.normalizar("income_needed_now", "talvez")[1]
    assert pol.normalizar("user_constraints", "   ")[1]
    assert len(pol.normalizar("user_constraints", "x" * 900)[0]) == pol.MAX_TEXTO


def test_campo_desconhecido_e_recusado():
    assert "desconhecido" in pol.normalizar("renda_da_tia", 1)[1]


# -- aplicar / procedência -----------------------------------------------------

def test_aplicar_grava_procedencia_e_nao_apaga_o_bom_com_o_ruim():
    base, _ = pol.aplicar({}, {"risk_profile": "moderado"}, fonte="entrevista",
                          evidencias={"risk_profile": "fico no meio"},
                          agora=AGORA)
    assert base["risk_profile"] == {"value": "moderado", "source": "entrevista",
                                    "at": AGORA.isoformat(),
                                    "evidence": "fico no meio"}
    nova, erros = pol.aplicar(base, {"risk_profile": "doido",
                                     "liquidity_need": "alta"}, fonte="manual")
    assert "risk_profile" in erros
    assert nova["risk_profile"]["value"] == "moderado"
    assert nova["liquidity_need"]["source"] == "manual"
    assert base.get("liquidity_need") is None  # pura: não muta a entrada


def test_aplicar_recusa_fonte_desconhecida():
    with pytest.raises(ValueError):
        pol.aplicar({}, {}, fonte="palpite")


# -- progresso -----------------------------------------------------------------

def test_politica_vazia_e_zero_e_nao_conclui():
    prog = pol.progresso({})
    assert prog.pct == 0 and not prog.minimos_completos
    assert len(pol.erros_de_conclusao({})) == len(pol.OBRIGATORIOS)


def test_progresso_conta_so_minimos_e_complementares_a_parte():
    parcial, _ = pol.aplicar({}, {"objective": "aposentadoria",
                                  "time_horizon": "muito_longo",
                                  "user_constraints": "sem tabaco"},
                             fonte="manual")
    prog = pol.progresso(parcial)
    assert prog.pct == pytest.approx(33.3)
    assert prog.complementares_ok == 1
    assert pol.progresso(_completa()).pct == 100.0


def test_condicional_nao_conta_quando_nao_se_aplica():
    sem_aporte, _ = pol.aplicar({}, {"makes_contributions": False},
                                fonte="manual")
    com_aporte, _ = pol.aplicar({}, {"makes_contributions": True},
                                fonte="manual")
    assert "monthly_contribution" not in pol.progresso(
        sem_aporte).complementares_faltantes
    assert "monthly_contribution" in pol.progresso(
        com_aporte).complementares_faltantes


def test_valor_gravado_que_deixou_de_ser_valido_conta_como_faltando():
    politica = _completa()
    politica["risk_profile"] = {"value": "ultra", "source": "manual"}
    assert "risk_profile" in pol.progresso(politica).faltantes


# -- status --------------------------------------------------------------------

def test_status_efetivo():
    completa = _completa()
    assert pol.status_efetivo(None, {}) == pol.NOT_STARTED
    assert pol.status_efetivo("IN_PROGRESS", {}) == pol.IN_PROGRESS
    assert pol.status_efetivo("COMPLETED", completa, schema_version="1",
                              completed_at=AGORA, agora=AGORA) == pol.COMPLETED


def test_concluida_que_perdeu_o_motivo_vira_revisao():
    completa = _completa()
    # vazia gravada como COMPLETED não é configuração válida
    assert pol.status_efetivo("COMPLETED", {}) == pol.NEEDS_REVIEW
    assert pol.status_efetivo("COMPLETED", completa,
                              schema_version="0") == pol.NEEDS_REVIEW
    velha = AGORA - dt.timedelta(days=pol.REVISAO_DIAS + 1)
    assert pol.status_efetivo("COMPLETED", completa, schema_version="1",
                              completed_at=velha, agora=AGORA) == pol.NEEDS_REVIEW


# -- derivados, alertas e texto ------------------------------------------------

def test_valores_derivam_renda_fixa_variavel_e_internacional():
    v = pol.valores(_completa())
    assert v["fixed_income_target"] == 30
    assert v["variable_income_target"] == 60
    assert v["international_target"] == 10


def test_alertas_apontam_tensao_sem_bloquear():
    politica, _ = pol.aplicar(_completa(), {
        "time_horizon": "curto", "risk_profile": "arrojado",
        "predominant_strategy": "crescimento",
        "has_emergency_reserve": False,
        "asset_class_limits": {"fiis": 20},
    }, fonte="manual")
    avisos = " | ".join(pol.alertas(politica))
    assert "Horizonte curto com perfil arrojado" in avisos
    assert "renda passiva com estratégia de crescimento" in avisos
    assert "Sem reserva de emergência" in avisos
    assert "Fundos imobiliários: alvo de 30% acima do limite de 20%" in avisos
    assert pol.erros_de_conclusao(politica) == []


def test_texto_da_politica_nomeia_lacuna_e_procedencia():
    parcial, _ = pol.aplicar({}, {"objective": "renda_passiva"},
                             fonte="entrevista")
    texto = pol.texto_da_politica(parcial, versao=1, status=pol.IN_PROGRESS)
    assert "Objetivo principal: Renda passiva (dito na entrevista)" in texto
    assert "Horizonte de investimento: não informado" in texto
    assert "Versão: 1 · Status: Em andamento" in texto
    completo = pol.texto_da_politica(_completa())
    assert "renda variável 60%" in completo


def test_formatar_reais():
    assert pol.formatar("monthly_contribution", 1500.5) == "R$ 1.500,50"
    assert pol.formatar("strategy_priorities", []) == "nenhum"
