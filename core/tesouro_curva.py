"""
core/tesouro_curva.py — leitura da curva oficial do Tesouro Direto.

Camada fina sobre `tesouro_market_rates`, alimentada por
`data_pipeline/jobs/update_tesouro_curva.py`. Existe para que a view não
escreva SQL e, principalmente, para concentrar **duas convenções** que erram
em silêncio quando espalhadas:

1. **Unidade.** O CSV do Tesouro publica taxa em **por cento ao ano** ('13,52'
   é 13,52%). `core.tesouro_mtm` trabalha em **decimal** (0,1352). A conversão
   acontece aqui, uma vez, e a coluna decimal sai com sufixo `_dec`. Misturar
   as duas não levanta exceção: produz um MtM absurdo que parece cálculo.

2. **Ponta.** Quem **já tem** o título e cogita sair marca pela taxa de
   **venda** (`sell_rate` — o Tesouro recompra). Quem cogita **entrar** numa
   alternativa paga a taxa de **compra** (`buy_rate`). São taxas diferentes, e
   a diferença é o spread que o veredito precisa descontar. Por isso as duas
   funções abaixo têm nomes que dizem a ponta, e não um genérico `taxa()`.
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd
from sqlalchemy import text

logger = logging.getLogger(__name__)

_COLUNAS = (
    "security_key, title_name, maturity_date, base_date, "
    "buy_rate, sell_rate, buy_pu, sell_pu, base_pu"
)


def _vazio() -> pd.DataFrame:
    return pd.DataFrame(columns=[c.strip() for c in _COLUNAS.split(",")] +
                        ["buy_rate_dec", "sell_rate_dec"])


def _decimalizar(df: pd.DataFrame) -> pd.DataFrame:
    """Acrescenta as colunas decimais exigidas por `core.tesouro_mtm`."""
    for origem, destino in (("buy_rate", "buy_rate_dec"), ("sell_rate", "sell_rate_dec")):
        if origem in df.columns:
            df[destino] = pd.to_numeric(df[origem], errors="coerce") / 100.0
    return df


def _consultar(engine, sql: str, params: dict) -> pd.DataFrame:
    if engine is None:
        return _vazio()
    try:
        with engine.connect() as conn:
            df = pd.read_sql(text(sql), conn, params=params)
    except Exception as exc:
        # Tabela ausente é o caso normal antes da primeira execução do job.
        logger.info("tesouro_curva: consulta falhou (%s)", exc)
        return _vazio()
    if df.empty:
        return _vazio()
    return _decimalizar(df)


def curva_disponivel(engine) -> date | None:
    """Data-base mais recente presente na tabela, ou ``None`` se não há curva.

    A tela usa isto para dizer de quando é a marcação — número de mercado sem
    data é o mesmo problema do preço parado que já mordeu a B3 neste projeto.
    """
    if engine is None:
        return None
    try:
        with engine.connect() as conn:
            valor = conn.execute(
                text("SELECT MAX(base_date) FROM tesouro_market_rates")
            ).scalar()
    except Exception:
        return None
    if valor is None:
        return None
    return valor if isinstance(valor, date) else pd.to_datetime(valor).date()


def taxas_mais_recentes(engine, security_keys: list[str] | None = None,
                        ate: date | None = None) -> pd.DataFrame:
    """Última cotação de cada título (opcionalmente até uma data-base).

    Uma linha por `security_key`. É a base tanto da marcação (ponta de venda)
    quanto da lista de alternativas (ponta de compra).
    """
    filtros = []
    params: dict = {}
    if security_keys:
        filtros.append("security_key = ANY(:chaves)")
        params["chaves"] = list(security_keys)
    if ate is not None:
        filtros.append("base_date <= :ate")
        params["ate"] = ate
    onde = ("WHERE " + " AND ".join(filtros)) if filtros else ""

    sql = f"""
        SELECT DISTINCT ON (security_key) {_COLUNAS}
        FROM tesouro_market_rates
        {onde}
        ORDER BY security_key, base_date DESC
    """
    return _consultar(engine, sql, params)


def serie_titulo(engine, security_key: str, desde: date | None = None) -> pd.DataFrame:
    """Trajetória diária de um título, em ordem cronológica."""
    params: dict = {"chave": security_key}
    onde = "WHERE security_key = :chave"
    if desde is not None:
        onde += " AND base_date >= :desde"
        params["desde"] = desde

    sql = f"""
        SELECT {_COLUNAS}
        FROM tesouro_market_rates
        {onde}
        ORDER BY base_date
    """
    return _consultar(engine, sql, params)


def titulos_ofertados(engine, ate: date | None = None) -> pd.DataFrame:
    """Títulos com cotação recente — o cardápio de alternativas.

    Devolve a ponta de **compra**, que é o que o investidor pagaria para
    entrar. Ainda inclui títulos já vencidos ou fora de oferta se a janela
    guardada os alcançar; quem monta o seletor deve filtrar por vencimento
    futuro, e não presumir que a tabela só tem título vivo.
    """
    df = taxas_mais_recentes(engine, ate=ate)
    if df.empty:
        return df
    return df.sort_values(["title_name", "maturity_date"]).reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# Índice implícito
#
# Título indexado publica **spread**, não taxa cheia: um Tesouro Selic 2031 sai
# a 0,08% ao ano — que é o ágio sobre a Selic, não o rendimento. Capitalizar o
# spread sozinho faria qualquer alternativa prefixada parecer imbatível, e o
# erro não levanta exceção nenhuma: sai como veredito confiante.
#
# O que falta é o índice, e o próprio cardápio do Tesouro o precifica:
#   • Selic futura ≈ taxa do prefixado de vencimento mais próximo;
#   • inflação implícita = (1 + pré) / (1 + real) − 1, o breakeven clássico.
# Quando o par não existe, a resposta é ``None`` — e o veredito fica sem base,
# que é melhor que comparar spread com taxa cheia fingindo que são a mesma
# grandeza.
# ─────────────────────────────────────────────────────────────────────────────

_ZERO_PRE = "tesouro prefixado"
_ZERO_IPCA = "tesouro ipca"
_CUPOM = "juros semestrais"


_INDEXADOR_POR_NOME = (
    ("tesouro selic", "SELIC"),
    ("tesouro ipca", "IPCA"),
    ("tesouro igpm", "IGPM"),
    ("tesouro igp-m", "IGPM"),
    ("tesouro educa", "IPCA"),
    ("tesouro renda", "IPCA"),
    ("tesouro prefixado", "PRE"),
)


def indexador_do_titulo(nome: str) -> str:
    """Indexador a partir do nome que o Tesouro publica.

    Existe aqui, e não na tela, porque é quem decide **quais alternativas
    podem ser comparadas**: as duas pernas precisam ser do mesmo indexador,
    senão o motor soma um ágio de Selic com uma taxa cheia de prefixado. Errar
    o mapeamento não levanta exceção — muda o cardápio e o veredito.

    Educa+ e Renda+ são IPCA+ com nome comercial diferente; deixá-los cair no
    ``PRE`` do fim os colocaria no cardápio errado.
    """
    baixo = str(nome or "").lower().strip()
    for prefixo, idx in _INDEXADOR_POR_NOME:
        if baixo.startswith(prefixo):
            return idx
    return "PRE"


def _mid(linha) -> float | None:
    """Taxa média entre as duas pontas — projeção de índice não tem lado."""
    compra, venda = linha.get("buy_rate_dec"), linha.get("sell_rate_dec")
    valores = [v for v in (compra, venda) if v is not None and pd.notna(v)]
    if not valores:
        return None
    return float(sum(valores) / len(valores))


def _mais_proximo(cotacoes: pd.DataFrame, prefixo: str, vencimento: date) -> float | None:
    """Taxa do zero-cupom daquela família com vencimento mais perto de ``vencimento``."""
    if cotacoes is None or cotacoes.empty or "title_name" not in cotacoes.columns:
        return None
    nomes = cotacoes["title_name"].astype(str).str.lower()
    elegiveis = cotacoes[nomes.str.startswith(prefixo) & ~nomes.str.contains(_CUPOM)]
    if elegiveis.empty:
        return None
    registros = elegiveis.to_dict("records")
    registros.sort(key=lambda r: abs((r["maturity_date"] - vencimento).days))
    for linha in registros:
        taxa = _mid(linha)
        if taxa is not None:
            return taxa
    return None


def indice_implicito(cotacoes: pd.DataFrame, indexador: str,
                     vencimento: date) -> float | None:
    """Índice anual que as duas pernas da comparação recebem por igual.

    Função pura: recebe o quadro já lido de `taxas_mais_recentes`. ``PRE``
    devolve 0,0 — a taxa do prefixado já é cheia, somar índice contaria o
    rendimento duas vezes.
    """
    idx = (indexador or "").upper()
    if idx == "PRE":
        return 0.0
    pre = _mais_proximo(cotacoes, _ZERO_PRE, vencimento)
    if pre is None:
        return None
    if idx == "SELIC":
        return pre
    if idx == "IPCA":
        real = _mais_proximo(cotacoes, _ZERO_IPCA, vencimento)
        if real is None or real <= -1.0:
            return None
        return (1.0 + pre) / (1.0 + real) - 1.0
    # IGP-M e o que mais aparecer: o Tesouro não oferta par para inferir o
    # índice, e chutar aqui seria inventar o número que decide a venda.
    return None


def taxa_indice_implicita(engine, indexador: str, vencimento: date,
                          ate: date | None = None) -> float | None:
    """Versão que lê o banco. Ver `indice_implicito` para a regra."""
    if (indexador or "").upper() == "PRE":
        return 0.0
    return indice_implicito(taxas_mais_recentes(engine, ate=ate), indexador, vencimento)
