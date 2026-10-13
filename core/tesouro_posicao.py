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
from datetime import date, timedelta
from typing import Any, Sequence

from sqlalchemy import text

from core.tesouro_curva import (
    indexador_do_titulo,
    indice_implicito,
    taxas_mais_recentes,
)
from core.tesouro_mtm import (
    DIAS_UTEIS_ANO,
    AvaliacaoLote,
    Comparacao,
    LoteTesouro,
    TaxaContratada,
    avaliar_lote,
    comparar_carregar_vs_vender,
    dias_uteis,
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
    # 'extrato' (Extrato Analítico, taxa impressa por lote) ou 'movimentacao'
    # (lotes remontados das compras, taxa deduzida do PU pago).
    origem_lotes: str = "extrato"

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


# ─────────────────────────────────────────────────────────────────────────────
# Sem Extrato Analítico: os lotes saem da Movimentação
#
# Quem importou só a Movimentação da B3 tem, de cada compra, a data, a
# quantidade e o PU — falta a taxa. Ela não precisa ser inventada: no Tesouro
# Direto a compra sai na taxa de compra do dia, e a curva oficial guarda essa
# taxa junto do PU de compra. A diferença entre o PU pago e o PU publicado
# (ordem dada em outra hora) volta para a taxa pela própria fórmula do preço:
#
#     PU = VNA / (1+i)**(du/252)   ⇒   1+i_lote = (1+i_dia)·(PU_dia/PU_lote)**(252/du)
#
# O VNA é o mesmo nas duas pontas porque a data é a mesma, e por isso a conta
# vale para prefixado, IPCA+ e Selic sem conhecer o índice. Sem curva naquele
# dia, só o prefixado tem resposta exata (VNA = 1.000); os demais ficam sem
# taxa, e lote sem taxa fica sem marcação — nunca com taxa zero.
# ─────────────────────────────────────────────────────────────────────────────

SQL_MOVIMENTOS_TD = """
SELECT event_date, movement, product, quantity, unit_price, total_value
  FROM investment_movement_events
 WHERE user_id = :user_id AND product ILIKE 'tesouro%'
 ORDER BY event_date, id
"""

SQL_CURVA_NAS_COMPRAS = """
SELECT security_key, base_date, buy_rate, buy_pu
  FROM tesouro_market_rates
 WHERE security_key = ANY(:chaves) AND base_date = ANY(:datas)
"""

# Quantidade de cada título na foto de custódia mais recente do Tesouro. Lote
# remontado que não fecha com ela não é a posição do usuário.
SQL_CUSTODIA_TD = """
WITH td AS (
    SELECT a.ticker AS security_key, s.report_date, s.quantity
      FROM portfolio_position_snapshots s
      JOIN assets a ON a.id = s.asset_id
     WHERE s.user_id = :user_id
       AND (s.source_id LIKE 'td-snap-%' OR s.source_table = 'tesouro_direto')
)
SELECT security_key, SUM(quantity) AS quantidade
  FROM td
 WHERE report_date = (SELECT MAX(report_date) FROM td)
 GROUP BY security_key
"""

# Dias corridos para trás em que a curva ainda serve à compra: fim de semana e
# feriado não têm cotação.
_DIAS_ATRAS_DA_CURVA = 4
_TD_COMPRA = frozenset({"compra"})
_TD_BAIXA = frozenset({"venda", "resgate", "vencimento", "resgate antecipado"})
_TOLERANCIA_QTD = 0.005


def _sem_acento(valor: object) -> str:
    import unicodedata

    t = unicodedata.normalize("NFKD", str(valor or "")).encode("ascii", "ignore").decode()
    return t.strip().lower()


def _como_data(valor: Any) -> date | None:
    if valor is None:
        return None
    if hasattr(valor, "date") and callable(valor.date):
        return valor.date()
    return valor if isinstance(valor, date) else None


def _texto_taxa(indexador: str, valor: float) -> str:
    casas = 3 if indexador == "SELIC" else 2
    numero = f"{abs(valor) * 100:.{casas}f}".replace(".", ",")
    sinal = "-" if valor < 0 else "+"
    if indexador == "PRE":
        return f"{'-' if valor < 0 else ''}{numero}%"
    return f"{indexador} {sinal} {numero}%"


def taxa_da_compra(
    *,
    pu_compra: float,
    data_compra: date,
    vencimento: date,
    indexador: str,
    cotacao: dict | None,
) -> TaxaContratada | None:
    """Taxa contratada de uma compra a partir do PU pago. Função pura.

    ``cotacao`` é a linha da curva do dia (ou de poucos dias antes), com
    ``buy_rate`` em % ao ano e ``buy_pu``. Sem ela, só o prefixado é resolvido.
    """
    if not pu_compra or pu_compra <= 0:
        return None
    du = dias_uteis(data_compra, vencimento)
    if du <= 0:
        return None
    valor = None
    if cotacao and cotacao.get("buy_pu") and cotacao.get("buy_rate") is not None:
        pu_dia = float(cotacao["buy_pu"])
        taxa_dia = float(cotacao["buy_rate"]) / 100.0
        if pu_dia > 0:
            valor = (1.0 + taxa_dia) * (pu_dia / pu_compra) ** (DIAS_UTEIS_ANO / du) - 1.0
    elif indexador == "PRE":
        valor = (1000.0 / pu_compra) ** (DIAS_UTEIS_ANO / du) - 1.0
    if valor is None:
        return None
    return TaxaContratada(indexador=indexador, valor=valor,
                          texto=_texto_taxa(indexador, valor))


def lotes_da_movimentacao(
    movimentos: Sequence[dict],
    *,
    vencimentos: dict[str, date],
    indexadores: dict[str, str],
    curva: dict[tuple[str, date], dict],
) -> dict[str, list[LoteTesouro]]:
    """Lotes em aberto de cada título, remontados da Movimentação. Função pura.

    ``movimentos``: event_date, movement, chave, quantity, unit_price,
    total_value. Compra abre lote; venda, resgate e vencimento baixam do lote
    mais antigo (PEPS, a ordem que o Tesouro usa no IR). Transferência entre
    corretoras vem em par e não muda a posição; fica de fora, como na Evolução.
    ``curva``: ``{(chave, base_date): {"buy_rate", "buy_pu"}}``.
    """
    abertos: dict[str, list[list]] = {}  # chave -> [[data, qtd, pu, investido]]
    for m in sorted(movimentos, key=lambda x: x["event_date"]):
        tipo = _sem_acento(m.get("movement"))
        qtd = float(m.get("quantity") or 0)
        chave = m["chave"]
        if qtd <= 0:
            continue
        if tipo in _TD_COMPRA:
            total = float(m.get("total_value") or 0)
            pu = float(m.get("unit_price") or 0) or total / qtd
            abertos.setdefault(chave, []).append(
                [m["event_date"], qtd, pu, total or pu * qtd])
        elif tipo in _TD_BAIXA:
            fila = abertos.get(chave, [])
            while qtd > 1e-9 and fila:
                lote = fila[0]
                usa = min(qtd, lote[1])
                lote[3] -= lote[3] * usa / lote[1]
                lote[1] -= usa
                qtd -= usa
                if lote[1] <= 1e-9:
                    fila.pop(0)

    lotes: dict[str, list[LoteTesouro]] = {}
    for chave, fila in abertos.items():
        vencimento = vencimentos.get(chave)
        if vencimento is None:
            continue
        indexador = indexadores.get(chave, "PRE")
        for data_compra, qtd, pu, investido in fila:
            dias = (data_compra - timedelta(days=d)
                    for d in range(_DIAS_ATRAS_DA_CURVA + 1))
            cotacao = next((curva[(chave, dia)] for dia in dias
                            if (chave, dia) in curva), None)
            lotes.setdefault(chave, []).append(LoteTesouro(
                data_aplicacao=data_compra,
                quantidade=qtd,
                preco_aplicacao=pu,
                valor_investido=investido,
                taxa_contratada=taxa_da_compra(
                    pu_compra=pu, data_compra=data_compra, vencimento=vencimento,
                    indexador=indexador, cotacao=cotacao),
            ))
    return lotes


def _titulos_da_movimentacao(engine, user_id: str, data_avaliacao: date,
                             diagnostico: dict | None) -> list[TituloAnalitico]:
    from data_pipeline.importers.investments.tesouro_direto import tesouro_security_key

    try:
        with engine.connect() as conn:
            linhas = conn.execute(text(SQL_MOVIMENTOS_TD), {"user_id": user_id}).fetchall()
    except Exception as exc:
        logger.info("tesouro_posicao: sem movimentação do Tesouro (%s)", exc)
        return []
    movimentos = [
        {"event_date": _como_data(r.event_date), "movement": r.movement,
         "chave": tesouro_security_key(r.product, r.product),
         "quantity": r.quantity, "unit_price": r.unit_price,
         "total_value": r.total_value}
        for r in linhas if _como_data(r.event_date) is not None
    ]
    if not movimentos:
        return []
    # O nome da B3 ("Tesouro IPCA+ 2029") é o que a tela mostra e o que diz se
    # o papel paga cupom; o da curva não traz o ano.
    nomes = {tesouro_security_key(r.product, r.product): str(r.product) for r in linhas}

    mercado = taxas_mais_recentes(engine)
    if mercado.empty:
        return []
    cotacoes = {str(r["security_key"]): r for r in mercado.to_dict("records")}
    vencimentos = {k: _como_data(r["maturity_date"]) for k, r in cotacoes.items()}
    indexadores = {k: indexador_do_titulo(str(r.get("title_name") or ""))
                   for k, r in cotacoes.items()}

    compras = [m for m in movimentos if _sem_acento(m["movement"]) in _TD_COMPRA]
    chaves = sorted({m["chave"] for m in compras})
    datas = sorted({m["event_date"] - timedelta(days=d)
                    for m in compras for d in range(_DIAS_ATRAS_DA_CURVA + 1)})
    curva: dict[tuple[str, date], dict] = {}
    custodia: dict[str, float] = {}
    try:
        with engine.connect() as conn:
            if chaves:
                for r in conn.execute(text(SQL_CURVA_NAS_COMPRAS),
                                      {"chaves": chaves, "datas": datas}).fetchall():
                    curva[(str(r.security_key), _como_data(r.base_date))] = {
                        "buy_rate": r.buy_rate, "buy_pu": r.buy_pu}
            for r in conn.execute(text(SQL_CUSTODIA_TD), {"user_id": user_id}).fetchall():
                custodia[str(r.security_key)] = float(r.quantidade or 0)
    except Exception as exc:
        logger.info("tesouro_posicao: curva ou custódia indisponível (%s)", exc)

    lotes = lotes_da_movimentacao(movimentos, vencimentos=vencimentos,
                                  indexadores=indexadores, curva=curva)
    divergentes: list[tuple[str, float, float]] = []
    sem_taxa: list[str] = []
    titulos: list[TituloAnalitico] = []
    for chave, grupo in lotes.items():
        quantidade = sum(lote.quantidade for lote in grupo)
        if chave in custodia and abs(custodia[chave] - quantidade) > _TOLERANCIA_QTD:
            divergentes.append((chave, quantidade, custodia[chave]))
            continue
        if any(lote.taxa_contratada is None for lote in grupo):
            sem_taxa.append(chave)
        indexador = indexador_dos_lotes(grupo)
        titulo = avaliar_titulo(
            security_key=chave,
            titulo=nomes.get(chave, chave),
            vencimento=vencimentos[chave],
            custodiante=None,
            report_date=None,
            lotes=grupo,
            cotacao=cotacoes.get(chave),
            data_avaliacao=data_avaliacao,
            taxa_indice=indice_implicito(mercado, indexador, vencimentos[chave]),
        )
        titulo.origem_lotes = "movimentacao"
        titulos.append(titulo)

    if diagnostico is not None:
        diagnostico.update({
            "origem": "movimentacao",
            "divergentes": divergentes,
            "sem_taxa": sorted(sem_taxa),
            "ultima_movimentacao": max(m["event_date"] for m in movimentos),
        })
    return sorted(titulos, key=lambda t: (t.vencimento, t.security_key))


def carregar_titulos(engine, user_id: str,
                     data_avaliacao: date | None = None,
                     diagnostico: dict | None = None) -> list[TituloAnalitico]:
    """Lê os lotes do último extrato de cada título e os marca a mercado.

    Sem nenhum Extrato Analítico importado, os lotes saem da Movimentação
    (``origem_lotes == "movimentacao"``). Devolve lista vazia — nunca erro —
    quando nem um nem outro existe. ``diagnostico``, quando passado, recebe a
    origem e os títulos que não puderam ser remontados.
    """
    if engine is None:
        return []
    data_avaliacao = data_avaliacao or date.today()

    try:
        with engine.connect() as conn:
            linhas = conn.execute(text(SQL_LOTES), {"user_id": user_id}).fetchall()
    except Exception as exc:
        logger.info("tesouro_posicao: sem lotes analíticos (%s)", exc)
        linhas = []

    if not linhas:
        return _titulos_da_movimentacao(engine, user_id, data_avaliacao, diagnostico)

    if diagnostico is not None:
        diagnostico["origem"] = "extrato"

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
