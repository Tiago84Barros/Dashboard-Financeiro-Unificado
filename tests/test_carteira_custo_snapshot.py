"""De onde sai o CUSTO de uma posicao quando ha duas fontes discordando.

A corretora publica o preco medio dela na "Posicao Detalhada" -- um numero
calculado sobre o historico INTEIRO, inclusive o que e anterior a qualquer
arquivo que este app tenha importado. O app tambem agrega, em
`portfolio_positions`, as notas de negociacao que ele conseguiu ler.

Ate 2026-09-22 a segunda fonte ganhava da primeira, e o resultado era um
preco medio inventado sobre a quantidade de hoje. Em producao isso publicava
BBAS3 com custo de R$ 15.374,93 (PM R$ 10,40, tirado de 1.086 cotas de
historico parcial) contra os R$ 35.377,68 que o extrato da B3 declara (PM
R$ 23,92 sobre as 1.479 cotas em carteira) -- menos da metade do custo real
na maior posicao da carteira, e um lucro fantasma no lugar do prejuizo.

O argumento nao e "a corretora e mais confiavel" no abstrato: e que
quantidade, valor de mercado e custo da MESMA linha do snapshot sao
consistentes entre si. Misturar a quantidade de uma fonte com o preco medio
de outra produz um custo que nenhuma das duas afirma.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from core.investimentos import _montar_carteira_snapshot


def _row(ticker, *, qty, vm, invested=None, pp_qty=0, pp_ti=0, pp_avg=0,
         b3_avg=0, b3_qty=0):
    return SimpleNamespace(
        ticker=ticker,
        quantity=qty,
        market_value=vm,
        invested_value=invested,
        asset_type="stock",
        asset_name=ticker,
        sector="other",
        currency="BRL",
        country="BR",
        report_date=date(2026, 9, 21),
        live_price=None,
        live_timestamp=None,
        usd_brl_rate=None,
        pp_quantity=pp_qty,
        pp_total_invested=pp_ti,
        pp_average_price=pp_avg,
        b3_avg_price=b3_avg,
        b3_quantity=b3_qty,
        b3_report_date=date(2026, 9, 21),
    )


def _pos(rows, ticker):
    carteira = _montar_carteira_snapshot(rows)
    return next(p for p in carteira["posicoes"] if p["ticker"] == ticker)


def test_custo_do_extrato_ganha_do_historico_parcial():
    """O caso BBAS3 de 21/09/2026, com os numeros de producao."""
    pos = _pos([_row("BBAS3", qty=1479, vm=34017.00, invested=35377.68,
                     pp_qty=1086, pp_ti=11294.64, pp_avg=10.4003,
                     b3_avg=23.9200, b3_qty=1479)], "BBAS3")

    assert round(pos["total_investido"], 2) == 35377.68
    assert round(pos["preco_medio"], 2) == 23.92
    assert pos["custo_fonte"] == "b3_posicao_detalhada"
    # Numero declarado pela corretora nao e estimativa: o card nao pode
    # rotular como "Custo estimado" o unico custo autoritativo que existe.
    assert pos["custo_estimado"] is False
    # -3,85% no extrato; o historico parcial publicava lucro de +121%.
    assert pos["rentab_pct"] < 0


def test_historico_parcial_continua_valendo_quando_o_extrato_nao_traz_custo():
    """SBSP3: a B3 publica PM zero, e o importador grava isso como ausencia.

    Sem custo do extrato, o agregado das notas e o melhor que existe -- e
    segue marcado como estimativa, porque o PM de 100 cotas foi esticado
    para as 145 em carteira.
    """
    pos = _pos([_row("SBSP3", qty=145, vm=4051.30, invested=None,
                     pp_qty=100, pp_ti=5324.78, pp_avg=53.2478)], "SBSP3")

    assert round(pos["preco_medio"], 2) == 53.25
    assert pos["custo_fonte"] == "preco_medio_estimado"
    assert pos["custo_estimado"] is True


def test_custo_zero_no_snapshot_nao_e_custo():
    """Zero e o jeito de dizer "nao sei", nao "custou nada".

    Se o zero passasse por custo valido, a posicao apareceria com 100% de
    lucro -- e ninguem desconfia de um numero que a corretora "declarou".
    """
    pos = _pos([_row("XPTO3", qty=10, vm=1000.0, invested=0.0,
                     pp_qty=10, pp_ti=800.0, pp_avg=80.0)], "XPTO3")

    assert pos["total_investido"] == 800.0
    assert pos["custo_fonte"] == "b3_negociacao"


def test_preco_medio_da_b3_ganha_do_consolidado_da_rico():
    """Um consolidado da Rico mais recente nao pode assumir o preco medio.

    O `DENSE_RANK` que escolhe a linha da posicao ordena por data PRIMEIRO --
    autoridade da fonte so desempata dentro da mesma data. Entao basta o
    usuario subir o .xlsx da Rico depois do ultimo extrato da B3 para que o
    consolidado passe a ditar quantidade, valor de mercado E custo de todo
    ativo que as duas fontes cobrem.

    Para a foto da posicao isso esta certo. Para o custo nao: preco medio nao
    envelhece como cotacao envelhece -- ele so muda quando ha compra ou venda
    --, e a B3 e a custodia central, com o historico de TODAS as corretoras,
    enquanto o consolidado enxerga so a propria. O numero da Rico aqui (PM
    R$ 31,00) e o de uma corretora que so viu parte das compras.
    """
    pos = _pos([_row("BBAS3", qty=1479, vm=34017.00, invested=45849.00,
                     b3_avg=23.9200, b3_qty=1479)], "BBAS3")

    assert round(pos["preco_medio"], 2) == 23.92
    assert round(pos["total_investido"], 2) == 35377.68
    assert pos["custo_fonte"] == "b3_posicao_detalhada"
    assert pos["custo_estimado"] is False


def test_preco_medio_da_b3_escala_para_a_quantidade_de_hoje():
    """Quantidade nova, preco medio antigo: o PM da B3 vale, o total nao.

    Se o extrato da B3 e de antes da ultima compra, o total investido que ele
    publica e de outra posicao -- mas o preco medio segue sendo o melhor que
    existe. Multiplicar pelo que se tem hoje e extrapolacao, e o card precisa
    dizer isso: e "Custo estimado", nao "Custo investido".
    """
    pos = _pos([_row("DIRR3", qty=800, vm=9000.00, invested=None,
                     b3_avg=11.84, b3_qty=649)], "DIRR3")

    assert round(pos["preco_medio"], 2) == 11.84
    assert round(pos["total_investido"], 2) == 9472.00
    assert pos["custo_fonte"] == "b3_preco_medio_escalado"
    assert pos["custo_estimado"] is True


def test_sem_nenhuma_fonte_b3_o_consolidado_segue_valendo():
    """A regra e de prioridade, nao de proibicao.

    Os CDBs e os titulos privados (4606422UNA, 5082123CA1) existem so no
    consolidado da corretora: nao tem linha na "Posicao Detalhada" da B3 nem
    nota de negociacao importada. Descartar o numero deles em nome da regra
    trocaria um custo correto por "Nao informado" -- perder o dado nao e
    cumprir a regra, e so perder o dado.
    """
    pos = _pos([_row("CDB0001", qty=1, vm=10500.00, invested=10000.00)], "CDB0001")

    assert round(pos["total_investido"], 2) == 10000.00
    assert pos["custo_fonte"] == "snapshot"
