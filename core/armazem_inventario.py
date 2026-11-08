"""Inventário de um ativo no armazém local: o que ele guarda, fonte por fonte.

Por que existe. A premissa máxima do CLAUDE.md manda toda LLM receber o
máximo de dado que existe no Supabase **e no armazém local**. Até aqui, quando
um ativo não estava na vitrine do Supabase, o app e as LLMs concluíam "não há
dado sobre este ativo" sem olhar o armazém -- que guarda décadas de pregão,
demonstrativos, proventos, documentos da CVM e notícias que a vitrine não
publica. Regra do usuário de 08/11/2026: lacuna sobre um ativo só é afirmada
depois de varrer o armazém local.

Este módulo não traz o dado: diz **se e quanto** dado existe, por fonte, com o
período coberto. É barato (uma contagem por fonte) e serve a três leitores:

* o prompt da LLM, para separar "o armazém não tem" de "o dado existe e não
  veio a este contexto";
* a tela, para a mensagem "ticker não encontrado" dizer o que o armazém sabe;
* o log de lacunas, para a triagem saber de antemão se o dado existe.

Duas metades, a mesma regra dos ``*_detalhe_armazem``:

* :func:`ler_inventario` é SQL e roda onde o armazém está -- direto no
  desenvolvimento, ou no servidor do túnel (``scripts/servir_armazem_leitura``).
  Devolve contagens e datas cruas.
* :func:`resumo_para_prompt` e :func:`resumo_curto` formatam e rodam sempre no
  app.

**Cada fonte falha sozinha.** Tabela que não existe num armazém, banco não
configurado ou consulta que erra viram ``não consultada (motivo)`` naquela
fonte, e as outras seguem. Fonte não consultada NUNCA é lida como ausência:
o resumo diz isso em letras, porque a LLM leria silêncio como "não tem".
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Mapping

logger = logging.getLogger(__name__)

CLASSES = ("b3", "fii", "us")
TICKERS_MAX = 10

#: Bancos do armazém. ``mercado`` guarda ``market`` e ``market_us``; o acervo
#: de notícias e a safra da Memória de Mercado moram em bancos próprios
#: (``core.noticias.destino.url_acervo`` e ``core.memoria_mercado.destino``).
BANCOS = ("mercado", "noticias", "memoria")


@dataclass(frozen=True)
class Fonte:
    chave: str
    rotulo: str
    banco: str
    sql: str


def _contagem(tabela: str, simbolo: str, data: str | None, extra: str = "") -> str:
    """``SELECT simbolo, n, desde, ate`` de uma tabela, agrupado por símbolo."""
    desde = f"min({data})" if data else "NULL"
    ate = f"max({data})" if data else "NULL"
    return (f"SELECT upper({simbolo}) AS simbolo, count(*) AS n, {desde} AS desde, "
            f"{ate} AS ate FROM {tabela} WHERE upper({simbolo}) = ANY(:t){extra} "
            "GROUP BY 1")


_SQL_NOTICIAS = """
SELECT upper(t.ticker) AS simbolo, count(*) AS n,
       min(i.publicado_em) AS desde, max(i.publicado_em) AS ate
FROM noticias_itens i
CROSS JOIN LATERAL jsonb_array_elements_text(
    COALESCE(i.entidades -> 'tickers', '[]'::jsonb)) AS t(ticker)
WHERE upper(t.ticker) = ANY(:t)
GROUP BY 1
"""

_SQL_CADASTRO_US = """
SELECT upper(a.symbol) AS simbolo, count(*) AS n, NULL AS desde, NULL AS ate
FROM market_us.assets a
WHERE upper(a.symbol) = ANY(:t)
GROUP BY 1
"""

_NOTICIAS = Fonte("noticias", "notícias do acervo local", "noticias", _SQL_NOTICIAS)

#: Fontes por classe. A ordem é a do resumo. Cada SQL devolve
#: ``simbolo, n, desde, ate`` e recebe ``:t`` (lista de tickers em maiúsculas).
FONTES: dict[str, tuple[Fonte, ...]] = {
    "b3": (
        Fonte("pregao", "pregão diário da B3 (COTAHIST)", "mercado",
              _contagem("market.b3_security_history", "ticker", "trade_date",
                        " AND bdi = '02'")),
        Fonte("preco_historico", "preço histórico do coletor (brapi)", "mercado",
              _contagem("market.historical_prices", "ticker", "date")),
        Fonte("demonstrativos", "demonstrativos anuais e trimestrais", "mercado",
              _contagem("market.income_statements", "ticker", "year")),
        Fonte("metricas", "métricas calculadas", "mercado",
              _contagem("market.calculated_metrics", "ticker", None)),
        Fonte("proventos", "proventos", "mercado",
              _contagem("market.dividends", "ticker",
                        "COALESCE(ex_date, payment_date, event_date)")),
        Fonte("documentos_cvm", "documentos CVM/IPE (texto no RAG)", "mercado",
              _contagem("public.docs_corporativos", "ticker", "data")),
        Fonte("eventos_resultado", "reação a resultados (Memória de Mercado)", "memoria",
              _contagem("memoria_mercado.eventos_medidos", "simbolo", "data_evento")),
        _NOTICIAS,
    ),
    "fii": (
        Fonte("informe_cvm", "informe mensal da CVM", "mercado",
              _contagem("market.fii_metric_observations", "ticker", "reference_date",
                        " AND source = 'cvm_informe_mensal'")),
        Fonte("pregao", "pregão diário da B3 (COTAHIST)", "mercado",
              _contagem("market.fii_b3_security_history", "ticker", "trade_date")),
        Fonte("preco_historico", "preço histórico do coletor", "mercado",
              _contagem("market.historical_prices", "ticker", "date")),
        Fonte("proventos", "proventos", "mercado",
              _contagem("market.dividends", "ticker",
                        "COALESCE(ex_date, payment_date, event_date)")),
        Fonte("documentos_fnet", "documentos FNET", "mercado",
              _contagem("market.fii_documents", "ticker", "reference_date")),
        Fonte("composicao", "composição da carteira", "mercado",
              _contagem("market.fii_exposures", "ticker", "reference_date")),
        Fonte("imoveis", "imóveis cadastrados", "mercado",
              _contagem("market.fii_imoveis", "ticker", None)),
        Fonte("score_pit", "score PIT mês a mês", "mercado",
              _contagem("market.fii_pit_score_snapshots", "ticker", "reference_date")),
        _NOTICIAS,
    ),
    "us": (
        Fonte("cadastro", "cadastro do universo americano", "mercado", _SQL_CADASTRO_US),
        Fonte("preco_diario", "preço diário", "mercado",
              _contagem("market_us.prices_daily", "symbol", "date")),
        Fonte("preco_mensal", "preço mensal", "mercado",
              _contagem("market_us.prices_monthly", "symbol", "month_end")),
        Fonte("demonstrativos", "demonstrativos (SEC)", "mercado",
              _contagem("market_us.income_statements", "symbol", "reference_date")),
        Fonte("proventos", "proventos", "mercado",
              _contagem("market_us.dividends", "symbol", "ex_date")),
        Fonte("documentos_sec", "documentos entregues à SEC", "mercado",
              _contagem("market_us.sec_filings", "symbol", "filing_date")),
        Fonte("saidas", "registro de saída da bolsa (delisting)", "mercado",
              _contagem("market_us.delistings", "symbol", None)),
        _NOTICIAS,
    ),
}

_URL_OU_CONEXAO = re.compile(r"\S+://\S+|\bLINE \d+:.*$", re.IGNORECASE)


def normalizar_tickers(tickers) -> list[str]:
    return list(dict.fromkeys(
        str(t).strip().upper().removesuffix(".SA") for t in tickers if str(t).strip()))


def _motivo(exc: BaseException) -> str:
    """Primeira linha do erro, sem URL de conexão nem o SQL apontado pelo banco.

    O texto vai para o prompt e para a tela; host e usuário do banco não.
    """
    texto = str(exc).splitlines()[0].strip() if str(exc) else ""
    texto = _URL_OU_CONEXAO.sub("", texto).strip(" :;,")
    return f"{type(exc).__name__}: {texto[:120]}" if texto else type(exc).__name__


def ler_inventario(engines: Mapping[str, object | None], classe: str, tickers) -> dict:
    """Contagem e período por fonte, para cada ticker, lidos de ``engines``.

    ``engines`` é ``{banco: engine | None}`` com as chaves de :data:`BANCOS`;
    banco ``None`` é "não configurado aqui" e vira estado ``sem_banco`` em todas
    as fontes dele. Nunca levanta por causa de UMA fonte: a falha fica
    registrada nela e as demais são lidas.

    Devolve::

        {"classe": "b3", "tickers": [...],
         "fontes": {chave: {"rotulo": ..., "estado": "lida" | "sem_banco" | "falha",
                            "motivo": ...}},
         "ativos": {ticker: {chave: {"n": int, "desde": ..., "ate": ...}}}}

    Ticker sem linha numa fonte lida simplesmente não aparece nela: quem
    formata lê ``estado == "lida"`` e ausência como zero.
    """
    from sqlalchemy import text

    if classe not in FONTES:
        raise ValueError(f"classe desconhecida: {classe!r}")
    alvo = normalizar_tickers(tickers)
    saida: dict = {"classe": classe, "tickers": alvo, "fontes": {},
                   "ativos": {tk: {} for tk in alvo}}
    if not alvo:
        return saida
    for fonte in FONTES[classe]:
        estado: dict = {"rotulo": fonte.rotulo}
        engine = engines.get(fonte.banco)
        if engine is None:
            estado["estado"] = "sem_banco"
            saida["fontes"][fonte.chave] = estado
            continue
        try:
            # Uma conexão por fonte: consulta que falha deixa a transação
            # abortada, e a fonte seguinte precisa de uma limpa.
            with engine.connect() as conn:
                linhas = conn.execute(text(fonte.sql), {"t": alvo}).mappings().all()
        except Exception as exc:  # noqa: BLE001 - a fonte fica marcada, as outras seguem
            logger.info("inventário (%s/%s) não lido: %s", classe, fonte.chave, exc)
            estado["estado"] = "falha"
            estado["motivo"] = _motivo(exc)
            saida["fontes"][fonte.chave] = estado
            continue
        estado["estado"] = "lida"
        saida["fontes"][fonte.chave] = estado
        for linha in linhas:
            simbolo = str(linha.get("simbolo") or "").upper()
            if simbolo in saida["ativos"]:
                saida["ativos"][simbolo][fonte.chave] = {
                    "n": int(linha.get("n") or 0),
                    "desde": linha.get("desde"), "ate": linha.get("ate")}
    return saida


# ── Formatação (roda no app; aceita datas em texto vindas do túnel) ─────────

def _data(valor) -> str:
    if valor is None or valor == "":
        return ""
    if isinstance(valor, datetime):
        return valor.strftime("%d/%m/%Y")
    if isinstance(valor, date):
        return valor.strftime("%d/%m/%Y")
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        return str(int(valor))
    texto = str(valor)
    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00")).strftime("%d/%m/%Y")
    except ValueError:
        return texto[:10]


def _periodo(item: Mapping) -> str:
    desde, ate = _data(item.get("desde")), _data(item.get("ate"))
    if desde and ate:
        return f", de {desde} a {ate}" if desde != ate else f", em {desde}"
    if ate:
        return f", até {ate}"
    return ""


def _n(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _fontes_lidas(inventario: Mapping) -> tuple[list[str], list[tuple[str, dict]]]:
    """``(chaves lidas, [(chave, estado) não lidas])`` na ordem da classe."""
    fontes = inventario.get("fontes") or {}
    ordem = [f.chave for f in FONTES.get(str(inventario.get("classe")), ())]
    ordem += [c for c in fontes if c not in ordem]
    lidas = [c for c in ordem if (fontes.get(c) or {}).get("estado") == "lida"]
    nao_lidas = [(c, fontes[c]) for c in ordem
                 if c in fontes and fontes[c].get("estado") != "lida"]
    return lidas, nao_lidas


def _motivo_nao_lida(estado: Mapping) -> str:
    if estado.get("estado") == "sem_banco":
        return "banco desta fonte não configurado onde o armazém foi lido"
    return str(estado.get("motivo") or "falha na consulta")


def conhece(inventario: Mapping, ticker: str) -> bool | None:
    """``True`` se alguma fonte tem linha; ``False`` se todas as fontes lidas
    deram zero; ``None`` se nenhuma fonte pôde ser lida."""
    lidas, _ = _fontes_lidas(inventario)
    dados = (inventario.get("ativos") or {}).get(str(ticker).upper()) or {}
    if any(int((dados.get(c) or {}).get("n") or 0) > 0 for c in lidas):
        return True
    return False if lidas else None


def _veredito(inventario: Mapping, ticker: str) -> str:
    lidas, nao_lidas = _fontes_lidas(inventario)
    dados = (inventario.get("ativos") or {}).get(str(ticker).upper()) or {}
    com_linha = [c for c in lidas if int((dados.get(c) or {}).get("n") or 0) > 0]
    if com_linha:
        return (f"o armazém local CONHECE este ativo ({len(com_linha)} de {len(lidas)} "
                "fontes consultadas com linha).")
    if not lidas:
        return ("nenhuma fonte pôde ser consultada; a existência do dado no armazém "
                "está em aberto -- não trate como ausência.")
    if nao_lidas:
        return (f"nenhuma linha nas {len(lidas)} fontes consultadas; {len(nao_lidas)} "
                "fonte(s) não consultada(s), então a ausência NÃO está confirmada.")
    return (f"o armazém local NÃO tem nenhuma linha sobre este ativo em nenhuma das "
            f"{len(lidas)} fontes consultadas.")


CABECALHO = (
    "VARREDURA DO ARMAZÉM LOCAL ({origem}) — o que o armazém guarda sobre cada ativo, "
    "fonte por fonte, com o período coberto. Serve para separar \"o dado não existe\" "
    "de \"o dado existe e não veio a este contexto\". Regra de leitura: fonte com "
    "\"nenhuma linha\" = o armazém não tem; fonte \"não consultada\" = ninguém olhou, "
    "não trate como ausência; dado listado aqui que não apareça nas outras seções "
    "EXISTE no armazém -- diga isso e declare a lacuna em vez de afirmar que não há dado."
)


def resumo_para_prompt(inventario: Mapping | None, *, origem: str) -> str:
    """Bloco para o prompt, uma seção por ativo. Vazio sem tickers."""
    if not inventario or not inventario.get("tickers"):
        return ""
    fontes = inventario.get("fontes") or {}
    lidas, nao_lidas = _fontes_lidas(inventario)
    linhas = [CABECALHO.format(origem=origem)]
    for tk in inventario["tickers"]:
        dados = (inventario.get("ativos") or {}).get(tk) or {}
        linhas.append(f"  {tk}:")
        for chave in lidas:
            rotulo = fontes[chave].get("rotulo") or chave
            item = dados.get(chave) or {}
            n = int(item.get("n") or 0)
            if n > 0:
                linhas.append(f"    {rotulo}: {_n(n)} linhas{_periodo(item)}")
            else:
                linhas.append(f"    {rotulo}: nenhuma linha")
        for chave, estado in nao_lidas:
            rotulo = estado.get("rotulo") or chave
            linhas.append(f"    {rotulo}: não consultada ({_motivo_nao_lida(estado)})")
        linhas.append(f"    → {_veredito(inventario, tk)}")
    return "\n".join(linhas)


def resumo_curto(inventario: Mapping | None, ticker: str, *, limite: int = 220) -> str:
    """Uma linha para a tela ou para a mensagem de uma lacuna.

    Exemplo: ``armazém local: pregão diário da B3 (COTAHIST) 2.480 linhas até
    07/11/2026; proventos 120; sem documentos CVM/IPE; notícias não consultada``.
    """
    if not inventario:
        return ""
    tk = str(ticker).upper()
    fontes = inventario.get("fontes") or {}
    lidas, nao_lidas = _fontes_lidas(inventario)
    dados = (inventario.get("ativos") or {}).get(tk) or {}
    tem, sem = [], []
    for chave in lidas:
        rotulo = str(fontes[chave].get("rotulo") or chave)
        item = dados.get(chave) or {}
        n = int(item.get("n") or 0)
        if n > 0:
            ate = _data(item.get("ate"))
            tem.append(f"{rotulo} {_n(n)}" + (f" até {ate}" if ate else ""))
        else:
            sem.append(rotulo)
    partes = []
    if tem:
        partes.append("; ".join(tem))
    if sem:
        partes.append("sem " + ", ".join(sem))
    if nao_lidas:
        partes.append("não consultada: " + ", ".join(
            str(e.get("rotulo") or c) for c, e in nao_lidas))
    if not partes:
        return "armazém local: nenhuma fonte consultada"
    texto = "armazém local: " + " | ".join(partes)
    return texto if len(texto) <= limite else texto[:limite - 1].rstrip() + "…"
