"""Chat da Visão Geral da Análise do Portfólio: a carteira inteira como escopo.

O que se prende aqui é o que faria o chat mentir sem quebrar: o saldo saindo
sem o toggle, a regra 2 dizendo "só esta classe" sobre um contexto que é o
consolidado, e o retorno mercado/custo chegando à LLM com cara de
rentabilidade.
"""
from __future__ import annotations

import pathlib
import sys

_RAIZ = pathlib.Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

import core.llm_carteira as llm_carteira  # noqa: E402
from core.llm_carteira import regras_da_analise  # noqa: E402
from core.llm_context_carteira import build_carteira_geral_context  # noqa: E402


def _carteira():
    return {
        "total_investido": 100000.0,
        "total_mercado": 104000.0,
        "rentabilidade_total_pct": 4.0,
        "rentabilidade_total_disponivel": True,
        "cotacoes_disponiveis": True,
        "posicoes": [
            {"ticker": "BBAS3", "classe": "Ações BR", "setor": "Financeiro",
             "pct_carteira": 30.0, "rentab_pct": 12.5, "valor_mercado": 31200.0},
            {"ticker": "HGLG11", "classe": "FIIs", "setor": "Logística",
             "pct_carteira": 50.0, "rentab_pct": -3.0, "valor_mercado": 52000.0},
            {"ticker": "IVVB11", "classe": "ETF", "setor": "Índice",
             "pct_carteira": 20.0, "rentab_pct": 8.0, "valor_mercado": 20800.0,
             "retorno_brl_disponivel": False},
        ],
        "por_classe": [
            {"nome": "FIIs", "pct_carteira": 50.0, "num_ativos": 1, "rentab_pct": -3.0},
            {"nome": "Ações BR", "pct_carteira": 30.0, "num_ativos": 1, "rentab_pct": 12.5},
        ],
        "por_setor": [{"nome": "Logística", "pct_carteira": 50.0}],
    }


def _proventos():
    return {"total_12m": 8000.0,
            "por_ativo_12m": [{"ticker": "HGLG11", "total": 6000.0},
                              {"ticker": "BBAS3", "total": 2000.0}]}


def test_sem_toggle_nenhum_valor_em_reais_sai():
    texto = build_carteira_geral_context(_carteira(), _proventos())
    assert "R$" not in texto
    assert "104" not in texto  # nem o valor de mercado cru
    assert "HGLG11: 50.0%" in texto
    assert "Renda recebida em 12 meses sobre o custo: 8.00%" in texto
    assert "HGLG11 75.0%" in texto


def test_toggle_ligado_envia_o_consolidado():
    texto = build_carteira_geral_context(_carteira(), _proventos(), valores_reais=True)
    assert "R$ 104.000,00" in texto
    assert "R$ 8.000,00" in texto


def test_concentracao_e_retorno_declarados_com_limite():
    texto = build_carteira_geral_context(_carteira(), _proventos())
    assert "Posições acima de 10%: HGLG11, BBAS3, IVVB11" in texto
    assert "não inclui proventos" in texto
    # Retorno em BRL indisponível não vira número.
    assert "IVVB11: 20.0% | ETF | Índice | ausente" in texto


def test_carteira_vazia_nao_quebra():
    texto = build_carteira_geral_context({}, None)
    assert "sem dado de alocação" in texto
    assert "Renda recebida em 12 meses: ausente" in texto


def test_regra_2_acompanha_o_escopo():
    assert "apenas esta classe" in regras_da_analise()
    geral = regras_da_analise(geral=True)
    assert "apenas esta classe" not in geral
    assert "CONTEXTO DA CARTEIRA" in geral
    assert "permitida" in geral  # a regra 7 é a mesma nos dois recortes


def test_chat_geral_monta_o_prompt_da_carteira(monkeypatch):
    capturado = {}

    def _falso(messages, **_):
        capturado["system"] = messages[0]["content"]
        return "ok"

    monkeypatch.setattr(llm_carteira, "_chat_complete", _falso)
    monkeypatch.setattr(llm_carteira, "_report_model", lambda: "x")
    assert llm_carteira.chat_com_carteira("CTX", [], "oi", classe="geral") == "ok"
    system = capturado["system"]
    assert "=== CONTEXTO DA CARTEIRA ===\nCTX" in system
    assert "a carteira inteira" in system
    assert "apenas esta classe" not in system


def test_barra_da_visao_geral_sem_dossie_e_com_toggle_do_consolidado(monkeypatch):
    # O AppTest roda no mesmo processo: o dublê entra por monkeypatch, fora do
    # app, para ser desfeito no fim e não vazar para o resto da suíte.
    from streamlit.testing.v1 import AppTest

    import design.chat_carteira as chat

    monkeypatch.setattr(chat, "llm_disponivel", lambda: True)
    monkeypatch.setattr(chat, "provedores_disponiveis", lambda: ["openai"])
    monkeypatch.setattr(chat, "load_chat_history", lambda *_a, **_k: [])
    monkeypatch.setattr(chat, "save_chat_history", lambda *_a, **_k: None)
    monkeypatch.setattr(
        chat, "chat_com_carteira",
        lambda contexto, _hist, _perg, *, classe: f"classe={classe} | {contexto}")

    def _app():
        import design.chat_carteira as chat

        chat.render_chat_carteira(
            classe="geral", tickers=["BBAS3", "HGLG11"],
            build_context=lambda _p, *, valores_reais=False: f"reais={valores_reais}")

    app = AppTest.from_function(_app).run(timeout=30)
    assert not app.exception
    rotulos = [b.label for b in app.button]
    assert not any("dossiê" in r for r in rotulos)
    assert app.checkbox[0].label == "Enviar os valores em reais da carteira inteira à LLM"
    assert len(app.chat_input) == 1

    app.chat_input[0].set_value("Onde estou concentrado?").run(timeout=30)
    respostas = [m.markdown[0].value for m in app.chat_message if m.name == "assistant"]
    assert respostas == ["classe=geral | reais=False"]
