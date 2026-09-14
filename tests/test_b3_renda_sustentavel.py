"""Sustentabilidade histórica da distribuição — spec 2026-09-09."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.b3_renda_sustentavel import (
    MIN_ANOS,
    enrich_com_historico_patrimonial,
    enrich_com_renda_sustentavel,
    fracao_pl_em_queda_com_lucro,
    leitura_da_serie,
    sustentabilidade_do_ano,
)


def _serie(payouts, dys=None):
    """Histórico anual no formato de load_multiplos_historico_batch."""
    n = len(payouts)
    dys = dys if dys is not None else [0.05] * n
    return pd.DataFrame({
        "Ticker": ["XPTO3"] * n,
        "Data": pd.to_datetime([f"{2018 + i}-12-31" for i in range(n)]),
        "DY": dys,
        "Payout": payouts,
    })


def test_fronteiras_da_faixa():
    # Teste 1 do spec: os quatro pontos exatos da faixa de dois lados.
    assert sustentabilidade_do_ano(0.05) == 0.0
    assert sustentabilidade_do_ano(1.30) == 0.0
    assert sustentabilidade_do_ano(0.25) == 1.0
    assert sustentabilidade_do_ano(0.80) == 1.0


def test_sustentabilidade_do_ano_ausencia_nunca_pune():
    # Rodada de correção 1, C-1: antes desta rodada a cópia do B3 devolvia
    # 0.0 (nota pior possível) para ausência, divergindo da cópia dos EUA
    # (que já devolvia None) e contradizendo a Global Constraint do plano
    # ("Ausência nunca pune"). Agora as duas importam a mesma banda
    # compartilhada (core/renda_sustentavel_banda.py) e a política é None.
    assert sustentabilidade_do_ano(None) is None
    assert sustentabilidade_do_ano(np.nan) is None
    assert sustentabilidade_do_ano(np.inf) is None
    assert sustentabilidade_do_ano("abc") is None


def test_episodio_isolado_nao_condena():
    # Teste 2: um ano de 300% em oito, os outros sete dentro da faixa.
    leitura = leitura_da_serie(_serie([0.50] * 7 + [3.00]))
    assert leitura["payout_sustentabilidade"] >= 0.85


def test_padrao_persistente_zera():
    # Teste 3: acima do TETO em todos os anos.
    leitura = leitura_da_serie(_serie([1.40, 1.55, 2.10, 1.80, 1.60]))
    assert leitura["payout_sustentabilidade"] == 0.0


def test_ano_incoerente_sai_como_ausencia():
    # Teste 4: DY > 0,5% com Payout <= 1% é contradição da fonte.
    leitura = leitura_da_serie(
        _serie([0.40, 0.005, 0.50, 0.60], dys=[0.05, 0.06, 0.05, 0.05])
    )
    assert leitura["n_anos_payout"] == 3
    assert leitura["payout_sustentabilidade"] == 1.0


def test_historico_curto_produz_nan():
    # Teste 5: menos de MIN_ANOS observados não vira nota.
    leitura = leitura_da_serie(_serie([0.40] * (MIN_ANOS - 1)))
    assert leitura["payout_sustentabilidade"] is None
    assert leitura["n_anos_payout"] == MIN_ANOS - 1


def test_payouts_nao_finitos_saem_da_serie_como_ausencia():
    """Infinitos/NaN não entram nem na contagem nem na mediana histórica."""
    leitura = leitura_da_serie(_serie([0.40, np.inf, 0.50, -np.inf, np.nan, 0.60]))
    assert leitura["n_anos_payout"] == 3
    assert leitura["payout_sustentabilidade"] == 1.0
    assert leitura["payout_mediano_hist"] == pytest.approx(0.50)


def test_janela_desliza_sobre_exercicios_nao_sobre_observacoes_validas():
    """I-1: a janela recorta os últimos JANELA_ANOS EXERCÍCIOS, não as
    últimas JANELA_ANOS observações válidas.

    Cenário do revisor: 14 exercícios, os 8 mais antigos ótimos e os 6 mais
    recentes incoerentes (DY alto com payout quase nulo — saem como
    ausência). Antes da correção, o corte de [-JANELA_ANOS:] era aplicado
    DEPOIS de remover os incoerentes, então a janela "deslizava" para trás e
    pontuava 1.0 com os 8 anos antigos, sem nenhum sinal dos últimos 6 anos.
    Depois da correção, os últimos 8 exercícios são recortados PRIMEIRO;
    deles, só os 2 mais antigos (ainda ótimos) sobram coerentes — 2 anos,
    abaixo de MIN_ANOS — então o resultado é ausência (neutro), não nota
    cheia.
    """
    payouts = [0.50] * 8 + [0.005] * 6  # 8 exercícios antigos ótimos, 6 recentes incoerentes
    dys = [0.05] * 8 + [0.06] * 6
    leitura = leitura_da_serie(_serie(payouts, dys))
    assert leitura["n_anos_payout"] == 2
    assert leitura["payout_sustentabilidade"] is None


def test_dy_sustentavel_nunca_cai_no_dy_bruto():
    # Teste 6: sem sustentabilidade, dy_sustentavel é NaN — nunca o DY cru.
    df_mult = pd.DataFrame({"Ticker": ["XPTO3", "CURTA3"], "DY": [0.08, 0.09]})
    hist = {
        "XPTO3": _serie([0.40, 0.50, 0.60, 0.55]),
        "CURTA3": _serie([0.40, 0.50]),
    }
    out = enrich_com_renda_sustentavel(df_mult, hist)
    linha = out[out["Ticker"] == "CURTA3"].iloc[0]
    assert np.isnan(linha["payout_sustentabilidade"])
    assert np.isnan(linha["dy_sustentavel"])
    boa = out[out["Ticker"] == "XPTO3"].iloc[0]
    assert boa["dy_sustentavel"] == pytest.approx(0.08 * boa["payout_sustentabilidade"])


def test_mesma_derivacao_para_as_mesmas_linhas():
    # Teste 7 do spec: tela e Análise do Portfólio derivam o MESMO valor.
    # Regra certa num consumidor só já publicou número errado neste projeto.
    hist = {
        "XPTO3": _serie([0.30, 0.45, 0.60, 0.75, 0.90]),
        "OUTRO4": _serie([1.40, 1.50, 1.60, 1.70]),
    }
    df_tela = pd.DataFrame({"Ticker": ["XPTO3", "OUTRO4"], "DY": [0.08, 0.11]})
    df_analise = pd.DataFrame({"Ticker": ["OUTRO4", "XPTO3"], "DY": [0.11, 0.08]})
    a = enrich_com_renda_sustentavel(df_tela, hist).set_index("Ticker")
    b = enrich_com_renda_sustentavel(df_analise, hist).set_index("Ticker")
    for tk in ("XPTO3", "OUTRO4"):
        assert a.loc[tk, "payout_sustentabilidade"] == pytest.approx(
            b.loc[tk, "payout_sustentabilidade"])
        assert a.loc[tk, "dy_sustentavel"] == pytest.approx(
            b.loc[tk, "dy_sustentavel"])


_COLUNAS_CONTRATO_RS = (
    "payout_sustentabilidade", "payout_mediano_hist", "n_anos_payout",
    "dy_sustentavel",
)


def test_enrich_preserva_quadro_sem_historico():
    # I-3, caminho "hist_batch vazio": a saída antecipada não pode devolver
    # o quadro sem as quatro colunas de contrato — esse é justamente o
    # caminho alcançável em produção quando o loader devolve {} (ex.:
    # core/b3_data.py::_financeiro engolindo uma exceção do banco).
    df_mult = pd.DataFrame({"Ticker": ["XPTO3"], "DY": [0.08]})
    out = enrich_com_renda_sustentavel(df_mult, {})
    assert list(out["Ticker"]) == ["XPTO3"]
    for coluna in _COLUNAS_CONTRATO_RS:
        assert coluna in out.columns
        assert np.isnan(out[coluna].iloc[0])


def test_enrich_preserva_colunas_de_contrato_com_df_mult_vazio():
    # I-3, caminho "df_mult vazio": mesmo sem nenhuma linha, o contrato de
    # colunas de saída deve existir (0 linhas, 4 colunas a mais).
    df_mult = pd.DataFrame({"Ticker": pd.Series(dtype=str), "DY": pd.Series(dtype=float)})
    out = enrich_com_renda_sustentavel(df_mult, {"XPTO3": _serie([0.40, 0.50, 0.60])})
    assert out.empty
    for coluna in _COLUNAS_CONTRATO_RS:
        assert coluna in out.columns


def test_enrich_preserva_colunas_de_contrato_com_historico_sem_payout():
    # I-3: hist_batch não é vazio, mas o histórico não tem coluna Payout —
    # _anos_observados descarta a chave em silêncio. `dados` ainda é
    # preenchido por chave (o laço sempre insere uma entrada), então este
    # caso segue para o merge; o contrato de colunas precisa se manter de
    # qualquer forma, com todas as quatro colunas em NaN.
    df_mult = pd.DataFrame({"Ticker": ["XPTO3"], "DY": [0.08]})
    hist_sem_payout = {"XPTO3": pd.DataFrame({"Data": pd.to_datetime(["2023-12-31"])})}
    out = enrich_com_renda_sustentavel(df_mult, hist_sem_payout)
    assert list(out["Ticker"]) == ["XPTO3"]
    for coluna in _COLUNAS_CONTRATO_RS:
        assert coluna in out.columns
    # n_anos_payout é 0 (contagem real), não NaN; as demais são ausência.
    assert out["n_anos_payout"].iloc[0] == 0
    assert pd.isna(out["payout_sustentabilidade"].iloc[0])
    assert pd.isna(out["payout_mediano_hist"].iloc[0])
    assert pd.isna(out["dy_sustentavel"].iloc[0])


def test_com_colunas_de_contrato_anexa_as_quatro_colunas_em_nan():
    # I-3, unidade do helper usado pelas duas saídas antecipadas de
    # enrich_com_renda_sustentavel (inclusive o ramo "dados vazio", hoje
    # inalcançável a partir de hist_batch mas coberto pelo mesmo helper).
    from core.b3_renda_sustentavel import _com_colunas_de_contrato

    df_mult = pd.DataFrame({"Ticker": ["XPTO3"], "DY": [0.08]})
    out = _com_colunas_de_contrato(df_mult)
    assert list(out["Ticker"]) == ["XPTO3"]
    for coluna in _COLUNAS_CONTRATO_RS:
        assert coluna in out.columns
        assert np.isnan(out[coluna].iloc[0])


def _anual(pares):
    """Pares: lista de (ano, pl_mi, lucro_mi)."""
    return [{"ano": ano, "pl_mi": pl, "lucro_mi": lucro}
            for ano, pl, lucro in pares]


def test_fracao_conta_apenas_pares_com_queda_e_lucro():
    serie = _anual([
        (2019, 100.0, 10.0),
        (2020, 90.0, 8.0),
        (2021, 95.0, 9.0),
        (2022, 80.0, 5.0),
        (2023, 70.0, -2.0),
    ])
    frac, n_pares = fracao_pl_em_queda_com_lucro(serie)
    assert n_pares == 4
    assert frac == pytest.approx(0.5)


def test_ausencia_de_dado_nao_e_zero():
    """Faltante não pode fabricar queda por coerção para zero."""
    serie = _anual([
        (2019, 100.0, 10.0),
        (2020, None, 8.0),
        (2021, 95.0, None),
        (2022, 90.0, 7.0),
    ])
    frac, n_pares = fracao_pl_em_queda_com_lucro(serie)
    assert n_pares == 1
    assert frac == pytest.approx(1.0)


def test_pares_patrimoniais_nao_finitos_sao_ausencia():
    serie = _anual([
        (2019, 100.0, 10.0),
        (2020, np.inf, 8.0),
        (2021, 90.0, -np.inf),
        (2022, 80.0, 7.0),
    ])
    frac, n_pares = fracao_pl_em_queda_com_lucro(serie)
    assert n_pares == 1
    assert frac == pytest.approx(1.0)


def test_serie_sem_par_avaliavel():
    frac, n_pares = fracao_pl_em_queda_com_lucro(_anual([(2020, 100.0, 5.0)]))
    assert frac is None
    assert n_pares == 0


def test_enrich_patrimonial_acrescenta_colunas():
    df_mult = pd.DataFrame({"Ticker": ["XPTO3", "SEMDADO3"], "DY": [0.08, 0.02]})
    lote = {"XPTO3": _anual([(2019, 100.0, 10.0), (2020, 90.0, 8.0)])}
    out = enrich_com_historico_patrimonial(df_mult, lote)
    linha = out[out["Ticker"] == "XPTO3"].iloc[0]
    assert linha["pl_queda_com_lucro_frac"] == pytest.approx(1.0)
    assert linha["n_pares_pl"] == 1
    vazia = out[out["Ticker"] == "SEMDADO3"].iloc[0]
    assert np.isnan(vazia["pl_queda_com_lucro_frac"])


def test_criacao_de_portfolio_enriquece_frame_decisorio_sem_fabricar_zero(
    monkeypatch,
):
    """O piso e a rota leem o mesmo quadro, inclusive quando há lacuna."""
    import core.dossie_b3 as dossie
    from views.portfolio_b3 import _enrich_decision_universe

    df_mult = pd.DataFrame({
        "Ticker": ["COMHIST3", "SEMHIST3"],
        "DY": [0.08, 0.09],
    })
    hist = {"COMHIST3": _serie([0.40, 0.50, 0.60])}
    monkeypatch.setattr(dossie, "load_pl_lucro_anual_batch", lambda _tickers: {
        "COMHIST3": _anual([(2021, 100.0, 10.0), (2022, 90.0, 8.0)]),
    })

    out = _enrich_decision_universe(df_mult, hist, ("COMHIST3", "SEMHIST3"))
    com_hist = out.set_index("Ticker").loc["COMHIST3"]
    sem_hist = out.set_index("Ticker").loc["SEMHIST3"]

    assert com_hist["payout_sustentabilidade"] == pytest.approx(1.0)
    assert com_hist["pl_queda_com_lucro_frac"] == pytest.approx(1.0)
    assert np.isnan(sem_hist["payout_sustentabilidade"])
    assert np.isnan(sem_hist["dy_sustentavel"])
    assert np.isnan(sem_hist["pl_queda_com_lucro_frac"])


def test_entry_guard_recebe_sustentabilidade_historica_sem_fabricar_zero(
    monkeypatch,
):
    """O guard da carteira recebe o quadro enriquecido, não o snapshot cru."""
    import core.dossie_b3 as dossie
    import views.portfolio_b3 as portfolio_b3

    df_mult = pd.DataFrame({
        "Ticker": ["COMHIST3", "SEMHIST3"],
        "DY": [0.08, 0.09],
    })
    df_set = pd.DataFrame({
        "ticker": ["COMHIST3", "SEMHIST3"],
        "SETOR": ["Teste", "Teste"],
        "SUBSETOR": ["Teste", "Teste"],
        "SEGMENTO": ["Teste", "Teste"],
    })
    hist_guard = {"COMHIST3": _serie([0.40, 0.50, 0.60])}
    monkeypatch.setattr(dossie, "load_pl_lucro_anual_batch", lambda _tickers: {})

    received: dict[str, pd.DataFrame] = {}

    def fake_build(frame, *_args):
        received["frame"] = frame
        return {}, pd.DataFrame()

    monkeypatch.setattr(portfolio_b3, "_build_entry_guard", fake_build)
    portfolio_b3._prepare_entry_guard(
        df_mult, df_set, hist_guard, None, ("COMHIST3", "SEMHIST3")
    )

    frame = received["frame"].set_index("Ticker")
    assert frame.loc["COMHIST3", "payout_sustentabilidade"] == pytest.approx(1.0)
    assert frame.loc["COMHIST3", "dy_sustentavel"] == pytest.approx(0.08)
    assert np.isnan(frame.loc["SEMHIST3", "payout_sustentabilidade"])
    assert np.isnan(frame.loc["SEMHIST3", "dy_sustentavel"])


_COLUNAS_DECISAO = (
    "payout_sustentabilidade", "payout_mediano_hist", "n_anos_payout",
    "dy_sustentavel", "pl_queda_com_lucro_frac", "n_pares_pl",
)


def test_empresas_b3_enriquece_o_quadro_que_a_decisao_le(monkeypatch):
    """Rodada de correção 1: a ligação nas duas telas de Empresas B3 (dossiê
    da Análise de Empresa e ranking da Análise Avançada) tinha só leitura de
    código para provar que a evidência chega ao quadro. As seis colunas
    (renda + patrimonial) têm que aparecer com valor DERIVADO do histórico
    injetado, não só a coluna existindo vazia — e ausência tem que ficar NaN,
    nunca zero.
    """
    import core.dossie_b3 as dossie
    from views.empresas_b3 import _enrich_com_evidencia_historica

    df_mult = pd.DataFrame({
        "Ticker": ["COMHIST3", "SEMHIST3"],
        "DY": [0.08, 0.09],
    })
    hist = {"COMHIST3": _serie([0.40, 0.50, 0.60])}
    monkeypatch.setattr(dossie, "load_pl_lucro_anual_batch", lambda _tickers: {
        "COMHIST3": _anual([(2021, 100.0, 10.0), (2022, 90.0, 8.0)]),
    })

    out = _enrich_com_evidencia_historica(
        df_mult, hist, ("COMHIST3", "SEMHIST3")
    ).set_index("Ticker")

    for coluna in _COLUNAS_DECISAO:
        assert coluna in out.columns

    com_hist = out.loc["COMHIST3"]
    assert com_hist["payout_sustentabilidade"] == pytest.approx(1.0)
    assert com_hist["dy_sustentavel"] == pytest.approx(0.08)
    assert com_hist["pl_queda_com_lucro_frac"] == pytest.approx(1.0)
    assert com_hist["n_pares_pl"] == 1

    sem_hist = out.loc["SEMHIST3"]
    for coluna in _COLUNAS_DECISAO:
        assert np.isnan(sem_hist[coluna])


def test_analise_do_portfolio_db_enriquece_o_universo_que_o_score_le(
    monkeypatch,
):
    """Mesma cobertura para core.portfolio_db_analysis: a função extraída
    (``_enriquece_universo_com_evidencia_historica``) roda sem Streamlit/
    banco de verdade, então o teste exercita a composição real, não uma
    reimplementação dela.
    """
    import core.dossie_b3 as dossie
    from core.portfolio_db_analysis import (
        _enriquece_universo_com_evidencia_historica,
    )

    universo = pd.DataFrame({
        "Ticker": ["COMHIST3", "SEMHIST3"],
        "DY": [0.08, 0.09],
    })
    historicos = {"COMHIST3": _serie([0.40, 0.50, 0.60])}
    monkeypatch.setattr(dossie, "load_pl_lucro_anual_batch", lambda _tickers: {
        "COMHIST3": _anual([(2021, 100.0, 10.0), (2022, 90.0, 8.0)]),
    })

    out = _enriquece_universo_com_evidencia_historica(
        universo, historicos, ("COMHIST3", "SEMHIST3")
    ).set_index("Ticker")

    for coluna in _COLUNAS_DECISAO:
        assert coluna in out.columns

    com_hist = out.loc["COMHIST3"]
    assert com_hist["payout_sustentabilidade"] == pytest.approx(1.0)
    assert com_hist["dy_sustentavel"] == pytest.approx(0.08)
    assert com_hist["pl_queda_com_lucro_frac"] == pytest.approx(1.0)
    assert com_hist["n_pares_pl"] == 1

    sem_hist = out.loc["SEMHIST3"]
    for coluna in _COLUNAS_DECISAO:
        assert np.isnan(sem_hist[coluna])
