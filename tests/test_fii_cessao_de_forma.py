"""Cessao minima das regras de forma: a carteira nunca sai zerada por forma.

Regra de forma e teto de concentracao, cobertura de dimensao e teto por
ativo -- limites que dizem como a carteira se distribui. Portao de risco
(liquidez, historico, drawdown, faixa de P/VP, teto de plausibilidade do DY)
filtra o universo ANTES do otimizador e nunca cede: se nenhum FII passa por
ele, a saida honesta e dizer que nao ha ativo investivel na data.
"""

from core.fii_methodology import MacroScenario
from core.fii_portfolio_v4 import PortfolioPolicy, optimize_diligence_portfolio
from tests.test_fii_portfolio_v4 import _candidate

CENARIO = MacroScenario(selic=12, ipca=5)


def _universo_concentrado_em_uma_regiao(n: int = 12) -> list[dict]:
    """Tijolo e papel em quantidade, com todo o tijolo no Sudeste.

    E o formato real do mercado brasileiro: ``region`` so se aplica a tijolo e
    hibrido, e esses estao concentrados. Exigir no maximo 35% numa unica
    regiao e aritmeticamente inalcancavel aqui, e nao protege ninguem -- as
    demais dimensoes seguem diversificadas.
    """
    linhas = [_candidate(i, "tijolo" if i % 2 == 0 else "papel")
              for i in range(n)]
    for linha in linhas:
        if linha["tipo"] == "tijolo":
            linha["regions"] = {"Sudeste": 1.0}
    return linhas


def test_cessao_afrouxa_regiao_e_preserva_os_limites_que_nao_prendiam():
    resultado = optimize_diligence_portfolio(
        _universo_concentrado_em_uma_regiao(), CENARIO,
        policy=PortfolioPolicy(max_assets=12),
    )
    cessao = resultado["shape_cession"]

    assert resultado["items"], "carteira nao pode sair zerada por regra de forma"
    assert "concentracao por region" in cessao["grupos"]
    # O re-aperto grupo a grupo devolve ao valor original todo limite que nao
    # era necessario. Sem ele a nota publicada acusaria risco que o investidor
    # nao chegou a correr.
    assert "cobertura de dimensao" not in cessao["grupos"]
    assert cessao["limites_efetivos"]["max_region"] > PortfolioPolicy().max_region
    assert 0.0 < cessao["grau"] <= 1.0


def test_universo_sem_ativo_investivel_e_declarado_e_nao_inventado():
    resultado = optimize_diligence_portfolio(
        [], CENARIO, policy=PortfolioPolicy(max_assets=12))

    assert resultado["items"] == []
    assert resultado["shape_cession"]["nivel"] == "sem universo investivel"
    assert resultado["can_publish"] is False


def test_universo_folgado_nao_cede_nada():
    tipos = ["tijolo", "papel", "fof", "hibrido"] * 3
    resultado = optimize_diligence_portfolio(
        [_candidate(i, tipo) for i, tipo in enumerate(tipos)], CENARIO,
        policy=PortfolioPolicy(max_assets=12),
    )

    assert resultado["items"]
    assert "shape_cession" not in resultado


def test_aperto_de_forma_nunca_reduz_a_carteira():
    """A busca pelo menor grau nao pode comprar selo com concentracao.

    O otimizador é substituido por um que devolve carteira em qualquer grau,
    mas com menos ativos quando a forma aperta. Sem a guarda, a busca fica
    com a carteira de 2 ativos porque ela cede menos; com ela, fica com a de
    quatro.
    """
    from core.fii_portfolio_v4 import PortfolioPolicy, _cessao_minima_de_forma

    def tentar(politica):
        apertado = float(politica.max_asset) < 0.9
        pesos = [0.5, 0.5] if apertado else [0.25] * 4
        return {"items": [{"ticker": f"X{i}11", "weight": w}
                          for i, w in enumerate(pesos)]}

    cessao, melhor = _cessao_minima_de_forma(
        PortfolioPolicy(max_asset=0.15), tentar)
    assert len(melhor["items"]) == 4
    assert max(i["weight"] for i in melhor["items"]) == 0.25
    assert cessao["teto por ativo"] > 0.0
