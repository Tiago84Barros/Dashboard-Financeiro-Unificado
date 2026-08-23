"""As três abas com nota precisam declarar que a escala é local.

B3, FIIs e Empresas Americanas usam o mesmo cartão, a mesma faixa 0–100 e o
mesmo vocabulário de badge, mas são metodologias independentes com rigor
diferente — cada nota é um percentil dentro do próprio universo comparável. A
casca visual comum sugere comparabilidade que não existe; o painel do
Portfólio Global já dizia isso, as abas individuais não (achado SCORE-02).
"""
import io
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("arquivo", [
    "views/empresas_b3.py",
    "views/fiis.py",
    "views/empresas_americanas.py",
])
def test_aba_com_nota_declara_que_a_escala_e_local(arquivo):
    fonte = io.open(RAIZ / arquivo, encoding="utf-8").read()
    assert "aviso_escala_do_score" in fonte, (
        f"{arquivo} exibe nota sem declarar que ela não vale nas outras abas")


def test_o_aviso_nomeia_as_tres_abas():
    from design.componentes import AVISO_ESCALA_NAO_COMPARAVEL

    for aba in ("B3", "FIIs", "Empresas Americanas"):
        assert aba in AVISO_ESCALA_NAO_COMPARAVEL
    assert "não é comparável" in AVISO_ESCALA_NAO_COMPARAVEL.lower()
