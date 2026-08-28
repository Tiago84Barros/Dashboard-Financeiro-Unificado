# -*- coding: utf-8 -*-
"""Apura, no relatorio anual, se a companhia e REIT por eleicao fiscal (A-156).

Por que nao pelo SIC: medido em 27/08/2026, os 22 ativos que o cadastro rotula
"Real Estate" generico chegam TODOS com SIC 6500 -- a Innovative Industrial
Properties, REIT declarado, e a Forestar Group, incorporadora, com o mesmo
codigo. O SIC diz o setor; nao diz a estrutura. Era a hipotese obvia, e gravar
o SIC teria custado uma coluna e uma varredura para nao mudar nenhuma decisao.

O que separa e a eleicao fiscal. Ser REIT nao e rotulo de cadastro: e um regime
tributario que a companhia elege e declara no proprio 10-K. Nos 21 documentos
medidos a separacao foi limpa -- 12 declaram, 9 nao -- e bate com a leitura
humana.

Tres armadilhas cobradas na calibragem, todas com nome proprio:

1. **Hipotese nao e eleicao.** A Belpointe PREP escreve "*se* elegermos ser
   tributados como sociedade, *poderemos* eleger qualificar como REIT". Um
   padrao ingenuo lia isso como eleicao e tirava do universo uma sociedade
   operacional. Dai a exigencia de verbo em forma realizada e o descarte de
   frase condicional.
2. **Eleicao passada nao e status atual.** A Seritage "*havia previamente*
   elegido" -- e revogou em 2022, virando C-corp tributavel. E o mesmo defeito
   que o N-54C corrigiu no A-147: o documento diz o que a empresa FOI.
3. **Ausencia de leitura nao e ausencia de eleicao.** Sem documento, o veredito
   e None, e quem consome mantem a exclusao. Ver `core.us_instrumento`.
"""
from __future__ import annotations

import logging
import re
from typing import Callable

from core.us_instrumento import ELEICAO_REIT_AUSENTE, ELEICAO_REIT_DECLARADA

logger = logging.getLogger(__name__)

FORMAS_RELATORIO_ANUAL = ("10-K", "20-F", "40-F")

# Eleicao em forma realizada ("elegemos", "temos elegido", "pretendemos
# qualificar"). A mencao de passagem a REIT de terceiro nao casa: o verbo tem
# de ter a propria companhia por sujeito.
ELEICAO_REIT = re.compile(
    r"(?:we|company|trust|registrant|it)\s+(?:ha(?:ve|s)|had)\s+"
    r"(?:previously\s+|initially\s+|also\s+)?"
    r"(?:elected|qualified|operated|been\s+(?:organized|taxed))"
    r"[^.;]{0,160}?(?:real estate investment trust|\bREIT\b)"
    r"|elected\s+to\s+be\s+(?:taxed|treated)\s+as\s+a\s+"
    r"(?:real estate investment trust|REIT)"
    r"|(?:we|company)\s+intends?\s+to\s+(?:elect|continue\s+to\s+qualify|qualify)"
    r"[^.;]{0,120}?(?:real estate investment trust|\bREIT\b)",
    re.IGNORECASE)

# Revogacao da propria eleicao. Sem o sujeito possessivo, "REIT status would
# terminate" (fator de risco) contaria como revogacao consumada.
REVOGACAO_REIT = re.compile(
    r"(?:revoked|terminated|elected\s+to\s+terminate|approved\s+a\s+plan\s+to\s+"
    r"terminate)\s+(?:its|our|the\s+(?:company|trust)(?:&#8217;s|'s|\u2019s)?)\s*"
    r"REIT\s+(?:election|status)", re.IGNORECASE)

# Marcadores de hipotese. Presentes na frase, ela deixa de afirmar o fato.
CONDICIONAL = re.compile(
    r"\b(?:if|may|might|could|would|should we|in the event)\b", re.IGNORECASE)


def url_relatorio_anual(sub: dict | None) -> str | None:
    """URL do documento principal do relatorio anual mais recente, ou None."""
    sub = sub or {}
    cik = str(sub.get("cik") or "").strip()
    recentes = (sub.get("filings", {}) or {}).get("recent", {}) or {}
    formas = recentes.get("form") or []
    acessos = recentes.get("accessionNumber") or []
    docs = recentes.get("primaryDocument") or []
    if not cik.isdigit():
        return None
    for i, forma in enumerate(formas):
        if str(forma or "").upper().strip() not in FORMAS_RELATORIO_ANUAL:
            continue
        if i >= len(acessos) or i >= len(docs) or not acessos[i] or not docs[i]:
            continue
        acesso = str(acessos[i]).replace("-", "")
        return (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                f"{acesso}/{docs[i]}")
    return None


def _afirmacoes(texto: str, padrao: re.Pattern[str]) -> list[str]:
    """Trechos que casam com `padrao` fora de frase condicional."""
    achados = []
    for m in padrao.finditer(texto):
        frase = texto[texto.rfind(".", 0, m.start()) + 1:m.end()]
        if not CONDICIONAL.search(frase):
            achados.append(frase.strip())
    return achados


def eleicao_no_texto(texto: str | None) -> bool:
    """True quando o documento afirma eleicao REIT vigente.

    Revogacao afirmada vence a eleicao afirmada: quem revogou tambem descreve a
    eleicao que teve, e ler so a primeira metade diria o contrario do documento.
    """
    limpo = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(texto or "")))
    if not _afirmacoes(limpo, ELEICAO_REIT):
        return False
    return not _afirmacoes(limpo, REVOGACAO_REIT)


def apurar_eleicao(sub: dict | None,
                   baixar_texto: Callable[[str], str | None]) -> str | None:
    """'declarada', 'ausente' ou None quando o relatorio nao pode ser lido.

    None nao e "nao e REIT": e ausencia de veredito. Quem chama trata os dois
    de forma diferente, senao a falha de rede viraria promocao silenciosa.
    """
    url = url_relatorio_anual(sub)
    if not url:
        return None
    try:
        texto = baixar_texto(url)
    except Exception as exc:  # noqa: BLE001
        logger.info("relatorio anual indisponivel (%s): %s", type(exc).__name__, url)
        return None
    if texto is None:
        return None
    return ELEICAO_REIT_DECLARADA if eleicao_no_texto(texto) else ELEICAO_REIT_AUSENTE
