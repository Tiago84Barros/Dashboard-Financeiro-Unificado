"""Saúde das empresas selecionadas + concentração de ciclo (puro).

Casos ancorados na carteira real de 27/07/2026 que expôs a lacuna:
WEGE3 · BRAP3 · LEVE3 · UNIP6 — quatro nomes, todos pró-cíclicos, um com
payout de 318% e endividamento de 3,2×.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.b3_holdings_health import (
    ATENCAO, CRITICO, OK, check_holdings, check_portfolio,
)


def _empresa(ticker: str, **campos) -> dict:
    base = {
        "Ticker": ticker, "P/L": 8.0, "P/VP": 1.2, "DY": 0.05, "Payout": 0.50,
        "ROIC": 0.18, "Margem_Operacional": 0.15, "Margem_Liquida": 0.10,
        "Endividamento_Total": 0.8, "Liquidez_Corrente": 1.8, "P_FCO": 8.0,
    }
    base.update(campos)
    return base


# ── por empresa ──────────────────────────────────────────────────────────────

def test_empresa_saudavel_nao_gera_alerta():
    df = pd.DataFrame([_empresa("BOA3")])
    h = check_holdings(df, ["BOA3"], selic=0.11)[0]
    assert h.nivel == OK and not h.alertas
    assert h.bloqueante is False


def test_payout_acima_do_lucro_e_critico():
    """UNIP6 real: payout de 318% com DY de 17%."""
    df = pd.DataFrame([_empresa("UNIP6", Payout=3.18, DY=0.17)])
    h = check_holdings(df, ["UNIP6"], selic=0.11)[0]
    assert h.nivel == CRITICO
    assert any("payout" in a.lower() for a in h.alertas)
    assert h.bloqueante is True


def test_falha_estrutural_isolada_e_atencao_nao_critico():
    """Calibração contra caso real (28/07/2026): PETR4 tinha liquidez corrente
    0,74 e virava crítica, apesar de margem operacional de 29%, ROIC de 14% e
    FCO forte. No universo, 54 das 86 empresas com liquidez < 1 geram caixa —
    condenar por sinal estrutural isolado é alarme falso em 63% dos casos."""
    df = pd.DataFrame([_empresa("ALAV3", Endividamento_Total=3.24,
                                **{"P/L": 4.0, "P/VP": 0.5})])
    h = check_holdings(df, ["ALAV3"], selic=0.11)[0]
    assert h.nivel == ATENCAO and h.bloqueante is False
    assert any("sem confirmação por falha operacional" in a for a in h.alertas)
    # a divergência entre as rotas continua sendo reportada
    assert h.classificacao_valor == "armadilha_potencial"
    assert any("ARMADILHA POTENCIAL" in a for a in h.alertas)


def test_estrutural_confirmada_por_operacional_volta_a_ser_critico():
    df = pd.DataFrame([_empresa("GRAVE3", Endividamento_Total=3.24,
                                Margem_Operacional=-0.05)])
    h = check_holdings(df, ["GRAVE3"], selic=0.11)[0]
    assert h.nivel == CRITICO and h.bloqueante is True


def test_duas_falhas_estruturais_juntas_sao_criticas():
    df = pd.DataFrame([_empresa("DUPLA3", Endividamento_Total=3.24,
                                Liquidez_Corrente=0.6)])
    h = check_holdings(df, ["DUPLA3"], selic=0.11)[0]
    assert h.nivel == CRITICO


def test_geradora_de_caixa_com_liquidez_baixa_nao_e_condenada():
    """PETR4 real: liquidez 0,74, margem operacional 29%, ROIC 14%, FCO 2,85."""
    df = pd.DataFrame([_empresa("PETR4", Liquidez_Corrente=0.74,
                                Margem_Operacional=0.289, ROIC=0.142,
                                P_FCO=2.85, Endividamento_Total=1.34,
                                Payout=0.31)])
    h = check_holdings(df, ["PETR4"], selic=0.11)[0]
    assert h.nivel == ATENCAO and h.bloqueante is False


def test_holding_nao_recebe_alerta_de_payout_apertado():
    """ITSA4 real: payout de 82% numa holding é repasse, não aperto de caixa."""
    df = pd.DataFrame([_empresa("ITSA4", Payout=0.82, Margem_Operacional=np.nan,
                                P_FCO=np.nan)])
    h = check_holdings(df, ["ITSA4"], selic=0.11)[0]
    assert not any("Dividendo" in a for a in h.alertas)


def test_operadora_com_payout_apertado_continua_alertando():
    df = pd.DataFrame([_empresa("OPER3", Payout=0.85)])
    h = check_holdings(df, ["OPER3"], selic=0.11)[0]
    assert any("Dividendo" in a for a in h.alertas)


def test_roic_abaixo_da_selic_e_atencao_nao_veto():
    """Vale de ciclo não pode ser vetado — é a tese da rota de valor."""
    df = pd.DataFrame([_empresa("CICL3", ROIC=0.085)])
    h = check_holdings(df, ["CICL3"], selic=0.11)[0]
    assert h.nivel == ATENCAO
    assert h.bloqueante is False
    assert any("abaixo da Selic" in a for a in h.alertas)


def test_holding_sem_margem_operacional_vira_atencao_por_falta_de_evidencia():
    """BRAP3 real: é holding, não tem operação própria.

    Payout de 120% numa holding é REPASSE do dividendo da controlada — atenção,
    não veto. Confundi-lo com o payout de 318% de uma operadora alavancada
    seria crying wolf (calibração feita contra a carteira real de 27/07/2026).
    """
    df = pd.DataFrame([_empresa("BRAP3", Margem_Operacional=np.nan,
                                Payout=1.19, ROIC=0.094)])
    h = check_holdings(df, ["BRAP3"], selic=0.11)[0]
    assert h.classificacao_valor == "sem_evidencia"
    assert h.nivel == ATENCAO and h.bloqueante is False
    assert any("não pôde julgar" in a for a in h.alertas)
    assert any("repasse de holding" in a for a in h.alertas)


def test_fco_negativo_e_critico():
    df = pd.DataFrame([_empresa("QUEIMA3", P_FCO=-2.0)])
    h = check_holdings(df, ["QUEIMA3"], selic=0.11)[0]
    assert h.nivel == CRITICO
    assert any("FCO negativo" in a for a in h.alertas)


def test_ticker_ausente_do_cross_section_nao_quebra():
    df = pd.DataFrame([_empresa("BOA3")])
    assert [h.ticker for h in check_holdings(df, ["INEXISTENTE3"])] == ["INEXISTENTE3"]


def test_entradas_vazias_nao_quebram():
    assert check_holdings(pd.DataFrame(), ["X3"]) == []
    assert check_holdings(pd.DataFrame([_empresa("A3")]), []) == []


# ── carteira ─────────────────────────────────────────────────────────────────

def test_carteira_toda_ciclica_e_sinalizada():
    """A carteira real: 4 setores nominais, 1 fator só."""
    df = pd.DataFrame([_empresa(t) for t in
                       ("WEGE3", "BRAP3", "LEVE3", "UNIP6")])
    setores = {"WEGE3": "Bens Industriais", "BRAP3": "Materiais Básicos",
               "LEVE3": "Bens Industriais", "UNIP6": "Materiais Básicos"}
    saude = check_portfolio(df, list(setores), setores, selic=0.11)
    assert saude.pct_ciclico == 1.0
    assert saude.pct_defensivo == 0.0
    assert any("Fator único" in a for a in saude.alertas)
    assert any("contrapeso defensivo" in a for a in saude.alertas)
    assert any("abaixo do mínimo prudente" in a for a in saude.alertas)


def test_carteira_equilibrada_nao_gera_alerta_de_fator():
    tickers = ["A3", "B3", "C3", "D3", "E3"]
    df = pd.DataFrame([_empresa(t) for t in tickers])
    setores = {"A3": "Bens Industriais", "B3": "Utilidade Pública",
               "C3": "Saúde", "D3": "Financeiro", "E3": "Consumo não Cíclico"}
    saude = check_portfolio(df, tickers, setores, selic=0.11)
    assert saude.pct_ciclico < 0.75
    assert not any("Fator único" in a for a in saude.alertas)
    assert not any("contrapeso defensivo" in a for a in saude.alertas)


def test_carteira_agrega_criticos_e_atencao():
    df = pd.DataFrame([
        _empresa("TRAP3", Payout=3.0),          # crítico
        _empresa("CICL3", ROIC=0.05),           # atenção
        _empresa("BOA3"),                        # ok
    ])
    setores = {"TRAP3": "Saúde", "CICL3": "Utilidade Pública", "BOA3": "Financeiro"}
    saude = check_portfolio(df, list(setores), setores, selic=0.11)
    assert [h.ticker for h in saude.criticos] == ["TRAP3"]
    assert [h.ticker for h in saude.atencao] == ["CICL3"]


def test_setor_desconhecido_nao_penaliza_como_ciclico():
    df = pd.DataFrame([_empresa("X3"), _empresa("Y3")])
    saude = check_portfolio(df, ["X3", "Y3"], {"X3": "Vago", "Y3": None},
                            selic=0.11)
    assert saude.pct_ciclico == 0.0


# ── integração com a interface ───────────────────────────────────────────────

def test_secao_alerta_na_tela_onde_a_decisao_e_tomada():
    """O alerta precisa aparecer junto dos líderes, não numa aba separada."""
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string("""
import pandas as pd
import views.portfolio_b3 as view

mult = pd.DataFrame([
    # armadilha: endividamento alto + payout muito acima do lucro
    {'Ticker':'UNIP6','P/L':18.7,'P/VP':3.66,'DY':0.17,'Payout':3.18,
     'ROIC':0.085,'Margem_Operacional':0.124,'Endividamento_Total':3.24,
     'Liquidez_Corrente':2.09,'P_FCO':5.5},
    {'Ticker':'LEVE3','P/L':6.4,'P/VP':3.97,'DY':0.09,'Payout':0.58,
     'ROIC':0.44,'Margem_Operacional':0.17,'Endividamento_Total':1.46,
     'Liquidez_Corrente':1.37,'P_FCO':5.7},
])
setores = pd.DataFrame([
    {'ticker':'UNIP6','SETOR':'Materiais Básicos'},
    {'ticker':'LEVE3','SETOR':'Consumo Cíclico'},
])
view._render_saude_da_carteira(['UNIP6','LEVE3'], mult, setores, 0.11)
""").run(timeout=60)

    assert not app.exception
    rendered = "\n".join(item.value for item in app.markdown)
    assert "Saúde das empresas selecionadas" in rendered
    erros = "\n".join(item.value for item in app.error)
    assert "UNIP6" in erros                      # crítico vira erro visível
    avisos = "\n".join(item.value for item in app.warning)
    assert "cíclicos" in avisos or "Fator único" in avisos


def test_secao_nao_renderiza_sem_selecionadas():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string("""
import pandas as pd
import views.portfolio_b3 as view
view._render_saude_da_carteira([], pd.DataFrame(), pd.DataFrame(), 0.11)
""").run(timeout=60)
    assert not app.exception
    assert not any("Saúde das empresas" in i.value for i in app.markdown)


def test_aviso_do_custo_de_resiliencia_aparece_quando_o_filtro_corta():
    """Descoberta de 29/07/2026 rodando o motor sem navegador: com o filtro
    desligado a carteira saiu com 10 nomes e 23% defensivos; ligado a 5 p.p.,
    caiu para 6 e 83% cíclicos. O custo precisa ser exibido, não deduzido."""
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string("""
import numpy as np, pandas as pd, streamlit as st
import views.portfolio_b3 as view

# Reproduz o trecho do diagnóstico com dois segmentos reprovados pelo filtro
resultados = [
    {"setor": "Utilidade Pública", "segmento": "Energia", "roic_spread_mean": 0.01,
     "roic_hit_rate": 0.4},
    {"setor": "Materiais Básicos", "segmento": "Mineração", "roic_spread_mean": 0.02,
     "roic_hit_rate": 0.3},
]
from core.b3_holdings_health import classify_cycle
thr = 0.05
cortados = [r for r in resultados
            if (not np.isfinite(r["roic_spread_mean"])) or r["roic_spread_mean"] < thr]
por_classe = {}
for r in cortados:
    c = classify_cycle(r["setor"]); por_classe[c] = por_classe.get(c, 0) + 1
st.info(f"Custo do filtro de resiliência: {len(cortados)} segmento(s) — "
        f"{por_classe.get('defensivo',0)} defensivo(s) e "
        f"{por_classe.get('ciclico',0)} cíclico(s).")
""").run(timeout=60)

    assert not app.exception
    texto = "\n".join(item.value for item in app.info)
    assert "Custo do filtro de resiliência" in texto
    assert "1 defensivo(s)" in texto and "1 cíclico(s)" in texto
