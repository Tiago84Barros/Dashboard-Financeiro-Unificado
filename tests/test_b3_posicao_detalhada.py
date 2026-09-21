"""Testes do parser do extrato "Posição Detalhada" da Área do Investidor.

O arquivo é uma FOTO da carteira, não um log de eventos, e sete seções
empilhadas numa aba só, cada uma com colunas diferentes. As armadilhas que
estes testes prendem são todas silenciosas — nenhuma delas levanta exceção:

  - "1.479" lido como 1,479 (milhar sem casa decimal);
  - "-R$ 124,10" lido como None (espaço no MEIO, depois do sinal);
  - posição zerada entrando como posição viva;
  - Custódia Remunerada somando de novo papel já contado em "Ações";
  - provisionado indo parar em `dividends`, inflando renda realizada.
"""
from __future__ import annotations

from datetime import date

import pytest

from data_pipeline.importers.investments.b3_posicao_detalhada import (
    chave_provisionado,
    chave_snapshot,
    cotas_por_divisao,
    extrair_data_foto,
    numero_br,
    posicao,
    secoes,
    tipo_provento,
)
from data_pipeline.importers.investments.common import to_float_br

VAZIA = (None, None, None, None, None, None, None)


def _linhas_acoes() -> list[tuple]:
    """Bloco "Ações" no formato real: título, cabeçalho e duas linhas."""
    return [
        ("Ações", None, None, None, None, None, "R$ 109.127,53"),
        (
            "36,6% | Renda Variável Brasil", "Saldo", "% Alocação",
            "Rentabilidade", "Preço médio", "Último preço (R$)", "Qtd. total",
        ),
        ("BBAS3", "R$ 34.017,00", "12,49%", "R$ 1,00", "R$ 22,00",
         "R$ 23,00", "1.479"),
        ("CSMG3", "R$ 0,00", "0%", "R$ 0,00", "R$ 0,00", "R$ 20,00", "0"),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# numero_br
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("1.479", 1479.0),        # quantidade: milhar SEM casa decimal
        ("4.567", 4567.0),
        ("R$ 34.017,00", 34017.0),
        ("-R$ 124,10", -124.10),  # espaço entre o sinal e o número
        ("17", 17.0),
        ("R$ 1,00", 1.0),
    ],
)
def test_numero_br(texto, esperado):
    assert numero_br(texto) == pytest.approx(esperado)


def test_numero_br_distingue_milhar_de_decimal():
    """"1.479" é mil e quatrocentos; "1,479" continua sendo um e pouco.

    O extrato publica quantidade sem centavos e valor com centavos, então as
    duas formas convivem no mesmo arquivo. Ler o milhar como decimal daria
    BBAS3 com 1,479 ações — número válido, mil vezes menor, e nenhum erro.
    """
    assert numero_br("1.479") == 1479.0
    assert numero_br("1,479") == pytest.approx(1.479)


def test_to_float_br_le_negativo_com_espaco():
    """Regressão: "-R$ 124,10" devolvia None, não -124,10.

    `.strip()` não alcança o espaço do MEIO — o que sobrava era "- 124,10",
    que `float()` recusa. E None, num importador, significa "essa coluna não
    veio", não "não consegui ler": a linha inteira saía torta sem erro. Os
    positivos escapavam porque neles o espaço cai na borda.
    """
    assert to_float_br("-R$ 124,10") == pytest.approx(-124.10)
    assert to_float_br("R$ 124,10") == pytest.approx(124.10)
    assert to_float_br("-") is None


# ─────────────────────────────────────────────────────────────────────────────
# extrair_data_foto
# ─────────────────────────────────────────────────────────────────────────────

def test_extrair_data_foto_do_cabecalho():
    linhas = [
        (None, None, None, None, None, "Conta: undefined | 21/09/2026, 18:45"),
        VAZIA,
    ]
    assert extrair_data_foto(linhas) == date(2026, 9, 21)


def test_extrair_data_foto_ignora_vencimento_la_embaixo():
    """Só o topo é varrido: mais abaixo o arquivo é cheio de datas.

    Vencimento de aluguel e previsão de pagamento de provento casam com o
    mesmo padrão. `report_date` está na chave única do snapshot — pegar a
    data errada não dá erro, grava a foto no dia errado.
    """
    linhas = [VAZIA, VAZIA, VAZIA, ("DEXP3", "07/10/2026")]
    assert extrair_data_foto(linhas) is None


# ─────────────────────────────────────────────────────────────────────────────
# secoes
# ─────────────────────────────────────────────────────────────────────────────

def test_secoes_separa_por_forma_da_linha():
    blocos = secoes(_linhas_acoes())
    assert len(blocos) == 1
    sec = blocos[0]
    assert sec["categoria"] == "ACOES"
    assert sec["subsecao"] == "Renda Variável Brasil"
    assert len(sec["linhas"]) == 2


def test_secoes_marca_bloco_de_custodia():
    """Título de bloco é a linha com SÓ a coluna A preenchida.

    É o que separa carteira de custódia e de provisão — e só a forma da linha
    distingue, porque o texto do título muda de extrato para extrato.
    """
    linhas = [
        *_linhas_acoes(),
        VAZIA,
        ("Custódia Remunerada", None, None, None, None, None, None),
        ("Ações e Fundos Imobiliários", None, None, None, None, None,
         "R$ 45.771,34"),
        ("0% | Renda Variável Brasil", "Valor total", "% Alocação",
         "Valor atual (PU)", "Quantidade", "Data de vencimento", None),
        ("BBAS3", "R$ 34.017,00", "12,49%", "R$ 23,00", "1.479",
         "07/10/2026", None),
    ]
    blocos = secoes(linhas)
    assert [b["bloco"] for b in blocos] == ["CARTEIRA", "CUSTODIA REMUNERADA"]


# ─────────────────────────────────────────────────────────────────────────────
# cotas_por_divisao
# ─────────────────────────────────────────────────────────────────────────────

def test_cotas_por_divisao_recupera_inteiro():
    """A seção de FII não publica quantidade; Saldo / cotação a recupera."""
    qtd, motivo = cotas_por_divisao(3846.60, 106.85)
    assert qtd == 36.0
    assert motivo == ""


def test_cotas_por_divisao_recusa_nao_inteiro():
    """Se não cai em inteiro, o extrato não permite AFIRMAR a quantidade.

    Cota de FII é indivisível. Divisão com resto significa que saldo e
    cotação não se referem à mesma coisa (ou vieram arredondados de formas
    diferentes) — e aí o número derivado seria um palpite com cara de
    observação.
    """
    qtd, motivo = cotas_por_divisao(1000.00, 106.85)
    assert qtd is None
    assert "nao e inteiro" in motivo


@pytest.mark.parametrize("saldo, cotacao", [(None, 10.0), (100.0, None),
                                            (100.0, 0.0)])
def test_cotas_por_divisao_sem_entrada(saldo, cotacao):
    qtd, motivo = cotas_por_divisao(saldo, cotacao)
    assert qtd is None
    assert motivo


# ─────────────────────────────────────────────────────────────────────────────
# posicao
# ─────────────────────────────────────────────────────────────────────────────

def test_posicao_acao():
    sec = secoes(_linhas_acoes())[0]
    pos, motivo = posicao(sec, sec["linhas"][0])
    assert motivo == ""
    assert pos["ticker"] == "BBAS3"
    assert pos["asset_type"] == "stock"
    assert pos["quantity"] == 1479.0
    assert pos["market_value"] == pytest.approx(34017.00)


def test_posicao_zerada_nao_entra():
    """CSMG3 com Qtd. 0 é papel vendido, não posição viva.

    Gravar quantidade zero num snapshot faria a tela mostrar um ativo que o
    usuário não tem mais — com valor R$ 0,00, que parece dado faltando.
    """
    sec = secoes(_linhas_acoes())[0]
    pos, motivo = posicao(sec, sec["linhas"][1])
    assert pos is None
    assert "zero" in motivo


def test_posicao_sem_quantidade_nao_entra():
    """Renda Fixa e Fundos de Investimentos não publicam quantidade.

    O extrato traz só saldo e valor aplicado do CDB e do FIP. Sem quantidade
    nem preço unitário não há o que derivar, e a regra é que nada sem
    quantidade entra.
    """
    linhas = [
        ("Renda Fixa", None, None, None, None, None, "R$ 1.177,32"),
        ("0,4% | Pós-Fixado", "Saldo a mercado", "% Alocação",
         "Valor aplicado", "Rentabilidade a mercado", "Data aplicação",
         "Data vencimento"),
        ("CDB BMG - FEV/2028", "R$ 1.177,32", "0,43%", "R$ 1.000,00",
         "17,73%", "01/02/2024", "01/02/2028"),
    ]
    sec = secoes(linhas)[0]
    pos, motivo = posicao(sec, sec["linhas"][0])
    assert pos is None
    assert "nao publica quantidade" in motivo


def test_posicao_aluguel_tomador_tem_sinal_negativo():
    """Tomador é posição tomada em empréstimo: saldo e quantidade negativos.

    Guardar +17 ao lado de um saldo -124,10 quebraria a identidade
    quantidade x preço = valor e somaria uma posição comprada inexistente.
    """
    linhas = [
        ("Aluguel", None, None, None, None, None, "-R$ 124,10"),
        ("-0% | Renda Variável Brasil", "Saldo", "% Alocação",
         "Última cotação", "Qtd.", "Vencimento", "Tomador/Doador"),
        ("DEXP3", "-R$ 124,10", "-0,05%", "R$ 7,30", "17", "07/10/2026",
         "Tomador"),
    ]
    sec = secoes(linhas)[0]
    pos, motivo = posicao(sec, sec["linhas"][0])
    assert motivo == ""
    assert pos["quantity"] == -17.0
    assert pos["market_value"] == pytest.approx(-124.10)
    assert pos["is_loaned"] is True


def test_posicao_tesouro_nao_inventa_preco_unitario():
    """O extrato não publica PU — saldo/quantidade daria um PU falso.

    Os dois vêm arredondados, então a divisão produz um número próximo do
    certo e indistinguível de uma observação. Preço derivado de
    arredondamento já custou caro neste projeto.
    """
    linhas = [
        ("Tesouro Direto", None, None, None, None, None, "R$ 109.542,88"),
        ("20% | Tesouro", "Saldo", "% Alocação", "Valor aplicado",
         "Quantidade", "Disponível", "Vencimento"),
        ("Tesouro Selic 2031", "R$ 56.154,59", "20,6%", "R$ 50.000,00",
         "2,83", "2,83", "01/03/2031"),
    ]
    sec = secoes(linhas)[0]
    pos, motivo = posicao(sec, sec["linhas"][0])
    assert motivo == ""
    assert pos["asset_type"] == "tesouro"
    assert pos["quantity"] == pytest.approx(2.83)
    assert pos["market_price"] is None
    assert pos["market_value"] == pytest.approx(56154.59)


def test_posicao_fii_deriva_cotas():
    linhas = [
        ("Fundos Imobiliários", None, None, None, None, None,
         "R$ 18.331,90"),
        ("6,7% | Fundos Listados", None, None, "Saldo", "% Alocação",
         "Preço médio (abertura)", "Última cotação"),
        ("KNCR11", None, None, "R$ 3.846,60", "1,41%", "R$ 100,00",
         "R$ 106,85"),
    ]
    sec = secoes(linhas)[0]
    pos, motivo = posicao(sec, sec["linhas"][0])
    assert motivo == ""
    assert pos["asset_type"] == "fii"
    assert pos["quantity"] == 36.0


# ─────────────────────────────────────────────────────────────────────────────
# tipo_provento
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "evento, esperado",
    [
        ("JUROS SOBRE CAPITAL PROPRIO", "jcp"),
        ("JURO", "jcp"),
        ("DIVIDENDO", "dividend"),
        ("DIVI", "dividend"),
        ("RENDIMENTO", "reit_income"),
        ("AMORTIZACAO", "amortization"),
    ],
)
def test_tipo_provento(evento, esperado):
    assert tipo_provento(evento) == esperado


def test_tipo_provento_juros_vence_dividendo_na_ordem():
    """"JUROS SOBRE CAPITAL PROPRIO" não pode cair em "dividend".

    O extrato abrevia o mesmo evento de quatro formas ("DIVI", "JURO",
    "DIVIDENDO", "JUROS SOBRE CAPITAL PROPRIO"), e JCP e dividendo têm
    tributação diferente — trocar um pelo outro é erro de imposto, não de
    rótulo.
    """
    assert tipo_provento("JUROS SOBRE CAPITAL PROPRIO") == "jcp"
    assert tipo_provento("JUROS SOBRE CAPITAL PROPRIO") != "dividend"


# ─────────────────────────────────────────────────────────────────────────────
# Chaves de unicidade
# ─────────────────────────────────────────────────────────────────────────────

def test_chave_snapshot_nao_depende_da_quantidade():
    """A foto de hoje à tarde SOBRESCREVE a de hoje de manhã.

    O cabeçalho do extrato traz hora, então duas exportações do mesmo dia são
    normais — e entre elas a quantidade pode ter mudado. Com o valor dentro
    da chave, as duas virariam linhas separadas do mesmo ativo na mesma data,
    e somar `market_value` por data contaria o papel duas vezes. Nenhum erro
    apareceria: só um patrimônio maior.
    """
    assert (chave_snapshot(date(2026, 9, 21), "BBAS3", "ACOES")
            == chave_snapshot(date(2026, 9, 21), "BBAS3", "ACOES"))


def test_chave_snapshot_separa_comprado_de_alugado():
    """DEXP3 em "Ações" e DEXP3 em "Aluguel" são posições opostas.

    As duas têm o mesmo ticker e o mesmo `asset_type` "stock". Sem a
    categoria na chave, a posição tomada em empréstimo (negativa) apagaria a
    comprada.
    """
    assert (chave_snapshot(date(2026, 9, 21), "DEXP3", "ACOES")
            != chave_snapshot(date(2026, 9, 21), "DEXP3", "ALUGUEL"))


def test_chave_snapshot_separa_datas():
    assert (chave_snapshot(date(2026, 9, 21), "BBAS3", "ACOES")
            != chave_snapshot(date(2026, 9, 20), "BBAS3", "ACOES"))


def test_chave_provisionado_separa_eventos_na_mesma_previsao():
    """PETR3 tem DIVI e JURO com a mesma data de pagamento previsto."""
    d, p = date(2026, 9, 21), date(2026, 12, 21)
    assert (chave_provisionado(d, "PETR3", "DIVI", p, 1)
            != chave_provisionado(d, "PETR3", "JURO", p, 1))


def test_chave_provisionado_nao_depende_do_valor():
    """Valor provisionado é revisado entre extratos — é conteúdo, não chave.

    Com o bruto na chave, a revisão de R$ 51,40 para R$ 53,00 não corrigiria
    a linha: criaria uma segunda, e a renda futura apareceria somada.
    """
    d, p = date(2026, 9, 21), date(2026, 12, 21)
    assert (chave_provisionado(d, "PETR3", "DIVI", p, 1)
            == chave_provisionado(d, "PETR3", "DIVI", p, 1))


def test_chave_provisionado_desempata_trio_repetido():
    """Duas parcelas do mesmo evento na mesma data não podem colapsar."""
    d, p = date(2026, 9, 21), date(2026, 12, 21)
    assert (chave_provisionado(d, "PETR3", "DIVI", p, 1)
            != chave_provisionado(d, "PETR3", "DIVI", p, 2))
