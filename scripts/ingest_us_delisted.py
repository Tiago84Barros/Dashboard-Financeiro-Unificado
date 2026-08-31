# -*- coding: utf-8 -*-
"""Ingesta os FUNDAMENTOS das empresas americanas que ja sairam da bolsa.

Por que existe: `market_us.companies` so tem sobrevivente. As 12.107 saidas
estavam registradas em `market_us.delistings` e nao entravam em lugar nenhum --
"registrar saida nao e consumir saida". Cada safra do painel PIT e montada a
partir do universo VIVO, entao a empresa que morreu nunca esteve em safra
alguma, nem nas datas em que estava viva e negociando. O universo do ranking
era 100% sobrevivente em todas as 16 safras.

O que este passo corrige e o que NAO corrige:

  - CORRIGE o universo do ranking. A SEC serve companyfacts de empresa morta
    integralmente (verificado: SPARK THERAPEUTICS, 273 conceitos; CCA
    INDUSTRIES, 303). Com os fundamentos gravados e `delisted_date` preenchida,
    `compute_score_history` passa a incluir a empresa nas safras ANTERIORES a
    saida -- o portao `delisted < as_of` ja existe em scoring_history.py e ate
    hoje nunca disparou, porque `assets.delisted_date` estava NULL nas 7.654
    linhas.
  - NAO corrige o retorno realizado. Nenhuma fonte acessivel serve preco de
    ticker morto: yfinance devolve zero barra ("possibly delisted"), Stooq
    responde com desafio de bot. Sem preco nao ha retorno futuro, e por isso a
    empresa entra no ranking e sai da medicao de excesso. Fechar essa metade
    exige convencao declarada de retorno de deslistagem, que e outro passo e
    depende de separar a causa da saida (8-K item 1.03 x 2.01).

Guarda obrigatoria -- ticker reciclado: `market_us.assets` e UNIQUE
(symbol, exchange) e `repo.upsert_asset` casa nessa mesma chave. 55 dos 1.899
simbolos resolvidos pertencem HOJE a uma empresa viva com outro CIK; ingerir por
simbolo repontaria a linha da empresa viva para a morta. Esses 55 sao pulados e
contados, nunca gravados.

Segunda guarda -- simbolo reusado entre duas empresas MORTAS: 27 tickers
aparecem sob mais de um CIK (AAN e a Aaron's antiga, morta em 2021, e a nova,
morta em 2025). Sao vidas sequenciais sob o mesmo ticker, e o schema nao tem
mapeamento simbolo->CIK datado para desempatar. Ficam de fora e sao contados.

Cobertura: so os 1.899 simbolos resolvidos entram, e eles sao de 2020 em diante
(2011-2019 tem 1 simbolo em 8.879 registros -- teto do inline-XBRL, a fonte nao
carrega o ticker). A empresa que saiu em 2023, porem, estava viva desde antes:
ela entra em TODAS as safras entre a estreia e a saida, nao so nas recentes.

    python scripts/ingest_us_delisted.py --dry-run
    python scripts/ingest_us_delisted.py --limit 5
    python scripts/ingest_us_delisted.py
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

from data_pipeline.us import ingest  # noqa: E402

# Um simbolo por empresa morta, com a data de saida mais antiga registrada.
# Exclui (a) quem ja foi ingerido -- casando por CIK, nunca por simbolo -- e
# (b) o simbolo que hoje pertence a outra empresa viva.
_SQL_ALVOS = """
  SELECT d.symbol,
         lpad(d.cik::text, 10, '0') AS cik,
         MIN(d.delisted_date)       AS delisted_date,
         MIN(d.absence_year)        AS absence_year
  FROM market_us.delistings d
  WHERE d.symbol IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM market_us.companies c
                    WHERE c.cik = lpad(d.cik::text, 10, '0'))
    AND NOT EXISTS (SELECT 1 FROM market_us.assets a
                    JOIN market_us.companies co ON co.id = a.company_id
                    WHERE a.symbol = d.symbol
                      AND co.cik <> lpad(d.cik::text, 10, '0'))
    AND d.symbol NOT IN (SELECT symbol FROM market_us.delistings
                         WHERE symbol IS NOT NULL
                         GROUP BY symbol HAVING COUNT(DISTINCT cik) > 1)
  GROUP BY d.symbol, lpad(d.cik::text, 10, '0')
  ORDER BY d.symbol
"""

# Quantos ficaram de fora pela guarda do ticker reciclado -- numero que precisa
# aparecer no resultado, senao a exclusao vira truncamento silencioso.
_SQL_AMBIGUOS = """
  SELECT COUNT(*) FROM (
    SELECT symbol FROM market_us.delistings WHERE symbol IS NOT NULL
    GROUP BY symbol HAVING COUNT(DISTINCT cik) > 1) t
"""

_SQL_COLIDEM = """
  SELECT COUNT(DISTINCT d.symbol)
  FROM market_us.delistings d
  JOIN market_us.assets a ON a.symbol = d.symbol
  JOIN market_us.companies co ON co.id = a.company_id
  WHERE d.symbol IS NOT NULL AND co.cik <> lpad(d.cik::text, 10, '0')
"""

# A saida so passa a valer para a safra depois que a data esta na linha do ativo.
# Sem este UPDATE a empresa entraria em TODAS as safras, inclusive nas
# posteriores a propria morte -- que e um vies pior do que o que estamos
# corrigindo.
# Marca por company_id, NUNCA por simbolo: `AAC.U` e gravada como `AACU`
# (o normalizador tira o ponto) e um UPDATE casando pelo simbolo pedido nao
# encontra a linha -- falha silenciosa que deixaria a empresa morta viva em
# todas as safras posteriores a propria morte.
_SQL_MARCA_SAIDA = """
  UPDATE market_us.assets
     SET delisted_date = :delisted_date,
         is_active     = FALSE,
         is_delisted   = TRUE,
         updated_at    = NOW()
   WHERE company_id = :company_id
"""


def _engine():
    from scripts.publish_fii_selection_from_local import _warehouse_url
    return create_engine(
        _warehouse_url().replace("postgresql://", "postgresql+psycopg2://"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None,
                    help="ingere apenas os N primeiros (piloto)")
    ap.add_argument("--dry-run", action="store_true",
                    help="lista os alvos e sai sem gravar nada")
    args = ap.parse_args()

    engine = _engine()
    with engine.connect() as conn:
        alvos = [dict(r._mapping) for r in conn.execute(text(_SQL_ALVOS))]
        colidem = conn.execute(text(_SQL_COLIDEM)).scalar() or 0
        ambiguos = conn.execute(text(_SQL_AMBIGUOS)).scalar() or 0

    if args.limit:
        alvos = alvos[:args.limit]

    if args.dry_run:
        print(json.dumps({"alvos": len(alvos),
                          "pulados_ticker_reciclado": colidem,
                          "pulados_simbolo_ambiguo": ambiguos,
                          "exemplos": [a["symbol"] for a in alvos[:10]]},
                         default=str))
        return 0

    provider = ingest.make_provider()
    # A SEC nao lista mais estes tickers em company_tickers.json; o simbolo->CIK
    # sai do proprio registro de saidas. Sem isto get_profile devolve None e a
    # empresa fica invisivel exatamente como esta hoje.
    provider.set_cik_hints({a["symbol"]: a["cik"] for a in alvos})

    resultado = {"tentados": 0, "ok": 0, "sem_fatos": 0, "erros": 0,
                 "marcados": 0, "vivos_nao_marcados": 0,
                 "pulados_ticker_reciclado": colidem,
                 "pulados_simbolo_ambiguo": ambiguos}
    t0 = time.time()
    for i, alvo in enumerate(alvos, 1):
        sym = alvo["symbol"]
        resultado["tentados"] += 1
        try:
            # O perfil sai do submissions da SEC e responde a pergunta que
            # decide a marcacao: o CIK ainda consta em alguma bolsa?
            perfil = provider.get_profile(sym) or {}
            # with_prices=False: nenhuma fonte serve preco de ticker morto, e
            # tentar so gera erro registrado e tempo gasto.
            r = ingest.ingest_symbol(provider, engine, sym,
                                     with_prices=False)
        except Exception as exc:  # noqa: BLE001
            resultado["erros"] += 1
            print(f"[{i}/{len(alvos)}] {sym} ERRO {type(exc).__name__}: "
                  f"{str(exc)[:90]}", flush=True)
            continue
        if not r.get("ok"):
            resultado["sem_fatos"] += 1
            continue
        resultado["ok"] += 1
        # A saida so e gravada com evidencia de que a empresa saiu mesmo. O
        # registro de `delistings` deriva a saida da AUSENCIA de 10-K, e
        # ausencia tambem e o rastro de quem so trocou de ticker ou se
        # reorganizou. Marcar esses como mortos injetaria morte falsa no painel
        # -- vies pior do que o que estamos corrigindo. A prova e a SEC nao
        # listar mais bolsa nem ticker para o CIK.
        if perfil.get("exchangeShortName"):
            resultado["vivos_nao_marcados"] += 1
            continue
        with engine.begin() as conn:
            n = conn.execute(text(_SQL_MARCA_SAIDA),
                             {"delisted_date": alvo["delisted_date"],
                              "company_id": r["company_id"]}).rowcount
        resultado["marcados"] += int(n or 0)
        if i % 50 == 0:
            print(f"[{i}/{len(alvos)}] ok={resultado['ok']} "
                  f"sem_fatos={resultado['sem_fatos']} "
                  f"({time.time() - t0:.0f}s)", flush=True)

    resultado["segundos"] = round(time.time() - t0, 1)
    print(json.dumps(resultado, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
