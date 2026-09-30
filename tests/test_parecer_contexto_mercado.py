"""O parecer do gate de seleção recebe macro, notícias e detalhe do armazém.

O parecer REPROVA empresas na Criação de Portfólio. Até aqui ele decidia sem
juros, sem noticiário e sem o comportamento do papel, contra a premissa de que
toda LLM do app consulta todo dado disponível. Dar o contexto a um componente
que veta exige a trava junto: notícia e preço situam o parecer, mas não
sustentam veto sozinhos (regra 5.5). O harness ``scripts/eval_gate_selecao.py
--contexto adverso|favoravel`` mede essa trava com LLM real; estes testes
fixam a fiação.
"""
from __future__ import annotations

import sys
from pathlib import Path

import core.dossie_b3 as d
from core.contexto_mercado import REGRA_CONTEXTO_MERCADO

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import eval_gate_selecao as harness  # noqa: E402


def _capturar_prompt(monkeypatch) -> dict:
    visto: dict = {}

    def falso(prompt, tk):
        visto["prompt"] = prompt
        return {"classificacao_selecao": "aprovar", "motivo_selecao": "ok",
                "relatorio": {"comportamento_de_mercado": "Selic 13,75% em 2026"}}

    monkeypatch.setattr(d, "_parecer_llm_cached", falso)
    return visto


def test_prompt_trava_o_veto_por_noticia_ou_preco():
    assert "5.5." in d._PROMPT_PARECER
    regra = d._PROMPT_PARECER[d._PROMPT_PARECER.index("5.5."):]
    assert "NUNCA sustentam um veto sozinhos" in regra
    assert '"aprovar_com_ressalvas"' in regra
    assert "opinião de preço, não veto" in regra


def test_prompt_tem_campo_proprio_para_os_numeros():
    assert '"comportamento_de_mercado"' in d._PROMPT_PARECER
    assert "relatorio.comportamento_de_mercado" in d._PROMPT_PARECER


def test_contexto_chega_ao_prompt_com_a_regra(monkeypatch):
    visto = _capturar_prompt(monkeypatch)
    ctx = "CONTEXTO DE MERCADO: Selic 13,75% {chave_literal}"
    parecer, _ = d.gerar_parecer_empresa(
        "BOAA3", dossie=harness.SOLIDA.dossie, contexto_mercado=ctx)
    assert ctx in visto["prompt"]
    assert REGRA_CONTEXTO_MERCADO in visto["prompt"]
    assert parecer["relatorio"]["comportamento_de_mercado"] == "Selic 13,75% em 2026"


def test_sem_contexto_o_prompt_diz_lacuna_e_nao_calmaria(monkeypatch):
    visto = _capturar_prompt(monkeypatch)
    d.gerar_parecer_empresa("BOAA3", dossie=harness.SOLIDA.dossie)
    assert d._SEM_CONTEXTO_MERCADO in visto["prompt"]
    assert "lacuna de contexto" in visto["prompt"]


def test_montagem_pede_so_o_noticiario_do_ativo(monkeypatch):
    import core.contexto_mercado as cm
    import core.llm_context_b3 as lc

    chamadas: dict = {}

    def bloco(ativos, **kw):
        chamadas["ativos"], chamadas["kw"] = ativos, kw
        return "BLOCO MACRO"

    monkeypatch.setattr(cm, "bloco_contexto_mercado", bloco)
    monkeypatch.setattr(lc, "get_warehouse_detail_context", lambda tks: "DETALHE " + tks[0])
    txt = d.contexto_mercado_para_parecer("TAEE11")
    assert txt == "BLOCO MACRO\n\nDETALHE TAEE11"
    assert chamadas["ativos"] == {"b3": {"TAEE11": ""}}
    # a idade da vitrine geral muda de hora em hora e quebraria o cache diário
    assert chamadas["kw"] == {"noticias_gerais": False}


def test_fonte_que_falha_e_nomeada_e_nao_derruba_o_gate(monkeypatch):
    import core.contexto_mercado as cm
    import core.llm_context_b3 as lc

    def quebra(*a, **k):
        raise RuntimeError("banco fora")

    monkeypatch.setattr(cm, "bloco_contexto_mercado", quebra)
    monkeypatch.setattr(lc, "get_warehouse_detail_context", quebra)
    txt = d.contexto_mercado_para_parecer("TAEE11")
    assert "CONTEXTO DE MERCADO: falha ao montar (banco fora)" in txt
    assert "DETALHE DO ARMAZÉM: falha ao montar (banco fora)" in txt


def test_gate_de_selecao_entrega_o_contexto_ao_parecer(monkeypatch):
    recebido: dict = {}
    monkeypatch.setattr(d, "contexto_mercado_para_parecer", lambda tk: f"CTX {tk}")

    def parecer(tk, **kw):
        recebido.update(kw)
        return {"classificacao_selecao": "aprovar", "motivo_selecao": ""}, {}

    monkeypatch.setattr(d, "gerar_parecer_empresa", parecer)
    d.avaliar_para_selecao("taee11.sa")
    assert recebido["contexto_mercado"] == "CTX TAEE11"


def test_harness_injeta_contexto_sintetico_nas_duas_direcoes():
    assert harness._contexto_do_caso(harness.SOLIDA, "vazio") == ""
    adverso = harness._contexto_do_caso(harness.SOLIDA, "adverso")
    favoravel = harness._contexto_do_caso(harness.PATRIMONIO_NEGATIVO, "favoravel")
    assert "BOAA3" in adverso and "despencam" in adverso
    assert "QUEB3" in favoravel and "dispara" in favoravel
    assert "{" not in adverso + favoravel
