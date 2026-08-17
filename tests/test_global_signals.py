"""Sinais normalizados do motor de movimentacao, com procedencia."""
import numpy as np
import pandas as pd
import pytest

from core.data_confidence import score_ticker
from core.global_portfolio.correlation import LIMIAR_REDUNDANCIA, pares_redundantes
from core.global_portfolio.risk import contribuicao_marginal
from core.global_portfolio.roles import PAPEIS, classificar
from core.global_portfolio.signals import (
    LIMITE_ATIVO_DEFAULT,
    LIMITE_PAIS_DEFAULT,
    LIMITE_SETOR_DEFAULT,
    Sinal,
    gerar_sinais,
    sinais_concentracao,
    sinais_desvio_alvo,
    sinais_papel_ausente,
    sinais_qualidade,
    sinais_redundancia,
    sinais_risco,
    sinais_valuation,
)
from core.portfolio.repository import _normalizar_alvos

ANALISADORES_VALIDOS = {"concentration", "correlation", "factors", "risk", "roles", "metrics", "targets"}


def _linha(symbol, classe="b3", peso=0.1, sector="financials", country="BR",
          currency="BRL", fundamentals=None, history=None, metrics=None):
    return {
        "asset_class": classe, "symbol": symbol, "sector": sector, "country": country,
        "currency": currency, "weight_global": peso,
        "payload": {
            "fundamentals": fundamentals or {},
            "history": history or {},
            "metrics": metrics or {},
        },
    }


# ---------------------------------------------------------------------------
# Convencao geral: analisador sempre no vocabulario, direcao sempre = f(valor)
# ---------------------------------------------------------------------------

def _checar_convencao(sinais: list[Sinal]):
    for s in sinais:
        assert -1.0 <= s.valor <= 1.0
        assert s.analisador in ANALISADORES_VALIDOS
        if s.valor > 0:
            assert s.direcao == "aumentar"
        elif s.valor < 0:
            assert s.direcao == "reduzir"
        else:
            assert s.direcao == "neutro"


# ---------------------------------------------------------------------------
# 1. valuation
# ---------------------------------------------------------------------------

def test_valuation_ativo_mais_caro_na_classe_fica_negativo():
    df = pd.DataFrame([
        _linha("BARATO", fundamentals={"P/L": 5.0}),
        _linha("MEIO", fundamentals={"P/L": 15.0}),
        _linha("CARO", fundamentals={"P/L": 50.0}),
    ])
    saida = {s.symbol: s for s in sinais_valuation(df)}
    _checar_convencao(list(saida.values()))
    assert saida["CARO"].valor < 0
    assert saida["CARO"].direcao == "reduzir"
    assert saida["BARATO"].valor > 0
    assert saida["BARATO"].valor > saida["MEIO"].valor > saida["CARO"].valor
    assert saida["CARO"].analisador == "metrics"


def test_valuation_campo_por_classe_pe_para_b3_e_us_pvp_para_fii():
    df = pd.DataFrame([
        _linha("B3A", classe="b3", fundamentals={"P/L": 10.0}),
        _linha("B3B", classe="b3", fundamentals={"P/L": 30.0}),
        _linha("USA", classe="us", fundamentals={"pe": 12.0}),
        _linha("USB", classe="us", fundamentals={"pe": 40.0}),
        _linha("FIIA", classe="fii", fundamentals={"pvp": 0.9}),
        _linha("FIIB", classe="fii", fundamentals={"pvp": 1.3}),
    ])
    saida = {s.symbol: s for s in sinais_valuation(df)}
    assert set(saida) == {"B3A", "B3B", "USA", "USB", "FIIA", "FIIB"}
    # dentro de cada classe, o mais caro fica pior que o mais barato
    assert saida["B3A"].valor > saida["B3B"].valor
    assert saida["USA"].valor > saida["USB"].valor
    assert saida["FIIA"].valor > saida["FIIB"].valor


def test_valuation_pl_nao_positivo_fica_fora():
    df = pd.DataFrame([
        _linha("PREJUIZO", fundamentals={"P/L": -8.0}),
        _linha("OK", fundamentals={"P/L": 10.0}),
    ])
    saida = {s.symbol: s for s in sinais_valuation(df)}
    assert "PREJUIZO" not in saida
    assert "OK" not in saida  # classe com 1 so valor: sem percentil e sem historico -> sem sinal


def test_valuation_sem_campo_nao_gera_sinal():
    df = pd.DataFrame([_linha("SEMDADO", fundamentals={})])
    assert sinais_valuation(df) == []


def test_valuation_usa_percentil_contra_o_proprio_historico():
    # Um so ativo na classe: sem percentil-na-classe (min 2). Mas com historico
    # de P/L longo o bastante, o componente historico sozinho basta para gerar sinal.
    historico = {"multiplos_anuais": [{"P/L": v} for v in (8.0, 9.0, 10.0, 11.0, 40.0)]}
    df = pd.DataFrame([_linha("SOLO", fundamentals={"P/L": 40.0}, history=historico)])
    saida = sinais_valuation(df)
    assert len(saida) == 1
    # 40.0 e o maior de todo o proprio historico -> percentil 100% -> valor bem negativo
    assert saida[0].valor < 0
    assert "proprio historico" in saida[0].texto


def test_valuation_classe_us_nao_usa_historico_por_falta_de_serie():
    # us nao tem bloco de multiplo historico no snapshot; mesmo com "history"
    # presente sob outra chave, o campo esperado (_HISTORICO_VALUATION) nao mapeia "us".
    df = pd.DataFrame([
        _linha("USUNICO", classe="us", fundamentals={"pe": 20.0},
              history={"financials_anuais": [{"pe": v} for v in range(10)]}),
    ])
    assert sinais_valuation(df) == []  # 1 ativo so na classe, sem historico mapeado -> sem sinal


# ---------------------------------------------------------------------------
# 2. qualidade + data_confidence
# ---------------------------------------------------------------------------

def test_qualidade_percentil_alto_e_positivo():
    df = pd.DataFrame([
        _linha("RUIM", metrics={"score": 20.0}),
        _linha("BOM", metrics={"score": 90.0}),
    ])
    saida = {s.symbol: s for s in sinais_qualidade(df)}
    _checar_convencao(list(saida.values()))
    assert saida["BOM"].valor > 0
    assert saida["RUIM"].valor < 0
    assert saida["BOM"].analisador == "metrics"


def test_qualidade_sem_score_nao_gera_sinal():
    df = pd.DataFrame([_linha("SEMSCORE", metrics={})])
    assert sinais_qualidade(df) == []


def test_qualidade_confianca_baixa_encolhe_o_sinal_com_confianca_real():
    # confianca vem de core.data_confidence.score_ticker (funcao real, pura):
    # um ticker com poucas metricas TTM, demonstracao velha e flags abertas
    # tira um score baixo de verdade, nao um numero inventado no teste.
    df = pd.DataFrame([
        _linha("A", metrics={"score": 90.0}),
        _linha("B", metrics={"score": 10.0}),
    ])
    sinal_score_alto = score_ticker(
        {"n_key_ttm": 9, "ymax": 2026, "dias_preco": 1, "n_flags": 0}, current_year=2026)
    sinal_score_baixo = score_ticker(
        {"n_key_ttm": 0, "ymax": 2018, "dias_preco": 200, "n_flags": 5}, current_year=2026)
    assert sinal_score_baixo["score"] < 30  # confirma a premissa antes de checar a consequencia
    assert sinal_score_alto["score"] > 90

    sem_confianca = {s.symbol: s for s in sinais_qualidade(df)}
    com_confianca = {s.symbol: s for s in sinais_qualidade(
        df, confianca={"A": sinal_score_alto["score"], "B": sinal_score_baixo["score"]})}

    # A com confianca alta: praticamente inalterado.
    assert com_confianca["A"].valor == pytest.approx(sem_confianca["A"].valor, rel=0.15)
    # B com confianca baixa: sinal (negativo) encolhe em magnitude.
    assert abs(com_confianca["B"].valor) < abs(sem_confianca["B"].valor)


def test_qualidade_sem_confianca_informada_usa_peso_pleno():
    df = pd.DataFrame([
        _linha("A", metrics={"score": 90.0}),
        _linha("B", metrics={"score": 10.0}),
    ])
    sem_confianca = {s.symbol: s for s in sinais_qualidade(df)}
    com_confianca_vazia = {s.symbol: s for s in sinais_qualidade(df, confianca={})}
    assert sem_confianca["A"].valor == pytest.approx(com_confianca_vazia["A"].valor)


# ---------------------------------------------------------------------------
# 3. risco vs peso (real: risk.contribuicao_marginal)
# ---------------------------------------------------------------------------

def _retornos_multifator(n=90, seed=21):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2018-01-31", periods=n, freq="ME")
    fator = rng.normal(0, 0.04, n)
    a = 0.8 * fator + rng.normal(0, 0.02, n)
    b = 0.5 * fator + rng.normal(0, 0.03, n)
    c = 0.2 * fator + rng.normal(0, 0.05, n)
    d = -0.3 * fator + rng.normal(0, 0.025, n)
    return pd.DataFrame({"A": a, "B": b, "C": c, "D": d}, index=idx)


def test_risco_usa_contribuicao_marginal_real_e_razao_alta_fica_negativa():
    ret = _retornos_multifator()
    pesos = {"A": 0.4, "B": 0.3, "C": 0.2, "D": 0.1}
    contribuicoes = contribuicao_marginal(ret, pesos)  # chamada real, nao fake
    assert set(contribuicoes) == {"A", "B", "C", "D"}

    df = pd.DataFrame([_linha(s, peso=pesos[s]) for s in pesos])
    saida = {s.symbol: s for s in sinais_risco(contribuicoes, df)}
    _checar_convencao(list(saida.values()))
    assert set(saida) == {"A", "B", "C", "D"}
    for symbol, c in contribuicoes.items():
        if c.razao > 1.0:
            assert saida[symbol].valor < 0
        elif c.razao < 1.0:
            assert saida[symbol].valor > 0
        assert saida[symbol].analisador == "risk"


def test_risco_peso_zero_sem_razao_nao_gera_sinal():
    ret = _retornos_multifator()[["A", "B", "C"]]
    pesos = {"A": 0.5, "B": 0.5, "C": 0.0}
    contribuicoes = contribuicao_marginal(ret, pesos)
    assert contribuicoes["C"].razao is None

    df = pd.DataFrame([_linha(s, peso=pesos[s]) for s in pesos])
    saida = {s.symbol: s for s in sinais_risco(contribuicoes, df)}
    assert "C" not in saida


def test_risco_sem_contribuicoes_devolve_vazio():
    df = pd.DataFrame([_linha("A")])
    assert sinais_risco({}, df) == []


# ---------------------------------------------------------------------------
# 4. redundancia (real: correlation.pares_redundantes)
# ---------------------------------------------------------------------------

def _retornos_com_correlacao(rho, n=60, cols=("A", "B")):
    rng = np.random.default_rng(11)
    base = rng.normal(0, 0.05, n)
    ruido = rng.normal(0, 0.05, n)
    b = rho * base + np.sqrt(max(0.0, 1 - rho ** 2)) * ruido
    idx = pd.date_range("2021-01-31", periods=n, freq="ME")
    return pd.DataFrame({cols[0]: base, cols[1]: b}, index=idx)


def test_redundancia_usa_pares_redundantes_real_e_pune_o_pior_score():
    ret = _retornos_com_correlacao(0.95)
    pares = pares_redundantes(ret)  # chamada real
    assert len(pares) == 1
    a, b, corr = pares[0]
    assert corr > LIMIAR_REDUNDANCIA

    df = pd.DataFrame([
        _linha("A", metrics={"score": 90.0}, peso=0.2),
        _linha("B", metrics={"score": 30.0}, peso=0.2),
    ])
    saida = {s.symbol: s for s in sinais_redundancia(pares, df)}
    _checar_convencao(list(saida.values()))
    assert set(saida) == {"B"}  # B tem pior score: fica com o sinal de reduzir
    assert saida["B"].valor < 0
    assert saida["B"].analisador == "correlation"


def test_redundancia_sem_score_desempata_por_peso_menor():
    ret = _retornos_com_correlacao(0.95)
    pares = pares_redundantes(ret)
    df = pd.DataFrame([
        _linha("A", peso=0.30),
        _linha("B", peso=0.05),
    ])
    saida = {s.symbol: s for s in sinais_redundancia(pares, df)}
    assert set(saida) == {"B"}


def test_redundancia_intensidade_maxima_no_limite_superior():
    df = pd.DataFrame([_linha("A", peso=0.1), _linha("B", peso=0.2)])
    saida = {s.symbol: s for s in sinais_redundancia([("A", "B", 1.0)], df)}
    assert saida["A"].valor == pytest.approx(-1.0)


def test_redundancia_sem_pares_devolve_vazio():
    df = pd.DataFrame([_linha("A")])
    assert sinais_redundancia([], df) == []


# ---------------------------------------------------------------------------
# 5. estouro de limite (concentracao)
# ---------------------------------------------------------------------------

def test_concentracao_ativo_acima_do_limite_gera_sinal_negativo():
    df = pd.DataFrame([
        _linha("GRANDE", peso=0.25, sector="tech", country="BR"),
        _linha("PEQUENO", peso=0.02, sector="tech", country="BR"),
        _linha("OUTRO", peso=0.02, sector="saude", country="US"),
    ])
    saida = {s.symbol: s for s in sinais_concentracao(df, limite_ativo=0.10,
                                                        limite_setor=0.90, limite_pais=0.90)}
    _checar_convencao(list(saida.values()))
    assert set(saida) == {"GRANDE"}
    assert saida["GRANDE"].valor < 0
    assert saida["GRANDE"].analisador == "concentration"


def test_concentracao_setor_estourado_atinge_todos_do_setor():
    df = pd.DataFrame([
        _linha("A", peso=0.20, sector="tech"),
        _linha("B", peso=0.20, sector="tech"),
        _linha("C", peso=0.05, sector="saude"),
    ])
    saida = {s.symbol: s for s in sinais_concentracao(df, limite_ativo=0.90,
                                                        limite_setor=0.30, limite_pais=0.90)}
    assert set(saida) == {"A", "B"}
    assert "C" not in saida


def test_concentracao_pais_estourado():
    df = pd.DataFrame([
        _linha("A", peso=0.40, country="US", sector="s1"),
        _linha("B", peso=0.40, country="US", sector="s2"),
        _linha("C", peso=0.20, country="BR", sector="s3"),
    ])
    saida = {s.symbol: s for s in sinais_concentracao(df, limite_ativo=0.90,
                                                        limite_setor=0.90, limite_pais=0.50)}
    assert set(saida) == {"A", "B"}


def test_concentracao_dentro_do_limite_nao_gera_sinal():
    df = pd.DataFrame([_linha("A", peso=0.05), _linha("B", peso=0.05)])
    assert sinais_concentracao(df) == []


def test_concentracao_usa_limites_default_do_modulo():
    assert LIMITE_ATIVO_DEFAULT == pytest.approx(0.10)
    assert LIMITE_SETOR_DEFAULT == pytest.approx(0.30)
    assert LIMITE_PAIS_DEFAULT == pytest.approx(0.60)


# ---------------------------------------------------------------------------
# 6. papel ausente (real: roles.classificar)
# ---------------------------------------------------------------------------

def test_papel_ausente_usa_classificar_real():
    # Ativo sem DY, sem historico de LPA, moeda BRL, setor comum: nenhum papel
    # se cumpre, mas hedge_cambial/protecao_inflacao/reserva_valor SAO avaliados
    # (e negados) -- nao ficam indeterminados -- entao ha evidencia real de ausencia.
    df = pd.DataFrame([{
        "asset_class": "b3", "symbol": "SEMPAPEL", "sector": "financials",
        "currency": "BRL", "weight_global": 0.1,
        "payload": {"fundamentals": {}, "history": {}, "classification": {}},
    }])
    papeis = classificar(df)  # chamada real, sem fake
    assert papeis[0].papeis == ()
    assert len(papeis[0].indeterminados) < len(PAPEIS)

    saida = sinais_papel_ausente(papeis)
    assert len(saida) == 1
    assert saida[0].symbol == "SEMPAPEL"
    assert saida[0].valor == pytest.approx(-1.0)
    assert saida[0].direcao == "reduzir"
    assert saida[0].analisador == "roles"


def test_papel_ausente_com_papel_nao_gera_sinal():
    payout_estavel = {"multiplos_anuais": [{"Payout": 40.0} for _ in range(5)]}
    df = pd.DataFrame([{
        "asset_class": "b3", "symbol": "COMPAPEL", "sector": "financials",
        "currency": "BRL", "weight_global": 0.1,
        "payload": {"fundamentals": {"DY": 9.0}, "history": payout_estavel, "classification": {}},
    }])
    papeis = classificar(df)
    assert papeis[0].papeis  # tem ao menos um papel
    assert sinais_papel_ausente(papeis) == []


def test_papel_ausente_totalmente_indeterminado_nao_gera_sinal():
    # roles.classificar sempre avalia hedge_cambial/protecao_inflacao/reserva_valor
    # de forma determinada (dependem so de moeda/setor/classe, nunca de serie),
    # entao "todos os 7 indeterminados" nunca sai de uma chamada real -- e
    # exatamente por isso que este teste constroi o PapelDoAtivo direto, para
    # exercitar o guarda de "sem evidencia nenhuma" que classificar() em si
    # nao consegue produzir.
    from core.global_portfolio.roles import PapelDoAtivo
    papeis = [PapelDoAtivo(symbol="SEMDADO", papeis=(), evidencias=(),
                           indeterminados=tuple(PAPEIS), justificativa="")]
    assert sinais_papel_ausente(papeis) == []


def test_papel_ausente_sem_papeis_devolve_vazio():
    assert sinais_papel_ausente([]) == []


# ---------------------------------------------------------------------------
# 7. desvio do alvo da classe
# ---------------------------------------------------------------------------

def test_desvio_alvo_classe_sobreponderada_empurra_para_reduzir():
    alvos = _normalizar_alvos({"b3": 0.5, "fii": 0.2, "us": 0.3})  # normalizacao real
    df = pd.DataFrame([
        _linha("B3A", classe="b3", peso=0.35),
        _linha("B3B", classe="b3", peso=0.35),  # b3 = 70% contra alvo de 50%
        _linha("FIIA", classe="fii", peso=0.10),  # fii = 10% contra alvo de 20%
    ])
    saida = {s.symbol: s for s in sinais_desvio_alvo(df, alvos)}
    _checar_convencao(list(saida.values()))
    assert saida["B3A"].valor < 0
    assert saida["B3B"].valor < 0
    assert saida["FIIA"].valor > 0
    assert saida["B3A"].analisador == "targets"


def test_desvio_alvo_classe_sem_alvo_nao_gera_sinal():
    alvos = _normalizar_alvos({"b3": 1.0})
    df = pd.DataFrame([_linha("FIIA", classe="fii", peso=0.1)])
    assert sinais_desvio_alvo(df, alvos) == []


def test_desvio_alvo_sem_alvos_devolve_vazio():
    df = pd.DataFrame([_linha("A")])
    assert sinais_desvio_alvo(df, {}) == []


# ---------------------------------------------------------------------------
# Orquestrador
# ---------------------------------------------------------------------------

def test_gerar_sinais_so_com_quadro_ja_produz_valuation_qualidade_e_concentracao():
    df = pd.DataFrame([
        _linha("A", peso=0.5, fundamentals={"P/L": 10.0}, metrics={"score": 80.0}),
        _linha("B", peso=0.05, fundamentals={"P/L": 20.0}, metrics={"score": 40.0}),
    ])
    saida = gerar_sinais(df, limite_ativo=0.10)
    _checar_convencao(saida)
    nomes = {s.nome for s in saida}
    assert {"valuation", "qualidade", "estouro_limite"} <= nomes


def test_gerar_sinais_ordenado_por_nome_e_symbol():
    df = pd.DataFrame([
        _linha("Z", peso=0.5, fundamentals={"P/L": 10.0}, metrics={"score": 80.0}),
        _linha("A", peso=0.5, fundamentals={"P/L": 20.0}, metrics={"score": 40.0}),
    ])
    saida = gerar_sinais(df)
    chaves = [(s.nome, s.symbol) for s in saida]
    assert chaves == sorted(chaves)


def test_gerar_sinais_combina_todos_os_analisadores_informados():
    ret = _retornos_multifator()[["A", "B"]]
    pesos = {"A": 0.5, "B": 0.5}
    contribuicoes = contribuicao_marginal(ret, pesos)
    pares = pares_redundantes(_retornos_com_correlacao(0.95))

    df = pd.DataFrame([
        _linha("A", peso=0.5, fundamentals={"P/L": 10.0}, metrics={"score": 80.0}),
        _linha("B", peso=0.5, fundamentals={"P/L": 20.0}, metrics={"score": 40.0}),
    ])
    papeis = classificar(df)
    alvos = _normalizar_alvos({"b3": 1.0})

    saida = gerar_sinais(df, contribuicoes=contribuicoes, pares_redundantes=pares,
                         papeis=papeis, alvos=alvos)
    _checar_convencao(saida)
    analisadores = {s.analisador for s in saida}
    # com todos os insumos presentes, os seis analisadores que os sete sinais
    # do Task 2 de fato consultam aparecem. "factors" fica de fora de proposito:
    # nenhum dos sete sinais especificados na spec e produzido por factors.py
    # (ver docstring do modulo) -- rotula-lo aqui seria procedencia falsa.
    assert analisadores == {"risk", "correlation", "roles", "targets", "metrics", "concentration"}
