# -*- coding: utf-8 -*-
"""Por que a empresa americana saiu da bolsa: comprada, quebrou, ou nao da para saber.

A distincao existe porque ela decide dinheiro. Uma empresa comprada com premio
devolveu capital ao acionista; uma que pediu falencia destruiu. Tratar as duas
como "saiu" e o mesmo erro de descarta-las: escolhe um numero sem dizer que
escolheu.

**O tipo de formulario nao separa.** `8-K` cobre desde troca de auditor ate
pedido de falencia. Quem discrimina e o ITEM: 1.03 (falencia ou recuperacao
judicial) contra 2.01 (conclusao de aquisicao ou alienacao de ativos). Medido
numa sondagem de 60 saidas, 34 carregavam 2.01 e apenas 3 carregavam 1.03 --
classificar por tipo de formulario deixava 34 aquisicoes passarem por morte.

**A janela importa para o 2.01 e nao para o 1.03.** No meio da vida, 2.01 e a
empresa comprando algo; nos ultimos meses de arquivamento, e ela sendo comprada.
Ja o 1.03 vale em qualquer momento da historia -- quem pediu concordata em 2015
e voltou a arquivar nao interessa a esta classificacao, e quem pediu no fim
tampouco muda de natureza por causa da data.

**A ordem tambem importa.** Falencia e testada ANTES de aquisicao porque venda
de ativos DENTRO da recuperacao judicial tambem arquiva 2.01: chamar isso de
aquisicao esconderia exatamente o caso que o investidor precisa ver.

**INDEFINIDO nao e residuo, e a maioria.** A primeira versao desta medicao
empurrava o nao-classificado para o grupo "morreu" por conservadorismo, e a
conclusao saiu invertida. Sem evidencia, o caso sai da conta -- excluir e melhor
que chutar para o lado que parece seguro, porque o chute apaga o que a medicao
tenta medir. Ver [[saida-da-bolsa-nao-e-morte]].

Este modulo e puro: nao faz rede, nao le banco. Quem busca na SEC e quem grava
sao os scripts.
"""
from __future__ import annotations

from datetime import date, timedelta

# Proxy de fusao/fechamento de capital arquivado pela PROPRIA empresa comprada.
FORMAS_FUSAO = ("DEFM14A", "PREM14A", "DEFM14C", "PREM14C", "SC 13E3")
# Recomendacao de resposta a oferta hostil: quem arquiva e o ALVO.
FORMAS_ALVO = ("SC 14D9",)

ITEM_FALENCIA = "1.03"       # 8-K: pedido de falencia ou concordata
ITEM_AQUISICAO = "2.01"      # 8-K: conclusao de aquisicao ou alienacao de ativos

# Janela final da historia de arquivamento em que um 2.01 fala da PROPRIA
# empresa sendo comprada, e nao dela comprando algo no curso normal.
DIAS_FIM = 365

ADQUIRIDA, SUMIU, INDEFINIDO = "adquirida", "sumiu", "indefinido"
CAUSAS = (ADQUIRIDA, SUMIU, INDEFINIDO)


def itens_de_8k(recentes: dict, dias_fim: int = DIAS_FIM) -> dict:
    """Separa os itens de 8-K do fim da historia dos do curso normal.

    `recentes` e o bloco `filings.recent` do submissions da SEC: listas
    paralelas `filingDate` e `items`, esta ultima com os codigos separados por
    virgula. Listas de tamanhos diferentes sao alinhadas por preenchimento --
    a SEC as devolve assim quando um arquivamento nao tem item.
    """
    datas = [str(d) for d in (recentes.get("filingDate") or [])]
    itens = [str(i or "") for i in (recentes.get("items") or [])]
    if not datas:
        return {"itens_finais": [], "itens_todos": [], "ultimo_arquivamento": None}
    itens += [""] * (len(datas) - len(itens))
    fim = max(datas)
    try:
        corte = (date.fromisoformat(fim) - timedelta(days=dias_fim)).isoformat()
    except ValueError:
        corte = fim
    todos: set[str] = set()
    finais: set[str] = set()
    for data, item in zip(datas, itens):
        codigos = {c.strip() for c in item.split(",") if c.strip()}
        todos |= codigos
        if data >= corte:
            finais |= codigos
    return {"itens_finais": sorted(finais), "itens_todos": sorted(todos),
            "ultimo_arquivamento": fim}


def classificar(formas: list[str] | None,
                itens_finais: list[str] | None,
                itens_todos: list[str] | None) -> str:
    """Adquirida, quebrou, ou -- o caso mais comum -- nao da para saber."""
    finais = set(itens_finais or [])
    todos = set(itens_todos or [])
    if ITEM_FALENCIA in todos:
        return SUMIU
    for f in (formas or []):
        if str(f).startswith(FORMAS_FUSAO) or str(f).startswith(FORMAS_ALVO):
            return ADQUIRIDA
    if ITEM_AQUISICAO in finais:
        return ADQUIRIDA
    return INDEFINIDO


def classificar_pacote(pacote: dict) -> str:
    """Mesma decisao, a partir do registro compacto que os scripts guardam."""
    return classificar(pacote.get("formas"),
                       pacote.get("itens_finais"),
                       pacote.get("itens_todos"))
