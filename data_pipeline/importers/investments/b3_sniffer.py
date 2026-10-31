"""
data_pipeline/importers/investments/b3_sniffer.py
=================================================
Identifica qual extrato um .xlsx contém, olhando só os nomes das abas.

Os arquivos vêm todos do mesmo lugar — o Relatório Consolidado carrega o nome
da XP, mas é emitido de dentro do investidor.b3.com.br como os outros dois —,
têm a mesma extensão e são indistinguíveis pelo nome. O que os separa está
dentro: Negociação traz a aba "Negociação", Movimentação traz "Movimentação",
o Consolidado traz as abas "Posição - …" e "Proventos Recebidos", e o extrato
"Posição" da Área do Investidor traz uma aba por classe de ativo com o nome nu
da classe ("Ações", "Renda Fixa", "Tesouro Direto"). Os parsers já procuram
essas abas antes de qualquer outra coisa — aqui a mesma checagem é antecipada,
para que o usuário não precise escolher a caixa de upload certa.

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

# Segunda passada, e só depois da primeira: o extrato "Posição" da Área do
# Investidor nomeia as abas pela classe do ativo e nada mais — "Ações",
# "Empréstimos", "ETF", "Fundo de Investimento", "Renda Fixa", "Tesouro
# Direto". Não dá para procurá-las por substring junto das outras assinaturas:
# "TESOURO DIRETO" está dentro de "POSIÇÃO - TESOURO DIRETO", e o Consolidado
# da XP — que tem parser próprio e traz proventos que este não tem — seria
# roubado por ela. Por isso o casamento aqui é EXATO e roda por último: o que
# a lista acima reconhece continua ganhando, e só o que sobra é testado contra
# nomes nus.
#
# A lista de nomes vem do próprio importador (`ABAS_CONHECIDAS`), não de uma
# cópia: aba que ganha leitor lá passa a ser detectada aqui no mesmo commit, e
# uma segunda lista divergiria na primeira correção feita de um lado só.
#
# Não há conferência de cabeçalho para confirmar. Seria o natural — o parser
# exige "Produto" na primeira coluna —, mas este arquivo é gerado por NPOI e
# declara dimensão "1 x 1": aberto em `read_only=True`, como `xlsx_probe` faz,
# ele parece vazio. Confirmar pelo conteúdo exigiria abrir o arquivo inteiro
# só para escolher o parser. O nome exato da aba basta para decidir, e o
# parser recusa com mensagem própria o que não for um extrato "Posição".
_CHAVE_POSICAO = "b3_posi"

__all__ = ["detect", "sheet_names"]


def _abas_nuas() -> frozenset[str]:
    from .b3_posicao import ABAS_CONHECIDAS

    return ABAS_CONHECIDAS


def detect(file_bytes: bytes) -> str | None:
    """Devolve "b3_neg", "b3_mov", "b3_pos", "b3_posi", "xp_csl" ou None.

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

    nuas = _abas_nuas()
    if any(aba in nuas for aba in abas):
        return _CHAVE_POSICAO
    return None
