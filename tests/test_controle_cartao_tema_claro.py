"""Aba Cartão de Crédito: título do filtro e grade do painel "a categorizar".

O ``st.data_editor`` desenha num canvas cujas cores o Streamlit escreve a partir
do tema do config (escuro) — CSS não alcança. No tema claro o painel de revisão
passa a ser desenhado com widgets nativos, e o teste guarda o contrato que a
gravação depende: o quadro devolvido tem de trazer as mesmas colunas que o
``st.data_editor`` devolveria, com os mesmos nomes.
"""
import inspect
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

import views.controle_financeiro as cf


def test_titulo_da_secao_de_filtros_mostra_apenas_filtro():
    fonte = inspect.getsource(cf._tab_cartao)
    assert '_secao_titulo("", "Filtro")' in fonte
    assert "Cabecalho e filtros" not in fonte


def test_secao_titulo_sem_icone_nao_deixa_espaco_a_esquerda(monkeypatch):
    saida = []
    monkeypatch.setattr(cf, "st", SimpleNamespace(
        markdown=lambda html, **kw: saida.append(html)))
    cf._secao_titulo("", "Filtro")
    assert ">Filtro<" in saida[0]


def test_revisao_escolhe_a_grade_pelo_tema_da_sessao():
    """A tela não pode chamar o data_editor direto: o claro precisa desviar."""
    fonte = inspect.getsource(cf._render_cartao_a_revisar)
    assert "no_claro()" in fonte
    assert "_editor_a_revisar_claro" in fonte
    assert "_editor_a_revisar_escuro" in fonte
    assert "st.data_editor" not in fonte


class _Coluna:
    def __init__(self, registro):
        self._reg = registro

    def markdown(self, html, **kw):
        self._reg.append(html)

    def selectbox(self, _rotulo, opcoes, index=0, **kw):
        return opcoes[index]

    def checkbox(self, _rotulo, value=False, **kw):
        return value


@pytest.fixture()
def _st_falso(monkeypatch):
    registro = []

    def columns(larguras, **kw):
        return [_Coluna(registro) for _ in larguras]

    monkeypatch.setattr(cf, "st", SimpleNamespace(
        columns=columns, markdown=lambda html, **kw: registro.append(html)))
    return registro


def test_editor_claro_devolve_as_colunas_que_a_gravacao_le(_st_falso):
    df_edit = pd.DataFrame([{
        "ID": "tx-1", "Data": date(2026, 9, 3), "Descrição": "PADARIA X",
        "Valor": 1234.5, "Categoria": cf.REVIEW_SENTINEL, "Criar regra": True,
    }])
    opcoes = [cf.REVIEW_SENTINEL, "Alimentação"]

    saida = cf._editor_a_revisar_claro(df_edit, opcoes)

    assert list(saida.columns) == list(df_edit.columns)
    linha = saida.iloc[0]
    # O laço de gravação lê exatamente estes quatro campos.
    assert linha["ID"] == "tx-1"
    assert linha["Descrição"] == "PADARIA X"
    assert linha["Categoria"] == cf.REVIEW_SENTINEL
    assert bool(linha["Criar regra"]) is True


def test_editor_claro_desenha_valor_e_data_no_formato_brasileiro(_st_falso):
    df_edit = pd.DataFrame([{
        "ID": "tx-1", "Data": date(2026, 9, 3), "Descrição": "PADARIA X",
        "Valor": 1234.5, "Categoria": cf.REVIEW_SENTINEL, "Criar regra": True,
    }])
    cf._editor_a_revisar_claro(df_edit, [cf.REVIEW_SENTINEL])
    marcacao = "".join(_st_falso)
    assert "03/09/2026" in marcacao
    assert "1.234,50" in marcacao
    # Cor vinda de token: é o que segue o tema claro (o canvas não seguia).
    assert "var(--app-text)" in marcacao


def test_editor_claro_escapa_o_estabelecimento(_st_falso):
    df_edit = pd.DataFrame([{
        "ID": "tx-1", "Data": None, "Descrição": "<b>LOJA</b>",
        "Valor": 10.0, "Categoria": cf.REVIEW_SENTINEL, "Criar regra": False,
    }])
    saida = cf._editor_a_revisar_claro(df_edit, [cf.REVIEW_SENTINEL])
    marcacao = "".join(_st_falso)
    assert "&lt;b&gt;LOJA&lt;/b&gt;" in marcacao
    # O valor gravado continua sendo o texto original, não o escapado.
    assert saida.iloc[0]["Descrição"] == "<b>LOJA</b>"
