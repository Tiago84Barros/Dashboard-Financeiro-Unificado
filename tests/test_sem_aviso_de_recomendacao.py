"""O app4 não exibe aviso de "não é recomendação" (decisão do usuário, 30/10/2026).

O banner "Ferramenta educacional — não é recomendação de investimento… rentabilidade
passada não garante resultado futuro" da Criação de Portfólio dos EUA saiu, e com ele
as legendas do mesmo tipo nos chats, nos relatórios e na Inteligência de Mercado.
Este teste lê os textos que chegam à tela e falha se um deles voltar.

Lê só literal de texto: docstring e comentário não aparecem para ninguém. O que fica
de fora de propósito é o que diz como ler o número — "a faixa é dispersão observada,
não previsão", "sem previsão de retorno" —, que é metodologia, não aviso.
"""
from __future__ import annotations

import ast
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

# Módulos de core cujo texto vai para a tela ou para o contexto da LLM.
_CORE_COM_TEXTO_DE_TELA = (
    "core/inteligencia/painel.py",
    "core/inteligencia_ativos/avaliacao.py",
    "core/inteligencia_ativos/relatorio_analista.py",
    "core/b3_portfolio_presets.py",
    "core/llm_context_fii.py",
)

_TRECHOS = (
    "não é recomendação de",
    "não constitui recomendação",
    "não constitui garantia",
    "não constitui consultoria",
    "nada aqui é recomendação",
    "apoio analítico — não é recomendação",
    "ferramenta educacional",
    "análise educacional",
    "garantia de retorno",
    "garantia de resultado",
    "garantias de resultado",
    "não garante o futuro",
    "não garante resultado",
    "rentabilidade passada",
)


def _arquivos() -> list[Path]:
    fontes = sorted((RAIZ / "views").rglob("*.py")) + sorted((RAIZ / "design").rglob("*.py"))
    return fontes + [RAIZ / c for c in _CORE_COM_TEXTO_DE_TELA]


def _docstrings(arvore: ast.AST) -> set[int]:
    ids = set()
    for no in ast.walk(arvore):
        if isinstance(no, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            corpo = getattr(no, "body", [])
            if (corpo and isinstance(corpo[0], ast.Expr)
                    and isinstance(corpo[0].value, ast.Constant)
                    and isinstance(corpo[0].value.value, str)):
                ids.add(id(corpo[0].value))
    return ids


def _textos(caminho: Path):
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    pular = _docstrings(arvore)
    for no in ast.walk(arvore):
        if (isinstance(no, ast.Constant) and isinstance(no.value, str)
                and id(no) not in pular):
            yield no.lineno, " ".join(no.value.split())


def test_nenhum_texto_de_tela_traz_aviso_de_recomendacao():
    achados = []
    for caminho in _arquivos():
        for linha, texto in _textos(caminho):
            minusculo = texto.lower()
            for trecho in _TRECHOS:
                if trecho in minusculo:
                    achados.append(f"{caminho.relative_to(RAIZ)}:{linha}: {trecho!r}")
    assert not achados, "\n".join(achados)


def test_o_banner_da_criacao_dos_eua_saiu():
    fonte = (RAIZ / "views" / "empresas_americanas.py").read_text(encoding="utf-8")
    assert "Ferramenta educacional" not in fonte
    assert "Rentabilidade passada" not in fonte


def test_o_detector_acha_o_aviso_quando_ele_existe(tmp_path):
    """Sem isto, um _TRECHOS mal escrito passaria por limpo para sempre."""
    falso = tmp_path / "tela.py"
    falso.write_text(
        'import streamlit as st\n'
        'st.info("⚠️ **Ferramenta educacional — não é recomendação de investimento.** "\n'
        '        "Rentabilidade passada não garante resultado futuro.")\n',
        encoding="utf-8")
    textos = [t.lower() for _, t in _textos(falso)]
    assert any(trecho in t for t in textos for trecho in _TRECHOS)
