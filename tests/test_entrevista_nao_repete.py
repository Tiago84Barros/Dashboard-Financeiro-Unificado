"""A entrevista da estratégia repetia a mesma pergunta (28/09/2026).

O usuário respondeu duas vezes o objetivo ("formar um capital que me dê
proteção ... e principalmente me gerar uma renda passiva") e a IA voltou com
"Qual é o principal objetivo do seu patrimônio investido?" nas duas.

Medido contra os provedores reais: OpenAI sem crédito, e o Gemini -- que
atendia -- descartava a PRIMEIRA de duas mensagens de sistema, justamente a
das regras. Respondia {"objective": ..., "question": ...}; sem "updates" nem
"next_question", nada era gravado e o roteiro devolvia a pergunta de novo.
Com uma mensagem de sistema só, o mesmo Gemini gravou objective=renda_passiva.
"""
import json

from core import llm_estrategia as ent
from core.estrategia import politica as pol

_PERGUNTA_OBJ = pol.POR_CHAVE["objective"].pergunta
_RESPOSTA = ("Meu principal objetivo é formar um capital que me dê proteção "
             "contra eventuais imprevistos e principalmente me gerar uma "
             "renda passiva")


def _historico():
    return [{"role": "assistant", "content": ent.abertura({})}]


def test_regras_e_estado_vao_numa_mensagem_de_sistema_so():
    msgs = ent._mensagens({}, _historico(), _RESPOSTA, "PERFIL X")
    sistemas = [m for m in msgs if m["role"] == "system"]
    assert len(sistemas) == 1
    assert msgs[0]["role"] == "system"
    texto = sistemas[0]["content"]
    assert "REGRAS" in texto and "Obrigatórios que faltam" in texto
    assert "PERFIL X" in texto


def test_prompt_diz_que_o_objetivo_destacado_e_o_principal():
    texto = ent._mensagens({}, [], "x", "")[0]["content"]
    assert "principalmente" in texto and "secondary_objectives" in texto
    assert "Nunca" in texto and "repita uma pergunta" in texto


def test_json_em_outro_formato_e_ilegivel_e_avisa():
    """O que o Gemini devolvia sem as regras não vira resposta vazia."""
    def _chat(*_a, **_k):
        return json.dumps({"objective": "Proteção e renda passiva",
                           "question": "Qual o seu horizonte?"})
    etapa = ent.proxima_etapa({}, _historico(), _RESPOSTA, chat=_chat)
    assert etapa.aviso and etapa.origem == "roteiro"
    assert not etapa.aplicados


def test_nao_repete_a_pergunta_identica_oferece_as_opcoes():
    def _chat(*_a, **_k):
        return json.dumps({"updates": {}, "evidence": {},
                           "next_question": "", "finished": False})
    etapa = ent.proxima_etapa({}, _historico(), _RESPOSTA, chat=_chat)
    assert etapa.pergunta != _PERGUNTA_OBJ
    assert "Renda passiva" in etapa.pergunta
    assert "Crescimento do patrimônio" in etapa.pergunta


def test_roteiro_sem_repeticao_mantem_a_pergunta_nova():
    """Pergunta que ainda não foi feita segue igual à do roteiro."""
    def _chat(*_a, **_k):
        return "sem json nenhum"
    etapa = ent.proxima_etapa({}, [], _RESPOSTA, chat=_chat)
    assert etapa.pergunta == _PERGUNTA_OBJ


def test_resposta_com_destaque_grava_e_segue_para_outro_campo():
    """O caminho que o Gemini fez com o prompt corrigido."""
    def _chat(*_a, **_k):
        return json.dumps({
            "updates": {"objective": "renda_passiva",
                        "secondary_objectives": ["preservacao_patrimonial"]},
            "evidence": {"objective": "principalmente me gerar uma renda passiva",
                         "secondary_objectives": "proteção contra imprevistos"},
            "proposal": None,
            "next_question": "Em quanto tempo pretende usar esse dinheiro?",
            "finished": False})
    etapa = ent.proxima_etapa({}, _historico(), _RESPOSTA, chat=_chat)
    assert pol.valor(etapa.politica, "objective") == "renda_passiva"
    assert "objective" in etapa.aplicados
    assert etapa.pergunta != _PERGUNTA_OBJ
