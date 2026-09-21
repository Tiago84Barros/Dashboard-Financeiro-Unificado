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
Streamlit, não percorre linhas — só `sheetnames`. A abertura do arquivo e a
normalização de texto vivem em `xlsx_probe`, compartilhadas com o detector do
Tesouro Direto.
"""
from __future__ import annotations

from .xlsx_probe import norm as _norm
from .xlsx_probe import sheet_names

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
# "SUA CARTEIRA" e a aba unica do extrato "Posicao Detalhada" da Area do
# Investidor. Ele nao tem nada em comum com o Consolidado da XP alem de ser
# uma foto da carteira: uma aba so, sete secoes empilhadas com colunas
# diferentes. A assinatura e o nome da aba porque e a unica coisa estavel --
# o nome do ARQUIVO exportado e sempre "PosicaoDetalhada.xlsx", igual para
# qualquer data, e a data vive dentro do cabecalho.
_ASSINATURAS: list[tuple[str, str]] = [
    ("NEGOCIACAO", "b3_neg"),
    ("MOVIMENTACAO", "b3_mov"),
    ("SUA CARTEIRA", "b3_pos"),
    ("POSICAO - ", "xp_csl"),
    ("PROVENTOS RECEBIDOS", "xp_csl"),
]

__all__ = ["detect", "sheet_names"]


def detect(file_bytes: bytes) -> str | None:
    """Devolve "b3_neg", "b3_mov", "b3_pos", "xp_csl" ou None.

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
