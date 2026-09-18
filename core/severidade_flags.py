"""Severidade de uma linha de red flag — FONTE ÚNICA.

`red_flags` de um dossiê emite coisas de naturezas diferentes na MESMA lista:
risco confirmado, observação de contexto (fato medido que NÃO é padrão) e
limitação de cobertura (o que os dados não deixam verificar). Imprimir as três
sob um cabeçalho de "sinais de alerta" é o que dilui o sinal para quem decide:
medido no armazém local em 15/09/2026, 2.419 das 3.737 empresas americanas
elegíveis (65%) abriam o dossiê com pelo menos uma linha, todas sob o mesmo
título. Uma bandeira que acende para dois terços do universo não distingue
ninguém.

A regra mora AQUI e só aqui, para os DOIS mercados. O prefixo nasceu na B3
(`core/dossie_b3.py`) e o módulo dos EUA reescreveu a mesma tabela: em
18/09/2026 havia de novo duas "fontes únicas" vivas, cada uma com seu teste de
AST, e elas já divergiam — a da B3 classificava `MOMENTUM:` e `DADOS:`, a
daqui não. As quatro entradas foram reunidas abaixo e `core/dossie_b3.py`
passou a importar daqui; `tests/test_dossie_severidade.py` verifica por AST que
nenhum outro módulo compara os prefixos por conta própria.

Nada é silenciado: toda linha emitida continua visível e continua chegando ao
parecer da LLM. O que muda é o cabeçalho sob o qual ela chega.
"""
from __future__ import annotations

from collections.abc import Iterable

SEVERIDADE_RISCO = "risco_confirmado"
SEVERIDADE_CONTEXTO = "contexto_observado"
SEVERIDADE_COBERTURA = "limitacao_cobertura"

SEVERIDADES = (SEVERIDADE_RISCO, SEVERIDADE_CONTEXTO, SEVERIDADE_COBERTURA)

# O prefixo é escrito por quem emite a linha, a partir da MEDIÇÃO do histórico
# (ver core/us_risco_historico.py). Ele não é um rótulo editorial: diz em
# quantos exercícios da janela a condição foi observada.
_PREFIXO_SEVERIDADE: dict[str, str] = {
    "CONTEXTO:": SEVERIDADE_CONTEXTO,
    "COBERTURA:": SEVERIDADE_COBERTURA,
    # As duas seguintes só são emitidas pela B3 hoje, e vieram de lá com a
    # medição que as motivou. `MOMENTUM:` olha UM trimestre a/a: condenar por
    # um período isolado é o oposto de julgar a qualidade histórica — 94 das
    # 426 empresas do armazém estavam em bandeira vermelha SÓ por esta linha.
    "MOMENTUM:": SEVERIDADE_CONTEXTO,
    # `DADOS:` descreve defeito do NOSSO banco (provento divergente na mesma
    # data-ex, DY em desacordo com o recomputado). É o que não conseguimos
    # verificar, não risco da empresa: 50 das 426 estavam em vermelho só por
    # ela. Nenhuma das duas some do dossiê; muda o cabeçalho sob o qual chega.
    "DADOS:": SEVERIDADE_COBERTURA,
}

TITULO_SEVERIDADE: dict[str, str] = {
    SEVERIDADE_RISCO:
        "Risco confirmado no histórico (verificado em código)",
    SEVERIDADE_CONTEXTO:
        "Observações de contexto — medidas em código, não são risco confirmado",
    SEVERIDADE_COBERTURA:
        "Limitações de cobertura — o que os dados não permitem verificar",
}


def severidade_flag(flag: str) -> str:
    """Severidade de UMA linha de red flag. Função pura, fonte única.

    Prefixo desconhecido cai em risco confirmado: o lado seguro é APARECER
    para quem decide, nunca sumir.
    """
    texto = (flag or "").strip()
    for prefixo, severidade in _PREFIXO_SEVERIDADE.items():
        if texto.startswith(prefixo):
            return severidade
    return SEVERIDADE_RISCO


def agrupa_flags_por_severidade(flags: Iterable[str] | None) -> dict[str, list[str]]:
    """``red_flags`` → ``{severidade: [linhas]}``.

    As três chaves estão SEMPRE presentes (na ordem de ``SEVERIDADES``), mesmo
    vazias, para que o consumidor não precise testar existência.
    """
    grupos: dict[str, list[str]] = {s: [] for s in SEVERIDADES}
    for flag in flags or []:
        grupos[severidade_flag(flag)].append(flag)
    return grupos
