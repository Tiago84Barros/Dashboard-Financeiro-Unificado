"""A-N1: `views/portfolio_b3.py::render()` chega mesmo a `render_safras`?

`tests/test_portfolio_b3_safras.py::test_chamada_de_render_safras_e_alcancavel_na_tela_b3`
prova por AST que a chamada nao esta em codigo morto. Isso e forma. Uma
guarda cuja condicao e sempre falsa POR DADO -- uma chave de sessao que
ninguem seta, uma lista vazia por defeito rio acima -- sobrevive aquele
verificador (ele so conclui morte quando o valor e constante em tempo de
leitura) e apaga a tela inteira de safras com a suite verde.

Este arquivo fecha essa lacuna EXECUTANDO `render()` sob dubles e exigindo
que `render_safras` tenha sido chamada de fato, com a carteira aprovada e
com `resultados_todos`.

Nao usa AppTest (`apptest-vaza-atribuicao-de-modulo`): o padrao desta base e
trocar o modulo `st` da view por um duble. O duble base e o `_Falso` ja
auditado em `tests/test_portfolio_b3_safras.py`; aqui ele e ESTENDIDO (nao
modificado) com o que uma tela de ~2.000 linhas exige e a tela de safras
nao exigia: retorno de widget, colunas que sao elas mesmas o duble, e
`session_state` alimentado pela `key=` como o Streamlit real faz.
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

import views.empresas_b3 as _emp
import views.portfolio_b3 as mod
from tests.test_portfolio_b3_safras import _Falso
from views.empresas_b3 import PITCoverage


class _YfProibido:
    """Sentinela no lugar do modulo `yfinance` de `views.empresas_b3`.

    Nao basta dublar os helpers: se algum caminho novo chamar `yf` direto, a
    chamada sai pela rede e o teste continua verde -- os helpers de dividendo
    engolem `Exception` e devolvem vazio. A guarda de socket do `conftest` nao
    pega isso (`guarda-em-python-nao-alcanca-driver-em-c`: o driver HTTP do
    yfinance e C). Entao registramos o acesso e o teste falha pelo registro.
    """

    def __init__(self) -> None:
        self.acessos: list[str] = []

    def __getattr__(self, nome: str):
        self.acessos.append(nome)
        raise RuntimeError(f"rede proibida no teste: yfinance.{nome}")


_TICKERS = ["AAAA3", "BBBB3", "CCCC3", "DDDD3"]
_ANO_FIM = 2024


# -- duble de streamlit com RETORNO de widget --------------------------------


class _Tela(_Falso):
    """`_Falso` + o que uma tela com dezenas de widgets exige para nao morrer.

    Regras:
    * todo widget devolve o valor que o proprio codigo de producao passou
      como padrao (`value=`/`index=`), nunca um valor inventado aqui -- a
      execucao provada e a execucao PADRAO da tela;
    * `key=` grava o valor em `session_state`, como o Streamlit faz, senao
      os `st.session_state.get(...)` espalhados pela tela leriam defaults
      que o usuario nunca veria;
    * o proprio duble e o contexto de `columns`/`expander`/`tabs`, para que
      `p1.number_input(...)` continue caindo aqui.
    """

    def __init__(self):
        super().__init__(botao=False)

    # contexto (with st.expander / with st.spinner / with col)
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _guarda(self, k, valor):
        chave = k.get("key")
        if chave:
            self.session_state[chave] = valor
        return valor

    def columns(self, spec, *a, **k):
        self.chamadas.append(("columns", (spec,), k))
        quantas = spec if isinstance(spec, int) else len(spec)
        return [self for _ in range(quantas)]

    def tabs(self, rotulos, *a, **k):
        self.chamadas.append(("tabs", (rotulos,), k))
        return [self for _ in rotulos]

    def expander(self, *a, **k):
        self.chamadas.append(("expander", a, k))
        return self

    def container(self, *a, **k):
        self.chamadas.append(("container", a, k))
        return self

    def spinner(self, *a, **k):
        self.chamadas.append(("spinner", a, k))
        return self

    def form(self, *a, **k):
        self.chamadas.append(("form", a, k))
        return self

    def number_input(self, label, min_value=None, max_value=None, value=None,
                     step=None, **k):
        self.chamadas.append(("number_input", (label,), k))
        bruto = value if value is not None else min_value
        return self._guarda(k, 0.0 if bruto is None else bruto)

    def selectbox(self, label, options, index=0, **k):
        self.chamadas.append(("selectbox", (label,), k))
        opcoes = list(options)
        i = 0 if index is None else int(index)
        return self._guarda(k, opcoes[i] if opcoes else None)

    def radio(self, label, options, index=0, **k):
        self.chamadas.append(("radio", (label,), k))
        opcoes = list(options)
        i = 0 if index is None else int(index)
        return self._guarda(k, opcoes[i] if opcoes else None)

    def multiselect(self, label, options, default=None, **k):
        self.chamadas.append(("multiselect", (label,), k))
        return self._guarda(k, list(default or []))

    def checkbox(self, label, value=False, **k):
        self.chamadas.append(("checkbox", (label,), k))
        return self._guarda(k, bool(value))

    def toggle(self, label, value=False, **k):
        self.chamadas.append(("toggle", (label,), k))
        return self._guarda(k, bool(value))

    def slider(self, label, min_value=None, max_value=None, value=None,
               step=None, **k):
        self.chamadas.append(("slider", (label,), k))
        bruto = value if value is not None else min_value
        return self._guarda(k, bruto)

    def text_input(self, label, value="", **k):
        self.chamadas.append(("text_input", (label,), k))
        return self._guarda(k, value)

    def text_area(self, label, value="", **k):
        self.chamadas.append(("text_area", (label,), k))
        return self._guarda(k, value)

    def form_submit_button(self, *a, **k):
        self.chamadas.append(("form_submit_button", a, k))
        return False

    def download_button(self, *a, **k):
        self.chamadas.append(("download_button", a, k))
        return False


# -- dados de entrada (nenhum toque em banco, rede ou cache) -----------------


def _hist_precos() -> pd.DataFrame:
    datas = pd.date_range("2014-01-31", "2025-09-30", freq="ME")
    base = np.linspace(10.0, 30.0, len(datas))
    return pd.DataFrame(
        {tk: base * (1.0 + 0.05 * i) for i, tk in enumerate(_TICKERS)},
        index=datas,
    )


def _df_set() -> pd.DataFrame:
    return pd.DataFrame({
        "ticker": _TICKERS,
        "SETOR": ["Financeiro"] * 2 + ["Utilidade Publica"] * 2,
        "SUBSETOR": ["Bancos"] * 2 + ["Energia"] * 2,
        "SEGMENTO": ["Bancos"] * 2 + ["Energia Eletrica"] * 2,
        "nome_empresa": [f"Empresa {t}" for t in _TICKERS],
    })


def _resultado(segmento, tickers) -> dict:
    anos = list(range(2015, _ANO_FIM + 1))
    lids_por_ano = {a: list(tickers) for a in anos}
    pesos_por_ano = {a: {t: 1.0 / len(tickers) for t in tickers} for a in anos}
    score_rows = [
        {"Ano": a, "ticker": t, "Score_Ajustado": 70.0 + i,
         "SETOR": "Financeiro", "SUBSETOR": "Bancos", "SEGMENTO": segmento}
        for a in anos for i, t in enumerate(tickers)
    ]
    lideres_rows = [
        {"Ano": a, "ticker": t, "SETOR": "Financeiro",
         "SUBSETOR": "Bancos", "SEGMENTO": segmento}
        for a in anos for t in tickers
    ]
    cobertura = PITCoverage(
        linhas_avaliadas=100, linhas_medidas=100, linhas_modeladas=0,
        snapshots_medidos=40, snapshots_modelados=0, decisoes=10,
    )
    return {
        "setor": "Financeiro", "subsetor": "Bancos", "segmento": segmento,
        "tickers": list(tickers),
        "liderancas_hist": {t: list(anos) for t in tickers},
        "participacao": {t: 1.0 / len(tickers) for t in tickers},
        "ultimo_lid": {t: _ANO_FIM for t in tickers},
        "score_proximo": {t: 70.0 + i for i, t in enumerate(tickers)},
        "ano_ref_score": _ANO_FIM,
        "lids_prox": list(tickers),
        "pesos_prox": {t: 1.0 / len(tickers) for t in tickers},
        "lids_por_ano": lids_por_ano,
        "pesos_por_ano": pesos_por_ano,
        "contrib_est": {t: 1000.0 for t in tickers},
        "ticker_maior_part": tickers[0],
        "score_rows": score_rows,
        "lideres_rows": lideres_rows,
        "val_est": 2500.0, "val_selic": 1800.0, "val_ew": 2000.0,
        "val_est_oos": 1300.0, "val_selic_oos": 1150.0, "val_ew_oos": 1200.0,
        "p_value_oos": 0.01, "n_months_oos": 24,
        "rank_ic_mean": 0.20, "rank_ic_years": 6, "rank_ic_tstat": 3.5,
        "p_value_ic": 0.01,
        "rank_ic_values": [0.1, 0.2, 0.3, 0.15, 0.25, 0.2],
        "ic_pairs": [(a, 70.0 + i, 0.10 + 0.01 * i)
                     for a in anos for i in range(len(tickers))],
        "n_empresas_medio": float(len(tickers)),
        "roic_spread_mean": 0.05, "roic_hit_rate": 0.8, "wf_hit_rate": 0.7,
        "n_anos": len(anos), "ano_inicio": anos[0], "ano_fim": anos[-1],
        "pit_coverage": cobertura,
        "pit_coverage_historico": cobertura,
        "pit_coverage_validacao": cobertura,
        "pit_coverage_decisao": cobertura,
        "pit_coverage_por_ano": {a: cobertura for a in anos},
        "constraint_warnings": [],
    }


def _multiplos() -> pd.DataFrame:
    """Mesmo formato de `core.market_read.load_multiplos_todos`: uma linha por
    ticker, coluna `Ticker` + `data` + `_MULT_COLS`."""
    linhas = []
    for t in _TICKERS:
        linha = {"Ticker": t, "data": pd.Timestamp(f"{_ANO_FIM}-12-31")}
        linha.update({
            # unidades como o carregador entrega: fracao, nao percentual
            # (`Endividamento_Total` e divida/PL em "x", `Payout` em 0..1).
            "P/L": 10.0, "P/VP": 1.5, "DY": 0.06, "ROE": 0.15, "ROA": 0.08,
            "ROIC": 0.14, "Margem_Liquida": 0.20, "Margem_Operacional": 0.25,
            "Endividamento_Total": 0.5, "Liquidez_Corrente": 1.5,
            "EV_EBIT": 8.0, "P_FCO": 9.0, "Payout": 0.40,
            "Patrimonio_Negativo": 0, "Endividamento_Fora_De_Faixa": 0,
            "FCO_Negativo": 0,
        })
        linhas.append(linha)
    return pd.DataFrame(linhas)


@pytest.fixture
def tela(monkeypatch):
    """`views.portfolio_b3` com TODA saida de processo dublada."""
    falso = _Tela()
    monkeypatch.setattr(mod, "st", falso)

    precos = _hist_precos()
    df_set = _df_set()
    selic = {a: 0.10 for a in range(2010, 2027)}

    # banco (core.b3_data, fachada do Supabase)
    monkeypatch.setattr(mod._db, "load_setores", lambda *a, **k: df_set)
    monkeypatch.setattr(mod._db, "load_selic_macro", lambda *a, **k: selic)
    monkeypatch.setattr(mod._db, "load_macro_history", lambda *a, **k: {})
    monkeypatch.setattr(mod._db, "load_historico_anos",
                        lambda *a, **k: {t: 12 for t in _TICKERS})
    monkeypatch.setattr(mod._db, "load_multiplos_todos", lambda *a, **k: _multiplos())
    monkeypatch.setattr(mod._db, "load_giro_diario", lambda *a, **k: {})
    monkeypatch.setattr(mod._db, "load_classes_irmas", lambda *a, **k: {})

    # liquidez / tamanho
    monkeypatch.setattr(mod, "_load_market_caps",
                        lambda *a, **k: {t: 5e9 for t in _TICKERS})
    monkeypatch.setattr(mod, "_load_adtv",
                        lambda *a, **k: {t: 5e7 for t in _TICKERS})

    # rede (yfinance) e armazem local
    monkeypatch.setattr(mod, "_batch_yf_precos_mensais",
                        lambda tks, **k: precos[[t for t in tks if t in precos]])
    monkeypatch.setattr(mod, "_yf_dividendos_anuais",
                        lambda *a, **k: pd.DataFrame(columns=["Data", "Dividendos"]))
    monkeypatch.setattr(mod, "_yf_multiplos_dividendos", lambda *a, **k: {})
    monkeypatch.setattr(mod, "_yf_trailing12m_divs", lambda *a, **k: 0.0)
    falso.yf_proibido = _YfProibido()
    monkeypatch.setattr(_emp, "yf", falso.yf_proibido)
    monkeypatch.setattr(mod, "get_local_macro_engine", lambda *a, **k: None)
    monkeypatch.setattr(mod, "load_portfolio_macro_snapshot",
                        lambda *a, **k: {})
    monkeypatch.setattr(mod, "quali_gate_disponivel", lambda *a, **k: False)

    # resultado ja calculado: a tela sem clique le de session_state e pula o
    # motor inteiro (`if rodar:`). E o caminho normal de todo rerun.
    falso.session_state["pb3_resultados"] = [
        _resultado("Bancos", _TICKERS[:2]),
        _resultado("Energia Eletrica", _TICKERS[2:]),
    ]
    falso.session_state["pb3_df_set"] = df_set
    falso.session_state["pb3_precos_all"] = precos
    falso.session_state["pb3_quality_summary"] = {}
    falso.session_state["pb3_quality_audit"] = pd.DataFrame()
    falso.session_state["pb3_hist_audit"] = pd.DataFrame()
    falso.session_state["pb3_entry_guard"] = {}
    return falso


def test_render_chama_render_safras_de_fato(tela, monkeypatch):
    """EXECUTA `render()` do inicio ao fim e exige a chamada.

    Custo medido (22/09/2026, Python 3.12): ~5 s dentro de `render()` e ~10 s
    de arquivo (o resto e importar streamlit/plotly), ~2.100 linhas de
    `render()` percorridas, 15 dubles instalados. Nao e marcado como `skip`:
    o que ele guarda -- a tela de safras inteira sumir da producao com a
    suite verde -- so aparece executando.
    """
    chamou = []
    monkeypatch.setattr(mod, "render_safras",
                        lambda *a, **k: chamou.append((a, k)))

    t0 = time.perf_counter()
    mod.render(show_header=False)
    gasto = time.perf_counter() - t0
    print(f"\n[alcance] render() executou em {gasto:.1f}s")

    assert chamou, (
        "render() terminou sem chamar render_safras -- a tela de safras "
        "(Blocos 1, 2 e 3) nao chega a ser desenhada em producao"
    )
    args, kwargs = chamou[0]
    aprovados, precos = args[0], args[1]
    assert aprovados, (
        "render_safras foi chamada com a carteira VAZIA -- a tela desenha, "
        "mas sem safra nenhuma"
    )
    assert isinstance(precos, pd.DataFrame) and not precos.empty
    assert "resultados_todos" in kwargs and kwargs["resultados_todos"], (
        "sem `resultados_todos` o Bloco 2 (vies do gate de aprovacao) nao "
        "tem a lista nao filtrada e sai zerado"
    )
    assert len(kwargs["resultados_todos"]) >= len(aprovados), (
        "a lista nao filtrada saiu menor que a aprovada -- os dois "
        "argumentos foram trocados"
    )
    assert not tela.yf_proibido.acessos, (
        "render() tocou o yfinance ao vivo (rede) em: "
        f"{sorted(set(tela.yf_proibido.acessos))} -- falta dublar esse caminho"
    )
