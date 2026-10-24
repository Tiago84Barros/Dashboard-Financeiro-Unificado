# -*- coding: utf-8 -*-
"""Treina e valida a previsao da variacao do lucro B3 (core/b3_previsao_lucros).

Tres etapas, em ordem, e o holdout so na ultima:

    # 1) desenvolvimento: walk-forward nas safras 2014-2021, escolhe o modelo
    python scripts/treinar_previsao_lucros_b3.py --etapa dev

    # 2) pares (ano, ticker, score, retorno) da aba de Portfolio B3, sem cabeca
    python scripts/treinar_previsao_lucros_b3.py --etapa pares --pares <arquivo.json>

    # 3) final: holdout 2022-2025 UMA vez, safra viva, efeito no Rank-IC, artefato
    python scripts/treinar_previsao_lucros_b3.py --etapa final --pares <arquivo.json>

Fontes, todas ja em disco -- nada aqui busca na rede:
  - DFP da CVM (cache data/cache/cvm/dfp/dfp_cia_aberta_AAAA.zip, 2010-2025):
    a MESMA fonte para empresa viva e para a que saiu, com DT_RECEB da primeira
    versao como AvailableAt. Fonte diferente para mortos e vivos seria vies de
    procedencia embutido no painel.
  - Armazem local: codigo CVM -> ticker (market.companies + market.assets) e o
    COTAHIST (market.b3_security_history), que diz em que safra cada empresa
    estava NEGOCIADA. data/b3_saidas.json acrescenta as empresas que sairam.

O holdout fica preso ao hash da configuracao (core.b3_previsao_lucros.config_hash):
rodar a etapa final de novo com o mesmo hash e recusado, a menos de
--refazer-holdout, e cada execucao fica contada no artefato.
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import core.b3_previsao_lucros as pl  # noqa: E402

ANOS_DFP = range(2010, 2026)
SAFRA_VIVA = 2026
# Pregoes minimos no trimestre anterior a decisao para contar como negociada.
MIN_PREGOES_LISTADA = 5


def _log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# ── fontes ───────────────────────────────────────────────────────────────────

def _engine():
    import sqlalchemy as sa
    from scripts.publish_fii_selection_from_local import _warehouse_url
    return sa.create_engine(os.getenv("DFU_WAREHOUSE_URL") or _warehouse_url())


def carregar_universo(eng) -> pd.DataFrame:
    """cd_cvm -> tickers, nome, setor, saida (bool). Vivas e as que sairam."""
    import sqlalchemy as sa
    vivas = pd.read_sql(sa.text("""
        select distinct c.codigo_cvm::int as cd_cvm, a.ticker, c.name as nome,
               c.sector as setor
        from market.assets a join market.companies c on c.id = a.company_id
        where a.asset_type in ('stock', 'unit') and c.codigo_cvm is not null
    """), eng)
    vivas["saida"] = False
    doc = json.loads((ROOT / "data" / "b3_saidas.json").read_text(encoding="utf-8"))
    sai = pd.DataFrame([{"cd_cvm": int(e["cd_cvm"]), "ticker": e["ticker"],
                         "nome": e.get("nome"), "setor": e.get("SETOR"), "saida": True}
                        for e in doc.get("empresas", []) if e.get("cd_cvm")])
    uni = pd.concat([vivas, sai], ignore_index=True)
    return uni.drop_duplicates(["cd_cvm", "ticker"])


def carregar_listagem(eng, tickers: list[str]) -> pd.DataFrame:
    """(ticker, safra) em que o papel negociou no 1o trimestre da safra --
    a janela antes da decisao de 01/04. Vale igual para vivas e mortas."""
    import sqlalchemy as sa
    q = sa.text("""
        select ticker, extract(year from trade_date)::int as safra, count(*) as n
        from market.b3_security_history
        where ticker = any(:tk) and extract(month from trade_date) <= 3
        group by 1, 2
    """)
    df = pd.read_sql(q, eng, params={"tk": list(tickers)})
    return df[df["n"] >= MIN_PREGOES_LISTADA][["ticker", "safra"]]


def carregar_dfp(dfp_dir: Path, cds: set[int]) -> pd.DataFrame:
    from data_pipeline.market.b3_saidas import fundamentos, ler_dfp
    linhas = []
    for ano in ANOS_DFP:
        arq = dfp_dir / f"dfp_cia_aberta_{ano}.zip"
        if not arq.exists():
            raise SystemExit(f"DFP {ano} ausente em {dfp_dir}: sem ela o painel tem buraco")
        t = time.time()
        dems = ler_dfp(arq.read_bytes(), ano, cds)
        for cd, dem in dems.items():
            f = fundamentos(dem)
            f.update({"cd_cvm": int(cd), "ano": int(ano), "available_at": dem.available_at,
                      "consolidado": bool(dem.consolidado)})
            linhas.append(f)
        _log(f"DFP {ano}: {len(dems)} empresas ({time.time() - t:.1f}s)")
    return pd.DataFrame(linhas)


def montar(dfp_dir: Path):
    eng = _engine()
    uni = carregar_universo(eng)
    lst = carregar_listagem(eng, sorted(uni["ticker"].unique()))
    fund = carregar_dfp(dfp_dir, set(uni["cd_cvm"]))
    painel = pl.montar_painel(fund)
    listada = lst.merge(uni[["ticker", "cd_cvm"]], on="ticker")[["cd_cvm", "safra"]] \
        .drop_duplicates()
    listada["listada"] = True
    painel = painel.merge(listada, on=["cd_cvm", "safra"], how="left")
    n_antes = len(painel)
    painel = painel[painel["listada"].eq(True)].drop(columns="listada")
    _log(f"painel: {n_antes} linhas com DFP, {len(painel)} com a empresa negociada na safra")
    return uni, painel, fund


# ── etapas ───────────────────────────────────────────────────────────────────

def _dev_safras():
    return list(range(pl.SAFRA_MIN_TESTE, pl.HOLDOUT_INICIO))


def _hold_safras():
    return list(range(pl.HOLDOUT_INICIO, SAFRA_VIVA))


def _limpar(o):
    if isinstance(o, dict):
        return {str(k): _limpar(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_limpar(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else round(float(o), 6)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, (pd.Timestamp, datetime)):
        return None if pd.isna(o) else o.isoformat()
    return o


def etapa_dev(painel: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    preds = pl.walk_forward(painel, _dev_safras(), log=_log)
    modelos = ["ingenuo", "ridge", "gbm", "mlp"]
    por_safra = pl.metricas_por_safra(preds, modelos)
    escolha = pl.escolher_modelo(preds)
    agregado = pl.metricas(preds, modelos)
    for s in por_safra:
        _log(f"dev safra {s['safra']}: n={s['n']} " + " ".join(
            f"{k}={s[k]['mae']:.4f}" for k in modelos))
    _log(f"dev agregado MAE: " + " ".join(f"{k}={agregado[k]['mae']:.4f}" for k in modelos))
    _log(f"escolha: {escolha['modelo']} (mlp elegivel: {escolha['mlp_elegivel']})")
    return preds, {"por_safra": por_safra, "agregado": agregado, "escolha": escolha,
                   "comparacoes": {k: pl.comparar_com_ingenuo(preds, k)
                                   for k in ("ridge", "gbm", "mlp")}}


def etapa_pares(destino: Path) -> None:
    """Roda a aba de Portfolio B3 sem cabeca (mesmo codigo e configuracao da
    medicao oficial do Rank-IC) e guarda os pares com ticker."""
    from scripts.medir_vantagem_oos import B3_CONFIG, _apontar_para_armazem_local, \
        _fonte_efetiva_b3, _rodar_aba_b3
    _apontar_para_armazem_local()
    os.chdir(ROOT)
    t = time.time()
    resultados = _rodar_aba_b3()
    pares = []
    for res in resultados:
        pares.extend(res.get("ic_pairs_tk") or [])
    if not pares:
        raise SystemExit("a aba nao devolveu ic_pairs_tk: nada a gravar")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps({
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "configuracao": B3_CONFIG, "fonte": _fonte_efetiva_b3(),
        "pares": [list(p) for p in pares]}, ensure_ascii=False), encoding="utf-8")
    _log(f"{len(pares)} pares gravados em {destino} ({time.time() - t:.0f}s)")


def _previsao_por_ticker(preds: pd.DataFrame, uni: pd.DataFrame, modelo: str) -> dict:
    tk = uni.groupby("cd_cvm")["ticker"].apply(list).to_dict()
    out = {}
    for cd, s, p in zip(preds["cd_cvm"], preds["safra"], preds[f"pred_{modelo}"]):
        for t in tk.get(cd, []):
            out[(int(s), str(t))] = float(p)
    return out


def etapa_final(uni, painel, pares_arq: Path | None, saida: Path, refazer: bool) -> dict:
    h = pl.config_hash()
    anterior = pl.carregar_artefato(str(saida))
    execucoes = 1
    if anterior and anterior.get("config_hash") == h and anterior.get("holdout"):
        if not refazer:
            raise SystemExit(
                f"holdout ja usado com a configuracao {h} em {anterior.get('gerado_em')}. "
                "Rodar de novo e escolher o resultado e a forma mais barata de "
                "overfitting; use --refazer-holdout so com motivo (fica contado).")
        execucoes = int(anterior.get("holdout_execucoes") or 1) + 1

    dev_preds, dev = etapa_dev(painel)
    modelo = dev["escolha"]["modelo"]
    com_mlp = True
    hold_preds = pl.walk_forward(painel, _hold_safras(), com_mlp=com_mlp, log=_log)
    modelos = ["ingenuo", "ridge", "gbm", "mlp"]
    hold = {"por_safra": pl.metricas_por_safra(hold_preds, modelos),
            "agregado": pl.metricas(hold_preds, modelos),
            "veredito": pl.comparar_com_ingenuo(hold_preds, modelo),
            "outros_modelos": {k: pl.comparar_com_ingenuo(hold_preds, k)
                               for k in ("ridge", "gbm", "mlp") if k != modelo}}
    v = hold["veredito"]
    _log(f"HOLDOUT {modelo}: MAE {v['mae_modelo']:.4f} vs ingenuo {v['mae_ingenuo']:.4f} "
         f"dif IC [{v['dif_erro_abs']['ic_low']:.4f}, {v['dif_erro_abs']['ic_high']:.4f}] "
         f"-> {v['veredito']}; sinal {v['acerto_sinal']:.3f} (n={v['n_sinal']}) "
         f"vs majoritario {v['acerto_sinal_majoritario']:.3f} -> {v['sinal']}")

    todas = pd.concat([dev_preds, hold_preds], ignore_index=True)
    todas = pl.intervalos_pit(todas, modelo)
    hold["cobertura_intervalo"] = pl.cobertura(todas[todas["safra"] >= pl.HOLDOUT_INICIO])

    # Safra viva: treina com tudo que era publico em 01/04/2026.
    viva = pl.conjunto_teste(painel, SAFRA_VIVA)
    treino_viva = pl.conjunto_treino(painel, SAFRA_VIVA)
    prev_viva = pl.treinar_e_prever(treino_viva, viva, com_mlp=(modelo == "mlp"))[modelo]
    lo, hi = pl.intervalo_conformal(todas["y"] - todas[f"pred_{modelo}"])
    viva = viva.assign(pred=prev_viva, ic_low=prev_viva + lo, ic_high=prev_viva + hi)

    rank_ic = {"status": "nao_medido",
               "motivo": "pares (ano, ticker, score, retorno) nao informados (--pares)"}
    if pares_arq and pares_arq.exists():
        doc = json.loads(pares_arq.read_text(encoding="utf-8"))
        rank_ic = pl.efeito_rank_ic([tuple(p) for p in doc["pares"]],
                                    _previsao_por_ticker(todas, uni, modelo))
        rank_ic["fonte_pares"] = {"gerado_em": doc.get("gerado_em"),
                                  "fonte": doc.get("fonte"),
                                  "configuracao": doc.get("configuracao")}
        if rank_ic.get("status") == "medido":
            _log(f"Rank-IC score {rank_ic['score']['mean']:.4f} -> combinado "
                 f"{rank_ic['combinado']['mean']:.4f}; delta IC "
                 f"[{rank_ic['delta']['ci_low']:.4f}, {rank_ic['delta']['ci_high']:.4f}] "
                 f"-> {rank_ic['veredito']}")

    info = uni.groupby("cd_cvm").agg(nome=("nome", "first"), setor=("setor", "first"),
                                     saida=("saida", "max"))
    info["tickers"] = uni.groupby("cd_cvm")["ticker"].apply(lambda s: sorted(set(s)))

    def _linha(r, pcol):
        i = info.loc[r.cd_cvm] if r.cd_cvm in info.index else None
        return {"cd_cvm": int(r.cd_cvm), "safra": int(r.safra),
                "tickers": list(i["tickers"]) if i is not None else [],
                "nome": i["nome"] if i is not None else None,
                "setor": i["setor"] if i is not None else None,
                "saida": bool(i["saida"]) if i is not None else False,
                "lucro_base": r.lucro_base, "ativo_base": r.ativo_base,
                "y_previsto": getattr(r, pcol),
                "ic_low": r.ic_low, "ic_high": r.ic_high,
                "y_real": getattr(r, "y", None),
                "lucro_previsto": r.lucro_base + getattr(r, pcol) * r.ativo_base,
                "lucro_real": getattr(r, "lucro_alvo", None)}

    eleg = {s: {"n_listadas_com_dfp": int((painel["safra"] == s).sum()),
                "n_base_publicada": int(len(pl.conjunto_teste(painel, s))),
                "n_com_alvo": int(pl.conjunto_teste(painel, s)["y"].notna().sum())}
            for s in _dev_safras() + _hold_safras() + [SAFRA_VIVA]}

    art = {
        "versao_metodologia": pl.VERSAO_METODOLOGIA,
        "config_hash": h,
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "holdout_execucoes": execucoes,
        "pergunta": ("Prever a variacao do lucro do proximo exercicio vence o "
                     "modelo ingenuo 'lucro = lucro deste ano'? E melhora o "
                     "Rank-IC do score B3?"),
        "configuracao": {"features": pl.FEATURES, "hiperparametros": pl.HIPERPARAMETROS,
                         "safras_dev": _dev_safras(), "safras_holdout": _hold_safras(),
                         "safra_viva": SAFRA_VIVA, "alvo": "(LL_s - LL_s-1) / Ativo_s-1",
                         "data_decisao": "01/04 da safra (core.b3_vigencia.REBAL_MONTH)"},
        "universo": {"n_empresas": int(painel["cd_cvm"].nunique()),
                     "n_saidas": int(uni[uni["saida"]]["cd_cvm"].nunique()),
                     "n_linhas_painel": int(len(painel)), "por_safra": eleg},
        "dev": dev,
        "holdout": hold,
        "rank_ic": rank_ic,
        "previsoes_atuais": [_linha(r, "pred") for r in viva.itertuples(index=False)],
        "historico": [_linha(r, f"pred_{modelo}") for r in todas.itertuples(index=False)],
        "limitacoes": [
            "Valores da DFP sao os da ULTIMA versao (reapresentacao posterior e "
            "look-ahead brando); a disponibilidade e a da PRIMEIRA (DT_RECEB).",
            "Empresas que sairam antes de 2016 e instituicoes financeiras fora de "
            "data/b3_saidas.json so entram se ainda estiverem em market.assets.",
            "Empresa so entra na safra em que negociou no 1o trimestre "
            f"(>= {MIN_PREGOES_LISTADA} pregoes no COTAHIST).",
            "Empresa sem DFP do ano-alvo (saiu, atrasou, cancelou) tem previsao sem "
            "desfecho: conta em n_sem_alvo, nao some.",
            "O ingenuo preve variacao zero e nao tem sinal; o sinal e comparado ao "
            "sinal majoritario do treino (McNemar) e a 50% (binomial).",
        ],
    }
    art = _limpar(art)
    saida.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(saida, "wt", encoding="utf-8") as fh:
        json.dump(art, fh, ensure_ascii=False, separators=(",", ":"))
    _log(f"artefato gravado em {saida} ({saida.stat().st_size / 1024:.0f} KiB)")
    return art


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--etapa", choices=("dev", "pares", "final"), required=True)
    ap.add_argument("--dfp-dir", type=Path, default=Path.cwd() / "data" / "cache" / "cvm" / "dfp")
    ap.add_argument("--pares", type=Path, default=None)
    ap.add_argument("--saida", type=Path, default=ROOT / pl.ARTEFATO)
    ap.add_argument("--dev-json", type=Path, default=None,
                    help="grava o resultado do desenvolvimento (fora do artefato)")
    ap.add_argument("--refazer-holdout", action="store_true")
    a = ap.parse_args(argv)

    if a.etapa == "pares":
        if not a.pares:
            raise SystemExit("--pares e obrigatorio na etapa pares")
        etapa_pares(a.pares)
        return 0
    uni, painel, _ = montar(a.dfp_dir)
    if a.etapa == "dev":
        _, dev = etapa_dev(painel)
        if a.dev_json:
            a.dev_json.write_text(json.dumps(_limpar(dev), ensure_ascii=False, indent=1),
                                  encoding="utf-8")
        return 0
    etapa_final(uni, painel, a.pares, a.saida, a.refazer_holdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
