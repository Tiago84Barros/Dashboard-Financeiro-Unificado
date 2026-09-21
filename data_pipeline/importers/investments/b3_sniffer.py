"""
data_pipeline/importers/investments/b3_sniffer.py
=================================================
Identifica qual extrato um .xlsx contém, olhando só os nomes das abas.

Os arquivos vêm todos do mesmo lugar — o Relatório Consolidado carrega o nome
da XP, mas é emitido de dentro do investidor.b3.com.br como os outros dois —,
têm a mesma extensão e são indistinguíveis pelo nome. O que os separa está
dentro: Negociação traz a aba "Negociação", Movimentação traz "Movimentação",
e o Consolidado traz as abas "Posição - …" e "Proventos Recebidos". Os parsers
já procuram essas abas antes de qualquer outra coisa — aqui a mesma checagem é
antecipada, para que o usuário não precise escolher a caixa de upload certa.

Puro: recebe bytes, devolve string ou None. Não toca o banco, não importa
Streamlit, não percorre linhas — só `sheetnames`.
"""
from __future__ import annotations

import io
import unicodedata

# Ordem importa: a primeira assinatura encontrada vence. Na prática os extratos
# são mutuamente exclusivos, mas um arquivo que trouxesse as duas abas da B3
# seria tratado como Negociação — e a Movimentação ignora compra e venda de
# qualquer forma, então o desempate não perde dado.
#
# A XP tem DUAS assinaturas porque as abas de posição e a de proventos são
# independentes: um consolidado de mês sem posição aberta ainda traz
# "Proventos Recebidos", e reconhecer só "POSICAO - " o recusaria. O hífen em
# "POSICAO - " não é decoração: sem ele o marcador casaria com qualquer aba
# que mencionasse posição, inclusive de origens que não têm parser aqui.
_ASSINATURAS: list[tuple[str, str]] = [
    ("NEGOCIACAO", "b3_neg"),
    ("MOVIMENTACAO", "b3_mov"),
    ("POSICAO - ", "xp_csl"),
    ("PROVENTOS RECEBIDOS", "xp_csl"),
]


def _norm(texto: str) -> str:
    """Caixa alta sem acento — 'Negociação' e 'Negociacao' colidem aqui."""
    sem_acento = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return sem_acento.upper().strip()


def sheet_names(file_bytes: bytes) -> list[str]:
    """Nomes das abas do .xlsx, ou lista vazia se o arquivo não abrir.

    Usado tanto pela detecção quanto pela mensagem de recusa — quando nada é
    reconhecido, a tela mostra o que o arquivo de fato tinha dentro.
    """
    try:
        import openpyxl
    except ImportError:
        return []

    try:
        wb = openpyxl.load_workbook(
            io.BytesIO(file_bytes), read_only=True, data_only=True,
        )
    except Exception:  # noqa: BLE001 — arquivo corrompido ou não-xlsx
        return []

    try:
        return list(wb.sheetnames)
    finally:
        try:
            wb.close()
        except Exception:  # noqa: BLE001
            pass


def detect(file_bytes: bytes) -> str | None:
    """Devolve "b3_neg", "b3_mov", "xp_csl" ou None.

    None cobre tanto "é um xlsx válido de outra origem" quanto "não consegui
    abrir": em ambos os casos a resposta certa é recusar o arquivo em vez de
    entregá-lo a um parser no chute.
    """
    abas = [_norm(nome) for nome in sheet_names(file_bytes)]
    if not abas:
        return None

    for marcador, chave in _ASSINATURAS:
        if any(marcador in aba for aba in abas):
            return chave
    return None
