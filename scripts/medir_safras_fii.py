# -*- coding: utf-8 -*-
"""Mede a carteira de safras FII (estrutura B3) no armazém local.

Reconstrói os snapshots PIT só em 31/03 de cada ano, aplica a elegibilidade
integrada estrita da data, e entrega a `core.fii_safras_carteira.medir_safras`:
score de 31/03/N decide a carteira de abril/N a março/N+1, portão Econômico
por tipo, líder + maior participação, orçamento igual por tipo, cap 0,25,
regra D1 (caixa à Selic), custos de giro e IR de 20% sobre ganho.

Não grava nada por padrão. ``--gravar`` escreve
``data/fii_safras_carteira.json``. ``--thr-selic`` aceita uma lista para a
sensibilidade do portão (a margem sobre a Selic é o critério que mais
reprova FII, cujo retorno de longo prazo anda perto do CDI).

    python scripts/medir_safras_fii.py
    python scripts/medir_safras_fii.py --thr-selic 15,5,0 --gravar
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from core.fii_integrated_model import (  # noqa: E402
    ColunasDeElegibilidadeAusentes,
    IntegratedEligibilityPolicy,
    apply_integrated_eligibility,
)
from core.fii_safras_carteira import VERSAO, medir_safras  # noqa: E402
from core.fii_validation import (  # noqa: E402
    linhas_pit_na_data,
    preparar_snapshots,
    ultimo_snapshot_na_data,
)
from data_pipeline.market.fii_pit import (  # noqa: E402
    _load_frames,
    _macro_scenarios,
    _monthly_returns,
    carregar_benchmark_fii,
    reconstruct_snapshots,
)
from scripts.publish_fii_selection_from_local import _warehouse_url  # noqa: E402

CAMINHO = ROOT / "data" / "fii_safras_carteira.json"


def _selic_mensal(conn) -> pd.Series:
    """Selic a.a. (fração) por mês, do último mês observável -- DCA do portão."""
    macro = pd.read_sql(text(
        'SELECT data, "Selic_Final" AS selic FROM public.info_economica_mensal ORDER BY data'),
        conn)
    if macro.empty:
        return pd.Series(dtype=float)
    macro["data"] = pd.to_datetime(macro["data"], utc=True).dt.tz_localize(None)
    serie = pd.to_numeric(macro["selic"], errors="coerce") / 100.0
    serie.index = macro["data"] + pd.offsets.MonthEnd(0)
    # o mês M só é conhecido depois de fechar: vale para o retorno de M+1
    return serie.dropna().shift(1, freq="ME")


def coletar(conn) -> dict:
    prices, dividends, observations, exposures, funds = _load_frames(conn)
    returns = _monthly_returns(prices, dividends)
    if returns.empty:
        return {"status": "blocked", "blockers": ["retornos mensais ausentes"]}
    returns["date"] = pd.to_datetime(returns["date"]).dt.normalize()
    primeiro = int(returns["date"].min().year) + 1
    ultimo = int(returns["date"].max().year)
    datas = [pd.Timestamp(year=a, month=3, day=31) for a in range(primeiro, ultimo + 1)
             if pd.Timestamp(year=a, month=3, day=31) <= returns["date"].max()]
    snaps = pd.DataFrame(reconstruct_snapshots(
        prices, dividends, observations, exposures, funds, decision_dates=datas))
    if snaps.empty:
        return {"status": "blocked", "blockers": ["nenhum snapshot em 31/03"]}
    s = preparar_snapshots(snaps)
    elegiveis: dict[int, list[dict]] = {}
    falhas: dict[int, str] = {}
    contagem: dict[int, dict] = {}
    for d in datas:
        linhas = linhas_pit_na_data(ultimo_snapshot_na_data(s, d))
        try:
            eleg, info = apply_integrated_eligibility(linhas, IntegratedEligibilityPolicy())
        except ColunasDeElegibilidadeAusentes as exc:
            falhas[d.year] = str(exc)
            continue
        elegiveis[d.year] = list(eleg)
        contagem[d.year] = {"com_dado": len(linhas), "elegiveis": len(eleg),
                            "candidatos_concessao_ignorados":
                                len(info.get("concession_candidates") or ())}
    cen = _macro_scenarios(conn, datas)
    selic_decisao = {d.year: cen[d.date().isoformat()]["selic"] / 100.0
                     for d in datas if d.date().isoformat() in cen}
    ifix, nome_bench = carregar_benchmark_fii(conn, returns)
    return {"status": "ok", "elegiveis": elegiveis, "returns": returns, "ifix": ifix,
            "benchmark": nome_bench, "selic_decisao": selic_decisao,
            "selic_mensal": _selic_mensal(conn), "falhas": falhas, "contagem": contagem}


def _enxuto(res: dict) -> dict:
    out = {k: res.get(k) for k in ("versao", "status", "ir_calculado", "leitura", "resumo",
                                   "fora_do_porte", "blockers")}
    out["safras"] = [{
        "safra": r["safra"], "contada": r.get("contada"), "caixa": r.get("caixa"),
        "n_elegiveis": r.get("n_elegiveis"), "n_nomes": len(r.get("pesos") or {}),
        "pesos": {k: round(v, 4) for k, v in (r.get("pesos") or {}).items()},
        "aprovados": [g["tipo"] for g in r.get("segmentos", []) if g["aprovado"]],
        "reprovados": {g["tipo"]: g["motivo"] for g in r.get("segmentos", [])
                       if not g["aprovado"]},
        **{k: (None if r.get(k) is None else round(float(r[k]), 4))
           for k in ("bruto", "giro", "custo", "ir", "liquido", "ifix", "ew", "selic",
                     "excesso_liquido_ifix", "excesso_liquido_ew")},
        "motivo_fora": r.get("motivo_fora"),
    } for r in res.get("safras", [])]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--thr-selic", default="15",
                    help="margem mínima sobre a Selic (%%), lista separada por vírgula")
    ap.add_argument("--gravar", action="store_true")
    args = ap.parse_args()
    engine = create_engine(
        _warehouse_url().replace("postgresql://", "postgresql+psycopg2://"))
    try:
        with engine.connect() as conn:
            dados = coletar(conn)
    finally:
        engine.dispose()
    if dados["status"] != "ok":
        print(json.dumps(dados, indent=2, ensure_ascii=False))
        return 1
    saida = {"versao": VERSAO, "benchmark": dados["benchmark"],
             "falhas_de_insumo": dados["falhas"], "universo": dados["contagem"],
             "variantes": {}}
    for thr in [float(x) for x in args.thr_selic.split(",") if x.strip()]:
        res = medir_safras(dados["elegiveis"], dados["returns"], dados["ifix"],
                           selic_decisao=dados["selic_decisao"],
                           selic_mensal=dados["selic_mensal"], params={"thr_selic": thr})
        saida["variantes"][f"thr_selic={thr:g}"] = _enxuto(res)
    texto = json.dumps(saida, indent=2, ensure_ascii=False, default=str)
    print(texto)
    if args.gravar:
        CAMINHO.write_text(texto, encoding="utf-8")
        print("gravado em", CAMINHO)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
