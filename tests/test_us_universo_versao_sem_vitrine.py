"""Universo zerado por carimbo de versão precisa dizer que foi isso.

`universo_us` conta apenas a geração corrente — e isso está certo: republicar a
vitrine não apaga as linhas antigas, e até 25/08/2026 elas inflavam o
denominador. O que faltava era a outra metade da regra: quando a versão
corrente não tem NENHUMA linha publicada, o resultado é `nominal=0`, que é
indistinguível de "o módulo americano não tem empresa nenhuma".

Foi o que aconteceu em 31/08/2026. `US_FUNDAMENTAL_SCORE_VERSION` subiu para
0.7.2 por uma correção que só tocou o painel PIT (`first_trade_date`); a
vitrine transversal, cujos números a correção não muda, continuou carimbada
0.7.1. As 2.626 empresas seguiam publicadas e o painel de abrangência dizia
zero, sem motivo.

Zero com motivo continua sendo zero — a correção não inventa cobertura. Ela
faz a diferença entre "não há empresa" e "não há PUBLICAÇÃO desta versão"
chegar a quem lê, que é a única das duas que alguém pode consertar.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from core.universo_decisao import universo_us


def _versao() -> str:
    from core.us_methodology import US_FUNDAMENTAL_SCORE_VERSION
    return US_FUNDAMENTAL_SCORE_VERSION


@pytest.fixture()
def vitrine():
    """Vitrine mínima em SQLite, com o mesmo nome qualificado do Postgres."""
    eng = create_engine("sqlite:///:memory:", poolclass=StaticPool,
                        connect_args={"check_same_thread": False})
    with eng.begin() as c:
        c.execute(text("ATTACH ':memory:' AS market_us"))
        c.execute(text("CREATE TABLE market_us.company_snapshots (symbol TEXT, "
                       "score_version TEXT, score_status TEXT)"))
    return eng


def _povoar(eng, linhas) -> None:
    with eng.begin() as c:
        for sym, ver, st in linhas:
            c.execute(text("INSERT INTO market_us.company_snapshots VALUES "
                           "(:s,:v,:t)"), {"s": sym, "v": ver, "t": st})


def test_vitrine_atrasada_zera_o_universo_mas_diz_por_que(vitrine):
    """O caso real: tudo publicado sob a versão anterior."""
    _povoar(vitrine, [(f"A{i}", "0.7.1", "decision_grade") for i in range(2626)])
    u = universo_us(vitrine)
    assert u.nominal == 0
    texto = " ".join(u.notas)
    assert "0.7.1" in texto and _versao() in texto
    assert "2626" in texto or "2.626" in texto


def test_vitrine_na_versao_corrente_nao_ganha_a_nota(vitrine):
    _povoar(vitrine, [("A", _versao(), "decision_grade"),
                      ("B", _versao(), "screen_grade"),
                      ("C", "0.7.1", "decision_grade")])
    u = universo_us(vitrine)
    assert (u.nominal, u.apto) == (2, 1)
    assert not any("publicada" in n for n in u.notas)


def test_vitrine_realmente_vazia_nao_culpa_a_versao(vitrine):
    """Sem nenhuma linha, o problema não é o carimbo — não inventar diagnóstico."""
    u = universo_us(vitrine)
    assert u.nominal == 0
    assert not any("0.7.1" in n for n in u.notas)
