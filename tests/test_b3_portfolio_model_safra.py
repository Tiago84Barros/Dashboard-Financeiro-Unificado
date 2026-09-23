"""A safra de uma carteira salva nao pode ser inferida de created_at.

Em fevereiro de 2027 a safra vigente ainda e a de 2026: date.today().year
carimbaria 2027 e a carteira apareceria no relatorio no ano errado, sem
erro visivel.
"""
import pandas as pd

from core.b3_portfolio_model import _params_com_safra


def test_carimbo_em_fevereiro_usa_a_safra_do_ano_anterior():
    params, ano_compra = _params_com_safra({}, hoje=pd.Timestamp("2027-02-10"))
    assert ano_compra == 2026
    assert params["safra"] == 2026
    assert params["vigencia_inicio"] == "2026-04-01"
    assert params["vigencia_fim"] == "2027-03-31"


def test_carimbo_em_abril_usa_a_safra_do_proprio_ano():
    params, ano_compra = _params_com_safra({}, hoje=pd.Timestamp("2026-04-01"))
    assert ano_compra == 2026
    assert params["safra"] == 2026


def test_ano_compra_explicito_do_usuario_prevalece():
    params, ano_compra = _params_com_safra({"ano_compra": 2023},
                                           hoje=pd.Timestamp("2026-09-21"))
    assert ano_compra == 2023
    assert params["safra"] == 2023
    assert params["vigencia_inicio"] == "2023-04-01"


def test_nao_perde_as_chaves_que_ja_vinham():
    params, _ = _params_com_safra({"score_version": "v9"},
                                  hoje=pd.Timestamp("2026-09-21"))
    assert params["score_version"] == "v9"
