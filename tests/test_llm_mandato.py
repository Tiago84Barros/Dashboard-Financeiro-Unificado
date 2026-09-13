"""A LLM foi contratada para opinar, e estava se recusando.

A tela devolvia: *"Por regras de compliance e atuando estritamente como apoio à
análise, não posso emitir recomendação personalizada de compra, venda ou
alteração de alocação de ativos"*. Isso não é prudência -- é o produto deixando
de existir. O app é a ferramenta de análise do próprio investidor, não uma
corretora falando com terceiros.

O que substitui a proibição não é liberdade: é a EXIGÊNCIA de que toda sugestão
venha ancorada. Continua proibido inventar -- preço-alvo, projeção de cotação,
qualquer número fora do contexto --, e isso é veracidade, não compliance.
"""
import re

import pytest

from core.llm_mandato import MANDATO, MANDATO_CURTO

MODULOS = ("core.llm_carteira", "core.llm_ativo", "core.llm_fii",
           "core.llm_global", "core.llm_b3", "core.llm_financeiro")

# Frases que produziam a recusa. Nenhuma delas pode voltar a um prompt.
RECUSAS = (
    r"não\s+(?:posso|pode)\s+emitir\s+recomenda",
    r"não\s+emita\s+recomendação",
    r"apoio\s+à\s+análise,\s*não\s+recomendação",
    r"não\s+sou\s+consultor",
)


def _fonte(modulo: str) -> str:
    import importlib
    import inspect
    return inspect.getsource(importlib.import_module(modulo))


@pytest.mark.parametrize("modulo", MODULOS)
def test_nenhum_prompt_proibe_a_llm_de_concluir(modulo):
    fonte = _fonte(modulo)
    for padrao in RECUSAS:
        achado = re.search(padrao, fonte, re.IGNORECASE)
        assert achado is None, f"{modulo}: {achado.group(0)!r} voltou ao prompt"


@pytest.mark.parametrize("modulo", MODULOS)
def test_todo_prompt_carrega_o_mandato(modulo):
    assert "exerça julgamento" in _fonte(modulo).lower() or "MANDATO" in _fonte(modulo)


def test_o_mandato_exige_ancora_e_nao_apenas_permite_opinar():
    texto = MANDATO.lower()
    # Sem estas três, "pode opinar" vira "pode chutar".
    assert "evidência" in texto
    assert "derrub" in texto        # o dado que derrubaria a sugestão
    assert "risco" in texto


def test_a_proibicao_de_inventar_numero_permanece():
    for texto in (MANDATO.lower(), MANDATO_CURTO.lower()):
        assert "preço-alvo" in texto or "preco-alvo" in texto
