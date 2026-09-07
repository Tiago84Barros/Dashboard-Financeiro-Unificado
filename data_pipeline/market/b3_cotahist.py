"""Leitor do COTAHIST da B3 -- o arquivo oficial, sem filtro embutido.

O parser de FIIs (`fii_b3_history.parse_cotahist`) nasceu com o filtro de BDI
cravado no meio do laco: lia o arquivo inteiro e jogava fora tudo que nao fosse
cota de fundo imobiliario. O arquivo, porem, traz **todos** os papeis negociados
no pregao -- acoes, ETFs, BDRs, units, opcoes -- com a mesma linha de 245
colunas. Estavamos baixando 584 MB por ano e aproveitando a fatia dos FIIs.

Este modulo separa as duas coisas: aqui mora a leitura do layout, e o filtro
vira parametro. `fii_b3_history` continua chamando com ``bdi_codes=("12",)`` e
nao muda de comportamento.

Layout (posicoes 0-indexadas, registro tipo "01", linha de 245 colunas)
----------------------------------------------------------------------
    0:2   TIPREG      "01" para cotacao do papel
    2:10  DATA        AAAAMMDD
   10:12  CODBDI      "02" lote padrao, "12" cota de FII, "96" fracionario
   12:24  CODNEG      o ticker
   24:27  TPMERC      "010" mercado a vista
   27:39  NOMRES      nome resumido da empresa
   39:49  ESPECI      ON, PN, CI, UNT, DRN...
   56:69  PREABE      abertura, em centavos
   69:82  PREMAX      maxima
   82:95  PREMIN      minima
   95:108 PREMED      media
  108:121 PREULT      fechamento
  147:152 TOTNEG      numero de negocios
  152:170 QUATOT      quantidade negociada
  170:188 VOLTOT      volume financeiro, em centavos
  210:217 FATCOT      fator de cotacao (1 ou 1000)
  230:242 CODISI      ISIN

O arquivo **nao e ajustado por proventos**. Ele e preco negociado e prova de
que o papel existia naquela data -- nao serie de retorno total. Quem usa isso
para medir retorno precisa dizer que usa preco bruto; ver
`docs/memoria_mercado.md`, secao de limitacoes.
"""
from __future__ import annotations

import io
import zipfile
from datetime import datetime

#: Cotas de fundo imobiliario.
BDI_FII = "12"
#: Lote padrao: acoes, units, ETFs e BDRs. E onde estao as acoes da B3.
BDI_LOTE_PADRAO = "02"
#: Mercado a vista. Sem isso entram termo, opcao e exercicio como se fossem
#: preco do papel -- e o "fechamento" de uma opcao nao e o fechamento da acao.
TPMERC_VISTA = "010"

#: Comprimento minimo para que a linha tenha ate o ISIN.
_COLUNAS_MINIMAS = 242


def _dinheiro(linha: str, inicio: int, fim: int) -> float | None:
    bruto = linha[inicio:fim].strip()
    try:
        return int(bruto) / 100.0 if bruto else None
    except ValueError:
        return None


def _inteiro(linha: str, inicio: int, fim: int) -> int | None:
    bruto = linha[inicio:fim].strip()
    try:
        return int(bruto) if bruto else None
    except ValueError:
        return None


def ler_linhas(
    conteudo: bytes,
    *,
    bdi_codes: tuple[str, ...] = (BDI_LOTE_PADRAO,),
    tpmerc: str = TPMERC_VISTA,
) -> list[dict]:
    """Le um ZIP do COTAHIST e devolve as linhas dos BDIs pedidos.

    `bdi_codes` vazio significa **todos os BDIs**, nao nenhum: a diferenca
    aparece na chamada, e recusar tudo em silencio seria o defeito caro.
    """
    with zipfile.ZipFile(io.BytesIO(conteudo)) as compactado:
        nomes = [n for n in compactado.namelist() if n.upper().endswith(".TXT")]
        if not nomes:
            return []
        linhas = compactado.read(nomes[0]).decode("latin-1").splitlines()
    return ler_texto(linhas, bdi_codes=bdi_codes, tpmerc=tpmerc)


def ler_texto(
    linhas: list[str],
    *,
    bdi_codes: tuple[str, ...] = (BDI_LOTE_PADRAO,),
    tpmerc: str = TPMERC_VISTA,
) -> list[dict]:
    """Mesma leitura, a partir das linhas ja descompactadas."""
    aceitos = set(bdi_codes)
    saida: list[dict] = []
    for linha in linhas:
        if len(linha) < _COLUNAS_MINIMAS or linha[0:2] != "01":
            continue
        bdi, mercado = linha[10:12], linha[24:27]
        ticker = linha[12:24].strip().upper()
        if not ticker:
            continue
        if aceitos and bdi not in aceitos:
            continue
        if tpmerc and mercado != tpmerc:
            continue
        try:
            data = datetime.strptime(linha[2:10], "%Y%m%d").date().isoformat()
        except ValueError:
            continue
        saida.append({
            "ticker": ticker,
            "trade_date": data,
            "bdi": bdi,
            "issuer_short_name": linha[27:39].strip() or None,
            "specification": linha[39:49].strip() or None,
            "isin": linha[230:242].strip() or None,
            "open": _dinheiro(linha, 56, 69),
            "high": _dinheiro(linha, 69, 82),
            "low": _dinheiro(linha, 82, 95),
            "average": _dinheiro(linha, 95, 108),
            "close": _dinheiro(linha, 108, 121),
            "trades": _inteiro(linha, 147, 152),
            "quantity": _inteiro(linha, 152, 170),
            "financial_volume": _dinheiro(linha, 170, 188),
            "fator_cotacao": _inteiro(linha, 210, 217),
        })
    return saida
