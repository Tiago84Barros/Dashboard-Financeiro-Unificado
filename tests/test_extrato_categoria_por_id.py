"""As telas de extrato resolvem a categoria pelo ID, nunca pelo nome.

O banco tem três ``Outros`` (despesa, receita e investimento — migration 072).
A prévia do upload e a fila de revisão montavam ``{nome: categoria}``, que
guardava um só deles: uma saída classificada em ``Outros`` podia ser gravada
apontando para o ``Outros`` de receita, e editar só a descrição de um
movimento na fila trocava a categoria dele sem ninguém pedir.
"""
from __future__ import annotations

import inspect

from core.bank_statement_import import (
    ROTULO_PENDENTE,
    rotulo_da_linha,
    rotulos_de_categoria,
)


def _cat(cid, nome, tipo, minha=False):
    return {"id": cid, "nome": nome, "tipo": tipo, "minha": minha}


CATEGORIAS = [
    _cat("e1", "Outros", "expense"),
    _cat("i1", "Outros", "income"),
    _cat("v1", "Outros", "investment"),
    _cat("m1", "Mercado", "expense"),
    _cat("s1", "Saúde", "expense"),
]


def test_nome_repetido_ganha_o_tipo_e_cada_rotulo_volta_ao_proprio_id():
    por_rotulo, rotulo_por_id = rotulos_de_categoria(CATEGORIAS)
    assert rotulo_por_id == {
        "e1": "Outros (despesa)", "i1": "Outros (receita)", "v1": "Outros (investimento)",
        "m1": "Mercado", "s1": "Saúde",
    }
    for cat in CATEGORIAS:
        assert por_rotulo[rotulo_por_id[cat["id"]]] is cat


def test_nenhuma_categoria_some_do_seletor():
    por_rotulo, _ = rotulos_de_categoria(CATEGORIAS)
    assert len(por_rotulo) == len(CATEGORIAS)


def test_mesma_categoria_de_sistema_e_da_pessoa_ficam_distintas():
    cats = [_cat("sis", "Lazer", "expense"), _cat("sua", "Lazer", "expense", minha=True)]
    por_rotulo, rotulo_por_id = rotulos_de_categoria(cats)
    assert rotulo_por_id["sis"] == "Lazer (despesa)"
    assert rotulo_por_id["sua"] == "Lazer (despesa, sua)"
    assert len(por_rotulo) == 2


def test_categoria_chamada_pendente_nao_colide_com_a_opcao_vazia():
    por_rotulo, rotulo_por_id = rotulos_de_categoria([_cat("p1", "Pendente", "expense")])
    assert rotulo_por_id["p1"] != ROTULO_PENDENTE
    assert ROTULO_PENDENTE not in por_rotulo


def test_rotulo_da_linha_vem_do_id_e_prefere_o_confirmado():
    _, rotulo_por_id = rotulos_de_categoria(CATEGORIAS)
    assert rotulo_da_linha({"categoria_id": "e1"}, rotulo_por_id) == "Outros (despesa)"
    assert rotulo_da_linha({"categoria_id": "e1", "categoria_confirmada_id": "i1"},
                           rotulo_por_id) == "Outros (receita)"


def test_linha_sem_id_conhecido_fica_pendente_mesmo_com_nome_igual():
    """O nome sozinho não decide: era assim que o tipo errado entrava."""
    _, rotulo_por_id = rotulos_de_categoria(CATEGORIAS)
    assert rotulo_da_linha({"categoria_nome": "Mercado"}, rotulo_por_id) == ROTULO_PENDENTE
    assert rotulo_da_linha({"categoria_id": None, "categoria_sugerida_texto": "Outros"},
                           rotulo_por_id) == ROTULO_PENDENTE
    assert rotulo_da_linha({"categoria_id": "sumiu"}, rotulo_por_id) == ROTULO_PENDENTE


def test_editar_so_a_descricao_nao_troca_a_categoria():
    """Ida e volta da fila de revisão: o rótulo exibido resolve ao MESMO id."""
    por_rotulo, rotulo_por_id = rotulos_de_categoria(CATEGORIAS)
    for cid in ("e1", "i1", "v1"):
        exibido = rotulo_da_linha({"categoria_confirmada_id": cid}, rotulo_por_id)
        assert por_rotulo[exibido]["id"] == cid


def test_telas_de_extrato_nao_indexam_categoria_por_nome():
    import views.bank_statement_upload as bsu
    import views.controle_financeiro as cf

    for fonte in (inspect.getsource(bsu._render_upload), inspect.getsource(cf._editor_extratos)):
        assert 'c["nome"]: c' not in fonte
        assert "rotulos_de_categoria" in fonte
