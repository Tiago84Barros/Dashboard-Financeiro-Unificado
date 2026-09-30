"""Lacuna 98182687: follow-up sem ticker perdia os múltiplos dos nomes da conversa.

A pergunta "monte um ranking dos 5 nomes" não cita ticker; os nomes vieram do
turno anterior. O construtor só olhava a pergunta atual, e a LLM respondia que
os múltiplos de BRAV3 e RAIZ4 "não foram carregados" — estando no banco.
"""
import pandas as pd

import core.conjuntura as conj
import core.llm_context_b3 as ctx


def _stub(monkeypatch):
    universo = pd.DataFrame({
        "Ticker": ["BRAV3", "RAIZ4", "WEGE3"], "P/L": [40.27, None, 30.0],
        "ROE": [0.02, None, 0.3], "DY": [0.006, None, 0.02],
        "SETOR": ["Petróleo", "Consumo Cíclico", "Bens Industriais"],
    })
    monkeypatch.setattr(ctx, "_universe_with_sector", lambda: universo)
    monkeypatch.setattr(ctx, "get_warehouse_detail_context", lambda t: "")
    monkeypatch.setattr(ctx, "get_chunks_context", lambda *a, **k: "")
    monkeypatch.setattr(ctx, "get_dre_history_context", lambda t: "")
    monkeypatch.setattr(conj, "bloco_para_prompt", lambda **k: "")


def _montar(pergunta, historico=None):
    return ctx.build_llm_context_for_portfolio_chat(
        pergunta, "BASE", {"items": [{"ticker": "WEGE3", "weight": 1.0}]},
        {"WEGE3": 1.0}, portfolio_tickers=["WEGE3"], history=historico)


def test_follow_up_sem_ticker_recebe_multiplos_dos_nomes_da_conversa(monkeypatch):
    _stub(monkeypatch)
    historico = [
        {"role": "user", "content": "Vale olhar petróleo e açúcar?"},
        {"role": "assistant", "content": "Candidatas fora da carteira: BRAV3 e RAIZ4."},
    ]
    contexto, meta = _montar("Monte um ranking dos 5 nomes por P/L e ROE.", historico)
    assert "BRAV3 [Petróleo]: P/L=40.27" in contexto
    assert "RAIZ4 [Consumo Cíclico]" in contexto
    assert meta["history_tickers"] == ["BRAV3", "RAIZ4"]


def test_pergunta_atual_vem_antes_do_historico_e_carteira_nao_repete(monkeypatch):
    _stub(monkeypatch)
    pedidos = []
    monkeypatch.setattr(ctx, "get_company_fundamentals_context",
                        lambda t: pedidos.append(list(t)) or "")
    historico = [{"role": "assistant", "content": "WEGE3, BRAV3 e RAIZ4"}]
    _montar("e a PETR4?", historico)
    assert pedidos == [["PETR4", "BRAV3", "RAIZ4"]]


def test_sem_historico_o_comportamento_nao_muda(monkeypatch):
    _stub(monkeypatch)
    contexto, meta = _montar("Qual o DY médio da carteira?")
    assert "FUNDAMENTOS DE EMPRESAS CONSULTADAS" not in contexto
    assert meta["history_tickers"] == []
