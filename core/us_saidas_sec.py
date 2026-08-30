# -*- coding: utf-8 -*-
"""Confirmar a saida e nomear quem saiu, com a evidencia da propria SEC.

`market_us.delistings` tem 12.107 linhas e **duas** delas trazem simbolo. Sem
simbolo a saida nao encontra preco, nao encontra safra e nao entra no backtest:
ela existe como contagem e nao como observacao. O painel continua 100%
sobrevivente com o registro da mortalidade ao lado, sem se tocar.

Duas coisas sao feitas aqui, e a ordem importa:

1. **refutar** a saida antes de nomea-la. A derivacao le `form.idx` e so conta
   como relatorio anual as formas de `FORMAS_RELATORIO_ANUAL_IDX`, comparadas
   por igualdade exata. `10-K/A`, `10-KT` e `40-F` ficam de fora dessa
   igualdade -- e um emissor canadense sob o MJDS, que arquiva 40-F a vida
   inteira e nunca um 10-K, aparece ausente em TODOS os anos da janela. Medido
   em 250 saidas sorteadas, 2,0% arquivaram relatorio anual em ano igual ou
   posterior ao da ausencia (10-K/A, 10-KT/A, 20-F/A). Morte inventada e pior
   que morte nao registrada: entra no painel como perda que nunca houve;
2. **nomear** o que sobreviveu a refutacao. O simbolo sai de `dei:TradingSymbol`
   na capa em XBRL inline do ultimo relatorio anual -- e NAO do campo `tickers`
   do `submissions.json`, que a SEC esvazia quando a empresa para de arquivar.
   Em 12 saidas checadas a mao, `tickers` veio vazio em 11; a unica preenchida
   era justamente a empresa viva. Resolver por ali nomearia so quem nao morreu,
   o mesmo vies que este trabalho existe para desfazer.

Este modulo nao faz rede. Ele recebe o JSON de `submissions` e o texto do
documento, e devolve decisao -- para que o teste exercite a regra sem depender
da SEC estar no ar.
"""
from __future__ import annotations

import re

#: Prefixos de relatorio anual, ja incluindo emenda (`/A`) e transicao (`T`).
#: `40-F` esta aqui e nao em `FORMAS_RELATORIO_ANUAL_IDX` por acidente: o
#: emissor MJDS arquiva 40-F e nada mais.
FORMAS_ANUAIS = ("10-K", "10-KSB", "10-K405", "10-KT", "20-F", "40-F")

#: Simbolo de bolsa plausivel: letras, com sufixo de classe opcional. Recusa
#: `AXP/21` (serie de divida da American Express Credit Corp, colhida na capa
#: de um 10-K real) e recusa texto de formulario como "None" ou "N/A".
_RX_SIMBOLO = re.compile(r"^[A-Z]{1,5}(?:[.-][A-Z]{1,2})?$")
_NAO_SIMBOLO = {"NONE", "N/A", "NA", "NOTAPPLICABLE", "NOT APPLICABLE", "-"}

#: A capa em XBRL inline marca o simbolo como fato nao numerico. O valor pode
#: vir cercado de `<span>`, entao o corpo e capturado inteiro e limpo depois.
_RX_FATO = re.compile(
    r"""<(?P<tag>ix:nonNumeric|ix:nonnumeric)\b[^>]*
        name\s*=\s*["']dei:TradingSymbol["'][^>]*>
        (?P<corpo>.*?)</(?P=tag)>""",
    re.IGNORECASE | re.DOTALL | re.VERBOSE)
_RX_TAGS = re.compile(r"<[^>]*>")


def e_relatorio_anual(forma: str) -> bool:
    """A forma e um relatorio anual, incluindo emenda e relatorio de transicao."""
    return str(forma or "").strip().upper().startswith(FORMAS_ANUAIS)


def filiais_anuais(submissions: dict) -> list[dict]:
    """Relatorios anuais de `submissions.json`, do mais recente para o mais antigo.

    `filings.recent` e um dicionario de listas paralelas. Percorrer por indice
    e obrigatorio: qualquer reordenacao independente de uma das listas juntaria
    a forma de uma linha com a data de outra.
    """
    recentes = ((submissions or {}).get("filings") or {}).get("recent") or {}
    formas = recentes.get("form") or []
    datas = recentes.get("filingDate") or []
    acessos = recentes.get("accessionNumber") or []
    docs = recentes.get("primaryDocument") or []
    saida: list[dict] = []
    for i, forma in enumerate(formas):
        if not e_relatorio_anual(forma):
            continue
        saida.append({
            "forma": str(forma).strip(),
            "data": datas[i] if i < len(datas) else None,
            "acesso": acessos[i] if i < len(acessos) else None,
            "documento": docs[i] if i < len(docs) else None,
        })
    saida.sort(key=lambda f: f["data"] or "", reverse=True)
    return saida


def refuta_saida(submissions: dict, absence_year: int) -> dict | None:
    """Evidencia de vida em ano igual ou posterior ao da ausencia, se houver.

    Igual e nao apenas posterior: `absence_year` e o primeiro ano SEM relatorio
    anual segundo o indice. Um relatorio arquivado nesse mesmo ano contradiz a
    premissa que gerou a linha.
    """
    try:
        limite = int(absence_year)
    except (TypeError, ValueError):
        return None
    for f in filiais_anuais(submissions):
        data = str(f.get("data") or "")
        if len(data) >= 4 and data[:4].isdigit() and int(data[:4]) >= limite:
            return {"forma": f["forma"], "data": data}
    return None


def simbolo_plausivel(bruto) -> str | None:
    """Normaliza o texto da capa, ou devolve None se aquilo nao e um ticker."""
    texto = _RX_TAGS.sub("", str(bruto or ""))
    texto = texto.replace("&nbsp;", " ").replace("\xa0", " ").strip().upper()
    texto = texto.strip(" \t\r\n.,;:")
    if not texto or texto in _NAO_SIMBOLO:
        return None
    return texto if _RX_SIMBOLO.match(texto) else None


def extrair_trading_symbol(documento: str) -> str | None:
    """`dei:TradingSymbol` da capa em XBRL inline do relatorio anual.

    Uma capa com varias classes listadas repete o fato, uma vez por classe. O
    primeiro em ordem de documento e a classe ordinaria em todos os casos
    inspecionados, e e o unico que interessa: a serie preferencial ou de
    divida nao e o papel cujo retorno o backtest mede.
    """
    for m in _RX_FATO.finditer(str(documento or "")):
        simbolo = simbolo_plausivel(m.group("corpo"))
        if simbolo:
            return simbolo
    return None
