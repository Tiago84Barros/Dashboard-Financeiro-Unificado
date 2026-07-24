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
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(_ROOT / ".env")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("market_ingest")

_REMOTE_HEAVY_COMMANDS = frozenset({
    "fiis-v2", "fiis-v2-history", "fiis-cvm-structured", "fiis-cvm-cri",
    "fiis-documents", "fiis-b3-history", "fiis-pit-backtest", "fiis-enrich",
})


def _local_database_target() -> bool:
    """Evita que cargas de arquivo/histórico sejam gravadas no Supabase."""
    url = (os.getenv("SUPABASE_UNIFICADO_URL") or os.getenv("DATABASE_URL") or
           os.getenv("SUPABASE_DB_URL") or "").lower()
    return "127.0.0.1" in url or "localhost" in url


def _point_to_warehouse() -> bool:
    """Usa a senha efetiva do container; o arquivo local é apenas fallback."""
    from dotenv import dotenv_values

    password = ""
    try:
        proc = subprocess.run(
            ["docker", "compose", "exec", "-T", "warehouse", "printenv",
             "POSTGRES_PASSWORD"],
            cwd=str(_ROOT / "warehouse"), capture_output=True, text=True,
            timeout=10, check=False,
        )
        if proc.returncode == 0:
            password = (proc.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        pass
    if not password:
        env_paths = [_ROOT / "warehouse" / ".env"]
        env_paths.extend(_ROOT.glob(".claude/worktrees/*/warehouse/.env"))
        warehouse_file = next((path for path in env_paths if path.exists()), None)
        password = str((dotenv_values(warehouse_file) if warehouse_file else {}).get(
            "WAREHOUSE_PASSWORD") or "").strip()
    if not password:
        log.error("senha do warehouse local indisponível")
        return False
    local_url = ("postgresql://postgres:" + quote(password, safe="")
                 + "@127.0.0.1:5433/postgres")
    for key in ("SUPABASE_UNIFICADO_URL", "DATABASE_URL", "SUPABASE_DB_URL"):
        os.environ[key] = local_url
    return True


def main() -> int:
    p = argparse.ArgumentParser(description="Ingestão BRAPI Pro -> Supabase (market.*)")
    p.add_argument("command",
                   choices=["validate", "cadastro", "bootstrap", "daily", "annual",
                            "reprocess", "renormalize", "integrity", "parity", "fiis",
                            "fiis-reprocess", "fiis-cvm", "fiis-series",
                            "fiis-metrics", "fiis-vacancia", "fiis-cadastro-gaps",
                            "fiis-imoveis",
                            "fiis-v2", "fiis-v2-history", "fiis-cvm-structured",
                            "fiis-cvm-cri", "fiis-v4", "fiis-v4-audit", "fiis-documents",
                            "fiis-registry", "fiis-b3-history", "fiis-entities",
                            "fiis-confidence", "fiis-pit-backtest", "fiis-monitor",
                            "fiis-enrich",
                            "benchmark", "setores"])
    p.add_argument("--dry-run", action="store_true", help="cadastro: só simula")
    p.add_argument("--tickers", nargs="*", help="Tickers específicos")
    p.add_argument("--source", default=None,
                   choices=["setores", "ticker_cvm", "brapi", "market"])
    p.add_argument("--limit", type=int, default=None,
                   help="bootstrap: tamanho do lote por execução (default 50)")
    p.add_argument("--years", type=int, default=5,
                   help="histórico/CVM: quantidade de anos, incluindo o atual")
    p.add_argument("--recent-months", type=int, default=24,
                   help="documentos FII: prioriza referências recentes (0=todas)")
    p.add_argument("--max-batch-mb", type=int, default=250,
                   help="documentos FII: orçamento máximo baixado por execução")
    p.add_argument("--max-document-mb", type=int, default=30,
                   help="documentos FII: tamanho máximo por arquivo")
    p.add_argument("--min-free-gb", type=int, default=10,
                   help="documentos FII: reserva mínima livre no disco local")
    p.add_argument("--candidate-limit", type=int, default=12,
                   help="fiis-enrich: quantidade de FIIs atuais priorizados")
    p.add_argument("--document-limit", type=int, default=150,
                   help="fiis-enrich: documentos máximos por execução")
    p.add_argument("--document-budget-mb", type=int, default=250,
                   help="fiis-enrich: orçamento de PDFs por execução")
    p.add_argument("--json", action="store_true")
    p.add_argument("--warehouse", action="store_true",
                   help="usa o PostgreSQL local warehouse em 127.0.0.1:5433")
    args = p.parse_args()

    if args.warehouse:
        if not _point_to_warehouse():
            return 1

    if (args.command in _REMOTE_HEAVY_COMMANDS and
            not _local_database_target() and
            os.getenv("ALLOW_HEAVY_REMOTE_INGEST", "").lower() != "true"):
        log.error(
            "Comando %s bloqueado: ingestão pesada exige --warehouse. "
            "Use ALLOW_HEAVY_REMOTE_INGEST=true somente para uma exceção consciente.",
            args.command)
        return 2

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
                        "fiis-metrics", "fiis-vacancia", "fiis-cadastro-gaps",
                        "fiis-imoveis", "fiis-v2",
                        "fiis-v2-history", "fiis-cvm-structured", "fiis-cvm-cri", "fiis-v4",
                        "fiis-v4-audit", "fiis-documents", "fiis-registry",
                        "fiis-b3-history", "fiis-entities", "fiis-confidence",
                        "fiis-pit-backtest", "fiis-monitor", "fiis-enrich", "benchmark"):
        from data_pipeline.market import fii_ingest
        if args.command == "fiis-registry":
            from data_pipeline.market.fii_registry import ingest_registry
            rep = ingest_registry()
            log.info("FIIs cadastro CVM — linhas=%s tickers vinculados=%s status=%s",
                     rep.get("rows"), rep.get("linked_tickers"), rep.get("status"))
        elif args.command == "fiis-b3-history":
            from data_pipeline.market.fii_b3_history import ingest_b3_history
            rep = ingest_b3_history(years=args.years)
            log.info("FIIs COTAHIST B3 — arquivos=%s linhas=%s tickers=%s erros=%s",
                     rep.get("archives"), rep.get("rows"), rep.get("tickers"),
                     len(rep.get("errors") or []))
        elif args.command == "fiis-entities":
            from data_pipeline.market.fii_entity_pipeline import resolve_entities
            rep = resolve_entities()
            log.info("FIIs entidades — novas=%s aceitas=%s propostas=%s",
                     rep.get("canonical_created"), rep.get("accepted"), rep.get("proposed"))
        elif args.command == "fiis-confidence":
            from data_pipeline.market.fii_confidence_pipeline import calibrate_parsers
            rep = calibrate_parsers()
            log.info("FIIs calibração — métricas=%s revisões=%s",
                     rep.get("calibrations"), rep.get("reviewed"))
        elif args.command == "fiis-pit-backtest":
            from data_pipeline.market.fii_pit import run_pit_validation
            rep = run_pit_validation(years=args.years)
            log.info("FIIs PIT — status=%s snapshots=%s períodos=%s benchmark=%s bloqueios=%s",
                     rep.get("status"), rep.get("snapshots"), rep.get("periods"),
                     rep.get("benchmark"), rep.get("blockers"))
        elif args.command == "fiis-monitor":
            from data_pipeline.market.fii_monitoring import run_monitoring
            rep = run_monitoring()
            log.info("FIIs monitoramento — status=%s falhas=%s",
                     rep.get("status"), rep.get("failed"))
        elif args.command == "fiis-enrich":
            from data_pipeline.market.fii_enrichment import run_enrichment
            rep = run_enrichment(
                years=args.years, candidate_limit=args.candidate_limit,
                document_limit=args.document_limit,
                recent_months=args.recent_months,
                document_budget_bytes=max(args.document_budget_mb, 1) * 1024 * 1024,
                max_document_bytes=max(args.max_document_mb, 1) * 1024 * 1024,
                min_free_bytes=max(args.min_free_gb, 0) * 1024 * 1024 * 1024,
                tickers=tickers)
            log.info("FIIs enriquecimento — status=%s candidatos=%s falhas=%s",
                     rep.get("status"), len(rep.get("candidates") or []),
                     rep.get("failed_stages"))
        elif args.command == "fiis-documents":
            from data_pipeline.market.fii_documents import process_pending_documents
            rep = process_pending_documents(
                limit=args.limit or 25, tickers=tickers,
                recent_months=args.recent_months,
                max_batch_bytes=max(args.max_batch_mb, 1) * 1024 * 1024,
                max_document_bytes=max(args.max_document_mb, 1) * 1024 * 1024,
                min_free_bytes=max(args.min_free_gb, 0) * 1024 * 1024 * 1024)
            log.info("FIIs documentos — selecionados=%s baixados=%s extraídos=%s "
                     "revisão=%s falhas=%s oversized=%s bytes=%s", rep.get("selected"),
                     rep.get("downloaded"), rep.get("extracted"),
                     rep.get("needs_review"), rep.get("failed"), rep.get("oversized"),
                     rep.get("bytes_processed"))
        elif args.command == "fiis-cvm-cri":
            from data_pipeline.market.fii_cvm_cri import ingest_cvm_cri
            rep = ingest_cvm_cri(years=args.years)
            log.info("FIIs CVM CRI - arquivos=%s CRIs=%s observacoes=%s "
                     "metricas_FII=%s erros=%s", rep.get("archives"),
                     rep.get("securities"), rep.get("security_observations"),
                     rep.get("fii_observations"), len(rep.get("errors") or []))
        elif args.command == "fiis-cvm-structured":
            from data_pipeline.market.fii_cvm_structured import ingest_cvm_structured
            rep = ingest_cvm_structured(years=args.years)
            log.info("FIIs CVM estruturada — arquivos=%s métricas=%s exposições=%s "
                     "documentos=%s erros=%s", rep.get("archives"), rep.get("observations"),
                     rep.get("exposures"), rep.get("documents"), len(rep.get("errors") or []))
        elif args.command == "fiis-v2-history":
            rep = fii_ingest.ingest_v2_history(limit=args.limit, tickers=tickers,
                                                years=args.years)
            log.info("FIIs Brapi v2 historico - fundos=%s req=%s precos=%s erros=%s",
                     rep.get("fundos"), rep.get("requisicoes"), rep.get("precos"),
                     rep.get("erros"))
        elif args.command == "fiis-v2":
            rep = fii_ingest.ingest_v2_details(limit=args.limit, tickers=tickers)
            log.info("FIIs Brapi v2 — fundos=%s req=%s métricas=%s exposições=%s "
                     "imóveis=%s dividendos=%s erros=%s",
                     rep.get("fundos"), rep.get("requisicoes"), rep.get("metricas"),
                     rep.get("exposicoes"), rep.get("imoveis"), rep.get("dividendos"),
                     rep.get("erros"))
        elif args.command == "fiis-v4-audit":
            rep = fii_ingest.audit_methodology_v4_data()
            log.info("FIIs auditoria v4 — status=%s checks=%s bloqueios=%s",
                     rep.get("status"), rep.get("checks"), rep.get("blockers"))
        elif args.command == "fiis-v4":
            rep = fii_ingest.snapshot_methodology_v4()
            log.info("FIIs metodologia v4 — status=%s fundos=%s gravados=%s bloqueios=%s",
                     rep.get("status"), rep.get("fundos"), rep.get("gravados"), rep.get("blockers"))
        elif args.command == "fiis-cvm":
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
        elif args.command == "fiis-cadastro-gaps":
            rep = fii_ingest.enrich_cadastro_gaps()
            log.info("FIIs cadastro — segmento=%s vacancia=%s erros=%s",
                     rep.get("segmento_preenchido"), rep.get("vacancia_preenchida"),
                     rep.get("erros"))
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
        if args.command not in ("fiis-v2", "fiis-v2-history", "fiis-cvm-structured",
                                "fiis-cvm-cri", "fiis-v4", "fiis-v4-audit",
                                "fiis-documents", "fiis-registry", "fiis-b3-history",
                                "fiis-entities", "fiis-confidence", "fiis-pit-backtest",
                                "fiis-monitor", "fiis-enrich", "benchmark") and rep.get("erros", 0) != -1:
            rep["metodologia_v4"] = fii_ingest.snapshot_methodology_v4()
        if args.json:
            print(json.dumps(rep, indent=2, default=str))
        if args.command == "fiis-documents":
            return 0 if rep.get("failed", 0) != -1 else 1
        if args.command == "fiis-cvm-cri":
            return 0 if rep.get("status") == "completed" else 1
        if args.command in ("fiis-registry", "fiis-b3-history", "fiis-entities",
                            "fiis-confidence"):
            return 0 if rep.get("status") == "completed" else 1
        if args.command == "fiis-pit-backtest":
            # Bloqueio metodológico é resultado válido; só falha operacional retorna 1.
            return 0 if rep.get("status") in ("passed", "blocked") else 1
        if args.command == "fiis-monitor":
            return 0 if rep.get("status") in ("passed", "warning") else 1
        if args.command == "fiis-enrich":
            return 0 if rep.get("status") in ("completed", "partial") else 1
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

    if args.command == "integrity":
        from data_pipeline.market import integrity
        from data_pipeline.utils.db_utils import get_pipeline_engine
        rep = integrity.check_dividend_echoes(get_pipeline_engine(), tickers)
        log.info("Integridade dividendos — ecos=%d em %d ticker(s)%s",
                 rep["linhas_eco"], len(rep["tickers"]),
                 " ✅" if not rep["linhas_eco"] else
                 " ⚠ rode scripts/fix_dividends_class_mix.py --apply")
        for tk, n in sorted(rep["tickers"].items()):
            log.warning("  %s: %d linha(s) de eco", tk, n)
        if args.json:
            print(json.dumps(rep, indent=2, default=str))
        return 0 if not rep["linhas_eco"] else 1

    if args.command == "bootstrap":
        prog = ingest.bootstrap(tickers, args.source or "setores", args.limit or 50)
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
