"""
core/inteligencia_ativos/fontes_informacoes.py
Leitores de notícias, relatórios e próximos eventos. Única parte com I/O da
camada de informações recentes; ``informacoes`` é puro.

De onde vem cada coisa:

- Notícias e documentos: ``data/public/informacoes_recentes.json.gz``, gerado
  do armazém local (acervo de notícias e ``docs_corporativos`` da CVM/IPE,
  ``market.fii_documents`` do FNET) por
  ``scripts/publish_informacoes_recentes.py``. O app publicado não alcança o
  armazém; o arquivo é a ponte.
- Proventos futuros: ``market.dividends`` no Supabase, só leitura, com a
  mesma regra de safra canônica do resto do projeto (``dividend_types``).
- Vencimento de título do Tesouro: o título da posição (``tesouro_posicao``).
- Divulgação de resultado na B3: prazo regulatório calculado, rotulado como
  prazo e não como data anunciada.

Sem fonte, o tipo de evento aparece como "sem fonte de data"; nada é
estimado.
"""
from __future__ import annotations

import gzip
import json
import logging
from datetime import date
from pathlib import Path

from core.inteligencia_ativos import informacoes as inf
from core.inteligencia_ativos.fundamentos import (
    ACAO,
    ETF,
    FII,
    RENDA_FIXA,
    tipo_do_ativo,
)

logger = logging.getLogger(__name__)

ARQUIVO = Path(__file__).resolve().parents[2] / "data" / "public" / \
    "informacoes_recentes.json.gz"

FONTE_PROVENTOS = "market.dividends (Supabase; origem brapi.dev)"
FONTE_PRAZO_CVM = "Resolução CVM 80/2022 (prazo de ITR e DFP)"
FONTE_TESOURO = "Título da posição no Tesouro Direto"

# BDR: recibo de ação estrangeira, sem ITR/DFP próprio na CVM
_SUFIXOS_BDR = frozenset({"31", "32", "33", "34", "35", "39"})

SEM_ARQUIVO = ("Dado não disponível. O arquivo de informações recentes não "
               "foi encontrado.")


def _ler_arquivo(caminho: str) -> dict:
    try:
        with gzip.open(caminho, "rb") as fh:
            return json.loads(fh.read().decode("utf-8"))
    except FileNotFoundError:
        logger.info("[informacoes] arquivo ausente: %s", caminho)
    except Exception as exc:  # arquivo corrompido não derruba a seção
        logger.warning("[informacoes] arquivo ilegível: %s",
                       type(exc).__name__)
    return {}


def _cache(fn):
    try:
        import streamlit as st
        return st.cache_data(ttl=900, show_spinner=False)(fn)
    except Exception:  # pragma: no cover - contexto sem Streamlit
        return fn


_arquivo_cache = _cache(_ler_arquivo)


def arquivo() -> dict:
    return _arquivo_cache(str(ARQUIVO))


def _do_ticker(por_ticker: dict, ticker: str):
    """Entrada exata; na B3, a de uma classe irmã (PETR3 para PETR4)."""
    tk = ticker.upper()
    if tk in por_ticker:
        return por_ticker[tk]
    raiz = inf.raiz_b3(tk)
    if raiz:
        for k in sorted(por_ticker):
            if inf.raiz_b3(k) == raiz:
                return por_ticker[k]
    return None


# ---------------------------------------------------------------------------
# Notícias e relatórios (arquivo)
# ---------------------------------------------------------------------------
def noticias_de(art: dict, ticker: str, tipo: str | None) -> inf.Noticias:
    """Notícias do ativo a partir do artefato. Puro."""
    if tipo == RENDA_FIXA:
        return inf.Noticias(motivo="Dado não disponível. Título de renda fixa "
                                   "não tem cobertura de notícia por ativo.")
    if not art:
        return inf.Noticias(motivo=SEM_ARQUIVO)
    bloco = art.get("noticias") or {}
    meta = {k: bloco.get(k) for k in ("janela_dias", "base_ate", "fonte")}
    meta["retrieved_at"] = art.get("gerado_em")
    entrada = _do_ticker(bloco.get("por_ticker") or {}, ticker)
    if not entrada:
        return inf.Noticias(**meta, motivo=(
            f"Dado não disponível. Nenhuma notícia vinculada a {ticker} nos "
            f"últimos {meta['janela_dias'] or '?'} dias do acervo."))
    n = inf.Noticias.de_dict({**meta, **entrada})
    if n.itens:
        return n
    total = sum(n.descartadas.values())
    return inf.Noticias(**{**meta, "descartadas": n.descartadas}, motivo=(
        f"Dado não disponível. {total} notícia(s) citavam {ticker}, nenhuma "
        "passou no filtro de relevância."))


def relatorios_de(art: dict, ticker: str, tipo: str | None) -> inf.Relatorios:
    """Documentos recentes do ativo a partir do artefato. Puro."""
    if tipo in (RENDA_FIXA, ETF):
        return inf.Relatorios(motivo="Dado não disponível. Não há base de "
                                     "documentos para esta classe.")
    if not art:
        return inf.Relatorios(motivo=SEM_ARQUIVO)
    bloco = art.get("relatorios") or {}
    mercado = "fii" if tipo == FII else "b3"
    fonte = (bloco.get("fonte") or {}).get(mercado)
    base = (bloco.get("base_ate") or {}).get(mercado)
    docs = _do_ticker(bloco.get("por_ticker") or {}, ticker)
    if tipo == ACAO and inf.raiz_b3(ticker) is None:
        return inf.Relatorios(motivo="Dado não disponível. Não há base de "
                                     "filings da SEC no projeto para ações "
                                     "americanas.")
    if not docs:
        return inf.Relatorios(fonte=fonte, base_ate=base, motivo=(
            f"Dado não disponível. Nenhum documento de {ticker} nos últimos "
            f"{bloco.get('janela_dias') or '?'} dias da base"
            + (f" (atualizada até {inf._data_br(base)})" if base else "") + "."))
    return inf.Relatorios.de_dict({"documentos": docs, "base_ate": base,
                                   "fonte": fonte,
                                   "retrieved_at": art.get("gerado_em")})


# ---------------------------------------------------------------------------
# Próximos eventos
# ---------------------------------------------------------------------------
def _fmt_valor(x) -> str:
    try:
        return f"R$ {float(x):.4f}".rstrip("0").rstrip(",.").replace(".", ",")
    except (TypeError, ValueError):
        return "valor não informado"


def eventos_de_proventos(linhas: list[dict], hoje: date) -> list[inf.Evento]:
    """Uma linha de ``market.dividends`` → um evento na próxima data que
    importa (data-com se ainda vem, senão o pagamento). Puro."""
    saida = []
    for r in linhas:
        ex, pg = r.get("ex_date"), r.get("payment_date")
        ex_iso = ex.isoformat() if ex else None
        pg_iso = pg.isoformat() if pg else None
        if ex_iso and ex_iso >= hoje.isoformat():
            data, quando = ex_iso, "data-com"
        elif pg_iso and pg_iso >= hoje.isoformat():
            data, quando = pg_iso, "pagamento"
        else:
            continue
        tipo = str(r.get("type") or "provento").upper()
        desc = (f"{tipo} de {_fmt_valor(r.get('amount'))} por cota/ação — "
                f"{quando}; data-com {inf._data_br(ex_iso)}, pagamento "
                f"{inf._data_br(pg_iso)}")
        atualizado = r.get("updated_at") or r.get("created_at")
        saida.append(inf.Evento(
            tipo="dividend", data=data, descricao=desc, natureza="anunciado",
            source=str(r.get("source") or FONTE_PROVENTOS),
            source_url=None,
            retrieved_at=atualizado.isoformat() if atualizado else None,
            reference_date=ex_iso or pg_iso))
    return saida


def evento_de_resultado(hoje: date) -> inf.Evento:
    limite, periodo = inf.prazo_de_resultado(hoje)
    return inf.Evento(
        tipo="earnings", data=limite.isoformat(),
        descricao=f"{periodo}: data-limite para a divulgação",
        natureza="prazo regulatório, não data anunciada",
        source=FONTE_PRAZO_CVM, reference_date=limite.isoformat())


def evento_de_vencimento(titulo) -> inf.Evento | None:
    venc = getattr(titulo, "vencimento", None)
    if venc is None:
        return None
    return inf.Evento(
        tipo="debt_maturity", data=venc.isoformat(),
        descricao=f"Vencimento de {getattr(titulo, 'security_key', 'título')}",
        natureza="contratual", source=FONTE_TESOURO,
        reference_date=venc.isoformat())


def montar_eventos(tipo: str | None, ticker: str, moeda: str, hoje: date, *,
                   proventos: list[dict] | None, titulo=None,
                   eh_tesouro: bool = False,
                   acao_b3: bool = False) -> inf.Eventos:
    """Junta as fontes que existem para a classe. Puro.

    ``acao_b3``: companhia aberta brasileira (não BDR), a única com prazo de
    ITR/DFP na CVM. ``proventos=None`` significa que a fonte não foi consultada (ou falhou),
    diferente de ``[]``: consultada e vazia."""
    itens: list[inf.Evento] = []
    consultados: list[str] = []
    if tipo in (ACAO, FII) and proventos is not None:
        consultados.append("dividend")
        itens += eventos_de_proventos(proventos, hoje)
    if tipo == ACAO and (moeda or "BRL").upper() == "BRL" \
            and inf.raiz_b3(ticker):
        consultados.append("earnings")
        itens.append(evento_de_resultado(hoje))
    if tipo == RENDA_FIXA and eh_tesouro:
        consultados.append("debt_maturity")
        ev = evento_de_vencimento(titulo)
        if ev is not None:
            itens.append(ev)
    ordenados = inf.ordenar_eventos(itens, hoje)
    motivo = None
    if not ordenados:
        motivo = ("Dado não disponível. Nenhum evento futuro com data nas "
                  "fontes do projeto para este ativo.")
    return inf.Eventos(itens=ordenados, tipos_consultados=tuple(consultados),
                       motivo=motivo)


def _carregar_proventos(ticker: str) -> list[dict]:
    from sqlalchemy import text

    from core.database import get_engine
    from core.dividend_types import sql_safra_canonica
    sql = text(f"""
        SELECT d.ticker, d.ex_date, d.payment_date, d.amount, d.type,
               d.source, d.created_at, d.updated_at
          FROM market.dividends d
         WHERE d.ticker = :t
           AND GREATEST(d.ex_date, d.payment_date) >= CURRENT_DATE
           AND {sql_safra_canonica("d")}
         ORDER BY COALESCE(d.ex_date, d.payment_date)
         LIMIT 40""")
    with get_engine().connect() as c:
        linhas = [dict(r._mapping) for r in c.execute(sql, {"t": ticker})]
    # eco de classe / mesma safra: o mesmo (tipo, datas) conta uma vez, pelo
    # menor valor (regra A-129 de dividend_types)
    unicos: dict[tuple, dict] = {}
    for r in linhas:
        k = (r.get("type"), r.get("ex_date"), r.get("payment_date"))
        if k not in unicos or (r.get("amount") or 0) < (unicos[k].get("amount")
                                                         or 0):
            unicos[k] = r
    return list(unicos.values())


_proventos_cache = _cache(_carregar_proventos)


def _proventos(ticker: str) -> list[dict] | None:
    try:
        return _proventos_cache(ticker.upper())
    except Exception as exc:  # banco fora do ar: fonte "não consultada"
        logger.warning("[informacoes] proventos indisponíveis: %s",
                       type(exc).__name__)
        return None


# ---------------------------------------------------------------------------
# Entrada única
# ---------------------------------------------------------------------------
def ler(ticker: str, nome: str, classe: str, moeda: str, *,
        hoje: date | None = None
        ) -> tuple[inf.Noticias, inf.Relatorios, inf.Eventos]:
    from core.inteligencia_ativos import calculos
    hoje = hoje or date.today()
    tipo = tipo_do_ativo(classe, moeda)
    tk = str(ticker or "").upper()
    art = arquivo()
    titulo, eh_tesouro = None, False
    if tipo == RENDA_FIXA:
        eh_tesouro = calculos.emissor({"ticker": tk, "nome": nome,
                                       "classe": classe}) \
            == calculos.TESOURO_NACIONAL
        if eh_tesouro:
            from core.inteligencia_ativos import fontes_fundamentos as ff
            try:
                titulo = ff.titulo_da_posicao(tk, ff._titulos_tesouro())
            except Exception as exc:
                logger.warning("[informacoes] títulos do Tesouro: %s",
                               type(exc).__name__)
    proventos = _proventos(tk) if tipo in (ACAO, FII) else None
    acao_b3 = (calculos.CLASSE_POLITICA.get((classe or "").strip())
               == "acoes_br" and inf.raiz_b3(tk) is not None
               and tk[-2:] not in _SUFIXOS_BDR)
    eventos = montar_eventos(tipo, tk, moeda, hoje, proventos=proventos,
                             titulo=titulo, eh_tesouro=eh_tesouro,
                             acao_b3=acao_b3)
    return (noticias_de(art, tk, tipo), relatorios_de(art, tk, tipo),
            eventos)
