"""Publica o painel dos cenários análogos em ``data/public/cenarios_analogos.json.gz``.

Monta, mês a mês desde 2010, o estado macro (Selic e direção em 6 meses, IPCA
12m, dólar em 12 meses, Treasury de 10 anos, P/L mediano, BOVA11 em 12 meses) e
o que veio depois, com :func:`core.memoria_mercado.cenarios_macro.painel_mensal`.
O casamento com o mês de hoje roda na hora da pergunta, sobre o painel.

Fontes, todas locais e só leitura:

- armazém ``macro_staging``: SGS 432 (Selic meta) e 13522 (IPCA 12m), gravados
  por ``scripts/ingerir_macro_brasil.py`` (histórico com ``--desde 2008-01-01``);
  câmbio médio mensal do BCE (BRL/EUR ÷ USD/EUR) e FRED DGS10, gravados por
  ``run_macro_updates.py``;
- armazém ``postgres``: BOVA11 em ``market.historical_prices``;
- arquivo ``data/public/valuation_historico.json.gz``: série ``mercado_b3``.

Recusa publicar (saída 1) se a Selic ou o BOVA11 estiverem parados: o "hoje"
do painel seria um mês que já passou com a data de agora.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SELIC_MAXIMA_DIAS = 5
BOVA_MAXIMA_DIAS = 10
VALUATION = ROOT / "data" / "public" / "valuation_historico.json.gz"

_ECB_BRL = "EXR|M.BRL.EUR.SP00.A"
_ECB_USD = "EXR|M.USD.EUR.SP00.A"

# A tabela é append-only: a mesma (série, período) aparece a cada coleta. Vale
# a coleta mais nova; valor nulo (feriado no DGS10) fica fora.
_SQL_MACRO = """
    SELECT DISTINCT ON (reference_period) reference_period, value
      FROM macro_observations
     WHERE provider = :provedor AND provider_code = :codigo
       AND value IS NOT NULL AND NOT COALESCE(is_forecast, false)
     ORDER BY reference_period, retrieved_at DESC
"""
_SQL_BOVA = """
    SELECT date, close FROM market.historical_prices
     WHERE ticker = 'BOVA11' AND close > 0
"""


def coletar(conn_macro, conn_mercado, valuation: Path = VALUATION) -> dict[str, dict]:
    from sqlalchemy import text

    def serie(provedor, codigo):
        return {r[0]: float(r[1]) for r in conn_macro.execute(
            text(_SQL_MACRO), {"provedor": provedor, "codigo": codigo})}

    brl, usd_eur = serie("ecb", _ECB_BRL), serie("ecb", _ECB_USD)
    dados = {
        "selic": serie("bcb_sgs", "432"),
        "ipca12": serie("bcb_sgs", "13522"),
        "usd": {k: brl[k] / usd_eur[k] for k in brl if usd_eur.get(k)},
        "us10": serie("fred", "DGS10"),
        "bova": {r[0]: float(r[1]) for r in conn_mercado.execute(text(_SQL_BOVA))},
        "pl": {},
    }
    try:
        art = json.loads(gzip.decompress(valuation.read_bytes()).decode("utf-8"))
        dados["pl"] = {date.fromisoformat(r[0]): float(r[1])
                       for r in (art.get("mercado_b3") or {}).get("serie") or []
                       if r[1]}
    except (OSError, ValueError):
        pass  # sem P/L a dimensão sai do denominador; "fontes" registra n=0
    return dados


def fontes(dados: dict[str, dict]) -> dict:
    return {k: {"n": len(v), "ate": max(v).isoformat() if v else None}
            for k, v in dados.items()}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--saida", type=Path, default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    from sqlalchemy import create_engine

    from core.macro_data.database import get_local_macro_engine
    from core.memoria_mercado import cenarios_macro as cmac
    from scripts.publish_fii_selection_from_local import _warehouse_url

    engine_macro = get_local_macro_engine()
    if engine_macro is None:
        print("MACRO_LOCAL_DB_URL não configurada -- este script lê o Docker local.")
        return 2
    engine_mercado = create_engine(_warehouse_url())
    try:
        with engine_macro.connect() as cm_, engine_mercado.connect() as cmerc:
            dados = coletar(cm_, cmerc)
    finally:
        engine_macro.dispose()
        engine_mercado.dispose()

    agora = datetime.now(timezone.utc)
    hoje = agora.date()
    relatorio = {"fontes": fontes(dados)}
    erros = []
    for chave, maximo in (("selic", SELIC_MAXIMA_DIAS), ("bova", BOVA_MAXIMA_DIAS)):
        ultimo = max(dados[chave], default=None)
        if ultimo is None or (hoje - ultimo).days > maximo:
            erros.append(f"{chave} sem dado nos últimos {maximo} dias")
    if erros:
        relatorio["erro"] = "; ".join(erros) + (
            "; rode scripts/ingerir_macro_brasil.py e o espelho antes de publicar")
        print(json.dumps(relatorio, ensure_ascii=False, sort_keys=True), flush=True)
        return 1

    painel = cmac.painel_mensal(hoje=hoje, **dados)
    res = cmac.analogos(painel)
    dados_gz = cmac.serializar(agora, painel, relatorio["fontes"])
    destino = args.saida or cmac.CAMINHO_PADRAO
    relatorio.update(meses=len(painel), episodios=[a.mes for a in res.episodios],
                     bytes=len(dados_gz), destino=str(destino), dry_run=args.dry_run)
    if not args.dry_run:
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(dados_gz)
    print(json.dumps(relatorio, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
