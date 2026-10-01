"""Cards da aba Tabelas: cada um mostra o seu tipo, qualquer que seja o rádio.

Com o rádio em "Despesas", os cards de Entradas e Investimentos somavam a lista
já recortada pelo tipo e exibiam R$ 0,00 "no filtro aplicado" — o usuário lia
"não investi nada em 2026" num ano com R$ 137 mil aportados (01/10/2026). O
rádio recorta a tabela e o Total Filtrado; os cards por tipo, não.
"""
from contextlib import contextmanager

import pytest

import core.controle as ctrl
import views.controle_financeiro as cf


@contextmanager
def _nulo():
    yield


def _tx(valor, tipo_fluxo, categoria):
    return {"id": f"{tipo_fluxo}-{valor}", "descricao": categoria, "valor": valor,
            "valor_fmt": "", "data": None, "data_fmt": "01/10/2026",
            "tipo": tipo_fluxo, "tipo_fluxo": tipo_fluxo, "tipo_label": tipo_fluxo,
            "status": "paid", "categoria": categoria, "conta": "Conta Corrente",
            "eh_fatura_cartao": False, "eh_receita": tipo_fluxo == "income",
            "eh_despesa": tipo_fluxo == "expense",
            "eh_investimento": tipo_fluxo == "investment",
            "ano": 2026, "mes": 10, "dia": 1}


_TXS = [
    _tx(10_000.0, "income", "Salário"),
    _tx(-1_000.0, "expense", "Outros"),
    _tx(-757.0, "expense", "Saúde"),
    _tx(-5_000.0, "investment", "Renda Fixa"),
    _tx(-2_000.0, "investment", "Exterior"),
]


class _FakeSt:
    def __init__(self, tipo):
        self.tipo = tipo
        self.html: list[str] = []

    def radio(self, *_a, **_kw):
        return self.tipo

    def checkbox(self, *_a, **_kw):
        return False

    def selectbox(self, _label, options, index=0, **_kw):
        return options[index]

    def text_input(self, *_a, **_kw):
        return ""

    def columns(self, n, **_kw):
        return [_nulo() for _ in range(n if isinstance(n, int) else len(n))]

    def markdown(self, body, **_kw):
        self.html.append(str(body))

    def __getattr__(self, _nome):
        return lambda *_a, **_kw: None


def _render(monkeypatch, tipo) -> str:
    fake = _FakeSt(tipo)
    monkeypatch.setattr(cf, "st", fake)

    def _filtradas(tipo="Todos", categoria="Todas", ano=None, mes=None, dia=None,
                   texto="", incluir_fatura_cartao=False):
        return ctrl._filtrar_transacoes(list(_TXS), tipo, categoria, ano, mes,
                                        dia, texto, incluir_fatura_cartao)

    monkeypatch.setattr(cf, "get_transacoes_filtradas", _filtradas)
    monkeypatch.setattr(cf, "_render_bank_statement_section", lambda *_a: None)
    cf._tab_tabelas({})
    return "\n".join(fake.html)


@pytest.mark.parametrize("tipo", ["Todos", "Receitas", "Despesas", "Investimentos"])
def test_cards_por_tipo_nao_dependem_do_radio(monkeypatch, tipo):
    html = _render(monkeypatch, tipo)

    assert "R$ 10.000,00" in html   # Entradas
    assert "R$ 1.757,00" in html    # Saídas
    assert "R$ 7.000,00" in html    # Investimentos


@pytest.mark.parametrize("tipo, total, linhas", [
    ("Todos", "R$ 18.757,00", 5),
    ("Despesas", "R$ 1.757,00", 2),
    ("Investimentos", "R$ 7.000,00", 2),
])
def test_total_filtrado_e_tabela_seguem_o_radio(monkeypatch, tipo, total, linhas):
    html = _render(monkeypatch, tipo)

    assert total in html
    assert f"{linhas} lançamento(s) selecionado(s)" in html
