import ast
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from core.b3_vigencia import (
    REBAL_MONTH,
    ano_base_do_score,
    janela_de_vigencia,
    safra_completa,
    safra_vigente_em,
)

RAIZ = Path(__file__).parents[1]


def test_rebal_month_e_abril():
    assert REBAL_MONTH == 4


@pytest.mark.parametrize(
    "data, esperado",
    [
        (date(2026, 1, 1), 2025),
        (date(2026, 3, 31), 2025),   # vespera: safra ainda e a anterior
        (date(2026, 4, 1), 2026),    # vira em 1o de abril
        (date(2026, 9, 21), 2026),
        (date(2026, 12, 31), 2026),
    ],
)
def test_safra_vigente_vira_em_abril(data, esperado):
    assert safra_vigente_em(data) == esperado


def test_aceita_timestamp_e_date():
    assert safra_vigente_em(pd.Timestamp("2026-04-01")) == 2026
    assert safra_vigente_em(date(2026, 4, 1)) == 2026


def test_janela_de_vigencia_e_abril_a_marco():
    inicio, fim = janela_de_vigencia(2026)
    assert inicio == pd.Timestamp("2026-04-01")
    assert fim == pd.Timestamp("2027-03-31")


def test_ano_base_e_o_exercicio_anterior():
    assert ano_base_do_score(2026) == 2025


def test_safra_vigente_nunca_usa_balanco_do_futuro():
    """Propriedade: em toda data de 2010 a 2030, o exercicio-base da safra
    vigente e estritamente anterior ao ano da propria data. Se esta
    propriedade cair, a tela esta publicando look-ahead."""
    for d in pd.date_range("2010-01-01", "2030-12-31", freq="D"):
        safra = safra_vigente_em(d)
        assert ano_base_do_score(safra) < d.year, d


def test_safra_completa_so_apos_o_fim_da_janela():
    assert safra_completa(2025, hoje=pd.Timestamp("2026-04-01")) is True
    assert safra_completa(2026, hoje=pd.Timestamp("2026-09-21")) is False
    assert safra_completa(2026, hoje=pd.Timestamp("2027-03-30")) is False
    assert safra_completa(2026, hoje=pd.Timestamp("2027-03-31")) is True


def _nomes_atribuidos(caminho: Path) -> set[str]:
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    nomes: set[str] = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Assign):
            for alvo in no.targets:
                if isinstance(alvo, ast.Name):
                    nomes.add(alvo.id)
    return nomes


def test_mes_de_rebalance_definido_num_lugar_so():
    """Guarda duplicada nao fica igual: tres copias da regra de abril ja
    existiram e uma delas (o grafico) ficou para tras. O caminho e resolvido
    a partir da raiz do pacote, nao por `parts` de caminho absoluto --
    filtro por caminho absoluto nao visita nada dentro de worktree."""
    suspeitos = [
        RAIZ / "views" / "portfolio_b3.py",
        RAIZ / "views" / "empresas_b3.py",
        RAIZ / "core" / "b3_safras.py",
    ]
    for caminho in suspeitos:
        if not caminho.exists():
            continue
        definidos = {n for n in _nomes_atribuidos(caminho)
                     if "REBAL" in n.upper() and "MONTH" in n.upper()}
        assert not definidos, (
            f"{caminho.relative_to(RAIZ)} define {sorted(definidos)}; "
            "o mes de rebalance mora so em core/b3_vigencia.py"
        )
