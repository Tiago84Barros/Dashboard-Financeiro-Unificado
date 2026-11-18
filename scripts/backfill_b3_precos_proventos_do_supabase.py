"""Traz ao armazém local os preços e proventos da B3 que só o Supabase tem.

Contexto (15/11/2026): o Supabase deixou de receber atualização (diretriz do
usuário) e o app4 passa a ler a B3 do armazém. Antes da troca, a paridade
medida mostrou que o armazém não era superconjunto da cópia da nuvem:

- ``market.historical_prices``: 13.932 linhas de ação/unit/ETF só no Supabase,
  quase todas pregões de 2025-2026 gravados de julho a setembro de 2026, quando
  a ingestão ainda escrevia direto na nuvem (o caminho local começou em
  05/10/2026). Mesma fonte (``brapi_legacy_quote``), nunca chegaram ao local.
- ``market.dividends``: 522 linhas só no Supabase, das quais 454 são fósseis
  de revisão (o armazém tem o mesmo provento, mesma data-ex e tipo, com valor
  ou pagamento já corrigido -- ver ``upsert-sem-delete-deixa-fossil``). Só as
  que **não** têm par por (ticker, data-ex, tipo) no armazém são lacuna.

O que o script faz:

- lê o Supabase (só leitura, em lotes por letra inicial, porque a conexão cai
  em leitura grande);
- preços: insere no armazém as linhas (ticker, date) que faltam, com
  ``ON CONFLICT DO NOTHING`` -- nunca sobrescreve o valor do armazém, que é a
  versão mais nova. ``raw_payload_id`` vai nulo (o id é da nuvem); procedência,
  ``knowledge_at`` e ``availability_quality`` são mantidos;
- proventos: insere só as linhas sem par por (ticker, data-ex, tipo) no
  armazém; o fóssil de revisão fica de fora;
- FIIs ficam fora (são de outra fatia), e ticker que o armazém não tem em
  ``market.assets`` sai em ``retidos``;
- com ``--apply``, grava as chaves inseridas em ``local_staging/backups/``
  (fora do git) para permitir desfazer.

Não escreve no Supabase. Simula por omissão:

    python scripts/backfill_b3_precos_proventos_do_supabase.py
    python scripts/backfill_b3_precos_proventos_do_supabase.py --apply
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import string
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

PASTA_BACKUP = RAIZ / "local_staging" / "backups"

_COLS_PRECO = [
    "ticker", "date", "open", "high", "low", "close", "adjusted_close",
    "volume", "updated_at", "source", "knowledge_at", "availability_quality",
    "content_hash",
]
_COLS_PROVENTO = [
    "ticker", "payment_date", "ex_date", "amount", "type", "source",
    "event_date", "created_at", "updated_at",
]
# ``event_date`` é coluna gerada no armazém: lida para o relatório, nunca inserida.
_COLS_PROVENTO_INSERT = [c for c in _COLS_PROVENTO if c != "event_date"]


def _ler(engine, sql: str, params: dict | None = None,
         tentativas: int = 4) -> pd.DataFrame:
    """Lê com nova tentativa: a conexão com o Supabase cai em leitura longa."""
    import time

    from sqlalchemy.exc import OperationalError

    for i in range(tentativas):
        try:
            with engine.connect() as c:
                return pd.read_sql_query(text(sql), c, params=params or {})
        except OperationalError:
            if i == tentativas - 1:
                raise
            engine.dispose()
            time.sleep(2 * (i + 1))
    raise AssertionError("inalcançável")


def _ler_linhas(engine, tabela: str, cols: list[str], tickers: list[str]) -> pd.DataFrame:
    """Linhas completas só dos tickers pedidos, em lotes pequenos."""
    partes = []
    for i in range(0, len(tickers), 20):
        lote = tickers[i:i + 20]
        partes.append(_ler(engine, f"select {', '.join(cols)} from market.{tabela}"
                                   " where ticker = any(:tks)", {"tks": lote}))
    partes = [p for p in partes if not p.empty]
    return pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=cols)


def _ler_por_letra(engine, sql: str) -> pd.DataFrame:
    print(f"lendo {sql[:60]}...", file=sys.stderr, flush=True)
    partes = [_ler(engine, sql, {"l": letra}) for letra in string.ascii_uppercase]
    partes = [p for p in partes if not p.empty]
    return pd.concat(partes, ignore_index=True) if partes else pd.DataFrame()


def precos_faltantes(local: pd.DataFrame, nuvem: pd.DataFrame) -> pd.DataFrame:
    """Linhas da nuvem cujo (ticker, date) não existe no armazém."""
    if nuvem.empty:
        return nuvem
    chaves = local[["ticker", "date"]].drop_duplicates() if not local.empty else (
        pd.DataFrame(columns=["ticker", "date"]))
    m = nuvem.merge(chaves, on=["ticker", "date"], how="left", indicator=True)
    return m[m["_merge"] == "left_only"].drop(columns="_merge")


def proventos_faltantes(local: pd.DataFrame, nuvem: pd.DataFrame) -> pd.DataFrame:
    """Linhas da nuvem sem par por (ticker, data-ex, tipo) no armazém.

    O par pela data-ex e tipo, e não pela chave única (que inclui o valor),
    é o que separa lacuna de fóssil de revisão: se o armazém já tem o provento
    daquela data-ex, a versão da nuvem com outro valor é a antiga.
    """
    if nuvem.empty:
        return nuvem
    k = ["ticker", "ex_date", "type"]
    pares = local[k].drop_duplicates() if not local.empty else pd.DataFrame(columns=k)
    m = nuvem.merge(pares, on=k, how="left", indicator=True)
    return m[m["_merge"] == "left_only"].drop(columns="_merge")


def _contar_fosseis(loc, d_sup: pd.DataFrame, d_falta: pd.DataFrame,
                    nao_fii: set) -> int:
    """Linhas só da nuvem pela chave única, mas com par por data-ex e tipo."""
    if d_sup.empty:
        return 0
    k = ["ticker", "event_date", "type", "amount"]
    d_loc = _ler_por_letra(loc, "select ticker, event_date, type, amount"
                                " from market.dividends where left(ticker, 1) = :l")
    so_nuvem = d_sup[d_sup["ticker"].isin(nao_fii)].merge(
        d_loc[k].drop_duplicates(), on=k, how="left", indicator=True)
    return int((so_nuvem["_merge"] == "left_only").sum() - len(d_falta))


def _registros(df: pd.DataFrame, cols: list[str]) -> list[dict]:
    out = []
    for r in df[cols].itertuples(index=False):
        d = {}
        for c, v in zip(cols, r):
            if v is None or (not isinstance(v, (str, bytes)) and pd.isna(v)):
                d[c] = None
            elif hasattr(v, "item"):
                d[c] = v.item()
            elif isinstance(v, pd.Timestamp):
                d[c] = v.to_pydatetime()
            else:
                d[c] = v
        out.append(d)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="grava no armazém")
    args = ap.parse_args(argv)

    from core.database import get_engine
    from scripts.publish_fii_selection_from_local import _warehouse_url

    loc = create_engine(_warehouse_url())
    sup = get_engine()

    ativos = _ler(loc, "select ticker, asset_type from market.assets")
    nao_fii = set(ativos.loc[ativos["asset_type"] != "fii", "ticker"])
    fii = set(ativos.loc[ativos["asset_type"] == "fii", "ticker"])

    # Primeiro só as chaves (leve); as linhas completas vêm depois, só dos
    # tickers com pregão faltando.
    sql_k = "select ticker, date from market.historical_prices where left(ticker, 1) = :l"
    p_loc = _ler_por_letra(loc, sql_k)
    k_sup = _ler_por_letra(sup, sql_k)
    k_sup = k_sup[~k_sup["ticker"].isin(fii)]
    k_falta = precos_faltantes(p_loc, k_sup)
    p_sup = _ler_linhas(sup, "historical_prices", _COLS_PRECO,
                        sorted(set(k_falta["ticker"])))
    p_falta = precos_faltantes(p_loc, p_sup)
    p_retidos = sorted(set(p_falta["ticker"]) - nao_fii)
    p_falta = p_falta[p_falta["ticker"].isin(nao_fii)]

    sql_d = ("select " + ", ".join(_COLS_PROVENTO) +
             " from market.dividends where left(ticker, 1) = :l")
    d_loc = _ler_por_letra(loc, "select ticker, ex_date, type from market.dividends"
                                " where left(ticker, 1) = :l")
    d_sup = _ler_por_letra(sup, sql_d)
    d_sup = d_sup[~d_sup["ticker"].isin(fii)]
    d_falta = proventos_faltantes(d_loc, d_sup)
    d_retidos = sorted(set(d_falta["ticker"]) - nao_fii)
    d_falta = d_falta[d_falta["ticker"].isin(nao_fii)]

    resumo = {
        "precos": {
            "candidatas": int(len(p_falta)),
            "tickers": int(p_falta["ticker"].nunique()) if len(p_falta) else 0,
            "por_ano": {int(k): int(v) for k, v in
                        p_falta["date"].map(lambda d: d.year).value_counts().sort_index().items()},
            "retidos_sem_ativo": p_retidos,
        },
        "proventos": {
            "candidatas": int(len(d_falta)),
            "tickers": int(d_falta["ticker"].nunique()) if len(d_falta) else 0,
            "fosseis_de_revisao_fora": _contar_fosseis(loc, d_sup, d_falta, nao_fii),
            "retidos_sem_ativo": d_retidos,
        },
        "apply": bool(args.apply),
    }

    if args.apply and (len(p_falta) or len(d_falta)):
        def _n(c, t):
            return c.execute(text(f"select count(*) from market.{t}")).scalar_one()

        with loc.begin() as c:
            # ``rowcount`` do executemany não soma os lotes: mede antes e depois.
            n0_p, n0_d = _n(c, "historical_prices"), _n(c, "dividends")
            if len(p_falta):
                sql = text(
                    "insert into market.historical_prices (" + ", ".join(_COLS_PRECO) + ") "
                    "values (" + ", ".join(":" + x for x in _COLS_PRECO) + ") "
                    "on conflict (ticker, date) do nothing")
                c.execute(sql, _registros(p_falta, _COLS_PRECO))
            if len(d_falta):
                sql = text(
                    "insert into market.dividends (" + ", ".join(_COLS_PROVENTO_INSERT) + ") "
                    "values (" + ", ".join(":" + x for x in _COLS_PROVENTO_INSERT) + ") "
                    "on conflict on constraint uq_dividends do nothing")
                c.execute(sql, _registros(d_falta, _COLS_PROVENTO_INSERT))
            ins_p = _n(c, "historical_prices") - n0_p
            ins_d = _n(c, "dividends") - n0_d
        resumo["precos"]["inseridas"] = int(ins_p)
        resumo["proventos"]["inseridas"] = int(ins_d)
        PASTA_BACKUP.mkdir(parents=True, exist_ok=True)
        carimbo = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        destino = PASTA_BACKUP / f"backfill_b3_do_supabase_{carimbo}.json"
        destino.write_text(json.dumps({
            "precos": [[t, str(d)] for t, d in p_falta[["ticker", "date"]].itertuples(index=False)],
            "proventos": [[t, str(e), str(x), tp, float(a)] for t, e, x, tp, a in
                          d_falta[["ticker", "event_date", "ex_date", "type", "amount"]]
                          .itertuples(index=False)],
        }, ensure_ascii=False), encoding="utf-8")
        resumo["chaves_gravadas_em"] = str(destino.relative_to(RAIZ))

    print(json.dumps(resumo, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
