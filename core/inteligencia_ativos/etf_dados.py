"""
core/inteligencia_ativos/etf_dados.py
Dados de ETF (taxa, composição, setores, liquidez) a partir da vitrine
``data/public/etf_dados.json.gz``. Módulo puro: quem baixa é
``scripts/publish_etf_dados.py``, na máquina local; quem lê é
``fontes_fundamentos.dados_etf``.

Antes disto, ``dados_etf`` devolvia ``{}`` e os oito indicadores de ETF da
tela saíam "Dado não disponível." para SPY, IEFA, SGOV e companhia -- e a LLM
dizia não ter informação sobre ETF, com razão.

A fonte é o yfinance (``Ticker.info`` e ``Ticker.funds_data``), a mesma
biblioteca que o projeto já usa para cotação. O que ela não tem continua
ausente, e não estimado:

* **tracking error**: exige a série do índice ao lado da do fundo;
* **exposição geográfica**: o yfinance não abre o fundo por país. O que vai no
  campo é a CATEGORIA do fundo ("Foreign Large Blend"), com nota dizendo que
  não é composição por país -- é a melhor pista que existe, e a nota impede
  que ela seja lida como medida.
"""
from __future__ import annotations

import re

from core.inteligencia_ativos.fundamentos import Dado

ARQUIVO = "data/public/etf_dados.json.gz"
FONTE = "yfinance (Ticker.info e funds_data)"
TOP_N = 10

# O verbo de replicação ("track", "correspond", "replicate") e, logo depois,
# a primeira sequência de palavras com inicial maiúscula terminada em Index:
# "...correspond generally to the price and yield performance of the S&P 500
# Index" -> "S&P 500 Index". Tokens com ponto ("U.S.") entram na sequência.
_VERBO = re.compile(r"\b(?:track|correspond|replicat)\w*", re.IGNORECASE)
_NOME_INDICE = re.compile(
    r"(?P<indice>(?:[A-Z0-9][\w&.\-/']*\s+){1,8}(?:Index|Indices)\b)")


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def indice_da_descricao(texto: str | None) -> str | None:
    """Índice de referência citado na descrição do fundo, ou ``None``. Puro."""
    texto = str(texto or "")
    verbo = _VERBO.search(texto)
    if not verbo:
        return None
    m = _NOME_INDICE.search(texto[verbo.end():verbo.end() + 240])
    if not m:
        return None
    indice = " ".join(m.group("indice").split())
    return indice if len(indice) <= 90 else None


def expense_ratio_pct(operacoes: dict, info: dict) -> float | None:
    """Taxa de administração em % (0,09 = 0,09% a.a.).

    As duas fontes do yfinance usam unidades diferentes: o relatório anual em
    ``funds_data.fund_operations`` vem em FRAÇÃO (0.000945 no SPY) e
    ``info["netExpenseRatio"]`` já vem em PERCENTUAL (0.0945). Somar ou trocar
    uma pela outra sem converter erra por 100x -- por isso a conversão mora
    aqui, num lugar só.
    """
    fracao = _num((operacoes or {}).get("Annual Report Expense Ratio"))
    if fracao is not None and 0 <= fracao < 0.2:
        return round(fracao * 100.0, 4)
    pct = _num((info or {}).get("netExpenseRatio"))
    if pct is not None and 0 <= pct < 20:
        return round(pct, 4)
    return None


def extrair(simbolo: str, info: dict, fundos: dict, *, gerado_em: str) -> dict:
    """Registro compacto de um ETF a partir do que o yfinance devolveu. Puro.

    ``fundos`` já vem em tipos simples (o publicador converte os DataFrames):
    ``descricao``, ``visao`` (fund_overview), ``operacoes`` (fund_operations,
    coluna do próprio fundo), ``top`` ([{simbolo, nome, peso}] com peso em
    fração), ``setores`` e ``classes`` ({nome: fração}).
    """
    info, fundos = info or {}, fundos or {}
    visao = fundos.get("visao") or {}
    descricao = fundos.get("descricao") or info.get("longBusinessSummary")
    top = [t for t in (fundos.get("top") or []) if _num(t.get("peso")) is not None]
    top.sort(key=lambda t: -float(t["peso"]))
    top = top[:TOP_N]
    setores = {k: round(float(v), 4) for k, v in (fundos.get("setores") or {}).items()
               if _num(v) is not None and float(v) > 0}
    classes = {k: round(float(v), 4) for k, v in (fundos.get("classes") or {}).items()
               if _num(v) is not None and float(v) > 0}
    return {
        "simbolo": str(simbolo).upper(),
        "nome": info.get("longName") or info.get("shortName"),
        "indice": indice_da_descricao(descricao),
        "categoria": visao.get("categoryName") or info.get("category"),
        "familia": visao.get("family") or info.get("fundFamily"),
        "expense_ratio_pct": expense_ratio_pct(fundos.get("operacoes") or {}, info),
        "patrimonio": _num(info.get("totalAssets")),
        "volume_medio": _num(info.get("averageVolume")),
        "moeda": info.get("currency"),
        "dividend_yield_pct": (round(_num(info.get("yield")) * 100, 4)
                               if _num(info.get("yield")) is not None else None),
        "top": [{"simbolo": t.get("simbolo"), "nome": t.get("nome"),
                 "peso": round(float(t["peso"]), 4)} for t in top],
        "setores": setores,
        "classes": classes,
        "gerado_em": gerado_em,
    }


def _bi(x: float | None, moeda: str | None) -> str | None:
    if x is None:
        return None
    simbolo = "US$" if (moeda or "USD").upper() == "USD" else (moeda or "")
    if x >= 1e9:
        return f"{simbolo} {x / 1e9:.1f} bi".replace(".", ",")
    return f"{simbolo} {x / 1e6:.0f} mi"


def _lista_pct(pares, n: int = 5) -> str:
    return ", ".join(f"{nome} {peso * 100:.1f}%".replace(".", ",")
                     for nome, peso in pares[:n])


def dados_de(registro: dict | None) -> dict:
    """Registro da vitrine → indicadores da tela (``Dado`` por chave). Puro.

    Só entra o que existe; chave ausente vira "Dado não disponível." no
    ``fundamentos.montar``, que é o mesmo caminho das outras classes."""
    if not registro:
        return {}
    ref = registro.get("gerado_em")
    saida: dict[str, Dado] = {}

    def dado(chave, valor, nota=None):
        if valor is not None and valor != "":
            saida[chave] = Dado(valor, FONTE, ref, nota)

    if registro.get("indice"):
        dado("indice", registro["indice"], "extraído da descrição do fundo")
    elif registro.get("categoria"):
        dado("indice", f"Categoria {registro['categoria']}",
             "a descrição do fundo não cita o índice; vai a categoria")
    top = registro.get("top") or []
    if top:
        dado("composicao", "Maiores posições: " + _lista_pct(
            [(t.get("simbolo") or t.get("nome"), t["peso"]) for t in top]))
        soma = sum(t["peso"] for t in top)
        dado("concentracao",
             f"{len(top)} maiores posições = {soma * 100:.1f}% do fundo".replace(".", ","))
    elif registro.get("classes"):
        dado("composicao", "Classes de ativo: " + _lista_pct(
            sorted(registro["classes"].items(), key=lambda kv: -kv[1])))
    dado("expense_ratio", registro.get("expense_ratio_pct"))
    partes = []
    patrimonio = _bi(registro.get("patrimonio"), registro.get("moeda"))
    if patrimonio:
        partes.append(f"patrimônio {patrimonio}")
    if registro.get("volume_medio"):
        partes.append(f"volume médio {registro['volume_medio'] / 1e6:.1f} mi "
                      "de cotas/dia".replace(".", ","))
    dado("liquidez", "; ".join(partes) or None)
    if registro.get("categoria"):
        dado("exposicao_geografica", f"Categoria {registro['categoria']}",
             "categoria do fundo, não composição por país (o yfinance não a abre)")
    setores = sorted((registro.get("setores") or {}).items(), key=lambda kv: -kv[1])
    if setores:
        dado("exposicao_setorial", _lista_pct(
            [(k.replace("_", " ").title(), v) for k, v in setores]))
    return saida


def simbolos_das_posicoes(registro: dict | None) -> list[str]:
    """Tickers das maiores posições, para o fallback de notícias. Puro."""
    return [str(t["simbolo"]).upper() for t in (registro or {}).get("top") or []
            if t.get("simbolo")]
