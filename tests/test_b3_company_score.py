from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.b3_company_score import TRACK_LABELS, classification, score_cross_section


def _universe() -> pd.DataFrame:
    return pd.DataFrame([
        {"Ticker": "BOA3", "ROE": .24, "ROA": .12, "ROIC": .20,
         "Margem_Liquida": .20, "Margem_Operacional": .25,
         "Endividamento_Total": .40, "Liquidez_Corrente": 2.0,
         "P/L": 7.0, "P/VP": 1.1, "EV_EBIT": 6.0, "P_FCO": 5.0,
         "DY": .08, "Payout": .55, "ROE_slope_log": .08,
         "ROIC_slope_log": .07, "Margem_Liquida_slope_log": .04,
         "Margem_Operacional_slope_log": .03},
        {"Ticker": "MED3", "ROE": .14, "ROA": .07, "ROIC": .12,
         "Margem_Liquida": .12, "Margem_Operacional": .15,
         "Endividamento_Total": .90, "Liquidez_Corrente": 1.4,
         "P/L": 12.0, "P/VP": 1.8, "EV_EBIT": 10.0, "P_FCO": 9.0,
         "DY": .04, "Payout": .40, "ROE_slope_log": .03,
         "ROIC_slope_log": .02, "Margem_Liquida_slope_log": .01,
         "Margem_Operacional_slope_log": .01},
        {"Ticker": "FRA3", "ROE": .04, "ROA": .01, "ROIC": .02,
         "Margem_Liquida": .02, "Margem_Operacional": .04,
         "Endividamento_Total": 2.2, "Liquidez_Corrente": .7,
         "P/L": 28.0, "P/VP": 4.0, "EV_EBIT": 24.0, "P_FCO": 22.0,
         "DY": .01, "Payout": .20, "ROE_slope_log": -.05,
         "ROIC_slope_log": -.04, "Margem_Liquida_slope_log": -.03,
         "Margem_Operacional_slope_log": -.02},
        {"Ticker": "NUL3", "ROE": np.nan, "ROA": np.nan, "ROIC": np.nan},
    ])


def test_score_b3_ordena_pares_e_mantem_seis_trilhas():
    scored = score_cross_section(_universe()).set_index("Ticker")
    assert scored.loc["BOA3", "score"] > scored.loc["FRA3", "score"]
    assert scored.loc["BOA3", "score_valuation"] > scored.loc["FRA3", "score_valuation"]
    assert scored.loc["BOA3", "score_solidity"] > scored.loc["FRA3", "score_solidity"]
    for column in TRACK_LABELS:
        assert 0 <= scored.loc["BOA3", column] <= 100


def test_ausencia_e_neutra_mas_reduz_cobertura():
    scored = score_cross_section(_universe()).set_index("Ticker")
    assert scored.loc["NUL3", "coverage"] < scored.loc["BOA3", "coverage"]
    assert scored.loc["NUL3", "score_growth"] == 50.0


def test_multiplos_negativos_nao_sao_tratados_como_barganha():
    """Deficitária fica no fundo da trilha, não na mediana dela.

    A versão anterior exigia coverage_valuation == 0 e score_valuation == 50,0 —
    tratava o múltiplo negativo como dado ausente. Mas 50 é a mediana do corte,
    então a empresa deficitária saía "mais barata" que metade do universo. Hoje
    o múltiplo é ranqueado pelo yield recíproco, que é monótono através do zero:
    o dado existe (cobertura cheia) e ranqueia embaixo. Ver
    tests/test_score_sinal_de_denominador.py (achado A-101).
    """
    universe = _universe()
    universe.loc[universe["Ticker"] == "FRA3", ["P/L", "P/VP", "EV_EBIT", "P_FCO"]] = -5
    scored = score_cross_section(universe).set_index("Ticker")
    assert scored.loc["FRA3", "coverage_valuation"] == 100
    assert scored.loc["FRA3", "score_valuation"] < 50.0
    assert scored.loc["FRA3", "score_valuation"] < scored.loc["MED3", "score_valuation"]


def test_classificacao_visual():
    assert classification(80) == ("Excelente", "sucesso")
    assert classification(52) == ("Neutra", "neutro")
    assert classification(None) == ("Sem classificação", "neutro")


@pytest.fixture
def _restaura_facade_b3():
    """O AppTest roda o script NESTE processo e substitui atributos de
    core.b3_data/core.dossie_b3. Sem restaurar, o vazamento derruba
    tests/test_market_read.py quando a suíte roda inteira."""
    import core.b3_data as facade
    import core.dossie_b3 as dossie
    originais = [
        (facade, "load_multiplos_todos", facade.load_multiplos_todos),
        (facade, "load_multiplos_historico_batch", facade.load_multiplos_historico_batch),
        (dossie, "build_dossie", dossie.build_dossie),
    ]
    try:
        yield
    finally:
        for modulo, nome, valor in originais:
            setattr(modulo, nome, valor)


def test_painel_b3_renderiza_radar_e_dossie_no_streamlit(_restaura_facade_b3):
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string("""
import pandas as pd
import core.dossie_b3 as dossie
import views.empresas_b3 as view

universo = pd.DataFrame([
    {'Ticker':'BOA3','ROE':.24,'ROA':.12,'ROIC':.20,'Margem_Liquida':.20,
     'Margem_Operacional':.25,'Endividamento_Total':.4,'Liquidez_Corrente':2,
     'P/L':7,'P/VP':1.1,'EV_EBIT':6,'P_FCO':5,'DY':.08,'Payout':.55},
    {'Ticker':'MED3','ROE':.14,'ROA':.07,'ROIC':.12,'Margem_Liquida':.12,
     'Margem_Operacional':.15,'Endividamento_Total':.9,'Liquidez_Corrente':1.4,
     'P/L':12,'P/VP':1.8,'EV_EBIT':10,'P_FCO':9,'DY':.04,'Payout':.4},
    {'Ticker':'FRA3','ROE':.04,'ROA':.01,'ROIC':.02,'Margem_Liquida':.02,
     'Margem_Operacional':.04,'Endividamento_Total':2.2,'Liquidez_Corrente':.7,
     'P/L':28,'P/VP':4,'EV_EBIT':24,'P_FCO':22,'DY':.01,'Payout':.2},
    {'Ticker':'OUT3','ROE':.10,'ROA':.05,'ROIC':.08,'Margem_Liquida':.08,
     'Margem_Operacional':.10,'Endividamento_Total':1.2,'Liquidez_Corrente':1.1,
     'P/L':16,'P/VP':2.2,'EV_EBIT':14,'P_FCO':12,'DY':.03,'Payout':.3},
])
meta = pd.DataFrame([
    {'ticker':tk,'SETOR':'Teste','SUBSETOR':'Teste','SEGMENTO':'Teste'}
    for tk in universo['Ticker']
])
view._db.load_multiplos_todos = lambda: universo
view._db.load_multiplos_historico_batch = lambda tickers: {}
dossie.build_dossie = lambda ticker: {
    'ticker':ticker, 'red_flags':[],
    'sensibilidade_juros':{'regra':'Alavancagem moderada.'},
    'eventos_societarios':{'eventos':[]},
}
view._render_b3_score_dashboard('BOA3', universo.iloc[0], meta)
""").run(timeout=60)

    assert not app.exception
    assert any(exp.label == "📄 Dossiê, classificação e critérios avançados"
               for exp in app.expander)
    rendered = "\n".join(item.value for item in app.markdown)
    assert "Pontuação fundamentalista" in rendered
    captions = "\n".join(item.value for item in app.caption)
    assert "Referência da comparação" in captions


def test_roe_nao_conta_duas_vezes_na_eficiencia_de_capital():
    """A trilha de eficiência de capital mede capital, não alavancagem.

    ROE estava em quality E em capital_efficiency, somando peso 0,13 — mais que
    qualquer outra métrica isolada, sem que a metodologia dissesse isso. Pior:
    ROE é alavancado, então dívida inflava a "eficiência do capital" que o ROIC
    existe justamente para medir sem esse efeito (achado A-102).
    """
    from core.b3_company_score import FACTOR_TRACKS

    assert [nome for nome, _ in FACTOR_TRACKS["capital_efficiency"]] == ["ROIC"]
    trilhas_com_roe = [t for t, ms in FACTOR_TRACKS.items()
                       if any(nome == "ROE" for nome, _ in ms)]
    assert trilhas_com_roe == ["quality"]


def test_trilha_com_meia_cobertura_nao_produz_conviccao_de_trilha_cheia():
    """Cobertura parcial encolhe a nota para o neutro (achado A-103).

    Antes, apurar solidez sobre uma métrica de duas dava os mesmos pontos que
    apurar sobre as duas; a diferença aparecia só na coluna de cobertura, ao
    lado da nota — não dentro dela.
    """
    universe = _universe()
    cheia = score_cross_section(universe).set_index("Ticker")
    parcial_df = universe.copy()
    parcial_df.loc[parcial_df["Ticker"] == "BOA3", "Liquidez_Corrente"] = np.nan
    parcial = score_cross_section(parcial_df).set_index("Ticker")

    assert parcial.loc["BOA3", "coverage_solidity"] < cheia.loc["BOA3", "coverage_solidity"]
    # A nota cheia era boa; a parcial precisa estar mais perto do neutro.
    assert cheia.loc["BOA3", "score_solidity"] > 50.0
    assert abs(parcial.loc["BOA3", "score_solidity"] - 50.0) < \
        abs(cheia.loc["BOA3", "score_solidity"] - 50.0)


def test_cobertura_cheia_nao_e_penalizada_pelo_encolhimento():
    """O encolhimento não pode mexer em quem tem todas as métricas."""
    scored = score_cross_section(_universe()).set_index("Ticker")
    assert scored.loc["BOA3", "coverage_solidity"] == 100
    assert scored.loc["BOA3", "score_solidity"] > scored.loc["FRA3", "score_solidity"]


def test_sem_pares_apurados_nao_vira_veredito_de_empresa_mediana():
    """Ausência de nota é "Sem classificação", não "Neutra" (achado A-103).

    O caminho de fallback devolvia score 50,0 — que é a mediana do corte, e que
    classification() rotula "Neutra". A tela então afirmava, com 0% de
    cobertura, que a empresa era mediana entre pares que nunca foram apurados.
    """
    from views.empresas_b3 import _fmt_pontuacao

    assert classification(float("nan")) == ("Sem classificação", "neutro")
    assert _fmt_pontuacao(float("nan")) == "—"
    assert _fmt_pontuacao(None) == "—"
    assert _fmt_pontuacao(72.36) == "72.4/100"
