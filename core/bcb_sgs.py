"""Reserva do BCB/SGS: o web service SOAP legado em ``www3.bcb.gov.br``.

Em 03/10/2026 o host da API REST (``api.bcb.gov.br``) deixou de existir no DNS
autoritativo do BCB -- NXDOMAIN pelo 8.8.8.8, pelo 1.1.1.1, daqui e dos EUA. O
``update_bcb`` do GitHub Actions, que até 02/10 gravava os 17 anos de
``public.macro``, passou a voltar com 0 pontos em todas as séries. O SOAP
(``FachadaWSSGS.getValoresSeriesXML``) continuou respondendo, de dentro e de
fora do Brasil, e devolve 2010-2026 de uma série diária numa chamada só.

A API REST continua sendo a primeira tentativa (é a documentada); esta é a
reserva de quem a consome: ``scripts.seed_macro_bcb`` e
``core.rentabilidade.baixar_cdi_bcb``.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import date

logger = logging.getLogger(__name__)

URL_SOAP = "https://www3.bcb.gov.br/wssgs/services/FachadaWSSGS"

_ENVELOPE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
    'xmlns:pub="http://publico.ws.casosdeuso.sgs.pec.bcb.gov.br">'
    "<soapenv:Body><pub:getValoresSeriesXML>"
    "<codigosSeries><item>{codigo}</item></codigosSeries>"
    "<dataInicio>{ini}</dataInicio><dataFim>{fim}</dataFim>"
    "</pub:getValoresSeriesXML></soapenv:Body></soapenv:Envelope>"
)

_ITEM = re.compile(r"<DATA>\s*([^<]*?)\s*</DATA>\s*<VALOR>\s*([^<]*?)\s*</VALOR>", re.S)
_FAULT = re.compile(r"<faultstring>(.*?)</faultstring>", re.S)


def _data(texto: str) -> date:
    """``D/M/AAAA`` (diária) ou ``M/AAAA`` (mensal, vira o dia 1 como na REST)."""
    partes = [int(p) for p in texto.split("/")]
    if len(partes) == 3:
        return date(partes[2], partes[1], partes[0])
    if len(partes) == 2:
        return date(partes[1], partes[0], 1)
    raise ValueError(texto)


def parse_sgs_xml(resposta: str) -> dict[date, float]:
    """Pontos de ``getValoresSeriesXML``. Ignora item sem data ou valor legível.

    O XML das séries vem escapado dentro de uma string SOAP; ``html.unescape``
    o devolve à forma ``<ITEM><DATA>..</DATA><VALOR>..</VALOR>``.
    """
    out: dict[date, float] = {}
    for bruto_data, bruto_valor in _ITEM.findall(html.unescape(resposta or "")):
        try:
            out[_data(bruto_data)] = float(bruto_valor.replace(",", "."))
        except ValueError:
            continue
    return out


def baixar_sgs_soap(codigo: int, inicio: date, fim: date,
                    timeout: float = 60.0) -> tuple[dict[date, float], str | None]:
    """Série ``codigo`` entre ``inicio`` e ``fim`` pelo SOAP.

    Devolve ``(serie, motivo)``: ``motivo`` é ``None`` quando a chamada
    respondeu, mesmo sem pontos no intervalo; senão diz por que falhou.
    """
    import requests

    corpo = _ENVELOPE.format(codigo=int(codigo), ini=f"{inicio:%d/%m/%Y}",
                             fim=f"{fim:%d/%m/%Y}")
    try:
        r = requests.post(URL_SOAP, data=corpo.encode("utf-8"), timeout=timeout,
                          headers={"Content-Type": "text/xml; charset=utf-8",
                                   "SOAPAction": ""})
    except Exception as exc:  # noqa: BLE001 -- motivo vai para quem chamou
        logger.warning("[bcb_sgs] SOAP %s não respondeu (%s).", codigo, type(exc).__name__)
        return {}, f"o SOAP do SGS não respondeu ({type(exc).__name__})"
    falha = _FAULT.search(r.text or "")
    if falha:
        return {}, f"o SOAP do SGS recusou a série {codigo}: {falha.group(1).strip()[:200]}"
    if not r.ok:
        return {}, f"o SOAP do SGS respondeu HTTP {r.status_code}"
    return parse_sgs_xml(r.text), None
