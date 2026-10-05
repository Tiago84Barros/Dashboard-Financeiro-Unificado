"""Lacuna a3dce42e: no chat de ITSA4 a LLM declarou ausentes o P/VP, o DY e o
Payout dos pares (BBAS3, BBDC4, SANB11, BPAC11), embora o universo de múltiplos
que ``get_peers_context`` já carrega traga as três colunas para eles."""
import numpy as np
import pandas as pd

import core.llm_context_b3 as ctx


def _cenario(monkeypatch):
    monkeypatch.setattr(ctx, "compute_segment_peers",
                        lambda tk: (["BBAS3", "SANB11"], "segmento Bancos"))
    monkeypatch.setattr(ctx._db, "load_setores", lambda: pd.DataFrame(
        {"ticker": ["BBAS3", "SANB11"], "nome_empresa": ["BANCO DO BRASIL", "SANTANDER"],
         "SETOR": ["x", "x"], "SEGMENTO": ["y", "y"]}))
    monkeypatch.setattr(ctx, "_universe_with_sector", lambda: pd.DataFrame({
        "Ticker": ["BBAS3", "SANB11"], "P/L": [7.6, 7.54],
        "P/VP": [0.606, np.nan], "DY": [0.03, 0.09], "Payout": [0.228, 0.679],
        "ROE": [0.0798, 0.1036], "Margem_Liquida": [0.048, 0.0788]}))
    monkeypatch.setattr(ctx, "_ler_alavancagem", lambda tk: None)


def test_linha_do_par_traz_pvp_dy_e_payout(monkeypatch):
    _cenario(monkeypatch)
    texto, _ = ctx.get_peers_context(["ITSA4"], max_tickers=1)
    linha = next(ln for ln in texto.splitlines() if "BBAS3" in ln)
    assert "P/VP=0.61" in linha
    assert "DY=3.0%" in linha
    assert "Payout=22.8%" in linha
    # O que já ia continua indo.
    assert "P/L=7.60" in linha and "ROE=8.0%" in linha and "MargemLiq=4.8%" in linha


def test_ausencia_do_par_fica_nomeada(monkeypatch):
    _cenario(monkeypatch)
    texto, _ = ctx.get_peers_context(["ITSA4"], max_tickers=1)
    linha = next(ln for ln in texto.splitlines() if "SANB11" in ln)
    # NaN não vira zero nem some: aparece como N/D.
    assert "P/VP=N/D" in linha
    assert "Payout=67.9%" in linha
