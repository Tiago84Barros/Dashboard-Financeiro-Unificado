"""O cifrão em texto de chat não pode virar fórmula.

O defeito que originou estes testes não parecia defeito de renderização: a
resposta citava dois valores em reais, o KaTeX engolia o trecho entre eles e a
tela mostrava uma fórmula verde no lugar do texto. Quem lê conclui que a LLM
escreveu outra coisa.

O segundo grupo de testes existe porque a correção é a mesma em seis arquivos.
Guarda repetida diverge: basta um chat novo esquecer o escape para o bug voltar
só naquela tela, e ninguém percebe até ver o print.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from core.utils import escapar_cifrao

_RAIZ = pathlib.Path(__file__).resolve().parents[1]

# Arquivos que desenham mensagem de chat com st.markdown. Novo chat entra aqui.
_TELAS_COM_CHAT = (
    "design/chat_carteira.py",
    "design/chat_ativo.py",
    "views/analise_portfolio_us.py",
    "views/analise_portfolio_b3.py",
    "views/controle_financeiro.py",
    "views/portfolio_global.py",
)

# Nomes cujo conteúdo é texto livre — do usuário ou da LLM — e não HTML nosso.
_TEXTO_LIVRE = {"resposta", "pergunta", "content"}


# ── comportamento ────────────────────────────────────────────────────────────

def test_par_de_valores_nao_vira_formula():
    bruto = "BBAS3 R$ 34.090,95 e preço médio R$ 23,92"
    assert escapar_cifrao(bruto) == r"BBAS3 R\$ 34.090,95 e preço médio R\$ 23,92"


def test_cifrao_solitario_tambem_escapa():
    # Um só já basta: o próximo turno da conversa fecha o par.
    assert escapar_cifrao("total de R$ 1,00") == r"total de R\$ 1,00"


def test_nao_escapa_duas_vezes():
    # Escapar o que já veio escapado imprime a barra invertida na tela.
    assert escapar_cifrao(r"R\$ 10,00") == r"R\$ 10,00"


def test_dentro_de_bloco_de_codigo_fica_intacto():
    # Em código o cifrão nunca chegou ao KaTeX; a barra ali seria invenção.
    bruto = "veja:\n```bash\necho $HOME\n```\ncusta R$ 5,00"
    saida = escapar_cifrao(bruto)
    assert "echo $HOME" in saida
    assert r"custa R\$ 5,00" in saida


def test_codigo_inline_fica_intacto():
    bruto = "use `$PATH` para R$ 3,00"
    saida = escapar_cifrao(bruto)
    assert "`$PATH`" in saida
    assert r"R\$ 3,00" in saida


@pytest.mark.parametrize("vazio", ["", None])
def test_vazio_nao_explode(vazio):
    assert escapar_cifrao(vazio) == ""


def test_texto_sem_cifrao_sai_identico():
    bruto = "ROE de 41,4% e DY de 13,0%"
    assert escapar_cifrao(bruto) == bruto


# ── unicidade da guarda: pela AST, não pelo comportamento de uma tela ────────

def _markdowns_de_texto_livre(fonte: str) -> list[ast.Call]:
    """st.markdown cujo argumento é texto livre de chat, não HTML montado aqui."""
    achados = []
    for no in ast.walk(ast.parse(fonte)):
        if not isinstance(no, ast.Call):
            continue
        fn = no.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "markdown"):
            continue
        if not (isinstance(fn.value, ast.Name) and fn.value.id == "st"):
            continue
        if not no.args:
            continue
        arg = no.args[0]
        nome = None
        if isinstance(arg, ast.Name):
            nome = arg.id
        elif isinstance(arg, ast.Subscript) and isinstance(arg.slice, ast.Constant):
            nome = arg.slice.value
        elif isinstance(arg, ast.Call):  # já embrulhado
            nome = None
        if nome in _TEXTO_LIVRE:
            achados.append(no)
    return achados


@pytest.mark.parametrize("arquivo", _TELAS_COM_CHAT)
def test_toda_mensagem_de_chat_passa_pelo_escape(arquivo):
    fonte = (_RAIZ / arquivo).read_text(encoding="utf-8")
    nus = _markdowns_de_texto_livre(fonte)
    assert not nus, (
        f"{arquivo}: st.markdown com texto de chat sem escapar_cifrao nas linhas "
        + ", ".join(str(no.lineno) for no in nus)
    )


@pytest.mark.parametrize("arquivo", _TELAS_COM_CHAT)
def test_tela_importa_o_helper_comum(arquivo):
    fonte = (_RAIZ / arquivo).read_text(encoding="utf-8")
    assert "escapar_cifrao" in fonte, f"{arquivo} não usa o helper comum"
    assert "from core.utils import" in fonte, (
        f"{arquivo} deve importar de core.utils — cópia local diverge"
    )
