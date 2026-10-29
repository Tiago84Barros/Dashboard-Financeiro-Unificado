"""Publica os proventos de renda da B3 por ano em ``data/public/proventos_b3.json.gz``.

A fonte é a resposta mais nova da brapi para cada ticker, arquivada em
``market.brapi_raw_payloads`` no armazém local (Docker ``dfu_warehouse``), só
leitura. ``market.dividends`` não serve: o upsert não apaga, e cada revisão da
brapi deixa a linha antiga ao lado da nova (ver ``core.proventos_b3``).

Por ticker: ``foto_em`` (dia da resposta) e ``anos`` (``{"AAAA": total}``, a
soma por ação dos proventos de renda pelo ano de pagamento). Ticker cuja
resposta só trouxe amortização fica com ``anos`` vazio: está coberto, e a
cobertura diz que não houve renda. Resposta com a lista vazia fica de fora, e
o leitor cai no banco para esse ticker.

Recusa publicar (saída 1) se a resposta mais nova de todas tiver mais de
``FOTO_MAXIMA_DIAS`` dias: renovar ``gerado_em`` sobre coleta parada faria o
arquivo parecer em dia.
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

CAMINHO_PADRAO = ROOT / "data" / "public" / "proventos_b3.json.gz"
VERSAO = 1
# O mesmo limite põe na fila de Restrições o ticker cuja resposta parou de renovar.
from core.proventos_b3 import FOTO_MAXIMA_DIAS  # noqa: E402

METODO = ("Anual, por ticker. Resposta mais nova da brapi (market.brapi_raw_payloads, "
          "endpoint quote), normalizada como na ingestão: sem o eco de classe, "
          "só renda (dividendo, JCP, rendimento; amortização e restituição de "
          "capital saem), sem a cópia com pagamento no dia da data-ex quando o "
          "mês tem o calendário real, linha idêntica uma vez. Soma por ação, "
          "valor como declarado, pelo ano de pagamento (data-ex quando a brapi "
          "não informa o pagamento).")


def _dia(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return (v.astimezone(timezone.utc) if v.tzinfo else v).date()
    if isinstance(v, date):
        return v
    return datetime.fromisoformat(str(v)).date()


def montar_payload(fotos, agora: datetime) -> dict:
    """Puro. ``fotos``: tuplas ``(ticker, fetched_at, symbol, dividendsData)``,
    como ``core.proventos_b3.SQL_FOTOS`` devolve."""
    from core.dividend_types import soma_por_ano
    from core.proventos_b3 import eventos_das_fotos

    fotos = list(fotos)
    quando: dict[str, date] = {}
    for tk, foto_em, *_ in fotos:
        tk, dia = str(tk).strip().upper(), _dia(foto_em)
        if dia is not None and (tk not in quando or dia > quando[tk]):
            quando[tk] = dia
    anos: dict[str, dict[str, float]] = {tk: {} for tk in quando}
    for r in soma_por_ano(eventos_das_fotos(fotos)).itertuples():
        anos.setdefault(str(r.ticker), {})[str(int(r.ano))] = round(float(r.total), 6)
    mais_nova = max(quando.values(), default=None)
    return {
        "versao": VERSAO, "gerado_em": agora.isoformat(timespec="seconds"),
        "metodo": METODO,
        "foto_mais_nova": mais_nova.isoformat() if mais_nova else None,
        "n_tickers": len(anos),
        "n_com_renda": sum(1 for a in anos.values() if a),
        "por_ticker": {tk: {"foto_em": quando[tk].isoformat() if tk in quando else None,
                            "anos": dict(sorted(anos[tk].items()))}
                       for tk in sorted(anos)},
    }


def foto_velha(payload: dict, hoje: date) -> str | None:
    """Motivo da recusa, ou ``None`` se a coleta está em dia."""
    base = payload.get("foto_mais_nova")
    if not base or not payload.get("n_tickers"):
        return "armazém sem nenhuma resposta da brapi com a lista de proventos"
    idade = (hoje - date.fromisoformat(base)).days
    if idade > FOTO_MAXIMA_DIAS:
        return (f"resposta mais nova da brapi é de {base} ({idade} dias); limite "
                f"{FOTO_MAXIMA_DIAS}. Rodar a coleta da brapi antes.")
    return None


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
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    from sqlalchemy import create_engine, text

    from core.proventos_b3 import SQL_FOTOS
    from scripts.publish_fii_selection_from_local import _warehouse_url
    try:
        conn = create_engine(_warehouse_url()).connect()
    except Exception as exc:
        print(f"armazém local indisponível: {type(exc).__name__}: {exc}")
        return 2
    with conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        fotos = conn.execute(text(SQL_FOTOS)).fetchall()
    agora = datetime.now(timezone.utc)
    payload = montar_payload(fotos, agora)
    relatorio = {k: payload[k] for k in ("foto_mais_nova", "n_tickers", "n_com_renda")}
    motivo = foto_velha(payload, agora.date())
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
