"""Receitas por categoria no contexto do chat do Controle Financeiro.

Lacuna b98a68c2: a LLM recusou "quanto daria só com o salário" porque o
contexto levava só o total de receitas. As linhas estavam em
``dados_mes["transacoes"]`` -- as mesmas que somam o total --, com categoria e
descrição, e o montador as ignorava.
"""
import pytest

from core.llm_context_financeiro import build_financas_chat_context


def _tx(descricao, valor, categoria, *, receita=True, data="05/10"):
    return {"descricao": descricao, "valor": valor, "categoria": categoria,
            "data_fmt": data, "eh_receita": receita}


def _contexto(dados_mes):
    base = {"despesas": 0.0, "categorias": [], "data_source": "real",
            "num_transacoes": len(dados_mes.get("transacoes") or [])}
    return build_financas_chat_context(
        user_question="e se for só o salário?",
        dados_mes={**base, **dados_mes},
        historico=[], hist_anual={}, gastos_categoria_anual=[], gastos_cartao={},
        evolucao={}, investido_mes=0.0, ano_ref=2026, mes_ref=10,
    )


def test_receitas_do_mes_chegam_por_categoria_e_lancamento():
    contexto, meta = _contexto({
        "receitas": 23919.56,
        "transacoes": [
            _tx("Salário outubro", 21719.56, "Salário"),
            _tx("Aluguel apto", 2200.00, "Aluguel Recebido", data="10/10"),
            _tx("Mercado", -850.00, "Alimentação", receita=False),
        ],
    })

    assert "RECEITAS POR CATEGORIA NO MÊS" in contexto
    assert "Salário: R$ 21.719,56" in contexto
    assert "Aluguel Recebido: R$ 2.200,00" in contexto
    assert "Salário outubro" in contexto          # o lançamento, não só a soma
    assert "Mercado" not in contexto               # despesa não vira receita

    cats = {c["nome"]: c["valor"] for c in meta["receitas_categorias_mes"]}
    assert cats == {"Salário": pytest.approx(21719.56),
                    "Aluguel Recebido": pytest.approx(2200.00)}


def test_receita_sem_categoria_aparece_como_tal():
    contexto, meta = _contexto({
        "receitas": 500.0,
        "transacoes": [_tx("Pix recebido", 500.0, None)],
    })
    assert "(sem categoria): R$ 500,00" in contexto
    assert meta["receitas_categorias_mes"][0]["nome"] == "(sem categoria)"


def test_total_sem_lancamentos_declara_que_falta_o_detalhe():
    """Quem chama sem ``transacoes`` (ou mock antigo) não pode parecer mês sem
    receita: o texto diz que o detalhamento não veio."""
    contexto, meta = _contexto({"receitas": 1000.0})
    assert "Detalhamento das receitas indisponível" in contexto
    assert meta["receitas_categorias_mes"] == []


def test_detalhe_que_nao_fecha_com_o_total_e_sinalizado():
    contexto, _ = _contexto({
        "receitas": 1000.0,
        "transacoes": [_tx("Salário", 800.0, "Salário")],
    })
    assert "não fecha com o total" in contexto
