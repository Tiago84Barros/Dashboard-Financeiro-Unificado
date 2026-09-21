"""
data_pipeline/importers/investments/tesouro_sniffer.py
======================================================
Decide se um .xlsx do Tesouro Direto é Extrato **Analítico** ou **Consolidado**.

Os dois saem do mesmo portal, têm a mesma extensão e nomes de arquivo que não
distinguem nada. O que os separa está dentro, e a ordem da checagem aqui não é
estética:

1. **Conteúdo primeiro.** O parser do Analítico procura uma aba cujo nome
   contenha "ANALITICO" e, **não achando, cai na primeira aba do arquivo**.
   Um Analítico cuja aba se chame "Extrato" (o portal já emitiu assim) seria
   entregue ao parser do Consolidado se o nome da aba decidisse — e o
   Consolidado leria aquele arquivo como posição, gravando snapshot a partir de
   um documento que é de lotes. Por isso o cabeçalho manda.
2. **Nome da aba depois.** Só quando o cabeçalho de Analítico não aparece é que
   a aba "Extrato" identifica o Consolidado.

O reconhecimento do Analítico reusa `parse_cabecalho` do próprio importador, e
não uma cópia das expressões regulares: a pergunta que o detector faz é
exatamente "este arquivo passa no cabeçalho que o parser exige?". Uma segunda
cópia dos padrões divergiria na primeira correção feita de um lado só, e o
sintoma seria um arquivo aceito na porta e recusado lá dentro.

Puro: recebe bytes, devolve string ou None. Não toca o banco e não importa
Streamlit.
"""
from __future__ import annotations

from .xlsx_probe import abrir, fechar, norm, primeiras_linhas, sheet_names

ANALITICO = "tesouro_analitico"
CONSOLIDADO = "tesouro_direto"

# O parser do Consolidado exige a aba com este nome exato
# (`if "Extrato" not in workbook.sheetnames: raise`). Comparar aqui de forma
# mais frouxa aceitaria na porta um arquivo que o parser recusaria adiante.
_ABA_CONSOLIDADO = "Extrato"

__all__ = ["ANALITICO", "CONSOLIDADO", "detect", "sheet_names"]


def _aba_do_analitico(workbook):
    """A mesma aba que `tesouro_analitico.parse_arquivo` escolheria."""
    for nome in workbook.sheetnames:
        if "ANALITICO" in norm(nome):
            return workbook[nome]
    return workbook[workbook.sheetnames[0]]


def _tem_cabecalho_analitico(workbook) -> bool:
    from .tesouro_analitico import parse_cabecalho

    try:
        linhas = primeiras_linhas(_aba_do_analitico(workbook))
    except Exception:  # noqa: BLE001 — aba ilegível não é Analítico
        return False
    cabecalho = parse_cabecalho(linhas)
    return bool(cabecalho.get("titulo") and cabecalho.get("vencimento"))


def detect(file_bytes: bytes) -> str | None:
    """Devolve "tesouro_analitico", "tesouro_direto" ou None.

    None cobre tanto "é um xlsx válido de outra origem" quanto "não consegui
    abrir" — nos dois casos o arquivo é recusado em vez de entregue a um parser
    no chute.
    """
    workbook = abrir(file_bytes)
    if workbook is None:
        return None

    try:
        if not workbook.sheetnames:
            return None
        if _tem_cabecalho_analitico(workbook):
            return ANALITICO
        if _ABA_CONSOLIDADO in workbook.sheetnames:
            return CONSOLIDADO
        return None
    finally:
        fechar(workbook)
