"""Publica os pregões recentes da B3 em ``data/public/pregoes_recentes_b3.json.gz``.

Serve ao contexto das LLMs (``core/pregoes_recentes.py``). O bloco CONTEXTO DE
MERCADO só tinha fechamento MENSAL de bolsa: BOVA11 e SMAL11 de
``market.historical_prices`` do Supabase e a ferramenta ``preco_ativo``. Em
29/10/2026 o último ponto era de 05/10. O publicador da brapi está barrado
desde 12/10 porque o Supabase passou dos 500 MB. A queda de 26/10 não
aparecia: BOVA11 −14,5% e BBAS3 −29% num pregão. Sem esse dado a LLM
tratou a "ressaca pós-eleição" do noticiário como manchete sem confirmação.

A fonte é o COTAHIST, o arquivo oficial da B3 com o fechamento bruto, sem
retroajuste. Os ZIPs anuais ficam em ``local_staging/fii_b3_cotahist``. O alvo
``b3_pregao`` da rotina noturna os baixa antes deste. Não é
``market.historical_prices`` do armazém: ele mistura barras diárias com a barra
do mês datada no primeiro pregão (a linha de 01/10/2026 carregava a máxima e a
mínima de outubro inteiro). E tinha buracos e cotação intradiária
(``scripts/carteira20_desempenho.py``). Entram:

* ações, units e BDRs do lote padrão e dos BDIs especiais (02, 05–09), pelo
  fechamento dividido pelo fator de cotação;
* ETFs (BDI 14) -- o BOVA11 é o termômetro do Ibovespa, que o COTAHIST não
  traz como índice;
* FIIs (BDI 12).

Formato: ``pregoes`` (as datas, em ordem) e, por ticker, o tipo, o nome do
pregão, o volume financeiro médio dos últimos 21 pregões e um fechamento por
pregão (``null`` onde não negociou). Fica só quem negociou em algum dos últimos
21 pregões.

Recusa publicar (saída 1) se o último pregão tiver mais de
``SERIE_MAXIMA_DIAS`` dias. Renovar ``gerado_em`` sobre fita parada faria o
arquivo parecer em dia.

    python scripts/publish_pregoes_recentes_b3.py [--dry-run] [--pasta DIR]
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CAMINHO_PADRAO = ROOT / "data" / "public" / "pregoes_recentes_b3.json.gz"
VERSAO = 1
#: ~3 meses de pregões: cobre "no mês", 21 pregões e a máxima recente.
PREGOES = 63
#: Pregões que definem "ativo" e o volume médio.
PREGOES_ATIVO = 21
SERIE_MAXIMA_DIAS = 10
#: Até este dia de janeiro a B3 pode ainda não ter o ZIP do ano.
TOLERANCIA_ZIP_DO_ANO = 10

BDI_ETF = "14"
BDI_FII = "12"
BDIS_ACOES = ("02", "05", "06", "07", "08", "09")

METODO = ("Diário, por ticker. Fechamento de cada pregão no COTAHIST da B3 "
          "(arquivo oficial), sem retroajuste por provento ou desdobramento: "
          "ações, units e BDRs (BDI 02 e 05–09) pelo fechamento ÷ fator de "
          "cotação; ETFs (BDI 14); FIIs (BDI 12). Pregão sem negócio fica nulo.")


def pasta_cotahist() -> Path:
    from data_pipeline.market.fii_b3_history import CACHE_ROOT

    return Path(os.environ.get("PREGOES_RECENTES_COTAHIST_DIR") or ROOT / CACHE_ROOT)


def _tipo(bdi: str) -> str:
    return {BDI_ETF: "etf", BDI_FII: "fii"}.get(bdi, "acao")


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f) or f <= 0:
        return None
    return f


def _unitario(r: dict) -> float | None:
    preco = _num(r.get("close"))
    if preco is None:
        return None
    fator = r.get("fator_cotacao") or 1
    return preco / fator if fator > 1 else preco


def montar_payload(linhas: list[dict], agora: datetime, *,
                   pregoes: int = PREGOES) -> dict:
    """Puro. ``linhas``: as de ``b3_cotahist.ler_texto`` (``trade_date`` ISO).

    O mesmo ticker em dois BDIs no mesmo pregão (raro: mudança de segmento)
    fica com o de maior volume financeiro.
    """
    melhor: dict[tuple[str, str], dict] = {}
    for r in linhas:
        preco = _unitario(r)
        if preco is None:
            continue
        chave = (str(r["ticker"]).strip().upper(), str(r["trade_date"]))
        atual = melhor.get(chave)
        if atual is None or (r.get("financial_volume") or 0) > atual["vol"]:
            melhor[chave] = {"preco": preco, "vol": r.get("financial_volume") or 0.0,
                             "tipo": _tipo(r.get("bdi") or ""),
                             "nome": r.get("issuer_short_name") or ""}
    datas = sorted({d for _, d in melhor})[-pregoes:]
    recentes = set(datas[-PREGOES_ATIVO:])
    por_ticker: dict[str, dict] = {}
    for tk in sorted({t for t, _ in melhor}):
        dias = [melhor.get((tk, d)) for d in datas]
        if not any(x for d, x in zip(datas, dias) if d in recentes):
            continue
        ultimo = next(x for x in reversed(dias) if x)
        vol = sum(x["vol"] for d, x in zip(datas, dias) if x and d in recentes)
        por_ticker[tk] = {
            "t": ultimo["tipo"], "n": ultimo["nome"],
            "v": round(vol / max(1, len(recentes))),
            "c": [round(x["preco"], 4) if x else None for x in dias],
        }
    return {
        "versao": VERSAO, "gerado_em": agora.isoformat(timespec="seconds"),
        "base_ate": datas[-1] if datas else None,
        "metodo": METODO, "n_tickers": len(por_ticker),
        "pregoes": datas, "por_ticker": por_ticker,
    }


def serie_velha(payload: dict, hoje: date) -> str | None:
    """Motivo da recusa, ou ``None`` se a fita está em dia."""
    base = payload.get("base_ate")
    if not base:
        return "nenhum pregão nos ZIPs do COTAHIST"
    idade = (hoje - date.fromisoformat(base)).days
    if idade > SERIE_MAXIMA_DIAS:
        return (f"último pregão do COTAHIST é {base} ({idade} dias); limite "
                f"{SERIE_MAXIMA_DIAS}. Rodar o alvo b3_pregao antes.")
    return None


def linhas_do_cotahist(pasta: Path, hoje: date, *, pregoes: int = PREGOES) -> list[dict]:
    """Linhas do ano corrente e, se faltar pregão para a janela, do anterior.

    ZIP ausente é erro, e não janela mais curta (exceto o do ano no início de
    janeiro, quando a B3 ainda não o publicou).
    """
    from data_pipeline.market.b3_cotahist import ler_linhas

    bdis = BDIS_ACOES + (BDI_ETF, BDI_FII)
    linhas: list[dict] = []
    for ano in (hoje.year, hoje.year - 1):
        caminho = pasta / f"COTAHIST_A{ano}.ZIP"
        if not caminho.exists():
            if ano == hoje.year and hoje <= date(ano, 1, TOLERANCIA_ZIP_DO_ANO):
                continue
            raise SystemExit(f"{caminho} não existe; o alvo b3_pregao da rotina "
                             "noturna o baixa (ou use --pasta)")
        linhas = ler_linhas(caminho.read_bytes(), bdi_codes=bdis) + linhas
        if len({r["trade_date"] for r in linhas}) >= pregoes:
            break
    return linhas


def serializar(payload: dict, caminho: Path) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    bruto = json.dumps(payload, ensure_ascii=False,
                       separators=(",", ":")).encode("utf-8")
    # mtime=0: mesmo conteúdo, mesmos bytes (não suja o git à toa)
    with open(caminho, "wb") as fh, gzip.GzipFile(
            filename="", mode="wb", fileobj=fh, mtime=0) as gz:
        gz.write(bruto)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--saida", type=Path, default=CAMINHO_PADRAO)
    p.add_argument("--pasta", type=Path, default=None,
                   help="pasta dos COTAHIST_A{ano}.ZIP (padrão: local_staging/fii_b3_cotahist)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    agora = datetime.now(timezone.utc)
    linhas = linhas_do_cotahist(args.pasta or pasta_cotahist(), agora.date())
    payload = montar_payload(linhas, agora)
    relatorio = {k: payload[k] for k in ("base_ate", "n_tickers")}
    relatorio["n_pregoes"] = len(payload["pregoes"])
    motivo = serie_velha(payload, agora.date())
    if motivo:
        relatorio["erro"] = motivo
        print(json.dumps(relatorio, ensure_ascii=False))
        return 1
    if not args.dry_run:
        serializar(payload, args.saida)
        relatorio["arquivo"] = str(args.saida)
        relatorio["bytes"] = args.saida.stat().st_size
    print(json.dumps(relatorio, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
