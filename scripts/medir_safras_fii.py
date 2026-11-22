# -*- coding: utf-8 -*-
"""Mede a carteira de safras FII (estrutura B3) no armazém local.

Reconstrói os snapshots PIT só em 31/03 de cada ano, aplica a elegibilidade
integrada estrita da data, e entrega a `core.fii_safras_carteira.medir_safras`:
score de 31/03/N decide a carteira de abril/N a março/N+1, portão Econômico
por tipo, líder + maior participação, orçamento igual por tipo, cap 0,25,
regra D1 (caixa à Selic), custos de giro e IR de 20% sobre ganho.

Não grava nada por padrão. ``--gravar`` escreve
``data/fii_safras_carteira.json``. As opções aceitam listas e o script mede
todas as combinações -- o default de cada uma é a regra da B3:

- ``--portao selic,selic_liquida,ifix``: a referência da margem do portão
  (Selic bruta, Selic líquida de IR, IFIX);
- ``--thr-selic 15,5,0``: margem mínima das duas variantes de Selic;
- ``--thr-ifix 0,2``: margem mínima sobre o IFIX;
- ``--nomes-por-tipo b3,3,5``: ``b3`` é líder + maior participação; um
  número N leva os N primeiros do score de cada tipo aprovado;
- ``--segmento tipo,cadastral``: o grupo da carteira -- o tipo do FII, ou o
  tipo partido pelo segmento do cadastro CVM (logística, lajes, shoppings).
  ``universo`` de cada ano traz quantos elegíveis tinham segmento cadastral;
- ``--decaimento on,off``: o desconto de 7% a.a. por ano consecutivo de
  liderança (teto 30%) da B3 ligado (default) ou desligado. Desligado, o
  líder de score estável nunca sai; a comparação mede se a rotação forçada
  paga o giro e o IR que gera.

    python scripts/medir_safras_fii.py
    python scripts/medir_safras_fii.py --thr-selic 15,5,0 --gravar
    python scripts/medir_safras_fii.py --portao selic,selic_liquida,ifix \
        --thr-selic 15,0 --nomes-por-tipo b3,3,5
    python scripts/medir_safras_fii.py --portao ifix --segmento tipo,cadastral
    python scripts/medir_safras_fii.py --decaimento on,off --nomes-por-tipo b3,4
"""
from __future__ import annotations

import argparse
import itertools
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
from core.fii_safras_carteira import (  # noqa: E402
    PORTOES,
    SEGMENTACOES,
    VERSAO,
    cobertura_do_segmento,
    medir_safras,
)
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
        # snapshots só em 31/03: o de outro ano é de fundo que saiu da bolsa
        linhas = linhas_pit_na_data(ultimo_snapshot_na_data(s, d, idade_max_dias=0))
        try:
            eleg, info = apply_integrated_eligibility(linhas, IntegratedEligibilityPolicy())
        except ColunasDeElegibilidadeAusentes as exc:
            falhas[d.year] = str(exc)
            continue
        elegiveis[d.year] = list(eleg)
        contagem[d.year] = {"com_dado": len(linhas), "elegiveis": len(eleg),
                            "candidatos_concessao_ignorados":
                                len(info.get("concession_candidates") or ()),
                            "elegiveis_com_segmento_cadastral":
                                cobertura_do_segmento(eleg)["com_segmento"]}
    cen = _macro_scenarios(conn, datas)
    selic_decisao = {d.year: cen[d.date().isoformat()]["selic"] / 100.0
                     for d in datas if d.date().isoformat() in cen}
    ifix, nome_bench = carregar_benchmark_fii(conn, returns)
    return {"status": "ok", "elegiveis": elegiveis, "returns": returns, "ifix": ifix,
            "benchmark": nome_bench, "selic_decisao": selic_decisao,
            "selic_mensal": _selic_mensal(conn), "falhas": falhas, "contagem": contagem}


def _enxuto(res: dict) -> dict:
    out = {k: res.get(k) for k in ("versao", "status", "ir_calculado", "ifix_fonte",
                                   "ifix_no_portao", "leitura", "resumo",
                                   "fora_do_porte", "blockers")}
    out["safras"] = [{
        "safra": r["safra"], "contada": r.get("contada"), "caixa": r.get("caixa"),
        "n_elegiveis": r.get("n_elegiveis"), "n_nomes": len(r.get("pesos") or {}),
        "cobertura_segmento": r.get("cobertura_segmento"),
        "pesos": {k: round(v, 4) for k, v in (r.get("pesos") or {}).items()},
        "aprovados": [g["segmento"] for g in r.get("segmentos", []) if g["aprovado"]],
        "reprovados": {g["segmento"]: g["motivo"] for g in r.get("segmentos", [])
                       if not g["aprovado"]},
        "margens_pct": {g["segmento"]: {k: (None if g.get(f"margem_{k}_pct") is None
                                        else round(float(g[f"margem_{k}_pct"]), 2))
                                    for k in ("selic", "selic_liquida", "ifix")}
                        for g in r.get("segmentos", [])},
        **{k: (None if r.get(k) is None else round(float(r[k]), 4))
           for k in ("bruto", "giro", "custo", "ir", "liquido", "ifix", "ew", "selic",
                     "excesso_liquido_ifix", "excesso_liquido_ew")},
        "motivo_fora": r.get("motivo_fora"),
    } for r in res.get("safras", [])]
    return out


def _lista(texto: str) -> list[str]:
    return [x.strip() for x in str(texto).split(",") if x.strip()]


_DECAIMENTO = {"on": True, "off": False}


def variantes(portoes: str, thr_selic: str, thr_ifix: str,
              nomes_por_tipo: str, segmento: str = "tipo",
              decaimento: str = "on") -> list[tuple[str, dict]]:
    """(rótulo, params) de cada combinação; o limiar acompanha o portão.

    Com ``segmento`` só ``tipo`` e ``decaimento`` só ``on`` (os defaults), o
    rótulo é o da v1.1.0.
    """
    nomes = [None if n.lower() == "b3" else int(n) for n in _lista(nomes_por_tipo)]
    decaimentos = [d.lower() for d in _lista(decaimento)]
    for dec in decaimentos:
        if dec not in _DECAIMENTO:
            raise SystemExit(f"--decaimento desconhecido: {dec} (use on, off)")
    segmentos = _lista(segmento)
    for seg in segmentos:
        if seg not in SEGMENTACOES:
            raise SystemExit(f"--segmento desconhecido: {seg} (use {', '.join(SEGMENTACOES)})")
    out: list[tuple[str, dict]] = []
    for portao in _lista(portoes):
        if portao not in PORTOES:
            raise SystemExit(f"--portao desconhecido: {portao} (use {', '.join(PORTOES)})")
        chave = PORTOES[portao][1]
        limiares = [float(x) for x in _lista(thr_ifix if chave == "thr_ifix" else thr_selic)]
        for thr, n, seg, dec in itertools.product(limiares, nomes, segmentos,
                                                  decaimentos):
            rotulo = (f"portao={portao}|{chave}={thr:g}|nomes_por_tipo="
                      f"{'b3' if n is None else n}")
            params = {"portao": portao, chave: thr, "nomes_por_tipo": n}
            if seg != "tipo":
                rotulo += f"|segmento={seg}"
                params["segmento"] = seg
            if dec != "on":
                rotulo += f"|decaimento={dec}"
                params["decaimento"] = _DECAIMENTO[dec]
            out.append((rotulo, params))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--portao", default="selic",
                    help="referência do portão: selic, selic_liquida, ifix (lista)")
    ap.add_argument("--thr-selic", default="15",
                    help="margem mínima sobre a Selic (%%), lista separada por vírgula")
    ap.add_argument("--thr-ifix", default="0",
                    help="margem mínima sobre o IFIX (%%), lista separada por vírgula")
    ap.add_argument("--nomes-por-tipo", default="b3",
                    help="b3 (líder + maior participação) ou N fundos por tipo (lista)")
    ap.add_argument("--segmento", default="tipo",
                    help="grupo da carteira: tipo, cadastral (lista)")
    ap.add_argument("--decaimento", default="on",
                    help="desconto do líder consecutivo: on, off (lista)")
    ap.add_argument("--gravar", action="store_true")
    args = ap.parse_args()
    grade = variantes(args.portao, args.thr_selic, args.thr_ifix, args.nomes_por_tipo,
                      args.segmento, args.decaimento)
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
    for rotulo, params in grade:
        res = medir_safras(dados["elegiveis"], dados["returns"], dados["ifix"],
                           selic_decisao=dados["selic_decisao"],
                           selic_mensal=dados["selic_mensal"], params=params,
                           ifix_fonte=dados["benchmark"])
        saida["variantes"][rotulo] = _enxuto(res)
    texto = json.dumps(saida, indent=2, ensure_ascii=False, default=str)
    print(texto)
    if args.gravar:
        CAMINHO.write_text(texto, encoding="utf-8")
        print("gravado em", CAMINHO)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
