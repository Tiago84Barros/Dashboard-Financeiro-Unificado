"""Troca de código na B3 (ELET3 → AXIA3): uma companhia e classe, um código.

Antes, os dois códigos ficavam ativos e caíam em segmentos diferentes: a
mesma Eletrobras ON liderava dois segmentos no mesmo ano e entrava duas
vezes no benchmark equal-weight. ON/PN/Unit da mesma companhia NÃO são troca
e continuam separadas.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core import b3_troca_codigo as tc


# ── Mapa de trocas ───────────────────────────────────────────────────────────


def _ativos(*linhas):
    return pd.DataFrame(linhas, columns=["ticker", "company_id"])


def _sem_conhecidos(mapa):
    """O mapa sempre traz PARES_CONHECIDOS; aqui interessa o que o cadastro decidiu."""
    return {a: n for a, n in mapa.items() if a not in tc.PARES_CONHECIDOS}


def test_par_conhecido_da_mesma_companhia_e_troca():
    df = _ativos(("ELET3", 1), ("AXIA3", 1), ("ELET6", 1))
    mapa = tc.mapa_de_trocas(df)
    assert mapa["ELET3"] == "AXIA3" and "ELET6" not in mapa
    assert _sem_conhecidos(mapa) == {}


def test_companhias_diferentes_vetam_ate_o_par_conhecido():
    df = _ativos(("ELET3", 1), ("AXIA3", 2))
    assert "ELET3" not in tc.mapa_de_trocas(df)


def test_on_e_pn_nunca_viram_troca():
    df = _ativos(("ELET3", 1), ("ELET6", 1), ("AZUL3", 7), ("AZUL4", 7))
    aliases = [
        {"brapi_symbol": "ELET3", "b3_ticker": "ELET6", "motivo": tc.MOTIVO_TROCA},
        {"brapi_symbol": "AZUL4", "b3_ticker": "AZUL3", "motivo": "legado"},
    ]
    mapa = tc.mapa_de_trocas(df, aliases)
    assert mapa.get("ELET3") != "ELET6"
    assert not {"ELET6", "AZUL3", "AZUL4"} & set(mapa)


def test_alias_legado_so_vale_com_a_mesma_companhia():
    # Legado: brapi_symbol = código novo, b3_ticker = código antigo.
    df = _ativos(("XPTO3", 5), ("NOVA3", 5), ("ALOS3", 9), ("BRML3", 10))
    aliases = pd.DataFrame([
        ("NOVA3", "XPTO3", "brapi devolveu outro símbolo"),
        # incorporação: BRML3 é OUTRA companhia, não troca de código
        ("ALOS3", "BRML3", "brapi devolveu outro símbolo"),
    ], columns=["brapi_symbol", "b3_ticker", "motivo"])
    assert _sem_conhecidos(tc.mapa_de_trocas(df, aliases)) == {"XPTO3": "NOVA3"}


def test_alias_de_troca_provada_vale_sem_os_dois_ativos():
    aliases = [("VELH3", "NOVO3", tc.MOTIVO_TROCA)]
    assert _sem_conhecidos(tc.mapa_de_trocas(_ativos(("NOVO3", 3)), aliases)) == {
        "VELH3": "NOVO3"}


def test_cadeia_de_trocas_aponta_para_o_ultimo_codigo():
    aliases = [("AAAA3", "BBBB3", tc.MOTIVO_TROCA), ("BBBB3", "CCCC3", tc.MOTIVO_TROCA)]
    assert _sem_conhecidos(tc.mapa_de_trocas(None, aliases)) == {
        "AAAA3": "CCCC3", "BBBB3": "CCCC3"}


# ── Classificação setorial: uma só para os dois códigos ──────────────────────


def _setores(*linhas):
    return pd.DataFrame(linhas, columns=["ticker", "company_id", "SETOR",
                                         "SUBSETOR", "SEGMENTO", "listagem"])


def test_listagem_oficial_prevalece_sobre_a_inferida():
    df = _setores(
        ("ELET3", 1, "Utilidade Pública", "Energia Elétrica", "Energia Elétrica", "NM"),
        ("AXIA3", 1, "Outros", "Outros", "Outros", None),
    )
    out = tc.unificar_classificacao(df, {"ELET3": "AXIA3"})
    assert set(out["SEGMENTO"]) == {"Energia Elétrica"}
    assert set(out["SETOR"]) == {"Utilidade Pública"}


def test_empate_fica_com_o_codigo_novo():
    df = _setores(
        ("CCRO3", 2, "Bens Industriais", "Transporte", "Exploração de Rodovias", "NM"),
        ("MOTV3", 2, "Bens Industriais", "Transporte", "Transporte Rodoviário", "NM"),
    )
    out = tc.unificar_classificacao(df, {"CCRO3": "MOTV3"})
    assert set(out["SEGMENTO"]) == {"Transporte Rodoviário"}
    assert tc.escolher_classificacao(None, None) == "novo"


# ── Universo: uma linha por (companhia, classe) ──────────────────────────────


def test_dedup_tira_o_antigo_e_mantem_on_pn():
    df = pd.DataFrame({
        "ticker":       ["ELET3", "AXIA3", "ELET6", "PETR3", "PETR4"],
        "codigo_atual": ["AXIA3", "AXIA3", "ELET6", "PETR3", "PETR4"],
    })
    out, mapa = tc.deduplicar_por_empresa(df)
    assert mapa == {"ELET3": "AXIA3"}
    assert out["ticker"].tolist() == ["AXIA3", "ELET6", "PETR3", "PETR4"]


def test_dedup_nao_tira_o_antigo_quando_o_atual_esta_fora_do_universo():
    # Sem o código atual no universo, tirar o antigo apagaria a empresa.
    df = pd.DataFrame({"ticker": ["ELET3", "PETR4"], "codigo_atual": ["AXIA3", "PETR4"]})
    out, mapa = tc.deduplicar_por_empresa(df)
    assert mapa == {} and out["ticker"].tolist() == ["ELET3", "PETR4"]


def test_dedup_sem_a_coluna_nao_mexe():
    df = pd.DataFrame({"ticker": ["ELET3", "AXIA3"]})
    out, mapa = tc.deduplicar_por_empresa(df)
    assert mapa == {} and out is df


# ── Dobras: o histórico do antigo passa a responder pelo atual ───────────────


def test_preco_encadeado_nao_inventa_retorno_na_emenda():
    idx = pd.date_range("2024-01-31", periods=24, freq="ME")
    velho = pd.Series(10.0 * np.cumprod(np.full(24, 1.01)), index=idx)
    # o código novo começa no mês 18, numa base de ajuste 4% acima
    novo = (velho * 1.04).where(idx >= idx[18])
    df = pd.DataFrame({"ELET3": velho, "AXIA3": novo, "PETR4": 5.0}, index=idx)
    df.attrs["fonte"] = "teste"

    out = tc.dobrar_colunas(df, {"ELET3": "AXIA3"})

    assert "ELET3" not in out.columns and out.attrs == {"fonte": "teste"}
    serie = out["AXIA3"]
    assert serie.notna().all()                       # o histórico do antigo veio
    pd.testing.assert_series_equal(serie.iloc[18:], novo.iloc[18:], check_names=False)
    ret = serie.pct_change().dropna()
    assert ret.round(10).nunique() == 1              # sem salto de 4% na emenda
    assert ret.iloc[17] == pytest.approx(0.01)


def test_preco_so_do_antigo_passa_para_o_atual():
    idx = pd.date_range("2024-01-31", periods=3, freq="ME")
    df = pd.DataFrame({"CARD3": [1.0, 2.0, 3.0]}, index=idx)
    out = tc.dobrar_colunas(df, {"CARD3": "CSUD3"})
    assert out.columns.tolist() == ["CSUD3"] and out["CSUD3"].tolist() == [1.0, 2.0, 3.0]


def test_linhas_na_mesma_chave_ficam_com_o_codigo_atual():
    df = pd.DataFrame({
        "ticker": ["ELET3", "ELET3", "AXIA3", "PETR4"],
        "mes":    ["2025-07", "2025-08", "2025-08", "2025-08"],
        "volume": [1.0, 2.0, 20.0, 5.0],
    })
    out = tc.dobrar_linhas(df, {"ELET3": "AXIA3"})
    axia = out[out["ticker"] == "AXIA3"].set_index("mes")["volume"].to_dict()
    assert axia == {"2025-07": 1.0, "2025-08": 20.0}
    assert "ELET3" not in set(out["ticker"])


def test_historico_do_antigo_completa_os_anos_do_atual():
    def _h(tk, anos, roe):
        return pd.DataFrame({"Ticker": tk, "Data": [pd.Timestamp(a, 12, 31) for a in anos],
                             "ROE": roe})
    hist = {"ELET3": _h("ELET3", [2022, 2023, 2024], 0.10),
            "AXIA3": _h("AXIA3", [2024, 2025], 0.20)}
    out = tc.dobrar_historico(hist, {"ELET3": "AXIA3"})
    assert set(out) == {"AXIA3"}
    h = out["AXIA3"]
    assert [d.year for d in h["Data"]] == [2022, 2023, 2024, 2025]
    assert h.loc[h["Data"].dt.year == 2024, "ROE"].item() == 0.20  # atual prevalece
    assert set(h["Ticker"]) == {"AXIA3"}


def _hist_disp(tk, linhas):
    """linhas: (ano, ROE, P/L, AvailableAt)."""
    return pd.DataFrame({
        "Ticker": tk,
        "Data": [pd.Timestamp(a, 12, 31) for a, *_ in linhas],
        "ROE": [r for _, r, _, _ in linhas],
        "P/L": [pl for _, _, pl, _ in linhas],
        "AvailableAt": [d for *_, d in linhas],
    })


def _ano(h, ano, col):
    return h.loc[h["Data"].dt.year == ano, col].item()


def test_valor_emprestado_do_antigo_adia_a_disponibilidade():
    # O atual publicou 2024 em março sem P/L; o antigo trouxe o P/L em maio.
    # Emprestar o P/L e manter março anteciparia em dois meses um dado que
    # só existia em maio.
    marco, maio = pd.Timestamp("2025-03-10", tz="UTC"), pd.Timestamp("2025-05-10", tz="UTC")
    hist = {"AXIA3": _hist_disp("AXIA3", [(2024, 0.20, np.nan, marco), (2023, 0.15, 7.0, marco)]),
            "ELET3": _hist_disp("ELET3", [(2024, 0.19, 8.0, maio), (2023, 0.14, 6.0, maio)])}
    h = tc.dobrar_historico(hist, {"ELET3": "AXIA3"})["AXIA3"]
    assert _ano(h, 2024, "P/L") == 8.0
    assert _ano(h, 2024, "AvailableAt") == maio
    # nada emprestado: a linha do atual fica com a própria data
    assert _ano(h, 2023, "P/L") == 7.0 and _ano(h, 2023, "AvailableAt") == marco


def test_rebalanceamento_de_abril_nao_consome_o_valor_de_maio():
    import views.empresas_b3 as emp

    marco, maio = pd.Timestamp("2025-03-10", tz="UTC"), pd.Timestamp("2025-05-10", tz="UTC")
    hist = {"AXIA3": _hist_disp("AXIA3", [(2024, 0.20, np.nan, marco)]),
            "ELET3": _hist_disp("ELET3", [(2024, 0.19, 8.0, maio)])}
    h = tc.dobrar_historico(hist, {"ELET3": "AXIA3"})["AXIA3"].assign(_ano=2024)
    _, aceita = emp._classificar_disponibilidade_pit(h, pd.Timestamp(2025, 4, 1))
    assert not aceita.any()
    _, aceita = emp._classificar_disponibilidade_pit(h, pd.Timestamp(2025, 6, 1))
    assert aceita.all()


def test_baseline_e_medido_nao_se_misturam_na_mesma_data():
    maio = pd.Timestamp("2025-05-10", tz="UTC")
    # atual baseline (prazo legal), antigo medido: as disponibilidades não se
    # comparam, então o antigo não empresta — e vice-versa
    hist = {"AXIA3": _hist_disp("AXIA3", [(2024, 0.20, np.nan, pd.NaT)]),
            "ELET3": _hist_disp("ELET3", [(2024, 0.19, 8.0, maio), (2022, 0.10, 5.0, maio)])}
    h = tc.dobrar_historico(hist, {"ELET3": "AXIA3"})["AXIA3"]
    assert np.isnan(_ano(h, 2024, "P/L")) and pd.isna(_ano(h, 2024, "AvailableAt"))
    # a data que só o antigo tem entra inteira, com a disponibilidade dele
    assert _ano(h, 2022, "P/L") == 5.0 and _ano(h, 2022, "AvailableAt") == maio

    hist = {"AXIA3": _hist_disp("AXIA3", [(2024, 0.20, np.nan, maio)]),
            "ELET3": _hist_disp("ELET3", [(2024, 0.19, 8.0, pd.NaT)])}
    h = tc.dobrar_historico(hist, {"ELET3": "AXIA3"})["AXIA3"]
    assert np.isnan(_ano(h, 2024, "P/L")) and _ano(h, 2024, "AvailableAt") == maio


def test_carimbo_malformado_nao_empresta():
    maio = pd.Timestamp("2025-05-10", tz="UTC")
    hist = {"AXIA3": pd.DataFrame({"Ticker": "AXIA3", "Data": [pd.Timestamp(2024, 12, 31)],
                                   "ROE": [0.20], "P/L": [np.nan], "AvailableAt": [maio]}),
            "ELET3": pd.DataFrame({"Ticker": "ELET3", "Data": [pd.Timestamp(2024, 12, 31)],
                                   "ROE": [0.19], "P/L": [8.0], "AvailableAt": ["lixo"]})}
    h = tc.dobrar_historico(hist, {"ELET3": "AXIA3"})["AXIA3"]
    assert np.isnan(_ano(h, 2024, "P/L")) and _ano(h, 2024, "AvailableAt") == maio


def test_dois_baselines_emprestam_e_seguem_baseline():
    hist = {"AXIA3": _hist_disp("AXIA3", [(2024, 0.20, np.nan, pd.NaT)]),
            "ELET3": _hist_disp("ELET3", [(2024, 0.19, 8.0, pd.NaT)])}
    h = tc.dobrar_historico(hist, {"ELET3": "AXIA3"})["AXIA3"]
    assert _ano(h, 2024, "P/L") == 8.0 and pd.isna(_ano(h, 2024, "AvailableAt"))
    # sem a coluna (contrato de market_read: ausência = tudo baseline)
    hist = {k: v.drop(columns="AvailableAt") for k, v in hist.items()}
    h = tc.dobrar_historico(hist, {"ELET3": "AXIA3"})["AXIA3"]
    assert _ano(h, 2024, "P/L") == 8.0 and "AvailableAt" not in h.columns


def test_escalares_irmas():
    mapa = {"ELET3": "AXIA3", "CARD3": "CSUD3"}
    assert tc.dobrar_escalares({"ELET3": 1.0, "AXIA3": 2.0, "CARD3": 3.0}, mapa) == {
        "AXIA3": 2.0, "CSUD3": 3.0}
    assert tc.dobrar_escalares({"ELET3": 25, "AXIA3": 1}, mapa, modo="max") == {"AXIA3": 25}
    irmas = {"ELET3": ("ELET6", "AXIA3"), "AXIA3": ("ELET3", "ELET6"), "ELET6": ("ELET3", "AXIA3")}
    assert tc.dobrar_irmas(irmas, mapa) == {"AXIA3": ("ELET6",), "ELET6": ("AXIA3",)}


# ── Leitor do universo ───────────────────────────────────────────────────────

_ELET = ("Utilidade Pública", "Energia Elétrica", "Energia Elétrica")
_OUTROS = ("Outros", "Outros", "Outros")


def _universo_bruto() -> pd.DataFrame:
    """O que a consulta de load_setores devolvia: cada código casa com a SUA
    linha de public.setores — ELET3 com a oficial, AXIA3 com uma inferida."""
    linhas = [
        ("ELET3", 1, "Eletrobras", *_ELET, "N1"),
        ("ELET6", 1, "Eletrobras", *_ELET, "N1"),
        ("AXIA3", 1, "Axia", *_OUTROS, None),
        ("ENEE3", 2, "Energia E", *_ELET, "NM"),
        ("ENFF3", 3, "Energia F", *_ELET, "NM"),
        ("OUTA3", 4, "Outra A", *_OUTROS, "NM"),
        ("OUTB3", 5, "Outra B", *_OUTROS, "NM"),
    ]
    return pd.DataFrame(linhas, columns=["ticker", "company_id", "nome_empresa", "SETOR",
                                         "SUBSETOR", "SEGMENTO", "listagem"])


def _load_setores(monkeypatch, bruto: pd.DataFrame, aliases: pd.DataFrame) -> pd.DataFrame:
    from core import market_read as mr

    def fake_q(sql, params=None, engine=None):
        return aliases.copy() if "ticker_alias" in sql else bruto.copy()

    monkeypatch.setattr(mr, "_q", fake_q)
    return mr.load_setores.__wrapped__()


def test_load_setores_poe_os_dois_codigos_no_mesmo_segmento(monkeypatch):
    df = _load_setores(monkeypatch, _universo_bruto(),
                       pd.DataFrame(columns=["brapi_symbol", "b3_ticker", "motivo"]))
    seg = df.set_index("ticker")["SEGMENTO"]
    assert seg["ELET3"] == seg["AXIA3"] == "Energia Elétrica"
    atual = df.set_index("ticker")["codigo_atual"]
    assert atual["ELET3"] == "AXIA3" and atual["ELET6"] == "ELET6"
    assert "listagem" not in df.columns


def test_load_setores_sem_tabela_de_alias_ainda_usa_os_pares_conhecidos(monkeypatch):
    falhou = pd.DataFrame()
    falhou.attrs["load_error"] = "query_failed"
    df = _load_setores(monkeypatch, _universo_bruto(), falhou)
    assert df.set_index("ticker").at["ELET3", "codigo_atual"] == "AXIA3"


# ── Aba de portfólio: liderança e benchmark do segmento ──────────────────────


def _hist(tks_roe: dict[str, float], anos) -> dict[str, pd.DataFrame]:
    return {
        tk: pd.DataFrame({
            "Ticker": tk,
            "Data": [pd.Timestamp(a, 12, 31) for a in anos],
            "ROE": [roe] * len(anos),
            "ROIC": [roe * 0.8] * len(anos),
            "Margem_Liquida": [0.08] * len(anos),
            "P/L": [8.0] * len(anos),
            "P/VP": [1.2] * len(anos),
            "DY": [0.04] * len(anos),
            "AvailableAt": [pd.Timestamp(a + 1, 3, 20) for a in anos],
        })
        for tk, roe in tks_roe.items()
    }


def _rodar_segmentos(universo: pd.DataFrame, hist: dict, precos: pd.DataFrame) -> list[dict]:
    """O laço da aba (views.portfolio_b3.render): um _processar_segmento por segmento."""
    from views import portfolio_b3 as portfolio

    resultados = []
    for (setor, sub, seg), grupo in universo.groupby(["SETOR", "SUBSETOR", "SEGMENTO"]):
        tks = [t for t in grupo["ticker"] if t in hist]
        res = portfolio._processar_segmento(
            tks, hist, precos, str(setor), str(sub), str(seg),
            taxa_selic_aa=0.0, selic_macro={}, macro_history={}, aporte=1000.0,
            ano_inicio=2018, gamma=1.0, cap=1.0, soft=0.0,
        )
        if res:
            resultados.append(res)
    return resultados


def _cenario():
    anos = list(range(2016, pd.Timestamp.now().year))
    # Eletrobras ON tem o melhor ROE; o histórico mora nos dois códigos
    # (a ingestão gravou os fatos sob os dois).
    roe = {"ELET3": 0.30, "AXIA3": 0.30, "ELET6": 0.05, "ENEE3": 0.12, "ENFF3": 0.10,
           "OUTA3": 0.12, "OUTB3": 0.10}
    hist = _hist(roe, anos)
    idx = pd.date_range("2016-01-31", periods=(pd.Timestamp.now().year - 2016) * 12, freq="ME")
    precos = pd.DataFrame({
        tk: 100.0 * np.cumprod(np.full(len(idx), 1.004 + i * 0.001))
        for i, tk in enumerate(roe)
    }, index=idx)
    # AXIA3 só tem preço desde a troca (ago/2025); antes, só ELET3.
    precos["AXIA3"] = (precos["ELET3"] * 1.05).where(idx >= pd.Timestamp("2025-08-01"))
    return hist, precos


def _lideres_eletrobras_on(resultados: list[dict]) -> dict[int, list[str]]:
    por_ano: dict[int, list[str]] = {}
    for res in resultados:
        for ano, lids in res["lids_por_ano"].items():
            por_ano.setdefault(ano, []).extend(t for t in lids if t in ("ELET3", "AXIA3"))
    return por_ano


def _universo_da_aba(monkeypatch, hist, precos):
    """O caminho da aba depois da correção: load_setores → dedup → dobras."""
    df = _load_setores(monkeypatch, _universo_bruto(),
                       pd.DataFrame(columns=["brapi_symbol", "b3_ticker", "motivo"]))
    df, mapa = tc.deduplicar_por_empresa(df)
    hist = tc.dobrar_historico(hist, mapa)
    precos = tc.dobrar_colunas(precos, mapa)
    return df, hist, precos


def test_sem_a_correcao_a_eletrobras_liderava_dois_segmentos():
    # Controle: prova que o cenário reproduz o defeito, para o teste seguinte
    # não passar por vacuidade.
    hist, precos = _cenario()
    res = _rodar_segmentos(_universo_bruto(), hist, precos)
    dobrados = {a: t for a, t in _lideres_eletrobras_on(res).items() if len(t) > 1}
    assert dobrados, "o cenário deveria reproduzir a liderança dupla"


def test_par_renomeado_nao_lidera_dois_segmentos_no_mesmo_ano(monkeypatch):
    hist, precos = _cenario()
    universo, hist, precos = _universo_da_aba(monkeypatch, hist, precos)
    por_ano = _lideres_eletrobras_on(_rodar_segmentos(universo, hist, precos))
    assert por_ano, "a Eletrobras ON deveria liderar algum ano"
    # no máximo uma liderança por ano, e sempre sob o código atual
    assert all(t in ([], ["AXIA3"]) for t in por_ano.values()), por_ano
    # com o histórico do ELET3 dobrado, lidera desde a primeira safra
    assert min(a for a, t in por_ano.items() if t) == 2018


def test_benchmark_do_segmento_conta_a_empresa_uma_vez_e_mantem_on_pn(monkeypatch):
    hist, precos = _cenario()
    universo, hist, precos = _universo_da_aba(monkeypatch, hist, precos)
    resultados = _rodar_segmentos(universo, hist, precos)
    # o universo do equal-weight do segmento é o conjunto de tickers dele
    todos = [tk for r in resultados for tk in r["tickers"]]
    assert "ELET3" not in todos and todos.count("AXIA3") == 1
    energia = next(r for r in resultados if "ENEE3" in r["tickers"])
    assert "AXIA3" in energia["tickers"]
    assert "ELET6" in energia["tickers"]            # PN é outro ativo, fica
