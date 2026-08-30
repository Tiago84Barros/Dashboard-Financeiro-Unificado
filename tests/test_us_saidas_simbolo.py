# -*- coding: utf-8 -*-
"""Confirmar a saida antes de nomea-la, e nomea-la pela fonte que sobrevive.

Duas armadilhas, uma em cada etapa:

* a **ausencia** foi lida de uma lista de formas que nao contem `40-F`. Quem
  arquiva 40-F a vida inteira -- todo emissor canadense sob o MJDS -- nunca
  aparece nessa lista e e declarado morto em todos os anos da janela;
* o **simbolo** parece estar de graca no `submissions.json`, no campo
  `tickers`. Ele so esta preenchido para quem ainda arquiva: usar aquele campo
  nomearia exatamente os sobreviventes, que e o vies que este trabalho desfaz.
"""
import core.us_survivorship as surv
from core.us_saidas_sec import (
    e_relatorio_anual,
    extrair_trading_symbol,
    filiais_anuais,
    refuta_saida,
    simbolo_plausivel,
)


def _sub(*filiais):
    """`submissions.json` reduzido: listas paralelas, como a SEC serve."""
    return {"filings": {"recent": {
        "form": [f[0] for f in filiais],
        "filingDate": [f[1] for f in filiais],
        "accessionNumber": [f"0000000000-{i:02d}-000000" for i, _ in enumerate(filiais)],
        "primaryDocument": [f"doc{i}.htm" for i, _ in enumerate(filiais)],
    }}}


# ── refutacao ───────────────────────────────────────────────────────────────

def test_quarenta_f_e_relatorio_anual():
    """O emissor MJDS arquiva 40-F e nada mais; sem isso ele morre todo ano."""
    assert e_relatorio_anual("40-F")
    assert e_relatorio_anual("40-F/A")
    assert e_relatorio_anual("10-KT")
    assert not e_relatorio_anual("8-K")
    assert not e_relatorio_anual("10-Q")


def test_saida_e_refutada_por_anual_no_proprio_ano_da_ausencia():
    """`absence_year` e o primeiro ano SEM anual; um anual nele nega a premissa."""
    sub = _sub(("40-F", "2023-12-02"), ("8-K", "2022-05-01"))
    assert refuta_saida(sub, 2023) == {"forma": "40-F", "data": "2023-12-02"}
    assert refuta_saida(sub, 2024) is None


def test_evento_nao_anual_posterior_nao_refuta():
    """Continuar existindo para efeitos de 8-K nao e continuar reportando."""
    assert refuta_saida(_sub(("8-K", "2024-01-10")), 2021) is None


def test_filiais_anuais_nao_cruzam_linhas_de_listas_paralelas():
    """Forma de uma linha com data de outra inventaria vida onde nao houve."""
    sub = _sub(("10-Q", "2025-01-01"), ("10-K", "2021-03-01"),
               ("10-K/A", "2022-06-01"))
    anuais = filiais_anuais(sub)
    assert [(f["forma"], f["data"]) for f in anuais] == [
        ("10-K/A", "2022-06-01"), ("10-K", "2021-03-01")]
    assert anuais[0]["documento"] == "doc2.htm"


# ── simbolo ─────────────────────────────────────────────────────────────────

def test_simbolo_sai_da_capa_mesmo_embrulhado_em_span():
    doc = ('<ix:nonNumeric contextRef="c" name="dei:TradingSymbol">'
           '<span class="x">AKRX</span></ix:nonNumeric>')
    assert extrair_trading_symbol(doc) == "AKRX"


def test_serie_de_divida_nao_e_ticker():
    """`AXP/21` veio da capa de um 10-K real e nao e papel negociavel."""
    assert simbolo_plausivel("AXP/21") is None
    assert simbolo_plausivel("N/A") is None
    assert simbolo_plausivel("") is None
    assert simbolo_plausivel(" bns ") == "BNS"
    assert simbolo_plausivel("PTVCA") == "PTVCA"


def test_capa_sem_o_fato_nao_inventa_simbolo():
    assert extrair_trading_symbol("<html><body>10-K</body></html>") is None
    assert extrair_trading_symbol(
        '<ix:nonNumeric name="dei:EntityRegistrantName">ACME</ix:nonNumeric>'
    ) is None


# ── a lista de formas que decide a saida ────────────────────────────────────

def test_evidencia_de_reporte_conta_emenda_e_quarenta_f_como_vida():
    """Quem decide saida procura a presenca em toda forma anual."""
    idx = ("Form Type  Company  CIK  Date  File Name\n"
           "10-K/A     ACME     1    2023-01-01  edgar/data/1/x.txt\n"
           "40-F       MAPLE    2    2023-01-01  edgar/data/2/y.txt\n"
           "10-KT      TRANS    3    2023-01-01  edgar/data/3/z.txt\n"
           "8-K        RUIDO    4    2023-01-01  edgar/data/4/w.txt\n")
    assert surv.ciks_com_evidencia_de_reporte(idx) == {1, 2, 3}


def test_coorte_da_mortalidade_nao_foi_alargada_junto():
    """Populacao medida e teste de vida sao perguntas diferentes.

    Alargar a coorte moveria a mortalidade ja publicada sem que a medicao
    tivesse sido refeita. `40-F` prova vida (nao ha saida) e continua fora da
    coorte ampla e da domestica (nao entra no denominador).
    """
    idx = ("Form Type  Company  CIK  Date  File Name\n"
           "10-K       ACME     1    2023-01-01  edgar/data/1/x.txt\n"
           "40-F       MAPLE    2    2023-01-01  edgar/data/2/y.txt\n")
    assert surv.ciks_com_relatorio_anual(idx) == {1}
    assert surv.ciks_com_relatorio_anual_operacional(idx) == {1}
    assert surv.ciks_com_evidencia_de_reporte(idx) == {1, 2}
