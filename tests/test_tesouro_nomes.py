"""Testes da traducao do codigo da B3 para o nome publico do Tesouro Direto.

O extrato da B3 publica o titulo pelo codigo da divida ("LFT mar/2031",
"NTNB PRINC ago/2032"). O site do Tesouro Direto -- e o investidor -- chamam
os mesmos papeis de "Tesouro Selic 2031" e "Tesouro IPCA+ 2032".

As armadilhas que estes testes prendem:

  - NTNB PRINC e NTNB sao titulos DIFERENTES (principal x com cupom) e caem
    no MESMO ticker `TIPCA2032`: o nome amigavel nao pode ser derivado do
    ticker, so do texto da fonte;
  - inventar nome para codigo desconhecido -- o certo e devolver o original;
  - perder o ano quando ele so existe na coluna Vencimento.
"""
from __future__ import annotations

import pytest

from core.tesouro_nomes import nome_amigavel


@pytest.mark.parametrize(
    "texto, vencimento, esperado",
    [
        # Codigos da divida, como a B3 publica.
        ("LFT mar/2031", "01/03/2031", "Tesouro Selic 2031"),
        ("LFT mar/2028", "01/03/2028", "Tesouro Selic 2028"),
        ("LTN jan/2032", "01/01/2032", "Tesouro Prefixado 2032"),
        (
            "NTNF jan/2035", "01/01/2035",
            "Tesouro Prefixado com Juros Semestrais 2035",
        ),
        # Principal (sem cupom) x com cupom: mesmo ticker, nomes distintos.
        ("NTNB PRINC ago/2032", "15/08/2032", "Tesouro IPCA+ 2032"),
        (
            "NTNB ago/2032", "15/08/2032",
            "Tesouro IPCA+ com Juros Semestrais 2032",
        ),
        # O ano pode vir so do vencimento.
        ("LFT", "01/03/2031", "Tesouro Selic 2031"),
        # ... ou so do texto.
        ("LTN jan/2028", None, "Tesouro Prefixado 2028"),
    ],
)
def test_traduz_codigo_da_b3(texto, vencimento, esperado):
    assert nome_amigavel(texto, vencimento) == esperado


@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("Tesouro Selic 2031", "Tesouro Selic 2031"),
        ("Tesouro IPCA+ 2032", "Tesouro IPCA+ 2032"),
        ("Tesouro Prefixado 2032", "Tesouro Prefixado 2032"),
        ("Tesouro Educa+ 2030", "Tesouro Educa+ 2030"),
        ("Tesouro Renda+ 2045", "Tesouro Renda+ 2045"),
    ],
)
def test_nome_ja_amigavel_passa_intacto(texto, esperado):
    """A XP ja entrega o nome publico -- traduzir de novo nao pode estragar."""
    assert nome_amigavel(texto) == esperado


def test_codigo_desconhecido_devolve_o_original():
    """Nao inventar: um codigo novo tem que sair legivel, nao virar 'Tesouro '.

    Se a B3 publicar uma familia que este mapa nao conhece, o certo e a tela
    mostrar o texto da fonte -- feio e verdadeiro -- em vez de um rotulo
    amigavel errado, que ninguem tem como desconfiar.
    """
    assert nome_amigavel("XPTO abr/2040", "01/04/2040") == "XPTO abr/2040"


def test_vazio_nao_quebra():
    assert nome_amigavel(None) == ""
    assert nome_amigavel("") == ""


def test_sem_ano_nao_inventa_ano():
    """Sem vencimento e sem ano no texto, o nome sai sem o ano."""
    assert nome_amigavel("LFT") == "Tesouro Selic"


# ─────────────────────────────────────────────────────────────────────────────
# Camada de exibição
# ─────────────────────────────────────────────────────────────────────────────

def test_nome_exibicao_traduz_so_o_tesouro():
    """A tradução vale para o Tesouro e para mais ninguém.

    `nome_amigavel` decide pelo prefixo do texto. Se a tela aplicasse a regra
    a todo ativo, uma empresa chamada "LFT Participações" apareceria como
    "Tesouro Selic" — e ninguém teria como desconfiar do rótulo.
    """
    from core.investimentos import _nome_exibicao

    assert _nome_exibicao("LFT mar/2031", "TSELIC2031", "tesouro") == (
        "Tesouro Selic 2031"
    )
    assert _nome_exibicao("LFT mar/2031", "TSELIC2031", "fixed_income") == (
        "Tesouro Selic 2031"
    )
    assert _nome_exibicao("LFT Participações SA", "LFTP3", "stock") == (
        "LFT Participações SA"
    )
    # Sem nome guardado, o ticker é o que sobra.
    assert _nome_exibicao(None, "BBAS3", "stock") == "BBAS3"
