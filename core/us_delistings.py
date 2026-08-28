"""Deriva as SAIDAS do universo americano a partir do indice anual da SEC.

O painel dos EUA acusa zero saidas em 16 safras e, por construcao, nunca
acusaria outra coisa: `market_us.companies` foi montada a partir de quem esta
listado hoje, entao quem morreu nunca entrou. A medicao de `core.us_survivorship`
ja disse o tamanho do buraco -- 70% das 9.686 empresas de 2010 sumiram ate 2025
-- mas tamanho nao e registro: para o backtest deixar de ser 100% sobrevivente
e preciso saber QUEM saiu e QUANDO.

A fonte e o `full-index` da SEC, que nao depende de a empresa estar viva hoje:
lista quem arquivou relatorio anual em cada trimestre. Uma empresa que arquivava
em 2012 e nunca mais arquivou saiu do mercado -- por falencia, fechamento de
capital ou aquisicao.

Tres regras que decidem se o registro presta:

1. **Sair exige ausencia em TODOS os anos seguintes.** Empresa que atrasa um
   10-K e volta no ano seguinte nao morreu. A regra "ultimo ano presente" sem
   essa exigencia transformaria atraso de arquivamento em deslistagem, e o
   registro ficaria cheio de mortes que nunca aconteceram.

2. **Ano truncado nao vira extincao em massa.** Se o download de um trimestre
   falhar, aquele ano aparece pequeno e todo mundo "some" nele. O piso de
   cobertura -- relativo ao MAIOR ano ja visto, nunca ao ano vizinho, senao dois
   anos truncados se validam -- descarta o ano incompleto em vez de acreditar
   nele. E a mesma armadilha que quase marcou 636 FIIs saudaveis como
   encerrados em 28/08/2026.

3. **A data e a do primeiro ano em que a ausencia e CONHECIDA**, nao a do
   ultimo ano presente. Datar a saida no ultimo relatorio anual afirmaria saber
   que a empresa morreu antes de haver qualquer evidencia disso; a evidencia so
   existe quando o ano seguinte fecha sem arquivamento. Datar depois e
   conservador e honesto; datar antes e vantagem retroativa.

O que este modulo NAO faz: dizer a CAUSA. Ausencia de arquivamento nao separa
falencia de aquisicao, e a diferenca vale muito para o investidor -- quem foi
comprado com premio nao perdeu capital. A causa exige documento (item 1.03 x
2.01 do 8-K) e fica para uma etapa propria; ate la o motivo gravado e
`ausencia_de_relatorio_anual`, que e o que de fato se observou.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

# Piso de cobertura de um ano contra o MAIOR ano ja visto. O universo de
# arquivadores anuais dos EUA encolheu de verdade ao longo de 2010-2025 (menos
# empresas listadas), entao o piso nao pode ser rigoroso demais: 60% acomoda a
# tendencia real e ainda barra o ano em que faltou um trimestre inteiro (que
# custaria ~25% de imediato e tipicamente muito mais).
COBERTURA_MINIMA = 0.60

MOTIVO_DERIVADO = "ausencia_de_relatorio_anual"
FONTE = "sec_full_index"


@dataclass
class Saida:
    """Uma saida derivada: quem, quando foi visto por ultimo, quando sumiu."""
    cik: int
    ultimo_ano_com_relatorio: int
    ano_da_ausencia: int
    motivo: str = MOTIVO_DERIVADO
    fonte: str = FONTE

    @property
    def data_saida(self) -> date:
        """Fim do primeiro ano em que a ausencia ja e observavel."""
        return date(self.ano_da_ausencia, 12, 31)


@dataclass
class Diagnostico:
    """Resultado da derivacao com o motivo, para quando nao houver saidas."""
    saidas: list[Saida] = field(default_factory=list)
    anos_comparaveis: list[int] = field(default_factory=list)
    anos_descartados: dict[int, int] = field(default_factory=dict)
    motivo: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.saidas)


def _anos_comparaveis(por_ano: dict[int, set[int]]
                      ) -> tuple[list[int], dict[int, int]]:
    """Separa os anos cujo indice parece completo dos que vieram truncados.

    O piso e relativo ao maior universo ja visto ATE aquele ano, e nao ao ano
    anterior: dois anos truncados em sequencia se aprovariam um ao outro, e a
    consequencia seria declarar morta metade do mercado.
    """
    comparaveis, descartados, maior = [], {}, 0
    for ano in sorted(por_ano):
        n = len(por_ano[ano] or ())
        if maior and n < COBERTURA_MINIMA * maior:
            descartados[ano] = n
            continue
        maior = max(maior, n)
        comparaveis.append(ano)
    return comparaveis, descartados


def derivar_saidas(por_ano: dict[int, set[int]]) -> Diagnostico:
    """Quem arquivava relatorio anual e parou de arquivar, e em que ano.

    `por_ano` e {ano: CIKs que arquivaram relatorio anual naquele ano}. Uma
    saida so e declarada quando o CIK esta ausente em TODOS os anos comparaveis
    posteriores ao ultimo em que apareceu.
    """
    anos, descartados = _anos_comparaveis(por_ano or {})
    if len(anos) < 2:
        return Diagnostico(
            anos_comparaveis=anos, anos_descartados=descartados,
            motivo=(f"apenas {len(anos)} ano(s) de indice comparavel: sem dois "
                    f"anos completos nao ha como observar ausencia"))

    # ultimo ano em que cada CIK aparece; quem aparece no ultimo ano da janela
    # esta vivo ate onde a evidencia alcanca.
    ultimo: dict[int, int] = {}
    for ano in anos:
        for cik in por_ano[ano]:
            if ano > ultimo.get(cik, 0):
                ultimo[cik] = ano
    fim = anos[-1]

    saidas = []
    for cik, ano_visto in ultimo.items():
        if ano_visto >= fim:
            continue
        posteriores = [a for a in anos if a > ano_visto]
        if not posteriores:
            continue
        saidas.append(Saida(cik=cik, ultimo_ano_com_relatorio=ano_visto,
                            ano_da_ausencia=posteriores[0]))
    saidas.sort(key=lambda s: (s.ano_da_ausencia, s.cik))

    motivo = ""
    if not saidas:
        motivo = (f"nenhum CIK deixou de arquivar entre {anos[0]} e {fim} -- "
                  f"resultado impossivel num mercado real, entao o indice lido "
                  f"provavelmente nao e o universo completo")
    return Diagnostico(saidas=saidas, anos_comparaveis=anos,
                       anos_descartados=descartados, motivo=motivo)


def resumo(diag: Diagnostico) -> dict[str, Any]:
    """Agregado do diagnostico, para relatorio e para gravar junto da medicao."""
    por_ano: dict[str, int] = {}
    for s in diag.saidas:
        por_ano[str(s.ano_da_ausencia)] = por_ano.get(str(s.ano_da_ausencia), 0) + 1
    return {
        "total_saidas": len(diag.saidas),
        "anos_comparaveis": diag.anos_comparaveis,
        "anos_descartados": {str(a): n for a, n in diag.anos_descartados.items()},
        "saidas_por_ano": por_ano,
        "motivo": diag.motivo,
        "fonte": FONTE,
        "motivo_gravado": MOTIVO_DERIVADO,
    }
