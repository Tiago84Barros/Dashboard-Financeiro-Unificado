"""Regras da Análise do Portfólio por classe.

Os testes exercitam funções puras. A tentação era abrir a aba com AppTest e
conferir o texto na tela, mas neste projeto AppTest já vazou atribuição de
módulo entre testes — passa isolado e falha só no CI dentro da suíte. A regra
mora na função; é ela que é verificada aqui.
"""
import math

import pytest

from core.llm_context_carteira import build_carteira_classe_context
from core.portfolio_valuations import (
    METRICS,
    METRICS_BY_CLASS,
    _agregavel,
    aggregate_tesouro,
    aggregate_valuations,
    metric_spec,
    para_percentual,
)


def _pos(ticker, valor, investido=None):
    return {"ticker": ticker, "valor_mercado": valor,
            "total_investido": investido if investido is not None else valor}


# ── Validade por indicador ────────────────────────────────────────────────

@pytest.mark.parametrize("metric,valor", [
    ("pl", -5.0), ("pl", 0.0), ("pvp", 0.0), ("ev_ebitda", -1.2),
    ("dy", -0.1), ("vacancia_media", -3.0),
])
def test_valor_invalido_fica_de_fora(metric, valor):
    """Múltiplo não positivo é indefinido, não barato — não pode virar média."""
    assert _agregavel(metric, valor) is None


@pytest.mark.parametrize("metric,valor", [
    ("roe", -18.0), ("marg_liq", -4.5), ("cresc_rec_5a", -12.0),
    ("net_margin", -2.0), ("fcf_yield", -1.0), ("dy", 0.0),
])
def test_negativo_legitimo_entra(metric, valor):
    """Rejeitar ROE negativo enviesaria a média para cima onde o dado é ruim."""
    assert _agregavel(metric, valor) == valor


def test_metrica_desconhecida_cai_na_regra_conservadora():
    spec = metric_spec("indicador_que_nao_existe")
    assert spec.floor == 0.0 and spec.strict is True


def test_nan_e_infinito_nao_entram():
    assert _agregavel("roe", float("nan")) is None
    assert _agregavel("roe", float("inf")) is None
    assert _agregavel("pl", "texto") is None


# ── Conversão de unidade do universo americano ────────────────────────────

def test_fracao_americana_vira_ponto_percentual():
    assert para_percentual("net_margin", 0.12) == pytest.approx(12.0)
    assert para_percentual("roe", 0.08) == pytest.approx(8.0)


def test_multiplo_nao_e_convertido():
    """P/L 12 é 12 vezes; multiplicar por 100 publicaria 1200."""
    assert para_percentual("pe", 12.0) == pytest.approx(12.0)
    assert para_percentual("ev_ebitda", 7.5) == pytest.approx(7.5)


# ── Agregação por classe ──────────────────────────────────────────────────

def test_metricas_da_classe_nao_se_misturam():
    """Vacância é de FII e ROE é de companhia; a mesma média não comporta os dois."""
    assert "vacancia_media" not in METRICS_BY_CLASS["acoes"]
    assert "roe" not in METRICS_BY_CLASS["fiis"]
    # A fonte dos EUA não entrega dividend yield; anunciá-lo criaria card vazio.
    assert "dy" not in METRICS_BY_CLASS["exterior"]


def test_cobertura_e_medida_na_classe_recebida():
    posicoes = [_pos("AAA", 700.0), _pos("BBB", 300.0)]
    fund = {"AAA": {"pl": 10.0}, "BBB": {}}
    resultado = aggregate_valuations(posicoes, fund, ("pl",))
    assert resultado["pl"]["value"] == pytest.approx(10.0)
    assert resultado["pl"]["coverage"] == pytest.approx(0.7)
    assert resultado["pl"]["assets"] == 1


def test_media_e_ponderada_pelo_valor_de_mercado():
    posicoes = [_pos("AAA", 900.0), _pos("BBB", 100.0)]
    fund = {"AAA": {"pvp": 1.0}, "BBB": {"pvp": 11.0}}
    resultado = aggregate_valuations(posicoes, fund, ("pvp",))
    assert resultado["pvp"]["value"] == pytest.approx(2.0)


def test_lotes_do_mesmo_ticker_somam_peso_e_contam_um_ativo():
    posicoes = [_pos("AAA", 500.0), _pos("AAA", 500.0), _pos("BBB", 1000.0)]
    fund = {"AAA": {"pl": 10.0}, "BBB": {"pl": 20.0}}
    resultado = aggregate_valuations(posicoes, fund, ("pl",))
    assert resultado["pl"]["value"] == pytest.approx(15.0)
    assert resultado["pl"]["assets"] == 2


def test_sem_dado_algum_devolve_none_e_nao_zero():
    """None e 0 dizem coisas opostas: 'não apurado' contra 'apurado e nulo'."""
    resultado = aggregate_valuations([_pos("AAA", 100.0)], {}, ("pl",))
    assert resultado["pl"]["value"] is None
    assert resultado["pl"]["coverage"] == 0.0


def test_assinatura_antiga_continua_valendo():
    """A aba Carteira chama sem `metrics`; o padrão não pode ter mudado."""
    resultado = aggregate_valuations([_pos("AAA", 100.0)], {"AAA": {"dy": 8.0}})
    assert set(resultado) == set(METRICS)


def test_media_ignora_infinito_sem_contaminar_cobertura():
    posicoes = [_pos("AAA", 500.0), _pos("BBB", 500.0)]
    fund = {"AAA": {"pl": float("inf")}, "BBB": {"pl": 10.0}}
    resultado = aggregate_valuations(posicoes, fund, ("pl",))
    assert math.isfinite(resultado["pl"]["value"])
    assert resultado["pl"]["coverage"] == pytest.approx(0.5)


# ── Tesouro Direto ────────────────────────────────────────────────────────

def test_tesouro_agrega_prazo_indexador_e_retorno():
    posicoes = [
        _pos("Tesouro IPCA+ 2029", 6000.0, 5000.0),
        _pos("Tesouro Selic 2027", 4000.0, 4000.0),
    ]
    resumo = aggregate_tesouro(posicoes, 2026)
    assert resumo["titulos"] == 2
    assert resumo["valor_mercado"] == pytest.approx(10000.0)
    assert resumo["resultado"] == pytest.approx(1000.0)
    assert resumo["retorno_pct"] == pytest.approx(1000 / 9000 * 100)
    # 3 anos com peso 6000 e 1 ano com peso 4000.
    assert resumo["prazo_medio_anos"] == pytest.approx((6000 * 3 + 4000 * 1) / 10000)
    assert resumo["cobertura_prazo"] == pytest.approx(1.0)
    assert sum(resumo["por_indexador"].values()) == pytest.approx(1.0)


def test_tesouro_sem_ano_nao_entra_no_prazo_medio():
    """Título sem vencimento legível encurtaria o prazo se contasse como zero."""
    posicoes = [_pos("Tesouro IPCA+ 2029", 5000.0),
                _pos("Tesouro Educa+", 5000.0)]
    resumo = aggregate_tesouro(posicoes, 2026)
    assert resumo["cobertura_prazo"] < 1.0
    assert resumo["valor_sem_ano"] > 0.0
    assert resumo["prazo_medio_anos"] == pytest.approx(3.0)


def test_tesouro_vazio_nao_explode():
    resumo = aggregate_tesouro([], 2026)
    assert resumo["titulos"] == 0 and resumo["retorno_pct"] is None


# ── Contexto enviado à LLM ────────────────────────────────────────────────

def _contexto_acoes():
    posicoes = [_pos("BBAS3", 7000.0), _pos("PSSA3", 3000.0)]
    valuations = aggregate_valuations(
        posicoes, {"BBAS3": {"pl": 5.0, "dy": 9.0}, "PSSA3": {"pl": 8.0}},
        METRICS_BY_CLASS["acoes"])
    db = {"referencia": "universo B3 · 300 empresas",
          "linhas": [{"ticker": "BBAS3", "score": 72.0, "classificacao": "Bom",
                      "percentil": 0.88, "cobertura": 91.0}],
          "ausentes": ["PSSA3"]}
    return build_carteira_classe_context(
        "acoes", posicoes, valuations=valuations, db=db)


def test_contexto_nao_leva_valor_em_reais():
    """Saldo é dado pessoal e não melhora a resposta; só o peso sai daqui."""
    texto = _contexto_acoes()
    for proibido in ("7000", "3000", "10000", "R$"):
        assert proibido not in texto


def test_contexto_traz_peso_percentual():
    texto = _contexto_acoes()
    assert "BBAS3: 70.0%" in texto and "PSSA3: 30.0%" in texto


def test_contexto_declara_cobertura_parcial():
    texto = _contexto_acoes()
    assert "cobertura 70.0%" in texto  # o DY cobre só o BBAS3


def test_contexto_declara_ausencia_como_ausencia():
    texto = _contexto_acoes()
    assert "Sem nota apurada no universo: PSSA3" in texto
    assert "NÃO é nota mediana" in texto


def test_contexto_de_tesouro_usa_conjuntura_e_nao_multiplo():
    posicoes = [_pos("Tesouro Selic 2029", 1000.0)]
    texto = build_carteira_classe_context(
        "tesouro", posicoes, tesouro=aggregate_tesouro(posicoes, 2026),
        macro={"atual": {"ano": 2026, "selic": 12.0, "ipca": 4.0,
                         "juros_real_ex_ante": 7.5}})
    assert "POSIÇÃO EM TESOURO DIRETO" in texto
    assert "Juro real ex-ante" in texto
    assert "P/L" not in texto


def test_contexto_com_banco_indisponivel_diz_que_esta_indisponivel():
    texto = build_carteira_classe_context(
        "fiis", [_pos("MXRF11", 100.0)],
        db={"erro": "Universo indisponível nesta sessão."})
    assert "Nenhuma nota comparativa" in texto


def test_contexto_de_classe_vazia_nao_quebra():
    texto = build_carteira_classe_context("exterior", [])
    assert "nenhuma posição" in texto


def test_valuations_ausentes_nao_viram_zero_no_texto():
    texto = build_carteira_classe_context(
        "acoes", [_pos("AAA", 100.0)],
        valuations=aggregate_valuations([_pos("AAA", 100.0)], {}, ("pl",)))
    assert "P/L: ausente" in texto
    assert "P/L: 0.00" not in texto


# ── Degradação do confronto com o banco ───────────────────────────────────

def test_analise_do_banco_sem_ticker_devolve_estrutura_vazia():
    from core.portfolio_db_analysis import (
        analise_acoes_db,
        analise_exterior_db,
        analise_fiis_db,
    )
    for fn in (analise_acoes_db, analise_fiis_db, analise_exterior_db):
        saida = fn([])
        assert saida["linhas"] == [] and saida["ausentes"] == []
        assert saida["erro"] is None


def test_analise_do_banco_nunca_propaga_excecao(monkeypatch):
    """A aba tem que continuar de pé com o banco fora; o erro vira texto."""
    import core.b3_data as _b3_data
    import core.portfolio_db_analysis as mod

    def _explode(*_a, **_k):
        raise RuntimeError("postgres://usuario:senha@host/base")

    # O módulo importa `core.b3_data` dentro da função; o alvo do patch é o
    # módulo de origem, não um atributo que nunca existiu aqui.
    monkeypatch.setattr(_b3_data, "load_multiplos_todos", _explode, raising=False)
    saida = mod.analise_acoes_db(["BBAS3"])
    assert saida["erro"]
    # E nada da string de conexão pode vazar para a interface.
    assert "senha" not in saida["erro"] and "postgres" not in saida["erro"]
