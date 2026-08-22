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
