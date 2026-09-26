"""Entrevista guiada: a LLM propõe, a validação decide."""
import json

from core import llm_estrategia as ent
from core.estrategia import politica as pol

MINIMOS_MENOS_ALOCACAO = {
    "objective": "renda_passiva", "time_horizon": "longo",
    "risk_profile": "moderado", "liquidity_need": "baixa",
    "predominant_strategy": "dividendos",
}


def _chat(resposta: dict, capturado: dict | None = None):
    def _falso(messages, **kw):
        if capturado is not None:
            capturado["messages"] = messages
            capturado["kw"] = kw
        return json.dumps(resposta)
    return _falso


def test_grava_so_o_que_tem_evidencia_e_passa_na_validacao():
    etapa = ent.proxima_etapa({}, [], "Quero viver de renda, sou moderado",
                              chat=_chat({
        "updates": {"objective": "renda_passiva", "risk_profile": "moderado",
                    "liquidity_need": "baixa", "time_horizon": "eterno"},
        "evidence": {"objective": "viver de renda", "risk_profile": "moderado",
                     "time_horizon": "sempre"},
        "next_question": "Em quanto tempo pretende usar o dinheiro?",
        "finished": False}))
    assert set(etapa.aplicados) == {"objective", "risk_profile"}
    assert "sem trecho" in etapa.rejeitados["liquidity_need"]
    assert "opção inválida" in etapa.rejeitados["time_horizon"]
    assert etapa.politica["objective"]["source"] == "entrevista"
    assert etapa.politica["objective"]["evidence"] == "viver de renda"
    assert etapa.pergunta == "Em quanto tempo pretende usar o dinheiro?"
    assert not etapa.encerrar


def test_encerrar_com_minimo_faltando_vira_pergunta_do_roteiro():
    base, _ = pol.aplicar({}, MINIMOS_MENOS_ALOCACAO, fonte="manual")
    etapa = ent.proxima_etapa(base, [], "acho que é isso", chat=_chat(
        {"updates": {}, "evidence": {}, "next_question": "", "finished": True}))
    assert not etapa.encerrar
    assert etapa.pergunta == pol.POR_CHAVE["asset_class_targets"].pergunta


def test_proposta_nao_grava_sem_confirmacao_e_encerra_quando_confirma():
    base, _ = pol.aplicar({}, MINIMOS_MENOS_ALOCACAO, fonte="manual")
    divisao = {"renda_fixa": 40, "acoes_br": 20, "fiis": 30, "exterior": 10}
    etapa = ent.proxima_etapa(base, [], "não sei dividir", chat=_chat({
        "updates": {}, "evidence": {}, "proposal": divisao,
        "next_question": "Que tal esta divisão?", "finished": False}))
    assert etapa.proposta == {k: float(v) for k, v in divisao.items()}
    assert pol.valor(etapa.politica, "asset_class_targets") is None

    etapa = ent.proxima_etapa(etapa.politica, [], "sim, pode ser", chat=_chat({
        "updates": {"asset_class_targets": divisao},
        "evidence": {"asset_class_targets": "sim, pode ser"},
        "next_question": None, "finished": True}))
    assert etapa.encerrar and etapa.pergunta is None
    assert pol.progresso(etapa.politica).minimos_completos


def test_resposta_ilegivel_ou_sem_llm_cai_no_roteiro_sem_gravar():
    etapa = ent.proxima_etapa({}, [], "oi", chat=lambda *_a, **_k: "não é json")
    assert etapa.origem == "roteiro" and etapa.aviso
    assert etapa.politica == {}
    assert etapa.pergunta == pol.POR_CHAVE["objective"].pergunta

    def _cai(*_a, **_k):
        raise RuntimeError("Nenhum provedor LLM configurado")
    assert ent.proxima_etapa({}, [], "oi", chat=_cai).origem == "roteiro"


def test_prompt_leva_politica_contexto_e_historico_e_pede_json():
    capturado = {}
    historico = [{"role": "assistant", "content": "Qual o objetivo?"},
                 {"role": "user", "content": "crescer"}]
    ent.proxima_etapa({}, historico, "10 anos", contexto="Composição: FII 40%",
                      chat=_chat({"updates": {}, "evidence": {},
                                  "next_question": "E o risco?"}, capturado))
    msgs = capturado["messages"]
    assert capturado["kw"]["json_mode"] is True
    assert "NÃO recomende ativos" in msgs[0]["content"]
    assert "Objetivo principal: não informado" in msgs[1]["content"]
    assert "Composição: FII 40%" in msgs[1]["content"]
    assert "não são respostas do usuário" in msgs[1]["content"]
    assert [m["content"] for m in msgs[2:]] == ["Qual o objetivo?", "crescer",
                                               "10 anos"]


def test_limite_de_turnos_encerra_quando_minimos_completos():
    base, _ = pol.aplicar({}, {**MINIMOS_MENOS_ALOCACAO, "asset_class_targets": {
        "renda_fixa": 100}}, fonte="manual")
    historico = [{"role": "user", "content": "x"}] * (ent.MAX_TURNOS - 1)
    etapa = ent.proxima_etapa(base, historico, "mais uma", chat=_chat({
        "updates": {}, "evidence": {}, "next_question": "E o setor?",
        "finished": False}))
    assert etapa.encerrar


def test_abertura_e_roteiro_sem_llm():
    assert pol.POR_CHAVE["objective"].pergunta in ent.abertura({})
    base, _ = pol.aplicar({}, {"objective": "aposentadoria"}, fonte="manual")
    assert "continuar de onde paramos" in ent.abertura(base)
    assert ent.pergunta_do_roteiro(base) == pol.POR_CHAVE["time_horizon"].pergunta
