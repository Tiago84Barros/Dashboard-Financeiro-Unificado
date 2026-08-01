"""Rota de valor da carteira B3 — distorção com gate de solvência (pura)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.b3_value_route import (
    ARMADILHA, OPORTUNIDADE, SEM_EVIDENCIA, SEM_MARGEM, ValuePolicy,
    rank_value_opportunities, route_summary,
)


def _empresa(ticker: str, **campos) -> dict:
    """Empresa saudável e cara por padrão; os testes distorcem o que interessa."""
    base = {
        "Ticker": ticker,
        "P/L": 15.0, "P/VP": 2.0, "DY": 0.03,
        "ROIC": 0.15, "Margem_Operacional": 0.18, "Margem_Liquida": 0.12,
        "Endividamento_Total": 0.8, "Liquidez_Corrente": 1.8, "P_FCO": 10.0,
    }
    base.update(campos)
    return base


def test_empresa_barata_e_solida_e_oportunidade():
    # P/L 5 × P/VP 0,8 = 4 → Graham √(22,5/4)−1 ≈ +137% de margem
    df = pd.DataFrame([_empresa("BOA3", **{"P/L": 5.0, "P/VP": 0.8, "DY": 0.09})])
    out = rank_value_opportunities(df)
    linha = out.iloc[0]
    assert linha["classificacao"] == OPORTUNIDADE
    assert linha["margem_valor"] > 0.20
    assert 0 <= linha["valor_score"] <= 100
    assert "solvência preservada" in linha["explicacao"]


def test_barata_com_fco_negativo_e_armadilha_nao_oportunidade():
    """O caso Oi/Americanas: desconto real, operação queimando caixa."""
    df = pd.DataFrame([
        _empresa("TRAP3", **{"P/L": 4.0, "P/VP": 0.5, "P_FCO": -3.0})])
    out = rank_value_opportunities(df)
    linha = out.iloc[0]
    assert linha["classificacao"] == ARMADILHA
    assert "FCO negativo" in "; ".join(linha["falhas_solvencia"])
    # armadilha nunca recebe score — não pode ser ordenada como candidata
    assert pd.isna(linha["valor_score"])


@pytest.mark.parametrize("campos,esperado", [
    # Margem operacional negativa PRECISA de prejuízo confirmando — ver
    # test_margem_negativa_com_lucro_nao_reprova logo abaixo.
    ({"Margem_Operacional": -0.05, "ROE": -0.10}, "margem operacional negativa"),
    ({"Endividamento_Total": 4.0}, "endividamento"),
    ({"Liquidez_Corrente": 0.6}, "liquidez corrente"),
    ({"ROIC": -0.02}, "ROIC negativo"),
])
def test_cada_regra_de_solvencia_reprova_isoladamente(campos, esperado):
    df = pd.DataFrame([_empresa("X3", **{"P/L": 4.0, "P/VP": 0.5, **campos})])
    linha = rank_value_opportunities(df).iloc[0]
    assert linha["classificacao"] == ARMADILHA
    assert esperado in "; ".join(linha["falhas_solvencia"])


def test_margem_negativa_com_lucro_nao_reprova():
    """Banco do Brasil e Bradesco saíam CRÍTICOS por métrica que não se aplica.

    EBIT/receita não é conceito válido para instituição financeira, e a brapi
    devolve o quociente mesmo assim. Medido em 01/08/2026: das 81 empresas com
    margem operacional negativa, 28 são do setor Financeiro — o maior grupo — e
    33 têm ROE POSITIVO.
    """
    df = pd.DataFrame([
        _empresa("BANK3", **{"P/L": 4.0, "P/VP": 0.5,
                             "Margem_Operacional": -0.01, "ROE": 0.14})])
    linha = rank_value_opportunities(df).iloc[0]
    assert "margem operacional negativa" not in "; ".join(linha["falhas_solvencia"])
    assert linha["classificacao"] != ARMADILHA


def test_margem_negativa_sem_roe_nao_confirma_nem_absolve():
    """Ausência não vira veredito: sem o segundo sinal a falha não é conclusiva."""
    df = pd.DataFrame([
        _empresa("SEMROE3", **{"P/L": 4.0, "P/VP": 0.5,
                               "Margem_Operacional": -0.05})])
    linha = rank_value_opportunities(df).iloc[0]
    assert "margem operacional negativa" not in "; ".join(linha["falhas_solvencia"])


def test_solida_e_cara_fica_sem_margem():
    df = pd.DataFrame([_empresa("CARA3")])   # P/L 15 × P/VP 2 = 30 > 22,5
    linha = rank_value_opportunities(df).iloc[0]
    assert linha["classificacao"] == SEM_MARGEM
    assert pd.isna(linha["valor_score"])


def test_dado_critico_ausente_nao_vira_aprovacao():
    """Ausência nunca é tratada como solvência — princípio do projeto."""
    df = pd.DataFrame([
        _empresa("NUL3", **{"P/L": 4.0, "P/VP": 0.5, "P_FCO": np.nan})])
    linha = rank_value_opportunities(df).iloc[0]
    assert linha["classificacao"] == SEM_EVIDENCIA
    assert "P_FCO" in linha["criticos_ausentes"]
    assert pd.isna(linha["valor_score"])


def test_roic_abaixo_da_selic_e_ressalva_nao_reprovacao():
    """Vale de ciclo é a hipótese que a rota existe para capturar."""
    df = pd.DataFrame([
        _empresa("CICL3", **{"P/L": 4.0, "P/VP": 0.5, "ROIC": 0.05})])
    linha = rank_value_opportunities(df, selic=0.11).iloc[0]
    assert linha["classificacao"] == OPORTUNIDADE
    assert any("abaixo da Selic" in r for r in linha["ressalvas"])
    assert "ressalva" in linha["explicacao"]


def test_ordena_por_score_e_score_pesa_desconto_e_solvencia():
    df = pd.DataFrame([
        # mesmo desconto, solvência diferente → a mais sólida vem primeiro
        _empresa("SOLIDA3", **{"P/L": 5.0, "P/VP": 0.8,
                               "Liquidez_Corrente": 3.0, "Endividamento_Total": 0.2}),
        _empresa("APERTADA3", **{"P/L": 5.0, "P/VP": 0.8,
                                 "Liquidez_Corrente": 1.05, "Endividamento_Total": 2.4}),
    ])
    out = rank_value_opportunities(df)
    assert list(out["Ticker"]) == ["SOLIDA3", "APERTADA3"]
    assert out.iloc[0]["valor_score"] > out.iloc[1]["valor_score"]


def test_margem_consolidada_e_media_das_fontes_nao_a_mais_generosa():
    df = pd.DataFrame([_empresa("MIX3", **{"P/L": 5.0, "P/VP": 0.8, "DY": 0.01})])
    linha = rank_value_opportunities(df).iloc[0]
    assert linha["fontes_valuation"] == 2
    esperado = (linha["margem_graham"] + linha["margem_bazin"]) / 2
    assert linha["margem_valor"] == pytest.approx(esperado)
    assert linha["margem_valor"] < linha["margem_graham"]   # não escolhe a melhor


def test_politica_customizada_muda_o_veredito():
    df = pd.DataFrame([
        _empresa("MEDIA3", **{"P/L": 9.0, "P/VP": 1.5, "DY": 0.05})])
    exigente = ValuePolicy(margem_minima=0.60)
    assert rank_value_opportunities(df, policy=exigente).iloc[0]["classificacao"] == SEM_MARGEM
    frouxa = ValuePolicy(margem_minima=0.05)
    assert rank_value_opportunities(df, policy=frouxa).iloc[0]["classificacao"] == OPORTUNIDADE


def test_resumo_do_funil_conta_todas_as_classes():
    df = pd.DataFrame([
        _empresa("BOA3", **{"P/L": 5.0, "P/VP": 0.8}),
        _empresa("TRAP3", **{"P/L": 4.0, "P/VP": 0.5, "P_FCO": -1.0}),
        _empresa("CARA3"),
        _empresa("NUL3", **{"Liquidez_Corrente": np.nan}),
    ])
    resumo = route_summary(rank_value_opportunities(df))
    assert resumo == {OPORTUNIDADE: 1, ARMADILHA: 1, SEM_MARGEM: 1, SEM_EVIDENCIA: 1}


def test_frame_vazio_nao_quebra():
    out = rank_value_opportunities(pd.DataFrame())
    assert out.empty and "classificacao" in out.columns
    assert route_summary(out)[OPORTUNIDADE] == 0


def test_bloqueadas_por_dado_listam_tese_nao_julgada():
    """Cobertura de fundamentos tem custo: mede-se quantas teses ficaram mudas."""
    from core.b3_value_route import blocked_by_missing_data

    df = pd.DataFrame([
        # barata, mas sem o dado de caixa → não julgada
        _empresa("MUDA3", **{"P/L": 4.0, "P/VP": 0.5, "P_FCO": np.nan}),
        # cara e sem dado → não interessa para a fila de ingestão
        _empresa("CARA3", **{"P_FCO": np.nan}),
        _empresa("BOA3", **{"P/L": 5.0, "P/VP": 0.8}),
    ])
    out = rank_value_opportunities(df)
    bloqueadas = blocked_by_missing_data(out)
    assert list(bloqueadas["Ticker"]) == ["MUDA3"]
    assert "P_FCO" in bloqueadas.iloc[0]["criticos_ausentes"]


def test_bloqueadas_vazio_quando_nao_ha_lacuna():
    from core.b3_value_route import blocked_by_missing_data

    df = pd.DataFrame([_empresa("BOA3", **{"P/L": 5.0, "P/VP": 0.8})])
    assert blocked_by_missing_data(rank_value_opportunities(df)).empty


def test_secao_da_rota_renderiza_funil_e_armadilhas():
    """A seção precisa exibir o funil e, sobretudo, as armadilhas barradas."""
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string("""
import pandas as pd
import views.portfolio_b3 as view

mult = pd.DataFrame([
    {'Ticker':'BOA3','P/L':5.0,'P/VP':0.8,'DY':0.09,'ROIC':0.18,
     'Margem_Operacional':0.20,'Endividamento_Total':0.5,
     'Liquidez_Corrente':2.0,'P_FCO':8.0},
    {'Ticker':'TRAP3','P/L':4.0,'P/VP':0.5,'DY':0.02,'ROIC':0.05,
     'Margem_Operacional':0.03,'Endividamento_Total':0.9,
     'Liquidez_Corrente':1.5,'P_FCO':-2.0},
])
setores = pd.DataFrame([
    {'Ticker':'BOA3','SETOR':'Teste','SEGMENTO':'Teste'},
    {'Ticker':'TRAP3','SETOR':'Teste','SEGMENTO':'Teste'},
])
view._render_rota_de_valor(mult, setores, 0.1075)
""").run(timeout=60)

    assert not app.exception
    rendered = "\n".join(item.value for item in app.markdown)
    assert "Rota de valor" in rendered
    # a armadilha (FCO negativo) precisa aparecer como barrada, não como candidata
    assert any("Armadilhas de valor barradas" in exp.label for exp in app.expander)
    captions = "\n".join(item.value for item in app.caption)
    assert "armadilha de valor" in captions


def test_tabela_exibe_margem_em_pontos_percentuais_e_setor_resolvido():
    """Dois bugs vistos em producao: margem 1,97 exibida como '2%' (era fracao
    formatada como percentual) e Setor/Segmento None (load_setores usa a coluna
    'ticker' em minusculo, e o merge procurava 'Ticker')."""
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string("""
import pandas as pd
import views.portfolio_b3 as view

mult = pd.DataFrame([
    {'Ticker':'BOA3','P/L':5.0,'P/VP':0.8,'DY':0.09,'ROIC':0.18,
     'Margem_Operacional':0.20,'Endividamento_Total':0.5,
     'Liquidez_Corrente':2.0,'P_FCO':8.0},
])
# coluna em minusculo, como o load_setores real devolve
setores = pd.DataFrame([{'ticker':'BOA3','SETOR':'Industrial','SEGMENTO':'Motores'}])
view._render_rota_de_valor(mult, setores, 0.1075)
""").run(timeout=60)

    assert not app.exception
    tabela = app.dataframe[0].value
    assert "Setor" in tabela.columns
    assert tabela.iloc[0]["Setor"] == "Industrial"      # antes vinha None
    # média de Graham (137%) e Bazin (50%) ≈ 94 — em PONTOS PERCENTUAIS.
    # Antes chegava como 0,94 e a coluna exibia "1%".
    assert tabela.iloc[0]["Margem de segurança"] == pytest.approx(93.6, abs=1.0)
