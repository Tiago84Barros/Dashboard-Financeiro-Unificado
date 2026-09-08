"""Classificador de papel estrategico por ativo."""
import numpy as np
import pandas as pd
import pytest

from core.global_portfolio.roles import (
    LIMIARES,
    PAPEIS,
    ROTULOS_PAPEL,
    classificar,
)


def _linha(symbol, classe="b3", peso=0.1, currency="BRL", sector="financials",
           fundamentals=None, history=None, classification=None):
    return {
        "asset_class": classe, "symbol": symbol, "name": symbol,
        "sector": sector, "currency": currency, "weight_global": peso,
        "payload": {
            "fundamentals": fundamentals or {},
            "history": history or {},
            "classification": classification or {},
        },
    }


def test_todo_papel_tem_rotulo():
    assert set(ROTULOS_PAPEL) == set(PAPEIS)


def test_papeis_sao_deterministicos():
    assert PAPEIS == tuple(sorted(PAPEIS))


def test_renda_exige_dy_alto_e_payout_estavel():
    payout_estavel = {"multiplos_anuais": [{"Payout": 40.0} for _ in range(5)]}
    df = pd.DataFrame([
        _linha("ALTO", fundamentals={"DY": 9.0}, history=payout_estavel),
        _linha("BAIXO", fundamentals={"DY": 1.0}, history=payout_estavel),
    ])
    saida = {p.symbol: p for p in classificar(df)}
    assert "renda" in saida["ALTO"].papeis
    assert "renda" not in saida["BAIXO"].papeis


def test_renda_recusa_payout_erratico():
    erratico = {"multiplos_anuais": [{"Payout": v} for v in (5.0, 90.0, 10.0, 80.0, 15.0)]}
    estavel = {"multiplos_anuais": [{"Payout": 40.0} for _ in range(5)]}
    df = pd.DataFrame([
        _linha("ERRATICO", fundamentals={"DY": 9.0}, history=erratico),
        _linha("ESTAVEL", fundamentals={"DY": 9.0}, history=estavel),
    ])
    saida = {p.symbol: p for p in classificar(df)}
    assert "renda" not in saida["ERRATICO"].papeis
    assert "renda" in saida["ESTAVEL"].papeis


def test_crescimento_usa_cagr_de_lpa():
    crescendo = {"demonstracoes_anuais": [{"LPA": v} for v in (1.0, 1.3, 1.7, 2.2, 2.9)]}
    parado = {"demonstracoes_anuais": [{"LPA": 1.0} for _ in range(5)]}
    df = pd.DataFrame([
        _linha("CRESCE", history=crescendo),
        _linha("PARADO", history=parado),
    ])
    saida = {p.symbol: p for p in classificar(df)}
    assert "crescimento" in saida["CRESCE"].papeis
    assert "crescimento" not in saida["PARADO"].papeis


def test_hedge_cambial_vem_da_moeda():
    df = pd.DataFrame([
        _linha("AAPL", classe="us", currency="USD"),
        _linha("PETR4", currency="BRL"),
    ])
    saida = {p.symbol: p for p in classificar(df)}
    assert "hedge_cambial" in saida["AAPL"].papeis
    assert "hedge_cambial" not in saida["PETR4"].papeis


def test_protecao_inflacao_por_fii_de_papel_ou_por_setor():
    df = pd.DataFrame([
        _linha("KNCR11", classe="fii", sector="real_estate",
               classification={"composition": {"pct_papel": 94.0, "pct_imoveis": 0.0}}),
        _linha("SBSP3", sector="utilities"),
        _linha("LEVE3", sector="consumer"),
    ])
    saida = {p.symbol: p for p in classificar(df)}
    assert "protecao_inflacao" in saida["KNCR11"].papeis
    assert "protecao_inflacao" in saida["SBSP3"].papeis
    assert "protecao_inflacao" not in saida["LEVE3"].papeis


def test_reserva_valor_e_fii_de_tijolo():
    df = pd.DataFrame([
        _linha("HGLG11", classe="fii", sector="real_estate",
               classification={"composition": {"pct_imoveis": 96.0, "pct_papel": 0.0}}),
        _linha("KNCR11", classe="fii", sector="real_estate",
               classification={"composition": {"pct_imoveis": 0.0, "pct_papel": 94.0}}),
    ])
    saida = {p.symbol: p for p in classificar(df)}
    assert "reserva_valor" in saida["HGLG11"].papeis
    assert "reserva_valor" not in saida["KNCR11"].papeis


def test_baixa_volatilidade_e_diversificacao_vem_da_serie():
    n = 60
    idx = pd.date_range("2020-01-31", periods=n, freq="ME")
    quieto = np.full(n, 0.001)
    agitado = np.tile([0.20, -0.18], n // 2)
    ret = pd.DataFrame({"QUIETO": quieto, "AGITADO": agitado}, index=idx)
    df = pd.DataFrame([_linha("QUIETO"), _linha("AGITADO")])

    saida = {p.symbol: p for p in classificar(
        df, retornos=ret, correlacoes={"QUIETO": 0.05, "AGITADO": 0.85})}
    assert "baixa_volatilidade" in saida["QUIETO"].papeis
    assert "baixa_volatilidade" not in saida["AGITADO"].papeis
    assert "diversificacao" in saida["QUIETO"].papeis
    assert "diversificacao" not in saida["AGITADO"].papeis


def test_sem_serie_o_papel_fica_indeterminado_e_nao_negado():
    df = pd.DataFrame([_linha("SEMSERIE")])
    p = classificar(df, retornos=None, correlacoes=None)[0]
    assert "baixa_volatilidade" in p.indeterminados
    assert "diversificacao" in p.indeterminados
    assert "baixa_volatilidade" not in p.papeis


def test_toda_evidencia_acompanha_o_papel_que_a_gerou():
    payout = {"multiplos_anuais": [{"Payout": 40.0} for _ in range(5)]}
    df = pd.DataFrame([_linha("X", fundamentals={"DY": 9.0}, history=payout)])
    p = classificar(df)[0]
    papeis_com_evidencia = {e.papel for e in p.evidencias}
    assert set(p.papeis) <= papeis_com_evidencia, "papel sem numero e rotulo, nao classificacao"
    for e in p.evidencias:
        assert e.texto, "evidencia precisa de texto legivel"


def test_ativo_sem_papel_algum_e_declarado_explicitamente():
    df = pd.DataFrame([_linha("NADA", fundamentals={"DY": 0.5})])
    p = classificar(df)[0]
    assert p.papeis == ()
    assert "nenhum papel" in p.justificativa.lower()


def test_ordem_da_saida_segue_o_quadro():
    df = pd.DataFrame([_linha("B"), _linha("A"), _linha("C")])
    assert [p.symbol for p in classificar(df)] == ["B", "A", "C"]


def test_quadro_vazio_devolve_lista_vazia():
    vazio = pd.DataFrame(columns=["asset_class", "symbol", "name", "sector",
                                  "currency", "weight_global", "payload"])
    assert classificar(vazio) == []


def test_limiares_estao_num_so_lugar():
    for chave in ("payout_instavel", "cagr_minimo", "vol_baixa",
                  "correlacao_baixa", "papel_dominante", "tijolo_dominante"):
        assert chave in LIMIARES


def _fii(symbol, dy_mensal, vpa, peso=0.1):
    """Linha de FII com serie mensal sintetica."""
    meses = [{"Data": f"2024-{m:02d}-01", "DY_Patrimonial": d, "VPA": v}
             for m, (d, v) in enumerate(zip(dy_mensal, vpa), start=1)]
    return {
        "asset_class": "fii", "symbol": symbol, "name": symbol,
        "sector": "real_estate", "currency": "BRL", "weight_global": peso,
        "payload": {
            "fundamentals": {"dy_12m": sum(dy_mensal) / len(dy_mensal) * 12 * 100},
            "history": {"metricas_mensais": meses, "proventos_anuais": []},
            "classification": {"composition": {"pct_imoveis": 0.96, "pct_papel": 0.0}},
        },
    }


def test_renda_de_fii_usa_o_dy_mensal_e_exige_estabilidade():
    import pandas as pd

    from core.global_portfolio.roles import classificar

    estavel = [0.008] * 24
    # Mesma media que "estavel" (0.008): o teste isola a estabilidade como
    # unico diferenciador. Com [0.001, 0.020] a media do erratico (0.0105)
    # supera a do estavel, e a mediana da classe (so 2 FIIs neste teste) cai
    # entre as duas — ESTAVEL nunca alcancaria "acima da mediana" nem com
    # qualquer implementacao correta da regra. [0.001, 0.015] mantem o
    # mesmo espirito de oscilacao extrema sem essa colisao aritmetica.
    erratico = [0.001, 0.015] * 12
    vpa = [100.0] * 24
    df = pd.DataFrame([_fii("ESTAVEL", estavel, vpa), _fii("ERRATICO", erratico, vpa)])
    saida = {p.symbol: p for p in classificar(df)}

    assert "renda" in saida["ESTAVEL"].papeis
    assert "renda" not in saida["ERRATICO"].papeis
    assert "renda" not in saida["ERRATICO"].indeterminados, "avaliado e reprovado, nao indeterminado"


def test_fii_com_serie_curta_deixa_renda_indeterminada():
    import pandas as pd

    from core.global_portfolio.roles import classificar

    df = pd.DataFrame([_fii("CURTO", [0.008] * 6, [100.0] * 6)])
    p = classificar(df)[0]
    assert "renda" in p.indeterminados
    assert "renda" not in p.papeis


def test_crescimento_de_fii_usa_cagr_do_vpa_com_a_janela_declarada():
    import pandas as pd

    from core.global_portfolio.roles import classificar

    subindo = [100.0 * (1.02 ** i) for i in range(24)]
    parado = [100.0] * 24
    df = pd.DataFrame([_fii("SOBE", [0.008] * 24, subindo),
                       _fii("PARADO", [0.008] * 24, parado)])
    saida = {p.symbol: p for p in classificar(df)}

    assert "crescimento" in saida["SOBE"].papeis
    assert "crescimento" not in saida["PARADO"].papeis

    ev = [e for e in saida["SOBE"].evidencias if e.papel == "crescimento"][0]
    assert "mes" in ev.texto.lower(), "a janela real precisa aparecer na evidencia"


def test_limiar_de_meses_minimos_existe():
    from core.global_portfolio.roles import LIMIARES
    assert "meses_minimos_fii" in LIMIARES


def test_renda_de_fii_formata_dy_em_pontos_percentuais_no_texto():
    """dy e fracao por contrato de fields.valor (verificado em producao: DY de
    FII varia 0,0993 a 0,1817). O texto da evidencia precisa exibir pontos
    percentuais; Evidencia.valor/referencia continuam a fracao crua."""
    dy_mensal = [0.0125] * 12
    linha = {
        "asset_class": "fii", "symbol": "REIT11", "name": "REIT11",
        "sector": "real_estate", "currency": "BRL", "weight_global": 0.1,
        "payload": {
            "fundamentals": {"dy_12m": 0.1497},
            "history": {"metricas_mensais": [
                {"Data": f"2024-{m:02d}-01", "DY_Patrimonial": d}
                for m, d in enumerate(dy_mensal, start=1)
            ]},
            "classification": {},
        },
    }
    df = pd.DataFrame([linha])
    p = classificar(df)[0]
    ev = next(e for e in p.evidencias if e.papel == "renda")

    assert ev.valor == pytest.approx(0.1497), "Evidencia.valor continua fracao crua"
    assert "14." in ev.texto or "15." in ev.texto, "texto precisa mostrar ~14,97%, nao 0,15%"
    assert "0.15%" not in ev.texto and "0,15%" not in ev.texto


# --- Classe us: crescimento pela receita e renda com a causa nomeada ---------


def _us(symbol, fundamentals=None, history=None):
    """Ativo americano como ele chega na nuvem.

    `history` vazio nao e simplificacao de fixture: o adaptador preenche
    history.financials_anuais a partir de market_us.income_statements, que so
    existe no armazem local. Em producao os 30 ativos chegam assim.
    """
    return _linha(symbol, classe="us", currency="USD",
                  fundamentals=fundamentals, history=history)


def test_crescimento_de_us_vem_da_receita_e_nao_do_lpa_ausente():
    df = pd.DataFrame([_us("ADBE", fundamentals={"revenue_trend_5y": 0.1241,
                                                 "revenue_trend_r2_5y": 0.978})])

    p = classificar(df)[0]

    assert "crescimento" in p.papeis
    assert "crescimento" not in p.indeterminados
    ev = next(e for e in p.evidencias if e.papel == "crescimento")
    assert "receita" in ev.texto.lower(), "a base medida precisa aparecer na tela"
    assert "12.41%" in ev.texto


def test_crescimento_de_us_usa_regressao_e_nao_o_cagr_de_ponta_a_ponta():
    """O CAGR continua publicado em us_metrics, mas nao decide mais o papel.

    Se a regra ainda lesse `revenue_cagr_5y`, este ativo — que tem CAGR alto
    e nenhuma inclinacao — seria promovido a crescimento.
    """
    df = pd.DataFrame([_us("XYZ", fundamentals={"revenue_cagr_5y": 0.42})])

    assert "crescimento" in classificar(df)[0].indeterminados


def test_crescimento_de_us_publica_o_r2_junto_da_taxa():
    """Taxa sem aderencia e reta ajustada a ruido; quem le precisa ver as duas."""
    df = pd.DataFrame([_us("ITRI", fundamentals={"revenue_trend_5y": 0.12,
                                                 "revenue_trend_r2_5y": 0.05})])

    ev = next(e for e in classificar(df)[0].evidencias if e.papel == "crescimento")

    assert "0.05" in ev.texto and "R2" in ev.texto


def test_crescimento_de_us_sem_r2_diz_que_a_aderencia_e_desconhecida():
    df = pd.DataFrame([_us("ABC", fundamentals={"revenue_trend_5y": 0.12})])

    ev = next(e for e in classificar(df)[0].evidencias if e.papel == "crescimento")

    assert "desconhecida" in ev.texto


def test_crescimento_de_us_abaixo_do_limiar_e_negado_nao_indeterminado():
    baixo = LIMIARES["cagr_minimo"] / 2
    df = pd.DataFrame([_us("FFIV", fundamentals={"revenue_trend_5y": baixo})])

    p = classificar(df)[0]

    assert "crescimento" not in p.papeis
    assert "crescimento" not in p.indeterminados,         "avaliado e negado nao e o mesmo que sem dado"


def test_crescimento_de_us_sem_o_campo_continua_indeterminado():
    df = pd.DataFrame([_us("XYZ", fundamentals={"pe": 20.0})])

    assert "crescimento" in classificar(df)[0].indeterminados


def test_crescimento_de_us_ignora_eps_growth_como_substituto():
    """eps_growth_3y nao e a mesma medida e nao pode valer contra o limiar."""
    df = pd.DataFrame([_us("GNTX", fundamentals={"eps_growth_3y": 0.99,
                                                 "shareholder_yield": 0.05})])

    assert "crescimento" in classificar(df)[0].indeterminados


def test_renda_de_us_e_avaliada_pelo_dividend_yield():
    """Era indeterminada em 30 de 30 ativos ate o DY passar a ser derivado."""
    df = pd.DataFrame([
        _us("MDT", fundamentals={"dividend_yield": 0.031, "payout_ratio": 0.6}),
        _us("GNTX", fundamentals={"dividend_yield": 0.021, "payout_ratio": 0.3}),
        _us("ADBE", fundamentals={"dividend_yield": 0.0}),
    ])

    ps = {p.symbol: p for p in classificar(df)}

    assert "renda" in ps["MDT"].papeis
    assert "renda" not in ps["ADBE"].papeis
    assert "renda" not in ps["ADBE"].indeterminados,         "zero publicado pelo EDGAR e valor observado, nao lacuna"
    ev = next(e for e in ps["MDT"].evidencias if e.papel == "renda")
    assert "defasa" in ev.texto, "a defasagem de ate um ano precisa ir para a tela"


def test_renda_de_us_sem_dividend_yield_continua_indeterminada():
    df = pd.DataFrame([_us("EW", fundamentals={"payout_ratio": 0.2,
                                               "shareholder_yield": 0.03})])

    p = classificar(df)[0]

    assert "renda" in p.indeterminados
    assert "renda" not in p.papeis


def test_renda_de_us_e_vetada_por_payout_acima_do_lucro():
    """DY alto sustentado por payout de 180% nao e tese de renda."""
    df = pd.DataFrame([
        _us("A", fundamentals={"dividend_yield": 0.09, "payout_ratio": 1.8}),
        _us("B", fundamentals={"dividend_yield": 0.01, "payout_ratio": 0.4}),
    ])

    ps = {p.symbol: p for p in classificar(df)}

    assert "renda" not in ps["A"].papeis
    assert "renda" not in ps["A"].indeterminados, "vetado e negado, nao ignorado"


def test_renda_de_us_sem_payout_decide_pelo_dy_e_declara_o_que_nao_viu():
    """Exigir payout presente puniria quem tem menos dado, nao pior fundamento."""
    df = pd.DataFrame([
        _us("A", fundamentals={"dividend_yield": 0.05}),
        _us("B", fundamentals={"dividend_yield": 0.01}),
    ])

    p = {x.symbol: x for x in classificar(df)}["A"]

    assert "renda" in p.papeis
    ev = next(e for e in p.evidencias if e.papel == "renda")
    assert "nao foi verificada" in ev.texto


def test_motivo_so_existe_para_indeterminado():
    """Motivo colado num papel cumprido seria contradicao na mesma linha."""
    df = pd.DataFrame([
        _us("ADBE", fundamentals={"revenue_trend_5y": 0.13}),
        _linha("ITSA4", classe="b3", fundamentals={"DY": 0.09}),
    ])

    for p in classificar(df):
        for papel, _motivo in p.motivos_indeterminado:
            assert papel in p.indeterminados
            assert papel not in p.papeis


def test_papeis_de_b3_e_fii_nao_ganham_motivo_de_us():
    df = pd.DataFrame([_linha("ITSA4", classe="b3", fundamentals={"DY": 0.09})])

    assert classificar(df)[0].motivos_indeterminado == ()
