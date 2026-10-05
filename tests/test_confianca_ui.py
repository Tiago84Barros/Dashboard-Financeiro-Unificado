# -*- coding: utf-8 -*-
"""Aba Configuracoes -> Grau de Confianca: o que a apresentacao nao pode perder.

A tela toda e HTML montado por funcoes puras, de proposito: o que ela promete
-- nao medido nomeado, aviso de medicao velha so quando ela esta velha, texto
do banco escapado -- se verifica aqui sem subir o Streamlit nem tocar o banco.
"""
import re
from datetime import datetime, timedelta, timezone

from core.confianca_secao import Componente, ConfiancaSecao
from views import confianca as conf


def _secao(nome="Seleção de FIIs", pcts=(90.0, None), notas=()):
    comps = tuple(
        Componente(f"Comp {i}", p, 0.5, f"evidência {i}")
        for i, p in enumerate(pcts)
    )
    return ConfiancaSecao(secao=nome, componentes=comps, notas=tuple(notas))


def test_cards_das_secoes_saem_num_markdown_so():
    """Grade CSS, nao duas ``st.columns``: coluna empilha na propria altura e a
    fileira sai desencontrada quando uma secao tem mais notas que a vizinha."""
    html = conf._cards_html([_secao("A"), _secao("B"), _secao("C")])
    assert html.count('class="conf-cards"') == 1
    assert html.count('class="conf-card"') == 3
    # A grade precisa fechar sozinha; div aberta num bloco e fechada noutro
    # desenha moldura vazia com o conteudo fora da borda.
    assert html.count("<section") == html.count("</section>")
    assert html.count("<article") == html.count("</article>")


def test_componente_nao_medido_e_nomeado_e_nunca_vira_zero():
    """Exibir nao medido como 0% acusa defeito que ninguem observou."""
    html = conf._card(_secao(pcts=(90.0, None)))
    assert "não medido" in html
    assert ">0%<" not in html
    assert ">90%<" in html


def test_cobertura_parcial_aparece_no_card():
    html = conf._card(_secao(pcts=(90.0, None)))
    assert "50% do peso avaliado" in html


def test_secao_sem_nenhuma_medicao_nao_declara_zero_por_cento():
    """"Apoiado em 0% do peso" soa como medicao que deu zero; nao houve nenhuma."""
    html = conf._card(_secao(pcts=(None, None)))
    assert "0% do peso" not in html
    assert "Nenhum componente" in html
    assert "Confiança Não medido" not in html


def test_nome_e_evidencia_do_banco_entram_escapados():
    sec = ConfiancaSecao(
        secao="<i>FIIs</i>",
        componentes=(Componente("<b>n</b>", 80.0, 1.0, "<script>x</script>"),),
        notas=("<u>nota</u>",),
    )
    html = conf._card(sec)
    assert "<b>n</b>" not in html and "&lt;b&gt;n&lt;/b&gt;" in html
    assert "<script>" not in html
    assert "<i>FIIs</i>" not in html
    assert "<u>nota</u>" not in html


def test_aviso_de_medicao_velha_so_existe_quando_ela_esta_velha():
    """Frase fixa envelhece invertida: o aviso tem de sair da data medida."""
    agora = datetime.now(timezone.utc)
    secoes = [_secao()]
    fresca = conf._carimbo_html(agora - timedelta(hours=2), secoes)
    velha = conf._carimbo_html(
        agora - timedelta(days=conf.confianca_snapshot.VALIDADE_DIAS + 3), secoes)
    assert "conf-aviso" not in fresca
    assert "conf-aviso" in velha
    assert "recalcule" in velha.lower()
    # A idade sai do dado, nao de texto fixo.
    assert "idade" in fresca and "validade" in fresca


def test_carimbo_declara_quantas_secoes_foram_medidas():
    secoes = [_secao("A", pcts=(80.0,)), _secao("B", pcts=(None,))]
    html = conf._carimbo_html(datetime.now(timezone.utc), secoes)
    assert "1 de 2" in html


def test_hero_mostra_a_distribuicao_que_sustenta_o_numero():
    """O numero sozinho nao diz se vem de sete seçoes parecidas ou de duas
    altas carregando tres baixas."""
    secoes = [_secao("A", pcts=(95.0,)), _secao("B", pcts=(60.0,)),
              _secao("C", pcts=(10.0,)), _secao("D", pcts=(None,))]
    html = conf._hero_html(62.0, secoes)
    assert "62%" in html
    for rotulo in ("Alta", "Média", "Baixa", "Não medido"):
        assert rotulo in html


def test_hero_sem_medicao_nao_inventa_zero():
    html = conf._hero_html(None, [_secao("A", pcts=(None,))])
    assert "—" in html
    assert "0%</div>" not in html
    assert "Não medido" in html


def test_pergunta_nao_apurada_do_rigor_continua_escrita():
    """Nao apurada nao e vencida nem reprovada -- e sai da media, nao da tela."""
    dados = {"dimensoes": ["Validacao fora da amostra", "PIT"],
             "motores": {"FII": {"Validacao fora da amostra": (True, "ok"),
                                 "PIT": None},
                         "B3": {"Validacao fora da amostra": (False, "reprovada"),
                                "PIT": (True, "ok")}}}
    html = conf._tabela_rigor_html(dados)
    assert "não apurada" in html
    assert "✓" in html and "✗" in html and "—" in html
    assert "FII" in html and "B3" in html


def test_rigor_ausente_nao_desenha_tabela_vazia():
    assert conf._tabela_rigor_html(None) == ""
    assert conf._tabela_rigor_html({"dimensoes": [], "motores": {}}) == ""


def test_regua_das_faixas_sai_das_constantes():
    """Mudar ``FAIXA_ALTA`` sem mudar a legenda deixaria a tela mentindo."""
    from core.confianca_secao import FAIXA_ALTA, FAIXA_MEDIA

    html = conf._legenda_html()
    assert f"{FAIXA_ALTA:.0f}%" in html
    assert f"{FAIXA_MEDIA:.0f}" in html


def test_cor_da_tela_sai_de_token_de_tema():
    """Literal hex sem ``var(--app-*)`` nao acompanha o tema claro."""
    assert all(v.startswith("var(--app-") for v in conf._COR.values())
    assert all(cor.startswith("var(--app-") for _, cor in conf._SIMBOLO.values())
    sem_fallback = re.findall(r"(?<!, )#[0-9A-Fa-f]{6}", conf._CONF_CSS)
    assert not sem_fallback, sem_fallback


def test_a_tela_nao_usa_metrica_nativa():
    """Regra da casa: KPI e card CSS; ``st.metric`` ignora a folha de estilo."""
    import inspect

    fonte = inspect.getsource(conf)
    assert "st.metric" not in fonte
