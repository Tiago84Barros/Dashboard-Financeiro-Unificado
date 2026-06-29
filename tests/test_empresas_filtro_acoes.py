"""Filtro de ações do card de Empresas B3 (remove FIIs/ETFs/BDRs/subscrições)."""
import pandas as pd

from views.empresas_b3 import _eh_acao, _so_acoes


def test_eh_acao_on_pn():
    for tk in ("PETR3", "PETR4", "BBDC3", "ITUB4", "ELET6", "BRAP7", "ABCB8"):
        assert _eh_acao(tk) is True


def test_eh_acao_on_pn_sem_nome():
    # ON/PN sem nome real ainda são ações (sufixo decide)
    assert _eh_acao("AXIA5", "") is True
    assert _eh_acao("MEND3", "MEND3") is True


def test_eh_acao_unit_depende_de_nome():
    assert _eh_acao("KLBN11", "Klabin SA Ctf de Deposito") is True   # unit de empresa
    assert _eh_acao("ALUP11", "Alupar Investimento SA Unit") is True
    assert _eh_acao("XPML11", "XPML11") is False                     # FII sem nome
    assert _eh_acao("ACWI11", "") is False                           # ETF sem nome


def test_eh_acao_remove_bdr_e_subscricao():
    assert _eh_acao("AAPL34", "Apple BDR") is False
    assert _eh_acao("ROXO35", "Nubank BDR") is False
    assert _eh_acao("BRGE12", "") is False
    assert _eh_acao("PETR1", "Petrobras") is False   # subscrição


def test_so_acoes_filtra_df():
    df = pd.DataFrame({
        "ticker": ["PETR4", "KLBN11", "XPML11", "AAPL34", "MEND3"],
        "nome_empresa": ["Petrobras", "Klabin SA Ctf", "XPML11", "Apple BDR", "MEND3"],
        "SETOR": ["Petróleo", "Materiais", "", "", ""],
    })
    out = _so_acoes(df)
    assert set(out["ticker"]) == {"PETR4", "KLBN11", "MEND3"}
