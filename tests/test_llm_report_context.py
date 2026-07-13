"""Relatórios por Empresa: contexto rico (10 anos, dividendos, peers) e
limpeza de boilerplate jurídico do RAG."""
import pandas as pd

from core.llm_b3 import _fmt_dre, _fmt_multiplos, _report_model, analisar_empresa
from core.rag_b3 import _strip_boilerplate


def _df_anos(n, col="ROE", base=0.10):
    return pd.DataFrame({
        "Data": [pd.Timestamp(2010 + i, 12, 31) for i in range(n)],
        col: [base + 0.01 * i for i in range(n)],
    })


def test_fmt_multiplos_usa_ate_10_anos():
    out = _fmt_multiplos(_df_anos(16))
    anos = [ln.split(")")[0].strip(" (") for ln in out.splitlines()]
    assert len(anos) == 10  # não mais 3
    assert anos[0] == "2016" and anos[-1] == "2025"


def test_fmt_dre_inclui_dividendos_e_fco():
    df = _df_anos(12, col="Receita_Liquida", base=1e9)
    df["FCO"] = 2e8
    df["Dividendos"] = 1.5
    out = _fmt_dre(df)
    assert len(out.splitlines()) == 10
    assert "FCO=" in out and "Dividendos=" in out


def test_report_model_default_gpt4o(monkeypatch):
    monkeypatch.delenv("LLM_REPORT_MODEL", raising=False)
    assert _report_model() == "gpt-4o"
    monkeypatch.setenv("LLM_REPORT_MODEL", "gpt-4.1")
    assert _report_model() == "gpt-4.1"


def test_strip_boilerplate_remove_cabecalho_juridico():
    txt = ('COMPANHIA ENERGÉTICA DE BRASÍLIA COMPANHIA ABERTA NIRE: 53.3.0000154-5 '
           'CNPJ nº 00.070.698/0001-11 CVM 14451 FATO RELEVANTE ("Companhia" ou "CEB") '
           'comunica dividendos intermediários de R$ 1,20 por ação.')
    out = _strip_boilerplate(txt)
    assert "NIRE" not in out and "CNPJ" not in out and "COMPANHIA ABERTA" not in out
    assert "14451" not in out
    # o FATO permanece intacto
    assert "dividendos intermediários de R$ 1,20" in out


def test_analisar_empresa_injeta_peers_no_prompt(monkeypatch):
    captured = {}

    def fake_call(prompt, model=None):
        captured["prompt"] = prompt
        captured["model"] = model
        return '{"perspectiva": "moderada"}'

    import core.llm_b3 as llm
    monkeypatch.setattr(llm, "_call_llm", fake_call)
    monkeypatch.delenv("LLM_REPORT_MODEL", raising=False)
    llm.analisar_empresa(
        ticker="TEST3", nome="Teste SA", setor="X", segmento="Y",
        peso_pct=10.0, score=70.0, alpha_selic=2.0,
        df_mult=_df_anos(5), df_fin=pd.DataFrame(),
        macro_hist={}, portfolio_ctx="ctx",
        peers_ctx="PAR1 P/L=5,0 vs PAR2 P/L=9,0",
    )
    assert "PAR1 P/L=5,0" in captured["prompt"]      # peers entram no prompt
    assert "PARES DO MESMO SEGMENTO" in captured["prompt"]
    assert captured["model"] == "gpt-4o"             # modelo de relatório
