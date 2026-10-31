"""Testes da camada pura do importador do extrato "Posição" da B3.

O extrato traz uma aba por classe de ativo, cada uma com colunas próprias, e
a foto inteira vai para `portfolio_position_snapshots`. Três coisas erram em
silêncio aqui, e são elas que estes testes guardam:

1. **Rótulo que é prefixo de outro.** "Quantidade" é prefixo de "Quantidade
   Disponível" e "Valor Atualizado" de "Valor Atualizado MTM". Casamento por
   substring pegaria a coluna errada e gravaria a quantidade livre como se
   fosse a posição — número plausível, posição errada.
2. **Sinal do empréstimo.** Doador emprestou papel que é dele; Tomador deve
   papel que não é. As duas linhas são idênticas fora da coluna `Natureza`, e
   chutar o sinal soma dívida ao patrimônio.
3. **Data da foto.** Ela não está em NENHUMA célula do arquivo — só no nome do
   download. `report_date` está na chave única do snapshot: cair em
   `date.today()` colapsaria o histórico num snapshot sobrescrito.

Nada aqui toca banco: `ler_aba` recebe linhas e devolve dicts.
"""
from __future__ import annotations

import datetime as dt
import io

import pytest

from data_pipeline.importers.investments.b3_posicao import (
    ABAS_CONHECIDAS,
    SOURCE,
    SOURCE_TABLE,
    cabecalho,
    chave_snapshot,
    data_da_foto,
    ler_aba,
)

# Os nomes de aba e os valores de `Tipo` daqui vem do arquivo da B3, nao da
# lista de categorias do app. Mesmo assim `tests/test_categorias.py` reprova
# qualquer arquivo que repita CINCO ou mais nomes de
# `categorias.NOMES_DE_INVESTIMENTO` -- e a guarda contra uma segunda copia
# daquela lista, e ela nao sabe separar fixture de recolagem. Por isso a aba
# aqui e "Acoes" (como vem no arquivo, sem acento) e o fundo de teste e
# "Fundo de Investimento Imobiliario": quatro nomes coincidem, e um quinto
# literal acrescentado sem pensar reprova a suite inteira.

# ─────────────────────────────────────────────────────────────────────────────
# Cabeçalhos observados no arquivo real (posicao-2026-10-27-08-15-29.xlsx)
# ─────────────────────────────────────────────────────────────────────────────

HDR_ACOES = (
    "Produto", "Instituição", "Conta", "Código de Negociação",
    "CNPJ da Empresa", "Código ISIN / Distribuição", "Tipo", "Escriturador",
    "Quantidade", "Quantidade Disponível", "Quantidade Indisponível",
    "Motivo", "Preço de Fechamento", "Valor Atualizado",
)

HDR_EMPRESTIMOS = (
    "Produto", "Instituição", "Natureza", "Número de Contrato",
    "Código de Negociação", "Quantidade", "Preço de Fechamento",
    "Valor Atualizado",
)

HDR_RENDA_FIXA = (
    "Produto", "Instituição", "Emissor", "Código", "Indexador",
    "Vencimento", "Quantidade",
    "Preço Atualizado MTM", "Valor Atualizado MTM",
    "Preço Atualizado CURVA", "Valor Atualizado CURVA",
    "Preço Atualizado FECHAMENTO", "Valor Atualizado FECHAMENTO",
)

HDR_TESOURO = (
    "Produto", "Instituição", "Indexador", "Vencimento", "Quantidade",
    "Valor Aplicado", "Valor bruto", "Valor líquido", "Valor Atualizado",
)


def _uma(aba: str, hdr: tuple, linha: tuple) -> dict:
    """Lê uma única linha e exige que ela tenha sido aceita."""
    posicoes, recusas = ler_aba(aba, [hdr, linha])
    assert recusas == [], recusas
    assert len(posicoes) == 1
    return posicoes[0]


# ─────────────────────────────────────────────────────────────────────────────
# Casamento de rótulo — exato, nunca por prefixo
# ─────────────────────────────────────────────────────────────────────────────

def test_cabecalho_mapeia_rotulo_normalizado_para_coluna():
    hdr = cabecalho(("Produto", "Código de Negociação", "-", None, "Tipo"))

    assert hdr["PRODUTO"] == 0
    assert hdr["CODIGO DE NEGOCIACAO"] == 1
    # "-" é a forma do extrato dizer "não se aplica": não é rótulo.
    assert "-" not in hdr
    assert hdr["TIPO"] == 4


def test_cabecalho_mantem_a_primeira_de_duas_colunas_homonimas():
    """Duas colunas com o mesmo nome: a primeira vence, e de forma estável.

    Deixar a última sobrescrever faria o significado da coluna depender da
    ordem em que a B3 exporta — e ela já mudou de ordem antes.
    """
    hdr = cabecalho(("Produto", "Quantidade", "Quantidade"))

    assert hdr["QUANTIDADE"] == 1


def test_quantidade_nao_e_roubada_por_quantidade_disponivel():
    """A posição é "Quantidade"; "Quantidade Disponível" desconta o bloqueado.

    No arquivo real BRAP3 tem 100 em Quantidade e 100 em Disponível, então o
    defeito passaria invisível ali. Aqui os dois números são diferentes de
    propósito: é a única forma de o teste falhar quando o casamento voltar a
    ser por prefixo.
    """
    pos = _uma("Acoes", HDR_ACOES, (
        "BRAP3 - BRADESPAR S.A.", "BANCO BTG PACTUAL S.A.", "12345-6",
        "BRAP3", "03.847.461/0001-92", "BRBRAPACNPR5", "ON", "BRADESPAR",
        100, 40, 60, "Garantia", 24.50, 2450.00,
    ))

    assert pos["quantity"] == 100.0
    assert pos["market_value"] == 2450.00


def test_valor_atualizado_nao_e_roubado_por_valor_atualizado_mtm():
    """O rótulo mais longo vem ANTES na linha, de propósito.

    Casando por substring, a primeira coluna cujo nome contém "Valor
    Atualizado" seria a MTM — valor de outro papel, gravado sem erro.
    """
    hdr = ("Produto", "Quantidade", "Valor Atualizado MTM", "Valor Atualizado")
    pos = _uma("Acoes", hdr, ("XPTO3 - XPTO S.A.", 10, 99999.99, 100.00))

    assert pos["market_value"] == 100.00


# ─────────────────────────────────────────────────────────────────────────────
# Linhas que não são posição
# ─────────────────────────────────────────────────────────────────────────────

def test_bloco_total_no_pe_da_aba_nao_vira_posicao():
    """A aba termina com linha vazia e um bloco "Total".

    Ele preenche só as últimas colunas. Lido como posição, viraria um ativo
    sem ticker com o valor da aba inteira — e a carteira dobraria de tamanho.
    """
    linhas = [
        HDR_ACOES,
        ("BRAP3 - BRADESPAR S.A.", "BTG", "1", "BRAP3", "", "", "ON", "",
         100, 100, 0, "", 24.50, 2450.00),
        (None,) * len(HDR_ACOES),
        ("", "", "", "", "", "", "", "", "", "", "", "", "Total", 2450.00),
    ]
    posicoes, recusas = ler_aba("Acoes", linhas)

    assert [p["ticker"] for p in posicoes] == ["BRAP3"]
    assert recusas == []


def test_aba_desconhecida_devolve_vazio_sem_recusa():
    """Aba de outra origem não é erro desta aba: ela só não é lida aqui.

    Virar recusa encheria a tela de aviso para todo arquivo que trouxesse uma
    planilha auxiliar, e o usuário aprenderia a ignorar o aviso.
    """
    assert ler_aba("Plan1", [("Produto",), ("X", )]) == ([], [])


def test_aba_sem_linhas_nao_levanta():
    assert ler_aba("Acoes", []) == ([], [])


# ─────────────────────────────────────────────────────────────────────────────
# Ações, ETF e fundos
# ─────────────────────────────────────────────────────────────────────────────

def test_ticker_vem_do_codigo_de_negociacao_nao_do_produto():
    """"Código de Negociação" é o código da fita; o Produto é texto livre.

    No arquivo real o Produto de ITSA4 começa com "ITAUSA", que não é ticker
    de nada. Usar o prefixo criaria um ativo fantasma ao lado do verdadeiro.
    """
    pos = _uma("Acoes", HDR_ACOES, (
        "ITAUSA - ITAUSA S.A.", "BTG", "1", "ITSA4", "", "", "PN", "",
        300, 300, 0, "", 9.80, 2940.00,
    ))

    assert pos["ticker"] == "ITSA4"
    assert pos["name"] == "ITAUSA S.A."


def test_instituicao_e_a_corretora_da_linha():
    """O extrato é da custódia central e nomeia a corretora de cada posição.

    Gravar "B3" em todas perderia a única informação que diz onde o papel
    está — e é por instituição que a chave do snapshot separa duas posições do
    mesmo ticker.
    """
    pos = _uma("Acoes", HDR_ACOES, (
        "BRAP3 - BRADESPAR", "BANCO C6 S.A.", "1", "BRAP3", "", "", "ON", "",
        10, 10, 0, "", 24.50, 245.00,
    ))

    assert pos["institution"] == "BANCO C6 S.A."


def test_etf_continua_etf_mesmo_classificado_como_criptoativo():
    """BITH11 vem com Tipo "Criptoativo" e segue sendo ETF listado na B3.

    A classe do ativo é o veículo, não o lastro: trocá-la tiraria o papel da
    linha de ETF da carteira sem nada ter mudado.
    """
    pos = _uma("ETF", HDR_ACOES, (
        "BITH11 - HASHDEX", "BTG", "1", "BITH11", "", "", "Criptoativo", "",
        50, 50, 0, "", 174.32, 8716.25,
    ))

    assert pos["asset_type"] == "etf"


@pytest.mark.parametrize(
    "tipo, esperado",
    [
        ("FII", "fii"),
        ("Fundo de Investimento Imobiliário", "fii"),
        ("FI Renda Fixa", "fundo_rf"),
        ("FIC RF Referenciado DI", "fundo_rf"),
    ],
)
def test_fundo_de_investimento_segue_a_regra_da_xp(tipo, esperado):
    """Mesma classificação do parser da XP, de propósito.

    Um fundo que fosse FII num arquivo e fundo_rf no outro mudaria a
    composição da carteira conforme a origem do import, sem aporte nenhum.
    """
    pos = _uma("Fundo de Investimento", HDR_ACOES, (
        "MXRF11 - MAXI RENDA", "BTG", "1", "MXRF11", "", "", tipo, "",
        1000, 1000, 0, "", 9.50, 9500.00,
    ))

    assert pos["asset_type"] == esperado


def test_fundos_no_plural_cai_na_mesma_categoria():
    """A categoria entra na chave do snapshot e não pode seguir o nome da aba.

    No dia em que a B3 voltar a escrever "Fundos de Investimento", a mesma
    posição viraria uma segunda linha na mesma data, e a carteira contaria o
    fundo duas vezes.
    """
    linha = ("MXRF11 - MAXI RENDA", "BTG", "1", "MXRF11", "", "", "FII", "",
             1000, 1000, 0, "", 9.50, 9500.00)

    singular = _uma("Fundo de Investimento", HDR_ACOES, linha)
    plural = _uma("Fundos de Investimento", HDR_ACOES, linha)

    assert singular["_categoria"] == plural["_categoria"] == "fundos"


def test_linha_sem_quantidade_e_recusada_com_motivo():
    """Recusar é melhor que gravar posição sem tamanho.

    O motivo nomeia só o Produto: o extrato tem CPF, conta e número de
    contrato, e nenhum deles pode sair no log.
    """
    posicoes, recusas = ler_aba("Acoes", [
        HDR_ACOES,
        ("BRAP3 - BRADESPAR", "BTG", "1", "BRAP3", "", "", "ON", "",
         "-", "-", "-", "", 24.50, 2450.00),
    ])

    assert posicoes == []
    assert len(recusas) == 1
    assert "Quantidade" in recusas[0]
    assert "BRAP3 - BRADESPAR" in recusas[0]


# ─────────────────────────────────────────────────────────────────────────────
# Empréstimos — o sinal mora na Natureza
# ─────────────────────────────────────────────────────────────────────────────

def test_doador_e_posicao_propria_positiva():
    """Doador emprestou papel que é dele: continua no patrimônio."""
    pos = _uma("Empréstimos", HDR_EMPRESTIMOS, (
        "ALOS3 - ALLOS S.A.", "BTG", "Doador", "C-0001", "ALOS3",
        1000, 20.00, 20000.00,
    ))

    assert pos["quantity"] == 1000.0
    assert pos["market_value"] == 20000.00
    assert pos["is_loaned"] is True


def test_tomador_e_posicao_devida_negativa():
    """Tomador deve o papel: ele não é patrimônio, é obrigação.

    O preço NÃO leva sinal — preço é preço. O sinal mora na quantidade e no
    valor, que é o que soma na carteira.
    """
    pos = _uma("Empréstimos", HDR_EMPRESTIMOS, (
        "DEXP3 - DEXCO S.A.", "BTG", "Tomador", "C-0002", "DEXP3",
        20, 6.919, 138.38,
    ))

    assert pos["quantity"] == -20.0
    assert pos["market_value"] == -138.38
    assert pos["market_price"] == 6.919
    assert pos["is_loaned"] is True


@pytest.mark.parametrize("natureza", ["", "-", None, "Intermediário"])
def test_natureza_desconhecida_e_recusada(natureza):
    """Sem a Natureza as duas respostas possíveis têm sinais opostos.

    Chutar "Doador" somaria dívida ao patrimônio; chutar "Tomador" apagaria
    posição viva. As duas erram em silêncio, e por isso a linha sai.
    """
    posicoes, recusas = ler_aba("Empréstimos", [
        HDR_EMPRESTIMOS,
        ("DEXP3 - DEXCO", "BTG", natureza, "C-0002", "DEXP3",
         20, 6.919, 138.38),
    ])

    assert posicoes == []
    assert len(recusas) == 1
    assert "Natureza" in recusas[0]


# ─────────────────────────────────────────────────────────────────────────────
# Renda Fixa — uma marcação inteira, nunca metade de cada
# ─────────────────────────────────────────────────────────────────────────────

def _linha_rf(mtm, curva, fechamento, *, codigo="C6A1B2"):
    return (
        "CDB - CDB", "BANCO C6 S.A.", "BANCO C6 S.A.", codigo, "CDI",
        "15/03/2028", 1.0,
        mtm[0], mtm[1], curva[0], curva[1], fechamento[0], fechamento[1],
    )


def test_renda_fixa_prefere_mtm_quando_ela_vem():
    pos = _uma("Renda Fixa", HDR_RENDA_FIXA, _linha_rf(
        (1100.00, 1100.00), (1050.00, 1050.00), (1090.00, 1090.00),
    ))

    assert pos["_marcacao"] == "MTM"
    assert pos["market_price"] == 1100.00
    assert pos["market_value"] == 1100.00


def test_renda_fixa_cai_para_a_marcacao_seguinte_inteira():
    """Preço e valor saem da MESMA marcação, sempre.

    Misturar preço MTM com valor de curva daria um preço que não multiplica a
    quantidade para o valor gravado — e a divergência só apareceria no dia em
    que alguém conferisse a conta.
    """
    pos = _uma("Renda Fixa", HDR_RENDA_FIXA, _linha_rf(
        ("-", "-"), (1050.00, 1050.00), (1090.00, 1090.00),
    ))

    assert pos["_marcacao"] == "CURVA"
    assert pos["market_price"] == 1050.00
    assert pos["market_value"] == 1050.00


def test_renda_fixa_sem_nenhuma_marcacao_e_recusada():
    posicoes, recusas = ler_aba("Renda Fixa", [
        HDR_RENDA_FIXA, _linha_rf(("-", "-"), ("-", "-"), ("-", "-")),
    ])

    assert posicoes == []
    assert len(recusas) == 1
    assert "marcac" in recusas[0].lower()


def test_renda_fixa_usa_o_codigo_da_b3_como_ticker():
    """Sem o código, três CDBs de três bancos colidiriam num ativo só.

    "CDB" é tipo de papel, não identidade: o preço médio e a posição dos três
    virariam uma média sem significado.
    """
    pos = _uma("Renda Fixa", HDR_RENDA_FIXA, _linha_rf(
        (1100.00, 1100.00), ("-", "-"), ("-", "-"), codigo="C6A1B2",
    ))

    assert pos["ticker"] == "C6A1B2"
    assert pos["name"] == "CDB - BANCO C6 S.A."
    assert pos["asset_type"] == "fixed_income"


def test_renda_fixa_sem_codigo_cai_no_tipo_do_papel():
    """Último recurso, e visível: melhor um ticker genérico que nenhum."""
    pos = _uma("Renda Fixa", HDR_RENDA_FIXA, _linha_rf(
        (1100.00, 1100.00), ("-", "-"), ("-", "-"), codigo="",
    ))

    assert pos["ticker"] == "CDB"


def test_cff_e_classe_de_fip_nao_de_renda_fixa():
    """Mesma classificação do parser da XP para o mesmo papel."""
    linha = list(_linha_rf((500.00, 500.00), ("-", "-"), ("-", "-")))
    linha[0] = "CFF - FUNDO DE INVESTIMENTO EM PARTICIPACOES"
    pos = _uma("Renda Fixa", HDR_RENDA_FIXA, tuple(linha))

    assert pos["asset_type"] == "fundo_rf"


# ─────────────────────────────────────────────────────────────────────────────
# Tesouro Direto
# ─────────────────────────────────────────────────────────────────────────────

def test_tesouro_separa_aplicado_de_atualizado():
    """`invested_value` é o aporte; `market_value` é quanto vale hoje.

    Os dois na mesma coluna apagariam o rendimento do título — a carteira
    mostraria o valor de compra como se fosse o de mercado.
    """
    pos = _uma("Tesouro Direto", HDR_TESOURO, (
        "Tesouro IPCA+ 2029", "BANCO C6 S.A.", "IPCA", "15/05/2029",
        1.50, 5000.00, 6000.00, 5700.00, 6000.00,
    ))

    assert pos["ticker"] == "TIPCA2029"
    assert pos["quantity"] == 1.50
    assert pos["invested_value"] == 5000.00
    assert pos["market_value"] == 6000.00
    # O extrato não publica PU. Valor/quantidade seria número derivado
    # passando por número lido.
    assert pos["market_price"] is None
    assert pos["asset_type"] == "tesouro"


def test_tesouro_cai_para_valor_bruto_nao_para_o_liquido():
    """O líquido desconta IR e taxa que só existem no resgate.

    Ele não é o valor do título hoje: usá-lo subavaliaria a carteira de quem
    não vai resgatar nada.
    """
    pos = _uma("Tesouro Direto", HDR_TESOURO, (
        "Tesouro Selic 2031", "BTG", "SELIC", "01/03/2031",
        2.0, 10000.00, 11000.00, 10400.00, "-",
    ))

    assert pos["market_value"] == 11000.00


def test_tesouro_sem_valor_algum_e_recusado():
    posicoes, recusas = ler_aba("Tesouro Direto", [
        HDR_TESOURO,
        ("Tesouro Selic 2031", "BTG", "SELIC", "01/03/2031",
         2.0, 10000.00, "-", "-", "-"),
    ])

    assert posicoes == []
    assert len(recusas) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Data da foto — não está em nenhuma célula
# ─────────────────────────────────────────────────────────────────────────────

def _xlsx_com_criacao(criado: dt.datetime | None) -> bytes:
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    wb.active.title = "Acoes"
    wb.properties.created = criado
    wb.properties.modified = criado
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_data_da_foto_vem_do_nome_do_download():
    assert data_da_foto(
        "posicao-2026-10-27-08-15-29.xlsx", b"",
    ) == dt.date(2026, 10, 27)


def test_o_nome_ganha_do_docprops():
    """O nome é o que o usuário vê e pode conferir.

    O docProps carrega a data de EXPORTAÇÃO, que pode ser outro dia — o
    arquivo baixado hoje de uma posição de ontem, por exemplo.
    """
    xlsx = _xlsx_com_criacao(dt.datetime(2020, 1, 1, 12, 0, 0))

    assert data_da_foto(
        "posicao-2026-10-27-08-15-29.xlsx", xlsx,
    ) == dt.date(2026, 10, 27)


def test_docprops_e_a_reserva_do_arquivo_renomeado():
    """"carteira.xlsx" não pode ser recusado tendo a data dentro."""
    xlsx = _xlsx_com_criacao(dt.datetime(2026, 10, 27, 8, 15, 29))

    assert data_da_foto("carteira.xlsx", xlsx) == dt.date(2026, 10, 27)


def test_data_impossivel_no_nome_cai_para_a_reserva():
    """"2026-13-45" casa com o padrão e não é data.

    Sem a validação, `date()` levantaria ValueError dentro do parser e o
    arquivo inteiro falharia por causa do nome.
    """
    xlsx = _xlsx_com_criacao(dt.datetime(2026, 10, 27, 8, 15, 29))

    assert data_da_foto("posicao-2026-13-45.xlsx", xlsx) == dt.date(2026, 10, 27)


def test_sem_data_em_lugar_nenhum_devolve_none():
    """None é a resposta certa: o parser recusa em vez de usar hoje.

    `report_date` está na chave única do snapshot. Com `date.today()`, toda
    foto sem data viraria a MESMA linha, sobrescrita a cada import — histórico
    perdido sem uma mensagem de erro.
    """
    assert data_da_foto("carteira.xlsx", b"nao e um xlsx") is None
    assert data_da_foto("", b"") is None


# ─────────────────────────────────────────────────────────────────────────────
# Chave do snapshot — identidade da foto, sem quantidade nem valor
# ─────────────────────────────────────────────────────────────────────────────

def test_a_chave_e_estavel_para_a_mesma_foto():
    """Reimportar o mesmo arquivo atualiza a linha, não cria outra."""
    args = (dt.date(2026, 10, 27), "acoes", "BRAP3", ["BTG", "12345-6"])

    assert chave_snapshot(*args) == chave_snapshot(*args)


def test_a_chave_nao_depende_de_quantidade_nem_de_valor():
    """Quantidade na chave faria cada reimport virar uma linha nova.

    O ativo mudaria de tamanho ao longo do dia e a carteira somaria todas as
    versões — a chave é a identidade da posição, não o seu estado.
    """
    # A assinatura não aceita quantidade: o teste é o contrato.
    chave = chave_snapshot(
        dt.date(2026, 10, 27), "acoes", "BRAP3", ["BTG", "12345-6"],
    )

    assert chave.startswith("b3posi-snap-")


@pytest.mark.parametrize(
    "a, b",
    [
        # Datas diferentes: duas fotos, não uma sobrescrita.
        (
            (dt.date(2026, 10, 27), "acoes", "BRAP3", ["BTG"]),
            (dt.date(2026, 10, 28), "acoes", "BRAP3", ["BTG"]),
        ),
        # Mesmo ticker em duas corretoras: duas posições que somam.
        (
            (dt.date(2026, 10, 27), "acoes", "BRAP3", ["BTG"]),
            (dt.date(2026, 10, 27), "acoes", "BRAP3", ["C6"]),
        ),
        # Custódia e empréstimo do mesmo papel coexistem.
        (
            (dt.date(2026, 10, 27), "acoes", "ALOS3", ["BTG"]),
            (dt.date(2026, 10, 27), "emprestimos", "ALOS3", ["BTG"]),
        ),
        # Doador e Tomador do mesmo papel na mesma corretora.
        (
            (dt.date(2026, 10, 27), "emprestimos", "ALOS3", ["BTG", "DOADOR"]),
            (dt.date(2026, 10, 27), "emprestimos", "ALOS3", ["BTG", "TOMADOR"]),
        ),
    ],
)
def test_chaves_diferentes_para_posicoes_diferentes(a, b):
    assert chave_snapshot(*a) != chave_snapshot(*b)


def test_discriminador_vazio_nao_muda_a_chave():
    """Coluna que não veio não pode virar outra identidade.

    O extrato escreve "-" em coluna que não se aplica, e isso varia por
    classe: a mesma posição mudaria de chave conforme a B3 preenchesse ou não
    a conta, e viraria duas linhas na mesma data.
    """
    base = chave_snapshot(dt.date(2026, 10, 27), "acoes", "BRAP3", ["BTG"])

    assert base == chave_snapshot(
        dt.date(2026, 10, 27), "acoes", "BRAP3", ["BTG", ""],
    )
    assert base == chave_snapshot(
        dt.date(2026, 10, 27), "acoes", "BRAP3", ["BTG", None],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Contrato com o resto do app
# ─────────────────────────────────────────────────────────────────────────────

def test_a_fonte_tem_nome_proprio():
    """`source_table` separa esta foto das outras no desempate da leitura.

    Compartilhar o nome com `b3_posicao_detalhada` faria as duas fotos
    disputarem a mesma linha do snapshot.
    """
    assert SOURCE == SOURCE_TABLE == "b3_posicao"


def test_todas_as_abas_do_arquivo_real_tem_leitor():
    """As seis abas do extrato observado em 27/10/2026.

    Aba sem leitor não é erro visível: ela entra na nota de "abas ignoradas" e
    a classe inteira desaparece da foto.
    """
    from data_pipeline.importers.investments.xlsx_probe import norm

    observadas = [
        "Acoes", "Empréstimos", "ETF", "Fundo de Investimento",
        "Renda Fixa", "Tesouro Direto",
    ]
    assert [a for a in observadas if norm(a) not in ABAS_CONHECIDAS] == []
