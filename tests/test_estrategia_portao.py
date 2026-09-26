"""Portão estratégia → análise de ativos: só a vigente concluída libera."""
import datetime as dt

from core.estrategia import politica as pol
from core.estrategia import portao
from core.estrategia import repositorio as repo

MINIMOS = {
    "objective": "renda_passiva", "time_horizon": "longo",
    "risk_profile": "moderado", "liquidity_need": "baixa",
    "predominant_strategy": "dividendos",
    "asset_class_targets": {"renda_fixa": 40, "acoes_br": 20, "fiis": 30,
                            "exterior": 10},
}


def _reg(status, politica, versao=1, concluida=None):
    return repo.Registro(
        id=f"r{versao}", version=versao, status_gravado=status,
        schema_version=pol.SCHEMA_VERSION, politica=politica, entrevista=[],
        completion_pct=0, completed_at=concluida, created_at=None,
        updated_at=None)


def _completa():
    return pol.aplicar({}, MINIMOS, fonte="manual")[0]


def test_sem_estrategia_bloqueia_com_todos_os_minimos():
    lib = portao.avaliar(repo.Estado())
    assert not lib.disponivel
    assert lib.como_dict() == {
        "analysis_available": False,
        "configuration_status": "NOT_STARTED",
        "completion_percentage": 0.0,
        "reason": "STRATEGY_CONFIGURATION_REQUIRED",
        "missing_fields": list(pol.OBRIGATORIOS),
        "next_step": {"section": "settings", "tab": "general",
                      "feature": "investment_strategy"},
    }


def test_rascunho_incompleto_diz_quanto_fez_e_o_que_falta():
    parcial, _ = pol.aplicar({}, {"objective": "renda_passiva",
                                  "time_horizon": "longo",
                                  "liquidity_need": "baixa"}, fonte="manual")
    d = portao.avaliar(repo.Estado(rascunho=_reg("IN_PROGRESS", parcial))).como_dict()
    assert d["configuration_status"] == "IN_PROGRESS"
    assert d["completion_percentage"] == 50.0
    assert d["missing_fields"] == ["risk_profile", "predominant_strategy",
                                   "asset_class_targets"]


def test_rascunho_completo_mas_nao_concluido_continua_bloqueado():
    lib = portao.avaliar(repo.Estado(rascunho=_reg("IN_PROGRESS", _completa())))
    assert not lib.disponivel and lib.pct == 100.0 and lib.faltantes == ()


def test_concluida_libera_mesmo_com_edicao_aberta():
    agora = dt.datetime.now(dt.timezone.utc)
    vigente = _reg("COMPLETED", _completa(), concluida=agora)
    lib = portao.avaliar(repo.Estado(
        vigente=vigente, rascunho=_reg("IN_PROGRESS", {}, versao=2)))
    assert lib.disponivel and lib.politica is vigente
    assert lib.como_dict() == {"analysis_available": True,
                               "configuration_status": "COMPLETED",
                               "completion_percentage": 100.0,
                               "policy_version": 1}


def test_concluida_vencida_pede_revisao():
    velha = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=400)
    lib = portao.avaliar(repo.Estado(
        vigente=_reg("COMPLETED", _completa(), concluida=velha)))
    assert not lib.disponivel
    assert lib.motivo == portao.REVISAO_NECESSARIA
    assert lib.status == pol.NEEDS_REVIEW


def test_tabela_ausente_e_falha_de_leitura_fecham(monkeypatch):
    assert portao.avaliar(repo.Estado(tabela_ausente=True)).motivo == \
        portao.ESTRATEGIA_INDISPONIVEL

    def _cai(**_k):
        raise RuntimeError("banco fora")
    monkeypatch.setattr(repo, "carregar", _cai)
    lib = portao.verificar()
    assert not lib.disponivel and lib.motivo == portao.ESTRATEGIA_INDISPONIVEL
