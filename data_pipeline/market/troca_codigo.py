"""
data_pipeline/market/troca_codigo.py — troca de código B3 no banco.

A regra (o que é troca, qual código é o atual, de onde sai a classificação
setorial) mora em ``core.b3_troca_codigo``. Aqui fica o que toca o banco:

* ``carregar_trocas`` lê as trocas já registradas em ``market.ticker_alias``;
* ``e_troca`` prova uma troca pelo cadastro (mesma companhia, mesma classe);
* ``unificar`` dá ao histórico um dono só: passa as linhas de fato do código
  antigo para o atual, desativa o antigo, registra o alias antigo → atual,
  apaga o alias legado invertido e alinha a classificação em
  ``public.setores``.

``unificar(aplicar=False)`` só conta — é o dry-run da migração. Com
``aplicar=True`` grava na transação do chamador. Idempotente: na segunda vez
não há linha a mover nem ativo a desativar.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from core.b3_troca_codigo import (MOTIVO_TROCA, PARES_CONHECIDOS, escolher_classificacao,
                                  fechar_cadeias, mesma_classe, normalizar)
from data_pipeline.market.repository import _NATURAL_KEY

logger = logging.getLogger(__name__)

# Tabelas de fato e o que identifica a linha além do ticker (as restrições
# únicas de repository). As safras de indicadores não têm chave única: mesma
# métrica, mesmo período, mesmo valor e mesma disponibilidade é a mesma safra.
FATOS: tuple[tuple[str, tuple[str, ...]], ...] = tuple(
    (t, tuple(c for c in _NATURAL_KEY[t] if c != "ticker"))
    for t in ("historical_prices", "historical_price_observations",
              "income_statements", "balance_sheets", "cash_flow_statements",
              "dividends", "calculated_metrics")
) + (("calculated_metric_vintages",
      ("period", "year", "quarter", "metric_name", "metric_value", "available_at")),)

_CLASSIFICACAO = ("SETOR", "SUBSETOR", "SEGMENTO")
_TK_SETORES = "UPPER(REPLACE(ticker, '.SA', ''))"


def carregar_trocas(conn) -> dict[str, str]:
    """{antigo: atual} das trocas gravadas com ``MOTIVO_TROCA``.

    Só o que está no banco — ``PARES_CONHECIDOS`` fica de fora: pular um
    código antigo que ainda não foi unificado deixaria o histórico dele
    parado. Sem a tabela, ``{}``.
    """
    try:
        if not conn.execute(text(
                "SELECT to_regclass('market.ticker_alias') IS NOT NULL")).scalar():
            return {}
        rows = conn.execute(text(
            "SELECT brapi_symbol, b3_ticker FROM market.ticker_alias WHERE motivo = :m"),
            {"m": MOTIVO_TROCA}).fetchall()
    except Exception as exc:
        logger.warning("trocas de código: market.ticker_alias ilegível: %s", exc)
        return {}
    mapa = {}
    for s, b in rows:
        s, b = normalizar(s), normalizar(b)
        if s and b and s != b and mesma_classe(s, b):
            mapa[s] = b
    return fechar_cadeias(mapa)


def mesma_companhia(conn, cvm_map: dict | None, a: str, b: str) -> bool:
    """Código CVM quando os dois são conhecidos; senão o company_id dos ativos."""
    ca, cb = (cvm_map or {}).get(a), (cvm_map or {}).get(b)
    if ca is not None and cb is not None:
        return int(ca) == int(cb)
    rows = conn.execute(text(
        "SELECT ticker, company_id FROM market.assets "
        "WHERE ticker IN (:a, :b) AND company_id IS NOT NULL"), {"a": a, "b": b}).fetchall()
    cid = {normalizar(t): c for t, c in rows}
    return a in cid and b in cid and cid[a] == cid[b]


def e_troca(conn, cvm_map: dict | None, antigo: str, novo: str,
            trocas: dict[str, str] | None = None) -> bool:
    """``novo`` substitui ``antigo``? Mesma classe, mesma companhia, e
    ``novo`` não é ele mesmo um código já substituído (a brapi devolver o
    código velho para o novo não pode inverter a troca)."""
    antigo, novo = normalizar(antigo), normalizar(novo)
    if antigo == novo or not mesma_classe(antigo, novo):
        return False
    if novo in PARES_CONHECIDOS or novo in (trocas or {}):
        return False
    return mesma_companhia(conn, cvm_map, antigo, novo)


def _colunas(conn) -> dict[str, set[str]]:
    nomes = [t for t, _ in FATOS] + ["assets", "ticker_alias", "setores"]
    lista = ", ".join(f"'{n}'" for n in nomes)
    rows = conn.execute(text(f"""
        SELECT table_schema || '.' || table_name, column_name
        FROM information_schema.columns
        WHERE table_schema IN ('market', 'public') AND table_name IN ({lista})
    """)).fetchall()
    out: dict[str, set[str]] = {}
    for tabela, coluna in rows:
        out.setdefault(tabela, set()).add(coluna)
    return out


def _fatos(conn, cols: dict[str, set[str]], p: dict, aplicar: bool) -> dict:
    out = {}
    for tabela, chave in FATOS:
        tem = cols.get(f"market.{tabela}")
        if not tem:
            out[tabela] = "tabela ausente"
            continue
        if not {"ticker", *chave} <= tem:
            out[tabela] = "sem as colunas da chave"
            continue
        casa = " AND ".join(f"n.{c} IS NOT DISTINCT FROM o.{c}" for c in chave)
        existe = f"EXISTS (SELECT 1 FROM market.{tabela} n WHERE n.ticker = :n AND {casa})"
        if aplicar:
            mover = conn.execute(text(
                f"UPDATE market.{tabela} AS o SET ticker = :n "
                f"WHERE o.ticker = :a AND NOT {existe}"), p).rowcount
            descartar = conn.execute(text(
                f"DELETE FROM market.{tabela} WHERE ticker = :a"), p).rowcount
        else:
            mover, descartar = conn.execute(text(
                f"SELECT COUNT(*) FILTER (WHERE NOT {existe}), COUNT(*) FILTER (WHERE {existe}) "
                f"FROM market.{tabela} o WHERE o.ticker = :a"), p).fetchone()
        out[tabela] = {"mover": int(mover or 0), "descartar": int(descartar or 0)}
    return out


def _alias(conn, cols, p: dict, codigo_cvm, aplicar: bool) -> dict | str:
    if "market.ticker_alias" not in cols:
        return "tabela ausente"
    antigo, novo = p["a"], p["n"]
    linhas = [(normalizar(s), normalizar(b), m or "") for s, b, m in conn.execute(text(
        "SELECT brapi_symbol, b3_ticker, motivo FROM market.ticker_alias "
        "WHERE brapi_symbol IN (:a, :n)"), p).fetchall()]
    ja = any(s == antigo and b == novo and m == MOTIVO_TROCA for s, b, m in linhas)
    rel = {
        "gravar": None if ja else f"{antigo}->{novo}",
        # direção legada (brapi_symbol = novo): mandaria o histórico do
        # código atual para o antigo em fix_orphan_alias_tickers e nas notícias
        "apagar": [f"{s}->{b} ({m})" for s, b, m in linhas if s == novo and b == antigo],
        # alias do código atual para OUTRA companhia (incorporação): não é
        # troca, fica — e vai como pendência no relatório
        "terceiros": [f"{s}->{b} ({m})" for s, b, m in linhas if s == novo and b != antigo],
    }
    if aplicar:
        conn.execute(text("""
            INSERT INTO market.ticker_alias (brapi_symbol, b3_ticker, codigo_cvm, motivo)
            VALUES (:a, :n, :c, :m)
            ON CONFLICT (brapi_symbol) DO UPDATE
                SET b3_ticker = EXCLUDED.b3_ticker, motivo = EXCLUDED.motivo,
                    codigo_cvm = COALESCE(EXCLUDED.codigo_cvm, market.ticker_alias.codigo_cvm)
        """), {**p, "c": codigo_cvm, "m": MOTIVO_TROCA})
        conn.execute(text(
            "DELETE FROM market.ticker_alias WHERE brapi_symbol = :n AND b3_ticker = :a"), p)
    return rel


def _setores(conn, cols, p: dict, aplicar: bool) -> dict | str | None:
    tem = cols.get("public.setores")
    if not tem or not set(_CLASSIFICACAO) <= tem:
        return "tabela ausente"
    antigo, novo = p["a"], p["n"]
    listagem = '"LISTAGEM"' if "LISTAGEM" in tem else "NULL"
    linhas = {}
    for tk, nome, setor, sub, seg, lst in conn.execute(text(f"""
            SELECT {_TK_SETORES}, nome_empresa, "SETOR", "SUBSETOR", "SEGMENTO", {listagem}
            FROM public.setores WHERE {_TK_SETORES} IN (:a, :n) ORDER BY ticker
            """), p).fetchall():
        linhas.setdefault(tk, {"nome": nome, "classif": (setor, sub, seg), "listagem": lst})
    la, ln = linhas.get(antigo), linhas.get(novo)
    if la is None and ln is None:
        return None
    if ln is None:
        origem = antigo
    elif la is None:
        origem = novo
    else:
        origem = escolher_classificacao(la["listagem"], ln["listagem"])
        origem = antigo if origem == "antigo" else novo
    classif = linhas[origem]["classif"]
    alinhar = [tk for tk, r in ((antigo, la), (novo, ln))
               if r is not None and r["classif"] != classif]
    rel = {"origem": origem, "classificacao": " / ".join(str(c or "") for c in classif),
           "alinhar": alinhar, "inserir": novo if ln is None else None}
    if aplicar:
        valores = {"s": classif[0], "ss": classif[1], "seg": classif[2]}
        for tk in alinhar:
            conn.execute(text(f"""
                UPDATE public.setores SET "SETOR" = :s, "SUBSETOR" = :ss, "SEGMENTO" = :seg
                WHERE {_TK_SETORES} = :tk
            """), {**valores, "tk": tk})
        if ln is None:
            # sem a linha, o código atual sairia do universo da tela quando o
            # antigo fosse desativado (load_setores exige setor)
            conn.execute(text(f"""
                INSERT INTO public.setores (ticker, nome_empresa, "SETOR", "SUBSETOR", "SEGMENTO")
                SELECT :n, :nome, :s, :ss, :seg
                WHERE NOT EXISTS (SELECT 1 FROM public.setores WHERE {_TK_SETORES} = :n)
            """), {**valores, "n": novo, "nome": la["nome"]})
    return rel


def unificar(conn, antigo: str, novo: str, *, aplicar: bool = False,
             codigo_cvm: int | None = None) -> dict:
    """O histórico de ``antigo`` passa a ser de ``novo``. Devolve o relatório.

    Ordem: o ativo novo existe e está ativo (as FKs dos fatos apontam para
    ele) → fatos mudam de dono (na mesma chave vale a linha do código novo;
    a do antigo é descartada) → o antigo fica inativo → alias antigo → novo
    → ``public.setores`` com uma classificação só. Não toca na fita
    (b3_security_history), nos payloads brutos nem em ticker_cvm: são
    registro do que existiu sob o código antigo.
    """
    antigo, novo = normalizar(antigo), normalizar(novo)
    if antigo == novo or not mesma_classe(antigo, novo):
        raise ValueError(f"{antigo} -> {novo} não é troca de código da mesma classe")
    p = {"a": antigo, "n": novo}
    cols = _colunas(conn)

    ativos = {normalizar(t): {"company_id": cid, "ativo": bool(at)}
              for t, cid, at in conn.execute(text(
                  "SELECT ticker, company_id, is_active FROM market.assets "
                  "WHERE ticker IN (:a, :n)"), p).fetchall()}
    ca = (ativos.get(antigo) or {}).get("company_id")
    cn = (ativos.get(novo) or {}).get("company_id")
    if ca is not None and cn is not None and ca != cn:
        raise ValueError(f"{antigo} (company_id {ca}) e {novo} (company_id {cn}) "
                         "são companhias diferentes")
    rel = {"antigo": antigo, "novo": novo, "aplicado": bool(aplicar),
           "ativos": {antigo: ativos.get(antigo), novo: ativos.get(novo)}}

    if aplicar:
        if novo not in ativos and antigo in ativos:
            conn.execute(text("""
                INSERT INTO market.assets (company_id, ticker, asset_type, exchange, currency, is_active)
                SELECT company_id, :n, asset_type, exchange, currency, TRUE
                FROM market.assets WHERE ticker = :a
                ON CONFLICT (ticker) DO NOTHING
            """), p)
        elif novo in ativos:
            conn.execute(text("""
                UPDATE market.assets
                SET is_active = TRUE,
                    company_id = COALESCE(company_id,
                        (SELECT company_id FROM market.assets WHERE ticker = :a))
                WHERE ticker = :n AND (is_active IS NOT TRUE OR company_id IS NULL)
            """), p)

    rel["fatos"] = _fatos(conn, cols, p, aplicar)

    if aplicar:
        conn.execute(text(
            "UPDATE market.assets SET is_active = FALSE "
            "WHERE ticker = :a AND is_active IS NOT FALSE"), p)
    rel["alias"] = _alias(conn, cols, p, codigo_cvm, aplicar)
    rel["setores"] = _setores(conn, cols, p, aplicar)
    if aplicar:
        logger.info("troca de código %s -> %s unificada: %s", antigo, novo, rel["fatos"])
    return rel
