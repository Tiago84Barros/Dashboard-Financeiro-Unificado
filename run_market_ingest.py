"""
run_market_ingest.py
CLI da ingestão BRAPI Pro -> Supabase (schema market.*).

Comandos:
    python run_market_ingest.py bootstrap   # 16 anos de histórico (range=max)
    python run_market_ingest.py daily        # atualização leve diária
    python run_market_ingest.py annual       # refresh de demonstrações
    python run_market_ingest.py reprocess    # recalcula indicadores (sem rede)

Opções comuns:
    --tickers PETR4 VALE3   processa só estes
    --source setores|brapi  universo (default: setores)
    --limit N               limita a N empresas (teste)

Requer schema market.* aplicado (013_market_brapi_schema.sql) e BRAPI_TOKEN
(para o universo completo; sem token, só os 4 tickers gratuitos da brapi).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))
from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("market_ingest")


def main() -> int:
    p = argparse.ArgumentParser(description="Ingestão BRAPI Pro -> Supabase (market.*)")
    p.add_argument("command",
                   choices=["validate", "cadastro", "bootstrap", "daily", "annual",
                            "reprocess", "renormalize", "parity", "fiis",
                            "fiis-reprocess", "fiis-cvm", "fiis-series",
                            "fiis-metrics", "fiis-vacancia", "fiis-imoveis",
                            "benchmark", "setores"])
    p.add_argument("--dry-run", action="store_true", help="cadastro: só simula")
    p.add_argument("--tickers", nargs="*", help="Tickers específicos")
    p.add_argument("--source", default=None,
                   choices=["setores", "ticker_cvm", "brapi", "market"])
    p.add_argument("--limit", type=int, default=None,
                   help="bootstrap: tamanho do lote por execução (default 50)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    from data_pipeline.market import ingest

    tickers = [t.upper().replace(".SA", "") for t in args.tickers] if args.tickers else None
    log.info("Comando=%s  source=%s  limit=%s  tickers=%s",
             args.command, args.source, args.limit, tickers or "(universo)")

    if args.command == "validate":
        rep = ingest.validate(tickers or ["PETR4", "ITUB4", "WEGE3"])
        log.info("BRAPI token: %s", rep.get("brapi_token"))
        log.info("schema market.*: %s | persistido: %s",
                 rep.get("schema_market"), rep.get("persistido"))
        for tk, e in rep.get("tickers", {}).items():
            if e.get("erro"):
                log.warning("  %s: ERRO %s", tk, e["erro"])
            else:
                b = e.get("blocos", {})
                log.info("  %s: perfil=%s cotação=%s histórico=%d DRE=%d BP=%d DFC=%d div=%d ind=%d %s",
                         tk, b.get("perfil"), b.get("cotacao"), b.get("historico", 0),
                         b.get("dre", 0), b.get("bp", 0), b.get("dfc", 0),
                         b.get("dividendos", 0), b.get("indicadores", 0),
                         (f"| faltando: {', '.join(e['faltando'])}" if e.get("faltando") else ""))
        log.info("Tempo: %ss | Requisições estimadas: %s",
                 rep.get("tempo_s"), rep.get("requisicoes_estimadas"))
        log.info("Contagem por tabela market.*:")
        for t, s in (rep.get("contagem_tabelas") or {}).items():
            log.info("    %-22s total=%s dup=%s", t, s.get("total"), s.get("duplicados"))
        dups = rep.get("duplicidades") or {}
        log.info("Duplicidades: %s", dups if dups else "nenhuma ✅")
        if args.json:
            print(json.dumps(rep, indent=2, default=str))
        return 0

    if args.command == "cadastro":
        rep = ingest.cadastro(apply=not args.dry_run)
        log.info("Cadastro CVM — tickers no mapa=%s novos cvm_to_ticker=%s companies=%s %s",
                 rep.get("tickers_no_mapa"), rep.get("novos_cvm_to_ticker"),
                 rep.get("companies_upsert"), f"ERRO: {rep['erro']}" if rep.get("erro") else "")
        if args.json:
            print(json.dumps(rep, indent=2, default=str))
        return 0 if not rep.get("erro") else 1

    if args.command == "setores":
        rep = ingest.enrich_setores()
        log.info("Setores — cad=%s mapa(CVM->B3)=%s empresas=%s mapeadas=%s cvm_raw=%s erros=%s",
                 rep.get("cad"), rep.get("mapa"), rep.get("empresas"),
                 rep.get("mapeadas"), rep.get("cvm_raw"), rep.get("erros"))
        if args.json:
            print(json.dumps(rep, indent=2, default=str))
        return 0 if rep.get("erros", 0) != -1 else 1

    if args.command in ("fiis", "fiis-reprocess", "fiis-cvm", "fiis-series",
                        "fiis-metrics", "fiis-vacancia", "fiis-imoveis", "benchmark"):
        from data_pipeline.market import fii_ingest
        if args.command == "fiis-cvm":
            rep = fii_ingest.enrich_cvm()
            log.info("FIIs CVM — ano=%s no_banco=%s casados=%s gravados=%s erros=%s",
                     rep.get("ano"), rep.get("fiis_no_banco"), rep.get("casados"),
                     rep.get("gravados"), rep.get("erros"))
        elif args.command == "fiis-metrics":
            rep = fii_ingest.backfill_metrics_monthly()
            log.info("FIIs métricas mensais — anos=%s no_banco=%s linhas=%s gravados=%s erros=%s",
                     rep.get("anos"), rep.get("fiis_no_banco"), rep.get("linhas"),
                     rep.get("gravados"), rep.get("erros"))
        elif args.command == "fiis-vacancia":
            rep = fii_ingest.enrich_vacancia()
            log.info("FIIs vacância — com_imoveis=%s com_vacancia=%s gravados=%s erros=%s",
                     rep.get("fiis_com_imoveis"), rep.get("com_vacancia"),
                     rep.get("gravados"), rep.get("erros"))
        elif args.command == "fiis-imoveis":
            rep = fii_ingest.ingest_imoveis()
            log.info("FIIs imóveis — fiis=%s com_imoveis=%s imoveis=%s gravados=%s erros=%s",
                     rep.get("fiis"), rep.get("com_imoveis"), rep.get("imoveis"),
                     rep.get("gravados"), rep.get("erros"))
        elif args.command == "fiis-series":
            rep = fii_ingest.backfill_series()
            log.info("FIIs séries — fiis=%s precos=%s dividendos=%s erros=%s",
                     rep.get("fiis"), rep.get("precos"), rep.get("dividendos"), rep.get("erros"))
        elif args.command == "benchmark":
            rep = fii_ingest.ingest_benchmark("XFIX11")  # proxy do IFIX (ETF)
            log.info("Benchmark %s — precos=%s erros=%s",
                     rep.get("ticker"), rep.get("precos"), rep.get("erros"))
        else:
            rep = (fii_ingest.reprocess() if args.command == "fiis-reprocess"
                   else fii_ingest.ingest(limit=args.limit, tickers=tickers))
            log.info("FIIs — candidatos=%s fiis=%s etfs_ignorados=%s gravados=%s erros=%s",
                     rep.get("candidatos"), rep.get("fiis"), rep.get("etfs_ignorados"),
                     rep.get("gravados"), rep.get("erros"))
        if args.json:
            print(json.dumps(rep, indent=2, default=str))
        return 0 if rep.get("erros", 0) != -1 else 1

    if args.command == "parity":
        from data_pipeline.market import parity
        save = "artifacts/quality/parity_market_vs_legacy.json"
        try:
            import os as _os
            _os.makedirs("artifacts/quality", exist_ok=True)
        except Exception:
            save = None
        rep = parity.run_parity(save_path=save)
        mv, st_ = rep["multiplos"], rep["setores"]
        cov = mv["coverage"]
        log.info("PARIDADE legado×market")
        log.info("  multiplos — legado=%s market=%s comuns=%s (só_legado=%s só_market=%s)",
                 cov["legacy"], cov["market"], cov["common"],
                 cov["only_legacy"], cov["only_market"])
        ov = mv.get("overall", {})
        log.info("  concordância geral: %s (%s/%s comparações)",
                 ov.get("agree_rate"), ov.get("agree"), ov.get("compared"))
        log.info("  por métrica (concord. | comparados | mediana rel | p90 rel):")
        for m, s in mv["metrics"].items():
            flag = ""
            if s.get("legacy_constant"):
                flag = f"  ⚠ legado CONSTANTE={s.get('legacy_constant_value')} (placeholder)"
            elif s.get("legacy_zero_rate") and s["legacy_zero_rate"] >= 0.5:
                flag = f"  ⚠ legado zero em {int(s['legacy_zero_rate']*100)}% (bug)"
            if s.get("compared"):
                log.info("    %-20s %s | n=%s | med=%s | p90=%s%s",
                         m, s.get("agree_rate"), s["compared"],
                         s.get("median_rel_diff"), s.get("p90_rel_diff"), flag)
            else:
                log.info("    %-20s sem comparação (%s)%s", m,
                         s.get("motivo") or f"missing_market={s.get('missing_market')}", flag)
        log.info("  setores — comuns=%s | divergências de SETOR=%s",
                 st_["common"], st_["setor_mismatch"])
        if save:
            log.info("  relatório salvo em %s", save)
        if args.json:
            print(json.dumps(rep, indent=2, default=str, ensure_ascii=False))
        return 0

    if args.command == "bootstrap":
        prog = ingest.bootstrap(tickers, args.source or "ticker_cvm", args.limit or 50)
    elif args.command == "daily":
        prog = ingest.daily(tickers, args.source or "setores", args.limit)
    elif args.command == "annual":
        prog = ingest.annual(tickers, args.source or "setores", args.limit)
    elif args.command == "renormalize":
        prog = ingest.renormalize(tickers, args.limit)
    else:
        prog = ingest.reprocess_metrics(tickers, args.limit)

    if prog.get("erros") == -1:
        log.error("Falha: schema market.* ausente ou banco não conectado.")
        return 1

    log.info("Progresso — empresas=%d preços=%d demonstrações=%d dividendos=%d "
             "indicadores=%d erros=%d (tickers=%d)",
             prog["empresas"], prog["precos"], prog["demonstracoes"], prog["dividendos"],
             prog["indicadores"], prog["erros"], prog["tickers"])
    if "restantes" in prog:
        log.info("Bootstrap — universo=%s | processados agora=%s | RESTANTES=%s",
                 prog.get("universo"), prog["tickers"], prog["restantes"])
    if args.json:
        print(json.dumps(prog, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
