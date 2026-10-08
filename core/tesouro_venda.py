"""
core/tesouro_venda.py — "dá para vender? e vale?" respondido sem adivinhar.

A pergunta de quem tem o papel na mão tem três camadas, e misturá-las é o que
torna a marcação a mercado confusa:

1. **Dá para vender a mercado?** O Tesouro recompra todo título em custódia em
   dia útil. O que pode faltar é o **preço** para dizer por quanto: sem a curva
   do dia, o valor exibido é o do extrato, e decidir por ele é decidir por um
   preço velho. Esta camada é factual.

2. **Quanto da venda de hoje vem da marcação?** O ágio (ou deságio) em reais e
   em percentual, e quanto dele sobra depois do IR. Também factual.

3. **Vale vender?** Esta **não** tem resposta em reais sozinha. O resultado de
   vender depende inteiramente de onde o dinheiro vai parar; comparar o título
   consigo mesmo perde por definição (antecipa IR e cruza o spread). O que tem
   resposta exata é a **taxa de indiferença**: acima dela trocar ganha, abaixo
   perde. É um número comparável de cabeça com o cardápio do dia, e devolve a
   decisão a quem lê em vez de fingir recomendá-la.

A diferença entre a taxa de indiferença e a taxa de recompra de hoje é o preço
anual do imposto antecipado — o único "custo de vender" que não depende de
escolha nenhuma.

Classificação pelo medido, não pelo nome
----------------------------------------
Um Tesouro Selic quase não marca: o ágio contratado é de centésimos e o papel
tem liquidez diária perto do par. Ainda assim a faixa sai da **medição** do dia,
não do nome do título; o nome só acrescenta a explicação estrutural. Convenção
que apaga o observado já custou caro neste projeto.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Sequence

from core.tesouro_curva import indexador_do_titulo
from core.tesouro_mtm import (
    LIMIAR_INDIFERENCA,
    AvaliacaoLote,
    ProjecaoCarrego,
    aliquota_ir,
    projetar_carrego,
    taxa_de_indiferenca,
)

# A faixa morta da marcação é a mesma da comparação: abaixo de 0,5% do valor da
# posição, a diferença é ruído de arredondamento de PU, de calendário e de
# spread intradiário. Um só limiar para as duas leituras evita a tela chamar de
# "ágio relevante" o que o veredito trata como empate.
LIMIAR_MARCACAO = LIMIAR_INDIFERENCA

AGIO = "AGIO"
DESAGIO = "DESAGIO"
NEUTRA = "NEUTRA"
SEM_PRECO = "SEM_PRECO"

_ROTULO = {
    AGIO: "Vendável com ágio",
    DESAGIO: "Venda realizaria deságio",
    NEUTRA: "Marcação sem efeito prático",
    SEM_PRECO: "Sem preço de mercado",
}

# A resposta à pergunta de quem abre a sub-aba: levar até o vencimento ou
# vender antes. Ela sai da mesma régua que a tela já afirmava — a taxa de
# indiferença contra a melhor oferta do dia —, só que dita com todas as letras.
# Deixar o leitor cruzar indiferença com cardápio de cabeça foi o que fez a tela
# parecer uma planilha sem resposta.
LEVAR = "LEVAR"
VENDER = "VENDER"
SEM_RESPOSTA = "SEM_RESPOSTA"

_ROTULO_VEREDITO = {
    LEVAR: "Leve até o vencimento",
    VENDER: "Vale vender e reaplicar",
    SEM_RESPOSTA: "Sem preço de hoje para decidir",
}


_PREFIXO_TAXA = {
    "IPCA": "IPCA + ",
    "IGPM": "IGP-M + ",
    "SELIC": "Selic + ",
}


def rotulo_taxa(indexador: str, taxa: float | None) -> str:
    """"14,27% a.a." para prefixado, "IPCA + 7,72% a.a." para indexado.

    Um indexado publica **spread**, e exibir 7,72% sozinho ao lado de 14,27%
    convida a comparar grandezas diferentes — o erro que o módulo de curva já
    evita na conta e que a tela não pode reintroduzir no texto.
    """
    if taxa is None:
        return "—"
    numero = f"{taxa * 100:.2f}".replace(".", ",")
    return f"{_PREFIXO_TAXA.get(indexador, '')}{numero}% a.a."


def rotulo_alternativa(alternativa: dict | None) -> str:
    """Nome + ano de vencimento.

    A curva publica ``title_name`` genérico ("Tesouro IPCA+", "Tesouro Selic"):
    o vencimento mora em outra coluna. Sem ele, duas ofertas do cardápio
    aparecem com o mesmo nome e a frase fica ambígua exatamente onde precisa
    ser específica.
    """
    if not alternativa:
        return "—"
    nome = str(alternativa.get("title_name") or "").strip()
    venc = alternativa.get("maturity_date")
    ano = getattr(venc, "year", None)
    return f"{nome} {ano}".strip() if ano else nome


@dataclass(frozen=True)
class LeituraVenda:
    """Tudo o que a tela precisa dizer sobre vender **um** título, hoje."""

    security_key: str
    titulo: str
    vencimento: date
    indexador: str
    situacao: str
    rotulo: str
    pode_marcar: bool
    liquidez_diaria: bool

    mtm_pct: float | None
    ganho_mtm_reais: float | None
    ganho_mtm_liquido: float | None

    valor_investido: float
    liquido_hoje: float | None
    liquido_vencimento: float | None
    imposto_antecipado: float | None
    du_restante: int
    depende_do_indice: bool

    taxa_mercado: float | None
    taxa_indiferenca: float | None
    custo_anual_do_ir: float | None

    alternativa: dict | None
    alternativa_supera: bool | None

    projecao: ProjecaoCarrego
    frase: str

    veredito: str
    veredito_rotulo: str
    veredito_motivo: str


def _melhor_alternativa(
    *, security_key: str, indexador: str, vencimento: date,
    cardapio: Iterable[dict],
) -> dict | None:
    """O título à venda hoje que mais paga, no mesmo indexador e prazo ≥.

    A regra é dita aqui e não escolhida na tela porque ela é o que dá sentido à
    comparação: ágio de Selic e taxa cheia de prefixado são grandezas
    diferentes, e duas pernas que terminam em datas diferentes não se comparam.
    Prazo **maior ou igual** porque encurtar o prazo troca o risco junto com o
    papel, e aí a conta já não é a mesma pergunta.
    """
    melhor = None
    for linha in cardapio:
        if linha.get("security_key") == security_key:
            continue
        if indexador_do_titulo(linha.get("title_name")) != indexador:
            continue
        venc = linha.get("maturity_date")
        taxa = linha.get("buy_rate_dec")
        if venc is None or vencimento is None or venc < vencimento:
            continue
        if taxa is None or taxa != taxa:  # NaN
            continue
        if melhor is None or taxa > melhor["buy_rate_dec"]:
            melhor = dict(linha)
    return melhor


def _frase(situacao: str, *, indexador: str, liquidez_diaria: bool,
           supera: bool | None, alternativa: dict | None) -> str:
    """Uma linha que diz o que fazer com o número, sem virar recomendação."""
    if situacao == SEM_PRECO:
        return ("O valor mostrado é o do extrato, na data em que ele foi gerado. "
                "Sem a curva do dia não há como dizer por quanto o Tesouro "
                "recompraria hoje.")

    if situacao == NEUTRA:
        base = ("A marcação está dentro da faixa de ruído: vender ou carregar "
                "entrega praticamente o mesmo valor, e o que decide é o imposto.")
        if liquidez_diaria:
            base += (" É o comportamento esperado de um Tesouro Selic — ele "
                     "acompanha a taxa diária e negocia perto do par, então "
                     "quase não tem marcação para realizar.")
        return base

    if situacao == DESAGIO:
        base = ("Vender hoje realiza o deságio: a taxa de mercado está acima da "
                "que você contratou, e o preço de recompra está abaixo da curva "
                "do seu lote. Carregar até o vencimento entrega a taxa "
                "contratada e dispensa a marcação.")
    else:
        base = ("Há ágio: a taxa de mercado caiu abaixo da que você contratou, "
                "e o preço de recompra está acima da curva do seu lote. O ágio "
                "só vira vantagem se o dinheiro for para algo que renda mais "
                "que a taxa de indiferença.")

    if supera is None or alternativa is None:
        return base + (" Nenhum título do mesmo indexador e prazo igual ou "
                       "maior está sendo ofertado para comparar.")
    nome = rotulo_alternativa(alternativa)
    taxa_txt = rotulo_taxa(indexador, alternativa.get("buy_rate_dec"))
    if supera:
        return (base + f" A melhor oferta de hoje no mesmo indexador é {nome}, "
                f"ofertado a {taxa_txt} na compra, e ela passa da taxa de "
                "indiferença: trocar termina acima de carregar, se essa taxa se "
                "confirmar até o vencimento.")
    return (base + f" A melhor oferta de hoje no mesmo indexador é {nome}, "
            f"ofertado a {taxa_txt} na compra, e ela não alcança a taxa de "
            "indiferença: trocar termina abaixo de carregar.")


def _veredito(situacao: str, *, indexador: str, liquidez_diaria: bool,
              supera: bool | None, alternativa: dict | None,
              taxa_indiferenca: float | None, vencimento: date | None,
              ganho_liquido: float | None,
              imposto_antecipado: float | None) -> tuple[str, str]:
    """Levar ou vender, e o motivo em uma frase.

    Vender só ganha de carregar quando o dinheiro tem para onde ir rendendo
    mais que a taxa de indiferença — é a única régua que já desconta o imposto
    antecipado e a marcação. Sem oferta que passe dela, o ágio não paga a troca
    e o deságio seria perda realizada; nos dois casos a resposta é carregar.
    """
    if situacao == SEM_PRECO:
        return SEM_RESPOSTA, ("A curva do Tesouro do dia não tem este título: "
                              "sem o preço de recompra de hoje não há como "
                              "comparar vender com carregar.")

    alvo = rotulo_taxa(indexador, taxa_indiferenca)
    prazo = f" até {vencimento:%m/%Y}" if vencimento else ""
    if supera and alternativa is not None:
        return VENDER, (
            f"Vendendo hoje e comprando {rotulo_alternativa(alternativa)} a "
            f"{rotulo_taxa(indexador, alternativa.get('buy_rate_dec'))}, você "
            f"termina acima de carregar: a troca precisa render mais de {alvo}"
            f"{prazo}, e essa oferta passa disso.")

    if situacao == DESAGIO:
        perda = (f" de {_reais_simples(abs(ganho_liquido))}"
                 if ganho_liquido is not None else "")
        return LEVAR, (f"Vender hoje realizaria a perda{perda} da marcação; "
                       "carregando até o vencimento você recebe a taxa que "
                       "contratou.")
    if situacao == NEUTRA:
        if liquidez_diaria:
            return LEVAR, ("Tesouro Selic quase não marca: vender antes não "
                           "rende nada a mais e só antecipa "
                           f"{_reais_simples(imposto_antecipado)} de imposto. "
                           "Venda só quando precisar do dinheiro.")
        return LEVAR, ("A marcação de hoje é praticamente zero: vender antes "
                       f"só anteciparia {_reais_simples(imposto_antecipado)} "
                       "de imposto.")

    # Ágio sem destino que o pague.
    premio = (f"O ágio de hoje ({_reais_simples(ganho_liquido)} depois do IR) "
              if ganho_liquido is not None else "O ágio de hoje ")
    if alternativa is None:
        return LEVAR, (premio + "não compensa sozinho: vender só valeria "
                       f"reaplicando a mais de {alvo}{prazo}, e o Tesouro não "
                       "oferta hoje título do mesmo indexador com prazo igual "
                       "ou maior para isso.")
    return LEVAR, (premio + "não compensa: vender só valeria reaplicando a "
                   f"mais de {alvo}{prazo}, e a melhor oferta de hoje paga "
                   f"{rotulo_taxa(indexador, alternativa.get('buy_rate_dec'))} "
                   f"({rotulo_alternativa(alternativa)}).")


def ler_venda(titulo, *, data_avaliacao: date,
              cardapio: Sequence[dict] = ()) -> LeituraVenda:
    """Monta a leitura de venda de um `core.tesouro_posicao.TituloAnalitico`.

    Função pura: tudo o que ela sabe vem do título já avaliado e do cardápio do
    dia. Nenhuma consulta, nenhuma data implícita — a data de avaliação é a da
    curva que marcou a posição, e não ``hoje``, para que o número e a procedência
    nunca se descolem.
    """
    avaliacoes: list[AvaliacaoLote] = list(titulo.avaliacoes)
    projecao = projetar_carrego(
        avaliacoes, vencimento=titulo.vencimento, data_avaliacao=data_avaliacao,
        taxa_mercado_resgate=titulo.taxa_mercado_venda,
        taxa_indice=titulo.taxa_indice, aproximado=titulo.aproximado)

    pode_marcar = bool(titulo.marcado_a_mercado)
    mtm = titulo.mtm_pct
    if not pode_marcar or mtm is None:
        situacao = SEM_PRECO
    elif abs(mtm) < LIMIAR_MARCACAO:
        situacao = NEUTRA
    elif mtm > 0:
        situacao = AGIO
    else:
        situacao = DESAGIO

    # O ágio bruto não é o que entra no bolso: a parte dele que é ganho paga IR
    # na alíquota do prazo já corrido. Exibir só o bruto superestima o prêmio
    # exatamente no caso em que ele é usado para decidir.
    ganho = titulo.ganho_mtm_reais
    ganho_liquido = None
    if ganho is not None:
        dias = max((a.dias_corridos for a in avaliacoes), default=0)
        aliq = aliquota_ir(dias)
        ganho_liquido = ganho * (1.0 - aliq) if (aliq is not None and ganho > 0) else ganho

    indiferenca = taxa_de_indiferenca(
        avaliacoes, vencimento=titulo.vencimento, data_avaliacao=data_avaliacao,
        taxa_mercado_resgate=titulo.taxa_mercado_venda,
        taxa_indice=titulo.taxa_indice)
    custo_ir = (indiferenca - titulo.taxa_mercado_venda
                if (indiferenca is not None and titulo.taxa_mercado_venda is not None)
                else None)

    alternativa = _melhor_alternativa(
        security_key=titulo.security_key, indexador=titulo.indexador,
        vencimento=titulo.vencimento, cardapio=cardapio)
    supera = None
    if alternativa is not None and indiferenca is not None:
        supera = alternativa["buy_rate_dec"] > indiferenca

    liquidez = titulo.indexador == "SELIC"
    veredito, motivo = _veredito(
        situacao, indexador=titulo.indexador, liquidez_diaria=liquidez,
        supera=supera, alternativa=alternativa, taxa_indiferenca=indiferenca,
        vencimento=titulo.vencimento, ganho_liquido=ganho_liquido,
        imposto_antecipado=projecao.imposto_antecipado)
    return LeituraVenda(
        security_key=titulo.security_key,
        titulo=titulo.titulo,
        vencimento=titulo.vencimento,
        indexador=titulo.indexador,
        situacao=situacao,
        rotulo=_ROTULO[situacao],
        pode_marcar=pode_marcar,
        liquidez_diaria=liquidez,
        mtm_pct=mtm,
        ganho_mtm_reais=ganho,
        ganho_mtm_liquido=ganho_liquido,
        valor_investido=titulo.valor_investido,
        liquido_hoje=projecao.valor_liquido_hoje,
        liquido_vencimento=projecao.valor_liquido_vencimento,
        imposto_antecipado=projecao.imposto_antecipado,
        du_restante=projecao.du_restante,
        depende_do_indice=projecao.depende_do_indice,
        taxa_mercado=titulo.taxa_mercado_venda,
        taxa_indiferenca=indiferenca,
        custo_anual_do_ir=custo_ir,
        alternativa=alternativa,
        alternativa_supera=supera,
        projecao=projecao,
        frase=_frase(situacao, indexador=titulo.indexador,
                     liquidez_diaria=liquidez, supera=supera,
                     alternativa=alternativa),
        veredito=veredito,
        veredito_rotulo=_ROTULO_VEREDITO[veredito],
        veredito_motivo=motivo,
    )


def resumo_da_carteira(leituras: Sequence[LeituraVenda]) -> dict:
    """Consolida as leituras para o topo da tela.

    O ágio da carteira é a **soma** dos ganhos de marcação, não a média das
    porcentagens: a média trataria um lote de R$ 1.600 e um de R$ 51.000 como
    iguais. O percentual volta no fim, sobre a base marcada.
    """
    marcadas = [lv for lv in leituras if lv.pode_marcar]
    ganho = sum(lv.ganho_mtm_reais or 0.0 for lv in marcadas)
    ganho_liquido = sum(lv.ganho_mtm_liquido or 0.0 for lv in marcadas)
    base = sum((lv.projecao.valor_bruto_hoje or 0.0) - (lv.ganho_mtm_reais or 0.0)
               for lv in marcadas)
    return {
        "titulos": len(leituras),
        "marcados": len(marcadas),
        "com_agio": sum(1 for lv in leituras if lv.situacao == AGIO),
        "com_desagio": sum(1 for lv in leituras if lv.situacao == DESAGIO),
        "neutros": sum(1 for lv in leituras if lv.situacao == NEUTRA),
        "sem_preco": sum(1 for lv in leituras if lv.situacao == SEM_PRECO),
        "investido": sum(lv.valor_investido for lv in leituras),
        "liquido_hoje": sum(lv.liquido_hoje or 0.0 for lv in leituras),
        "liquido_vencimento": (sum(lv.liquido_vencimento or 0.0 for lv in leituras)
                               if any(lv.liquido_vencimento is not None for lv in leituras)
                               else None),
        "imposto_antecipado": sum(lv.imposto_antecipado or 0.0 for lv in leituras),
        "ganho_mtm": ganho if marcadas else None,
        "ganho_mtm_liquido": ganho_liquido if marcadas else None,
        "mtm_pct": (ganho / base) if base > 0 else None,
        "projecao_parcial": any(lv.liquido_vencimento is None for lv in leituras),
        "depende_do_indice": any(lv.depende_do_indice for lv in leituras),
    }


def mais_vantajoso_de_vender(leituras: Sequence[LeituraVenda]) -> int | None:
    """Em qual papel a tela deve abrir: o que mais compensa vender hoje.

    A régua é a que o resto da sub-aba afirma, e ela não é "quem subiu mais".
    Ágio só vira vantagem quando o dinheiro tem para onde ir: por isso quem tem
    oferta do dia passando da taxa de indiferença vem antes de quem tem ágio sem
    destino melhor. Dentro de cada grupo o desempate é o ganho **líquido** em
    reais — 3% de um lote de R$ 1.600 não decide o mesmo que 1% de um de
    R$ 51.000, e o imposto antecipado já saiu do número.

    Devolve ``None`` quando ninguém está com ágio relevante. Abrir num deságio
    seria a tela destacando justamente o movimento que ela desaconselha, e
    inventar destaque onde não há é pior do que abrir no primeiro da lista.
    """
    candidatos = [(i, lv) for i, lv in enumerate(leituras)
                  if lv.pode_marcar and lv.situacao == AGIO]
    if not candidatos:
        return None
    # `-i` no fim: empate exato fica com o primeiro da carteira, para a caixa
    # não trocar de papel sozinha entre dois reruns idênticos.
    return max(candidatos,
               key=lambda par: (1 if par[1].alternativa_supera else 0,
                                par[1].ganho_mtm_liquido or 0.0,
                                -par[0]))[0]


def frase_do_destaque(leitura: LeituraVenda | None) -> str:
    """Por que a caixa abriu onde abriu — dito antes que o usuário pergunte.

    Sem isso o destaque viraria recomendação muda: o papel aparece em primeiro
    e a tela não diz com que régua ele chegou lá, nem que a régua tem limite.
    """
    if leitura is None:
        return ("Nenhum título está com ágio relevante hoje: a caixa abre no "
                "primeiro da carteira.")
    if leitura.alternativa_supera:
        return (f"Mais vantajoso de vender hoje: {leitura.titulo} — é o maior "
                "ágio líquido entre os papéis cuja melhor oferta de hoje passa "
                "da taxa de indiferença.")
    if leitura.alternativa_supera is False:
        return (f"Maior ágio líquido hoje: {leitura.titulo} — nenhuma oferta do "
                "dia alcança a taxa de indiferença, então trocar termina abaixo "
                "de carregar.")
    return (f"Maior ágio líquido hoje: {leitura.titulo} — sem oferta do mesmo "
            "indexador e prazo para comparar, não dá para dizer se trocar "
            "supera a taxa de indiferença.")


def veredito_da_carteira(leituras: Sequence[LeituraVenda]) -> str:
    """O tom da carteira: basta um título valendo a troca para pedir atenção."""
    if any(lv.veredito == VENDER for lv in leituras):
        return VENDER
    if leituras and all(lv.veredito == SEM_RESPOSTA for lv in leituras):
        return SEM_RESPOSTA
    return LEVAR


def frase_do_veredito(leituras: Sequence[LeituraVenda]) -> str:
    """Por que a resposta é essa — a régua, dita uma vez para a carteira."""
    if not leituras:
        return "Nada a decidir."
    if all(lv.veredito == SEM_RESPOSTA for lv in leituras):
        return ("A curva do Tesouro do dia ainda não foi coletada: os valores "
                "são os do extrato, e sem o preço de recompra de hoje não há "
                "como comparar vender com carregar.")
    regra = ("Vender antes só compensa se o dinheiro for para algo que renda "
             "mais do que o título já entrega até o vencimento, descontado o "
             "imposto que a venda antecipa.")
    if any(lv.veredito == VENDER for lv in leituras):
        return regra + (" Há oferta do Tesouro hoje que passa dessa conta — o "
                        "card do título diz qual.")
    return regra + (" Nenhuma oferta do Tesouro hoje passa dessa conta; o "
                    "motivo de cada título está no card dele.")


def manchete_do_veredito(leituras: Sequence[LeituraVenda]) -> str:
    """A resposta da carteira inteira, numa linha.

    "Leve todos até o vencimento" quando é o que vale para todos; quando algum
    título vale a troca, ele é nomeado — a manchete não pode esconder a
    exceção atrás de uma contagem.
    """
    if not leituras:
        return "Sem títulos do Tesouro Direto na carteira"
    vender = [lv for lv in leituras if lv.veredito == VENDER]
    sem = [lv for lv in leituras if lv.veredito == SEM_RESPOSTA]
    if vender:
        nomes = ", ".join(lv.titulo for lv in vender)
        return f"Vale vender {nomes}" + (
            "; os demais, leve até o vencimento"
            if len(vender) < len(leituras) - len(sem) else "")
    if len(sem) == len(leituras):
        return "Sem preço de hoje para decidir"
    if sem:
        return (f"Leve até o vencimento — {len(sem)} "
                f"título{'s' if len(sem) != 1 else ''} sem preço de hoje")
    if len(leituras) == 1:
        return "Leve até o vencimento"
    return f"Leve os {len(leituras)} títulos até o vencimento"


def _reais_simples(valor: float | None) -> str:
    """R$ só para encaixar na frase; a formatação da tela mora em ``design``."""
    if valor is None:
        return "—"
    corpo = f"{valor:,.2f}".replace(",", "~").replace(".", ",").replace("~", ".")
    return f"R$ {corpo}"
