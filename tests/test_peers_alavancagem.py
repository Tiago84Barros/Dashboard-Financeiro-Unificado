"""Lacuna 0a2516dd: o chat de ISAE3 não tinha a dívida líquida/EBITDA dos
concorrentes, embora a base (balanço) e o arquivo de valuation (EBITDA de 12
meses da brapi) permitam calculá-la por ``ler_acao_b3``."""
import pandas as pd

import core.llm_context_b3 as ctx
from core.inteligencia_ativos.fundamentos import Dado


def _cenario(monkeypatch, alavancagem):
    monkeypatch.setattr(ctx, "compute_segment_peers",
                        lambda tk: (["CPFE3", "NEOE3"], "segmento Energia Elétrica"))
    monkeypatch.setattr(ctx._db, "load_setores", lambda: pd.DataFrame(
        {"ticker": ["CPFE3", "NEOE3"], "nome_empresa": ["CPFL", "NEOENERGIA"],
         "SETOR": ["x", "x"], "SEGMENTO": ["y", "y"]}))
    monkeypatch.setattr(ctx, "_universe_with_sector", lambda: pd.DataFrame(
        {"Ticker": ["CPFE3", "NEOE3"], "P/L": [8.0, 7.7]}))
    monkeypatch.setattr(ctx, "_ler_alavancagem", alavancagem)


def test_linha_do_par_traz_divida_liquida_ebitda(monkeypatch):
    dados = {"CPFE3": Dado(3.389, "fonte", "exercício 2025", None)}
    _cenario(monkeypatch, lambda tk: dados.get(tk))
    texto, _ = ctx.get_peers_context(["ISAE3"], max_tickers=1)
    linha_cpfe = next(l for l in texto.splitlines() if "CPFE3" in l)
    linha_neoe = next(l for l in texto.splitlines() if "NEOE3" in l)
    assert "DL/EBITDA=3.39x" in linha_cpfe
    # Ausência fica nomeada, nunca vira zero nem some.
    assert "DL/EBITDA=N/D" in linha_neoe
    assert "dívida líquida do balanço" in texto


def test_falha_na_leitura_nao_derruba_os_pares(monkeypatch):
    def _explode(tk):
        raise RuntimeError("sem banco")
    _cenario(monkeypatch, _explode)
    texto, peers = ctx.get_peers_context(["ISAE3"], max_tickers=1)
    assert peers == {"ISAE3": ["CPFE3", "NEOE3"]}
    assert "DL/EBITDA=N/D" in texto


def test_razao_retida_mostra_o_motivo_curto(monkeypatch):
    # Dado com valor None (fontes divergentes / EBITDA negativo) é N/D.
    _cenario(monkeypatch, lambda tk: Dado(None, "fonte", None, "EBITDA negativo"))
    texto, _ = ctx.get_peers_context(["ISAE3"], max_tickers=1)
    assert "DL/EBITDA=N/D" in texto


def test_legenda_sobrevive_ao_teto(monkeypatch):
    pares = [f"PAR{i:03d}3" for i in range(150)]
    monkeypatch.setattr(ctx, "compute_segment_peers", lambda tk: (pares, "segmento X"))
    monkeypatch.setattr(ctx._db, "load_setores", lambda: pd.DataFrame())
    monkeypatch.setattr(ctx, "_universe_with_sector", lambda: pd.DataFrame())
    monkeypatch.setattr(ctx, "_ler_alavancagem", lambda tk: None)
    texto, _ = ctx.get_peers_context(["ISAE3"], max_tickers=1)
    assert "(truncado)" in texto
    assert texto.rstrip().endswith("fontes divergentes)")
