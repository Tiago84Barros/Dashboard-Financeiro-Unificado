"""IPCA Pessoal no contexto do chat do Controle Financeiro.

Premissa do projeto: a LLM recebe todo dado disponível. Quando a conta não é
possível, o contexto diz por quê, em vez de calar.
"""
from __future__ import annotations

from datetime import date

from core.ipca_pessoal import ORIGEM_ITENS, CategoriaIpca, ResultadoIpcaPessoal
from core.llm_context_financeiro import build_financas_chat_context


def _contexto(**extra):
    base = {"despesas": 0.0, "categorias": [], "data_source": "real",
            "num_transacoes": 0}
    contexto, _ = build_financas_chat_context(
        user_question="minha inflação está acima do IPCA?",
        dados_mes=base, historico=[], hist_anual={}, gastos_categoria_anual=[],
        gastos_cartao={}, evolucao={}, investido_mes=0.0, ano_ref=2026, mes_ref=10,
        **extra,
    )
    return contexto


def test_resultado_entra_no_contexto():
    res = ResultadoIpcaPessoal(
        ok=True, inicio=date(2025, 11, 1), fim=date(2026, 10, 1),
        acumulado_12m=0.0612, oficial_12m=0.0480,
        categorias=(CategoriaIpca("Moradia", "Habitação", True, 0.5, 0.07,
                                  ORIGEM_ITENS, 12, 0.9, 0.035),),
    )
    contexto = _contexto(ipca_pessoal=res)
    assert "IPCA PESSOAL" in contexto
    assert "6,12%" in contexto and "4,80%" in contexto and "+1,32 p.p." in contexto
    assert "Moradia" in contexto and ORIGEM_ITENS in contexto


def test_falha_de_leitura_e_nomeada():
    assert "leitura dos lançamentos falhou" in _contexto(ipca_pessoal=None)


def test_conta_impossivel_diz_o_motivo():
    res = ResultadoIpcaPessoal(ok=False, motivo="há 12 meses e falta 1")
    assert "Não calculado: há 12 meses e falta 1" in _contexto(ipca_pessoal=res)


def test_sem_o_parametro_o_bloco_nao_aparece():
    assert "IPCA PESSOAL" not in _contexto()
