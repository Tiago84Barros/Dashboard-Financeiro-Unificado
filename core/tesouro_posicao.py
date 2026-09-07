"""
core/tesouro_posicao.py — junta Extrato Analítico + curva oficial.

É a camada que a tela consome. Duas decisões aqui valem mais que o resto do
módulo:

**Só o extrato mais recente de cada título conta.** `tesouro_lots` guarda um
conjunto de lotes por (título, data do extrato). Importar o extrato de outubro
sem descartar o de setembro somaria a mesma posição duas vezes — e o total
dobrado pareceria aporte, não bug. A seleção por `MAX(report_date)` é o que
impede isso, e está aqui, não na view.

**A marcação é opcional, o aviso não.** Quando a curva não tem o título (job
nunca rodou, título fora de oferta), a avaliação cai para o valor bruto que o
próprio extrato imprimiu — que é verdadeiro, mas da data do arquivo. O campo
`fonte_preco` viaja junto até a tela justamente para que valor velho nunca
apareça com cara de preço de hoje.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Sequence

from sqlalchemy import text

from core.tesouro_curva import indice_implicito, taxas_mais_recentes
from core.tesouro_mtm import (
    AvaliacaoLote,
    Comparacao,
    LoteTesouro,
    avaliar_lote,
    comparar_carregar_vs_vender,
    parse_taxa_contratada,
)

logger = logging.getLogger(__name__)


@dataclass
class TituloAnalitico:
    """Um título do Tesouro com seus lotes marcados a mercado."""

    security_key: str
    titulo: str
    vencimento: date
    custodiante: str | None
    report_date: date | None
    lotes: list[LoteTesouro]
    avaliacoes: list[AvaliacaoLote]

    taxa_mercado_venda: float | None
    taxa_mercado_compra: float | None
    pu_mercado: float | None
    data_curva: date | None

    quantidade: float
    valor_investido: float
    valor_bruto: float | None
    valor_liquido: float | None
    ir: float | None
    iof: float | None
    ganho_mtm_reais: float | None
    mtm_pct: float | None
    fonte_preco: str
    aproximado: bool
    indexador: str
    taxa_indice: float | None

    @property
    def marcado_a_mercado(self) -> bool:
        return self.fonte_preco == "curva" and self.mtm_pct is not None

    def comparar(self, taxa_alternativa: float | None,
                 data_avaliacao: date | None = None) -> Comparacao:
        return comparar_carregar_vs_vender(
            self.avaliacoes,
            vencimento=self.vencimento,
            data_avaliacao=data_avaliacao or (self.avaliacoes[0].data_avaliacao
                                              if self.avaliacoes else date.today()),
            taxa_mercado_resgate=self.taxa_mercado_venda,
            taxa_alternativa=taxa_alternativa,
            taxa_indice=self.taxa_indice,
            aproximado=self.aproximado,
        )


SQL_LOTES = """
SELECT l.*
  FROM tesouro_lots l
  JOIN (
        SELECT security_key, MAX(report_date) AS report_date
          FROM tesouro_lots
         WHERE user_id = :user_id
         GROUP BY security_key
       ) ultimo
    ON ultimo.security_key = l.security_key
   AND ultimo.report_date  = l.report_date
 WHERE l.user_id = :user_id
 ORDER BY l.security_key, l.application_date
"""


def indexador_dos_lotes(lotes: Sequence[LoteTesouro]) -> str:
    """'PRE' | 'SELIC' | 'IPCA' | 'IGPM' | 'MISTO'.

    Um mesmo título só tem um indexador; 'MISTO' só aparece se o extrato vier
    com taxas incoerentes, e nesse caso o índice implícito fica indefinido em
    vez de escolher um dos dois no escuro.
    """
    idxs = {lote.taxa_contratada.indexador for lote in lotes
            if lote.taxa_contratada is not None}
    if not idxs:
        return "PRE"
    return idxs.pop() if len(idxs) == 1 else "MISTO"


def _lote_de_linha(linha: Any) -> LoteTesouro:
    def num(campo: str) -> float | None:
        valor = linha._mapping.get(campo)
        return float(valor) if valor is not None else None

    return LoteTesouro(
        data_aplicacao=linha._mapping["application_date"],
        quantidade=float(linha._mapping["quantity"] or 0.0),
        preco_aplicacao=float(linha._mapping["unit_price"] or 0.0),
        valor_investido=float(linha._mapping["invested_value"] or 0.0),
        taxa_contratada=parse_taxa_contratada(linha._mapping.get("contracted_rate")),
        valor_bruto_extrato=num("gross_value"),
        dias_corridos_extrato=(int(linha._mapping["elapsed_days"])
                               if linha._mapping.get("elapsed_days") is not None else None),
        ir_extrato=num("ir_amount"),
        iof_extrato=num("iof_amount"),
        taxa_b3_extrato=num("fee_b3"),
        taxa_instituicao_extrato=num("fee_institution"),
        valor_liquido_extrato=num("net_value"),
    )


def avaliar_titulo(
    *,
    security_key: str,
    titulo: str,
    vencimento: date,
    custodiante: str | None,
    report_date: date | None,
    lotes: Sequence[LoteTesouro],
    cotacao: dict | None,
    data_avaliacao: date,
    taxa_indice: float | None = None,
) -> TituloAnalitico:
    """Marca todos os lotes de um título e consolida. Função pura.

    `cotacao` é uma linha de `core.tesouro_curva` (taxas já em decimal) ou
    ``None`` quando a curva não cobre o título. `taxa_indice` é o índice do
    papel (0,0 no prefixado, Selic/IPCA implícitos nos indexados); sem ele a
    comparação capitalizaria o ágio como se fosse o rendimento inteiro.
    """
    taxa_venda = cotacao.get("sell_rate_dec") if cotacao else None
    taxa_compra = cotacao.get("buy_rate_dec") if cotacao else None
    pu_mercado = cotacao.get("sell_pu") if cotacao else None
    data_curva = cotacao.get("base_date") if cotacao else None

    avaliacoes = [
        avaliar_lote(
            lote,
            nome_titulo=titulo,
            vencimento=vencimento,
            data_avaliacao=data_avaliacao,
            taxa_mercado_resgate=taxa_venda,
            pu_mercado=pu_mercado,
            custos=(lote.taxa_b3_extrato or 0.0) + (lote.taxa_instituicao_extrato or 0.0),
        )
        for lote in lotes
    ]

    def soma(campo: str) -> float | None:
        valores = [getattr(a, campo) for a in avaliacoes]
        validos = [v for v in valores if v is not None]
        return sum(validos) if validos else None

    investido = sum(lote.valor_investido for lote in lotes)
    bruto = soma("valor_bruto")
    ganho_mtm = soma("ganho_mtm_reais")
    # MtM da posição é ponderado pelo valor de cada lote, não média simples:
    # lotes têm tamanhos muito diferentes e a média simples daria peso igual a
    # uma aplicação de R$ 30 e a uma de R$ 30.000.
    curva = bruto - ganho_mtm if (bruto is not None and ganho_mtm is not None) else None
    mtm_pct = (ganho_mtm / curva) if (curva and curva > 0 and ganho_mtm is not None) else None

    indexador = indexador_dos_lotes(lotes)

    fontes = {a.fonte_preco for a in avaliacoes} or {"indisponivel"}
    fonte = "curva" if fontes == {"curva"} else ("extrato" if "extrato" in fontes else "indisponivel")

    return TituloAnalitico(
        security_key=security_key,
        titulo=titulo,
        vencimento=vencimento,
        custodiante=custodiante,
        report_date=report_date,
        lotes=list(lotes),
        avaliacoes=avaliacoes,
        taxa_mercado_venda=taxa_venda,
        taxa_mercado_compra=taxa_compra,
        pu_mercado=pu_mercado,
        data_curva=data_curva,
        quantidade=sum(lote.quantidade for lote in lotes),
        valor_investido=investido,
        valor_bruto=bruto,
        valor_liquido=soma("valor_liquido"),
        ir=soma("ir"),
        iof=soma("iof"),
        ganho_mtm_reais=ganho_mtm,
        mtm_pct=mtm_pct,
        fonte_preco=fonte,
        aproximado=any(a.aproximado for a in avaliacoes),
        indexador=indexador,
        taxa_indice=taxa_indice,
    )


def carregar_titulos(engine, user_id: str,
                     data_avaliacao: date | None = None) -> list[TituloAnalitico]:
    """Lê os lotes do último extrato de cada título e os marca a mercado.

    Devolve lista vazia — nunca erro — quando `tesouro_lots` ainda não existe:
    é o estado de quem não importou nenhum Extrato Analítico.
    """
    if engine is None:
        return []
    data_avaliacao = data_avaliacao or date.today()

    try:
        with engine.connect() as conn:
            linhas = conn.execute(text(SQL_LOTES), {"user_id": user_id}).fetchall()
    except Exception as exc:
        logger.info("tesouro_posicao: sem lotes analíticos (%s)", exc)
        return []

    if not linhas:
        return []

    por_titulo: dict[str, list[Any]] = {}
    for linha in linhas:
        por_titulo.setdefault(str(linha._mapping["security_key"]), []).append(linha)

    # Uma leitura só, do cardápio inteiro: a marcação usa as linhas da
    # carteira, e o índice implícito precisa das outras (o prefixado vizinho
    # é o que projeta a Selic, e o par pré/real é o que dá a inflação).
    mercado = taxas_mais_recentes(engine)
    cotacoes = ({str(r["security_key"]): r for r in mercado.to_dict("records")}
                if not mercado.empty else {})

    titulos: list[TituloAnalitico] = []
    for chave, grupo in por_titulo.items():
        cabeca = grupo[0]._mapping
        lotes = [_lote_de_linha(linha) for linha in grupo]
        indexador = indexador_dos_lotes(lotes)
        titulos.append(avaliar_titulo(
            security_key=chave,
            titulo=str(cabeca.get("title_name") or chave),
            vencimento=cabeca["maturity_date"],
            custodiante=cabeca.get("custodian"),
            report_date=cabeca.get("report_date"),
            lotes=lotes,
            cotacao=cotacoes.get(chave),
            data_avaliacao=data_avaliacao,
            taxa_indice=indice_implicito(mercado, indexador, cabeca["maturity_date"]),
        ))

    return sorted(titulos, key=lambda t: (t.vencimento, t.security_key))
