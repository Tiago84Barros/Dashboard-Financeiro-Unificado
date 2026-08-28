"""O viés de sobrevivência dos EUA passou de aviso genérico a número medido.

Avisar que o viés existe não diz ao usuário se ele é pequeno ou se invalida a
evidência. A medição de 27/08/2026 sobre `market_us.score_vintages` dá o
tamanho: 16 safras, 2.692 entradas de empresas e **zero saídas** -- as 106
empresas da safra de 2010 estão todas na de 2025.

Estes testes rodam sem armazém: exercitam o contador com painéis sintéticos e a
leitura da medição gravada. O dia em que houver deslistagem ingerida, o painel
passa a ter saídas, `frase_turnover` muda de frase e o portão de
`core.validacao_motor` vira `True` -- por medição, não por edição.
"""
from __future__ import annotations

from datetime import date

import pytest

from core.us_survivorship import (
    carregar_medicao,
    frase_turnover,
    gravar_medicao,
    medir_turnover,
)


class _FakeConn:
    def __init__(self, linhas):
        self._linhas = linhas

    def execute(self, *_a, **_k):
        return list(self._linhas)

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class _FakeEngine:
    def __init__(self, linhas):
        self._linhas = linhas

    def connect(self):
        return _FakeConn(self._linhas)


def test_painel_sem_saida_e_contado_como_sem_saida() -> None:
    linhas = [(date(2020, 6, 30), 1), (date(2020, 6, 30), 2),
              (date(2021, 6, 30), 1), (date(2021, 6, 30), 2),
              (date(2021, 6, 30), 3)]
    m = medir_turnover(_FakeEngine(linhas))
    assert m["saidas"] == 0
    assert m["entradas"] == 1
    assert m["safras"] == 2


def test_empresa_que_some_entre_safras_conta_como_saida() -> None:
    """Este e o caso que hoje nao existe no painel real -- e o que deveria existir."""
    linhas = [(date(2020, 6, 30), 1), (date(2020, 6, 30), 2),
              (date(2021, 6, 30), 2)]
    m = medir_turnover(_FakeEngine(linhas))
    assert m["saidas"] == 1
    assert m["entradas"] == 0


def test_frase_muda_quando_ha_saida() -> None:
    """A frase nao pode ser fixa: se o vies encolher, o texto tem de acompanhar."""
    sem = frase_turnover({"saidas": 0, "entradas": 2692, "safras": 16,
                          "primeira_safra": "2010-06-30", "ultima_safra": "2025-06-30",
                          "empresas_primeira": 106})
    com = frase_turnover({"saidas": 40, "entradas": 2692, "safras": 16,
                          "primeira_safra": "2010-06-30", "ultima_safra": "2025-06-30",
                          "empresas_primeira": 106})
    assert "nenhuma saída" in sem and "100% sobrevivente" in sem
    assert "40 saídas" in com and "nenhuma saída" not in com


def test_sem_medicao_nao_inventa_numero(tmp_path) -> None:
    """Ausencia de medicao nao vira zero saidas; vira ausencia de frase."""
    assert carregar_medicao(tmp_path / "nao_existe.json") is None
    assert frase_turnover({}) is None


def test_medicao_gravada_no_repositorio_e_legivel() -> None:
    """A tela publicada le este arquivo: o armazem nao e alcancavel de producao."""
    med = carregar_medicao()
    if med is None:
        pytest.skip("medicao ainda nao gravada neste checkout")
    assert med["safras"] >= 2
    assert med["entradas"] > 0
    assert "medido_em" in med


def test_gravar_e_carregar_preservam_o_numero(tmp_path) -> None:
    alvo = tmp_path / "m.json"
    gravar_medicao({"saidas": 7, "entradas": 9, "safras": 3,
                    "primeira_safra": "2020-06-30", "ultima_safra": "2022-06-30",
                    "empresas_primeira": 5, "medido_em": "2026-08-27"}, alvo)
    assert carregar_medicao(alvo)["saidas"] == 7


def test_portao_us_apura_pelo_painel_quando_assets_nao_existe(monkeypatch) -> None:
    """Em producao o schema so tem company_snapshots/prices_monthly.

    Antes disso o portao virava "nao apurado" justamente onde o usuario decide,
    embora o painel ja respondesse a pergunta por outro caminho.
    """
    import core.us_survivorship as us
    from core.validacao_motor import _deslistadas_us_pelo_painel as portao

    monkeypatch.setattr(us, "carregar_medicao",
                        lambda *a, **k: {"saidas": 0, "safras": 16})
    p = portao()
    assert p.ok is False and "16 safras" in p.detalhe

    monkeypatch.setattr(us, "carregar_medicao",
                        lambda *a, **k: {"saidas": 31, "safras": 16})
    assert portao().ok is True

    monkeypatch.setattr(us, "carregar_medicao", lambda *a, **k: None)
    assert portao().ok is None
