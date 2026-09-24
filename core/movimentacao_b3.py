"""O que cada linha da Movimentação da B3 faz com a quantidade e com o caixa.

O importador guarda a linha crua em ``investment_movement_events``; este
módulo interpreta. Separar as duas coisas é o que permite corrigir uma regra
sem pedir o arquivo de novo -- a B3 não documenta o layout, e o que está aqui
foi inferido dos rótulos que ela publica.

A regra que protege o resto: **errar a interpretação tem de custar cobertura,
nunca um número errado.** A conciliação em ``core.rentabilidade`` só aceita um
ativo cuja quantidade feche com a posição; se uma regra daqui contar em dobro
ou esquecer um lado, a quantidade não fecha e o ativo sai da medição, com
motivo. O único erro que a quantidade NÃO pega é o de caixa -- quantidade
certa com dinheiro faltando --, e por isso todo evento que traz cotas com
custo que o extrato não publica bloqueia o ativo em vez de entrar de graça.

Rótulos tratados (minúsculos, como o importador grava):

* sem caixa, sinal pelo sentido (crédito soma, débito subtrai):
  bonificação em ativos, desdobro, grupamento, fração em ativos;
* transferência de custódia (``transferência``): sem caixa; se a soma do
  ativo ficar positiva, entrou papel de fora com custo desconhecido e o ativo
  é bloqueado. Troca de corretora (débito numa, crédito noutra) soma zero;
* recibo de subscrição: cotas novas COM caixa (o valor da operação). O recibo
  de FII (sufixo 12 a 15) vira a cota ``XXXX11``. Sem valor publicado, o ativo
  é bloqueado -- entraria cota sem o dinheiro que a pagou;
* leilão de fração: só caixa (a venda das frações que a fração em ativos tirou);
* incorporação, cisão, conversão: cotas trocam de código sem custo ligado --
  quem recebe é bloqueado.

Ignorados de propósito: compra, venda e transferência - liquidação (o arquivo
Negociação é a fonte das negociações; contar aqui seria dobrar), proventos
(já vão para ``dividends``), direitos de subscrição (o direito não é posição;
a venda dele já é provento), empréstimo e atualização. A atualização é o
passo em que o recibo vira cota; contá-la junto com o recibo dobraria, e
ignorá-la no caso em que ela seria a única pista só custa cobertura.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

SEM_CAIXA = frozenset({
    "bonificação em ativos",
    "desdobro",
    "grupamento",
    "fração em ativos",
})
TRANSFERENCIA = "transferência"
RECIBO = "recibo de subscrição"
LEILAO_FRACAO = "leilão de fração"
TROCA_DE_CODIGO = frozenset({"incorporação", "cisão", "conversão"})

MOTIVO_SUBSCRICAO_SEM_VALOR = "subscrição sem valor no extrato da B3"
MOTIVO_TRANSFERENCIA = "entrou por transferência de custódia, sem custo conhecido"
MOTIVO_TROCA_DE_CODIGO = "recebeu cotas por incorporação ou troca de código, sem custo ligado"

_RX_RECIBO_FII = re.compile(r"^([A-Z]{3}[A-Z0-9])1[2-5]$")


def _sinal(direcao: str) -> float:
    d = (direcao or "").strip().lower()
    if d.startswith("cred") or d.startswith("créd") or d.startswith("entrada"):
        return 1.0
    if d.startswith("deb") or d.startswith("déb") or d.startswith("saida") or d.startswith("saída"):
        return -1.0
    return 0.0


def _num(v: Any) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def ticker_da_cota(ticker: str) -> str:
    """Recibo de subscrição de FII (``ABCD13``) → a cota (``ABCD11``)."""
    t = (ticker or "").strip().upper()
    m = _RX_RECIBO_FII.match(t)
    return f"{m.group(1)}11" if m else t


def interpretar(eventos: list[dict]) -> dict:
    """Converte as linhas cruas em ajustes por ativo.

    eventos: {data, ticker, movimento, sentido, quantidade, valor}
      (ticker já normalizado pelo chamador para a forma-base, sem o ``F``).

    Devolve:
      ajustes:    [{data, ticker, delta_qtd, caixa}] -- caixa negativo é
                  dinheiro que saiu do investidor, como numa compra;
      bloqueados: {ticker: motivo} -- ativos que a medição não pode aceitar;
      subscritos: tickers que receberam cota paga (contam como compra).
    """
    ajustes: list[dict] = []
    bloqueados: dict[str, str] = {}
    subscritos: set[str] = set()
    transferido: dict[str, float] = {}

    for ev in eventos:
        mov = (ev.get("movimento") or "").strip().lower()
        tk = (ev.get("ticker") or "").strip().upper()
        d: date | None = ev.get("data")
        if not mov or not tk or d is None:
            continue
        s = _sinal(ev.get("sentido") or "")
        q = abs(_num(ev.get("quantidade")))
        valor = abs(_num(ev.get("valor")))

        if mov in SEM_CAIXA:
            if s and q:
                ajustes.append({"data": d, "ticker": tk, "delta_qtd": s * q, "caixa": 0.0})
        elif mov == TRANSFERENCIA:
            if s and q:
                ajustes.append({"data": d, "ticker": tk, "delta_qtd": s * q, "caixa": 0.0})
                transferido[tk] = transferido.get(tk, 0.0) + s * q
        elif mov == RECIBO:
            if s <= 0 or not q:
                continue
            cota = ticker_da_cota(tk)
            ajustes.append({"data": d, "ticker": cota, "delta_qtd": q, "caixa": -valor})
            subscritos.add(cota)
            if valor <= 0:
                bloqueados.setdefault(cota, MOTIVO_SUBSCRICAO_SEM_VALOR)
        elif mov == LEILAO_FRACAO:
            if valor > 0:
                ajustes.append({"data": d, "ticker": tk, "delta_qtd": 0.0, "caixa": valor})
        elif mov in TROCA_DE_CODIGO:
            if s and q:
                ajustes.append({"data": d, "ticker": tk, "delta_qtd": s * q, "caixa": 0.0})
                if s > 0:
                    bloqueados.setdefault(tk, MOTIVO_TROCA_DE_CODIGO)

    for tk, liq in transferido.items():
        if liq > 1e-6:
            bloqueados.setdefault(tk, MOTIVO_TRANSFERENCIA)

    return {"ajustes": ajustes, "bloqueados": bloqueados, "subscritos": subscritos}
