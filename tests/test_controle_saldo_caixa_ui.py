"""Renderiza os KPIs do Controle Financeiro para checar o que o usuário lê.

Os defeitos desta regra não aparecem na fórmula: aparecem no card — saldo
pintado de vermelho sem déficit, ou renda comprometida somando aporte. Por isso
o teste chama a função de render e inspeciona o HTML, não os números.
"""
from contextlib import contextmanager

import pytest

import views.controle_financeiro as cf

# Cenário do mês de setembro/2026 relatado pelo usuário: as despesas ficam
# abaixo da renda e o aporte de R$ 10 mil é que faz a soma passar da entrada.
_RECEITAS = 27_636.02
_DESPESAS = 25_577.70
_APORTE = 10_000.0


@contextmanager
def _nulo():
    yield


class _FakeSt:
    def __init__(self):
        self.html: list[str] = []

    def markdown(self, body, **_kw):
        self.html.append(str(body))

    def columns(self, n, **_kw):
        return [_nulo() for _ in range(n if isinstance(n, int) else len(n))]

    def caption(self, *_a, **_kw):
        pass

    def plotly_chart(self, *_a, **_kw):
        pass

    def dataframe(self, *_a, **_kw):
        pass


def _render(monkeypatch, receitas, despesas, aporte) -> str:
    fake = _FakeSt()
    monkeypatch.setattr(cf, "st", fake)
    cf._tab_dashboard(
        {"receitas": receitas, "despesas": despesas, "categorias": [],
         "transacoes": []},
        [], {}, aporte,
    )
    return "\n".join(fake.html)


def test_aporte_acima_da_sobra_nao_pinta_deficit(monkeypatch):
    html = _render(monkeypatch, _RECEITAS, _DESPESAS, _APORTE)

    assert "R$ 2.058,32" in html          # saldo = receitas − despesas
    assert "-R$ 7.941,68" not in html     # o valor antigo, com o aporte subtraído
    assert cf._COR_INVEST in html         # azul: alocação, não déficit
    assert "92,55%" in html                # renda comprometida só de despesas
    assert "128,70%" not in html
    assert "Investido no mês: R$ 10.000,00" in html


def test_despesa_acima_da_renda_continua_vermelha(monkeypatch):
    html = _render(monkeypatch, _RECEITAS, 30_000.0, _APORTE)

    assert "-R$ 2.363,98" in html
    assert cf._COR_DESPESA in html


def test_sobra_maior_que_o_aporte_fica_verde(monkeypatch):
    html = _render(monkeypatch, _RECEITAS, 20_000.0, 2_000.0)

    assert "R$ 7.636,02" in html
    assert cf._COR_RECEITA in html


def test_renda_zero_nao_quebra_o_card_de_comprometimento(monkeypatch):
    html = _render(monkeypatch, 0.0, 500.0, 0.0)

    assert "Sem renda positiva" in html
    assert cf._COR_NEUTRO in html


@pytest.mark.parametrize("aporte", [0.0, 5_000.0, 50_000.0])
def test_aporte_nao_muda_saldo_nem_comprometimento_exibidos(monkeypatch, aporte):
    """O tamanho do aporte não pode alterar os dois números de caixa."""
    html = _render(monkeypatch, _RECEITAS, _DESPESAS, aporte)
    assert "R$ 2.058,32" in html
    assert "92,55%" in html
