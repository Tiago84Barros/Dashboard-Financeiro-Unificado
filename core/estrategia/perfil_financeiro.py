"""Perfil financeiro do usuário para a entrevista da estratégia.

Resume os últimos 12 meses FECHADOS do Controle Financeiro (o mês corrente
fica de fora: pela metade, derrubaria a média de renda e de gasto) num bloco
de texto que a IA da entrevista lê como contexto. Serve para ela avaliar a
forma de gasto, o padrão de consumo, a renda média e as despesas recorrentes,
e com isso perguntar melhor: aporte compatível com a sobra real, reserva de
emergência em meses de despesa, capacidade de risco diante da estabilidade da
renda. Nunca vira resposta: a política só registra o que o usuário confirmar.

Regras de leitura, as mesmas do Controle Financeiro:
- despesas do caixa NÃO incluem compras no cartão (elas viram fatura futura);
  o cartão aparece à parte e nunca é somado às despesas;
- aporte não é despesa: sobra = receitas − despesas, e o aporte é o destino
  de parte dessa sobra;
- dado de demonstração (MOCK_MODE ou fallback mock) fica de fora.

Puro (``resumir`` e ``texto``) mais um carregador (``carregar``) que lê os
repositórios do Controle Financeiro e nunca levanta erro.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import statistics
from dataclasses import dataclass, field

from core.categorias import normalizar
from core.llm_context_financeiro import classificar_essencialidade

logger = logging.getLogger(__name__)

MESES = 12
# Categoria presente em ao menos 75% dos meses com dado é despesa recorrente.
FRACAO_RECORRENTE = 0.75
# Compra do cartão em ao menos 3 faturas distintas é gasto recorrente.
MIN_FATURAS_RECORRENTE = 3
MAX_ITENS = 8
_MESES_PT = ("jan", "fev", "mar", "abr", "mai", "jun",
             "jul", "ago", "set", "out", "nov", "dez")


@dataclass(frozen=True)
class Categoria:
    nome: str
    media_mensal: float        # total no período / meses com dado
    meses: int                 # meses em que apareceu
    recorrente: bool
    essencialidade: str        # essencial | nao_essencial | nao_classificada


@dataclass(frozen=True)
class GastoCartao:
    nome: str
    media_mensal: float        # por fatura em que apareceu
    faturas: int


@dataclass(frozen=True)
class Perfil:
    meses: int                              # meses fechados com lançamento
    periodo: str                            # "out/25 a set/26"
    renda_media: float
    renda_mediana: float
    renda_min: float
    renda_max: float
    renda_cv: float | None                  # desvio / média; None com < 3 meses
    despesa_media: float
    despesa_max: float
    aporte_medio: float
    meses_com_aporte: int
    sobra_media: float                      # receitas − despesas (antes do aporte)
    meses_deficit: int
    comprometido_pct: float | None          # despesas / receitas no período
    sobra_pct: float | None                 # sobra / receitas no período
    aporte_pct: float | None                # aportes / receitas no período
    categorias: list[Categoria] = field(default_factory=list)
    despesa_recorrente_media: float = 0.0
    essencial_pct: float | None = None      # das despesas classificáveis do caixa
    sem_classificacao_media: float = 0.0    # fica fora do essencial_pct
    pagamento_fatura_media: float = 0.0     # categoria de fatura no caixa
    fatura_media: float | None = None
    faturas: int = 0
    cartao_categorias: list[Categoria] = field(default_factory=list)
    cartao_recorrentes: list[GastoCartao] = field(default_factory=list)
    parcelamentos_ativos: int = 0
    parcelas_mensais: float = 0.0
    parcelamentos_saldo: float = 0.0


# -- janela ----------------------------------------------------------------------

def janela(hoje: dt.date | None = None, n: int = MESES) -> list[tuple[int, int]]:
    """Os ``n`` meses fechados antes do mês de ``hoje``, do mais antigo ao atual."""
    hoje = hoje or dt.date.today()
    ano, mes = hoje.year, hoje.month
    saida = []
    for _ in range(n):
        mes -= 1
        if mes == 0:
            ano, mes = ano - 1, 12
        saida.append((ano, mes))
    return saida[::-1]


def _rotulo(ano: int, mes: int) -> str:
    return f"{_MESES_PT[mes - 1]}/{str(ano)[-2:]}"


# -- resumo puro -------------------------------------------------------------------

def _eh_pagamento_fatura(nome: str) -> bool:
    n = normalizar(nome)
    return "fatura" in n or "cartao" in n


def _chave_compra(descricao: object) -> str:
    """Estabelecimento sem números nem pontuação: agrupa a mesma cobrança."""
    n = normalizar(str(descricao or "")).split("|")[0]
    n = re.sub(r"parcela|parc", " ", n)
    n = re.sub(r"[^a-z ]+", " ", n)
    return " ".join(n.split())[:40]


def _categorias(meses: list[dict], chave: str, n_meses: int) -> list[Categoria]:
    total: dict[str, float] = {}
    presenca: dict[str, int] = {}
    for m in meses:
        vistos = set()
        for c in m.get(chave) or []:
            nome = str(c.get("nome") or "Sem categoria")
            gasto = abs(float(c.get("gasto") or 0))
            if gasto <= 0:
                continue
            total[nome] = total.get(nome, 0.0) + gasto
            vistos.add(nome)
        for nome in vistos:
            presenca[nome] = presenca.get(nome, 0) + 1
    limite = max(2, round(n_meses * FRACAO_RECORRENTE))
    saida = [Categoria(nome=nome, media_mensal=round(v / n_meses, 2),
                       meses=presenca[nome],
                       recorrente=presenca[nome] >= limite,
                       essencialidade=classificar_essencialidade(nome))
             for nome, v in total.items()]
    return sorted(saida, key=lambda c: -c.media_mensal)


def _recorrentes_cartao(compras: list[dict], validos: set[tuple[int, int]]
                        ) -> list[GastoCartao]:
    grupos: dict[str, dict] = {}
    for tx in compras:
        data = tx.get("data")
        if not isinstance(data, dt.date) or (data.year, data.month) not in validos:
            continue
        if int(tx.get("installment_total") or 1) > 1:
            continue          # parcela é compra avulsa, não recorrência
        chave = _chave_compra(tx.get("descricao"))
        if len(chave) < 3:
            continue
        g = grupos.setdefault(chave, {"total": 0.0, "faturas": set(),
                                      "nome": str(tx.get("descricao") or chave)})
        g["total"] += abs(float(tx.get("valor") or 0))
        g["faturas"].add((data.year, data.month))
    saida = [GastoCartao(nome=g["nome"].split("|")[0].strip()[:40],
                         media_mensal=round(g["total"] / len(g["faturas"]), 2),
                         faturas=len(g["faturas"]))
             for g in grupos.values() if len(g["faturas"]) >= MIN_FATURAS_RECORRENTE]
    return sorted(saida, key=lambda g: -g.media_mensal)[:MAX_ITENS]


def resumir(meses: list[dict], *, faturas: list[dict] | None = None,
            compras_cartao: list[dict] | None = None,
            dividas: list[dict] | None = None) -> Perfil | None:
    """Resume os meses do caixa e o cartão. None sem nenhum mês com lançamento.

    ``meses``: ``{ano, mes, receitas, despesas, aportes, categorias: [{nome,
    gasto}]}`` só de dado real; mês sem receita nem despesa é mês sem lançamento
    e fica fora das médias. ``faturas``: ``{ano, mes, total}`` das faturas do
    cartão. ``compras_cartao``: compras da fatura (``data`` = vencimento),
    para os gastos recorrentes e as categorias do cartão. ``dividas``:
    parcelamentos de ``core.controle.get_dividas_cc``.
    """
    com_dado = [m for m in meses
                if float(m.get("receitas") or 0) > 0 or float(m.get("despesas") or 0) > 0]
    if not com_dado:
        return None
    n = len(com_dado)
    rendas = [float(m.get("receitas") or 0) for m in com_dado]
    despesas = [float(m.get("despesas") or 0) for m in com_dado]
    aportes = [abs(float(m.get("aportes") or 0)) for m in com_dado]
    r_tot, d_tot, a_tot = sum(rendas), sum(despesas), sum(aportes)
    media = r_tot / n
    cv = (statistics.pstdev(rendas) / media) if n >= 3 and media > 0 else None

    cats = _categorias(com_dado, "categorias", n)
    fatura_cx = sum(c.media_mensal for c in cats if _eh_pagamento_fatura(c.nome))
    consumo = [c for c in cats if not _eh_pagamento_fatura(c.nome)]
    classificaveis = [c for c in consumo if c.essencialidade != "nao_classificada"]
    base = sum(c.media_mensal for c in classificaveis)
    essencial = sum(c.media_mensal for c in classificaveis
                    if c.essencialidade == "essencial")

    validos = {(int(m["ano"]), int(m["mes"])) for m in meses}
    fat = [f for f in (faturas or [])
           if (int(f.get("ano") or 0), int(f.get("mes") or 0)) in validos
           and float(f.get("total") or 0) > 0]

    compras = [tx for tx in (compras_cartao or [])
               if tx.get("eh_despesa") and tx.get("account_type") == "credit_card"]
    por_fatura: dict[tuple[int, int], dict[str, float]] = {}
    for tx in compras:
        data = tx.get("data")
        if isinstance(data, dt.date) and (data.year, data.month) in validos:
            cat = por_fatura.setdefault((data.year, data.month), {})
            nome = str(tx.get("categoria") or "Sem categoria")
            cat[nome] = cat.get(nome, 0.0) + abs(float(tx.get("valor") or 0))
    meses_cartao = [{"categorias": [{"nome": k, "gasto": v} for k, v in d.items()]}
                    for d in por_fatura.values()]
    cartao_cats = (_categorias(meses_cartao, "categorias", len(meses_cartao))
                   if meses_cartao else [])

    ativas = [d for d in (dividas or []) if d.get("is_ativa")]
    parcela = 0.0
    saldo = 0.0
    for d in ativas:
        tp = max(int(d.get("total_parcelas") or 1), 1)
        valor = float(d.get("total_compra") or 0) / tp
        parcela += valor
        saldo += valor * int(d.get("parcelas_restantes") or 0)

    primeiro, ultimo = com_dado[0], com_dado[-1]
    return Perfil(
        meses=n,
        periodo=(f"{_rotulo(primeiro['ano'], primeiro['mes'])} a "
                 f"{_rotulo(ultimo['ano'], ultimo['mes'])}"),
        renda_media=round(media, 2),
        renda_mediana=round(statistics.median(rendas), 2),
        renda_min=round(min(rendas), 2), renda_max=round(max(rendas), 2),
        renda_cv=round(cv, 3) if cv is not None else None,
        despesa_media=round(d_tot / n, 2), despesa_max=round(max(despesas), 2),
        aporte_medio=round(a_tot / n, 2),
        meses_com_aporte=sum(1 for a in aportes if a > 0),
        sobra_media=round((r_tot - d_tot) / n, 2),
        meses_deficit=sum(1 for r, d in zip(rendas, despesas) if d > r),
        comprometido_pct=round(d_tot / r_tot * 100, 1) if r_tot > 0 else None,
        sobra_pct=round((r_tot - d_tot) / r_tot * 100, 1) if r_tot > 0 else None,
        aporte_pct=round(a_tot / r_tot * 100, 1) if r_tot > 0 else None,
        categorias=consumo[:MAX_ITENS],
        despesa_recorrente_media=round(sum(c.media_mensal for c in consumo
                                           if c.recorrente), 2),
        essencial_pct=round(essencial / base * 100, 1) if base > 0 else None,
        sem_classificacao_media=round(sum(
            c.media_mensal for c in consumo
            if c.essencialidade == "nao_classificada"), 2),
        pagamento_fatura_media=round(fatura_cx, 2),
        fatura_media=(round(sum(float(f["total"]) for f in fat) / len(fat), 2)
                      if fat else None),
        faturas=len(fat),
        cartao_categorias=cartao_cats[:6],
        cartao_recorrentes=_recorrentes_cartao(compras, validos),
        parcelamentos_ativos=len(ativas),
        parcelas_mensais=round(parcela, 2),
        parcelamentos_saldo=round(saldo, 2),
    )


# -- texto para a IA ---------------------------------------------------------------

def _brl(v: float) -> str:
    return ("R$ " + f"{float(v or 0):,.2f}").replace(",", "X").replace(
        ".", ",").replace("X", ".")


def _pct(v: float | None) -> str:
    return "n/d" if v is None else f"{v:.1f}%".replace(".", ",")


_ESSENC = {"essencial": "essencial", "nao_essencial": "não essencial",
           "nao_classificada": "sem classificação"}


def _estabilidade(cv: float | None) -> str:
    if cv is None:
        return "poucos meses para medir"
    if cv < 0.10:
        return "estável"
    if cv < 0.25:
        return "variação moderada"
    return "muito variável"


def texto(p: Perfil) -> str:
    """Bloco de contexto, em linhas "- ", legível pela IA e pelo usuário."""
    linhas = [
        f"- Período: {p.periodo} ({p.meses} meses fechados com lançamento).",
        f"- Renda média mensal: {_brl(p.renda_media)} (mediana "
        f"{_brl(p.renda_mediana)}; mínima {_brl(p.renda_min)}, máxima "
        f"{_brl(p.renda_max)}). Estabilidade da renda: "
        f"{_estabilidade(p.renda_cv)}"
        + (f" (coeficiente de variação {p.renda_cv:.2f})".replace(".", ",") + "."
           if p.renda_cv is not None else "."),
        f"- Despesa média mensal do caixa: {_brl(p.despesa_media)} "
        f"(maior mês {_brl(p.despesa_max)}); renda comprometida "
        f"{_pct(p.comprometido_pct)}.",
        f"- Sobra média (receitas − despesas, antes de investir): "
        f"{_brl(p.sobra_media)} por mês ({_pct(p.sobra_pct)} da renda). "
        f"Meses com déficit: {p.meses_deficit} de {p.meses}.",
        f"- Aporte médio em investimentos: {_brl(p.aporte_medio)} por mês "
        f"({_pct(p.aporte_pct)} da renda); houve aporte em "
        f"{p.meses_com_aporte} de {p.meses} meses.",
        f"- Reserva de emergência de referência: 6 meses de despesa = "
        f"{_brl(p.despesa_media * 6)}; 12 meses = {_brl(p.despesa_media * 12)}.",
    ]
    if p.categorias:
        linhas.append("- Onde o caixa gasta (média mensal; R = recorrente, "
                      "presente em quase todo mês): " + "; ".join(
            f"{c.nome} {_brl(c.media_mensal)}{' R' if c.recorrente else ''} "
            f"({_ESSENC[c.essencialidade]})" for c in p.categorias) + ".")
        linhas.append(f"- Despesas recorrentes do caixa somam "
                      f"{_brl(p.despesa_recorrente_media)} por mês."
                      + (f" Essenciais são {_pct(p.essencial_pct)} das despesas "
                         "classificáveis" if p.essencial_pct is not None else "")
                      + (f" ({_brl(p.sem_classificacao_media)} por mês sem "
                         "classificação ficam fora dessa conta)."
                         if p.essencial_pct is not None and p.sem_classificacao_media
                         else "." if p.essencial_pct is not None else ""))
    if p.pagamento_fatura_media > 0:
        linhas.append(f"- Pagamento de fatura dentro das despesas do caixa: "
                      f"{_brl(p.pagamento_fatura_media)} por mês (é o cartão "
                      "sendo pago; não some com a fatura abaixo).")
    if p.fatura_media is not None:
        linhas.append(f"- Cartão de crédito: fatura média {_brl(p.fatura_media)} "
                      f"({p.faturas} faturas no período). Fica fora das despesas "
                      "do caixa acima.")
    if p.cartao_categorias:
        linhas.append("- Cartão por categoria (média por fatura): " + "; ".join(
            f"{c.nome} {_brl(c.media_mensal)} ({_ESSENC[c.essencialidade]})"
            for c in p.cartao_categorias) + ".")
    if p.cartao_recorrentes:
        linhas.append("- Cobranças que se repetem no cartão (mesmo estabelecimento "
                      f"em {MIN_FATURAS_RECORRENTE}+ faturas, sem parcelamento; "
                      "podem ser assinaturas ou compras frequentes): "
                      + "; ".join(f"{g.nome} {_brl(g.media_mensal)} "
                                  f"({g.faturas} faturas)"
                                  for g in p.cartao_recorrentes) + ".")
    if p.parcelamentos_ativos:
        linhas.append(f"- Parcelamentos ativos no cartão: {p.parcelamentos_ativos}, "
                      f"somando {_brl(p.parcelas_mensais)} por mês e "
                      f"{_brl(p.parcelamentos_saldo)} ainda a pagar.")
    return "\n".join(linhas)


# -- carregador ----------------------------------------------------------------------

def _mes_real(ano: int, mes: int) -> dict | None:
    from core.controle import get_controle
    dados = get_controle(ano, mes)
    if dados.get("data_source") != "real":
        return None
    aportes = sum(abs(float(t.get("valor") or 0))
                  for t in dados.get("transacoes") or []
                  if t.get("eh_investimento")
                  and t.get("account_type") != "credit_card")
    return {"ano": ano, "mes": mes,
            "receitas": float(dados.get("receitas") or 0),
            "despesas": float(dados.get("despesas") or 0),
            "aportes": aportes,
            "categorias": [{"nome": c.get("nome"), "gasto": c.get("gasto")}
                           for c in dados.get("categorias") or []]}


def carregar(hoje: dt.date | None = None) -> Perfil | None:
    """Lê o Controle Financeiro real e resume. None sem dado real; nunca levanta."""
    from core.config import settings
    if settings.MOCK_MODE:
        return None
    meses = []
    try:
        for ano, mes in janela(hoje):
            m = _mes_real(ano, mes)
            if m is None:
                return None     # fallback de demonstração: nada é do usuário
            meses.append(m)
    except Exception:  # noqa: BLE001
        logger.warning("[perfil_financeiro] caixa indisponível.", exc_info=True)
        return None

    faturas, compras, dividas = [], [], []
    try:
        from core import controle
        faturas = controle.get_historico_cc_mensal()
        compras = [tx for tx in controle.get_transacoes_cartao_credito()
                   if str(tx.get("source") or "") == "csv"]
        dividas = controle.get_dividas_cc()
    except Exception:  # noqa: BLE001
        logger.warning("[perfil_financeiro] cartão indisponível.", exc_info=True)
    try:
        return resumir(meses, faturas=faturas, compras_cartao=compras,
                       dividas=dividas)
    except Exception:  # noqa: BLE001
        logger.warning("[perfil_financeiro] resumo falhou.", exc_info=True)
        return None
