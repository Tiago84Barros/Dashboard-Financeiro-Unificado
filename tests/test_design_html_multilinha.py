"""Texto de fora não pode vazar a própria tag para a tela.

O defeito apareceu renderizando a Inteligência de Mercado com o armazém sem a
tabela de auditoria: a trava ``auditoria_falhou`` descreveu a falha com a
mensagem do psycopg2, que traz quebras de linha e um parágrafo em branco antes
do ``[SQL:``. ``html.escape`` fez o trabalho dele -- ``<``, ``&`` e ``"``
estavam escapados -- mas o markdown do Streamlit encerra o bloco HTML na
primeira linha em branco, e o resto da tag foi impresso no card::

    [SQL: SELECT 1 FROM public)" style="--badge-color:#D9534F;...">⊘ Auditoria

Escapar e achatar são guardas de canais diferentes. Estes testes cobrem os
dois, e cobrem o caso adversarial junto: achatar não pode desfazer o escape.
"""
from __future__ import annotations

import re

from design import componentes as cp
from design import inteligencia as di

# Como a mensagem do psycopg2 chega de verdade.
ERRO_DE_BANCO = (
    'relation "public.recomendacao_auditoria" does not exist\n'
    "LINE 1: SELECT 1 FROM public.recomendacao_auditoria LIMIT 1\n"
    "                      ^\n\n[SQL: SELECT 1 FROM public.recomendacao_auditoria]")


def _sem_quebra(html: str) -> bool:
    return "\n" not in html and "\r" not in html


# -- design.inteligencia -----------------------------------------------------
def test_selo_com_erro_de_banco_sai_em_uma_linha():
    html = di._selo("⊘", "Auditoria não gravou", "#D9534F",
                    f"trava disparada: {ERRO_DE_BANCO}")
    assert _sem_quebra(html)
    assert "&quot;" in html            # o escape continua de pé
    assert 'title="' in html and html.endswith("</span>")


def test_o_texto_do_erro_continua_legivel_depois_de_achatado():
    """Achatar não pode virar truncar: a informação toda continua no title."""
    html = di._linha(ERRO_DE_BANCO, aspas=True)
    for pedaco in ("recomendacao_auditoria", "does not exist", "[SQL:"):
        assert pedaco in html


def test_achatar_nao_desfaz_o_escape():
    for entrada in ('<script>alert(1)</script>', 'x" onmouseover="alert(1)',
                    'a\n<b\n\n&c"d'):
        html = di._linha(entrada, aspas=True)
        assert "<" not in html and ">" not in html
        assert '"' not in html
        assert _sem_quebra(html)


def test_selo_trava_de_verdade_nao_vaza_a_tag():
    """Pelo objeto real de trava, não por string montada à mão.

    É como o defeito chegou à tela: ``descrever()`` costura o detalhe -- a
    mensagem do banco -- dentro do texto da trava.
    """
    from core.seguranca import travas as tv

    trava = tv.Trava(nome="auditoria_falhou", disparada=True,
                     detalhe=ERRO_DE_BANCO)
    html = di.selo_trava(trava)
    assert _sem_quebra(html)
    assert re.fullmatch(r"<span[^>]*>.*</span>", html, re.S)


# -- design.componentes (o card compartilhado por 19 chamadores) -------------
def test_card_metrica_achata_titulo_valor_e_ajuda():
    assert _sem_quebra(cp._linha(ERRO_DE_BANCO, aspas=True))
    assert _sem_quebra(cp._linha("linha 1\n\nlinha 2"))


def test_linha_aceita_nao_string():
    """Mesma lição do ``str(delta)``: o componente converte, o chamador não."""
    assert cp._linha(12) == "12"
    assert cp._linha(None) == "None"


# -- O aviso em markdown, não só o selo em HTML ------------------------------
def test_aviso_de_trava_mantem_a_mensagem_do_banco_inteira():
    """O nome da tabela que falta é a parte útil, e era a que sumia.

    ``[SQL: ...]`` solto no markdown vira sintaxe de link: a tela mostrava
    ``[SQL: SELECT 1 FROM public)`` e perdia o resto. Dentro de crase, chega
    inteiro.
    """
    from core.seguranca import travas as tv

    trava = tv.Trava(nome="auditoria_falhou", disparada=True,
                     detalhe=ERRO_DE_BANCO)
    aviso = di._aviso_de_trava(trava)
    assert _sem_quebra(aviso)
    assert aviso.endswith("[SQL: SELECT 1 FROM public.recomendacao_auditoria]`")
    assert aviso.count("`") == 2          # abre e fecha, uma vez só
    assert aviso.startswith(tv.TEXTO["auditoria_falhou"][:20])


def test_crase_no_detalhe_nao_quebra_o_bloco_de_codigo():
    from core.seguranca import travas as tv

    trava = tv.Trava(nome="auditoria_falhou", disparada=True,
                     detalhe="falhou em `SELECT 1`")
    assert di._aviso_de_trava(trava).count("`") == 2


def test_trava_sem_detalhe_nao_ganha_bloco_de_codigo_vazio():
    from core.seguranca import travas as tv

    trava = tv.Trava(nome="dados_vencidos", disparada=True)
    assert "`" not in di._aviso_de_trava(trava)


# -- O corte na origem -------------------------------------------------------
def test_motivo_curto_declara_o_corte():
    """Mensagem cortada sem marca lê-se como mensagem inteira.

    O aviso da auditoria terminava em ``[SQL: SELECT 1 FROM public`` -- e nada
    ali dizia que faltava texto. Quem lesse concluiria que o erro era esse e
    mais nenhum.
    """
    from core.auditoria import trilha

    longo = trilha.motivo_curto("x" * 400)
    assert longo.endswith(" [...]")
    assert len(longo) <= 200 + len(" [...]")
    assert trilha.motivo_curto("curto e completo") == "curto e completo"


def test_motivo_curto_achata_antes_de_cortar():
    """Achatar primeiro: o corte de 200 não pode gastar caracteres com ``\n``."""
    from core.auditoria import trilha

    assert _sem_quebra(trilha.motivo_curto(ERRO_DE_BANCO))
    assert "recomendacao_auditoria" in trilha.motivo_curto(ERRO_DE_BANCO)
