"""Toda rota declarada precisa ter entrada no menu -- e vice-versa.

Duas telas nasceram inalcançáveis: ``🎯 Grau de Confiança`` (commit 76902ff) e
``🚦 Homologação`` (commit e7bd34b). As duas entraram em ``_ROTAS``, as duas
foram esquecidas em ``opcoes_menu``, e nenhuma das duas deu erro: o roteamento
por dicionário aceita chave que ninguém escolhe.

Em 06/09/2026 as duas saíram da sidebar, por caminhos opostos: Homologação foi
retirada junto com Inteligência de Mercado e Macro Internacional (retaguarda,
não tela), e Grau de Confiança virou aba de Configurações. O invariante segue
o mesmo -- rota e porta de entrada andam juntas.

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


_RETIRADAS = ("🧭 Inteligência de Mercado", "🌍 Macro Internacional",
              "🚦 Homologação")


def test_telas_retiradas_da_sidebar_nao_voltam_como_rota_orfa():
    """Retirar da sidebar sem tirar de ``_ROTAS`` recria a tela inalcançável.

    As três saíram da navegação a pedido do dono do app: são retaguarda
    analítica, não tela de consumo. O módulo continua em ``views/`` -- o que
    não pode voltar é a chave de rota que ninguém escolhe.
    """
    rotas = _rotas()
    for label in _RETIRADAS:
        assert label not in rotas, f"{label} voltou a _ROTAS sem porta de entrada"


def test_grau_de_confianca_e_aba_de_configuracoes():
    """A tela não sumiu: mudou de porta de entrada.

    Ela deixou de ser rota da sidebar e virou aba dentro de Configurações. O
    teste lê a view real: se a aba for renomeada ou o corpo deixar de ser
    chamado, a tela vira decoração de novo
    (``memoria: diagnostico-precisa-porta-de-entrada``).
    """
    assert "🎯 Grau de Confiança" not in _rotas()
    fonte = Path("views/configuracoes.py").read_text(encoding="utf-8")
    assert "🎯 Grau de Confiança" in fonte
    assert "from views.confianca import render_corpo" in fonte
    assert "render_corpo()" in fonte
    assert hasattr(__import__("views.confianca", fromlist=["render_corpo"]),
                   "render_corpo")


def test_o_modulo_de_cada_rota_existe_em_views():
    """Rota que aponta para módulo inexistente só falha ao ser clicada."""
    dic = _ATRIB["_ROTAS"]
    for valor in dic.values:
        assert isinstance(valor, ast.Constant)
        assert Path(f"views/{valor.value}.py").exists(), valor.value


def test_documentacao_nao_cita_rota_que_o_menu_nao_tem():
    """Texto que descreve a navegação envelhece invertido.

    Tirar as três telas da sidebar não tocou em ``docs/``: dois arquivos seguiam
    afirmando ``Rota `🧭 Inteligência de Mercado``` e ``rota **🚦 Homologação**``
    como se o menu ainda as tivesse. A frase não quebra nada e continua soando
    como documentação em dia (``memoria: aviso-que-envelhece-invertido``).

    O conjunto de rotas vem de ``app.py``, nunca de lista escrita aqui.
    """
    import re

    rotas = _rotas()
    citacao = re.compile(r"[Rr]ota\s+[`*]{1,2}([^`*\n]{3,40})[`*]{1,2}")
    orfas: list[str] = []
    for doc in sorted(Path("docs").glob("*.md")):
        texto = doc.read_text(encoding="utf-8")
        for achado in citacao.finditer(texto):
            alvo = achado.group(1).strip()
            if alvo not in rotas:
                linha = texto[:achado.start()].count("\n") + 1
                orfas.append(f"{doc}:{linha} cita a rota {alvo!r}")

    assert not orfas, (
        "documentação cita rota que não existe no menu de app.py; a tela pode "
        "ter saído da navegação sem que o texto acompanhasse:\n  "
        + "\n  ".join(orfas)
    )
