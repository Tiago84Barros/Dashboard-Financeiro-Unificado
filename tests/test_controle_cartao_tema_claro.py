"""Aba Cartão de Crédito: título do filtro e grade do painel "a categorizar".

O ``st.data_editor`` desenha num canvas cujas cores o Streamlit escreve a partir
do tema do config (escuro) — CSS não alcança. No tema claro o painel de revisão
passa a ser desenhado com widgets nativos, e o teste guarda o contrato que a
gravação depende: o quadro devolvido tem de trazer as mesmas colunas que o
``st.data_editor`` devolveria, com os mesmos nomes.
"""
import ast
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


def test_nenhum_titulo_de_secao_repete_a_palavra_como_icone():
    """O 1º argumento de _secao_titulo é ícone, não texto.

    Passar uma palavra ali imprimia "Resumo Resumo executivo da fatura" — o
    rótulo dobrado. A checagem é estrutural (AST) porque a tela tem 18 chamadas
    e a próxima a nascer também precisa cair na regra.
    """
    arvore = ast.parse(inspect.getsource(cf))
    for no in ast.walk(arvore):
        if (isinstance(no, ast.Call) and isinstance(no.func, ast.Name)
                and no.func.id == "_secao_titulo" and no.args):
            icone = no.args[0]
            if (isinstance(icone, ast.Constant) and isinstance(icone.value, str)
                    and icone.value):
                assert not icone.value.isascii(), (
                    f"linha {no.lineno}: ícone '{icone.value}' é texto e dobra o título")


def test_fatura_detalhada_escolhe_a_grade_pelo_tema_da_sessao():
    fonte = inspect.getsource(cf._editor_cartao_detalhado)
    assert "no_claro()" in fonte
    assert "_editor_detalhado_claro" in fonte
    assert "_editor_detalhado_escuro" in fonte
    assert "st.data_editor" not in fonte
    # O seletor de página tem de ficar FORA do form: dentro dele o Streamlit só
    # leria a troca no submit, e a página nunca mudaria.
    assert fonte.index("_pagina_detalhado(") < fonte.index('with st.form("cc_detail_editor_form"')


class _ColunaDetalhe:
    """Widgets que devolvem o valor de entrada, menos a descrição (editada)."""

    def markdown(self, html, **kw):
        pass

    def date_input(self, _rotulo, value=None, **kw):
        return value

    def text_input(self, _rotulo, value="", **kw):
        return "EDITADO"

    def selectbox(self, _rotulo, opcoes, index=0, **kw):
        return opcoes[index]

    def number_input(self, _rotulo, value=0, **kw):
        return value


def _df_detalhe():
    linhas = []
    for n in range(3):
        linhas.append({
            "ID": f"tx-{n}", "Vencimento": date(2026, 9, 10), "Compra": date(2026, 9, 3),
            "Descrição": f"LOJA {n}", "Categoria": "Alimentação", "Cartão": "Nubank",
            "Valor": 10.0 + n, "Parc. atual": 1, "Parc. total": 1, "Status": "settled",
        })
    return pd.DataFrame(linhas)


def test_editor_detalhado_claro_devolve_o_quadro_inteiro(monkeypatch):
    """Só a página visível é editada; o resto volta idêntico.

    O laço de gravação compara linha a linha contra a entrada e grava o que
    divergir — se as linhas fora da página não voltassem iguais, trocar de
    página reescreveria a fatura toda.
    """
    monkeypatch.setattr(cf, "st", SimpleNamespace(
        columns=lambda larguras, **kw: [_ColunaDetalhe() for _ in larguras],
        markdown=lambda html, **kw: None))
    df_edit = _df_detalhe()

    edited = cf._editor_detalhado_claro(
        df_edit, range(1, 2), ["Alimentação"], ["Nubank"], ["settled"])

    assert list(edited.columns) == list(df_edit.columns)
    assert len(edited) == len(df_edit)
    assert edited.iloc[1]["Descrição"] == "EDITADO"
    for fora in (0, 2):
        assert edited.iloc[fora].to_dict() == df_edit.iloc[fora].to_dict()


def test_editor_detalhado_claro_aceita_listas_de_opcoes_vazias(monkeypatch):
    monkeypatch.setattr(cf, "st", SimpleNamespace(
        columns=lambda larguras, **kw: [_ColunaDetalhe() for _ in larguras],
        markdown=lambda html, **kw: None))
    edited = cf._editor_detalhado_claro(_df_detalhe(), range(3), [], [], ["settled"])
    assert edited.iloc[0]["Categoria"] == "Sem categoria"
    assert edited.iloc[0]["Cartão"] == "Sem cartão"


def test_paginacao_do_editor_claro_limita_as_linhas_desenhadas(monkeypatch):
    registro = {}

    def selectbox(_rotulo, opcoes, **kw):
        registro["opcoes"] = opcoes
        return opcoes[0]

    monkeypatch.setattr(cf, "st", SimpleNamespace(selectbox=selectbox))
    # Poucas linhas: nem seletor, nem corte.
    assert list(cf._pagina_detalhado(5)) == list(range(5))
    assert "opcoes" not in registro
    # Fatura grande: a primeira página para no tamanho da página.
    total = cf._DETALHE_POR_PAGINA * 4 + 7
    assert list(cf._pagina_detalhado(total)) == list(range(cf._DETALHE_POR_PAGINA))
    assert len(registro["opcoes"]) == 5


# ─────────────────────── Últimos Lançamentos (Dashboard) ─────────────────────

def test_ultimos_lancamentos_escolhem_a_grade_pelo_tema_da_sessao():
    """O terceiro editor desta tela caía no canvas escuro dentro do tema claro.

    A grade de "Últimos Lançamentos" ficou para trás quando os outros dois
    ganharam a versão nativa — habilitar a edição no claro devolvia um bloco
    preto no meio da página branca.
    """
    fonte = inspect.getsource(cf._tab_dashboard)
    assert "_editor_lancamentos_claro" in fonte
    assert "no_claro()" in fonte
    # O seletor de página tem de ficar FORA do form, como nos outros dois.
    assert fonte.index("_pagina_lancamentos(") < fonte.index(
        'with st.form("form_editor_lancamentos"')


def _df_lancamentos():
    return pd.DataFrame([{
        "ID": f"tx-{n}", "Tipo": "saída", "Categoria": "Luz",
        "Data": date(2026, 9, 21 - n), "Valor": 100.0 + n,
        "Descrição": f"CONTA {n}", "Conta": "Conta Corrente",
    } for n in range(3)])


def test_editor_de_lancamentos_claro_devolve_o_quadro_inteiro(monkeypatch):
    """Só a página visível é editada; o resto volta idêntico.

    O laço de gravação compara cada linha com a entrada e grava o que divergir
    — linha de fora que não voltasse igual seria regravada sem o usuário ter
    tocado nela.
    """
    monkeypatch.setattr(cf, "st", SimpleNamespace(
        columns=lambda larguras, **kw: [_ColunaDetalhe() for _ in larguras],
        markdown=lambda html, **kw: None))
    df_edit = _df_lancamentos()

    edited = cf._editor_lancamentos_claro(
        df_edit, range(1, 2), ["entrada", "saída"], ["Luz"], ["Conta Corrente"])

    assert list(edited.columns) == list(df_edit.columns)
    assert len(edited) == len(df_edit)
    assert edited.iloc[1]["Descrição"] == "EDITADO"
    for fora in (0, 2):
        assert edited.iloc[fora].to_dict() == df_edit.iloc[fora].to_dict()


def test_editor_de_lancamentos_claro_aceita_listas_de_opcoes_vazias(monkeypatch):
    monkeypatch.setattr(cf, "st", SimpleNamespace(
        columns=lambda larguras, **kw: [_ColunaDetalhe() for _ in larguras],
        markdown=lambda html, **kw: None))
    edited = cf._editor_lancamentos_claro(
        _df_lancamentos(), range(3), ["saída"], [], [])
    assert edited.iloc[0]["Categoria"] == "Sem categoria"
    assert edited.iloc[0]["Conta"] == "Sem conta"


def test_paginacao_dos_lancamentos_limita_as_linhas_desenhadas(monkeypatch):
    registro = {}

    def selectbox(_rotulo, opcoes, **kw):
        registro["opcoes"] = opcoes
        return opcoes[0]

    monkeypatch.setattr(cf, "st", SimpleNamespace(selectbox=selectbox))
    assert list(cf._pagina_lancamentos(5)) == list(range(5))
    assert "opcoes" not in registro
    total = cf._LANC_POR_PAGINA * 3
    assert list(cf._pagina_lancamentos(total)) == list(range(cf._LANC_POR_PAGINA))
    assert len(registro["opcoes"]) == 3
