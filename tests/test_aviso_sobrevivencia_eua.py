"""A tela dos EUA afirmava evitar o viés de sobrevivência; a medição diz o oposto.

Em 27/08/2026, `delisted_date` era NULL nos 7.654 registros de `market_us.assets`
e nenhuma empresa deslistada entrava no universo de `score_vintages` -- que é a
fonte de todo Rank-IC e de todo backtest exibidos. A Metodologia, no entanto,
dizia ao usuário que "empresas deslistadas permanecem no universo histórico,
evitando o viés de sobrevivência".

Estes testes travam a correção pelo texto, não pelo banco: eles precisam passar
em CI, sem warehouse. O dia em que a ingestão de deslistadas existir de verdade,
estes testes falham -- e falhar aqui é o sinal certo de que o aviso deve ser
reescrito, não removido em silêncio.
"""
from __future__ import annotations

from pathlib import Path

import pytest

VIEW = Path(__file__).resolve().parents[1] / "views" / "empresas_americanas.py"


@pytest.fixture(scope="module")
def fonte() -> str:
    return VIEW.read_text(encoding="utf-8")


def test_metodologia_nao_afirma_universo_livre_de_sobrevivencia(fonte: str) -> None:
    assert "evitando o viés de sobrevivência" not in fonte
    assert "nenhuma deslistagem foi ingerida" in fonte


def test_aviso_existe_e_diz_o_que_o_usuario_precisa_decidir(fonte: str) -> None:
    from views.empresas_americanas import _AVISO_SOBREVIVENCIA as aviso

    assert "sobrevivência" in aviso
    # o ponto nao e "falta dado": e que o risco de ruina nao e observavel.
    assert "perda permanente de capital" in aviso


def test_aviso_acompanha_toda_evidencia_historica_exibida(fonte: str) -> None:
    # backtest do Laboratorio e tela de backtest dedicada.
    assert fonte.count("st.caption(_AVISO_SOBREVIVENCIA)") == 2
    # auditoria por industria traz a ressalva no proprio texto da secao.
    assert "sem nenhuma deslistagem no universo" in fonte
