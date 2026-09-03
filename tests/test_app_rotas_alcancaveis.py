"""Toda rota declarada precisa ter entrada no menu -- e vice-versa.

Duas telas nasceram inalcançáveis: ``🎯 Grau de Confiança`` (commit 76902ff) e
``🚦 Homologação`` (commit e7bd34b). As duas entraram em ``_ROTAS``, as duas
foram esquecidas em ``opcoes_menu``, e nenhuma das duas deu erro: o roteamento
por dicionário aceita chave que ninguém escolhe. A de Homologação é a que diz
em que fase de liberação o APP4 está.

O teste não lista as rotas à mão -- ele as **deriva do próprio app.py**. Lista
escrita à parte envelhece junto com o defeito que deveria pegar
(``memoria: verificador-e-escritor-listas-diferentes``): a rota número quinze
seria esquecida no menu e no teste pelo mesmo descuido.

Ler por AST, e não importar: ``app.py`` executa ``st.set_page_config`` e a
autenticação no topo do módulo. É o padrão já usado em
``tests/test_app_error_handling.py``.
"""
from __future__ import annotations

import ast
from pathlib import Path

_ARVORE = ast.parse(Path("app.py").read_text(encoding="utf-8"))


def _constantes(node: ast.AST) -> list[str]:
    return [e.value for e in ast.walk(node)
            if isinstance(e, ast.Constant) and isinstance(e.value, str)]


def _atribuicoes() -> dict[str, ast.AST]:
    achados: dict[str, ast.AST] = {}
    for node in ast.walk(_ARVORE):
        if isinstance(node, ast.Assign):
            for alvo in node.targets:
                if isinstance(alvo, ast.Name):
                    achados.setdefault(alvo.id, node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            achados.setdefault(node.target.id, node.value)
    return achados


_ATRIB = _atribuicoes()


def _rotas() -> set[str]:
    dic = _ATRIB["_ROTAS"]
    assert isinstance(dic, ast.Dict)
    return {c.value for c in dic.keys
            if isinstance(c, ast.Constant) and isinstance(c.value, str)}


def _menu_de_producao() -> set[str]:
    """As opções do ramo real -- ``_APP_TEST_MODE`` tem menu próprio e menor."""
    nomes = ("opcoes_visao", "opcoes_financas", "opcoes_invest", "opcoes_sistema")
    itens: set[str] = set()
    for nome in nomes:
        assert nome in _ATRIB, f"{nome} sumiu de app.py; o teste precisa acompanhar"
        itens.update(_constantes(_ATRIB[nome]))
    return itens


def test_toda_rota_declarada_aparece_no_menu():
    faltando = _rotas() - _menu_de_producao()
    assert not faltando, (
        "rota sem porta de entrada na sidebar -- a tela existe e ninguém "
        f"chega nela: {sorted(faltando)}")


def test_todo_item_do_menu_tem_rota():
    """O inverso: item que não roteia leva a tela em branco, sem erro."""
    orfas = _menu_de_producao() - _rotas()
    assert not orfas, f"item de menu sem rota em _ROTAS: {sorted(orfas)}"


def test_as_duas_telas_esquecidas_estao_no_menu():
    """Nomeadas de propósito: foram o defeito, e um dia foram removidas."""
    menu = _menu_de_producao()
    assert "🚦 Homologação" in menu
    assert "🎯 Grau de Confiança" in menu


def test_o_modulo_de_cada_rota_existe_em_views():
    """Rota que aponta para módulo inexistente só falha ao ser clicada."""
    dic = _ATRIB["_ROTAS"]
    for valor in dic.values:
        assert isinstance(valor, ast.Constant)
        assert Path(f"views/{valor.value}.py").exists(), valor.value
