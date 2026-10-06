"""
core/carteira_tabela.py
=======================
Dados da tabela de posições da Visão Geral (Investimentos → Análise).

A tabela mostra, por ativo e agrupada por tipo, o que `get_carteira()` já
calcula (custo, quantidade, preço médio, valor de mercado, peso) mais três
coisas que a carteira não carrega:

* **dividendos 12M** — vêm de `get_proventos()`, cruzados pelo ticker base
  (o fracionário ``PETR4F`` soma no ``PETR4``);
* **data do 1º aporte** — a evidência mais antiga de posse que existe no
  banco: compra em `investment_transactions` (o extrato de Negociação da B3
  começa em nov/2019; as notas da Nomad cobrem o exterior), crédito em
  `investment_movement_events` ou, na falta das duas, a foto de posição mais
  antiga. Ativo com posição de abertura declarada (`posicao_anterior`) é
  anterior ao extrato e aparece assim, sem data inventada;
* **corretora** — a "Posição Detalhada" da B3 não diz em que corretora o
  papel está (o importador grava a constante "B3 - Area do Investidor"). Quem
  diz é a Movimentação: cada provento, liquidação e transferência traz a
  instituição. Vale a data de crédito mais recente do ativo — num provento, as
  duas corretoras que custodiam o mesmo papel aparecem juntas. Ativo fora da
  B3 (XP consolidado, Tesouro Direto, Nomad) usa a instituição da foto.

Sem dado, a célula fica vazia e a nota de rodapé diz por quê. Nada aqui é
estimado.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime

from sqlalchemy import text

from core.config import settings
from core.movimentacao_b3 import ticker_da_cota
from core.user_context import user_cache_data

logger = logging.getLogger(__name__)

# Data em que o relatório de Negociação da B3 começa: o que é anterior a isso
# só existe como posição de abertura declarada.
INICIO_EXTRATO_B3 = "nov/2019"
ABERTURA = "abertura"

GRUPO_ACOES = "Ações"
GRUPO_FII = "FII"
GRUPO_AMERICANAS = "Americanas"
GRUPO_RENDA_FIXA = "Renda Fixa"
_ORDEM_GRUPOS = (GRUPO_ACOES, GRUPO_FII, GRUPO_AMERICANAS, GRUPO_RENDA_FIXA)

_CLASSES_RENDA_FIXA = frozenset({"Renda Fixa", "Tesouro Direto", "Fundo RF"})

# Instituição que não é corretora: o importador da Posição Detalhada da B3.
_INSTITUICAO_NEUTRA = re.compile(r"^\s*b3\b", re.IGNORECASE)


# ─────────────────────────────────────────────────────────────────────────────
# Funções puras
# ─────────────────────────────────────────────────────────────────────────────

def ticker_base(ticker: str | None) -> str:
    """Mesma regra de `core.investimentos._base_ticker`, sem importar o módulo
    pesado: fracionário (sufixo F) vira o papel inteiro. O recibo de
    subscrição de FII (``ABCD13``) vira a cota (``ABCD11``): é a mesma posse."""
    t = ticker_da_cota((ticker or "").upper().strip())
    if t.endswith("11F"):
        return t[:-1]
    if t.endswith("F") and len(t) > 4:
        return t[:-1]
    return t


def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c))


# Ordem importa: "NU INVEST" antes de "INVEST", "XP INVEST" antes de "XP".
_APELIDOS: tuple[tuple[str, str], ...] = (
    (r"\bNU ?INVEST|\bEASYNVEST", "Nu Invest"),
    (r"\bXP\b", "XP"),
    (r"\bRICO\b", "Rico"),
    (r"\bCLEAR\b", "Clear"),
    (r"\bBTG\b", "BTG Pactual"),
    (r"\bINTER\b", "Inter"),
    (r"\bITAU\b|\bIUNI\b", "Itaú"),
    (r"\bBRADESCO\b", "Bradesco"),
    (r"\bSANTANDER\b", "Santander"),
    (r"\bMODAL\b", "Modal"),
    (r"\bGENIAL\b", "Genial"),
    (r"\bORAMA\b", "Órama"),
    (r"\bAGORA\b", "Ágora"),
    (r"\bTORO\b", "Toro"),
    (r"\bWARREN\b", "Warren"),
    (r"\bAVENUE\b", "Avenue"),
    (r"\bNOMAD\b", "Nomad"),
    (r"\bC6\b", "C6"),
    (r"\bTESOURO DIRETO\b", "Tesouro Direto"),
    (r"\bBB\b|\bBANCO DO BRASIL\b", "BB"),
    (r"\bCAIXA\b", "Caixa"),
    (r"\bSAFRA\b", "Safra"),
    (r"\bMIRAE\b", "Mirae"),
)

_SUFIXOS = re.compile(
    r"\b(CORRETORA|DISTRIBUIDORA|DE TITULOS|E VALORES MOBILIARIOS|VALORES|"
    r"MOBILIARIOS|CAMBIO|TITULOS|S\.?/?A\.?|LTDA|DTVM|CCTVM|CTVM|CVM)\b.*$"
)


def nome_corretora(instituicao: str | None) -> str:
    """Nome curto da corretora: "XP INVESTIMENTOS CCTVM S/A" → "XP".

    Vazio para o que não é corretora (a constante da B3) ou não veio.
    """
    bruto = (instituicao or "").strip()
    if not bruto or _INSTITUICAO_NEUTRA.match(bruto):
        return ""
    chave = _sem_acento(bruto).upper()
    for padrao, nome in _APELIDOS:
        if re.search(padrao, chave):
            return nome
    curto = _SUFIXOS.sub("", chave).strip(" -,.") or chave
    return " ".join(p.capitalize() if len(p) > 3 else p for p in curto.split())


def _iso(d) -> str | None:
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    s = str(d).strip()[:10]
    return s or None


def _eh_credito(direcao: str | None) -> bool:
    d = _sem_acento((direcao or "").strip().lower())
    return d.startswith("cred") or d.startswith("entrada")


def corretoras_por_movimentacao(eventos: list[dict]) -> dict[str, list[str]]:
    """{ticker_base: [corretoras]} pela data de crédito mais recente.

    `eventos`: dicts com ``data``, ``ticker``, ``direcao``, ``instituicao``.
    Débito não entra: a saída de uma corretora numa transferência é
    justamente onde o papel deixou de estar.
    """
    ultimo: dict[str, str] = {}
    por_data: dict[tuple[str, str], set[str]] = defaultdict(set)
    for ev in eventos:
        if not _eh_credito(ev.get("direcao")):
            continue
        nome = nome_corretora(ev.get("instituicao"))
        dt = _iso(ev.get("data"))
        tk = ticker_base(ev.get("ticker"))
        if not (nome and dt and tk):
            continue
        por_data[(tk, dt)].add(nome)
        if dt > ultimo.get(tk, ""):
            ultimo[tk] = dt
    return {tk: sorted(por_data[(tk, dt)]) for tk, dt in ultimo.items()}


def corretoras_por_foto(fotos: list[dict]) -> dict[str, list[str]]:
    """{ticker_base: [corretoras]} da foto mais recente que tem corretora."""
    ultimo: dict[str, str] = {}
    por_data: dict[tuple[str, str], set[str]] = defaultdict(set)
    for f in fotos:
        nome = nome_corretora(f.get("instituicao"))
        dt = _iso(f.get("data"))
        tk = ticker_base(f.get("ticker"))
        if not (nome and dt and tk):
            continue
        por_data[(tk, dt)].add(nome)
        if dt > ultimo.get(tk, ""):
            ultimo[tk] = dt
    return {tk: sorted(por_data[(tk, dt)]) for tk, dt in ultimo.items()}


def primeiros_aportes(
    compras: list[dict],
    creditos: list[dict],
    fotos: list[dict],
    abertura: set[str] | list[str],
) -> dict[str, dict]:
    """{ticker_base: {"data": iso | None, "fonte": ...}}.

    Fonte, em ordem de autoridade: ``abertura`` (posse anterior ao extrato,
    sem data), ``extrato`` (compra ou crédito — a mais antiga das duas) e
    ``foto`` (só existe a foto de posição; a data é a da primeira foto, um
    teto para o aporte, não o aporte).
    """
    out: dict[str, dict] = {}
    extrato: dict[str, str] = {}
    for ev in list(compras) + list(creditos):
        tk, dt = ticker_base(ev.get("ticker")), _iso(ev.get("data"))
        if tk and dt and (tk not in extrato or dt < extrato[tk]):
            extrato[tk] = dt
    foto: dict[str, str] = {}
    for f in fotos:
        tk, dt = ticker_base(f.get("ticker")), _iso(f.get("data"))
        if tk and dt and (tk not in foto or dt < foto[tk]):
            foto[tk] = dt
    for tk, dt in foto.items():
        out[tk] = {"data": dt, "fonte": "foto"}
    for tk, dt in extrato.items():
        out[tk] = {"data": dt, "fonte": "extrato"}
    for tk in abertura:
        out[ticker_base(tk)] = {"data": None, "fonte": ABERTURA}
    return out


def grupo_da_posicao(pos: dict) -> str:
    """Ações, FII, Americanas, Renda Fixa — ou a própria classe (ETF, BDR...)."""
    pais = (pos.get("pais") or "BR").upper()
    moeda = (pos.get("moeda") or "BRL").upper()
    if pais != "BR" or moeda != "BRL":
        return GRUPO_AMERICANAS
    classe = pos.get("classe") or "Outros"
    if classe == "Ações BR":
        return GRUPO_ACOES
    if classe == "FII":
        return GRUPO_FII
    if classe in _CLASSES_RENDA_FIXA:
        return GRUPO_RENDA_FIXA
    return classe


def montar_tabela(
    posicoes: list[dict],
    renda_por_ticker: dict[str, float],
    extras: dict | None = None,
) -> dict:
    """Linhas agrupadas, subtotais e total — tudo o que o renderizador desenha.

    `% do patrimônio` é o peso no valor de mercado total (o mesmo
    `pct_carteira` do resto da tela); `% do setor` é o peso do ativo dentro
    do conjunto de posições com o mesmo setor — o painel "Concentração → Por
    Setor" usa a mesma base.
    """
    extras = extras or {}
    aportes = extras.get("primeiro_aporte") or {}
    corretoras = extras.get("corretoras") or {}

    renda: dict[str, float] = defaultdict(float)
    for tk, v in (renda_por_ticker or {}).items():
        renda[ticker_base(tk)] += float(v or 0)

    total_mkt = sum(float(p.get("valor_mercado") or 0) for p in posicoes)
    total_inv = sum(float(p.get("total_investido") or 0) for p in posicoes)
    base_peso = total_mkt if total_mkt > 0 else total_inv

    por_setor: dict[str, float] = defaultdict(float)
    for p in posicoes:
        por_setor[p.get("setor") or "—"] += float(p.get("valor_mercado") or 0)

    grupos: dict[str, list[dict]] = defaultdict(list)
    for p in posicoes:
        tk = ticker_base(p.get("ticker"))
        vm = float(p.get("valor_mercado") or 0)
        setor = p.get("setor") or "—"
        soma_setor = por_setor.get(setor, 0.0)
        aporte = aportes.get(tk) or {}
        linha = {
            "ticker":          p.get("ticker") or tk,
            "nome":            p.get("nome") or "",
            "classe":          p.get("classe") or "",
            "setor":           setor,
            "moeda":           (p.get("moeda") or "BRL").upper(),
            "cor":             p.get("cor") or "",
            "total_investido": float(p.get("total_investido") or 0),
            "quantidade":      float(p.get("quantidade") or 0),
            "dividendos_12m":  round(renda.get(tk, 0.0), 2),
            "preco_medio":     float(p.get("preco_medio") or 0),
            "preco_medio_moeda_original": p.get("preco_medio_moeda_original"),
            "valor_mercado":   vm,
            "pct_patrimonio":  vm / base_peso * 100 if base_peso > 0 else 0.0,
            "pct_setor":       vm / soma_setor * 100 if soma_setor > 0 else None,
            "primeiro_aporte": aporte.get("data"),
            "aporte_fonte":    aporte.get("fonte"),
            "corretoras":      list(corretoras.get(tk) or []),
            "rentab_pct":      p.get("rentab_pct"),
        }
        grupos[grupo_da_posicao(p)].append(linha)

    def _ordem(nome: str) -> tuple:
        if nome in _ORDEM_GRUPOS:
            return (0, _ORDEM_GRUPOS.index(nome), "")
        return (1, -sum(ln["valor_mercado"] for ln in grupos[nome]), nome)

    saida = []
    for nome in sorted(grupos, key=_ordem):
        linhas = sorted(grupos[nome], key=lambda ln: ln["valor_mercado"], reverse=True)
        vm_g = sum(ln["valor_mercado"] for ln in linhas)
        saida.append({
            "grupo":           nome,
            "linhas":          linhas,
            "n":               len(linhas),
            "total_investido": sum(ln["total_investido"] for ln in linhas),
            "dividendos_12m":  sum(ln["dividendos_12m"] for ln in linhas),
            "valor_mercado":   vm_g,
            "pct_patrimonio":  vm_g / base_peso * 100 if base_peso > 0 else 0.0,
        })

    return {
        "grupos": saida,
        "total": {
            "n":               len(posicoes),
            "total_investido": total_inv,
            "dividendos_12m":  sum(g["dividendos_12m"] for g in saida),
            "valor_mercado":   total_mkt,
            "pct_patrimonio":  100.0 if base_peso > 0 else 0.0,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Leitura do banco
# ─────────────────────────────────────────────────────────────────────────────

_SQL_COMPRAS = """
    SELECT upper(a.ticker) AS ticker, MIN(it.transaction_date)::date AS data
    FROM   investment_transactions it
    JOIN   assets a ON a.id = it.asset_id
    WHERE  it.user_id = :uid
      AND  lower(it.type) = 'buy'
      AND  it.quantity > 0
    GROUP  BY upper(a.ticker)
"""

_SQL_MOVIMENTOS = """
    SELECT event_date AS data, upper(ticker) AS ticker, direction AS direcao,
           movement AS movimento, institution AS instituicao
    FROM   investment_movement_events
    WHERE  user_id = :uid
"""

# A foto da B3 é excluída da corretora (constante "B3 - Area do Investidor")
# mas entra na data: é evidência de posse como qualquer outra foto.
_SQL_FOTOS = """
    SELECT upper(a.ticker) AS ticker, pps.report_date AS data,
           pps.institution AS instituicao
    FROM   portfolio_position_snapshots pps
    JOIN   assets a ON a.id = pps.asset_id
    WHERE  pps.user_id = :uid
"""


def _ler(conn, sql: str, uid: str, nome: str) -> list[dict] | None:
    """Cada leitura num SAVEPOINT: tabela ausente não derruba as outras."""
    try:
        with conn.begin_nested():
            return [dict(r._mapping) for r in conn.execute(text(sql), {"uid": uid})]
    except Exception as exc:  # noqa: BLE001
        logger.info("carteira_tabela: %s indisponível (%s)", nome, type(exc).__name__)
        return None


def _extras_vazios(motivo: str) -> dict:
    return {"primeiro_aporte": {}, "corretoras": {}, "fontes_ausentes": [motivo]}


@user_cache_data(ttl=300)
def get_extras_tabela() -> dict:
    """Primeiro aporte e corretora por ticker base, prontos para `montar_tabela`.

    ``fontes_ausentes`` lista o que não pôde ser lido, para a nota de rodapé.
    Em MOCK_MODE devolve vazio: a célula fica "—", nunca um valor fabricado.
    """
    if settings.MOCK_MODE:
        return _extras_vazios("modo demonstração: sem extratos")
    uid = settings.OWNER_USER_ID
    if not uid:
        return _extras_vazios("OWNER_USER_ID não configurado")

    from core.database import get_engine
    from core.posicao_anterior import listar as listar_abertura

    ausentes: list[str] = []
    try:
        with get_engine().connect() as conn:
            compras = _ler(conn, _SQL_COMPRAS, uid, "negociações")
            movimentos = _ler(conn, _SQL_MOVIMENTOS, uid, "movimentação B3")
            fotos = _ler(conn, _SQL_FOTOS, uid, "fotos de posição")
            abertura = listar_abertura(uid, conn=conn)
    except Exception as exc:  # noqa: BLE001
        logger.warning("carteira_tabela: banco indisponível (%s)", type(exc).__name__)
        return _extras_vazios("banco indisponível")

    if compras is None:
        ausentes.append("extrato de Negociação")
    if movimentos is None:
        ausentes.append("extrato de Movimentação da B3")
    if fotos is None:
        ausentes.append("fotos de posição")
    compras, movimentos, fotos = compras or [], movimentos or [], fotos or []

    creditos = [m for m in movimentos if _eh_credito(m.get("direcao"))]
    fotos_corretora = [f for f in fotos if not _INSTITUICAO_NEUTRA.match(f.get("instituicao") or "")]

    corretoras = corretoras_por_foto(fotos_corretora)
    corretoras.update(corretoras_por_movimentacao(movimentos))

    return {
        "primeiro_aporte": primeiros_aportes(compras, creditos, fotos, set(abertura)),
        "corretoras":      corretoras,
        "fontes_ausentes": ausentes,
    }
