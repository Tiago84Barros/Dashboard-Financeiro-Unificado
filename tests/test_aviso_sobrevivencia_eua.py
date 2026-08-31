"""O aviso de sobrevivência da tela dos EUA tem de acompanhar o dado, não o texto.

Primeira versão (27/08/2026): a Metodologia dizia que "empresas deslistadas
permanecem no universo histórico, evitando o viés de sobrevivência" enquanto
`delisted_date` era NULL nos 7.654 registros de `market_us.assets`. Os testes
travaram a correção e deixaram escrito que, no dia em que a ingestão existisse,
falhariam -- e que falhar ali significava reescrever o aviso, nunca removê-lo.

Esse dia foi 31/08/2026: 1.603 empresas mortas ingeridas, 702 saídas em 16
safras. O aviso foi reescrito, e o que estes testes travam agora é o inverso do
que travavam antes -- que a tela não volte a afirmar ausência de deslistagem
(passou a ser falso) e que não declare o viés resolvido (continua sendo falso do
lado do RETORNO, onde não há cotação de ticker morto).

Continuam sendo testes de texto, para rodar em CI sem warehouse. O que depende
do banco é a própria medição, checada em outro lugar.
"""
from __future__ import annotations

from pathlib import Path

import pytest

VIEW = Path(__file__).resolve().parents[1] / "views" / "empresas_americanas.py"


@pytest.fixture(scope="module")
def fonte() -> str:
    return VIEW.read_text(encoding="utf-8")


def test_metodologia_nao_afirma_nenhum_dos_dois_extremos(fonte: str) -> None:
    """Nem "viés evitado" (nunca foi) nem "nada ingerido" (deixou de ser)."""
    assert "evitando o viés de sobrevivência" not in fonte
    assert "nenhuma deslistagem foi ingerida" not in fonte
    assert "sem nenhuma deslistagem no universo" not in fonte


def test_aviso_nomeia_o_que_a_ingestao_nao_resolveu(fonte: str) -> None:
    from core.us_survivorship import AVISO_UNIVERSO_COM_SAIDAS as aviso

    assert "sobrevivência" in aviso
    # o vies mudou de lugar: universo corrigido, medicao de retorno nao.
    assert "RETORNO" in aviso
    # e o ponto nunca foi "falta dado": e que o risco de ruina fica subobservado.
    assert "perda permanente de capital" in aviso


def test_aviso_e_derivado_da_medicao_e_nao_declarado(fonte: str) -> None:
    """A frase fixa foi o defeito das duas vezes -- ela agora pergunta ao número."""
    from core.us_survivorship import frase_universo

    assert frase_universo({"saidas": 702}).startswith("⚠️ **Viés de sobrevivência (parcial):**")
    assert "nenhuma deslistagem foi ingerida" in frase_universo({"saidas": 0})
    # sem medição, a versão pessimista: número que ninguém apurou não se afirma.
    assert "nenhuma deslistagem foi ingerida" in frase_universo({})


def test_aviso_acompanha_toda_evidencia_historica_exibida(fonte: str) -> None:
    # backtest do Laboratorio e tela de backtest dedicada.
    assert fonte.count("st.caption(_aviso_sobrevivencia())") == 2
    assert "_AVISO_SOBREVIVENCIA" not in fonte, "constante virou derivação"
