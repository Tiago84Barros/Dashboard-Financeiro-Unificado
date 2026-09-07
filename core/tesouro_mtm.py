"""Marcação a mercado do Tesouro Direto, lote a lote.

Por que este módulo existe
--------------------------
``core.tesouro_analysis`` recusa emitir qualquer leitura de MtM, e a recusa
estava certa: o banco tinha valor de mercado e custo, e a diferença entre os
dois **mistura carrego com marcação**. Um Tesouro Selic que subiu 10% em 277
dias não valorizou 10% — ele rendeu a Selic. Chamar isso de MtM e derivar venda
seria inventar o sinal.

O Extrato Analítico fecha exatamente essa lacuna: ele traz a **taxa contratada
por lote** e a **data de aplicação**. Com a taxa de mercado de hoje (série
pública do Tesouro Transparente), a marcação vira uma conta fechada.

A identidade que sustenta tudo
------------------------------
O preço de um título do Tesouro é ``PU = VNA / (1 + i) ** (du/252)``, onde
``VNA`` é 1000 no prefixado e o valor nominal atualizado no Selic/IPCA+. A
marcação a mercado de um lote é a razão entre o preço de hoje e o preço que o
mesmo título teria hoje pela taxa que **você** contratou::

    MtM = (PU_mercado / PU_curva_contratada) - 1
        = ((1 + i_contratada) / (1 + i_mercado)) ** (du_restante/252) - 1

O ``VNA`` cancela na razão. Por isso a fórmula vale igual para prefixado, para
IPCA+ (taxa real dos dois lados) e para Selic (ágio/deságio dos dois lados),
sem precisar do VNA nem do IPCA projetado. Em título com juros semestrais ela
é aproximação — o cupom não cancela —, e o resultado sai marcado como tal.

O que este módulo **não** conclui
---------------------------------
Não conclui que "subiu, então venda". A partir de hoje o título rende a taxa de
mercado de hoje, não a que você contratou: a taxa contratada já está paga,
embutida no preço. Vender e recomprar o mesmo papel é estritamente pior que
carregar — você paga IR agora sobre um ganho que seria diferido e ainda cruza o
spread entre o preço de compra e o de resgate. Por isso o veredito daqui só
existe **contra uma alternativa nomeada**, com as duas pernas terminando na
mesma data. Comparação sem alternativa é torcida, não decisão.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

DIAS_UTEIS_ANO = 252

# ─────────────────────────────────────────────────────────────────────────────
# Calendário projetado
#
# ``core.pregao`` só conhece feriado **observado** (dia útil em que a bolsa
# inteira não negociou), e observação não alcança 2031. Contar dias úteis até o
# vencimento exige calendário para frente, então aqui a regra é federal: datas
# fixas + as móveis derivadas da Páscoa. As duas contagens não se substituem, e
# a diferença entre elas é medida no teste, não presumida.
# ─────────────────────────────────────────────────────────────────────────────

_FERIADOS_FIXOS = ((1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (12, 25))
# 20/11 (Consciência Negra) virou feriado nacional pela Lei 14.759/2023.
_ANO_CONSCIENCIA_NEGRA = 2024


def pascoa(ano: int) -> date:
    """Domingo de Páscoa pelo algoritmo de Meeus/Butcher (calendário gregoriano)."""
    a, b, c = ano % 19, ano // 100, ano % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    lm = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lm) // 451
    mes = (h + lm - 7 * m + 114) // 31
    dia = ((h + lm - 7 * m + 114) % 31) + 1
    return date(ano, mes, dia)


def feriados_nacionais(ano: int) -> frozenset[date]:
    """Feriados nacionais projetados para um ano, incluindo as datas móveis."""
    dias = {date(ano, mes, dia) for mes, dia in _FERIADOS_FIXOS}
    if ano >= _ANO_CONSCIENCIA_NEGRA:
        dias.add(date(ano, 11, 20))
    domingo = pascoa(ano)
    dias.add(domingo - timedelta(days=48))  # segunda de carnaval
    dias.add(domingo - timedelta(days=47))  # terça de carnaval
    dias.add(domingo - timedelta(days=2))   # sexta-feira santa
    dias.add(domingo + timedelta(days=60))  # corpus christi
    return frozenset(dias)


def dias_uteis(inicio: date, fim: date) -> int:
    """Dias úteis em ``[inicio, fim)`` — a convenção que o Tesouro publica.

    A data-base entra na contagem; o vencimento não, porque no vencimento o
    preço já é o nominal e não há mais dia a capitalizar.

    A convenção não foi escolhida por leitura de manual — ela foi **calibrada**
    contra os PUs oficiais, invertendo ``PU = 1000/(1+i)**(du/252)`` nos
    prefixados, que são os únicos em que o VNA é conhecido (1.000). Excluindo a
    data-base, todos os vencimentos erravam por exatamente -1 du; incluindo-a,
    o resíduo cai para o arredondamento da taxa publicada com duas casas
    (``tests/test_tesouro_mtm.py``).

    Retorna 0 quando ``fim`` não é posterior a ``inicio``: título vencido não
    tem prazo negativo, tem prazo zero.
    """
    if inicio is None or fim is None or fim <= inicio:
        return 0
    feriados: set[date] = set()
    for ano in range(inicio.year, fim.year + 1):
        feriados |= feriados_nacionais(ano)
    total = 0
    dia = inicio
    while dia < fim:
        if dia.weekday() < 5 and dia not in feriados:
            total += 1
        dia += timedelta(days=1)
    return total


# ─────────────────────────────────────────────────────────────────────────────
# Taxa contratada
# ─────────────────────────────────────────────────────────────────────────────

_RE_TAXA = re.compile(
    r"^\s*(?P<idx>SELIC|IPCA|IGPM|IGP-M)?\s*(?P<sinal>[+-])?\s*(?P<num>[\d.,]+)\s*%?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TaxaContratada:
    """Taxa de um lote como o extrato a escreve.

    ``valor`` é decimal ao ano. Para indexado é o ágio/deságio ou o cupom real
    sobre o índice; para prefixado é a taxa cheia. Os dois entram na mesma
    fórmula porque o mercado publica a taxa do mesmo jeito.
    """

    indexador: str  # 'SELIC' | 'IPCA' | 'PRE'
    valor: float
    texto: str


def parse_taxa_contratada(texto: object) -> TaxaContratada | None:
    """Lê 'SELIC + 0,103%', 'SELIC - 0,520%', 'IPCA + 6,12%' ou '13,50%'.

    Devolve ``None`` para texto ausente ou fora do formato — taxa que não foi
    lida não pode virar zero, porque zero é uma taxa e produziria MtM.
    """
    if texto is None:
        return None
    bruto = str(texto).strip()
    if not bruto:
        return None
    match = _RE_TAXA.match(bruto.replace("Á", "A").replace("á", "a"))
    if not match:
        return None
    numero = match.group("num").replace(".", "").replace(",", ".")
    try:
        valor = float(numero) / 100.0
    except ValueError:
        return None
    if match.group("sinal") == "-":
        valor = -valor
    idx = (match.group("idx") or "PRE").upper().replace("-", "")
    if idx == "IGPM":
        idx = "IGPM"
    return TaxaContratada(indexador=idx if idx in {"SELIC", "IPCA", "IGPM"} else "PRE",
                          valor=valor, texto=bruto)


# ─────────────────────────────────────────────────────────────────────────────
# Marcação a mercado
# ─────────────────────────────────────────────────────────────────────────────

def preco_unitario(valor_nominal: float, taxa: float, du: int) -> float | None:
    """``PU = VNA / (1+i)**(du/252)`` — a identidade de preço do Tesouro."""
    if valor_nominal is None or valor_nominal <= 0 or taxa is None:
        return None
    if taxa <= -1.0 or du is None or du < 0:
        return None
    return valor_nominal / ((1.0 + taxa) ** (du / DIAS_UTEIS_ANO))


def mtm_por_taxa(taxa_contratada: float | None, taxa_mercado: float | None, du_restante: int | None) -> float | None:
    """MtM decimal do lote: ganho de preço sobre a curva da taxa contratada.

    Positivo significa que o título vale hoje **mais** do que valeria pela taxa
    que você contratou — a taxa de mercado caiu desde a compra.
    """
    if taxa_contratada is None or taxa_mercado is None or du_restante is None:
        return None
    if du_restante <= 0:
        return 0.0  # no vencimento o preço converge para o nominal, sem marcação
    if taxa_contratada <= -1.0 or taxa_mercado <= -1.0:
        return None
    return ((1.0 + taxa_contratada) / (1.0 + taxa_mercado)) ** (du_restante / DIAS_UTEIS_ANO) - 1.0


def aliquota_ir(dias_corridos: int | None) -> float | None:
    """Alíquota regressiva de IR, por lote, em decimal."""
    if dias_corridos is None or dias_corridos < 0:
        return None
    if dias_corridos <= 180:
        return 0.225
    if dias_corridos <= 360:
        return 0.20
    if dias_corridos <= 720:
        return 0.175
    return 0.15


def aliquota_iof(dias_corridos: int | None) -> float:
    """IOF regressivo sobre o rendimento — zera a partir do 30º dia."""
    if dias_corridos is None or dias_corridos >= 30 or dias_corridos < 0:
        return 0.0
    return (30 - dias_corridos) / 30.0


# ─────────────────────────────────────────────────────────────────────────────
# Lote e avaliação
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LoteTesouro:
    """Uma linha do Extrato Analítico: uma aplicação, com sua própria taxa."""

    data_aplicacao: date
    quantidade: float
    preco_aplicacao: float
    valor_investido: float
    taxa_contratada: TaxaContratada | None
    valor_bruto_extrato: float | None = None
    dias_corridos_extrato: int | None = None
    ir_extrato: float | None = None
    iof_extrato: float | None = None
    taxa_b3_extrato: float | None = None
    taxa_instituicao_extrato: float | None = None
    valor_liquido_extrato: float | None = None


@dataclass(frozen=True)
class AvaliacaoLote:
    """Resultado da marcação de um lote numa data de avaliação."""

    data_avaliacao: date
    du_restante: int
    dias_corridos: int
    taxa_contratada: float | None
    taxa_mercado: float | None
    mtm: float | None
    aproximado: bool
    valor_bruto: float | None
    valor_curva_contratada: float | None
    ganho_mtm_reais: float | None
    rendimento_bruto: float | None
    ir: float | None
    iof: float | None
    valor_liquido: float | None
    fonte_preco: str  # 'curva' | 'extrato' | 'indisponivel'


def _tem_cupom(nome_titulo: str) -> bool:
    return "JUROS" in (nome_titulo or "").upper()


def avaliar_lote(
    lote: LoteTesouro,
    *,
    nome_titulo: str,
    vencimento: date,
    data_avaliacao: date,
    taxa_mercado_resgate: float | None,
    pu_mercado: float | None = None,
    custos: float = 0.0,
) -> AvaliacaoLote:
    """Marca um lote a mercado numa data, separando carrego de marcação.

    ``pu_mercado`` vem da série pública quando existe; sem ela, o valor bruto do
    próprio extrato é usado e a avaliação fica congelada na data do arquivo —
    o campo ``fonte_preco`` diz qual das duas sustentou o número.
    """
    du = dias_uteis(data_avaliacao, vencimento)
    dias_corridos = (data_avaliacao - lote.data_aplicacao).days
    taxa_c = lote.taxa_contratada.valor if lote.taxa_contratada else None
    mtm = mtm_por_taxa(taxa_c, taxa_mercado_resgate, du)
    aproximado = _tem_cupom(nome_titulo)

    if pu_mercado is not None and pu_mercado > 0:
        valor_bruto = pu_mercado * lote.quantidade
        fonte = "curva"
    elif lote.valor_bruto_extrato is not None:
        valor_bruto = lote.valor_bruto_extrato
        fonte = "extrato"
    else:
        valor_bruto = None
        fonte = "indisponivel"

    valor_curva = None
    ganho_mtm = None
    if valor_bruto is not None and mtm is not None and mtm > -1.0:
        valor_curva = valor_bruto / (1.0 + mtm)
        ganho_mtm = valor_bruto - valor_curva

    rendimento = ir = iof = liquido = None
    if valor_bruto is not None:
        rendimento = valor_bruto - lote.valor_investido
        aliq_ir = aliquota_ir(dias_corridos)
        base = max(rendimento, 0.0)
        iof = base * aliquota_iof(dias_corridos)
        ir = (base - iof) * aliq_ir if aliq_ir is not None else None
        if ir is not None:
            liquido = valor_bruto - ir - iof - max(custos, 0.0)

    return AvaliacaoLote(
        data_avaliacao=data_avaliacao,
        du_restante=du,
        dias_corridos=dias_corridos,
        taxa_contratada=taxa_c,
        taxa_mercado=taxa_mercado_resgate,
        mtm=mtm,
        aproximado=aproximado,
        valor_bruto=valor_bruto,
        valor_curva_contratada=valor_curva,
        ganho_mtm_reais=ganho_mtm,
        rendimento_bruto=rendimento,
        ir=ir,
        iof=iof,
        valor_liquido=liquido,
        fonte_preco=fonte,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Carregar × vender: a comparação só existe contra alternativa nomeada
# ─────────────────────────────────────────────────────────────────────────────

MANTER = "MANTER"
AVALIAR_TROCA = "AVALIAR TROCA"
VENDA_DESVANTAJOSA = "VENDA DESVANTAJOSA"
SEM_BASE = "SEM BASE PARA DECIDIR"

# Faixa morta: diferença menor que isto entre as duas pernas não é vantagem,
# é ruído de arredondamento de PU, de calendário e de spread intradiário.
LIMIAR_INDIFERENCA = 0.005  # 0,5% do valor da posição


@dataclass(frozen=True)
class Comparacao:
    """Carregar até o vencimento × vender hoje e reinvestir na alternativa."""

    veredito: str
    valor_final_carregando: float | None
    valor_final_vendendo: float | None
    vantagem_reais: float | None
    vantagem_pct: float | None
    imposto_antecipado: float | None
    taxa_alternativa: float | None
    taxa_indice: float | None
    du_restante: int
    aproximado: bool
    motivo: str


def comparar_carregar_vs_vender(
    avaliacoes: list[AvaliacaoLote],
    *,
    vencimento: date,
    data_avaliacao: date,
    taxa_mercado_resgate: float | None,
    taxa_alternativa: float | None,
    taxa_indice: float | None = None,
    aproximado: bool = False,
) -> Comparacao:
    """Compara as duas pernas terminando na **mesma data**, líquidas de imposto.

    Carregar: o valor bruto de hoje capitaliza à taxa de mercado de hoje até o
    vencimento — daqui para frente é ela que o título paga, não a contratada —
    e o IR incide no fim, na alíquota do prazo total, sobre o ganho total.

    Vender: realiza o líquido de hoje (IR e IOF já pagos) e capitaliza esse
    líquido à taxa da alternativa, com IR novo sobre o ganho do novo papel.

    ``taxa_indice`` é o índice que as duas pernas recebem por igual — a Selic
    projetada para um Tesouro Selic, a inflação implícita para um IPCA+. Sem
    ele, um título Selic seria capitalizado a 0,08% ao ano (o ágio contratado)
    em vez de Selic + 0,08%, e carregar pareceria absurdamente pior que
    qualquer alternativa. Para prefixado é zero: a taxa já é cheia.

    Sem alternativa informada não há veredito. A comparação de um investimento
    contra ele mesmo tem resposta conhecida e não depende de conta: perde pelo
    imposto antecipado.
    """
    du = dias_uteis(data_avaliacao, vencimento)
    validas = [a for a in avaliacoes if a.valor_bruto is not None and a.valor_liquido is not None]
    if not validas or taxa_mercado_resgate is None:
        return Comparacao(SEM_BASE, None, None, None, None, None, taxa_alternativa, taxa_indice, du, aproximado,
                          "Sem preço de mercado ou sem lote avaliável.")
    if du <= 0:
        return Comparacao(MANTER, None, None, None, None, None, taxa_alternativa, taxa_indice, du, aproximado,
                          "Título no vencimento ou vencido: não há prazo para comparar.")
    if taxa_alternativa is None:
        return Comparacao(SEM_BASE, None, None, None, None, None, None, taxa_indice, du, aproximado,
                          "Nenhuma alternativa escolhida. Vender para recomprar o mesmo "
                          "título perde por definição: antecipa IR e cruza o spread.")

    idx = taxa_indice or 0.0
    fator_carrego = ((1.0 + idx) * (1.0 + taxa_mercado_resgate)) ** (du / DIAS_UTEIS_ANO)
    fator_alt = ((1.0 + idx) * (1.0 + taxa_alternativa)) ** (du / DIAS_UTEIS_ANO)

    final_carregando = 0.0
    final_vendendo = 0.0
    imposto_antecipado = 0.0
    for a in validas:
        # Perna A — carregar: bruto de hoje rende a taxa de mercado até o fim.
        bruto_venc = a.valor_bruto * fator_carrego
        dias_totais = a.dias_corridos + (vencimento - data_avaliacao).days
        aliq_final = aliquota_ir(dias_totais) or 0.15
        investido = a.valor_bruto - (a.rendimento_bruto or 0.0)
        final_carregando += bruto_venc - max(bruto_venc - investido, 0.0) * aliq_final

        # Perna B — vender hoje e aplicar o líquido na alternativa até a mesma data.
        liquido = a.valor_liquido
        bruto_alt = liquido * fator_alt
        aliq_alt = aliquota_ir((vencimento - data_avaliacao).days) or 0.15
        final_vendendo += bruto_alt - max(bruto_alt - liquido, 0.0) * aliq_alt
        imposto_antecipado += (a.ir or 0.0) + (a.iof or 0.0)

    vantagem = final_vendendo - final_carregando
    base = sum(a.valor_bruto for a in validas)
    vantagem_pct = vantagem / base if base > 0 else None

    if vantagem_pct is None:
        veredito, motivo = SEM_BASE, "Base de comparação nula."
    elif vantagem_pct > LIMIAR_INDIFERENCA:
        veredito = AVALIAR_TROCA
        motivo = ("A alternativa escolhida termina acima de carregar, mesmo pagando o IR "
                  "hoje. A vantagem depende inteiramente da taxa da alternativa se "
                  "confirmar — ela não é garantida por este cálculo.")
    elif vantagem_pct < -LIMIAR_INDIFERENCA:
        veredito = VENDA_DESVANTAJOSA
        motivo = ("Carregar termina acima. O imposto antecipado na venda não é recuperado "
                  "pela taxa da alternativa no prazo restante.")
    else:
        veredito = MANTER
        motivo = ("As duas pernas terminam empatadas dentro da faixa de indiferença de "
                  f"{LIMIAR_INDIFERENCA:.1%}. Sem vantagem mensurável, o custo de operar decide.")

    return Comparacao(
        veredito=veredito,
        valor_final_carregando=final_carregando,
        valor_final_vendendo=final_vendendo,
        vantagem_reais=vantagem,
        vantagem_pct=vantagem_pct,
        imposto_antecipado=imposto_antecipado,
        taxa_alternativa=taxa_alternativa,
        taxa_indice=taxa_indice,
        du_restante=du,
        aproximado=aproximado,
        motivo=motivo,
    )
