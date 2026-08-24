"""
O maior desvio possivel era o unico que a politica nao enxergava.

`ThresholdRebalance` e `HybridRebalance` decidiam se e dia de mexer varrendo
`pesos_meta.items()`. Uma posicao que o alvo mandou ZERAR nao aparece no
dicionario de metas -- o ticker saiu da selecao, entao a chave sumiu -- e
portanto nunca era examinada. Uma carteira com 30% de um ativo que o modelo
quer fora reportava `max_desvio = 0,0` e "nao precisa rebalancear".

A assimetria era unidirecional e por isso passava despercebida: uma ENTRADA
nova (ticker em `pesos_meta` ausente de `pesos_atuais`) era detectada, porque
`pesos_atuais.get(tk, 0.0)` devolve zero. A SAIDA, que e a mesma conta ao
contrario, nao tinha o `.get` do outro lado.

Em producao o caminho e `CalendarRebalance` (`views/portfolio_global.py`), que
nao olha peso nenhum, e dentro de `core.global_portfolio.advisor` a projecao
devolve todos os simbolos com peso zero em vez de omiti-los -- entao o defeito
estava latente, nao vivo. Continua valendo travar: as duas classes sao API
publica do modulo, documentadas no cabecalho como politicas disponiveis.
"""
from datetime import date

from core.rebalancing import HybridRebalance, ThresholdRebalance

ONTEM = date(2026, 1, 1)
HOJE = date(2026, 2, 1)


def test_posicao_que_o_alvo_mandou_zerar_dispara_rebalanceamento():
    politica = ThresholdRebalance(banda_abs=0.05, rebal_inicial=False)
    # AAA saiu da selecao: a chave nem existe no alvo. BBB fica onde esta,
    # entao a saida de AAA e o UNICO desvio -- pela regra antiga, o laco
    # varria so `metas`, media desvio 0,0 e devolvia "nao precisa mexer".
    atuais = {"AAA": 0.30, "BBB": 0.70}
    metas = {"BBB": 0.70}

    deve, motivo = politica.deve_rebalancear(atuais, metas, HOJE, ONTEM)
    assert deve, "sair inteiro de 30% e o maior desvio possivel, nao zero"
    assert "AAA" in motivo


def test_hibrida_tambem_enxerga_a_saida_antes_do_prazo_do_calendario():
    politica = HybridRebalance(intervalo_dias_max=365, banda_abs=0.05)
    deve, motivo = politica.deve_rebalancear(
        {"AAA": 0.25, "BBB": 0.75}, {"BBB": 0.75}, HOJE, ONTEM
    )
    assert deve and "AAA" in motivo


def test_entrada_nova_continua_detectada():
    # A direcao que ja funcionava nao pode regredir.
    politica = ThresholdRebalance(banda_abs=0.05, rebal_inicial=False)
    deve, motivo = politica.deve_rebalancear(
        {"AAA": 1.00}, {"AAA": 0.60, "CCC": 0.30, "DDD": 0.10}, HOJE, ONTEM
    )
    assert deve and motivo


def test_carteira_alinhada_continua_sem_disparar():
    politica = ThresholdRebalance(banda_abs=0.05, rebal_inicial=False)
    pesos = {"AAA": 0.50, "BBB": 0.50}
    assert not politica.deve_rebalancear(pesos, dict(pesos), HOJE, ONTEM)[0]


def test_desvio_e_deterministico_qualquer_que_seja_a_ordem_dos_dicionarios():
    politica = ThresholdRebalance(banda_abs=0.05, rebal_inicial=False)
    a = {"AAA": 0.20, "BBB": 0.30, "CCC": 0.50}
    b = {"CCC": 0.50, "AAA": 0.20, "BBB": 0.30}
    metas = {"BBB": 0.40, "CCC": 0.60}
    assert (politica.deve_rebalancear(a, metas, HOJE, ONTEM)
            == politica.deve_rebalancear(b, metas, HOJE, ONTEM))
