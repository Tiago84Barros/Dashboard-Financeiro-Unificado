"""Publica os dados dos ETFs da carteira em ``data/public/etf_dados.json.gz``.

Os oito indicadores de ETF da Inteligência dos Ativos (índice, composição,
concentração, taxa, tracking error, liquidez, exposição geográfica e setorial)
saíam todos "Dado não disponível.": ``fontes_fundamentos.dados_etf`` devolvia
``{}`` porque nenhuma fonte do projeto trazia dado de fundo. A LLM, lendo a
mesma seção, dizia não ter informação sobre ETF -- com razão.

Quais ETFs: os da carteira real (``portfolio_positions`` com classe "ETF ...")
mais os índices do seletor de benchmark dos EUA (SPY, IWB, QQQ), que a Curva de
capital usa. Lê o Supabase só para a lista; os dados vêm do yfinance, na
máquina local (o app publicado não baixa nada).

Arquivo, e não tabela: são dezenas de fundos e o Supabase não tem folga.

Grava por omissão; ``--dry-run`` só mede. Uso:
    python scripts/publish_etf_dados.py
    python scripts/publish_etf_dados.py --dry-run --simbolo SPY --simbolo IEFA
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.inteligencia_ativos import etf_dados  # noqa: E402

logger = logging.getLogger(__name__)
CAMINHO_PADRAO = ROOT / etf_dados.ARQUIVO
VERSAO = 1

_SQL_ETFS = """
    SELECT DISTINCT a.ticker, a.currency
      FROM portfolio_positions pp
      JOIN assets a ON a.id = pp.asset_id
     WHERE COALESCE(pp.quantity, 0) > 0
       AND UPPER(COALESCE(a.class, '')) LIKE 'ETF%'
"""


def simbolo_yahoo(ticker: str, moeda: str | None) -> str:
    """IVVB11 (BRL) é ``IVVB11.SA`` no Yahoo; SPY (USD) é ``SPY``. Puro."""
    t = str(ticker or "").upper().strip()
    if (moeda or "USD").upper() == "BRL" and not t.endswith(".SA"):
        return f"{t}.SA"
    return t


def etfs_da_carteira(engine) -> dict[str, str]:
    """``{ticker: moeda}`` das posições abertas de classe ETF no Supabase."""
    from sqlalchemy import text
    with engine.connect() as conn:
        return {str(r[0]).upper(): str(r[1] or "USD").upper()
                for r in conn.execute(text(_SQL_ETFS)) if r[0]}


def _tabela(df) -> list[dict]:
    """DataFrame do yfinance → lista de dicts, sem depender de pandas aqui."""
    if df is None or getattr(df, "empty", True):
        return []
    return df.reset_index().to_dict("records")


def baixar(simbolo: str, *, yf=None) -> tuple[dict, dict]:
    """``(info, fundos)`` em tipos simples. Falha parcial vira campo vazio.

    ``funds_data`` levanta para papel que o Yahoo não trata como fundo (ETF
    brasileiro, às vezes); o ``info`` ainda traz nome, volume e patrimônio.
    """
    if yf is None:
        import yfinance as yf
    t = yf.Ticker(simbolo)
    try:
        info = dict(t.info or {})
    except Exception as exc:  # noqa: BLE001
        logger.warning("info %s: %s", simbolo, exc)
        info = {}
    fundos: dict = {}
    try:
        fd = t.funds_data
        fundos["descricao"] = getattr(fd, "description", None)
        fundos["visao"] = dict(getattr(fd, "fund_overview", None) or {})
        ops = getattr(fd, "fund_operations", None)
        if ops is not None and not getattr(ops, "empty", True):
            coluna = simbolo if simbolo in ops.columns else ops.columns[0]
            fundos["operacoes"] = {str(k): v for k, v in ops[coluna].items()}
        fundos["top"] = [{"simbolo": r.get("Symbol"), "nome": r.get("Name"),
                          "peso": r.get("Holding Percent")}
                         for r in _tabela(getattr(fd, "top_holdings", None))]
        fundos["setores"] = dict(getattr(fd, "sector_weightings", None) or {})
        fundos["classes"] = dict(getattr(fd, "asset_classes", None) or {})
    except Exception as exc:  # noqa: BLE001
        logger.warning("funds_data %s: %s", simbolo, exc)
    return info, fundos


def montar(alvos: dict[str, str], *, baixador=baixar,
           agora: datetime | None = None) -> dict:
    """Payload da vitrine. ``alvos`` = ``{ticker da carteira: moeda}``.

    A chave do registro é o ticker COMO A CARTEIRA O ESCREVE (IVVB11, não
    IVVB11.SA): é por ele que a tela procura.
    """
    agora = agora or datetime.now(timezone.utc)
    gerado = agora.isoformat(timespec="seconds")
    por_ticker, falhas = {}, {}
    for ticker, moeda in sorted(alvos.items()):
        yahoo = simbolo_yahoo(ticker, moeda)
        try:
            info, fundos = baixador(yahoo)
        except Exception as exc:  # noqa: BLE001 - um fundo não derruba os outros
            falhas[ticker] = f"{type(exc).__name__}: {exc}"[:160]
            continue
        if not info and not fundos:
            falhas[ticker] = "yfinance não devolveu nada"
            continue
        registro = etf_dados.extrair(ticker, info, fundos, gerado_em=gerado)
        registro["simbolo_yahoo"] = yahoo
        por_ticker[ticker] = registro
    return {"versao": VERSAO, "gerado_em": gerado, "fonte": etf_dados.FONTE,
            "por_ticker": por_ticker, "falhas": falhas}


def serializar(payload: dict, caminho: Path) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    bruto = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), default=str).encode("utf-8")
    with open(caminho, "wb") as fh, gzip.GzipFile(
            filename="", mode="wb", fileobj=fh, mtime=0) as gz:
        gz.write(bruto)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--saida", type=Path, default=CAMINHO_PADRAO)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--simbolo", action="append", dest="simbolos",
                   help="só estes (moeda USD); pode repetir")
    args = p.parse_args(argv)

    if args.simbolos:
        alvos = {s.upper(): "USD" for s in args.simbolos}
    else:
        from core.database import get_engine
        from core.publicacao_agenda import SIMBOLOS_BENCHMARK_EUA
        engine = get_engine()
        if engine is None:
            print("Supabase não configurado: sem a lista de ETFs da carteira.")
            return 2
        alvos = {s: "USD" for s in SIMBOLOS_BENCHMARK_EUA}
        alvos.update(etfs_da_carteira(engine))

    payload = montar(alvos)
    resumo = {"etfs": len(alvos), "publicados": len(payload["por_ticker"]),
              "falhas": payload["falhas"],
              "sem_taxa": sorted(k for k, v in payload["por_ticker"].items()
                                 if v.get("expense_ratio_pct") is None)}
    if not payload["por_ticker"]:
        resumo["erro"] = "nenhum ETF publicado"
        print(json.dumps(resumo, ensure_ascii=False, indent=2))
        return 1
    if not args.dry_run:
        serializar(payload, args.saida)
        resumo["arquivo"] = str(args.saida)
    print(json.dumps(resumo, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
